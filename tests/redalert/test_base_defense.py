"""Base defense: an attack on the base or a harvester is noticed, sized and answered in the same cycle."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

import situations
from fake_game import FakeRedAlert, at_seconds
from situations import ScriptedManager, ask, base_state, with_command
from switches_off import combat_off, new_agent, own_agent, scouting_off, tuning_off
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.base_defense import BaseDefenseManager, largest_class
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.fight import ratio_against
from ucla_agent.games.redalert.groups import GUARD, MAIN, RAID, SCOUT
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.tuning import TUNING_FILE, Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager, power_target

REPO_ROOT = Path(__file__).resolve().parents[2]
YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = ("tent", 18, 44)
# The ids `base_state` gives the buildings above when they are listed in this order.
YARD_ID, POWER_ID, BARRACKS_ID = 101, 102, 103
# 10 cells from the base toward the enemy's start at (74, 48).
STAGING = [32, 48]
GUARD_IDS = [200, 201, 202, 203, 204]
RAID_IDS = [400, 401]
RAID_TARGET = [60, 30]


def enemy(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def rifles_at(x: int, count: int = 8, first_id: int = 500) -> list[dict]:
    """`count` enemy rifle infantry standing in a column at `x`, from y = 44 down."""
    return [enemy(first_id + number, "e1", x, 44 + number) for number in range(count)]


def under_attack(seq: int, actor_id: int, actor_type: str, x: int, y: int, hp: int = 80) -> dict:
    return situations.event(seq, "under_attack", id=actor_id, type=actor_type, x=x, y=y, hp=hp)


def defense_agent(*more):
    """An agent with the base-defense manager and the managers given, and the base-defense manager itself."""
    manager = BaseDefenseManager()
    return new_agent(manager, *more), manager


def base(*buildings, tick: int = 1000, **observation) -> dict:
    """An Allied base of the construction yard, a power plant and a barracks, with what the test adds."""
    return base_state("england", YARD, POWER, BARRACKS, *buildings, tick=tick, **observation)


# -- declaring a threat ----------------------------------------------------------------------------


def test_one_enemy_jeep_10_cells_from_the_construction_yard_is_a_minor_threat_worth_500():
    agent, manager = defense_agent()

    ask(agent, base(visible_enemies=[enemy(500, "jeep", 32, 48)]))

    threat = manager.threat
    assert agent.threat_active and agent.ctx.threat_active
    assert (threat.is_major, threat.size, threat.cell) == (False, 500, (22, 48))
    assert [record.id for record in threat.enemies] == [500]


def test_eight_enemy_rifles_10_cells_away_are_a_major_threat_worth_800():
    agent, manager = defense_agent()

    ask(agent, base(visible_enemies=rifles_at(32)))

    assert agent.threat_active
    assert (manager.threat.is_major, manager.threat.size, manager.threat.cell) == (True, 800, (22, 48))


@pytest.mark.parametrize(
    "enemy_types, is_major",
    [(["jeep"], False), (["e1"], False), (["1tnk"], True), (["e1", "e1"], True)],
    ids=["a jeep at 500", "a rifle at 100", "a tank at 700", "two rifles"],
)
def test_a_threat_is_minor_only_when_it_is_one_enemy_unit_of_cost_600_or_less(enemy_types, is_major):
    agent, manager = defense_agent()
    enemies = [enemy(500 + number, enemy_type, 30, 48 + number) for number, enemy_type in enumerate(enemy_types)]

    ask(agent, base(visible_enemies=enemies))

    assert manager.threat.is_major is is_major


@pytest.mark.parametrize("x, declared", [(34, True), (35, False)], ids=["12 cells", "13 cells"])
def test_an_enemy_in_sight_is_a_threat_within_12_cells_of_an_own_building_and_not_beyond(x, declared):
    agent, manager = defense_agent()

    ask(agent, base(visible_enemies=[enemy(500, "e1", x, 48)]))

    assert agent.threat_active is declared and (manager.threat is not None) is declared


def test_an_enemy_that_does_not_fight_is_no_threat_however_close():
    agent, _ = defense_agent()

    ask(agent, base(visible_enemies=[enemy(500, "harv", 25, 48), enemy(501, "mcv", 24, 50)]))

    assert not agent.threat_active


def test_a_spy_plane_and_the_cameras_of_enemy_support_powers_over_the_base_are_no_threat():
    agent, _ = defense_agent()
    support = [enemy(500, "u2", 22, 46), enemy(501, "camera.spyplane", 23, 47), enemy(502, "badr", 20, 50)]

    ask(agent, base(visible_enemies=[*support, enemy(503, "camera.paradrop", 21, 49), enemy(504, "camera", 24, 49)]))

    assert not agent.threat_active


def test_paratroopers_the_item_table_does_not_know_are_counted_as_enemies_that_fight():
    agent, manager = defense_agent()

    ask(agent, base(visible_enemies=[enemy(500, "e1r1", 26, 48), enemy(501, "e3r1", 26, 49)]))

    assert manager.threat.is_major and [record.type for record in manager.threat.enemies] == ["e1r1", "e3r1"]


def test_the_building_a_threat_is_against_is_the_one_nearest_to_the_enemy():
    agent, manager = defense_agent()

    ask(agent, base(visible_enemies=[enemy(500, "e1", 18, 36)]))

    assert manager.threat.cell == (18, 44)


def test_a_building_reported_under_attack_is_a_threat_with_no_enemy_in_sight():
    agent, manager = defense_agent()

    ask(agent, base(events=[under_attack(30, POWER_ID, "powr", 19, 52)]))

    threat = manager.threat
    assert agent.threat_active and threat.cell == (19, 52) and threat.attacked == [POWER_ID]
    assert (threat.is_major, threat.size, threat.enemies) == (False, 0, [])


def test_a_harvester_reported_under_attack_is_a_threat_against_its_cell():
    agent, manager = defense_agent()
    harvester = situations.unit(300, "harv", 50, 20, idle=False)

    ask(agent, base(units=[harvester], events=[under_attack(30, 300, "harv", 50, 20)]))

    assert agent.threat_active and manager.threat.cell == (50, 20) and manager.threat.attacked == [300]


def test_an_own_combat_unit_reported_under_attack_is_no_threat_to_the_base():
    agent, _ = defense_agent()

    ask(agent, base(units=[situations.unit(300, "e1", 50, 20)], events=[under_attack(30, 300, "e1", 50, 20)]))

    assert not agent.threat_active


def test_of_several_actors_under_attack_the_threat_is_against_the_one_the_newest_event_names():
    agent, manager = defense_agent()
    events = [under_attack(30, POWER_ID, "powr", 19, 52), under_attack(31, BARRACKS_ID, "tent", 18, 44)]

    ask(agent, base(events=events))

    assert manager.threat.cell == (18, 44) and manager.threat.attacked == [BARRACKS_ID, POWER_ID]


def test_a_harvester_that_lost_hp_with_an_enemy_within_12_cells_is_a_threat_against_its_cell():
    agent, manager = defense_agent()
    raider = [enemy(500, "e1", 45, 20)]
    ask(agent, base(units=[situations.unit(300, "harv", 40, 20, idle=False)], visible_enemies=raider))
    before = agent.threat_active

    ask(agent, base(tick=1005, units=[situations.unit(300, "harv", 40, 20, idle=False, hp=80)], visible_enemies=raider))

    assert not before and agent.threat_active
    assert manager.threat.cell == (40, 20) and manager.threat.attacked == [300]
    assert [record.id for record in manager.threat.enemies] == [500]


def test_an_actor_that_lost_hp_with_no_enemy_near_is_no_threat():
    agent, _ = defense_agent()
    ask(
        agent,
        base(units=[situations.unit(300, "harv", 40, 20, idle=False)], visible_enemies=[enemy(500, "e1", 60, 20)]),
    )

    ask(agent, base(tick=1005, units=[situations.unit(300, "harv", 40, 20, idle=False, hp=80)]))

    assert not agent.threat_active


def test_the_size_counts_the_enemies_within_15_cells_of_the_base_or_the_threatened_actor_and_no_others():
    agent, manager = defense_agent()
    harvester = situations.unit(300, "harv", 60, 20, idle=False)
    enemies = [enemy(500, "jeep", 32, 48), enemy(501, "1tnk", 60, 34), enemy(502, "1tnk", 38, 48)]

    ask(agent, base(units=[harvester], visible_enemies=enemies, events=[under_attack(30, 300, "harv", 60, 20)]))

    assert manager.threat.size == 500 + 700 and {record.id for record in manager.threat.enemies} == {500, 501}


# -- the threat as it goes on and ends -------------------------------------------------------------


def test_a_threat_keeps_its_cell_while_new_signs_come_from_within_15_cells_and_moves_to_one_from_farther():
    agent, manager = defense_agent()
    harvester = situations.unit(300, "harv", 50, 20, idle=False)
    ask(agent, base(units=[harvester], visible_enemies=rifles_at(32)))

    ask(agent, base(tick=1005, units=[harvester], events=[under_attack(30, POWER_ID, "powr", 19, 52)]))
    kept = manager.threat.cell
    ask(agent, base(tick=1010, units=[harvester], events=[under_attack(31, 300, "harv", 50, 20)]))

    assert kept == (22, 48) and manager.threat.cell == (50, 20)


def test_a_threat_that_was_major_stays_major_when_the_enemies_thin_out():
    agent, manager = defense_agent()
    ask(agent, base(visible_enemies=rifles_at(32)))

    ask(agent, base(tick=1005, visible_enemies=rifles_at(32, count=1)))

    assert (manager.threat.is_major, manager.threat.size) == (True, 100)


def is_active_at(agent, tick: int, **observation) -> bool:
    """Whether the agent has a threat in force after it was handed the base at a tick."""
    ask(agent, base(tick=tick, **observation))
    return agent.threat_active


def test_the_threat_clears_125_ticks_after_the_last_enemy_was_seen_in_the_area():
    agent, manager = defense_agent()
    ask(agent, base(tick=1000, visible_enemies=[enemy(500, "jeep", 32, 48)]))

    still = [is_active_at(agent, tick) for tick in (1005, 1100, 1124)]
    cleared = not is_active_at(agent, 1125)

    assert still == [True, True, True] and cleared and manager.threat is None


def test_an_enemy_within_15_cells_of_the_base_keeps_the_threat_going_though_it_is_not_within_12():
    agent, _ = defense_agent()
    ask(agent, base(tick=1000, visible_enemies=[enemy(500, "jeep", 32, 48)]))
    lingering = [enemy(500, "jeep", 36, 48)]

    kept = [is_active_at(agent, tick, visible_enemies=lingering) for tick in range(1005, 1300, 5)]

    assert all(kept) and is_active_at(agent, 1419) and not is_active_at(agent, 1420)


def test_without_a_base_centre_the_buildings_alone_mark_the_threat_area():
    agent, manager = defense_agent()
    enemies = [enemy(500, "jeep", 32, 48), enemy(501, "1tnk", 52, 48)]

    ask(agent, base(base_center=None, visible_enemies=enemies))
    declared = (manager.threat.cell, manager.threat.size)
    lingering = [
        is_active_at(agent, tick, base_center=None, visible_enemies=[enemy(500, "jeep", 36, 48)])
        for tick in (1005, 1200)
    ]

    assert agent.world.base_center is None
    assert declared == ((22, 48), 500) and lingering == [True, True]


def test_an_enemy_within_15_cells_of_the_base_declares_no_threat_by_itself():
    agent, _ = defense_agent()

    ask(agent, base(visible_enemies=[enemy(500, "jeep", 36, 48)]))

    assert not agent.threat_active


def test_a_new_attack_after_the_end_is_a_new_threat_that_starts_minor():
    agent, manager = defense_agent()
    ask(agent, base(tick=1000, visible_enemies=rifles_at(32)))
    ask(agent, base(tick=1125))

    ask(agent, base(tick=1200, visible_enemies=[enemy(600, "jeep", 32, 48)]))

    assert agent.threat_active and not manager.threat.is_major


def test_the_agent_s_own_managers_answer_a_threat_in_the_cycle_it_is_detected():
    agent = own_agent()
    agent_s_manager = next(manager for manager in agent.managers if manager.name == "base_defense")

    ask(agent, base(visible_enemies=rifles_at(32)))

    assert agent.threat_active and agent_s_manager.threat.is_major


# -- the combat response ---------------------------------------------------------------------------


def guard_rifles(x: int = 28) -> list[dict]:
    """The home guard: five rifle infantry at the guard post in front of the base."""
    return [situations.unit(unit_id, "e1", x, 46 + number) for number, unit_id in enumerate(GUARD_IDS)]


def raid_tanks(*, idle: bool = True) -> list[dict]:
    """Two tanks 40 cells out, which a test puts into the raid group."""
    return [situations.unit(unit_id, "2tnk", 62, 48 + number, idle=idle) for number, unit_id in enumerate(RAID_IDS)]


def raiding(*, once: bool = False) -> ScriptedManager:
    """A stand-in for the raid manager: it sends the raid group to RAID_TARGET in every cycle, or only once."""
    sent_at: list[int] = []

    def propose(world, ctx):
        members = ctx.groups.members(RAID)
        if not members or (once and sent_at):
            return []
        sent_at.append(world.tick)
        return [Intent(Priority.MOVE, {"cmd": "attack_move", "units": members, "to": RAID_TARGET})]

    return ScriptedManager("raids", propose)


def with_raid(agent, units: list[dict]) -> None:
    """Show the agent its units at ticks 990 and 995, no enemy near; the raid tanks join the raid group in between."""
    ask(agent, base(tick=990, units=units))
    for unit_id in RAID_IDS:
        agent.ctx.groups.assign(unit_id, RAID)
    ask(agent, base(tick=995, units=units))


def naming(orders: list[dict], unit_ids: list[int]) -> list[dict]:
    """The orders that name any of the units."""
    return [order for order in orders if set(order.get("units", ())) & set(unit_ids)]


def priorities(agent, cmd: str) -> list[Priority]:
    return [entry.priority for entry in agent.ledger.entries if entry.order["cmd"] == cmd]


def tanks_at(x: int, count: int, first_id: int = 500, enemy_type: str = "1tnk") -> list[dict]:
    """`count` enemy tanks in a column at `x`, from y = 46 down."""
    return [enemy(first_id + number, enemy_type, x, 46 + number) for number in range(count)]


def test_a_minor_threat_is_answered_by_the_home_guard_alone_and_a_raid_40_cells_away_keeps_its_orders():
    agent, _ = defense_agent(raiding())
    units = [*guard_rifles(), situations.unit(300, "1tnk", 26, 52), *raid_tanks()]
    with_raid(agent, units)

    orders = ask(agent, base(units=units, visible_enemies=[enemy(500, "jeep", 32, 48)]))

    assert with_command(orders, "attack") == [{"cmd": "attack", "units": GUARD_IDS, "target": 500}]
    assert naming(orders, RAID_IDS) == [{"cmd": "attack_move", "units": RAID_IDS, "to": RAID_TARGET}]
    assert naming(orders, [300]) == [] and priorities(agent, "attack") == [Priority.EMERGENCY]
    assert not any(agent.ctx.groups.has_defense_override(group) for group in (GUARD, MAIN, RAID))


def test_the_guard_does_not_chase_the_enemy_of_a_minor_threat_beyond_15_cells_of_the_base():
    agent, _ = defense_agent()
    harvester = situations.unit(300, "harv", 50, 20, idle=False)
    attacked = [under_attack(30, 300, "harv", 50, 20)]

    orders = ask(
        agent, base(units=[*guard_rifles(), harvester], visible_enemies=[enemy(500, "e1", 54, 20)], events=attacked)
    )

    assert agent.threat_active and with_command(orders, "attack", "attack_move") == []


def test_a_major_threat_sends_every_own_combat_unit_within_25_cells_to_the_attacked_building_at_emergency_priority():
    agent, _ = defense_agent()
    near = [
        situations.unit(300, "1tnk", 40, 48),
        situations.unit(301, "e3", 22, 30),
        situations.unit(302, "jeep", 45, 48),
    ]
    far = [situations.unit(303, "2tnk", 48, 48)]
    harvester = situations.unit(304, "harv", 24, 40, idle=False)
    attacked = [under_attack(30, YARD_ID, "fact", 22, 48)]

    orders = ask(
        agent, base(units=[*guard_rifles(), *near, *far, harvester], visible_enemies=rifles_at(32), events=attacked)
    )

    expected = {"cmd": "attack_move", "units": [*GUARD_IDS, 300, 301, 302], "to": [22, 48]}
    assert with_command(orders, "attack_move") == [expected]
    assert priorities(agent, "attack_move") == [Priority.EMERGENCY]
    assert naming(orders, [303, 304]) == []


SCOUT_SLOTS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True))


@pytest.mark.parametrize("tuning, answers", [(SCOUT_SLOTS, False), (tuning_off(), True)], ids=["slots on", "slots off"])
def test_scout_slots_a_designated_scout_near_the_base_is_never_drawn_into_a_major_threat(tuning, answers):
    agent = new_agent(BaseDefenseManager(), tuning=tuning)
    scout = situations.unit(300, "jeep", 40, 48)
    ask(agent, base(tick=995, units=[*guard_rifles(), scout]))
    agent.ctx.groups.assign(300, SCOUT)

    orders = ask(agent, base(units=[*guard_rifles(), scout], visible_enemies=rifles_at(32)))

    (answer,) = with_command(orders, "attack_move")
    assert (300 in answer["units"]) is answers and set(GUARD_IDS) <= set(answer["units"])


# A medium tank of ours 4.5 cells from four enemy rifles bunched 11 cells from the construction yard: a major threat
# base defense sends the tank to, and a cluster the tank runs over, to (32, 41), 2 cells past the rifles' centroid.
CRUSH_TANK = situations.unit(300, "2tnk", 26, 40)
CRUSH_RIFLES = [enemy(500 + number, "e1", 30 + number % 2, 40 + number // 2) for number in range(4)]
CRUSH_RUN_MOVE = {"cmd": "move", "units": [300], "to": [32, 41]}
# Crush runs on, a single tank enough for one: one tank for every 4 rifles, the whole planned group of the four.
CRUSH_ON = tuning_off(combat=combat_off(crush_runs=True, crush_min_tanks=1, crush_per_tanks=4))


class Proposals:
    """A manager wrapped so that the orders it proposes in each cycle are kept, oldest first."""

    def __init__(self, manager) -> None:
        self.manager, self.name, self.cycles = manager, manager.name, []

    def intents(self, world, ctx) -> list[Intent]:
        proposed = self.manager.intents(world, ctx)
        self.cycles.append([intent.order for intent in proposed])
        return proposed


@pytest.mark.parametrize("group", [MAIN, GUARD])
def test_crush_run_a_tank_a_major_threat_draws_in_runs_over_4_rifles_and_base_defense_s_attack_move_skips_it(group):
    agent = new_agent(BaseDefenseManager(), CombatManager(), tuning=CRUSH_ON)
    units = [*guard_rifles(), CRUSH_TANK]
    ask(agent, base(tick=995, units=units))
    agent.ctx.groups.assign(300, group)

    orders = ask(agent, base(units=units, visible_enemies=CRUSH_RIFLES))

    assert agent.ctx.threat_level == "major" and naming(orders, [300]) == [CRUSH_RUN_MOVE]
    assert {"cmd": "attack_move", "units": GUARD_IDS, "to": [22, 48]} in orders


def test_crush_run_base_defense_gives_a_tank_on_a_run_no_order_while_it_lasts_and_sends_it_once_it_is_let_go():
    defense = Proposals(BaseDefenseManager())
    agent = new_agent(defense, CombatManager(), tuning=CRUSH_ON)
    units = [*guard_rifles(), CRUSH_TANK]
    ask(agent, base(tick=995, units=units))
    batches = [ask(agent, base(tick=tick, units=units, visible_enemies=CRUSH_RIFLES)) for tick in (1000, 1005, 1010)]
    # Two of the rifles are run over: the run ends as its cluster breaks, and base defense sends the tank after it.
    batches += [ask(agent, base(tick=tick, units=units, visible_enemies=CRUSH_RIFLES[:2])) for tick in (1015, 1020)]

    told = [naming(cycle, [300]) for cycle in defense.cycles[1:]]
    sent = {"cmd": "attack_move", "units": [300], "to": [22, 48]}
    assert [order["cmd"] for order in told[0]] == ["attack_move"] and told[1:] == [[], [], [], [sent]]
    assert naming(batches[0], [300]) == [CRUSH_RUN_MOVE] and naming(batches[-1], [300]) == [sent]


# A refinery out at (36, 40) with four enemy rifles bunched by it, and main's medium tank 7.5 cells beyond them, out of
# base defense's response radius: called home, it is moved to the staging point unless it runs over the rifles.
REFINERY_OUT = ("proc", 36, 40)
RIFLES_BY_THE_REFINERY = [enemy(500 + number, "e1", 40 + number % 2, 40 + number // 2) for number in range(4)]
TANK_BEYOND = situations.unit(300, "2tnk", 48, 40)


def test_crush_run_a_tank_of_main_called_home_runs_over_4_rifles_and_base_defense_does_not_move_it_home():
    defense = Proposals(BaseDefenseManager())
    agent = new_agent(defense, CombatManager(), tuning=CRUSH_ON)
    units = [*guard_rifles(), TANK_BEYOND]
    ask(agent, base(REFINERY_OUT, tick=995, units=units))
    agent.ctx.groups.recall(MAIN)

    first = ask(agent, base(REFINERY_OUT, tick=1000, units=units, visible_enemies=RIFLES_BY_THE_REFINERY))
    ask(agent, base(REFINERY_OUT, tick=1005, units=units, visible_enemies=RIFLES_BY_THE_REFINERY))

    home = {"cmd": "move", "units": [300], "to": STAGING}
    assert naming(defense.cycles[1], [300]) == [home] and naming(defense.cycles[2], [300]) == []
    assert naming(first, [300]) == [{"cmd": "move", "units": [300], "to": [39, 41]}]


def test_the_groups_of_the_units_answering_a_major_threat_engage_whatever_the_fight_estimate_says():
    agent, _ = defense_agent()
    units = [*guard_rifles(), situations.unit(300, "1tnk", 40, 48)]

    ask(agent, base(units=units, visible_enemies=rifles_at(32)))

    overrides = [agent.ctx.groups.has_defense_override(group) for group in (GUARD, MAIN, RAID)]
    assert overrides == [True, True, False]


def recall_case(agent, near: list[dict], enemies: list[dict]) -> list[dict]:
    """The orders answering a threat of `enemies`: the guard and `near` at home, raid tanks and a main tank far out."""
    units = [*guard_rifles(), *near, *raid_tanks(), situations.unit(310, "2tnk", 62, 60)]
    with_raid(agent, units)
    return ask(agent, base(units=units, visible_enemies=enemies))


def test_with_responders_worth_500_against_a_threat_worth_2800_the_groups_40_cells_out_are_recalled():
    agent, manager = defense_agent()

    orders = recall_case(agent, [], tanks_at(32, 4))

    assert (manager.threat.size, manager.threat.is_major) == (2800, True)
    assert naming(orders, [*RAID_IDS, 310]) == [{"cmd": "move", "units": [*RAID_IDS, 310], "to": STAGING}]
    assert agent.ctx.groups.is_recalled(RAID) and agent.ctx.groups.is_recalled(MAIN)
    assert agent.ctx.groups.has_defense_override(RAID)
    assert priorities(agent, "move") == [Priority.EMERGENCY]


# Units within 25 cells of the base worth 3,000 at full health.
RESPONDERS_WORTH_3000 = [("1tnk", 40, 48), ("1tnk", 40, 50), ("jeep", 36, 52), ("e3", 26, 52), ("e3", 26, 53)]


def test_with_responders_worth_3000_against_a_threat_worth_2800_no_group_is_recalled():
    agent, _ = defense_agent()
    near = [situations.unit(300 + number, *spec) for number, spec in enumerate(RESPONDERS_WORTH_3000)]

    orders = recall_case(agent, near, tanks_at(32, 4))

    assert naming(orders, [*RAID_IDS, 310]) == []
    assert not agent.ctx.groups.is_recalled(RAID) and not agent.ctx.groups.is_recalled(MAIN)
    assert with_command(orders, "attack_move")[0]["units"] == [*GUARD_IDS, 300, 301, 302, 303, 304]


def test_the_responders_are_weighed_by_the_health_they_have_left():
    agent, _ = defense_agent()
    near = [
        situations.unit(300 + number, unit_type, x, y, hp=50)
        for number, (unit_type, x, y) in enumerate(RESPONDERS_WORTH_3000)
    ]

    orders = recall_case(agent, near, tanks_at(32, 4))

    assert naming(orders, RAID_IDS) == [{"cmd": "move", "units": [*RAID_IDS, 310], "to": STAGING}]


def test_a_responder_standing_idle_on_the_threatened_cell_is_not_sent_there_again():
    agent, _ = defense_agent()
    units = [situations.unit(200, "e1", 22, 49), situations.unit(201, "e1", 23, 47, idle=False), *guard_rifles()[2:]]

    orders = ask(agent, base(units=units, visible_enemies=rifles_at(32)))

    expected = {"cmd": "attack_move", "units": [201, 202, 203, 204], "to": [22, 48]}
    assert with_command(orders, "attack_move") == [expected]


def test_a_responder_is_sent_to_the_threat_once_and_again_only_when_the_threat_moves_beyond_its_area():
    agent, _ = defense_agent()
    harvester = situations.unit(300, "harv", 50, 20, idle=False)
    units = [*guard_rifles(), harvester]

    def attack_moves(tick: int, *events: dict) -> list[dict]:
        state = base(tick=tick, units=units, visible_enemies=rifles_at(32), events=list(events))
        return with_command(ask(agent, state), "attack_move")

    first = attack_moves(1000)
    fighting = [attack_moves(1005), attack_moves(1100), attack_moves(1105, under_attack(30, POWER_ID, "powr", 19, 52))]
    moved = attack_moves(1110, under_attack(31, 300, "harv", 50, 20))

    assert first == [{"cmd": "attack_move", "units": GUARD_IDS, "to": [22, 48]}] and fighting == [[], [], []]
    assert moved == [{"cmd": "attack_move", "units": GUARD_IDS, "to": [50, 20]}]


FOCUS_ON = tuning_off(combat=combat_off(contact_focus=True))


def test_contact_focus_a_responder_in_contact_under_a_major_threat_gets_a_targeted_attack_and_the_rest_the_cell():
    agent = new_agent(BaseDefenseManager(), tuning=FOCUS_ON)
    units = [*guard_rifles(), situations.unit(300, "1tnk", 40, 48)]

    orders = ask(agent, base(units=units, visible_enemies=rifles_at(32)))

    # The guard's rifles stand 4 cells from the enemy rifles, in range; the tank 8 cells off is not in contact.
    attacks = with_command(orders, "attack")
    assert sorted(unit for order in attacks for unit in order["units"]) == GUARD_IDS
    assert {order["target"] for order in attacks} <= {500 + number for number in range(8)}
    assert with_command(orders, "attack_move") == [{"cmd": "attack_move", "units": [300], "to": [22, 48]}]
    assert set(priorities(agent, "attack")) == {Priority.EMERGENCY}


def test_contact_focus_a_responder_arrived_on_the_threatened_cell_attacks_an_enemy_within_its_reach():
    agent = new_agent(BaseDefenseManager(), tuning=FOCUS_ON)
    arrived = [situations.unit(300, "e1", 22, 49)]

    orders = ask(agent, base(units=arrived, visible_enemies=rifles_at(30)))

    # 8 cells from the nearest enemy rifle: out of contact (5 + 2), within the reach of 5 + 4.
    assert with_command(orders, "attack", "attack_move") == [{"cmd": "attack", "units": [300], "target": 505}]


def test_contact_focus_a_responder_is_told_to_attack_its_target_once_and_again_only_when_its_target_changes():
    agent = new_agent(BaseDefenseManager(), tuning=FOCUS_ON)
    units = guard_rifles()
    first = ask(agent, base(units=units, visible_enemies=rifles_at(32)))

    again = ask(agent, base(tick=1005, units=units, visible_enemies=rifles_at(32)))

    assert with_command(first, "attack") and with_command(again, "attack", "attack_move") == []


def test_contact_focus_off_a_responder_in_contact_under_a_major_threat_is_sent_to_the_cell():
    agent, _ = defense_agent()

    orders = ask(agent, base(units=guard_rifles(), visible_enemies=rifles_at(32)))

    assert with_command(orders, "attack", "attack_move") == [{"cmd": "attack_move", "units": GUARD_IDS, "to": [22, 48]}]


def test_the_guard_is_told_once_to_attack_the_enemy_of_a_minor_threat():
    agent, _ = defense_agent()
    jeep = [enemy(500, "jeep", 32, 48)]

    told = [
        with_command(ask(agent, base(tick=tick, units=guard_rifles(), visible_enemies=jeep)), "attack")
        for tick in (1000, 1005, 1100)
    ]

    assert told == [[{"cmd": "attack", "units": GUARD_IDS, "target": 500}], [], []]


def test_when_the_threat_clears_125_ticks_after_the_last_enemy_the_recalled_raid_gets_its_last_order_back():
    agent, _ = defense_agent(raiding(once=True))
    units = [*guard_rifles(), *raid_tanks(idle=False)]
    with_raid(agent, units)
    recalled = naming(ask(agent, base(units=units, visible_enemies=tanks_at(32, 4))), RAID_IDS)

    # On their way home the tanks are not told again what they were told less than 75 ticks ago.
    during = [naming(ask(agent, base(tick=tick, units=units)), RAID_IDS) for tick in (1005, 1070)]
    ask(agent, base(tick=1124, units=units))
    active_before_the_end = agent.threat_active
    restored = ask(agent, base(tick=1125, units=units))

    assert recalled == [{"cmd": "move", "units": RAID_IDS, "to": STAGING}] and during == [[], []]
    assert active_before_the_end and not agent.threat_active
    assert naming(restored, RAID_IDS) == [{"cmd": "attack_move", "units": RAID_IDS, "to": RAID_TARGET}]
    assert priorities(agent, "attack_move")[-1] == Priority.MOVE
    assert not agent.ctx.groups.is_recalled(RAID) and not agent.ctx.groups.has_defense_override(GUARD)


def test_a_recalled_unit_that_was_never_given_an_order_gets_none_when_the_threat_clears():
    agent, _ = defense_agent()
    units = [*guard_rifles(), *raid_tanks()]
    with_raid(agent, units)
    ask(agent, base(units=units, visible_enemies=tanks_at(32, 4)))

    restored = ask(agent, base(tick=1125, units=units))

    assert naming(restored, RAID_IDS) == [] and not agent.threat_active


def test_a_recalled_unit_lost_during_the_threat_is_left_out_of_what_is_given_back():
    agent, _ = defense_agent(raiding(once=True))
    units = [*guard_rifles(), *raid_tanks(idle=False)]
    with_raid(agent, units)
    ask(agent, base(units=units, visible_enemies=tanks_at(32, 4)))

    restored = ask(agent, base(tick=1125, units=units[:-1]))

    assert naming(restored, RAID_IDS) == [{"cmd": "attack_move", "units": RAID_IDS[:1], "to": RAID_TARGET}]


def test_an_order_the_game_rejected_is_not_what_a_recalled_unit_gets_back():
    targets = {995: RAID_TARGET, 1000: [70, 20]}

    def propose(world, ctx):
        members = ctx.groups.members(RAID)
        if world.tick not in targets or not members:
            return []
        return [Intent(Priority.MOVE, {"cmd": "attack_move", "units": members, "to": targets[world.tick]})]

    agent, _ = defense_agent(ScriptedManager("raids", propose))
    units = [*guard_rifles(), *raid_tanks(idle=False)]
    with_raid(agent, units)
    ask(agent, base(tick=1000, units=units))
    rejected = {"index": 0, "cmd": "attack_move", "status": "rejected", "reason": "invalid"}
    agent.on_action_result({"accepted": True, "status": "in_progress", "results": [rejected]}, situations.context())
    ask(agent, base(tick=1005, units=units, visible_enemies=tanks_at(32, 4)))

    restored = ask(agent, base(tick=1130, units=units))

    assert naming(restored, RAID_IDS) == [{"cmd": "attack_move", "units": RAID_IDS, "to": RAID_TARGET}]


RETREAT_BELOW_RATIO = 0.9


def holding_back(ratio: float) -> ScriptedManager:
    """A stand-in for the combat manager: a group whose fight estimate is below 0.9 retreats to the staging point.

    It honors base defense's override as the combat manager is to: a group under it engages whatever the ratio.
    """

    def propose(world, ctx):
        point = list(cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells))
        groups = ctx.groups
        retreating = [
            group
            for group in (GUARD, MAIN)
            if groups.members(group) and ratio < RETREAT_BELOW_RATIO and not groups.has_defense_override(group)
        ]
        return [
            Intent(Priority.MOVE, {"cmd": "move", "units": groups.members(group), "to": point}) for group in retreating
        ]

    return ScriptedManager("combat", propose)


def test_during_a_major_threat_the_responders_engage_though_the_fight_estimate_ratio_is_0_5():
    agent, _ = defense_agent(holding_back(0.5))
    units = [*guard_rifles(), situations.unit(300, "1tnk", 40, 48)]
    retreat_without_a_threat = with_command(ask(agent, base(tick=995, units=units)), "move")

    orders = ask(agent, base(units=units, visible_enemies=rifles_at(32)))

    assert [order["units"] for order in retreat_without_a_threat] == [GUARD_IDS, [300]]
    assert with_command(orders, "attack_move") == [{"cmd": "attack_move", "units": [*GUARD_IDS, 300], "to": [22, 48]}]
    assert with_command(orders, "move") == []


# -- harvesters ------------------------------------------------------------------------------------

PILLBOX = ("pbox", 27, 48)
FAR_PILLBOX = ("pbox", 12, 40)
HARVESTER_ID = 300


def harvester(x: int, y: int, *, idle: bool = False) -> dict:
    return situations.unit(HARVESTER_ID, "harv", x, y, idle=idle)


def defended(tick: int, harvester_at: tuple[int, int], *enemies: dict, idle: bool = False) -> dict:
    """The base with two pillboxes, its harvester at a cell and the enemies in sight."""
    return base(
        PILLBOX, FAR_PILLBOX, tick=tick, units=[harvester(*harvester_at, idle=idle)], visible_enemies=list(enemies)
    )


def test_a_harvester_with_an_enemy_tank_5_cells_away_runs_to_the_nearest_own_defense_and_harvests_125_ticks_later():
    agent, _ = defense_agent()

    fled = ask(agent, defended(1000, (40, 40), enemy(500, "1tnk", 45, 40)))
    followed = ask(agent, defended(1005, (35, 43), enemy(500, "1tnk", 43, 40)))
    sheltered = [ask(agent, defended(tick, (28, 47), idle=True)) for tick in (1010, 1129)]
    returned = ask(agent, defended(1130, (28, 47), idle=True))

    assert fled == [{"cmd": "move", "units": [HARVESTER_ID], "to": [27, 48]}]
    assert priorities(agent, "move") == [Priority.EMERGENCY]
    assert followed == [] and sheltered == [[], []]
    assert returned == [{"cmd": "harvest", "units": [HARVESTER_ID]}]
    assert agent.ctx.fleeing_harvesters == frozenset()


def test_a_harvester_runs_to_the_centre_of_the_base_when_no_defense_stands():
    agent, _ = defense_agent()

    orders = ask(agent, base(units=[harvester(40, 40)], visible_enemies=[enemy(500, "e1", 44, 40)]))

    assert orders == [{"cmd": "move", "units": [HARVESTER_ID], "to": [22, 48]}]


@pytest.mark.parametrize("x, flees", [(46, True), (47, False)], ids=["6 cells", "7 cells"])
def test_a_harvester_flees_from_an_enemy_within_6_cells_and_not_from_one_farther(x, flees):
    agent, _ = defense_agent()

    orders = ask(agent, defended(1000, (40, 40), enemy(500, "1tnk", x, 40)))

    assert bool(orders) is flees and (HARVESTER_ID in agent.ctx.fleeing_harvesters) is flees


def test_a_harvester_does_not_flee_from_an_enemy_that_does_not_fight():
    agent, _ = defense_agent()

    assert ask(agent, defended(1000, (40, 40), enemy(500, "harv", 42, 40))) == []


def test_an_enemy_lingering_within_10_cells_keeps_the_harvester_in_shelter():
    agent, _ = defense_agent()
    ask(agent, defended(1000, (40, 40), enemy(500, "1tnk", 45, 40)))
    lingering = enemy(500, "1tnk", 36, 48)

    # The tank 9 cells from the pillbox is a threat too; only what the harvester is told matters here.
    kept = [
        naming(ask(agent, defended(tick, (28, 47), lingering, idle=True)), [HARVESTER_ID])
        for tick in range(1005, 1300, 5)
    ]
    before = naming(ask(agent, defended(1419, (28, 47), idle=True)), [HARVESTER_ID])
    after = naming(ask(agent, defended(1420, (28, 47), idle=True)), [HARVESTER_ID])

    assert kept == [[]] * len(kept) and before == []
    assert after == [{"cmd": "harvest", "units": [HARVESTER_ID]}]


def test_upkeep_does_not_send_a_sheltering_harvester_back_to_work_however_long_it_idles():
    agent = new_agent(BaseDefenseManager(), UpkeepManager())
    ask(agent, defended(1000, (40, 40), enemy(500, "1tnk", 45, 40)))

    sent_back = [
        with_command(ask(agent, defended(tick, (28, 47), idle=True)), "harvest") for tick in range(1005, 1125, 5)
    ]
    returned = with_command(ask(agent, defended(1125, (28, 47), idle=True)), "harvest")

    assert sent_back == [[]] * len(sent_back) and returned == [{"cmd": "harvest", "units": [HARVESTER_ID]}]


def test_a_harvester_already_at_its_shelter_is_not_sent_there_again():
    agent, _ = defense_agent()

    orders = ask(agent, defended(1000, (28, 47), enemy(500, "1tnk", 33, 47)))

    assert with_command(orders, "move") == [] and agent.ctx.fleeing_harvesters == {HARVESTER_ID}


def test_a_fleeing_harvester_is_sent_to_its_shelter_once():
    agent, _ = defense_agent()
    tank = enemy(500, "1tnk", 45, 40)

    sent = [
        naming(ask(agent, defended(tick, (40, 40), tank, idle=True)), [HARVESTER_ID]) for tick in (1000, 1005, 1100)
    ]

    assert sent == [[{"cmd": "move", "units": [HARVESTER_ID], "to": [27, 48]}], [], []]


def test_a_harvester_lost_while_fleeing_is_forgotten():
    agent, _ = defense_agent()
    ask(agent, defended(1000, (40, 40), enemy(500, "1tnk", 45, 40)))

    ask(agent, base(PILLBOX, tick=1005))

    assert agent.ctx.fleeing_harvesters == frozenset()


def test_harvesters_are_never_part_of_a_combat_response():
    agent, _ = defense_agent()

    orders = ask(agent, base(units=[*guard_rifles(), harvester(24, 46)], visible_enemies=rifles_at(32)))

    assert all(HARVESTER_ID not in order["units"] for order in with_command(orders, "attack_move", "attack"))


# -- production while a major threat lasts --------------------------------------------------------


def trains(orders: list[dict]) -> list[dict]:
    return with_command(orders, "train")


def builds(orders: list[dict]) -> list[dict]:
    return with_command(orders, "build")


def test_during_a_vehicle_threat_the_infantry_queue_trains_rocket_infantry_three_at_a_time():
    agent, _ = defense_agent()

    orders = ask(agent, base(visible_enemies=tanks_at(32, 2)))

    assert trains(orders) == [{"cmd": "train", "item": "e3", "count": 3}]
    assert priorities(agent, "train") == [Priority.EMERGENCY]


def test_during_an_infantry_threat_the_infantry_queue_trains_rifles_three_at_a_time():
    agent, _ = defense_agent()

    orders = ask(agent, base(visible_enemies=rifles_at(32)))

    assert trains(orders) == [{"cmd": "train", "item": "e1", "count": 3}]


def test_without_rocket_infantry_on_offer_a_vehicle_threat_is_answered_with_rifles():
    agent, _ = defense_agent()

    orders = ask(agent, base(withheld=("e3",), visible_enemies=tanks_at(32, 2)))

    assert trains(orders) == [{"cmd": "train", "item": "e1", "count": 3}]


@pytest.mark.parametrize(
    "enemies, counter",
    [
        ([*tanks_at(32, 2), *rifles_at(33, first_id=600)], "e3"),
        ([enemy(500, "jeep", 32, 48), *rifles_at(33, first_id=600)], "e1"),
        ([enemy(500, "jeep", 32, 48), *rifles_at(33, count=5, first_id=600)], "e3"),
    ],
    ids=["tanks worth 1400 and rifles 800", "a jeep worth 500 and rifles 800", "a jeep and rifles worth 500 each"],
)
def test_the_counter_is_to_the_class_the_threat_is_mostly_worth_and_to_vehicles_on_a_tie(enemies, counter):
    agent, _ = defense_agent()

    orders = ask(agent, base(visible_enemies=enemies))

    assert [order["item"] for order in trains(orders)] == [counter]


def test_the_counters_wait_while_the_infantry_queue_holds_an_item():
    agent, _ = defense_agent()

    orders = ask(agent, base(production=[situations.job("Infantry", "e1")], visible_enemies=rifles_at(32)))

    assert trains(orders) == []


def test_the_counter_of_the_threat_is_kept_while_its_enemies_are_out_of_sight():
    agent, _ = defense_agent()
    ask(agent, base(visible_enemies=tanks_at(32, 4)))

    orders = ask(agent, base(tick=1080, events=[under_attack(30, YARD_ID, "fact", 22, 48)]))

    assert agent.threat_active and trains(orders) == [{"cmd": "train", "item": "e3", "count": 3}]


def test_a_major_threat_of_aircraft_alone_is_answered_with_no_counter_and_no_extra_defense():
    agent, manager = defense_agent()

    orders = ask(agent, base(visible_enemies=[enemy(500, "heli", 30, 48), enemy(501, "heli", 30, 49)]))

    assert manager.threat.is_major and trains(orders) == [] and builds(orders) == []


def test_a_minor_threat_trains_no_counter_and_claims_no_queue():
    agent, _ = defense_agent()

    orders = ask(agent, base(visible_enemies=[enemy(500, "jeep", 32, 48)]))

    assert trains(orders) == [] and builds(orders) == []
    assert not agent.ctx.claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")


def test_the_infantry_queue_is_claimed_while_a_major_threat_lasts_and_given_up_after():
    agent, _ = defense_agent()
    ask(agent, base(visible_enemies=rifles_at(32)))
    claimed = agent.ctx.claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")

    ask(agent, base(tick=1125))

    assert claimed and not agent.ctx.claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")


@pytest.mark.parametrize(
    "enemies, defense",
    [(rifles_at(32), "pbox"), (tanks_at(32, 2), "gun")],
    ids=["infantry", "vehicles"],
)
def test_an_allied_base_adds_a_pillbox_against_infantry_and_a_gun_against_vehicles(enemies, defense):
    agent, _ = defense_agent()

    orders = ask(agent, base(visible_enemies=enemies))

    assert builds(orders) == [{"cmd": "build", "item": defense}]
    assert priorities(agent, "build") == [Priority.EMERGENCY]


def test_at_most_one_extra_defense_is_ordered_within_any_120_seconds_and_one_for_each_threat():
    agent, _ = defense_agent()
    built_at = []

    # Three threats of 20 s each, the second within 120 s of the first defense, each over 8 s after its last sighting.
    for start in (1000, 2000, 4000):
        for tick in range(start, start + 500, 5):
            if builds(ask(agent, base(tick=tick, visible_enemies=rifles_at(32)))):
                built_at.append(tick)
        ask(agent, base(tick=start + 700))

    assert built_at == [1000, 4000]


def test_the_extra_defense_waits_while_the_defense_queue_holds_an_item():
    agent, _ = defense_agent()

    orders = ask(agent, base(production=[situations.job("Defense", "pbox")], visible_enemies=rifles_at(32)))

    assert builds(orders) == []


SOVIET_BASE = (("fact", 22, 48), ("powr", 19, 52), ("barr", 18, 44), ("proc", 26, 40), ("weap", 18, 54))


@pytest.mark.parametrize(
    "power_plants, built",
    [(2, [{"cmd": "build", "item": "tsla"}]), (1, [{"cmd": "build", "item": "powr"}])],
    ids=["the target of 20 left over", "short of it"],
)
def test_a_soviet_base_adds_a_tesla_coil_against_vehicles_only_with_the_power_target_left_for_it(power_plants, built):
    agent, _ = defense_agent()
    extra_plants = [("powr", 14, 48 + number * 3) for number in range(power_plants - 1)]

    orders = ask(agent, base_state("russia", *SOVIET_BASE, *extra_plants, visible_enemies=tanks_at(32, 2)))

    assert builds(orders) == built


# A tick at 380 s, from which upkeep's power target is 60.
MID_TARGET_TICK = 380 * 25


def soviet_with_headroom_100(*more_buildings, tick: int = MID_TARGET_TICK, **observation) -> dict:
    """The Soviet base with a second power plant and a flame tower: 200 provided, 100 drained; two tanks attack."""
    buildings = (*SOVIET_BASE, ("powr", 14, 48), ("ftur", 27, 46), *more_buildings)
    return base_state("russia", *buildings, tick=tick, visible_enemies=tanks_at(32, 2), **observation)


def test_with_headroom_100_and_a_target_of_60_no_tesla_coil_is_ordered_and_the_power_plant_is_first():
    agent, _ = defense_agent()

    orders = ask(agent, soviet_with_headroom_100())

    economy = agent.world.economy
    assert (economy.power_provided - economy.power_drained, power_target(agent.world, agent.ctx)) == (100, 60)
    assert builds(orders) == [{"cmd": "build", "item": "powr"}]


def test_while_the_power_plant_is_on_its_way_no_second_one_is_ordered_and_once_it_stands_the_tesla_coil_follows():
    agent, _ = defense_agent()
    ask(agent, soviet_with_headroom_100())
    in_the_queue = [situations.job("Building", "powr")]

    on_its_way = ask(agent, soviet_with_headroom_100(tick=MID_TARGET_TICK + 5, production=in_the_queue))
    # Within the minute between two extra defenses: the power plant did not count as one.
    standing = ask(agent, soviet_with_headroom_100(("powr", 14, 51), tick=MID_TARGET_TICK + 500))

    assert builds(on_its_way) == []
    assert builds(standing) == [{"cmd": "build", "item": "tsla"}]


def test_a_soviet_base_adds_a_flame_tower_against_infantry():
    agent, _ = defense_agent()

    orders = ask(agent, base_state("russia", *SOVIET_BASE, visible_enemies=rifles_at(32)))

    assert builds(orders) == [{"cmd": "build", "item": "ftur"}]


def test_counter_production_is_still_queued_while_upkeep_saves_for_a_lost_refinery():
    agent = new_agent(BaseDefenseManager(), UpkeepManager())
    after_the_opening = (("proc", 26, 40), ("weap", 18, 54), ("powr", 16, 48))

    quiet = trains(ask(agent, base(*after_the_opening, tick=995, cash=1000)))
    attacked = trains(ask(agent, base(*after_the_opening, tick=1000, cash=1000, visible_enemies=rifles_at(32))))

    assert [order["item"] for order in quiet if order["item"] != "harv"] == []
    assert {"cmd": "train", "item": "e1", "count": 3} in attacked


# -- no counter rifles once tanks are the army ----------------------------------------------------

TANK_ARMY = tuning_off(mix=dataclasses.replace(tuning_off().mix, main_force="tanks"))
MIXED_ARMY = tuning_off(mix=dataclasses.replace(tuning_off().mix, main_force="mixed"))
WAR_FACTORY = ("weap", 18, 54)
TRAIN_RIFLES_3, TRAIN_ROCKETS_3 = {"cmd": "train", "item": "e1", "count": 3}, {"cmd": "train", "item": "e3", "count": 3}
# 20 enemy rifles worth 2,000 in a block of four columns 8 to 11 cells east of the construction yard.
TWENTY_RIFLES = [enemy(500 + number, "e1", 30 + number % 4, 46 + number // 4) for number in range(20)]


def tank_main(count: int = 6) -> list[dict]:
    """main's light tanks, side by side from (10, 60) behind the base."""
    return [situations.unit(330 + number, "1tnk", 10 + number, 60) for number in range(count)]


def counter_orders(agent, enemies: list[dict], *buildings, units: list[dict], tick: int = 1000) -> list[dict]:
    """The train orders of a cycle in which the base with the buildings given sees the enemies and has the units."""
    return trains(ask(agent, base(*buildings, tick=tick, units=units, visible_enemies=enemies)))


def test_tank_army_with_a_war_factory_and_main_of_4_20_enemy_rifles_get_no_counter_and_one_event_a_threat(
    match_record,
):
    agent = new_agent(BaseDefenseManager(), tuning=TANK_ARMY)

    first = counter_orders(agent, TWENTY_RIFLES, WAR_FACTORY, units=tank_main(4))
    later = counter_orders(agent, TWENTY_RIFLES, WAR_FACTORY, units=tank_main(4), tick=1005)
    held = agent.ctx.claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")
    ask(agent, base(WAR_FACTORY, tick=1700, units=tank_main(4)))
    second_threat = counter_orders(agent, TWENTY_RIFLES, WAR_FACTORY, units=tank_main(4), tick=1800)

    assert (first, later, second_threat, held) == ([], [], [], False)
    skipped = [event for event in match_record("base_defense") if event["reason"] == "counter_skipped_tanks"]
    assert skipped == [
        {
            "reason": "counter_skipped_tanks",
            "tick": 1000,
            "threat_cell": [22, 48],
            "threat_size": 2000,
            "main_units": 4,
        },
        {
            "reason": "counter_skipped_tanks",
            "tick": 1800,
            "threat_cell": [22, 48],
            "threat_size": 2000,
            "main_units": 4,
        },
    ]


@pytest.mark.parametrize(
    "enemies, buildings, main, tuning, counter",
    [
        (tanks_at(32, 4), (WAR_FACTORY,), 6, TANK_ARMY, TRAIN_ROCKETS_3),
        (TWENTY_RIFLES, (), 6, TANK_ARMY, TRAIN_RIFLES_3),
        (TWENTY_RIFLES, (WAR_FACTORY,), 3, TANK_ARMY, TRAIN_RIFLES_3),
        (
            [enemy(500, "jeep", 32, 48), *rifles_at(33, count=5, first_id=600)],
            (WAR_FACTORY,),
            6,
            TANK_ARMY,
            TRAIN_ROCKETS_3,
        ),
        (TWENTY_RIFLES, (WAR_FACTORY,), 6, MIXED_ARMY, TRAIN_RIFLES_3),
    ],
    ids=[
        "4 enemy tanks get rockets",
        "no war factory",
        "main of 3 tanks",
        "a jeep and rifles worth 500 each is vehicle-class",
        "main_force mixed",
    ],
)
def test_tank_army_counters_are_trained_as_before_against_vehicles_before_a_war_factory_or_with_main_under_4(
    enemies, buildings, main, tuning, counter, match_record
):
    agent = new_agent(BaseDefenseManager(), tuning=tuning)

    orders = counter_orders(agent, enemies, *buildings, units=tank_main(main))

    assert orders == [counter]
    assert agent.ctx.claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")
    assert [event for event in match_record("base_defense") if event["reason"] == "counter_skipped_tanks"] == []


# -- repair under attack ---------------------------------------------------------------------------

REPAIR_POWER = {"cmd": "repair", "building": POWER_ID, "on": True}


def power_plant_hit(tick: int = 1000, hp: int = 90, *, repairing: bool = False, seq: int = 30, **observation) -> dict:
    """The base with its power plant at `hp` and reported under attack."""
    state = base(tick=tick, events=[under_attack(seq, POWER_ID, "powr", 19, 52, hp)], **observation)
    plant = next(building for building in state["observation"]["buildings"] if building["id"] == POWER_ID)
    plant.update(hp=hp, repairing=repairing)
    return state


def repairs(orders: list[dict]) -> list[dict]:
    return with_command(orders, "repair")


@pytest.mark.parametrize("cash, ore, repaired", [(100, 0, True), (60, 40, True), (99, 0, False)])
def test_a_building_under_attack_at_90_percent_is_repaired_when_100_can_be_spent(cash, ore, repaired):
    agent, _ = defense_agent()

    orders = ask(agent, power_plant_hit(cash=cash, ore=ore))

    assert repairs(orders) == ([REPAIR_POWER] if repaired else [])


def test_the_repair_of_a_building_under_attack_goes_at_emergency_priority():
    agent, _ = defense_agent()

    ask(agent, power_plant_hit())

    assert priorities(agent, "repair") == [Priority.EMERGENCY]


@pytest.mark.parametrize("hp, repairing", [(96, False), (60, True)], ids=["above 95%", "already repairing"])
def test_a_building_under_attack_above_95_percent_or_already_repairing_is_left_alone(hp, repairing):
    agent, _ = defense_agent()

    assert repairs(ask(agent, power_plant_hit(hp=hp, repairing=repairing))) == []


def test_a_building_that_lost_hp_with_an_enemy_near_is_repaired():
    agent, _ = defense_agent()
    raider = [enemy(500, "e1", 24, 58)]
    ask(agent, base(tick=995, visible_enemies=raider))
    state = base(visible_enemies=raider)
    next(building for building in state["observation"]["buildings"] if building["id"] == POWER_ID)["hp"] = 85

    assert repairs(ask(agent, state)) == [REPAIR_POWER]


def test_a_repair_the_game_has_not_answered_against_is_not_sent_again_within_250_ticks():
    agent, _ = defense_agent()

    sent = [
        repairs(ask(agent, power_plant_hit(tick, seq=30 + number)))
        for number, tick in enumerate((1000, 1005, 1249, 1250))
    ]

    assert sent == [[REPAIR_POWER], [], [], [REPAIR_POWER]]


def test_a_harvester_under_attack_gets_no_repair():
    agent, _ = defense_agent()

    orders = ask(agent, base(units=[harvester(40, 40)], events=[under_attack(30, HARVESTER_ID, "harv", 40, 40)]))

    assert repairs(orders) == []


def test_with_upkeep_a_badly_damaged_building_under_attack_gets_one_repair_order():
    agent = new_agent(BaseDefenseManager(), UpkeepManager())

    assert repairs(ask(agent, power_plant_hit(hp=50))) == [REPAIR_POWER]


# -- the vehicle attacks of the match --------------------------------------------------------------

JEEP_NEAR = enemy(500, "jeep", 32, 48)
POWER_CELL, FAR_HARVESTER_CELL = (19, 52), (50, 20)


def power_plant_attacked(tick: int, seq: int, *enemies: dict) -> dict:
    """The base with the power plant reported under attack at a tick, the enemies given in sight."""
    return base(tick=tick, visible_enemies=list(enemies), events=[under_attack(seq, POWER_ID, "powr", *POWER_CELL)])


def far_harvester(tick: int, *enemies: dict, hit_seq: int | None = None) -> dict:
    """The base with its harvester 40 cells out, reported under attack at a tick when `hit_seq` is given, the enemies
    given in sight."""
    hit = [] if hit_seq is None else [under_attack(hit_seq, HARVESTER_ID, "harv", *FAR_HARVESTER_CELL)]
    return base(tick=tick, units=[harvester(*FAR_HARVESTER_CELL)], visible_enemies=list(enemies), events=hit)


def test_on_threat_only_one_vehicle_led_threat_adds_one_vehicle_attack_and_a_minor_one_counts():
    agent, manager = defense_agent()

    for number, tick in enumerate((1000, 1005, 1010, 1100)):
        ask(agent, power_plant_attacked(tick, 30 + number, JEEP_NEAR))

    assert not manager.threat.is_major and agent.ctx.threat_level == "minor"
    assert agent.ctx.vehicle_attacks == [POWER_CELL]


@pytest.mark.parametrize(
    "enemies, is_vehicle_attack",
    [
        ([JEEP_NEAR], True),
        (rifles_at(32), False),
        ([*tanks_at(32, 1), *rifles_at(33, count=2, first_id=600)], True),
        ([JEEP_NEAR, *rifles_at(33, count=5, first_id=600)], True),
        ([JEEP_NEAR, *rifles_at(33, count=6, first_id=600)], False),
    ],
    ids=[
        "a jeep",
        "rifles",
        "a tank worth 700 and rifles 200",
        "a jeep and rifles worth 500 each",
        "rifles worth more",
    ],
)
def test_on_threat_only_a_threat_that_hit_the_base_is_a_vehicle_attack_when_vehicles_are_most_of_its_worth(
    enemies, is_vehicle_attack
):
    agent, manager = defense_agent()

    ask(agent, power_plant_attacked(1000, 30, *enemies))

    is_led = largest_class(manager.threat.enemies, agent.world) == "Vehicle"
    assert (is_led, agent.ctx.vehicle_attacks) == (is_vehicle_attack, [POWER_CELL] if is_vehicle_attack else [])


def test_on_threat_only_a_threat_that_hits_three_cycles_after_it_began_is_recorded_once_at_that_cycle_s_cell():
    agent, manager = defense_agent()
    for tick in (1000, 1005, 1010):
        ask(agent, far_harvester(tick, JEEP_NEAR))
    before = (manager.threat.cell, list(agent.ctx.vehicle_attacks))

    ask(agent, far_harvester(1015, JEEP_NEAR, hit_seq=30))
    ask(agent, far_harvester(1020, JEEP_NEAR, hit_seq=31))

    assert before == ((22, 48), [])
    assert agent.ctx.vehicle_attacks == [FAR_HARVESTER_CELL]


def test_on_threat_only_a_threat_that_never_hits_the_base_is_no_vehicle_attack():
    agent, _ = defense_agent()

    active = [is_active_at(agent, tick, visible_enemies=[JEEP_NEAR]) for tick in range(1000, 1100, 5)]
    ended = not is_active_at(agent, 1300)

    assert all(active) and ended and agent.ctx.vehicle_attacks == []


def test_on_threat_only_each_vehicle_attack_after_the_last_threat_ended_is_recorded_again():
    agent, _ = defense_agent()
    ask(agent, power_plant_attacked(1000, 30, JEEP_NEAR))
    ended = not is_active_at(agent, 1125)

    ask(agent, far_harvester(1200, enemy(501, "1tnk", 52, 24), hit_seq=31))

    assert ended and agent.ctx.vehicle_attacks == [POWER_CELL, FAR_HARVESTER_CELL]


# -- main's answer to a harvester attacked away from the base ---------------------------------------------------------

HARVESTER_RESPONSE = dataclasses.replace(
    tuning_off(), base_defense=dataclasses.replace(tuning_off().base_defense, harvester_group_response=True)
)
# A harvester 25 cells from the centre of the base at (22, 48), and three enemy tanks worth 2,100 around it: a major
# threat away from the base.
HARVESTER_OUT = (37, 28)
RAIDERS = [enemy(500, "1tnk", 40, 25), enemy(501, "1tnk", 41, 26), enemy(502, "1tnk", 40, 27)]
# main: three medium tanks, which no guard quota takes; and a jeep near the base that a test puts into the raid group.
MAIN_IDS = [310, 311, 312]
RAID_JEEP = situations.unit(320, "jeep", 26, 52)


class FixedPlan:
    """A plan selector that keeps the agent in the plan a test sets."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def main_tanks(at: tuple[int, int] = (30, 40), hp: int = 100) -> list[dict]:
    """main's three tanks side by side from a cell, standing idle."""
    return [situations.unit(unit_id, "2tnk", at[0] + number, at[1], hp=hp) for number, unit_id in enumerate(MAIN_IDS)]


def harvester_hit(tick: int, units: list[dict], at=HARVESTER_OUT, enemies=RAIDERS) -> dict:
    """The base with its units, its harvester at a cell reported under attack at a tick, and the enemies in sight."""
    hit = [under_attack(tick, HARVESTER_ID, "harv", *at)]
    return base(tick=tick, units=[harvester(*at), *units], visible_enemies=list(enemies), events=hit)


def responding_agent(plan: str = "HOLD", tuning: Tuning = HARVESTER_RESPONSE, *more):
    """An agent with base defense and the managers given, held in a plan, and the plan selector to change it by."""
    selector = FixedPlan(plan)
    return new_agent(BaseDefenseManager(), *more, tuning=tuning, plan_selector=selector), selector


def at_home(agent, units: list[dict]) -> None:
    """Show the agent its units at 995, no enemy near, and put the jeep 320 into the raid group."""
    ask(agent, base(tick=995, units=units))
    agent.ctx.groups.assign(RAID_JEEP["id"], RAID)


HOME = [*guard_rifles(), *main_tanks(), RAID_JEEP]


@pytest.mark.parametrize(
    "tuning, answering",
    [(HARVESTER_RESPONSE, MAIN_IDS), (tuning_off(), [*GUARD_IDS, *MAIN_IDS, 320])],
    ids=["switch on: main alone", "switch off: everyone near the base"],
)
def test_harvester_group_response_a_major_threat_on_a_harvester_25_cells_out_under_hold_is_main_s(tuning, answering):
    agent, _ = responding_agent(tuning=tuning)
    at_home(agent, HOME)

    orders = ask(agent, harvester_hit(1000, HOME))

    assert (agent.ctx.threat_level, agent.ctx.threat_cell) == ("major", HARVESTER_OUT)
    assert with_command(orders, "attack_move") == [{"cmd": "attack_move", "units": answering, "to": [37, 28]}]
    assert priorities(agent, "attack_move") == [Priority.EMERGENCY]
    assert {"cmd": "move", "units": [HARVESTER_ID], "to": [22, 48]} in orders
    assert agent.ctx.groups.has_defense_override(MAIN)


def test_harvester_group_response_no_guard_unit_nor_other_unit_near_the_base_is_given_any_order():
    agent, _ = responding_agent()
    at_home(agent, HOME)

    orders = [ask(agent, harvester_hit(tick, HOME)) for tick in (1000, 1005)]

    assert [naming(cycle, [*GUARD_IDS, 320]) for cycle in orders] == [[], []]
    assert (agent.ctx.groups.has_defense_override(GUARD), agent.ctx.groups.is_recalled(RAID)) == (False, False)
    assert with_command(orders[1], "attack_move") == []


@pytest.mark.parametrize(
    "plan, at, enemies, main_hp",
    [
        ("PRESS", HARVESTER_OUT, RAIDERS, 100),
        ("HOLD", (26, 38), [enemy(500, "1tnk", 29, 35), enemy(501, "1tnk", 30, 34)], 100),
        ("HOLD", HARVESTER_OUT, RAIDERS, 50),
    ],
    ids=["under PRESS", "within 12 cells of the base", "no main unit fit to fight"],
)
def test_harvester_group_response_a_threat_the_rule_does_not_handle_gets_today_s_response(plan, at, enemies, main_hp):
    agent, _ = responding_agent(plan)
    units = [*guard_rifles(), *main_tanks(hp=main_hp)]
    at_home(agent, [*units, RAID_JEEP])

    orders = ask(agent, harvester_hit(1000, [*units, RAID_JEEP], at, enemies))

    (answer,) = with_command(orders, "attack_move")
    assert set(GUARD_IDS) <= set(answer["units"]) and answer["to"] == list(at)


def test_harvester_group_response_when_the_plan_turns_to_press_main_is_let_go_and_today_s_response_follows():
    agent, selector = responding_agent()
    at_home(agent, HOME)
    ask(agent, harvester_hit(1000, HOME))
    selector.plan = "PRESS"

    orders = ask(agent, harvester_hit(1005, HOME))

    assert naming(with_command(orders, "attack_move"), GUARD_IDS)[0]["to"] == [37, 28]


def test_harvester_group_response_after_1500_ticks_main_is_let_go_and_its_next_order_is_the_plan_s():
    agent, _ = responding_agent("HOLD", HARVESTER_RESPONSE, CombatManager())
    gathered = [*guard_rifles(), *main_tanks(at=(32, 48))]
    ask(agent, base(tick=995, units=gathered))
    sent = ask(agent, harvester_hit(1000, gathered))
    out = [*guard_rifles(), *main_tanks(at=(36, 31))]

    answering = ask(agent, harvester_hit(2495, out, enemies=()))
    overridden = agent.ctx.groups.has_defense_override(MAIN)
    let_go = ask(agent, harvester_hit(2500, out, enemies=()))

    assert naming(sent, MAIN_IDS) == [{"cmd": "attack_move", "units": MAIN_IDS, "to": [37, 28]}]
    assert (naming(answering, MAIN_IDS), overridden, agent.threat_active) == ([], True, True)
    assert not agent.ctx.groups.has_defense_override(MAIN)
    assert naming(let_go, MAIN_IDS) == [{"cmd": "move", "units": MAIN_IDS, "to": STAGING}]


def main_of(*kinds: tuple[str, int]) -> list[dict]:
    """main's units, each of a type at a health, side by side from (30, 40), standing idle."""
    return [
        situations.unit(310 + number, unit_type, 30 + number, 40, hp=hp) for number, (unit_type, hp) in enumerate(kinds)
    ]


# main worth 4,200 and 1,680 against the raiders' 2,100: estimates of 2.0 and 0.8.
STRONG_MAIN = [("1tnk", 100)] * 6
WEAK_MAIN = [("1tnk", 100), ("1tnk", 100), ("1tnk", 40)]
ROCKETS_3 = {"cmd": "train", "item": "e3", "count": 3}


@pytest.mark.parametrize(
    "main, ratio, counters",
    [(STRONG_MAIN, 2.0, []), (WEAK_MAIN, 0.8, [ROCKETS_3])],
    ids=["main's estimate 2.0", "main's estimate 0.8"],
)
def test_harvester_group_response_no_counter_infantry_while_main_answers_at_1_3_or_more(main, ratio, counters):
    agent, _ = responding_agent()
    units = [*guard_rifles(), *main_of(*main), RAID_JEEP]
    at_home(agent, units)

    orders = ask(agent, harvester_hit(1000, units))

    main_units = [agent.world.combat_units[unit_id] for unit_id in agent.ctx.groups.members(MAIN)]
    raiders = [agent.world.enemy_memory[raider["id"]] for raider in RAIDERS]
    assert ratio_against(main_units, raiders, agent.world, agent.ctx.tuning) == pytest.approx(ratio)
    assert trains(orders) == counters
    assert agent.ctx.claims.is_held_by_other(INFANTRY_QUEUE, "upkeep") is bool(counters)


def test_harvester_group_response_once_main_is_let_go_the_counters_of_the_threat_are_trained_as_today():
    agent, _ = responding_agent()
    units = [*guard_rifles(), *main_of(*STRONG_MAIN), RAID_JEEP]
    at_home(agent, units)

    held = trains(ask(agent, harvester_hit(1000, units)))
    let_go = trains(ask(agent, harvester_hit(2500, units)))

    assert (held, let_go) == ([], [ROCKETS_3])


def answer_steps(events: list[dict]) -> list[tuple[str, list[int]]]:
    """The outcome and threat cell of each `base_defense` event of main's answer, in order."""
    assert {(event["response"], event["group"]) for event in events} <= {("group", MAIN)}
    return [(event["outcome"], event["threat_cell"]) for event in events]


def test_harvester_group_response_main_s_answer_is_written_sent_arrived_and_timed_out(match_record):
    agent, _ = responding_agent()
    gathered = [*guard_rifles(), *main_tanks(at=(32, 48))]
    ask(agent, base(tick=995, units=gathered))
    ask(agent, harvester_hit(1000, gathered))
    out = [*guard_rifles(), *main_tanks(at=(36, 31))]

    for tick in (2495, 2500, 2505):
        ask(agent, harvester_hit(tick, out, enemies=()))

    # main stood 20.6 cells from the threat's cell at 1000, and 3 cells from it at 2495.
    steps = answer_steps(match_record("base_defense"))
    assert steps == [("sent", [37, 28]), ("arrived", [37, 28]), ("timed_out", [37, 28])]


def test_harvester_group_response_main_s_answer_ends_with_the_threat_and_is_written_ended(match_record):
    agent, _ = responding_agent()
    at_home(agent, HOME)
    ask(agent, harvester_hit(1000, HOME))

    ask(agent, base(tick=1130, units=[harvester(*HARVESTER_OUT), *HOME]))

    assert (agent.threat_active, agent.ctx.groups.has_defense_override(MAIN)) == (False, False)
    assert answer_steps(match_record("base_defense")) == [
        ("sent", [37, 28]),
        ("arrived", [37, 28]),
        ("ended", [37, 28]),
    ]


def test_harvester_group_response_main_handed_over_to_today_s_response_is_written_handed_over(match_record):
    agent, selector = responding_agent()
    at_home(agent, HOME)
    ask(agent, harvester_hit(1000, HOME))
    selector.plan = "PRESS"

    ask(agent, harvester_hit(1005, HOME))

    steps = answer_steps(match_record("base_defense"))
    assert steps == [("sent", [37, 28]), ("arrived", [37, 28]), ("handed_over", [37, 28])]


def test_harvester_group_response_off_writes_no_base_defense_event(match_record):
    agent, _ = responding_agent(tuning=tuning_off())
    at_home(agent, HOME)

    ask(agent, harvester_hit(1000, HOME))

    assert match_record("base_defense") == []


# -- base defense's override on single units, as the managers that direct the groups read it ---------------------------


@pytest.mark.parametrize("marked, ordered", [((), MAIN_IDS), ((310,), [311, 312])], ids=["none marked", "310 marked"])
def test_unit_override_combat_leaves_a_unit_of_main_base_defense_took_alone_and_orders_the_rest(marked, ordered):
    agent = new_agent(CombatManager(), plan_selector=FixedPlan("HOLD"))
    agent.ctx.groups.set_unit_override(marked)

    orders = ask(agent, base(units=main_tanks()))

    assert naming(orders, MAIN_IDS) == [{"cmd": "move", "units": ordered, "to": STAGING}]
    assert not agent.ctx.groups.has_defense_override(MAIN)


# -- main kept on the attack (keep_attack_army) -------------------------------------------------------------------------

KEEP_ATTACK_ARMY = dataclasses.replace(
    tuning_off(), base_defense=dataclasses.replace(tuning_off().base_defense, keep_attack_army=True)
)
# main: a tank 23.4 cells from the centre of the base, within the response radius, and one 40 cells out.
MAIN_NEAR, MAIN_FAR = 310, 311
ATTACKING_MAIN = [situations.unit(MAIN_NEAR, "2tnk", 30, 26), situations.unit(MAIN_FAR, "2tnk", 62, 60)]
# A pillbox behind the construction yard, 6 cells from it: the yard stays the building nearest to the enemies.
HOME_PILLBOX = ("pbox", 16, 48)
# Eight rifles worth 800 against the guard's 500 and the pillbox's 600 at home; four tanks worth 2,800.
SMALL_THREAT, LARGE_THREAT = rifles_at(32), tanks_at(32, 4)


def attacked_in(plan: str, enemies: list[dict], tuning: Tuning = KEEP_ATTACK_ARMY, *, pillbox: bool = True):
    """An agent with base defense and combat held in a plan, and its orders in the cycle an attack of `enemies` comes:
    at home the guard's five rifles and a pillbox (unless left out), main's two tanks near and far."""
    agent = new_agent(BaseDefenseManager(), CombatManager(), tuning=tuning, plan_selector=FixedPlan(plan))
    buildings = [HOME_PILLBOX] if pillbox else []
    return agent, ask(agent, base(*buildings, units=[*guard_rifles(), *ATTACKING_MAIN], visible_enemies=enemies))


def sent_to_the_threat(agent, orders: list[dict]) -> list[int]:
    """The units of the orders that send them to the threat's cell."""
    cell = list(agent.ctx.threat_cell)
    return sorted(unit_id for order in orders if order.get("to") == cell for unit_id in order["units"])


@pytest.mark.parametrize("plan", ["PRESS", "STRIKE", "FINISH"])
def test_keep_attack_army_a_threat_the_guard_and_the_pillbox_outweigh_takes_no_unit_of_main(plan):
    agent, orders = attacked_in(plan, SMALL_THREAT)

    main_orders = naming(orders, [MAIN_NEAR, MAIN_FAR])
    assert (agent.ctx.threat_level, sent_to_the_threat(agent, orders)) == ("major", GUARD_IDS)
    assert main_orders and not agent.ctx.groups.is_recalled(MAIN)
    assert not any(agent.ctx.groups.is_overridden(unit_id) for unit_id in (MAIN_NEAR, MAIN_FAR))


def test_keep_attack_army_under_finish_main_keeps_its_combat_orders_while_the_guard_answers():
    agent, orders = attacked_in("FINISH", SMALL_THREAT)
    later = ask(
        agent, base(HOME_PILLBOX, tick=1005, units=[*guard_rifles(), *ATTACKING_MAIN], visible_enemies=SMALL_THREAT)
    )

    main_orders = naming([*orders, *later], [MAIN_NEAR, MAIN_FAR])
    main_priorities = {
        entry.priority for entry in agent.ledger.entries if {MAIN_NEAR, MAIN_FAR} & set(entry.order.get("units", ()))
    }
    assert main_orders and list(agent.ctx.threat_cell) not in [order.get("to") for order in main_orders]
    assert main_priorities == {Priority.MOVE}


def test_keep_attack_army_a_threat_worth_more_than_the_guard_and_the_pillbox_gets_today_s_response_recall_included():
    agent, orders = attacked_in("FINISH", LARGE_THREAT)
    _, without_switch = attacked_in("FINISH", LARGE_THREAT, tuning_off())

    assert (
        sent_to_the_threat(agent, orders)
        == sent_to_the_threat(agent, without_switch)
        == sorted([*GUARD_IDS, MAIN_NEAR])
    )
    assert naming(orders, [MAIN_FAR]) == naming(without_switch, [MAIN_FAR])
    assert naming(orders, [MAIN_FAR]) == [{"cmd": "move", "units": [MAIN_FAR], "to": STAGING}]
    assert agent.ctx.groups.is_recalled(MAIN) and agent.ctx.groups.is_recalled(RAID)


@pytest.mark.parametrize(
    "plan, tuning, pillbox",
    [("HOLD", KEEP_ATTACK_ARMY, True), ("FINISH", tuning_off(), True), ("FINISH", KEEP_ATTACK_ARMY, False)],
    ids=["under HOLD", "switch off", "no pillbox: 800 against the guard's 500"],
)
def test_keep_attack_army_main_near_the_base_answers_as_today_under_hold_off_or_when_the_guard_is_short(
    plan, tuning, pillbox
):
    agent, orders = attacked_in(plan, SMALL_THREAT, tuning, pillbox=pillbox)

    assert sent_to_the_threat(agent, orders) == sorted([*GUARD_IDS, MAIN_NEAR])
    assert not agent.ctx.groups.is_recalled(MAIN)


def test_keep_attack_army_under_finish_only_the_units_answering_the_threat_carry_the_override():
    agent, _ = attacked_in("FINISH", SMALL_THREAT)

    groups = agent.ctx.groups
    overridden = [unit_id for unit_id in [*GUARD_IDS, MAIN_NEAR, MAIN_FAR] if groups.is_overridden(unit_id)]
    assert overridden == GUARD_IDS
    assert not any(groups.has_defense_override(group) for group in (GUARD, MAIN, RAID))


@pytest.mark.parametrize(
    "tuning, far_out", [(KEEP_ATTACK_ARMY, [{"cmd": "move", "units": [MAIN_FAR], "to": STAGING}]), (tuning_off(), [])]
)
def test_keep_attack_army_under_hold_main_s_unit_far_out_keeps_combat_s_orders_while_the_one_near_answers(
    tuning, far_out
):
    agent, orders = attacked_in("HOLD", SMALL_THREAT, tuning)

    groups = agent.ctx.groups
    assert sent_to_the_threat(agent, orders) == sorted([*GUARD_IDS, MAIN_NEAR])
    assert (groups.is_overridden(MAIN_NEAR), groups.is_overridden(MAIN_FAR)) == (True, not far_out)
    assert groups.has_defense_override(MAIN) is not far_out and naming(orders, [MAIN_FAR]) == far_out


def test_keep_attack_army_the_marks_of_the_units_answering_are_cleared_when_the_threat_ends():
    agent, _ = attacked_in("FINISH", SMALL_THREAT)
    marked = [unit_id for unit_id in GUARD_IDS if agent.ctx.groups.is_overridden(unit_id)]

    ask(agent, base(HOME_PILLBOX, tick=1125, units=[*guard_rifles(), *ATTACKING_MAIN]))

    assert marked == GUARD_IDS and not agent.threat_active
    assert not any(agent.ctx.groups.is_overridden(unit_id) for unit_id in GUARD_IDS)


@pytest.mark.parametrize("tuning, group_marked", [(KEEP_ATTACK_ARMY, False), (tuning_off(), True)], ids=["on", "off"])
def test_keep_attack_army_a_group_called_home_has_its_members_marked_one_by_one(tuning, group_marked):
    agent, _ = attacked_in("FINISH", LARGE_THREAT, tuning)

    groups = agent.ctx.groups
    assert groups.is_recalled(MAIN) and groups.is_overridden(MAIN_FAR)
    assert (groups.has_defense_override(MAIN), groups.has_defense_override(RAID)) == (group_marked, group_marked)


# Two Yaks over the base, worth 2,700; a rocket soldier of main by the base, whose weapon hits aircraft.
AIR_RAID = [enemy(500, "yak", 30, 48), enemy(501, "yak", 31, 50)]
ROCKET_ID = 330
ROCKET = situations.unit(ROCKET_ID, "e3", 26, 52)
AIR_HOME = [*guard_rifles(), ROCKET, *ATTACKING_MAIN]


def air_raided(tuning: Tuning, *more, plan: str = "HOLD", **observation):
    """An agent with base defense and the managers given held in a plan, and its orders in the cycle the Yaks come."""
    agent = new_agent(BaseDefenseManager(), *more, tuning=tuning, plan_selector=FixedPlan(plan))
    return agent, ask(agent, base(units=AIR_HOME, visible_enemies=AIR_RAID, **observation))


@pytest.mark.parametrize(
    "tuning, answering, recalled",
    [(KEEP_ATTACK_ARMY, [ROCKET_ID], False), (tuning_off(), sorted([*GUARD_IDS, MAIN_NEAR, ROCKET_ID]), True)],
    ids=["switch on", "switch off"],
)
def test_keep_attack_army_under_hold_aircraft_alone_order_no_ground_unit_that_cannot_hit_air_and_recall_nothing(
    tuning, answering, recalled
):
    agent, orders = air_raided(tuning)

    groups = agent.ctx.groups
    assert (agent.ctx.threat_level, sent_to_the_threat(agent, orders)) == ("major", answering)
    assert (groups.is_recalled(MAIN), groups.is_recalled(RAID)) == (recalled, recalled)
    assert bool(naming(orders, [MAIN_FAR])) is recalled
    assert groups.is_overridden(ROCKET_ID) and groups.is_overridden(GUARD_IDS[0]) is recalled


def test_keep_attack_army_a_minor_threat_of_one_aircraft_is_attacked_by_the_guard_s_anti_air_units_alone():
    cheap_minor = dataclasses.replace(
        KEEP_ATTACK_ARMY, base_defense=dataclasses.replace(KEEP_ATTACK_ARMY.base_defense, minor_max_cost=1500)
    )
    agent = new_agent(BaseDefenseManager(), tuning=cheap_minor)
    ask(agent, base(tick=995, units=AIR_HOME))
    agent.ctx.groups.assign(ROCKET_ID, GUARD)

    orders = ask(agent, base(units=AIR_HOME, visible_enemies=AIR_RAID[:1]))

    assert agent.ctx.threat_level == "minor"
    assert with_command(orders, "attack") == [{"cmd": "attack", "units": [ROCKET_ID], "target": 500}]


def test_keep_attack_army_an_air_raid_stays_one_while_its_aircraft_are_out_of_sight_and_ends_with_paratroopers():
    agent, first = air_raided(KEEP_ATTACK_ARMY)
    hit = [under_attack(30, YARD_ID, "fact", 22, 48)]
    unseen = ask(agent, base(tick=1005, units=AIR_HOME, events=hit))
    dropped = ask(agent, base(tick=1010, units=AIR_HOME, visible_enemies=[enemy(510, "e1r1", 26, 46)]))

    assert sent_to_the_threat(agent, first) == [ROCKET_ID] and sent_to_the_threat(agent, unseen) == []
    assert sent_to_the_threat(agent, dropped) == sorted([*GUARD_IDS, MAIN_NEAR])


def test_keep_attack_army_and_harvester_group_response_aircraft_on_a_harvester_out_do_not_send_main():
    # far_rescue_cells 30: the harvester 25 cells out is no far rescue, which would keep main's rocket soldier home too.
    both = dataclasses.replace(
        KEEP_ATTACK_ARMY,
        base_defense=dataclasses.replace(
            KEEP_ATTACK_ARMY.base_defense, harvester_group_response=True, far_rescue_cells=30
        ),
    )
    yaks = [enemy(500, "yak", 39, 28), enemy(501, "yak", 40, 29)]
    agent = new_agent(BaseDefenseManager(), tuning=both, plan_selector=FixedPlan("HOLD"))
    ask(agent, base(tick=995, units=[harvester(*HARVESTER_OUT), *AIR_HOME]))

    orders = ask(agent, harvester_hit(1000, AIR_HOME, enemies=yaks))

    assert agent.ctx.threat_cell == HARVESTER_OUT and not agent.ctx.groups.has_defense_override(MAIN)
    assert sent_to_the_threat(agent, orders) == [ROCKET_ID]


# -- reactive defenses: the extra defense only for a ground threat at the base that our army there does not outweigh ---


def own_tanks(count: int, x: int = 28) -> list[dict]:
    """Our light tanks, worth 700 each, in a column at `x`."""
    return [situations.unit(600 + number, "1tnk", x, 46 + number) for number in range(count)]


def test_reactive_defenses_a_harvester_hit_35_cells_from_the_base_starts_no_extra_defense():
    agent, manager = defense_agent()
    harvester = situations.unit(300, "harv", 57, 48, hp=60)
    attackers = [enemy(500 + number, "1tnk", 59, 47 + number) for number in range(3)]

    orders = ask(
        agent, base(units=[harvester], visible_enemies=attackers, events=[under_attack(1, 300, "harv", 57, 48)])
    )

    assert manager.threat.is_major and manager.threat.cell == (57, 48)
    assert builds(orders) == []


@pytest.mark.parametrize("own, built", [(2, ["gun"]), (4, [])], ids=["1.0x", "2.0x"])
def test_reactive_defenses_ground_enemies_8_cells_from_a_main_base_building_start_one_while_we_are_weaker(own, built):
    agent, _ = defense_agent()
    # Two light tanks, 1,400 together, 8 cells east of the construction yard; our tanks 1,400 or 2,800 by them.
    tanks = [enemy(500, "1tnk", 30, 48), enemy(501, "1tnk", 30, 49)]

    orders = ask(agent, base(units=own_tanks(own), visible_enemies=tanks))

    assert [order["item"] for order in builds(orders)] == built
    assert agent.ctx.defense_goals == ({"gun": (30, 48)} if built else {})


def test_reactive_defenses_the_extra_defense_is_placed_on_the_side_of_the_base_facing_the_threat():
    agent = new_agent(BaseDefenseManager(), UpkeepManager())
    tanks = [enemy(500, "1tnk", 30, 48), enemy(501, "1tnk", 30, 49)]
    ask(agent, base(units=own_tanks(2), visible_enemies=tanks))

    ready = [situations.job("Defense", "gun", ready=True)]
    orders = ask(agent, base(tick=1005, units=own_tanks(2), visible_enemies=tanks, production=ready))

    placed = [order for order in with_command(orders, "place") if order["item"] == "gun"]
    assert len(placed) == 1 and placed[0]["at"][0] > YARD[1]


def test_reactive_defenses_a_threat_of_aircraft_alone_near_the_base_starts_no_extra_defense():
    agent, manager = defense_agent()
    helicopters = [enemy(500 + number, "heli", 26, 47 + number) for number in range(3)]

    orders = ask(agent, base(visible_enemies=helicopters, events=[under_attack(1, YARD_ID, "fact", 22, 48)]))

    assert manager.threat.is_major and builds(orders) == []


# -- reactive defenses: the harassment tower --------------------------------------------------------------------------

HARASSING = tuning_off(base_defense=dataclasses.replace(tuning_off().base_defense, harass_tower=True))
# An enemy refinery 5 cells east of the easternmost cell the Allied base can place a pillbox at, (31, 48).
ENEMY_REFINERY = enemy(900, "proc", 36, 48)
# Two light tanks and a rifle, 1,500 together, by the refinery and out of the base's alarm radius.
ARMY_OF_1500 = [enemy(910, "1tnk", 38, 47), enemy(911, "1tnk", 38, 48), enemy(912, "e1", 38, 49)]


def harassing(tuning=HARASSING, **observation):
    """The orders of an agent with base defense alone in its first cycle by the enemy refinery, and the agent."""
    agent = new_agent(BaseDefenseManager(), tuning=tuning)
    orders = ask(agent, base(**{"visible_enemy_buildings": [ENEMY_REFINERY], **observation}))
    return orders, agent


def test_reactive_defenses_an_enemy_refinery_5_cells_from_a_buildable_cell_gets_one_harassment_pillbox(match_record):
    orders, agent = harassing()

    goal = agent.ctx.defense_goals["pbox"]
    assert builds(orders) == [{"cmd": "build", "item": "pbox"}]
    assert cells.distance(goal, (36, 48)) <= 6
    (event,) = match_record("base_defense")
    assert (event["reason"], event["item"], event["cell"], event["target"]["type"]) == (
        "harass_tower", "pbox", list(goal), "proc"
    )


def test_reactive_defenses_with_an_enemy_army_worth_1500_within_10_cells_no_harassment_tower_goes_up():
    orders, agent = harassing(visible_enemies=ARMY_OF_1500)

    assert not agent.threat_active and builds(orders) == []


def test_reactive_defenses_no_harassment_tower_goes_up_within_an_enemy_ground_defense_s_range_and_2_cells():
    orders, _ = harassing(visible_enemy_buildings=[ENEMY_REFINERY, enemy(901, "pbox", 35, 48)])

    assert builds(orders) == []


def test_reactive_defenses_with_harass_tower_off_no_harassment_tower_goes_up():
    orders, _ = harassing(tuning=tuning_off())

    assert builds(orders) == []


# Own yards north of the centre of the base at (22, 48), and an enemy refinery 5 cells north of each: every cell the
# base could place a pillbox at in the refinery's reach is about 28 cells out by the first yard, about 15 by the second.
YARD_28_OUT, REFINERY_BY_28 = ("fact", 22, 20), enemy(900, "proc", 22, 15)
YARD_15_OUT, REFINERY_BY_15 = ("fact", 22, 33), enemy(900, "proc", 22, 28)


@pytest.mark.parametrize(
    "yard, refinery, built",
    [(YARD_28_OUT, REFINERY_BY_28, False), (YARD_15_OUT, REFINERY_BY_15, True)],
    ids=["28 cells out", "15 cells out"],
)
def test_harass_tower_an_enemy_refinery_in_reach_of_cells_28_out_gets_none_and_one_15_out_gets_one(
    yard, refinery, built, match_record
):
    agent = new_agent(BaseDefenseManager(), tuning=HARASSING)

    orders = ask(agent, base(yard, visible_enemy_buildings=[refinery]))

    assert builds(orders) == ([{"cmd": "build", "item": "pbox"}] if built else [])
    goals = [tuple(event["cell"]) for event in match_record("base_defense")]
    assert goals == ([agent.ctx.defense_goals["pbox"]] if built else [])
    assert all(cells.distance(goal, (22, 48)) <= 20 for goal in goals)


def test_harass_tower_with_no_centre_of_the_base_known_no_harassment_tower_goes_up():
    orders, _ = harassing(base_center=None)

    assert builds(orders) == []


# -- far rescue: main is kept out of a losing answer far from the base ------------------------------------------------

# An own construction yard 30 cells north of the centre of the base at (22, 48), and four enemy light tanks by it worth
# 2,800: a major threat against the far yard that the guard's five rifles (500) do not outweigh.
FAR_YARD = ("fact", 22, 18)
FAR_YARD_ID = 104
FAR_RAIDERS = [enemy(500 + number, "1tnk", 25 + number % 2, 17 + number // 2) for number in range(4)]
# Fifteen light tanks by the far yard, worth 10,500.
FAR_ARMY = [enemy(500 + number, "1tnk", 24 + number % 5, 16 + number // 5) for number in range(15)]
# An enemy construction yard 7 cells north of the far yard.
ENEMY_YARD_BY_IT = enemy(900, "fact", 22, 11)
# A refinery 10 cells north of the centre of the base, and the same four light tanks by it.
NEAR_REFINERY = ("proc", 22, 38)
NEAR_RAIDERS = [enemy(500 + number, "1tnk", 25 + number % 2, 37 + number // 2) for number in range(4)]
FAR_MAIN_IDS = [310, 311, 312, 313, 314, 315]


def far_main(count: int, hp: int = 100, x: int = 62, y: int = 60) -> list[dict]:
    """main's light tanks side by side from a cell, 40 cells from the base by default: two at 80 hp are worth 1,120,
    0.4 times the four raiders; six at full health 4,200, 1.5 times."""
    return [
        situations.unit(unit_id, "1tnk", x + number, y, hp=hp) for number, unit_id in enumerate(FAR_MAIN_IDS[:count])
    ]


MAIN_AT_0_4, MAIN_AT_1_5 = far_main(2, hp=80), far_main(6)


def far_rescue_agent(main: list[dict], tuning: Tuning = tuning_off(), building=FAR_YARD):
    """An agent with base defense held under HOLD, shown the guard and main at 995 with no enemy near."""
    agent = new_agent(BaseDefenseManager(), tuning=tuning, plan_selector=FixedPlan("HOLD"))
    ask(agent, base(building, tick=995, units=[*guard_rifles(), *main]))
    return agent


def building_hit(agent, main: list[dict], tick: int = 1000, enemies=FAR_RAIDERS, building=FAR_YARD, **observation):
    """The agent's orders in the cycle its fourth building is reported under attack, with `enemies` in sight."""
    hit = [under_attack(tick, FAR_YARD_ID, *building)]
    units = [*guard_rifles(), *main]
    return ask(agent, base(building, tick=tick, units=units, visible_enemies=list(enemies), events=hit, **observation))


def main_ids_of(main: list[dict]) -> list[int]:
    return [unit["id"] for unit in main]


@pytest.mark.parametrize("tuning", [tuning_off(), KEEP_ATTACK_ARMY], ids=["switches off", "keep_attack_army on"])
def test_far_rescue_a_threat_at_a_yard_30_cells_out_with_main_at_0_4_does_not_recall_main_and_is_written(
    tuning, match_record
):
    agent = far_rescue_agent(MAIN_AT_0_4, tuning)

    orders = building_hit(agent, MAIN_AT_0_4)

    assert (agent.ctx.threat_level, agent.ctx.threat_cell) == ("major", (22, 18))
    assert not agent.ctx.groups.is_recalled(MAIN) and naming(orders, main_ids_of(MAIN_AT_0_4)) == []
    assert sent_to_the_threat(agent, orders) == GUARD_IDS
    assert match_record("base_defense") == [
        {
            "reason": "far_rescue_declined",
            "tick": 1000,
            "threat_cell": [22, 18],
            "distance_cells": 30.0,
            "ratio": 0.4,
            "enemy_building": None,
            "recalled": [],
        }
    ]


def test_far_rescue_the_same_threat_with_main_at_1_5_recalls_main_as_today(match_record):
    agent = far_rescue_agent(MAIN_AT_1_5)

    orders = building_hit(agent, MAIN_AT_1_5)

    main_ids = main_ids_of(MAIN_AT_1_5)
    assert agent.ctx.groups.is_recalled(MAIN)
    assert naming(orders, main_ids) == [{"cmd": "move", "units": main_ids, "to": STAGING}]
    assert match_record("base_defense") == []


def test_far_rescue_an_enemy_yard_7_cells_from_a_yard_30_cells_out_keeps_main_out_at_1_5(match_record):
    agent = far_rescue_agent(MAIN_AT_1_5)

    orders = building_hit(agent, MAIN_AT_1_5, visible_enemy_buildings=[ENEMY_YARD_BY_IT])

    assert not agent.ctx.groups.is_recalled(MAIN) and naming(orders, main_ids_of(MAIN_AT_1_5)) == []
    (event,) = match_record("base_defense")
    assert (event["ratio"], event["enemy_building"]) == (1.5, {"id": 900, "type": "fact", "cell": [22, 11]})


def test_far_rescue_an_enemy_pillbox_by_the_far_yard_keeps_main_out_and_counts_against_main_where_it_reaches(
    match_record,
):
    agent = far_rescue_agent(MAIN_AT_1_5)

    orders = building_hit(agent, MAIN_AT_1_5, visible_enemy_buildings=[enemy(901, "pbox", 22, 22)])

    assert not agent.ctx.groups.is_recalled(MAIN) and naming(orders, main_ids_of(MAIN_AT_1_5)) == []
    (event,) = match_record("base_defense")
    # The pillbox 4 cells from the yard reaches it, and weighs twice its 600 with the raiders' 2,800: 4,200 / 4,000.
    assert (event["ratio"], event["enemy_building"]["type"]) == (1.05, "pbox")


def test_far_rescue_a_threat_10_cells_from_the_base_recalls_main_at_0_4_as_today(match_record):
    agent = far_rescue_agent(MAIN_AT_0_4, building=NEAR_REFINERY)

    orders = building_hit(agent, MAIN_AT_0_4, enemies=NEAR_RAIDERS, building=NEAR_REFINERY)

    main_ids = main_ids_of(MAIN_AT_0_4)
    assert agent.ctx.threat_cell == (22, 38) and agent.ctx.groups.is_recalled(MAIN)
    assert naming(orders, main_ids) == [{"cmd": "move", "units": main_ids, "to": STAGING}]
    assert match_record("base_defense") == []


def test_far_rescue_with_no_enemy_in_sight_by_the_far_yard_the_rescue_goes_as_today():
    agent = far_rescue_agent(MAIN_AT_0_4)

    orders = building_hit(agent, MAIN_AT_0_4, enemies=rifles_at(32))

    # The rifles count toward the threat by the base, not toward main's fight by the far yard, 30 cells from them.
    assert agent.ctx.threat_cell == (22, 18) and agent.ctx.groups.is_recalled(MAIN)
    assert naming(orders, main_ids_of(MAIN_AT_0_4))[0]["to"] == STAGING


def test_far_rescue_main_stays_out_while_the_enemies_step_out_of_sight_and_the_event_is_written_once_a_threat(
    match_record,
):
    agent = far_rescue_agent(MAIN_AT_0_4)
    building_hit(agent, MAIN_AT_0_4)

    unseen = building_hit(agent, MAIN_AT_0_4, tick=1005, enemies=())
    ask(agent, base(FAR_YARD, tick=1200, units=[*guard_rifles(), *MAIN_AT_0_4]))
    over = agent.threat_active
    again = building_hit(agent, MAIN_AT_0_4, tick=1300)

    assert not over and naming([*unseen, *again], main_ids_of(MAIN_AT_0_4)) == []
    assert [event["tick"] for event in match_record("base_defense")] == [1000, 1300]


@pytest.mark.parametrize("tuning", [tuning_off(), KEEP_ATTACK_ARMY], ids=["switches off", "keep_attack_army on"])
def test_far_rescue_main_sent_from_the_base_at_1_5_goes_back_to_the_staging_point_once_it_is_at_0_4(tuning):
    main = far_main(6, x=30, y=52)
    agent = far_rescue_agent(main, tuning)
    sent = building_hit(agent, main)

    pulled = building_hit(agent, main, tick=1005, enemies=FAR_ARMY)
    later = building_hit(agent, main, tick=1010, enemies=FAR_ARMY)

    main_ids = main_ids_of(main)
    assert with_command(sent, "attack_move") == [
        {"cmd": "attack_move", "units": [*GUARD_IDS, *main_ids], "to": [22, 18]}
    ]
    assert naming(pulled, main_ids) == [{"cmd": "move", "units": main_ids, "to": STAGING}]
    assert naming(later, main_ids) == [] and not agent.ctx.groups.is_recalled(MAIN)


def far_harvester_hit(tick: int, units: list[dict], enemies=FAR_RAIDERS) -> dict:
    """The base with the far yard, its units, and its harvester by the far yard reported under attack at a tick."""
    hit = [under_attack(tick, HARVESTER_ID, "harv", 24, 21)]
    return base(FAR_YARD, tick=tick, units=[harvester(24, 21), *units], visible_enemies=list(enemies), events=hit)


def test_far_rescue_harvester_group_response_main_at_0_4_is_not_sent_by_the_far_yard_and_the_guard_stays_home(
    match_record,
):
    agent, _ = responding_agent()
    units = [*guard_rifles(), *MAIN_AT_0_4]
    ask(agent, base(FAR_YARD, tick=995, units=[harvester(24, 21), *units]))

    orders = ask(agent, far_harvester_hit(1000, units))

    assert agent.ctx.threat_cell == (24, 21) and not agent.ctx.groups.has_defense_override(MAIN)
    assert naming(orders, [*GUARD_IDS, *main_ids_of(MAIN_AT_0_4)]) == []
    assert [event["reason"] for event in match_record("base_defense")] == ["far_rescue_declined"]


def test_far_rescue_harvester_group_response_main_answering_at_1_5_is_let_go_once_it_is_at_0_4(match_record):
    agent, _ = responding_agent()
    units = [*guard_rifles(), *MAIN_AT_1_5]
    ask(agent, base(FAR_YARD, tick=995, units=[harvester(24, 21), *units]))
    sent = ask(agent, far_harvester_hit(1000, units))

    let_go = ask(agent, far_harvester_hit(1005, units, enemies=FAR_ARMY))

    main_ids = main_ids_of(MAIN_AT_1_5)
    assert naming(sent, main_ids) == [{"cmd": "attack_move", "units": main_ids, "to": [24, 21]}]
    assert naming(let_go, [*GUARD_IDS, *main_ids]) == [{"cmd": "move", "units": main_ids, "to": STAGING}]
    assert not agent.ctx.groups.has_defense_override(MAIN)
    steps = [event.get("outcome", event.get("reason")) for event in match_record("base_defense")]
    assert steps == ["sent", "far_rescue_declined", "declined"]


# -- far rescue: the units already sent to a threat main is then kept out of are called back ---------------------------

# Fifteen of main's light tanks, worth 10,500: 3.75 times the four raiders by the far yard, so main is sent; and sixteen
# enemy light tanks by it, worth 11,200, against which main is at 0.94 and kept out.
MAIN_15_IDS = list(range(310, 325))
ARMY_OF_16 = [enemy(500 + number, "1tnk", 24 + number % 5, 16 + number // 5) for number in range(16)]


def main_15(x: int, y: int) -> list[dict]:
    """main's fifteen light tanks in three rows of five from a cell."""
    return [
        situations.unit(unit_id, "1tnk", x + number % 5, y + number // 5) for number, unit_id in enumerate(MAIN_15_IDS)
    ]


def harvester_answer(units: list[dict]):
    """An agent whose harvester response sent main to the far yard's harvester at 1000, and its orders then."""
    agent, _ = responding_agent()
    ask(agent, base(FAR_YARD, tick=995, units=[harvester(24, 21), *units]))
    return agent, ask(agent, far_harvester_hit(1000, units))


def base_answer(units: list[dict]):
    """An agent whose base defense sent main from the base to the far yard at 1000, and its orders then."""
    agent = far_rescue_agent(units)
    return agent, building_hit(agent, units)


def declined(agent, units: list[dict], answer, tick: int = 1005) -> list[dict]:
    """The agent's orders in the cycle sixteen enemy tanks by the far yard put main at 0.94 there."""
    if answer is harvester_answer:
        return ask(agent, far_harvester_hit(tick, units, enemies=ARMY_OF_16))
    return building_hit(agent, units, tick=tick, enemies=ARMY_OF_16)


def threat_cell_of(answer) -> list[int]:
    return [24, 21] if answer is harvester_answer else [22, 18]


@pytest.mark.parametrize(
    "answer, at", [(harvester_answer, (30, 40)), (base_answer, (30, 50))], ids=["harvester response", "base defense"]
)
def test_far_rescue_15_units_sent_to_a_threat_30_cells_out_all_get_a_move_to_staging_in_the_cycle_it_is_declined(
    answer, at, match_record
):
    units = [*guard_rifles(), *main_15(*at)]
    agent, sent = answer(units)

    pulled = declined(agent, units, answer)

    moves_in = with_command(sent, "attack_move")
    assert set(MAIN_15_IDS) <= {unit_id for order in moves_in for unit_id in order["units"]}
    assert [order["to"] for order in moves_in] == [threat_cell_of(answer)]
    assert naming(pulled, MAIN_15_IDS) == [{"cmd": "move", "units": MAIN_15_IDS, "to": STAGING}]
    assert priorities(agent, "move")[-1] == Priority.EMERGENCY
    (event,) = [event for event in match_record("base_defense") if event.get("reason") == "far_rescue_declined"]
    assert (event["ratio"], event["recalled"]) == (0.94, MAIN_15_IDS)


@pytest.mark.parametrize(
    "answer, at", [(harvester_answer, (30, 40)), (base_answer, (30, 50))], ids=["harvester response", "base defense"]
)
def test_far_rescue_a_unit_on_a_crush_run_keeps_its_run_and_is_called_back_once_the_run_lets_it_go(
    answer, at, match_record
):
    units = [*guard_rifles(), *main_15(*at)]
    agent, _ = answer(units)
    agent.ctx.crush_run = {310: (40, 30)}

    pulled = declined(agent, units, answer)
    agent.ctx.crush_run = {}
    let_go = declined(agent, units, answer, tick=1010)

    assert naming(pulled, MAIN_15_IDS) == [{"cmd": "move", "units": MAIN_15_IDS[1:], "to": STAGING}]
    assert naming(let_go, MAIN_15_IDS) == [{"cmd": "move", "units": [310], "to": STAGING}]
    (event,) = [event for event in match_record("base_defense") if event.get("reason") == "far_rescue_declined"]
    assert event["recalled"] == MAIN_15_IDS[1:]


def test_far_rescue_a_unit_a_strike_or_a_repair_retreat_holds_keeps_its_own_order_when_main_is_called_back():
    units = [*guard_rifles(), *main_15(30, 40)]
    agent, _ = harvester_answer(units)
    agent.ctx.combat_held = frozenset({311, 312})

    pulled = declined(agent, units, harvester_answer)

    assert naming(pulled, MAIN_15_IDS) == [{"cmd": "move", "units": [310, *MAIN_15_IDS[3:]], "to": STAGING}]


def test_far_rescue_the_combat_manager_publishes_a_heavy_tank_at_30_hp_in_repair_mode_as_held_and_none_at_full():
    agent = new_agent(CombatManager(), tuning=tuning_off(combat=combat_off(vehicle_repair=True)))
    heavy_tanks = [situations.unit(310 + number, "3tnk", 40 + number, 47) for number in range(5)]

    ask(agent, base_state("russia", YARD, tick=1000, units=heavy_tanks))
    at_full = agent.ctx.combat_held
    ask(agent, base_state("russia", YARD, tick=1005, units=[{**heavy_tanks[0], "hp": 30}, *heavy_tanks[1:]]))

    assert at_full == frozenset() and agent.ctx.combat_held == frozenset({310})


def test_far_rescue_the_keys_ship_20_and_1_0_built_in_and_in_the_tracked_file_and_are_read_strictly(tmp_path):
    shipped = [Tuning().base_defense, Tuning.from_file(REPO_ROOT).base_defense]
    tracked = (REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8")
    copy = tmp_path / TUNING_FILE
    copy.parent.mkdir(parents=True)
    copy.write_text(tracked.replace("far_rescue_min_ratio = 1.0\n", ""), encoding="utf-8")

    assert [(tuning.far_rescue_cells, tuning.far_rescue_min_ratio) for tuning in shipped] == [(20, 1.0), (20, 1.0)]
    with pytest.raises(ConfigError, match=r"far_rescue_min_ratio in \[base_defense\]"):
        Tuning.from_file(tmp_path)


# Eight heavy tanks attack an own yard put 30 cells north of the base at 6:00, with an enemy yard 7 cells from it; they
# are gone at 7:00.
FAR_MATCH_ATTACK_SECONDS, FAR_MATCH_GONE_SECONDS = 360, 420


@pytest.fixture(scope="module", params=["england", "russia"])
def far_rescue_match(request) -> tuple[WholeMatch, list[bool]]:
    """A whole fake match for a family's start with the shipped tuning and the far yard's attack, and whether main
    was called home after each cycle of the attack."""
    raiders: list[int] = []
    recalled: list[bool] = []

    def watch(agent) -> None:
        if agent.ctx.threat_cell == FAR_YARD[1:]:
            recalled.append(agent.ctx.groups.is_recalled(MAIN))

    def far_yard_attacked(game: FakeRedAlert) -> None:
        yard = game.add_own_building(*FAR_YARD)
        raiders.extend(game.place_enemy("3tnk", 24 + number % 4, 16 + number // 4, visible=True) for number in range(8))
        game.place_enemy("fact", ENEMY_YARD_BY_IT["x"], ENEMY_YARD_BY_IT["y"], visible=True)
        game.emit_under_attack(yard)

    def raiders_gone(game: FakeRedAlert) -> None:
        for raider in raiders:
            game.remove_enemy(raider)

    scenario = [
        at_seconds(FAR_MATCH_ATTACK_SECONDS, far_yard_attacked),
        at_seconds(FAR_MATCH_GONE_SECONDS, raiders_gone),
    ]
    return play_whole_match(Tuning(), request.param, scenario=scenario, watch=watch), recalled


def test_far_rescue_a_whole_fake_match_with_the_shipped_tuning_runs_without_an_error_within_the_compute_bound(
    far_rescue_match,
):
    match, _ = far_rescue_match

    assert match.agent.ctx.tuning == Tuning()
    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.run.invalid_actions == 0 and match.game.invalid_actions == 0
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_far_rescue_in_the_whole_match_main_is_never_called_to_the_far_yard_and_one_event_says_so(far_rescue_match):
    match, recalled = far_rescue_match

    declined = [data for data in match.data_of("base_defense") if data.get("reason") == "far_rescue_declined"]
    assert recalled and not any(recalled)
    assert [(data["threat_cell"], data["enemy_building"]["type"]) for data in declined] == [([22, 18], "fact")]
