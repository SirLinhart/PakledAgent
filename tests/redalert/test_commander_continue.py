"""The commander's continue option: a still-sound standing action the fresh list leaves off is offered again as Z."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from switches_off import SWITCHES_OFF
from test_commander import (
    FIRST_STEP,
    NAMES,
    SCOUT_ID,
    SCOUTING,
    TWO_STARTS,
    FIELDS,
    POWER,
    YARD,
    FixedPlan,
    advancing,
    commanding,
    enemy,
    step_orders,
    tanks,
    tanks_near,
)
from ucla_agent.games.redalert import command_army, command_prompt, command_scout, scout_steering
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.command_army import ADVANCE, ATTACK, FLANK, GATHER, HOLD, SIEGE, Continuing
from ucla_agent.games.redalert.command_prompt import CONTINUE, CONTINUE_HEAD, CONTINUE_ID, Action, Current
from ucla_agent.games.redalert.command_threat import LOSING_HERE, Threat
from ucla_agent.games.redalert.commander import SCOUT_ROLE
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.scout_mission_planner import MissionPlanner
from ucla_agent.games.redalert.scout_missions import EXPANSION_WATCH
from ucla_agent.games.redalert.scout_report import EnemyIntel
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import TUNING_FILE, CommanderTuning, Tuning

ARMY = dataclasses.replace(Tuning(), combat=dataclasses.replace(Tuning().combat, vehicle_repair=False))
MAIN_TANKS = tanks(6, 26, 47)
# A war factory, production, to advance on at (50, 50), and a refinery remembered at (40, 40).
MOVED_GOAL = {"visible_enemy_buildings": [enemy(641, "weap", 50, 50), enemy(642, "proc", 40, 40)]}


@dataclasses.dataclass
class Main:
    """main's brief and fresh actions in one cycle of a Soviet base."""

    agent: situations.RedAlertAgent
    found: command_army.ArmyBrief

    @property
    def world(self):
        return self.agent.world

    @property
    def ctx(self):
        return self.agent.ctx

    def actions(self, standing: Action | None, *, is_gate_open: bool = True, **continuing) -> tuple[Action, ...]:
        """The question's actions with `standing` as main's standing action (None for none)."""
        given = None if standing is None else Continuing(standing, **continuing)
        return command_army.army_actions(self.found, is_gate_open, NAMES, self.world, self.ctx, continuing=given)

    def question(self, standing: Action | None, **more) -> command_prompt.Question:
        actions = self.actions(standing, **more)
        current = None if standing is None else Current(standing, 0, self.found.view.center)
        return command_army.asked(self.found, actions, (), NAMES, self.world, self.ctx, current)


def main_in(units=MAIN_TANKS, tuning: Tuning = ARMY, **observation) -> Main:
    """main of these units in a Soviet base, one cycle played, and its brief."""
    agent = situations.new_agent(CombatManager(), tuning=tuning, plan_selector=FixedPlan("PRESS"))
    state = base_state("russia", YARD, POWER, ("barr", 18, 44), tick=12500, units=units, **observation)
    ask(agent, state, situations.context(TWO_STARTS))
    world, ctx = agent.world, agent.ctx
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    found = command_army.brief(main, NAMES, command_army.EnemyPeak(), world, ctx)
    return Main(agent, found)


def advance_to(cell, label: str = "refinery") -> Action:
    """An advance as it was taken on a cell."""
    return Action(
        kind=ADVANCE,
        key=(ADVANCE, cell),
        label=f"advance {command_prompt.at(cell)}",
        head=f"advance to {label} {command_prompt.at(cell)}, 14 cells, path clear",
        cell=cell,
    )


def continues(actions) -> list[Action]:
    return [action for action in actions if action.id == CONTINUE_ID]


def option_line(question: command_prompt.Question, letter: str) -> str:
    return next(option.description for option in question.request.options if option.id == letter)


# -- the army's continue option ----------------------------------------------------------------------------


def test_goal_moved_the_advance_on_the_old_cell_is_offered_as_z_beside_the_fresh_advance():
    scene = main_in(**MOVED_GOAL)
    standing = advance_to((40, 40))

    question = scene.question(standing)

    letters = {action.id: action for action in question.actions}
    assert letters["I"].key == (ADVANCE, (50, 50)) and letters[CONTINUE_ID].key == standing.key
    assert [action for action in question.actions if action.key == standing.key] == [letters[CONTINUE_ID]]
    line = option_line(question, CONTINUE_ID)
    assert line.startswith(CONTINUE_HEAD + "advance to refinery (40,40)") and line.endswith("(current)")
    assert line.count(CONTINUE_HEAD) == 1 and CONTINUE_ID in [option.id for option in question.request.options]
    assert letters[CONTINUE_ID].offered_as == CONTINUE


def test_no_duplicate_with_the_standing_action_on_the_fresh_list_no_z_and_its_own_letter_is_current():
    scene = main_in(**MOVED_GOAL)
    fresh = next(action for action in scene.actions(None) if action.kind == ADVANCE)

    question = scene.question(fresh)

    assert continues(question.actions) == [] and command_prompt.CURRENT_MARK in option_line(question, fresh.id)


def test_a_continue_option_offered_again_is_never_headed_twice():
    scene = main_in(**MOVED_GOAL)
    once = continues(scene.actions(advance_to((40, 40))))[0]

    (twice,) = continues(scene.actions(once))

    assert twice.head.count(CONTINUE_HEAD) == 1


SIEGED = {
    "visible_enemy_buildings": [enemy(600, "tsla", 46, 47), enemy(641, "weap", 60, 50)],
}
SIEGE_MAIN = [*tanks(4, 26, 47), *tanks(2, 26, 50, first=420, unit_type="v2rl")]


def test_cap_cut_the_siege_the_cut_dropped_comes_back_as_z_the_fresh_candidate_not_a_second_siege_letter():
    tuning = dataclasses.replace(ARMY, commander=CommanderTuning(options_max=3))
    scene = main_in(SIEGE_MAIN, tuning, **SIEGED)
    wide = main_in(SIEGE_MAIN, **SIEGED)
    candidate = next(action for action in wide.actions(None) if action.kind == SIEGE)
    taken = dataclasses.replace(candidate, head="siege as it was taken")

    actions = scene.actions(taken)

    (option,) = continues(actions)
    assert option.key == candidate.key and option.head == CONTINUE_HEAD + candidate.head
    assert [action.id for action in actions if action.kind == SIEGE] == [CONTINUE_ID] and len(actions) == 3


def labeled(kind: str, offer_kind: str = "") -> Action:
    return Action(kind=kind, key=(kind, offer_kind), label=kind, head=kind, offer_kind=offer_kind)


def test_displacement_a_full_list_gives_way_with_its_economy_target_first_then_its_flank_never_defense_or_gather():
    economy = labeled(ATTACK, command_army.ECONOMY)
    flank, advance, gather = labeled(FLANK), labeled(ADVANCE), labeled(GATHER)
    defensive = [labeled(command_army.PULL_BACK), labeled(HOLD)]

    with_economy = command_army.giving_way([*defensive, gather, economy, advance, flank])
    without = command_army.giving_way([*defensive, gather, advance, flank])
    full = command_prompt.lettered([*defensive, gather, economy, advance, flank], command_army.ARMY_IDS)
    placed = command_prompt.with_continue(full, advance_to((40, 40)), command_army.giving_way(full), 6)

    assert with_economy[0] is economy and without[0] is flank
    assert command_army.giving_way([*defensive, gather]) == []
    assert economy.key not in [action.key for action in placed] and len(placed) == 6
    assert command_prompt.with_continue(full[:3], advance_to((40, 40)), [], 3) == full[:3]


def test_invalid_no_z_once_main_is_within_3_cells_of_the_standing_action_s_cell():
    scene = main_in(**MOVED_GOAL)
    center = scene.found.view.center

    assert continues(scene.actions(advance_to((center[0] + 2, center[1])))) == []


def test_invalid_no_z_once_the_siege_s_defense_is_no_longer_known():
    scene = main_in(SIEGE_MAIN, **MOVED_GOAL)
    gone = Action(kind=SIEGE, key=(SIEGE, 999), label="siege", head="siege", cell=(46, 47))

    assert continues(scene.actions(gone)) == []


def test_invalid_no_z_for_an_advance_on_a_cell_no_remembered_enemy_stands_on_and_no_enemy_start():
    scene = main_in(**MOVED_GOAL)

    assert continues(scene.actions(advance_to((44, 30)))) == []
    assert continues(scene.actions(advance_to(command_army.enemy_start(scene.world))))


def test_invalid_no_z_with_the_base_attacked_or_main_losing_here():
    attacked = main_in(**MOVED_GOAL)
    attacked.ctx.threat_active = True
    attacked.found = command_army.brief(
        list(attacked.found.view.body), NAMES, command_army.EnemyPeak(), attacked.world, attacked.ctx
    )
    losing = main_in(**MOVED_GOAL)
    losing.found = dataclasses.replace(losing.found, danger=Threat(LOSING_HERE))

    assert attacked.found.danger.kind == "base attacked"
    assert continues(attacked.actions(advance_to((40, 40)))) == []
    assert continues(losing.actions(advance_to((40, 40)))) == []


def test_invalid_no_z_with_the_gate_closed_the_retreat_override_on_or_a_regroup_due():
    scene = main_in(**MOVED_GOAL)
    standing = advance_to((40, 40))

    assert continues(scene.actions(standing, is_gate_open=False)) == []
    assert continues(scene.actions(standing, is_pulled_back=True)) == []
    assert continues(scene.actions(standing, is_regroup_due=True)) == []


def test_invalid_while_main_is_behind_no_z_on_a_cell_more_than_15_cells_from_the_base_s_center():
    scene = main_in(**MOVED_GOAL)
    scene.found = dataclasses.replace(scene.found, estimate=command_army.EnemyEstimate(100_000, 12000))

    assert continues(scene.actions(advance_to((40, 40)))) == []


def test_invalid_no_z_for_an_attack_a_gather_or_a_hold():
    scene = main_in(**MOVED_GOAL)
    attack = Action(kind=ATTACK, key=(ATTACK, 9), label="attack", head="attack", cell=(40, 40))
    gather = Action(kind=GATHER, key=(GATHER, (30, 30)), label="gather", head="gather", cell=(40, 40))
    hold = Action(kind=HOLD, key=(HOLD, 1), label="hold", head="hold")

    assert [continues(scene.actions(action)) for action in (attack, gather, hold)] == [[], [], []]


# -- the answer naming Z ------------------------------------------------------------------------------------


def test_answer_z_keeps_the_standing_action_its_taken_tick_and_its_order(match_record):
    scene = commanding(CommanderTuning())
    picked = scene.question.actions[1]
    scene.cycle(1000)
    scene.chooser.answer(picked.id)
    scene.cycle(1005)
    order = {"cmd": "move", "units": [999], "to": list(picked.cell)}
    world = scene.agent.world
    assert scene.commander.order(SCOUT_ROLE, order, world) is not None
    others = [action for action in scene.question.actions if action.key != picked.key]
    with_z = (*others, dataclasses.replace(command_prompt.continued(picked), id=CONTINUE_ID))
    scene.question = dataclasses.replace(scene.question, actions=with_z)
    scene.cycle(1010, offered=with_z)
    scene.chooser.answer(CONTINUE_ID)

    scene.cycle(1015, offered=with_z)

    assert scene.commander.standing.key == picked.key and scene.commander.taken_tick == 1005
    assert scene.commander.order(SCOUT_ROLE, order, world) is None
    assert [event["kind"] for event in match_record("commander")].count("action") == 1
    assert (scene.commander.continue_picked, scene.commander.continue_offered) == (1, 1)


def test_answer_naming_another_action_3_s_after_the_pick_is_still_committed():
    scene = commanding(CommanderTuning())
    picked, other = scene.question.actions[1], scene.question.actions[2]
    scene.cycle(1000)
    scene.chooser.answer(picked.id)
    scene.cycle(1005)
    scene.cycle(1055)
    scene.chooser.answer(other.id)

    scene.cycle(1080)

    assert scene.commander.standing.key == picked.key and scene.commander.committed_answers == 1


# -- the scout's continue option ------------------------------------------------------------------------------


@dataclasses.dataclass
class Scout:
    """The scout's world in one cycle and what its actions are built from."""

    agent: situations.RedAlertAgent
    sight: SightGrid
    offered: list

    def actions(self, standing: Action | None, tuning: CommanderTuning | None = None) -> tuple[Action, ...]:
        world, ctx = self.agent.world, self.agent.ctx
        if tuning is not None:
            ctx.tuning = dataclasses.replace(ctx.tuning, commander=tuning)
        dangers = scout_steering.Dangers(world, ctx)
        jeep = world.combat_units[SCOUT_ID]
        return command_scout.scout_actions(jeep, dangers, self.sight, self.offered, world, ctx, standing)


def scout_at(cell=(40, 40), **observation) -> Scout:
    agent = situations.new_agent(tuning=SCOUTING)
    jeep = situations.unit(SCOUT_ID, "jeep", *cell)
    state = base_state("england", YARD, POWER, ("tent", 18, 44), tick=6000, units=[jeep], **observation)
    ask(agent, state, situations.context(TWO_STARTS))
    world, ctx = agent.world, agent.ctx
    ctx.terrain = Terrain([OrePatch(center=field, cells=frozenset({field})) for field in FIELDS])
    ctx.scout_report = EnemyIntel().observe(world, ctx.tuning.scouting)
    sight = SightGrid()
    sight.observe(world)
    return Scout(agent, sight, MissionPlanner().missions(world, ctx, sight))


def mission_action(mission) -> Action:
    """A mission as the scout's standing action, as it was taken."""
    return Action(
        kind=mission.kind,
        key=mission.key,
        label="watch",
        head=f"watch {command_prompt.at(mission.cell)}",
        cell=mission.cell,
    )


def outranked(scene: Scout) -> tuple[Action, Action]:
    """The expansion field mission the list offers, D, and another the best of its kind left off, as a standing
    action."""
    listed = scene.actions(None)
    shown = next(action for action in listed if action.kind == EXPANSION_WATCH)
    other = next(mission for mission in scene.offered if mission.kind == EXPANSION_WATCH and mission.key != shown.key)
    return shown, mission_action(other)


def test_scout_an_expansion_field_outranked_by_another_of_its_kind_is_offered_as_z_beside_d():
    scene = scout_at()
    shown, standing = outranked(scene)

    actions = scene.actions(standing)

    letters = {action.id: action for action in actions}
    assert letters["D"].key == shown.key and letters[CONTINUE_ID].key == standing.key


def test_scout_no_z_within_3_cells_of_the_mission_s_cell():
    scene = scout_at()
    _, standing = outranked(scene)
    near = scout_at((standing.cell[0] + 2, standing.cell[1]))

    assert continues(near.actions(standing)) == []


def test_scout_no_z_under_a_danger_flight_or_on_a_path_that_passes_danger(monkeypatch):
    scene = scout_at()
    _, standing = outranked(scene)
    monkeypatch.setattr(command_scout, "fled_from", lambda *given: (0, 0))
    fleeing = scene.actions(standing)
    monkeypatch.undo()
    monkeypatch.setattr(command_scout, "route", lambda *given, **more: None)

    assert continues(fleeing) == [] and continues(scene.actions(standing)) == []


def test_scout_a_full_list_gives_way_with_its_lowest_scored_mission_never_hold_or_back_off():
    scene = scout_at()
    _, standing = outranked(scene)
    full = scene.actions(None, CommanderTuning(options_max=len(scene.actions(None))))
    world, ctx = scene.agent.world, scene.agent.ctx
    jeep = world.combat_units[SCOUT_ID]
    dangers = scout_steering.Dangers(world, ctx).leaving((jeep.x, jeep.y))
    by_score = command_scout._mission_actions(jeep, dangers, scene.sight, scene.offered, world, ctx)

    actions = scene.actions(standing, CommanderTuning(options_max=len(full)))

    removed = {action.key for action in full} - {action.key for action in actions}
    assert len(actions) == len(full) and continues(actions)
    assert removed == {by_score[len(full) - 2].key}
    assert command_scout.HOLD in {action.kind for action in actions}


# -- the switch -------------------------------------------------------------------------------------------------


def test_switch_off_the_questions_hold_no_z():
    off = dataclasses.replace(ARMY, commander=CommanderTuning(continue_option=False))
    scene = main_in(tuning=off, **MOVED_GOAL)
    commander = commanding(CommanderTuning(continue_option=False)).commander

    assert commander.continued(commander.role) is None and commander.continued_scout() is None
    assert continues(scene.actions(None)) == []


def test_tuning_ships_on_built_in_and_in_the_tracked_file_and_switches_off_holds_it(match_record):
    assert CommanderTuning().continue_option is True
    assert Tuning.from_file(situations.REPO_ROOT).commander.continue_option is True
    assert SWITCHES_OFF.commander.continue_option is False and TUNING_FILE.name == "tuning.toml"
    commander = commanding(CommanderTuning()).commander
    commander.finalize()
    assert match_record("commander")[-1] == {
        "kind": "summary",
        "committed_answers": 0,
        "continue_offered": 0,
        "continue_picked": 0,
    }


# -- with main's stepped movement ---------------------------------------------------------------------------------


def test_stepped_an_answer_z_mid_step_leaves_the_step_index_and_timers_as_they_were_and_gives_no_new_order(
    monkeypatch,
):
    scene, combat = advancing(monkeypatch)
    # A nearer war factory turns the objective away from the one main advances on, which stays remembered.
    moved = {"visible_enemy_buildings": [enemy(641, "weap", 72, 48), enemy(643, "weap", 40, 62)]}
    scene.cycle(1010, units=tanks_near(37, 48, idle=False), **moved)
    asked = scene.chooser.submitted[-1]
    assert CONTINUE_ID in [option.id for option in asked.options]
    scene.chooser.answer(CONTINUE_ID)

    scene.cycle(1015, units=tanks_near(38, 48, idle=False), **moved)

    move = combat._stepper.move
    assert (move.index, move.point, move.ordered_tick) == (1, FIRST_STEP, 1005)
    assert step_orders(scene.batches[-1], FIRST_STEP) == []
    assert scene.agent.ctx.commander.standing.key == (ADVANCE, (72, 48))
