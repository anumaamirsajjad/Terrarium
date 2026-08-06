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

# "5,000 trees", "5000 trees", "5k trees", "two thousand" is deliberately not supported:
# spelled-out numerals are a rabbit hole and a demo types digits.
_TREES = re.compile(r"(\d[\d,\s]*(?:\.\d+)?)\s*(k|thousand)?\s*(?:more\s+)?trees?\b")
# A percentage near a greenery word, either order: "30% canopy" and "canopy by 30%".
_CANOPY_FIRST = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent)\D{0,24}?(canopy|tree|shade|green)")
_CANOPY_LAST = re.compile(
    r"(canopy|tree cover|shade|greening)\D{0,24}?(\d+(?:\.\d+)?)\s*(?:%|per\s*cent)"
)
# A percentage near a traffic word: "remove 40% of traffic".
_TRAFFIC_SHARE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent)\s*(?:of\s*)?(?:the\s*)?"
    r"(traffic|vehicles?|cars?|emissions?|lorr|truck)"
)
_TRAFFIC_WORDS = re.compile(
    r"\b(ban|banning|car[- ]free|pedestriani[sz]|low[- ]emission zone|lez|congestion charge|"
    r"combustion|no cars|no traffic|remove traffic|close .{0,12}to traffic|diesel)\b"
)
_WINDOW = re.compile(r"\b(20\d\d)\s*[-/ ]\s*(summer|winter)\b")
_WINDOW_REVERSED = re.compile(r"\b(summer|winter)\s*(?:of\s*)?(20\d\d)\b")
_SEASON = re.compile(r"\b(summer|winter)\b")


def _number(raw: str, multiplier: str | None) -> int:
    value = float(raw.replace(",", "").replace(" ", ""))
    if multiplier:
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
    lowered = text.lower()
    warnings: list[str] = []
    actions: list[PlantTrees | RestrictVehicles] = []

    trees = _TREES.search(lowered)
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
            "('5,000 trees', '30% canopy') and vehicle restriction ('ban combustion "
            "vehicles', 'remove 40% of traffic'), optionally with a season "
            "('in winter', '2024-winter')."
        )

    window: str | None = None
    season: Season | None = None
    explicit = _WINDOW.search(lowered)
    reversed_ = _WINDOW_REVERSED.search(lowered)
    if explicit is not None:
        window = f"{explicit.group(1)}-{explicit.group(2)}"
    elif reversed_ is not None:
        window = f"{reversed_.group(2)}-{reversed_.group(1)}"
    else:
        loose = _SEASON.search(lowered)
        if loose is not None:
            season = Season(loose.group(1))

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
