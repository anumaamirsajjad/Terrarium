"""Turn a `Plan` into the two numbers a core takes, or refuse it with a reason.

This is where the DSL earns its place. A plan says "5,000 trees"; a core takes a canopy
fraction; between them sits a question only the tile can answer — *is there anywhere to
put them?* The answer is not a constant. It is measured on the drawn polygon, using the
thermal core's own canopy proxy, and handed to this module as `PolygonMeasurement`.

That measurement is an argument rather than something read here for the usual reason: this
module has no grid, no cube and no config, so it stays pure arithmetic and its refusals are
testable without a Zarr store. The layer that owns the grid does the measuring (D6, again).

**A refusal is a product feature, not an error path.** Silently trimming an impossible plan
and returning a small delta looks exactly like a plan that worked badly, and the user has
no way to tell the two apart.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from terrarium.dsl.schema import TREE_CANOPY_M2, Plan

M2_PER_KM2 = 1_000_000.0


class PlanError(ValueError):
    """The plan cannot be delivered on this polygon. Carries a user-facing reason."""


class PolygonMeasurement(BaseModel):
    """What the tile says about the polygon the plan is aimed at.

    Measured, never assumed. `plantable_canopy_m2` is the sum over masked cells of the
    canopy each cell can still take — the thermal core's own headroom term, so the DSL and
    the physics cannot disagree about how much room is left. Water and no-data cells
    contribute zero, which is why a polygon over the Ravi refuses a planting instead of
    quietly returning no cooling.
    """

    model_config = ConfigDict(frozen=True)

    cells: int = Field(gt=0)
    area_m2: float = Field(gt=0.0, description="Total area of the masked cells")
    plantable_canopy_m2: float = Field(
        ge=0.0,
        description=(
            "Canopy area still available inside the polygon, summed per cell from the "
            "core's NDVI-based headroom. Never larger than `area_m2`; usually far smaller."
        ),
    )

    @property
    def area_km2(self) -> float:
        return self.area_m2 / M2_PER_KM2

    @property
    def max_canopy_fraction(self) -> float:
        """The largest uniform canopy fraction this polygon could actually absorb."""
        return self.plantable_canopy_m2 / self.area_m2

    @property
    def max_trees(self) -> int:
        """How many trees fit, at one crown each. Rounded down: this is a ceiling."""
        return int(self.plantable_canopy_m2 // TREE_CANOPY_M2)


class ResolvedPlan(BaseModel):
    """A plan, checked against a polygon, in the units `/simulate` speaks.

    Carries both unit systems on purpose. `canopy_fraction_added` is what the core acts on;
    `tree_count` is what a person can actually picture. Publishing only one of them would
    leave every reader converting by hand with a crown area they would have to guess.
    """

    model_config = ConfigDict(frozen=True)

    plan: Plan
    cells: int
    area_km2: float
    # What `/simulate` should be sent. Zero means the plan says nothing about that lever —
    # not that the lever was tried and did nothing.
    canopy_fraction_added: float = Field(ge=0.0, le=1.0)
    emission_fraction_removed: float = Field(ge=0.0, le=1.0)
    tree_count: int = Field(ge=0)
    max_trees: int = Field(
        ge=0, description="What this polygon could hold at all, measured from the cube"
    )
    canopy_utilisation: float = Field(
        ge=0.0,
        description=(
            "Requested canopy over available canopy. Above 1.0 the core will cap it "
            "per cell and deliver less than asked — legal for a fraction, which is "
            "documented as a ceiling, and refused for a tree count, which is not."
        ),
    )
    notes: tuple[str, ...] = Field(
        default=(),
        description="Non-fatal warnings. A plan with notes still runs; read them anyway.",
    )
    basis: str = Field(
        description="How tree counts and canopy fractions were converted into each other"
    )


CONVERSION_BASIS = (
    f"One tree = {TREE_CANOPY_M2:.0f} m2 of crown at maturity (~5.6 m diameter, a street "
    "tree rather than a forest tree). A literature figure, not one measured on this tile: "
    "halve it and a polygon holds twice the trees."
)


def resolve(plan: Plan, measurement: PolygonMeasurement) -> ResolvedPlan:
    """Check `plan` against `measurement` and return what to send a core.

    Raises `PlanError` when the plan cannot be delivered as stated.
    """
    notes: list[str] = []
    canopy_fraction = 0.0
    tree_count = 0
    requested_canopy_m2 = 0.0

    planting = plan.planting
    if planting is not None:
        if measurement.plantable_canopy_m2 <= 0.0:
            raise PlanError(
                "nothing in this polygon can be planted — every cell is water, no-data, "
                "or already reads as closed canopy. Draw it over somewhere with room."
            )

        if planting.tree_count is not None:
            tree_count = planting.tree_count
            requested_canopy_m2 = tree_count * TREE_CANOPY_M2
            if requested_canopy_m2 > measurement.plantable_canopy_m2:
                # The refusal the roadmap asked for, stated with the arithmetic that
                # produced it so the user can argue with the crown area rather than with a
                # bare "no".
                raise PlanError(
                    f"{tree_count:,} trees need {requested_canopy_m2 / M2_PER_KM2:.3f} km2 "
                    f"of crown at {TREE_CANOPY_M2:.0f} m2 each, but this "
                    f"{measurement.area_km2:.3f} km2 polygon has only "
                    f"{measurement.plantable_canopy_m2 / M2_PER_KM2:.3f} km2 still "
                    f"plantable — room for about {measurement.max_trees:,}. Shrink the "
                    "planting or enlarge the polygon."
                )
            canopy_fraction = requested_canopy_m2 / measurement.area_m2
        else:
            # Guarded by the model validator: exactly one of the two is set.
            assert planting.canopy_fraction_added is not None
            canopy_fraction = planting.canopy_fraction_added
            requested_canopy_m2 = canopy_fraction * measurement.area_m2
            tree_count = round(requested_canopy_m2 / TREE_CANOPY_M2)
            if requested_canopy_m2 > measurement.plantable_canopy_m2:
                # Not an error, because a fraction is documented as a ceiling that the core
                # caps per cell (`Intervention.canopy_fraction_added`), and a count is not.
                # The note is what stops the capping being invisible.
                notes.append(
                    f"{canopy_fraction:.0%} canopy is more than this polygon can take: "
                    f"only {measurement.max_canopy_fraction:.0%} is still plantable, so "
                    "the core will cap most cells and deliver less. The result reports "
                    "what was actually added."
                )

    restriction = plan.restriction
    emission_fraction = restriction.emission_fraction_removed if restriction else 0.0

    if restriction is not None and planting is None:
        notes.append(
            "This plan touches traffic only, so it returns no temperature change. The "
            "thermal emulator has no traffic term and was never trained on one."
        )
    if planting is not None and restriction is None:
        notes.append(
            "Planting moves air quality by ~0.0003 µg/m3 at this scale, so no air result "
            "is returned. Trees are a thermal instrument here, not an air one."
        )

    return ResolvedPlan(
        plan=plan,
        cells=measurement.cells,
        area_km2=measurement.area_km2,
        canopy_fraction_added=min(canopy_fraction, 1.0),
        emission_fraction_removed=emission_fraction,
        tree_count=tree_count,
        max_trees=measurement.max_trees,
        canopy_utilisation=(
            requested_canopy_m2 / measurement.plantable_canopy_m2
            if measurement.plantable_canopy_m2 > 0
            else 0.0
        ),
        notes=tuple(notes),
        basis=CONVERSION_BASIS,
    )
