"""The canary rifleman: posted on the line between the bases on every attack, and its death published."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import canary
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.canary import Canary
from ucla_agent.games.redalert.groups import CANARY, SCOUT
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning

# The opening's build complete: its war factory stands.
BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("proc", 26, 52), ("weap", 26, 44))
# Our base at (22, 48), the enemy's start at (74, 48): 52 cells apart, the post 22 cells from theirs.
POST = (52, 48)
OFF = dataclasses.replace(Tuning(), scouting=dataclasses.replace(Tuning().scouting, canary_post=False))


def own(unit_id: int, unit_type: str, x: int, y: int, *, idle: bool = True) -> dict:
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": None if idle else "move", "idle": idle}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def tanks(count: int = 6) -> list[dict]:
    return [own(300 + n, "2tnk", 31 + n % 3, 47 + n // 3) for n in range(count)]


class Scene:
    """An agent with no manager of its own, the canary rule run on its world after each cycle."""

    def __init__(self, tuning: Tuning = Tuning(), *managers) -> None:
        self.agent = situations.new_agent(*managers, tuning=tuning)
        self.rule = Canary()
        self.orders: list[dict] = []

    def cycle(self, tick: int, units, *, out_since: int | None = None, rush_unit: int | None = None, **seen) -> list:
        ctx = self.agent.ctx
        ctx.attack_out, ctx.attack_out_since = out_since is not None, out_since
        self.orders = ask(self.agent, base_state("england", *BASE, tick=tick, units=list(units), **seen))
        ctx.attack_out, ctx.attack_out_since = out_since is not None, out_since
        return [intent.order for intent in self.rule.intents(self.agent.world, ctx, rush_unit)]

    @property
    def ctx(self):
        return self.agent.ctx


def trained(orders: list[dict]) -> list[dict]:
    return [order for order in orders if order["cmd"] == "train" and order["item"] == "e1"]


def moved(orders: list[dict], unit_id: int) -> list[dict]:
    return [order for order in orders if order["cmd"] == "move" and order["units"] == [unit_id]]


def placed(base, enemy, buildings=(), terrain: Terrain | None = None):
    """The post with our base and the enemy's start where given and the buildings seen."""
    agent = situations.new_agent()
    ask(agent, base_state("england", *BASE, units=tanks(), visible_enemy_buildings=list(buildings)))
    world = agent.world
    world.base_center, world.enemy_base = base, enemy
    return canary.post_cell(world, terrain or Terrain(), agent.ctx)


# -- the post -------------------------------------------------------------------------------------------


def test_post_on_line_22_cells_from_enemy_start():
    assert placed((10, 40), (62, 40)) == (40, 40)


def test_post_moves_out_of_defense_coverage():
    # The pillbox covers 6 + 2 cells and the post keeps 2 more: the first cell toward our base beyond 10 of it.
    assert placed((10, 40), (62, 40), [foe(700, "pbox", 38, 40)]) == (27, 40)


def test_post_skips_impassable_cell():
    assert placed((10, 40), (62, 40), terrain=Terrain(impassable={(40, 40)})) == (39, 40)


def test_post_keeps_off_enemy_buildings_and_takes_the_midpoint_when_no_cell_is_left():
    assert placed((10, 40), (62, 40), [foe(701, "powr", 45, 40)]) == (34, 40)
    walls = Terrain(impassable={(x, 40) for x in range(0, 96)})
    assert placed((10, 40), (62, 40), terrain=walls) == (36, 40)


# -- who is the canary ----------------------------------------------------------------------------------


def test_trains_one_when_no_rush_rifleman(match_record):
    scene = Scene()
    scene.cycle(1000, tanks())

    first = scene.cycle(1005, tanks(), out_since=1005)
    waiting = [scene.cycle(1005 + n, tanks(), out_since=1005) for n in range(1, 4)]
    came = scene.cycle(1100, [*tanks(), own(400, "e1", 20, 44)], out_since=1005)

    assert len(trained(first)) == 1 and all(trained(orders) == [] for orders in waiting)
    assert scene.rule.unit_id == 400 and scene.ctx.groups.group_of(400) == CANARY
    assert moved(came, 400) == [{"cmd": "move", "units": [400], "to": list(POST)}]
    posted = [event for event in match_record("scout_slot") if event.get("kind") == canary.CANARY_KIND]
    assert posted[-1]["phase"] == canary.POSTED and posted[-1]["source"] == canary.TRAINED


def test_trains_one_when_the_rush_scout_is_a_jeep():
    scene = Scene()
    scene.cycle(1000, [*tanks(), own(390, "jeep", 60, 48)])

    first = scene.cycle(1005, [*tanks(), own(390, "jeep", 60, 48)], out_since=1005, rush_unit=390)

    assert len(trained(first)) == 1 and scene.rule.unit_id is None


def test_an_order_not_seen_for_2_s_is_placed_again_and_the_queue_is_claimed_meanwhile():
    scene = Scene()
    scene.cycle(1000, tanks())
    scene.cycle(1005, tanks(), out_since=1005)

    early = scene.cycle(1005 + 2 * TICKS_PER_SECOND - 1, tanks(), out_since=1005)
    again = scene.cycle(1005 + 2 * TICKS_PER_SECOND, tanks(), out_since=1005)

    assert trained(early) == [] and len(trained(again)) == 1
    assert scene.ctx.claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")


def test_rush_post_rifleman_reused():
    manager = ScoutingManager()
    agent = situations.new_agent(manager)
    rifle = own(390, "e1", 60, 48)
    ask(agent, base_state("england", *BASE, tick=1000, units=[*tanks(), rifle]))
    held_by_slot = agent.ctx.groups.group_of(390)
    # The rifleman stood at the rush watch's exit post (see `rush_scout`).
    manager._slots._rush.post_unit = 390
    agent.ctx.attack_out, agent.ctx.attack_out_since = True, 1005

    orders = ask(agent, base_state("england", *BASE, tick=1005, units=[*tanks(), rifle]))

    # The canary rule orders no rifleman; the emptied slot's own rules may order its replacement scout.
    assert held_by_slot == SCOUT and manager._canary._awaiting is False
    assert manager._canary.unit_id == 390 and agent.ctx.groups.group_of(390) == CANARY
    assert all(slot.unit_id != 390 for slot in manager._slots._slots)
    assert {"cmd": "move", "units": [390], "to": list(POST)} in orders


def test_posted_on_every_attack_set_out(match_record):
    scene = Scene()
    rifle = own(390, "e1", 40, 48)
    scene.cycle(1000, [*tanks(), rifle])
    first = scene.cycle(1005, [*tanks(), rifle], out_since=1005, rush_unit=390)
    at_post = own(390, "e1", *POST)
    home = scene.cycle(1100, [*tanks(), at_post])
    moved_off = own(390, "e1", 40, 48)

    second = scene.cycle(2000, [*tanks(), moved_off], out_since=2000)

    assert moved(first, 390) and moved(home, 390) == [] and moved(second, 390)
    phases = [
        (event["phase"], event.get("source"))
        for event in match_record("scout_slot")
        if event.get("kind") == canary.CANARY_KIND
    ]
    assert phases == [(canary.POSTED, canary.RUSH_POST), (canary.POSTED, canary.KEPT)]


def test_lost_canary_not_replaced_mid_attack():
    scene = Scene()
    rifle = own(390, "e1", 40, 48)
    scene.cycle(1000, [*tanks(), rifle])
    scene.cycle(1005, [*tanks(), rifle], out_since=1005, rush_unit=390)

    gone = [scene.cycle(1010 + n * 50, tanks(), out_since=1005) for n in range(4)]
    next_attack = scene.cycle(2000, tanks(), out_since=2000)

    assert all(trained(orders) == [] for orders in gone)
    assert len(trained(next_attack)) == 1


def test_canary_death_published():
    scene = Scene()
    rifle = own(390, "e1", *POST)
    scene.cycle(1000, [*tanks(), rifle])
    scene.cycle(1005, [*tanks(), rifle], out_since=1005, rush_unit=390)

    scene.cycle(1200, tanks(), out_since=1005)

    assert (scene.ctx.canary_lost_tick, scene.ctx.canary_lost_while_out) == (1200, True)
    assert scene.rule.unit_id is None


def test_canary_takes_no_other_orders():
    scene = Scene(Tuning(), BaseDefenseManager())
    rifle = own(390, "e1", 24, 46)
    scene.cycle(1000, [*tanks(), rifle])
    scene.cycle(1005, [*tanks(), rifle], out_since=1005, rush_unit=390)
    raiders = [foe(500 + n, "3tnk", 24 + n, 50) for n in range(6)]

    orders = scene.cycle(1010, [*tanks(), rifle], out_since=1005, visible_enemies=raiders)

    assert scene.ctx.threat_level == "major"
    assert not [order for order in scene.orders if 390 in order.get("units", ())]
    assert moved(orders, 390) == [] or moved(orders, 390) == [{"cmd": "move", "units": [390], "to": list(POST)}]


def test_switch_off_no_canary():
    scene = Scene(OFF)
    rifle = own(390, "e1", 40, 48)
    scene.cycle(1000, [*tanks(), rifle])

    orders = scene.cycle(1005, [*tanks(), rifle], out_since=1005, rush_unit=390)

    assert orders == [] and scene.ctx.groups.members(CANARY) == []


def test_no_canary_before_the_opening_s_build_is_complete():
    scene = Scene()
    early = (("fact", 22, 48), ("powr", 19, 52))
    ask(scene.agent, base_state("england", *early, tick=1000, units=tanks()))
    scene.ctx.attack_out, scene.ctx.attack_out_since = True, 1000

    assert scene.rule.intents(scene.agent.world, scene.ctx) == []


def test_tuning_canary_post_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).scouting, ScoutingTuning()):
        assert tuning.canary_post is True
        assert (tuning.canary_post_cells, tuning.canary_home_min_cells) == (22, 15)
        assert (tuning.canary_clear_margin_cells, tuning.canary_building_clear_cells) == (2, 10)
