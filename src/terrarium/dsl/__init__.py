"""The intervention DSL and the agent layer around it.

Layer 3, alongside `api/`. The ordering that matters: `schema` defines what a plan *is*,
`validate` decides whether one can be delivered on a given polygon, `library` prices it and
offers presets, `planner` produces plans from text, and `explain` turns a core's output back
into sentences. The language model appears in exactly one of those five files, which is the
point — the DSL is what makes the agent layer safe to hand a free-tier model.
"""

from terrarium.dsl.explain import Brief, BriefInputs, brief_for
from terrarium.dsl.library import PRESETS, CostEstimate, Preset, estimate_cost, preset
from terrarium.dsl.observe import (
    Observation,
    ObservationCategory,
    ObservationError,
    observation_from_photo,
)
from terrarium.dsl.planner import ParsedPlan, PlanParseError, parse_rules, plan_from_text
from terrarium.dsl.schema import Plan, PlantTrees, RestrictVehicles
from terrarium.dsl.validate import PlanError, PolygonMeasurement, ResolvedPlan, resolve

__all__ = [
    "PRESETS",
    "Brief",
    "BriefInputs",
    "CostEstimate",
    "Observation",
    "ObservationCategory",
    "ObservationError",
    "ParsedPlan",
    "Plan",
    "PlanError",
    "PlanParseError",
    "PlantTrees",
    "PolygonMeasurement",
    "Preset",
    "ResolvedPlan",
    "RestrictVehicles",
    "brief_for",
    "estimate_cost",
    "observation_from_photo",
    "parse_rules",
    "plan_from_text",
    "preset",
    "resolve",
]
