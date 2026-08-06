"""The intervention DSL: what a user asked for, before anything measures whether it fits.

A `Plan` is deliberately *not* an `Intervention`. `cores.base.Intervention` is what a core
can act on — a boolean mask and two fractions of a cell. A `Plan` is what a person says:
a name, a season, and a list of actions in human units ("5,000 trees", "ban combustion
vehicles"). Turning one into the other needs the tile, so it happens in `dsl.validate`
against numbers measured on the polygon, not here.

Geometry is absent for the same reason it is absent from a core (D6): a plan says *what*
to do, and the API already owns the one conversion from GeoJSON to the grid. Keeping the
polygon out means a plan is reusable — the same "street trees" plan applies to any polygon
the user draws, which is exactly what the preset library in `dsl.library` depends on.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from terrarium.config import Season

# Crown area of one established urban street tree, m². This is the exchange rate between
# the unit a person uses ("5,000 trees") and the unit the thermal core uses (a canopy
# fraction of cell area), and it is the single most load-bearing constant in the DSL: it
# is what makes a tree count checkable at all.
#
# 25 m² is a ~5.6 m crown diameter — a *street* tree at maturity, not a forest tree and
# not a sapling. It is a literature figure, not one measured on this tile, so it is stated
# in every response that depends on it (`dsl.validate.ResolvedPlan.basis`) rather than
# buried here. Halving it halves the trees a polygon can hold, so it is worth arguing
# about, which is why it is one named constant and not five inline multiplications.
TREE_CANOPY_M2 = 25.0

# One analysis cell, m². The grid is 100 m (`Tile.target_resolution_m`) and this module is
# not allowed to import it — see the note in `dsl.validate.plantable_area_m2`, where area
# arrives as an argument from the layer that does know the grid.


class PlantTrees(BaseModel):
    """Add canopy inside the polygon, expressed either way round.

    A person says "5,000 trees"; the core needs "+0.15 canopy fraction". Both are accepted
    and exactly one must be given, because supplying both invites them to disagree and
    there is no honest rule for which wins.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["plant_trees"] = "plant_trees"

    tree_count: int | None = Field(
        default=None,
        ge=1,
        description=(
            f"Number of trees to plant. Converted at {TREE_CANOPY_M2:.0f} m2 of crown "
            "each, and checked against the canopy the polygon can actually still hold."
        ),
    )
    canopy_fraction_added: float | None = Field(
        default=None,
        gt=0.0,
        le=1.0,
        description=(
            "Canopy cover to add as a fraction of cell area, if the plan is expressed "
            "that way. A ceiling: the thermal core caps each cell at its own headroom."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> PlantTrees:
        given = [self.tree_count is not None, self.canopy_fraction_added is not None]
        if sum(given) != 1:
            raise ValueError(
                "give either tree_count or canopy_fraction_added, not both and not "
                "neither — they are two ways of saying the same thing and there is no "
                "rule for reconciling them when they disagree"
            )
        return self


class RestrictVehicles(BaseModel):
    """Remove some share of the polygon's road PM2.5 — a low-emission zone.

    Read only by the air core (D14). It says nothing about surface temperature and the
    thermal emulator is never shown it, because no traffic term was ever in its training
    data and inventing one would be inventing a physical link.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["restrict_vehicles"] = "restrict_vehicles"

    emission_fraction_removed: float = Field(
        gt=0.0,
        le=1.0,
        description=(
            "Share of road and kiln PM2.5 removed inside the polygon. 1.0 means the "
            "vehicles are gone, not electrified: brake, tyre and road wear are roughly "
            "half of road PM2.5 and do not fall when the engine changes."
        ),
    )


Action = Annotated[PlantTrees | RestrictVehicles, Field(discriminator="kind")]


class Plan(BaseModel):
    """One named intervention, in the terms the person asking for it used.

    This is the contract the agent layer produces and the API consumes. An LLM emits it,
    a preset button emits it, and a rule-based parser emits it — all three land in the
    same validator, which is the point of having a DSL at all: the language model is a
    front door, and the door is not where correctness lives.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(
        min_length=1,
        max_length=80,
        description="Short human label. Shown on the result and in the brief.",
    )
    actions: tuple[Action, ...] = Field(
        min_length=1,
        description="At least one. Two actions of the same kind is a contradiction, not a sum.",
    )
    window: str | None = Field(
        default=None,
        description=(
            "Exact window label, e.g. '2024-winter'. Null means unspecified, which the "
            "API resolves to its default — the latest *summer*."
        ),
    )
    season: Season | None = Field(
        default=None,
        description=(
            "Season without a year, which is what people actually say ('in winter'). "
            "Weaker than `window` and ignored when `window` is given. Kept as its own "
            "field rather than guessed into a label because the guess belongs to the "
            "layer that knows which windows the cube actually has."
        ),
    )

    @model_validator(mode="after")
    def _one_action_per_kind(self) -> Plan:
        kinds = [action.kind for action in self.actions]
        duplicated = {kind for kind in kinds if kinds.count(kind) > 1}
        if duplicated:
            raise ValueError(
                f"plan repeats {sorted(duplicated)}. Two plantings in one polygon is not "
                "twice the trees — it is two numbers with no rule for combining them. "
                "Say it once."
            )
        return self

    @property
    def planting(self) -> PlantTrees | None:
        for action in self.actions:
            if isinstance(action, PlantTrees):
                return action
        return None

    @property
    def restriction(self) -> RestrictVehicles | None:
        for action in self.actions:
            if isinstance(action, RestrictVehicles):
                return action
        return None
