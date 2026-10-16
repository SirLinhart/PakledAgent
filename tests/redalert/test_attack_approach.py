"""The attack's approach: direct or by a side cell when the direct way is defended, and the attack being out."""

from __future__ import annotations

import dataclasses
import math

import situations
from situations import StubChooser, ask, base_state
from ucla_agent.games.redalert import attack_approach, command_army
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.commander import MAIN_ROLE, Commander
from ucla_agent.games.redalert.command_army import ADVANCE, ATTACK, FLANK, GATHER, SIEGE
from ucla_agent.games.redalert.command_prompt import Action
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning

YARD, POWER = ("fact", 22, 48), ("powr", 19, 52)
# The shipped thresholds with the vehicle repair off, so that main keeps every tank whatever its health, and the
# stepped movement off, so that the march goes as one order to the cell it is on its way to.
SHIPPED = dataclasses.replace(Tuning(), combat=dataclasses.replace(Tuning().combat, vehicle_repair=False))
UNSTEPPED = dataclasses.replace(
    SHIPPED, combat=dataclasses.replace(SHIPPED.combat, stepped_advance=False, stepped_return=False)
)
# And the forward staging off, so that the march is not held short of the pillbox's reach.
UNSTAGED = dataclasses.replace(UNSTEPPED, combat=dataclasses.replace(UNSTEPPED.combat, forward_staging=False))
FLANK_OFF = dataclasses.replace(SHIPPED, combat=dataclasses.replace(SHIPPED.combat, flank_approach=False))
START, OBJECTIVE = (10, 40), (60, 40)
LEFT_CELL, RIGHT_CELL = (48, 26), (48, 54)


def own(unit_id: int, unit_type: str, x: int, y: int, *, idle: bool = True) -> dict:
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": None if idle else "move", "idle": idle}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def tanks_at(x: int, y: int, count: int = 6, first: int = 300) -> list[dict]:
    return [own(first + n, "2tnk", x - 1 + n % 3, y - 1 + n // 3) for n in range(count)]


class Scene:
    """An agent with the combat manager alone under a tuning, one cycle at a time."""

    def __init__(self, tuning: Tuning = SHIPPED, **more) -> None:
        self.manager = CombatManager()
        self.agent = situations.new_agent(self.manager, tuning=tuning, **more)

    def cycle(self, tick: int, units, enemies=(), buildings=()) -> list[dict]:
        state = base_state(
            "england",
            YARD,
            POWER,
            tick=tick,
            units=list(units),
            visible_enemies=list(enemies),
            visible_enemy_buildings=list(buildings),
        )
        return ask(self.agent, state)

    @property
    def world(self):
        return self.agent.world

    @property
    def ctx(self):
        return self.agent.ctx


def weighed(
    match_record,
    tuning: Tuning = SHIPPED,
    terrain: Terrain | None = None,
    start=START,
    objective=OBJECTIVE,
    units=None,
    enemies=(),
    buildings=(),
    tick: int = 1000,
):
    """The approach from `start` to `objective` with the cycle's state seen, and its `approach` event."""
    scene = Scene(tuning)
    scene.cycle(tick, units or tanks_at(*start), enemies, buildings)
    if terrain is not None:
        scene.ctx.terrain = terrain
    chosen = attack_approach.approaches(start, objective, scene.world, scene.ctx)
    events = [event for event in match_record("combat") if event["reason"] == attack_approach.APPROACH]
    return chosen, events[-1]


def test_undefended_direct_is_taken_directly(match_record):
    chosen, event = weighed(match_record)

    assert chosen.chosen == attack_approach.DIRECT and chosen.side_cell is None
    assert chosen.legs == (OBJECTIVE,) and event["chosen"] == attack_approach.DIRECT


def test_defended_front_takes_less_defended_side(match_record):
    chosen, event = weighed(match_record, buildings=[foe(700, "pbox", 45, 40)])

    assert chosen.chosen in (attack_approach.LEFT, attack_approach.RIGHT)
    assert chosen.side_cell in (LEFT_CELL, RIGHT_CELL)
    # 14 cells off the line, at the point 12 cells short of the objective.
    assert abs(chosen.side_cell[1] - 40) == 14 and chosen.side_cell[0] == OBJECTIVE[0] - 12
    assert event["exposure"]["direct"] == 1200 and event["exposure"][chosen.chosen] == 0


def test_side_not_better_enough_goes_direct(match_record):
    # A medium tank (850) by each side cell: 0.7 of the pillbox's 1200, more than the 0.6 a side must keep under.
    tanks = [foe(800, "2tnk", 48, 24), foe(801, "2tnk", 48, 56)]

    chosen, event = weighed(match_record, enemies=tanks, buildings=[foe(700, "pbox", 45, 40)])

    assert chosen.chosen == attack_approach.DIRECT
    assert event["exposure"] == {"direct": 1200, "left": 850, "right": 850}


def ring(center, radius: int) -> set:
    """The cells at exactly `radius` steps of a cell: a wall no walk of eight directions crosses."""
    x, y = center
    return {
        (x + dx, y + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if max(abs(dx), abs(dy)) == radius
    }


def test_both_sides_blocked_go_direct(match_record):
    walls = Terrain(impassable=ring(LEFT_CELL, 3) | ring(RIGHT_CELL, 3))

    chosen, event = weighed(match_record, terrain=walls, buildings=[foe(700, "pbox", 45, 40)])

    assert chosen.chosen == attack_approach.DIRECT
    assert event["blocked"] == {"left": attack_approach.NO_WALK, "right": attack_approach.NO_WALK}


def test_map_edge_blocks_side(match_record):
    chosen, event = weighed(match_record, start=(10, 4), objective=(60, 4), buildings=[foe(700, "pbox", 45, 4)])

    # Facing east along the top edge, the left side cell would be 10 cells off the map.
    assert event["blocked"] == {"left": attack_approach.EDGE}
    assert chosen.chosen == attack_approach.RIGHT and chosen.side_cell == (48, 18)


def test_impassable_side_cell_gives_way_to_a_passable_one_within_2_cells_and_blocks_without_one():
    scene = Scene()
    scene.cycle(1000, tanks_at(*START))
    scene.ctx.terrain = Terrain(impassable={LEFT_CELL})
    near = attack_approach.side_cell(START, OBJECTIVE, attack_approach.LEFT, scene.world, scene.ctx)
    scene.ctx.terrain = Terrain(
        impassable={(LEFT_CELL[0] + dx, LEFT_CELL[1] + dy) for dx in range(-2, 3) for dy in range(-2, 3)}
    )
    blocked = attack_approach.side_cell(START, OBJECTIVE, attack_approach.LEFT, scene.world, scene.ctx)

    assert near[1] is None and math.dist(near[0], LEFT_CELL) == 1
    assert blocked == (LEFT_CELL, attack_approach.IMPASSABLE)


def test_a_side_walk_longer_than_the_ratio_of_the_direct_walk_is_too_long(match_record):
    # At a ratio of 1.0 the side walks, 38 + 14 steps, are longer than the direct one's 50.
    tight = dataclasses.replace(SHIPPED, combat=dataclasses.replace(SHIPPED.combat, flank_max_length_ratio=1.0))

    chosen, event = weighed(match_record, tuning=tight, buildings=[foe(700, "pbox", 45, 40)])

    assert event["blocked"] == {"left": attack_approach.TOO_LONG, "right": attack_approach.TOO_LONG}
    assert chosen.chosen == attack_approach.DIRECT


def test_seen_army_counts_in_exposure():
    tanks = [foe(800 + n, "2tnk", 30, 30 + n) for n in range(2)]
    legs = (LEFT_CELL, OBJECTIVE)

    def exposure_after(seconds: int) -> float:
        scene = Scene()
        scene.cycle(1000, tanks_at(*START), enemies=tanks)
        scene.cycle(1000 + seconds * TICKS_PER_SECOND, tanks_at(*START))
        return attack_approach.exposure(START, legs, scene.world, scene.ctx)

    # The tanks stand about 4 cells off the left leg from (10, 40) to (48, 26).
    assert exposure_after(10) == 1700
    assert exposure_after(40) == 0


def test_terrain_walk_steps_go_around_a_wall_and_stop_at_the_cap():
    wall = Terrain(impassable={(5, y) for y in range(-10, 11)})

    assert Terrain().walk_steps((0, 0), (10, 3), 20) == 10
    # Round the wall's end at (5, 11) and back: 11 steps each way.
    assert wall.walk_steps((0, 0), (10, 0), 30) == 22
    assert wall.walk_steps((0, 0), (10, 0), 21) is None
    assert Terrain(impassable=ring((10, 0), 2)).walk_steps((0, 0), (10, 0), 50) is None


# -- carrying it out ------------------------------------------------------------------------------------

# A war factory at the enemy's start that no defense covers, and a pillbox 4 cells off the straight way to it: the
# plan's objective is the war factory, and the right side clears the pillbox.
FACTORY, PILLBOX = foe(640, "weap", 74, 48), foe(700, "pbox", 55, 44)
SIDE = (62, 62)


def test_whole_main_goes_by_side_cell(match_record):
    scene = Scene(UNSTEPPED)
    scene.ctx.min_attack_strength = lambda plan: 3000
    gathered = tanks_at(32, 48)

    orders = scene.cycle(1000, gathered, buildings=[FACTORY, PILLBOX])

    assert orders == [{"cmd": "attack_move", "units": list(range(300, 306)), "to": list(SIDE)}]
    assert scene.ctx.attack_out and scene.ctx.attack_side_cell == SIDE
    later = scene.cycle(1005, [own(300 + n, "2tnk", 61 + n % 3, 61 + n // 3) for n in range(6)], buildings=[FACTORY])
    assert [order["to"] for order in later if order["cmd"] == "attack_move"] == [[74, 48]]


def test_whole_main_goes_by_side_cell_in_steps_its_artillery_included():
    scene = Scene(SHIPPED)
    scene.ctx.min_attack_strength = lambda plan: 3000
    artillery = [own(310, "arty", 31, 47), own(311, "arty", 31, 49)]

    scene.cycle(1000, [*tanks_at(33, 48), *artillery], buildings=[FACTORY, PILLBOX])

    move = scene.manager._stepper.move
    assert move.waypoints == [SIDE] and move.destination == (74, 48)
    assert move.group == {*range(300, 306), 310, 311}


def test_commander_flank_option_not_offered_when_on():
    def kinds(tuning: Tuning) -> list[str]:
        agent = situations.new_agent(CombatManager(), tuning=tuning)
        ask(agent, base_state("england", YARD, POWER, tick=3000, units=tanks_at(30, 47)))
        main = [agent.world.combat_units[unit_id] for unit_id in agent.ctx.groups.members(MAIN)]
        found = command_army.brief(main, {}, command_army.EnemyPeak(), agent.world, agent.ctx)
        return [action.kind for action in command_army.army_actions(found, True, {}, agent.world, agent.ctx)]

    assert FLANK not in kinds(SHIPPED) and ADVANCE in kinds(SHIPPED)
    assert FLANK in kinds(FLANK_OFF)


def test_commander_advance_via_side_cell():
    scene = Scene(UNSTEPPED)
    gathered = tanks_at(32, 48)
    scene.cycle(990, gathered, buildings=[FACTORY, PILLBOX])
    watch = scene.manager._attack
    watch.set_out((74, 48), [scene.world.combat_units[n] for n in range(300, 306)], scene.world, scene.ctx)
    watch.publish(scene.ctx)
    advance = Action(ADVANCE, (ADVANCE, (74, 48)), "advance to war factory", "advance to war factory", cell=(74, 48))
    siege = Action(SIEGE, (SIEGE, 700), "siege", "siege", cell=(70, 48))
    near_attack = Action(ATTACK, (ATTACK,), "attack", "attack", cell=(70, 48))

    routed = command_army.routed(advance, (32, 48), scene.ctx)

    assert routed.via == SIDE and routed.head.startswith(f"advance via ({SIDE[0]},{SIDE[1]})")
    assert command_army.routed(siege, (32, 48), scene.ctx).via == SIDE
    assert command_army.routed(near_attack, (32, 48), scene.ctx).via == SIDE
    assert command_army.routed(near_attack, (66, 48), scene.ctx).via is None
    assert command_army.routed(Action(GATHER, (GATHER,), "g", "g", cell=(32, 48)), (32, 48), scene.ctx).via is None
    scene.manager.command(routed)
    scene.cycle(1000, gathered, buildings=[FACTORY, PILLBOX])
    order = scene.manager.commanded_order
    assert (order["cmd"], sorted(order["units"]), order["to"]) == ("move", list(range(300, 306)), list(SIDE))


def units(scene: Scene, listed) -> list:
    """The units of a cycle played with them, as the world has them."""
    scene.cycle(1000, listed)
    return [scene.world.combat_units[unit["id"]] for unit in listed]


def test_attack_out_sets_and_ends(match_record):
    scene = Scene(UNSTEPPED)
    commander = Commander(StubChooser(), scene.ctx.tuning.commander)
    commander.role = MAIN_ROLE
    gathered = units(scene, tanks_at(32, 48))
    gather = Action(GATHER, (GATHER,), "gather", "gather", cell=(32, 48))
    manager, world, ctx = scene.manager, scene.world, scene.ctx

    manager._follow_commanded_attack(commander, gather, gathered, world, ctx)
    closed = ctx.attack_out
    commander.gate.is_open = True
    manager._follow_commanded_attack(commander, gather, gathered, world, ctx)
    opened = (ctx.attack_out, ctx.attack_out_since, ctx.attack_objective)
    commander.gate.is_open = False
    manager._follow_commanded_attack(commander, gather, gathered, world, ctx)

    assert closed is False and opened == (True, world.tick, (74, 48))
    assert ctx.attack_out is False and ctx.attack_out_since is None
    reasons = [event["reason"] for event in match_record("combat")]
    assert attack_approach.ATTACK_OUT in reasons and attack_approach.ATTACK_HOME in reasons


def test_attack_out_ends_when_main_is_home_again_after_it_was_away():
    scene = Scene(UNSTEPPED)
    watch = scene.manager._attack
    far = units(scene, tanks_at(60, 48))
    watch.set_out((74, 48), far, scene.world, scene.ctx)
    watch.follow(far, scene.world, scene.ctx)
    home = units(scene, tanks_at(28, 48))

    watch.follow(home, scene.world, scene.ctx)

    assert not watch.is_out


def test_switch_off_routes_as_before(match_record):
    scene = Scene(dataclasses.replace(UNSTAGED, combat=dataclasses.replace(UNSTAGED.combat, flank_approach=False)))
    scene.ctx.min_attack_strength = lambda plan: 3000

    orders = scene.cycle(1000, tanks_at(32, 48), buildings=[FACTORY, PILLBOX])

    assert orders == [{"cmd": "attack_move", "units": list(range(300, 306)), "to": [74, 48]}]
    assert not [event for event in match_record("combat") if event["reason"] == attack_approach.APPROACH]


def test_tuning_flank_approach_shipped_on():
    shipped = Tuning.from_file(situations.REPO_ROOT).combat
    built_in = CombatTuning()

    for tuning in (shipped, built_in):
        assert tuning.flank_approach is True
        assert (tuning.flank_recompute_cells, tuning.flank_min_distance_cells, tuning.flank_lead_cells) == (10, 20, 12)
        assert (tuning.flank_side_cells, tuning.flank_max_length_ratio, tuning.flank_army_seen_seconds) == (14, 1.8, 30)
        assert (tuning.flank_army_cells, tuning.flank_better_share) == (6, 0.6)
    assert shipped == built_in
