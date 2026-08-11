"""Segment a delta field into regions, and say what each region actually contained.

Deterministic. No model in this file — the model's part is `dsl.llm.describe_pattern`,
which reads the table this produces and nothing else.

**This is the one gap in the product a model genuinely fills.** Templates can state *how
much* cooling landed, because that is one number and `explain.py` already writes it. They
cannot say *where and why*, because the pattern differs every run: which blocks had
headroom, which were already wooded, which were over housing and which over the Ravi. A
table can carry that; a template sentence cannot enumerate it.

The join reuses what already exists rather than recomputing it:

- **The lattice** from `api/candidates.py` is the segmentation. A second segmentation
  scheme would mean the agent's regions and the explanation's regions were different
  regions with the same kind of name, which is the sort of thing nobody notices until two
  numbers disagree on screen.
- **`cores.equity.benefit_distribution`** supplies the population decile cut, so the
  explanation and the equity panel are stratifying identically.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import xarray as xr
from pydantic import BaseModel, ConfigDict, Field
from scipy.ndimage import binary_dilation

from terrarium.agent.state import Candidate
from terrarium.api.candidates import build_lattice
from terrarium.cores.equity import benefit_distribution
from terrarium.cores.thermal.simulate import (
    TREE_COVER_CLASS,
    WATER_CLASS,
)
from terrarium.dsl.explain import HINDCAST_OVERPREDICTION
from terrarium.state.grid import Grid

# Regions reported. The lattice is 121 blocks and most of them are untouched by any one
# intervention; a table of 121 rows is one no model reads carefully and no person reads at
# all. Eight is enough to carry a contrast — the best few and the worst few.
MAX_REGIONS = 8

# Below this a region's mean change is indistinguishable from the model's own noise, and
# describing it as "slight cooling" would be describing rounding.
NEGLIGIBLE_C = 0.005


class RegionExplanation(BaseModel):
    """One block of the lattice, and what the cube says was in it.

    Every field is measured. The model that reads these rows may reorganise them into
    prose and may not add to them, which is what `_numbers_are_faithful` enforces on its
    output.
    """

    model_config = ConfigDict(frozen=True)

    region_id: str
    # Hindcast-corrected and stated positive, matching every other cooling figure the
    # product publishes. A description quoting the raw delta would be the one place the
    # correction was skipped.
    expected_cooling_c: float
    canopy_added: float = Field(
        ge=0.0, description="Mean canopy fraction actually added, after the per-cell cap"
    )
    headroom_km2: float = Field(
        ge=0.0, description="Canopy this block could still take before the intervention"
    )
    residents: float = Field(ge=0.0)
    population_decile: int | None = Field(
        default=None,
        description="1 = least densely populated tenth of the tile's residents, 10 = most",
    )
    tree_cover_fraction: float = Field(ge=0.0, le=1.0)
    water_fraction: float = Field(ge=0.0, le=1.0)
    inside_polygon: bool = Field(
        description="Whether the drawn intervention overlapped this block at all"
    )
    spillover: bool = Field(
        description=(
            "Changed without being drawn on. Real physics — the 500 m neighbourhood terms "
            "carry cooling past the polygon's edge — and the one part of the pattern a "
            "reader is most likely to think is a bug"
        )
    )


class SpatialExplanation(BaseModel):
    """The whole pattern: the regions, and optionally a model's description of them."""

    model_config = ConfigDict(frozen=True)

    window: str
    regions: tuple[RegionExplanation, ...]
    summary: str | None = Field(
        default=None, description="A model's description, or null when none was reachable"
    )
    points: tuple[str, ...] = ()
    source: str = Field(
        default="table",
        description=(
            "'table' when no model wrote the prose — which is a working deployment, not a "
            "degraded one, because the regions are the answer and the prose is a reading "
            "of it"
        ),
    )


def _decile_map(delta: np.ndarray, population: np.ndarray) -> np.ndarray | None:
    """Per-cell population decile, from the equity core's own cut.

    Recomputed here rather than shared through `BenefitDistribution` because that model
    returns aggregates, not the per-cell assignment — but it is the *same* cut, taken from
    the same sorted cumulative population, so the two cannot disagree about which decile a
    cell is in.
    """
    try:
        distribution = benefit_distribution(delta, population)
    except ValueError:
        return None

    people = np.asarray(population, dtype="float64").reshape(-1)
    usable = np.isfinite(np.asarray(delta, dtype="float64").reshape(-1)) & (people > 0)
    if not usable.any():
        return None

    index = np.nonzero(usable)[0]
    order = index[np.argsort(people[index], kind="stable")]
    cumulative = np.cumsum(people[order])
    total = float(cumulative[-1])
    if total <= 0:
        return None

    groups = len(distribution.deciles)
    midpoint = cumulative - people[order] / 2.0
    assigned = np.minimum((midpoint / total * groups).astype(int), groups - 1) + 1

    deciles = np.zeros(people.shape, dtype="int16")
    deciles[order] = assigned
    return deciles.reshape(np.asarray(delta).shape)


def _block(values: np.ndarray, region: Candidate) -> np.ndarray:
    return np.asarray(values)[region.row0 : region.row1, region.col0 : region.col1]


def explain_pattern(
    *,
    window: xr.Dataset,
    label: str,
    grid: Grid,
    delta: np.ndarray,
    canopy_added: np.ndarray,
    mask: np.ndarray,
    max_regions: int = MAX_REGIONS,
) -> tuple[RegionExplanation, ...]:
    """Segment `delta` onto the lattice and join the cube's attributes.

    Returns the regions that actually changed, strongest cooling first, capped at
    `max_regions`. A region with no measurable change is dropped rather than reported as
    zero: an intervention touches a handful of blocks and listing the other hundred as
    "no change" buries the answer in its own negative space.
    """
    regions = build_lattice(window, grid)

    population = (
        np.nan_to_num(np.asarray(window["population"].values, dtype="float64"))
        if "population" in window
        else np.zeros(grid.shape)
    )
    deciles = _decile_map(delta, population)
    landcover = np.asarray(window["landcover"].values)
    # The ring the neighbourhood features reach into, so "spillover" is the physics term
    # rather than "anything that changed outside the polygon", which would include noise.
    near = binary_dilation(mask, iterations=3)

    explained: list[RegionExplanation] = []
    for region in regions:
        block_delta = _block(delta, region)
        finite = np.isfinite(block_delta)
        if not finite.any():
            continue

        mean_delta = float(block_delta[finite].mean())
        expected = max(-mean_delta, 0.0) / HINDCAST_OVERPREDICTION
        if expected < NEGLIGIBLE_C:
            continue

        block_mask = _block(mask, region)
        block_cover = _block(landcover, region)
        block_decile = _block(deciles, region) if deciles is not None else None
        inhabited = block_decile[block_decile > 0] if block_decile is not None else None

        explained.append(
            RegionExplanation(
                region_id=region.region_id,
                expected_cooling_c=expected,
                canopy_added=float(np.nan_to_num(_block(canopy_added, region)).mean()),
                headroom_km2=region.plantable_canopy_m2 / 1e6,
                residents=region.population,
                population_decile=(
                    int(np.median(inhabited)) if inhabited is not None and inhabited.size else None
                ),
                tree_cover_fraction=float((block_cover == TREE_COVER_CLASS).mean()),
                water_fraction=float((block_cover == WATER_CLASS).mean()),
                inside_polygon=bool(block_mask.any()),
                # Outside the drawn polygon but inside the feature neighbourhood.
                spillover=bool(not block_mask.any() and _block(near, region).any()),
            )
        )

    explained.sort(key=lambda entry: -entry.expected_cooling_c)
    return tuple(explained[:max_regions])


def as_table(regions: Sequence[RegionExplanation], *, label: str) -> str:
    """The regions as the text a model is handed. **This is the model's entire input.**

    Written as a table rather than as JSON because it is read as prose and a model that has
    to describe a pattern reads a table better than a nested object. Every number in the
    description must appear here — `_numbers_are_faithful` compares against exactly this
    string, so a column dropped from here is a figure the model may no longer use.
    """
    header = (
        f"Window: {label}. Cooling is degC, already corrected for the model's known "
        "2.5x over-prediction.\n\n"
        "region | cooling degC | canopy added | room to plant km2 | residents | "
        "density decile | tree cover | water | drawn on | spillover\n"
    )
    rows = "\n".join(
        f"{entry.region_id} | {entry.expected_cooling_c:.2f} | {entry.canopy_added:.0%} | "
        f"{entry.headroom_km2:.2f} | {entry.residents:,.0f} | "
        f"{entry.population_decile if entry.population_decile is not None else 'n/a'} | "
        f"{entry.tree_cover_fraction:.0%} | {entry.water_fraction:.0%} | "
        f"{'yes' if entry.inside_polygon else 'no'} | "
        f"{'yes' if entry.spillover else 'no'}"
        for entry in regions
    )
    return header + rows
