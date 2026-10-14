"""The watch on the enemy's air: what counts as air seen, that it stays seen, the aircraft counted over a match and
lately, and the rocket squads and AA defenses the aircraft seen lately call for."""

from __future__ import annotations

from dataclasses import replace

import pytest

import situations
from situations import REPO_ROOT, OpeningThen, ask, base_state, job, new_agent, own_agent, tracked_items, with_command
from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.redalert.anti_air import (
    AIR_PRODUCERS,
    AirWatch,
    anti_air_defenses_wanted,
    is_air_threat,
    is_combat_aircraft,
    rocket_squads_wanted,
)
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.plans import HOLD
from ucla_agent.games.redalert.remote_air import AIR_SQUAD
from ucla_agent.games.redalert.rush_watch import RUSH
from ucla_agent.games.redalert.tuning import AntiAirTuning, Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager
from ucla_agent.games.redalert.world import World


def enemy(enemy_id: int, enemy_type: str, **more) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": 60, "y": 40, "hp": 100, **more}


def show(world: World, tick: int, units=(), buildings=()) -> World:
    """The world after one more state that shows these enemy units and buildings."""
    observation = {"tick": tick, "visible_enemies": list(units), "visible_enemy_buildings": list(buildings)}
    world.update({"phase": "playing", "observation": observation})
    return world


def watched(*states: tuple[list[dict], list[dict]]) -> AirWatch:
    """A watch that took in a world after each of the states, given as (enemy units, enemy buildings)."""
    world, watch = World(tracked_items()), AirWatch()
    for tick, (units, buildings) in enumerate(states, start=1):
        watch.observe(show(world, tick * 25, units, buildings))
    return watch


@pytest.mark.parametrize("aircraft", ["heli", "mh60", "yak", "mig"])
def test_air_an_aircraft_with_a_weapon_is_a_combat_aircraft(aircraft):
    assert is_combat_aircraft(aircraft, World(tracked_items()))


@pytest.mark.parametrize("other", ["tran", "badr", "u2", "e1", "e3", "2tnk", "ftrk", "hpad", "afld", "unknown"])
def test_air_an_unarmed_aircraft_a_ground_unit_a_building_or_an_unknown_type_is_no_combat_aircraft(other):
    assert not is_combat_aircraft(other, World(tracked_items()))


def test_air_the_air_producers_are_the_helipad_and_the_airfield():
    assert AIR_PRODUCERS == {"hpad", "afld"}


def test_air_a_new_watch_and_one_that_saw_only_ground_units_and_buildings_have_seen_no_air():
    ground = watched(([enemy(900, "e1"), enemy(901, "2tnk")], [enemy(800, "weap"), enemy(801, "dome")]))

    assert (AirWatch().is_air_seen, AirWatch().aircraft_seen) == (False, 0)
    assert (ground.is_air_seen, ground.aircraft_seen) == (False, 0)


@pytest.mark.parametrize("aircraft", ["yak", "heli", "mig", "mh60"])
def test_air_one_combat_aircraft_in_sight_is_air_seen_and_counts_one(aircraft):
    watch = watched(([enemy(900, aircraft)], []))

    assert (watch.is_air_seen, watch.aircraft_seen) == (True, 1)


@pytest.mark.parametrize("producer", ["hpad", "afld", "afld.ukraine", "AFLD.Ukraine", "hpadr1"])
def test_air_a_helipad_or_an_airfield_in_sight_is_air_seen_with_no_aircraft_counted(producer):
    watch = watched(([], [enemy(800, producer)]))

    assert (watch.is_air_seen, watch.aircraft_seen) == (True, 0)


def test_air_unarmed_aircraft_are_no_air_seen():
    watch = watched(([enemy(900, "tran"), enemy(901, "badr"), enemy(902, "u2")], []))

    assert (watch.is_air_seen, watch.aircraft_seen) == (False, 0)


def test_air_stays_seen_after_the_aircraft_leaves_sight_and_after_its_wreck_is_seen():
    world, watch = World(tracked_items()), AirWatch()
    watch.observe(show(world, 25, [enemy(900, "yak")]))
    watch.observe(show(world, 50))
    out_of_sight = (watch.is_air_seen, 900 in world.enemy_memory)
    watch.observe(show(world, 75, [enemy(900, "yak", wreck=True)]))

    assert out_of_sight == (True, True)
    assert (watch.is_air_seen, watch.aircraft_seen, 900 in world.enemy_memory) == (True, 1, False)


def test_air_the_same_aircraft_seen_in_many_states_counts_once():
    watch = watched(*[([enemy(900, "yak")], [])] * 5)

    assert watch.aircraft_seen == 1


def test_air_four_aircraft_each_shot_down_before_the_next_came_count_four():
    states = []
    for number in range(4):
        states += [([enemy(900 + number, "mig")], []), ([enemy(900 + number, "mig", wreck=True)], [])]

    watch = watched(*states)

    assert watch.aircraft_seen == 4


def test_air_the_match_context_takes_the_enemy_memory_in_every_cycle():
    agent = own_agent()
    base = (("fact", 22, 48), ("powr", 19, 48))
    ask(agent, base_state("england", *base, tick=1000))
    before = agent.ctx.air_watch.is_air_seen

    ask(agent, base_state("england", *base, tick=1005, visible_enemies=[enemy(900, "heli", x=40)]))

    assert (before, agent.ctx.air_watch.is_air_seen, agent.ctx.air_watch.aircraft_seen) == (False, True, 1)


def test_air_a_watch_that_is_not_enabled_never_sees_air():
    world, watch = World(tracked_items()), AirWatch(enabled=False)
    for tick in (25, 50):
        watch.observe(show(world, tick, [enemy(900, "yak"), enemy(901, "heli")], [enemy(800, "afld")]))

    assert (watch.is_air_seen, watch.aircraft_seen) == (False, 0)


def test_air_with_the_anti_air_answer_switched_off_the_match_context_s_watch_never_sees_air():
    agent = own_agent(tuning=replace(Tuning(), anti_air=AntiAirTuning(enabled=False)))
    base = (("fact", 22, 48), ("powr", 19, 48))

    ask(agent, base_state("england", *base, tick=1000, visible_enemies=[enemy(900, "heli", x=40)]))

    assert (agent.ctx.air_watch.is_air_seen, agent.ctx.air_watch.aircraft_seen) == (False, 0)


# -- the extra answer: aircraft seen lately ------------------------------------------------------------------------

SHIPPED = AntiAirTuning()
# The shipped window of aircraft seen lately, in ticks.
WINDOW_TICKS = SHIPPED.aa_window_seconds * TICKS_PER_SECOND


def seen_together(count: int, tick: int = 25) -> AirWatch:
    """A watch that saw `count` migs in one state at `tick`."""
    world, watch = World(tracked_items()), AirWatch()
    watch.observe(show(world, tick, [enemy(900 + number, "mig") for number in range(count)]))
    return watch


def test_army_build_the_anti_air_keys_ship_3_aircraft_in_180_s_squads_per_2_beyond_2_up_to_3_and_2_defenses_from_6():
    shipped = Tuning.from_file(REPO_ROOT).anti_air

    assert shipped == SHIPPED
    assert (shipped.aa_min_aircraft, shipped.aa_window_seconds) == (3, 180)
    assert (shipped.aa_free_aircraft, shipped.aa_aircraft_per_squad, shipped.aa_squads_max) == (2, 2, 3)
    assert (shipped.aa_squad_points, shipped.air_flak_points) == (8, 0)
    assert (shipped.aa_defenses, shipped.aa_defenses_many, shipped.aa_many_aircraft) == (1, 2, 6)


@pytest.mark.parametrize(
    "aircraft, squads, defenses",
    [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 1), (4, 1, 1), (5, 1, 1), (6, 2, 2), (7, 2, 2), (8, 3, 2), (12, 3, 2)],
)
def test_army_build_no_extra_anti_air_below_3_aircraft_then_a_squad_per_2_beyond_2_up_to_3_and_2_defenses_from_6(
    aircraft, squads, defenses
):
    watch = seen_together(aircraft)

    assert is_air_threat(watch, 25, SHIPPED) == (aircraft >= 3)
    assert (rocket_squads_wanted(watch, 25, SHIPPED), anti_air_defenses_wanted(watch, 25, SHIPPED)) == (
        squads,
        defenses,
    )


def test_army_build_aircraft_seen_lately_count_each_once_at_its_last_sighting_the_shot_down_included():
    states = []
    for number in range(4):
        states += [([enemy(900 + number, "mig")], []), ([enemy(900 + number, "mig", wreck=True)], [])]

    watch = watched(*states)

    assert (watch.aircraft_lately(8 * 25, 180), watch.aircraft_seen) == (4, 4)


@pytest.mark.parametrize(
    "seconds_after, squads, defenses",
    [(0, 1, 1), (180, 1, 1), (181, 0, 0)],
    ids=["just seen", "180 s later", "181 s later: expired"],
)
def test_army_build_with_no_aircraft_seen_for_180_s_the_extra_squads_and_defenses_stop(seconds_after, squads, defenses):
    watch = seen_together(4)
    tick = 25 + seconds_after * TICKS_PER_SECOND

    assert (rocket_squads_wanted(watch, tick, SHIPPED), anti_air_defenses_wanted(watch, tick, SHIPPED)) == (
        squads,
        defenses,
    )


def test_army_build_an_aircraft_seen_again_counts_from_its_latest_sighting():
    world, watch = World(tracked_items()), AirWatch()
    watch.observe(show(world, 25, [enemy(900 + number, "mig") for number in range(3)]))
    watch.observe(show(world, 25 + WINDOW_TICKS, [enemy(900 + number, "mig") for number in range(3)]))

    assert watch.aircraft_lately(25 + WINDOW_TICKS + 100 * TICKS_PER_SECOND, 180) == 3


@pytest.mark.parametrize("producer", ["hpad", "afld"])
def test_army_build_an_air_producer_alone_calls_for_no_extra_anti_air(producer):
    watch = watched(([], [enemy(800, producer)]))

    assert watch.is_air_seen and not is_air_threat(watch, 25, SHIPPED)
    assert (rocket_squads_wanted(watch, 25, SHIPPED), anti_air_defenses_wanted(watch, 25, SHIPPED)) == (0, 0)


# -- an air attack on a remote base ------------------------------------------------------------------------------------

REMOTE_DEFENSES = [("agun", "tent"), ("sam", "barr")]
# The main base round the first yard at 22,48, and a second yard's base 30 cells south of it.
MAIN_YARD, REMOTE_YARD, REMOTE_REFINERY = ("fact", 22, 48), ("fact", 22, 78), ("proc", 26, 78)
REMOTE_BUILDINGS = [(22, 78), (26, 78)]
HELI_AT_THE_REMOTE_REFINERY = {"id": 950, "type": "heli", "x": 26, "y": 74, "hp": 100}
WRECK = {**HELI_AT_THE_REMOTE_REFINERY, "type": "heli.husk"}
SECONDS = 400
TICK = SECONDS * TICKS_PER_SECOND


def remote_base(
    barracks: str = "tent", *more: tuple, dome: bool = True, hit: tuple = (), tick: int = TICK, cash: int = 5000, **seen
):
    """A base round the first yard with a radar unless told otherwise, a second yard with a refinery 30 cells south,
    the buildings given besides, power to spare and so much money; the buildings in `hit` at 80 hp."""
    radar = (("dome", 30, 44),) if dome else ()
    opening = (("powr", 19, 48), (barracks, 22, 44), ("proc", 24, 39), ("proc", 18, 39), ("powr", 26, 50))
    main = (MAIN_YARD, *opening, ("weap", 22, 53), ("fix", 26, 54), *radar)
    state = base_state(
        "england" if barracks == "tent" else "russia",
        *main,
        REMOTE_YARD,
        REMOTE_REFINERY,
        *more,
        tick=tick,
        economy=situations.economy(cash, provided=600, drained=100, harvesters=2),
        **seen,
    )
    for building in state["observation"]["buildings"]:
        if (building["type"], building["x"], building["y"]) in hit:
            building["hp"] = 80
    return state


def remote_agent(**anti_air) -> RedAlertAgent:
    """An agent that plays with the upkeep manager alone, the remote base answer as the tuning file ships it unless
    told otherwise."""
    tuning = Tuning(anti_air=replace(AntiAirTuning(), **anti_air))
    return new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)


def attacked(agent: RedAlertAgent, barracks: str = "tent", *more: tuple, **state) -> list[dict]:
    """The orders of the cycle in which the heli hits the remote refinery, after one in which the base stood whole
    with no money, which ordered nothing that would still hold a queue."""
    ask(agent, remote_base(barracks, *more, tick=TICK - 25, cash=0, **state))
    hit = remote_base(barracks, *more, hit=(REMOTE_REFINERY,), visible_enemies=[HELI_AT_THE_REMOTE_REFINERY], **state)
    return ask(agent, hit)


def queued(queue: str, orders: list[dict]) -> list[str]:
    """The items a batch orders built in a queue."""
    items = tracked_items()
    return [order["item"] for order in with_command(orders, "build") if items[order["item"]].queue == queue]


def anti_air_events(monkeypatch) -> list[dict]:
    """The `anti_air` events written from now on."""
    logged: list[dict] = []
    monkeypatch.setattr(records, "log", lambda kind, data: logged.append(data) if kind == "anti_air" else None)
    return logged


@pytest.mark.parametrize("defense, barracks", REMOTE_DEFENSES, ids=["england", "russia"])
def test_remote_a_heli_hitting_a_second_yard_s_refinery_gets_one_aa_defense_placed_within_4_cells_of_that_base(
    monkeypatch, defense, barracks
):
    logged, agent = anti_air_events(monkeypatch), remote_agent()
    heli = {"visible_enemies": [HELI_AT_THE_REMOTE_REFINERY]}

    first = attacked(agent, barracks)
    on_its_way = ask(agent, remote_base(barracks, tick=TICK + 25, production=[job("Defense", defense)], **heli))
    ready = ask(agent, remote_base(barracks, tick=TICK + 50, production=[job("Defense", defense, ready=True)], **heli))
    (place,) = with_command(ready, "place")
    cell = tuple(place["at"])
    standing = ask(agent, remote_base(barracks, (defense, *cell), tick=TICK + 75, **heli))
    hit_again = remote_base(barracks, (defense, *cell), tick=TICK + 100, hit=(REMOTE_REFINERY, REMOTE_YARD), **heli)

    assert (queued("Defense", first), queued("Defense", on_its_way)) == ([defense], [])
    assert place["item"] == defense and any(cells.is_within(cell, spot, 4) for spot in REMOTE_BUILDINGS)
    assert (queued("Defense", standing), queued("Defense", ask(agent, hit_again))) == ([], [])
    assert [(event["action"], event["reason"]) for event in logged] == [
        ("mark", "remote_base_attack"),
        ("order", "remote_base_attack"),
        ("placed", "remote_base_attack"),
    ]
    assert (logged[0]["cell"], logged[0]["attacker"], logged[1]["item"]) == ([26, 78], 950, defense)


def test_remote_the_heli_destroyed_before_its_defense_is_placed_cancels_it_and_clears_the_mark(monkeypatch):
    logged, agent = anti_air_events(monkeypatch), remote_agent()
    attacked(agent)

    shot_down = ask(
        agent, remote_base(tick=TICK + 25, production=[job("Defense", "agun", ready=True)], visible_enemies=[WRECK])
    )

    assert with_command(shot_down, "cancel") == [{"cmd": "cancel", "item": "agun"}]
    assert with_command(shot_down, "place") == []
    assert [event["action"] for event in logged] == ["mark", "order", "cancel"]


def test_remote_another_aircraft_seen_near_the_base_in_the_last_30_s_keeps_the_order_when_the_attacker_dies():
    agent = remote_agent()
    other = {**HELI_AT_THE_REMOTE_REFINERY, "id": 951, "x": 20, "y": 74}
    ask(agent, remote_base(tick=TICK - 25))
    ask(agent, remote_base(hit=(REMOTE_REFINERY,), visible_enemies=[HELI_AT_THE_REMOTE_REFINERY, other]))

    shot_down = ask(agent, remote_base(tick=TICK + 25, production=[job("Defense", "agun")], visible_enemies=[WRECK]))

    assert with_command(shot_down, "cancel") == []


@pytest.mark.parametrize("defense, barracks", REMOTE_DEFENSES, ids=["england", "russia"])
def test_remote_with_no_radar_the_radar_is_ordered_first(defense, barracks):
    orders = attacked(remote_agent(), barracks, dome=False)

    assert (queued("Building", orders), queued("Defense", orders)) == (["dome"], [])


def test_remote_an_attack_on_the_main_base_changes_nothing(monkeypatch):
    logged, agent = anti_air_events(monkeypatch), remote_agent()
    at_home = {**HELI_AT_THE_REMOTE_REFINERY, "x": 24, "y": 35}
    ask(agent, remote_base(tick=TICK - 25))

    orders = ask(agent, remote_base(hit=(("proc", 24, 39),), visible_enemies=[at_home]))

    assert (queued("Defense", orders), logged) == ([], [])


def test_remote_an_aa_defense_within_6_cells_of_the_building_hit_covers_the_base(monkeypatch):
    logged, agent = anti_air_events(monkeypatch), remote_agent()

    covered = attacked(agent, "tent", ("agun", 30, 80))

    assert (queued("Defense", covered), logged) == ([], [])


def test_remote_no_hp_lost_or_no_aircraft_near_marks_nothing(monkeypatch):
    logged = anti_air_events(monkeypatch)
    far_heli = {**HELI_AT_THE_REMOTE_REFINERY, "y": 60}
    no_hit, far = remote_agent(), remote_agent()
    ask(no_hit, remote_base(tick=TICK - 25))
    ask(far, remote_base(tick=TICK - 25))

    unharmed = ask(no_hit, remote_base(visible_enemies=[HELI_AT_THE_REMOTE_REFINERY]))
    out_of_reach = ask(far, remote_base(hit=(REMOTE_REFINERY,), visible_enemies=[far_heli]))

    assert (queued("Defense", unharmed), queued("Defense", out_of_reach), logged) == ([], [], [])


def test_remote_a_cluster_with_no_yard_counts_from_remote_base_cells_off_the_main_yard():
    # 22 cells east of the main yard, toward the enemy.
    outpost = (("powr", 42, 48), ("proc", 46, 48))
    heli = {**HELI_AT_THE_REMOTE_REFINERY, "x": 46, "y": 44}

    def answer(agent: RedAlertAgent) -> list[str]:
        ask(agent, remote_base("tent", *outpost, tick=TICK - 25))
        hit = remote_base("tent", *outpost, hit=(("proc", 46, 48),), visible_enemies=[heli])
        return queued("Defense", ask(agent, hit))

    assert (answer(remote_agent()), answer(remote_agent(remote_base_cells=25))) == (["agun"], [])


def test_remote_with_remote_base_aa_off_nothing_is_ordered():
    assert queued("Defense", attacked(remote_agent(remote_base_aa=False))) == []


def test_remote_the_keys_are_built_in_and_tracked_with_the_requirement_s_values():
    tracked = Tuning.from_file(REPO_ROOT).anti_air

    assert (tracked.remote_base_aa, tracked.remote_base_cells) == (True, 15)
    assert (AntiAirTuning().remote_base_aa, AntiAirTuning().remote_base_cells) == (True, 15)


# -- the rocket squad sent meanwhile ---------------------------------------------------------------------------------

# Six idle rocket soldiers of main by the main base, the nearer the remote base the higher the id, and one busy.
ROCKETS = [situations.unit(400 + number, "e3", 30, 50 + number) for number in range(6)]
BUSY_ROCKET = situations.unit(410, "e3", 30, 70, idle=False)


def squad(agent: RedAlertAgent) -> list[int]:
    return agent.ctx.groups.roster(AIR_SQUAD)


def test_squad_with_no_radar_the_5_idle_rockets_of_main_nearest_the_base_are_sent_to_the_cell_hit_not_the_busy_one():
    agent = remote_agent()

    orders = attacked(agent, "tent", dome=False, units=[*ROCKETS, BUSY_ROCKET])

    (sent,) = [order for order in with_command(orders, "attack_move") if order["to"] == [26, 78]]
    assert sorted(sent["units"]) == [401, 402, 403, 404, 405] and sorted(squad(agent)) == sorted(sent["units"])
    assert ("e3", 5) not in [(order["item"], order["count"]) for order in with_command(orders, "train")]


def test_squad_with_no_idle_rockets_5_are_trained_once_and_taken_as_they_come():
    agent = remote_agent()

    orders = attacked(agent, "tent", dome=False)
    again = ask(agent, remote_base(dome=False, tick=TICK + 25, visible_enemies=[HELI_AT_THE_REMOTE_REFINERY]))
    fresh = [situations.unit(500 + number, "e3", 22, 44, idle=False) for number in range(5)]
    came = ask(
        agent, remote_base(dome=False, tick=TICK + 50, visible_enemies=[HELI_AT_THE_REMOTE_REFINERY], units=fresh)
    )

    assert [(order["item"], order["count"]) for order in with_command(orders, "train")] == [("e3", 5)]
    assert ("e3", 5) not in [(order["item"], order["count"]) for order in with_command(again, "train")]
    assert sorted(squad(agent)) == [500, 501, 502, 503, 504]
    assert [sorted(order["units"]) for order in with_command(came, "attack_move")] == [[500, 501, 502, 503, 504]]


def test_squad_goes_back_to_main_once_the_mark_is_cleared():
    agent = remote_agent()
    attacked(agent, "tent", dome=False, units=ROCKETS)

    ask(agent, remote_base(dome=False, tick=TICK + 25, visible_enemies=[WRECK], units=ROCKETS))

    assert squad(agent) == [] and {agent.ctx.groups.group_of(unit["id"]) for unit in ROCKETS} == {MAIN}


def test_squad_none_is_sent_with_a_radar_standing_or_while_main_holds_home_against_a_rush():
    with_radar, holding = remote_agent(), remote_agent()
    holding.ctx.rush_verdict = RUSH
    hold_tick = (holding.ctx.tuning.opening.rush_hold_until_seconds - 10) * TICKS_PER_SECOND
    heli = [HELI_AT_THE_REMOTE_REFINERY]

    attacked(with_radar, "tent", units=ROCKETS)
    ask(holding, remote_base(dome=False, tick=hold_tick - 25, cash=0, units=ROCKETS))
    held = ask(
        holding, remote_base(dome=False, tick=hold_tick, hit=(REMOTE_REFINERY,), visible_enemies=heli, units=ROCKETS)
    )

    assert (squad(with_radar), squad(holding)) == ([], [])
    assert [(order["item"], order["count"]) for order in with_command(held, "train")] == [("e3", 5)]
