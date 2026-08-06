"""Natural language in, a validated `Plan` out.

Two parsers, one contract. The language model is tried first when a key exists, and a
rule-based parser answers whenever it does not — or whenever the model returns something
that fails `Plan` validation, which is the same thing as far as this module is concerned.

**Neither parser is trusted.** Both produce a `Plan`, which is a Pydantic model, which then
goes through `dsl.validate.resolve` against the tile before any core sees a number. An LLM
that hallucinates "plant 900,000 trees" produces a well-formed plan that the validator
refuses on arithmetic, which is exactly the arrangement that lets a free-tier model near
the product at all.

The rule parser is not a fallback in the apologetic sense. It handles the phrasings a demo
actually uses, it never fails to be available, and it is the only path the test suite
exercises — because no test may touch the network.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from terrarium.config import Season
from terrarium.dsl.llm import LLMAdapter, LLMUnavailable
from terrarium.dsl.schema import Plan, PlantTrees, RestrictVehicles

logger = logging.getLogger(__name__)

PlanSource = Literal["llm", "rules"]


class PlanParseError(ValueError):
    """Nothing in the text described an intervention. Carries what *is* understood."""


class ParsedPlan(BaseModel):
    """A plan and the honest provenance of it."""

    model_config = ConfigDict(frozen=True)

    plan: Plan
    source: PlanSource = Field(
        description="'llm' when a model produced it, 'rules' when the parser did"
    )
    warnings: tuple[str, ...] = Field(
        default=(),
        description="Where the model was overruled, or what the parser had to assume",
    )


# ------------------------------------------------------------------ rule parser ---

# Urdu (Phase 11). The voice path captures `ur-PK`, so an Urdu sentence has to reach a
# parser that can read it — otherwise "voice in English and Urdu" means English, plus a
# microphone that produces a 422 in Urdu. The vocabulary is small on purpose: these are the
# words for the two things the DSL can express, not an attempt at Urdu NLP. Everything
# else about the sentence is ignored, exactly as it is in English.
#
# Note the script is right-to-left and the digits are usually Eastern Arabic-Indic
# (۰-۹), which no `\d` in a naive pattern matches. `_normalise` folds them to
# ASCII before any pattern runs, which is why one set of number patterns serves both
# languages.
_URDU_DIGITS = {
    **{chr(0x06F0 + i): str(i) for i in range(10)},  # Urdu ۰-۹
    **{chr(0x0660 + i): str(i) for i in range(10)},  # Arabic ٠-٩
}
_URDU_PERCENT = "٪"  # ٪
# درخت tree, پودے saplings, شجرکاری afforestation
_URDU_TREE_WORDS = r"درخت|پودے|پودوں|شجرکاری|شجر کاری"
# سایہ shade, ہریالی greenery
_URDU_CANOPY_WORDS = r"سایہ|سائبان|ہریالی|درختوں کا"
# گاڑی vehicle, ٹریفک traffic, پابندی ban, دھواں smoke
_URDU_TRAFFIC_WORDS = r"گاڑیوں|گاڑیاں|گاڑی|ٹریفک|دھواں|ڈیزل"
_URDU_BAN_WORDS = r"پابندی|بند کریں|بند کر|منع"
_URDU_WINTER = r"سردی|سردیوں|موسم سرما|جاڑا"
_URDU_SUMMER = r"گرمی|گرمیوں|موسم گرما"

# "5,000 trees", "5000 trees", "5k trees", "two thousand" is deliberately not supported:
# spelled-out numerals are a rabbit hole and a demo types digits.
_TREES = re.compile(
    rf"(\d[\d,\s]*(?:\.\d+)?)\s*(k|thousand|ہزار)?\s*(?:more\s+)?(?:trees?|{_URDU_TREE_WORDS})"
)
# Urdu puts the number before the noun too ("5000 درخت"), so the same pattern covers both
# once the digits are folded — but Urdu also says "درخت 5000", hence the reversed form.
_TREES_REVERSED = re.compile(rf"(?:{_URDU_TREE_WORDS})\s*(\d[\d,\s]*)\s*(ہزار)?")
# A percentage near a greenery word, either order: "30% canopy" and "canopy by 30%".
_CANOPY_FIRST = re.compile(
    rf"(\d+(?:\.\d+)?)\s*(?:%|{_URDU_PERCENT}|per\s*cent|فیصد)"
    rf"[^.]{{0,24}}?(canopy|tree|shade|green|{_URDU_CANOPY_WORDS}|{_URDU_TREE_WORDS})"
)
_CANOPY_LAST = re.compile(
    rf"(canopy|tree cover|shade|greening|{_URDU_CANOPY_WORDS})"
    rf"[^.]{{0,24}}?(\d+(?:\.\d+)?)\s*(?:%|{_URDU_PERCENT}|per\s*cent|فیصد)"
)
# A percentage near a traffic word: "remove 40% of traffic".
_TRAFFIC_SHARE = re.compile(
    rf"(\d+(?:\.\d+)?)\s*(?:%|{_URDU_PERCENT}|per\s*cent|فیصد)\s*(?:of\s*)?(?:the\s*)?"
    rf"(traffic|vehicles?|cars?|emissions?|lorr|truck|{_URDU_TRAFFIC_WORDS})"
)
_TRAFFIC_WORDS = re.compile(
    rf"\b(ban|banning|car[- ]free|pedestriani[sz]|low[- ]emission zone|lez|congestion charge|"
    rf"combustion|no cars|no traffic|remove traffic|close .{{0,12}}to traffic|diesel)\b"
    rf"|(?:{_URDU_TRAFFIC_WORDS})[^.]{{0,20}}?(?:{_URDU_BAN_WORDS})"
    rf"|(?:{_URDU_BAN_WORDS})[^.]{{0,20}}?(?:{_URDU_TRAFFIC_WORDS})"
)
_WINDOW = re.compile(r"\b(20\d\d)\s*[-/ ]\s*(summer|winter)\b")
_WINDOW_REVERSED = re.compile(r"\b(summer|winter)\s*(?:of\s*)?(20\d\d)\b")
_SEASON = re.compile(r"\b(summer|winter)\b")
_URDU_SEASON = re.compile(rf"({_URDU_WINTER})|({_URDU_SUMMER})")


def _normalise(text: str) -> str:
    """Lowercase, and fold Urdu/Arabic digits to ASCII.

    One pass so that every number pattern below works in either script. Without it an Urdu
    sentence carrying ۵۰۰۰ parses as a plan with no quantity in it, which then reads as the
    parser not understanding Urdu at all rather than not understanding its digits.
    """
    return "".join(_URDU_DIGITS.get(ch, ch) for ch in text).lower()


def _number(raw: str, multiplier: str | None) -> int:
    value = float(raw.replace(",", "").replace(" ", ""))
    if multiplier:
        # "k", "thousand", and ہزار all mean the same thing.
        value *= 1_000
    return round(value)


def _fraction(raw: str) -> float:
    """A percentage as a fraction, clamped into (0, 1].

    Clamped rather than rejected: "plant 150% canopy" is a person exaggerating, and the
    per-cell cap in the core is what actually decides the outcome anyway.
    """
    return min(max(float(raw) / 100.0, 0.01), 1.0)


def parse_rules(text: str) -> ParsedPlan:
    """Read an intervention out of plain text without a model.

    Deterministic, offline, and the only parser the tests use.
    """
    lowered = _normalise(text)
    warnings: list[str] = []
    actions: list[PlantTrees | RestrictVehicles] = []

    trees = _TREES.search(lowered) or _TREES_REVERSED.search(lowered)
    canopy_first = _CANOPY_FIRST.search(lowered)
    canopy_last = _CANOPY_LAST.search(lowered)

    if trees is not None:
        actions.append(PlantTrees(tree_count=max(_number(trees.group(1), trees.group(2)), 1)))
        if canopy_first or canopy_last:
            # Both units in one sentence: the count is the commitment, so it wins, and the
            # user is told rather than left to wonder which half was read.
            warnings.append(
                "the text gave both a tree count and a canopy percentage; the count was "
                "used, because it is the one that has to physically fit"
            )
    elif canopy_first is not None:
        actions.append(PlantTrees(canopy_fraction_added=_fraction(canopy_first.group(1))))
    elif canopy_last is not None:
        actions.append(PlantTrees(canopy_fraction_added=_fraction(canopy_last.group(2))))

    share = _TRAFFIC_SHARE.search(lowered)
    if share is not None:
        actions.append(RestrictVehicles(emission_fraction_removed=_fraction(share.group(1))))
    elif _TRAFFIC_WORDS.search(lowered) is not None:
        actions.append(RestrictVehicles(emission_fraction_removed=1.0))
        warnings.append(
            "read as removing all vehicle emissions inside the polygon. That is traffic "
            "gone, not electrified — brake, tyre and road wear are about half of road "
            "PM2.5 and survive an EV"
        )

    if not actions:
        raise PlanParseError(
            "no intervention found in that. This layer understands two things: planting "
            "('5,000 trees', '30% canopy', '۵۰۰۰ درخت') and vehicle restriction ('ban "
            "combustion vehicles', 'remove 40% of traffic', 'گاڑیوں پر پابندی'), "
            "optionally with a season ('in winter', '2024-winter', 'سردیوں میں')."
        )

    window: str | None = None
    season: Season | None = None
    explicit = _WINDOW.search(lowered)
    reversed_ = _WINDOW_REVERSED.search(lowered)
    urdu_season = _URDU_SEASON.search(lowered)
    if explicit is not None:
        window = f"{explicit.group(1)}-{explicit.group(2)}"
    elif reversed_ is not None:
        window = f"{reversed_.group(2)}-{reversed_.group(1)}"
    else:
        loose = _SEASON.search(lowered)
        if loose is not None:
            season = Season(loose.group(1))
        elif urdu_season is not None:
            # A year in Urdu text still reads as ASCII digits after `_normalise`, but the
            # season word does not, so the two halves of a window are handled separately.
            season = Season.WINTER if urdu_season.group(1) else Season.SUMMER

    return ParsedPlan(
        plan=Plan(
            name=_name_for(actions),
            actions=tuple(actions),
            window=window,
            season=season,
        ),
        source="rules",
        warnings=tuple(warnings),
    )


def _name_for(actions: list[PlantTrees | RestrictVehicles]) -> str:
    kinds = {action.kind for action in actions}
    if kinds == {"plant_trees"}:
        return "Planting"
    if kinds == {"restrict_vehicles"}:
        return "Low-emission zone"
    return "Planting and low-emission zone"


# -------------------------------------------------------------------- llm path ---

SYSTEM_PROMPT = """\
You convert a city planner's request into one JSON object. Output JSON only.

Schema:
{
  "name": string, short label, max 80 chars,
  "actions": [ one or both of:
      {"kind": "plant_trees", "tree_count": integer >= 1}
      {"kind": "plant_trees", "canopy_fraction_added": number in (0, 1]}
      {"kind": "restrict_vehicles", "emission_fraction_removed": number in (0, 1]}
  ],
  "window": string like "2024-winter", or null,
  "season": "summer" | "winter" | null
}

Rules:
- A plant_trees action carries EITHER tree_count OR canopy_fraction_added, never both.
- At most one action of each kind.
- Percentages become fractions: "30% canopy" -> 0.30.
- "ban cars", "car-free", "low-emission zone" -> emission_fraction_removed 1.0.
- Use "window" only when the text names a year; otherwise use "season", or null for both.
- Never invent an action the text does not ask for. If the text asks for nothing you can
  express in this schema, return {"actions": []} and let the caller reject it.
"""


def plan_from_text(text: str, *, adapter: LLMAdapter | None = None) -> ParsedPlan:
    """Parse `text` with the model if there is one, and with the rules otherwise.

    The model is never given the last word: its output is parsed as a `Plan`, and anything
    that fails validation falls through to the rule parser with a warning saying so. That
    is the whole safety argument for putting a free-tier model in front of a simulator.
    """
    if adapter is None:
        return parse_rules(text)

    try:
        raw = adapter.complete_json(system=SYSTEM_PROMPT, user=text.strip())
        plan = Plan.model_validate(json.loads(raw))
    except (LLMUnavailable, json.JSONDecodeError, ValidationError, TypeError) as exc:
        logger.info("falling back to the rule parser: %s", type(exc).__name__)
        parsed = parse_rules(text)
        return parsed.model_copy(
            update={
                "warnings": (
                    *parsed.warnings,
                    f"{adapter.name} could not produce a valid plan "
                    f"({type(exc).__name__}); read by the rule parser instead",
                )
            }
        )

    return ParsedPlan(plan=plan, source="llm")
