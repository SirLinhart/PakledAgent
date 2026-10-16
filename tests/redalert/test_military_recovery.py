"""The military recovery: the military focus at once while our army is below its bar, has dropped, or the base lost
units or defenses under attack."""

from __future__ import annotations

import situations
from situations import ask
from switches_off import tuning_off, upkeep_off
from test_spending_focus import base, focus_agent, units
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import BALANCED_FOCUS, MILITARY_FOCUS
from ucla_agent.games.redalert.spending_focus import (
    ARMY_DROPPED,
    BELOW_BAR,
    LOST_AT_BASE,
    MILITARY_RECOVERY,
    SPENDING_FOCUS_EVENT,
)
from ucla_agent.games.redalert.tuning import Tuning, UpkeepTuning

RECOVERY = tuning_off(
    upkeep=upkeep_off(refineries_max=2, refineries_max_late=2, spending_focus=True, military_recovery=True)
)
NO_RECOVERY = tuning_off(upkeep=upkeep_off(refineries_max=2, refineries_max_late=2, spending_focus=True))
SECOND = TICKS_PER_SECOND


def recovery_events(match_record) -> list[dict]:
    return [event for event in match_record(SPENDING_FOCUS_EVENT) if event.get("reason") == MILITARY_RECOVERY]


def test_an_army_below_800_starts_it_at_once(match_record):
    agent, _ = focus_agent(None, RECOVERY)
    off, _ = focus_agent(None, NO_RECOVERY)

    ask(agent, base(army=units(rockets=2)))
    ask(off, base(army=units(rockets=2)))

    assert agent.ctx.spending_focus == MILITARY_FOCUS and off.ctx.spending_focus == BALANCED_FOCUS
    (start,) = recovery_events(match_record)
    assert (start["phase"], start["cause"], start["army"], start["bar"]) == ("start", BELOW_BAR, 600, 800)


def seen_tanks(count: int) -> list[dict]:
    """Enemy heavy tanks (850 each) in sight far out: the enemy army the estimate is held to 1.5 times of."""
    return [{"id": 900 + n, "type": "2tnk", "x": 70, "y": 20 + n, "hp": 100} for n in range(count)]


def causes(match_record) -> list[str]:
    return [event["cause"] for event in recovery_events(match_record) if event["phase"] == "start"]


def test_an_army_above_its_bar_needs_none(match_record):
    agent, _ = focus_agent(2000, RECOVERY)

    ask(agent, base(army=units(tanks=4), visible_enemies=seen_tanks(3)))

    # 2,800 against a bar of max(0.8 x 2,000, 800).
    assert causes(match_record) == []


def test_an_army_below_08_of_the_estimate_starts_it(match_record):
    agent, _ = focus_agent(4000, RECOVERY)

    ask(agent, base(army=units(tanks=4), visible_enemies=seen_tanks(4)))

    # 2,800 against a bar of 0.8 x 4,000; 4 tanks seen, 3,400 x 1.5, do not hold E below 4,000.
    assert causes(match_record) == [BELOW_BAR]


def test_the_estimate_is_held_to_15_times_the_army_seen(match_record):
    # The record's 5604: our 1,900 against an estimate of 2,379 grown on unseen income, 1,000 of the enemy seen.
    agent, _ = focus_agent(2379, RECOVERY)
    rifles = [{"id": 900 + n, "type": "e1", "x": 70, "y": 20 + n, "hp": 100} for n in range(10)]

    ask(agent, base(army=units(rockets=6, tanks=1), visible_enemies=rifles))

    assert causes(match_record) == []


def test_an_army_that_lost_40_percent_below_the_enemys_starts_it(match_record):
    agent, _ = focus_agent(5000, RECOVERY)
    ask(agent, base(army=units(tanks=11), visible_enemies=seen_tanks(6)))

    ask(agent, base(army=units(tanks=6), tick=1000 + 4 * SECOND))

    # 7,700 to 4,200: 45% lost, under the enemy's 5,000 and above its bar of 4,000.
    assert causes(match_record) == [ARMY_DROPPED]


def test_a_drop_that_leaves_us_above_the_enemy_starts_nothing(match_record):
    agent, _ = focus_agent(1500, RECOVERY)
    ask(agent, base(army=units(tanks=6), visible_enemies=seen_tanks(2)))

    ask(agent, base(army=units(tanks=3), tick=1000 + 4 * SECOND))

    # 4,200 to 2,100, still above the enemy's 1,500.
    assert causes(match_record) == []


def test_1000_lost_under_a_threat_below_the_enemys_army_starts_it(match_record):
    agent, _ = focus_agent(5000, RECOVERY)
    ask(agent, base(army=units(tanks=8), visible_enemies=seen_tanks(6)))
    agent.ctx.threat_active = True

    ask(agent, base(army=units(tanks=6), tick=1000 + 4 * SECOND))

    # 5,600 to 4,200: a quarter lost, 1,400 at the base, under the enemy's 5,000 and above its bar of 4,000.
    assert causes(match_record) == [LOST_AT_BASE]


def test_a_loss_at_the_base_with_our_army_above_the_enemys_starts_nothing(match_record):
    # The record's 11255: our 9,200 lost units at the base against an estimate grown to 10,861 on unseen income.
    agent, _ = focus_agent(10861, RECOVERY)
    ask(agent, base(army=units(tanks=14), visible_enemies=seen_tanks(3)))
    agent.ctx.threat_active = True

    ask(agent, base(army=units(tanks=12), tick=1000 + 4 * SECOND))

    assert causes(match_record) == []


def test_it_ends_at_once_once_our_army_matches_the_enemys(match_record):
    agent, _ = focus_agent(3000, RECOVERY)
    ask(agent, base(army=units(tanks=3), visible_enemies=seen_tanks(3)))

    ask(agent, base(army=units(tanks=5), tick=1000 + SECOND))

    # 2,100 under the bar of 2,400, then 3,500 over the enemy's 3,000: ended in the next cycle, not 30 s later.
    assert [event["phase"] for event in recovery_events(match_record)] == ["start", "end"]


def test_it_ends_30s_after_the_army_is_back_above_its_bar(match_record):
    agent, _ = focus_agent(None, RECOVERY)
    ask(agent, base(army=units(rockets=2)))
    ask(agent, base(army=units(tanks=4), tick=1000 + SECOND))
    ask(agent, base(army=units(tanks=4), tick=1000 + 29 * SECOND))
    held = agent.ctx.spending_focus

    ask(agent, base(army=units(tanks=4), tick=1000 + 30 * SECOND))

    assert held == MILITARY_FOCUS
    phases = [(event["phase"], event.get("since")) for event in recovery_events(match_record)]
    assert phases == [("start", None), ("end", 1000)]


def test_tuning_military_recovery_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).upkeep, UpkeepTuning()):
        assert tuning.military_recovery is True
        assert (tuning.recovery_min_army, tuning.recovery_enemy_share, tuning.recovery_drop_share) == (800, 0.8, 0.4)
        assert (tuning.recovery_drop_seconds, tuning.recovery_attack_seconds, tuning.recovery_clear_seconds) == (
            120,
            60,
            30,
        )


def test_the_military_focus_of_the_recovery_ends_with_it_whatever_the_grown_estimate():
    # The record's estimate of 10,861 grown on unseen income, 3 tanks of the enemy seen.
    agent, _ = focus_agent(10861, RECOVERY)
    ask(agent, base(army=units(rockets=2), visible_enemies=seen_tanks(3)))
    during = agent.ctx.spending_focus

    ask(agent, base(army=units(tanks=14), tick=1000 + SECOND))

    # 9,800 against the enemy's 3,825 (1.5 x 2,550 seen): the recovery ends, and the MCV is no longer held.
    assert during == MILITARY_FOCUS and agent.ctx.spending_focus == BALANCED_FOCUS


# -- the spending focus on the capped estimate ---------------------------------------------------------------------


def test_the_focus_weighs_the_capped_estimate_and_holds_no_mcv_from_an_army_above_it():
    # The record's 4087: an estimate of 2,000 against our 700, with 300 of the enemy seen.
    agent, _ = focus_agent(2000, RECOVERY)
    rifles = [{"id": 900 + n, "type": "e1", "x": 70, "y": 20 + n, "hp": 100} for n in range(3)]

    ask(agent, base(army=units(tanks=1), visible_enemies=rifles))

    # E as the focus weighs it is 450: our 700 is above it, and no MCV is held.
    assert agent.ctx.mcv_held_by_focus is False


def test_without_the_cap_the_focus_holds_the_mcv_on_the_grown_estimate():
    off = tuning_off(
        upkeep=upkeep_off(refineries_max=2, refineries_max_late=2, spending_focus=True, military_recovery=False)
    )
    agent, _ = focus_agent(2000, off)

    ask(agent, base(army=units(tanks=1)))
    ask(agent, base(army=units(tanks=1), tick=1000 + 15 * SECOND))

    assert agent.ctx.spending_focus == MILITARY_FOCUS and agent.ctx.mcv_held_by_focus is True
