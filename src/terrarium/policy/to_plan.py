"""A `PolicyMeasure` becomes a `Plan`, where the two levers can express it.

Pure arithmetic over a `PolicyMeasure`, with no model and no cube — the same shape as
`dsl/validate.py`, and for the same reason: what a measure becomes must be checkable
without a Zarr store.

**Most measures do not map, and that is the point of this module reporting rather than
filtering.** The DSL has exactly two levers, a canopy fraction and an emission fraction.
A fuel sulfur limit, a catalytic converter mandate, a CNG policy, an inspection regime and
a brick-kiln technology standard are all real commitments this model has no lever for.
Returning `None` for them and counting them is a finding about the scope of the two levers;
quietly keeping only the ones that fit would describe a document this tool understands far
better than it does.

The number worth the whole phase is in the Punjab Clean Air Action Plan's Lahore source
apportionment: **diesel 28 % + two-stroke exhaust 8 % = 36 %** of measured high PM2.5.
That is an `emission_fraction_removed` with a government citation behind it, which is a
better provenance than anything in `dsl/library.py`.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from terrarium.dsl.schema import Plan, PlantTrees, RestrictVehicles
from terrarium.policy.schema import PolicyMeasure

# A percentage in the measure's stated target. The *first* one, because a target reading
# "35% by 2030" has one figure and a year, and a target with two percentages in it is
# ambiguous enough that guessing is worse than declining.
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent)")

# Words that put a measure on the traffic lever or the canopy lever. Small and explicit,
# like `dsl/planner.py`'s vocabulary: this is a mapping between two known sets, not an
# attempt at policy NLP.
_TRAFFIC = re.compile(
    r"\b(vehicle|traffic|diesel|petrol|two[- ]stroke|rickshaw|bus|truck|fleet|"
    r"emission|exhaust|transport|congestion|low[- ]emission)\b",
    re.I,
)
_CANOPY = re.compile(
    r"\b(tree|trees|plantation|afforest|canopy|green\s*(?:cover|belt|space)|urban forest)\b",
    re.I,
)

# What "increase tree cover" means when a measure names no figure. A measure that commits
# to planting without a number is a real commitment, and refusing to express it at all
# would drop the entire urban-greening chapter of most of these documents.
#
# 0.15 is the "street trees" preset's fraction — a retrofit, not a park. It is stated in
# the plan's name so nobody quotes it as the document's own figure.
DEFAULT_CANOPY_FRACTION = 0.15


class MappedPlan(BaseModel):
    """A measure, the plan it became, and how much of it survived the mapping."""

    model_config = ConfigDict(frozen=True)

    measure: PolicyMeasure
    plan: Plan
    basis: str = Field(
        description=(
            "Which figure in the measure produced which lever, in one sentence, so the "
            "number on screen can be traced back to the document"
        )
    )
    assumed: bool = Field(
        default=False,
        description=(
            "True when the document named no figure and a default was used. The plan is "
            "still the document's *intent*; the magnitude is this project's assumption"
        ),
    )


def _fraction(target: str) -> float | None:
    match = _PERCENT.search(target)
    if match is None:
        return None
    # Clamped rather than rejected: a document saying "100 %" means all of it, and a
    # transcription error saying "350 %" is better capped than allowed to fail validation.
    return min(max(float(match.group(1)) / 100.0, 0.01), 1.0)


def expressible(measure: PolicyMeasure) -> bool:
    """Whether one of the two levers can say anything about this measure at all."""
    return to_plan(measure) is not None


def to_plan(measure: PolicyMeasure) -> MappedPlan | None:
    """Turn `measure` into a `Plan`, or `None` when neither lever can express it.

    `None` is the common answer and is not a failure. It is counted by
    `policy.schema.Coverage` and reported, because the gap between what a published plan
    commits to and what two levers can model is a fact about this tool.
    """
    text = f"{measure.title} {measure.target}"
    fraction = _fraction(measure.target)

    if _TRAFFIC.search(text):
        if fraction is None:
            # A traffic measure with no figure — "improve enforcement", "phase out old
            # rickshaws" — has no honest emission fraction. Unlike planting, there is no
            # defensible default: the whole range from 1 % to 100 % is plausible, and
            # picking one would be inventing the document's ambition.
            return None
        return MappedPlan(
            measure=measure,
            plan=Plan(
                name=_name(measure.title),
                actions=(RestrictVehicles(emission_fraction_removed=fraction),),
            ),
            basis=(
                f"'{measure.target}' read as removing {fraction:.0%} of this polygon's road "
                f"PM2.5. Source: {measure.document}"
                + (f", p. {measure.source_page}" if measure.source_page else "")
            ),
        )

    if _CANOPY.search(text):
        assumed = fraction is None
        canopy = fraction if fraction is not None else DEFAULT_CANOPY_FRACTION
        return MappedPlan(
            measure=measure,
            plan=Plan(
                name=_name(measure.title if not assumed else f"{measure.title} (assumed 15%)"),
                actions=(PlantTrees(canopy_fraction_added=canopy),),
            ),
            basis=(
                (
                    f"'{measure.target}' read as adding {canopy:.0%} canopy."
                    if not assumed
                    else f"The document commits to planting but names no figure, so "
                    f"{canopy:.0%} canopy — this project's street-tree default — stands "
                    "in. The intent is the document's; the magnitude is not."
                )
                + f" Source: {measure.document}"
                + (f", p. {measure.source_page}" if measure.source_page else "")
            ),
            assumed=assumed,
        )

    return None


def _name(title: str) -> str:
    """A `Plan.name` is capped at 80 characters and a policy title routinely is not."""
    return title if len(title) <= 80 else f"{title[:77]}..."
