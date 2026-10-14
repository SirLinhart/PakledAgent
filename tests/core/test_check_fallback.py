"""The readiness check `fallback`: Red Alert answers the opening with every decision source failing."""

from __future__ import annotations

import pytest

from altruagent import WAIT, WithReasoning
from ucla_agent.check import fallback
from ucla_agent.check.line import FAIL, PASS
from ucla_agent.core import records
from ucla_agent.core.decide import ChoiceRequest, Option
from ucla_agent.games.redalert.agent import RedAlertAgent

DEPLOY = {"cmd": "deploy", "units": [fallback.MCV_ID]}


def answering(action):
    """A replacement for the decision-maker's `choose_action` that answers `action` as the agent wraps it."""
    return lambda self, state, context: action if action is WAIT else WithReasoning(action, "a summary")


def test_the_opening_state_holds_one_mcv_and_the_game_is_running():
    state = fallback.opening_state()

    assert state["phase"] == "playing" and not state["is_terminal"]
    assert [(unit["id"], unit["type"]) for unit in state["observation"]["units"]] == [(fallback.MCV_ID, "mcv")]
    assert state["observation"]["buildings"] == []


def test_the_forced_failure_raises_whatever_is_asked():
    request = ChoiceRequest("Which plan?", [Option("HOLD", "hold")], "", 1500, "0")

    with pytest.raises(ConnectionError):
        fallback.ForcedFailure().ask(request)


def test_with_every_source_failing_the_tracked_agent_deploys_its_mcv_within_100_ms(checked_project):
    line = fallback.check_fallback(checked_project)

    assert line.status == PASS
    assert line.detail.startswith(
        "with every decision source failing, Red Alert answered the opening with deploy 1 in "
    )


def test_the_decision_maker_is_made_with_model_plan_choice_on_and_a_failing_chooser(checked_project, monkeypatch):
    made = []
    original = RedAlertAgent.__init__

    def note(self, **options):
        made.append(options)
        original(self, **options)

    monkeypatch.setattr(RedAlertAgent, "__init__", note)

    fallback.check_fallback(checked_project)

    (options,) = made
    assert options["settings"].model_plan_choice is True
    assert isinstance(options["chooser"]._source, fallback.ForcedFailure)


def test_unusable_red_alert_settings_are_played_on_built_in_ones_as_the_agent_would(checked_project):
    agent_toml = checked_project / "config" / "agent.toml"
    agent_toml.write_text(agent_toml.read_text(encoding="utf-8").replace("model_commands = false", ""), "utf-8")

    assert fallback.check_fallback(checked_project).status == PASS


def test_no_match_record_is_opened(checked_project):
    fallback.check_fallback(checked_project)

    assert records._process.writer is None and not (checked_project / "runs").exists()


@pytest.mark.parametrize(
    "action, said",
    [
        (WAIT, "answered WAIT"),
        ({"type": "orders", "orders": []}, "not a batch of 1 to 20 orders"),
        ({"type": "orders", "orders": [DEPLOY] * 21}, "not a batch of 1 to 20 orders"),
        ({"type": "message", "text": "hi"}, "not a batch of 1 to 20 orders"),
        ({"type": "orders", "orders": [{"units": [100]}]}, "answered an order that names no command"),
        ({"type": "orders", "orders": [{"cmd": "stop", "units": list(range(51))}]}, "an order for more than 50 units"),
    ],
    ids=["wait", "empty", "21 orders", "not orders", "no command", "51 units"],
)
def test_an_answer_the_platform_would_not_take_fails(checked_project, monkeypatch, action, said):
    monkeypatch.setattr(RedAlertAgent, "choose_action", answering(action))

    line = fallback.check_fallback(checked_project)

    assert line.status == FAIL and said in line.detail


def test_an_answer_later_than_the_limit_fails(checked_project, monkeypatch):
    monkeypatch.setattr(fallback, "ANSWER_LIMIT_MS", 0)

    line = fallback.check_fallback(checked_project)

    assert line.status == FAIL and line.detail.endswith("ms, over 0")


def test_a_decision_maker_that_raises_fails(checked_project, monkeypatch):
    def broken(self, state, context):
        raise KeyError("observation")

    monkeypatch.setattr(RedAlertAgent, "choose_action", broken)

    line = fallback.check_fallback(checked_project)

    assert (line.status, line.detail) == (FAIL, "the Red Alert decision-maker raised KeyError: 'observation'")
