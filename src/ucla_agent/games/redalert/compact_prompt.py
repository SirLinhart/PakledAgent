"""The compact plan prompt: the plan question put to a model in about a thousand characters.

The plain-words briefing teaches a small model Red Alert in about 9,000
characters, and most of it is no help in choosing among two or three plans: a
small model reads it slowly and is led by its many figures to weigh the wrong
ones. With `[redalert] plan_prompt = "compact"` the plan question is this
prompt instead, in three parts:

1. A fixed system text (`SYSTEM_TEXT`): choose one open plan from the facts
   given, and answer with the choice first.
2. The state as `key=value` lines, always the keys of `KEYS` in that order,
   each value the plan rules' own figure of the cycle:
   - `left_s`: the seconds of the match left, `unknown` while the state gives
     no clock; `base_threat`: base defense's threat level (none, minor or
     major);
   - `army_value`, `enemy_estimate` and `army_ratio`: the own army and the
     enemy's as the plan rules weigh them (`plans.weighed_army`,
     `plans.weighed_enemy`) and the one ratio the decision table reads;
     `enemy_estimate_kind`: `assumed` while the enemy army is unknown and is
     taken to be a share of ours, else `seen_peak`, what has been seen of it;
     `last_enemy_army_seen_s`: the seconds since an enemy unit that fights was
     in sight, `never` before one has been;
   - `cash` (the money there is to spend) and `income_per_min`, `unknown`
     before the income is measured;
   - `our_refineries` and `our_harvesters`; `enemy_refineries` and
     `enemy_harvesters` remembered, and `enemy_economy_age_s`, the seconds
     since the longest ago seen of those was in sight, so every count is at
     most that old (`never` while none is remembered);
   - `raid_ready`: `yes` while the raid group holds its full quota, so it sets
     out at once; `raid_value`: the raid group's strength now; `raid_targets`:
     how many targets a raid may be sent at, and `raid_target_age_s`, the
     seconds since the longest ago seen of them was in sight (`none` without
     a target);
   - `press_block`: what keeps PRESS shut, the codes of
     `plans.press_blockers` joined by commas, or `none` while it is open.

   Then the model's recent plan picks (see `model_history`): `recent
   picks:` and a line for each, the newest first, with its age, the plan,
   the value killed and lost since and the army ratio then and now; or
   `recent picks: none` before the first.
3. `OPEN:` and a line `ID: <what moves and what it costs>` for each open plan,
   from the fixed table `OPTION_LINES`. It holds HOLD, RAID and PRESS only:
   FINISH and PRESERVE come from the script's rules and are never offered.

There is no glossary, no list of units and no recommendation of the
script's. The recent picks are the one exception to the facts of the cycle,
and they recommend nothing: they say what the model picked and what came of
it. The framing paragraph the other plan questions carry
(`model_history.FRAMING`), whose doctrine is a recommendation, is left out
here. The answer is a JSON object with the choice first, held by the
source's schema to the open plans, and a reason of at most 12 words, in at
most `ANSWER_TOKENS` tokens.

The whole prompt, the system text and the user message as a model source
lays it out (`local.choice_prompt`), is held to `MAX_PROMPT_CHARS`. A key line
or an open plan's line is never dropped: a longer prompt has its recent picks
cut first, the oldest first, then its plans' lines cut to their first
`OPTION_WORDS` words, and one that is still longer is an error. Every value
is a number, a word or a few codes, so a prompt comes to about a thousand
characters whatever the state, and about 200 more with three recent
picks.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from ucla_agent.core.decide import ChoiceRequest, Option
from ucla_agent.core.decide.local import choice_prompt
from ucla_agent.games.redalert import plans, raid_targets, raids
from ucla_agent.games.redalert.composition import HOLD, PRESS, RAID
from ucla_agent.games.redalert.family import HARVESTER, REFINERY
from ucla_agent.games.redalert.fight import strength
from ucla_agent.games.redalert.groups import RAID as RAID_GROUP
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.plans import Situation
from ucla_agent.games.redalert.world import EnemyRecord, World

SYSTEM_TEXT = (
    "Choose one OPEN plan. Use only supplied facts. Enemy counts may be stale; unknown is not zero. "
    "Prefer viable pressure when home is safe; HOLD must have a concrete benefit. "
    'Return only {"choice":"ID","reason":"up to 12 words"}.'
)
# The question: the heading of the open plans' lines.
OPEN_HEADING = "OPEN:"
# What each plan a model may be offered does now, at most 15 words.
OPTION_LINES: Mapping[str, str] = {
    HOLD: "Keep main home; grow forces but concede map pressure.",
    RAID: "Send the raid group at exposed harvesters; main holds.",
    PRESS: "Advance main toward the enemy base; gain ground, risk losses.",
}
KEYS = (
    "left_s",
    "base_threat",
    "army_value",
    "enemy_estimate",
    "army_ratio",
    "enemy_estimate_kind",
    "last_enemy_army_seen_s",
    "cash",
    "income_per_min",
    "our_refineries",
    "our_harvesters",
    "enemy_refineries",
    "enemy_harvesters",
    "enemy_economy_age_s",
    "raid_ready",
    "raid_value",
    "raid_targets",
    "raid_target_age_s",
    "press_block",
)
MAX_PROMPT_CHARS = 1500
# A prompt over the cap has each plan's line cut to this many words.
OPTION_WORDS = 10
ANSWER_TOKENS = 48
SEEN_PEAK = "seen_peak"
ASSUMED = "assumed"
UNKNOWN = "unknown"
NEVER = "never"
NONE = "none"
YES, NO = "yes", "no"
SECONDS_PER_MINUTE = 60


class CompactPrompt:
    """Writes the compact plan question of a cycle. See the module's note."""

    def __init__(self, max_chars: int = MAX_PROMPT_CHARS, option_lines: Mapping[str, str] = OPTION_LINES) -> None:
        """A prompt held to `max_chars`, offering each plan with its line of `option_lines`."""
        self._max_chars = max_chars
        self._option_lines = option_lines

    def request(
        self,
        world: World,
        ctx: MatchContext,
        situation: Situation,
        eligible: list[str],
        deadline_ms: int,
        state_tag: str,
    ) -> ChoiceRequest:
        """The plan question of the cycle whose figures are `situation`, offering the open plans `eligible`.

        Raises `ValueError` when the prompt is over its cap even with every plan's line cut.
        """
        values = state_values(world, ctx, situation)
        keys = [f"{key}={values[key]}" for key in KEYS]
        history = ctx.model_history
        lines = {plan: self._option_lines[plan] for plan in eligible}
        # The recent picks are cut first, the oldest first, down to none.
        for picks in range(len(history.plan_picks), -1, -1):
            context = "\n".join([*keys, *history.plan_lines(world, picks)])
            full = _request(context, lines, deadline_ms, state_tag)
            if prompt_chars(full) <= self._max_chars:
                return full
        cut = {option.id: first_words(option.description, OPTION_WORDS) for option in full.options}
        shorter = _request(context, cut, deadline_ms, state_tag)
        if prompt_chars(shorter) > self._max_chars:
            raise ValueError(
                f"The compact plan prompt is {prompt_chars(shorter)} characters with every plan's line cut to "
                f"{OPTION_WORDS} words, over its cap of {self._max_chars}."
            )
        return shorter


def _request(context: str, lines: Mapping[str, str], deadline_ms: int, state_tag: str) -> ChoiceRequest:
    """The plan question with these state lines and these open plans' lines, answered with the choice first."""
    return ChoiceRequest(
        question=OPEN_HEADING,
        options=[Option(plan, line) for plan, line in lines.items()],
        context=context,
        deadline_ms=deadline_ms,
        state_tag=state_tag,
        reason_first=False,
        system_text=SYSTEM_TEXT,
        answer_tokens=ANSWER_TOKENS,
    )


def prompt_chars(request: ChoiceRequest) -> int:
    """How many characters a model reads of a request: its system text and its user message as a source lays it
    out."""
    return len(request.system_text or "") + len(choice_prompt(request))


def first_words(text: str, count: int) -> str:
    """The first `count` words of a text."""
    return " ".join(text.split()[:count])


def state_values(world: World, ctx: MatchContext, situation: Situation) -> dict[str, str]:
    """Each key of `KEYS` -> its value in the cycle whose figures are `situation`."""
    return {
        **_clock_and_threat(situation, ctx),
        **_armies(situation, ctx),
        **_money(world, ctx),
        **_economies(world, ctx),
        **_raid(world, ctx),
        "press_block": ",".join(plans.press_blockers(situation, ctx.tuning.plan_rules, ctx.plan == PRESS)) or NONE,
    }


def _clock_and_threat(situation: Situation, ctx: MatchContext) -> dict[str, str]:
    """The seconds of the match left and the threat to the base."""
    left = UNKNOWN if situation.remaining_seconds is None else str(int(situation.remaining_seconds))
    return {"left_s": left, "base_threat": ctx.threat_level}


def _armies(situation: Situation, ctx: MatchContext) -> dict[str, str]:
    """The two armies as the plan rules weigh them, their ratio, and how the enemy's was come by."""
    rules = ctx.tuning.plan_rules
    is_assumed = rules.blind_press_guard and not situation.enemy_army_known
    unseen = situation.enemy_unseen_ticks
    return {
        "army_value": str(plans.weighed_army(situation, rules)),
        "enemy_estimate": str(round(plans.weighed_enemy(situation, rules))),
        "army_ratio": f"{situation.ratio:.2f}",
        "enemy_estimate_kind": ASSUMED if is_assumed else SEEN_PEAK,
        "last_enemy_army_seen_s": NEVER if unseen is None else str(unseen // TICKS_PER_SECOND),
    }


def _money(world: World, ctx: MatchContext) -> dict[str, str]:
    """The money there is to spend and the income per minute."""
    income = ctx.income.income_per_s
    per_minute = UNKNOWN if income is None else str(round(income * SECONDS_PER_MINUTE))
    return {"cash": str(world.economy.spendable), "income_per_min": per_minute}


def _economies(world: World, ctx: MatchContext) -> dict[str, str]:
    """Both sides' refineries and harvesters, the enemy's as remembered, and how old the enemy's counts are."""
    refineries = _remembered(world, REFINERY, is_building=True)
    harvesters = _remembered(world, HARVESTER, is_building=False)
    return {
        "our_refineries": str(ctx.production.standing(REFINERY)),
        "our_harvesters": str(world.economy.harvesters),
        "enemy_refineries": str(len(refineries)),
        "enemy_harvesters": str(len(harvesters)),
        "enemy_economy_age_s": _oldest_age(world, [*refineries, *harvesters], NEVER),
    }


def _raid(world: World, ctx: MatchContext) -> dict[str, str]:
    """Whether the raid group is complete, its strength now, and the targets a raid may be sent at with their age."""
    tuning = ctx.tuning
    members = [world.combat_units[unit_id] for unit_id in ctx.groups.members(RAID_GROUP)]
    quota = sum(raids.quota(world, tuning.composition).values())
    targets = raid_targets.targets(world, plans.raid_group_types(world, tuning), tuning)
    return {
        "raid_ready": YES if 0 < quota <= len(members) else NO,
        "raid_value": str(round(strength(members, world))),
        "raid_targets": str(len(targets)),
        "raid_target_age_s": _oldest_age(world, targets, NONE),
    }


def _remembered(world: World, base_type: str, *, is_building: bool) -> list[EnemyRecord]:
    """The remembered enemies of a type or a variant of it."""
    return [
        record
        for record in world.enemy_memory.values()
        if record.is_building == is_building and _base_type(record.type, world) == base_type
    ]


def _base_type(actor_type: str, world: World) -> str:
    """The type an actor's type is a variant of, or the type itself."""
    facts = world.items.get(actor_type)
    return actor_type if facts is None else facts.name


def _oldest_age(world: World, records: Iterable[EnemyRecord], without: str) -> str:
    """The seconds since the longest ago seen of the records was in sight; `without` when there are none."""
    ticks = [world.tick - record.last_seen_tick for record in records]
    return str(max(ticks) // TICKS_PER_SECOND) if ticks else without
