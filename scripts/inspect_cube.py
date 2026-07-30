"""Print what is actually inside a built State Cube.

    uv run python scripts/inspect_cube.py

Read-only. Reopens the Zarr store from disk rather than trusting whatever the build
process had in memory, so this is the honest view of what was persisted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from terrarium.config import get_settings
from terrarium.state.cube import (
    CUBE_VARIABLES,
    Dims,
    select_window,
    summarise,
    window_labels,
)
from terrarium.state.grid import grid_for_tile
from terrarium.state.store import connect_catalog, open_cube

TREE_COVER_CLASS = 10
BUILT_UP_CLASS = 50

# WorldCover class codes, for reading the categorical variable back as words.
WORLDCOVER_LEGEND = {
    10: "tree cover",
    20: "shrubland",
    30: "grassland",
    40: "cropland",
    50: "built-up",
    60: "bare / sparse",
    70: "snow and ice",
    80: "permanent water",
    90: "herbaceous wetland",
    95: "mangroves",
    100: "moss and lichen",
}


def _print_variables(ds, summary, label: str) -> None:
    """Per-variable value ranges for one cube (whole, or a single window's slice)."""
    print(f"  {label}")
    print(f"  {'variable':<22} {'units':<8} {'dtype':<8} {'valid':>7} "
          f"{'min':>10} {'p50':>10} {'mean':>10} {'max':>10}")
    print(f"  {'-' * 22} {'-' * 8} {'-' * 8} {'-' * 7} "
          f"{'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")

    for spec in CUBE_VARIABLES:
        var = next(v for v in summary.variables if v.name == spec.name)
        if not var.populated:
            print(f"  {spec.name:<22} {var.units:<8} {var.dtype:<8} {'MISSING':>7}")
            continue
        if spec.is_categorical:
            # The min/mean/max of class codes is arithmetic on labels and means nothing -
            # the mean of {tree cover, built-up} is not a land cover. See the composition
            # breakdown below instead.
            print(
                f"  {spec.name:<22} {var.units:<8} {var.dtype:<8} {var.valid_fraction:>6.1%} "
                f"{'(categorical - see composition below)':>43}"
            )
            continue
        values = np.asarray(ds[spec.name].values)
        median = float(np.median(values[np.isfinite(values)].astype("float64")))
        print(
            f"  {spec.name:<22} {var.units:<8} {var.dtype:<8} {var.valid_fraction:>6.1%} "
            f"{var.vmin:>10.3f} {median:>10.3f} {var.vmean:>10.3f} {var.vmax:>10.3f}"
        )
    print()


def _print_contrast(ds, labels: list[str]) -> None:
    """Observed tree-vs-built LST contrast, per window.

    This is the number that sets the ceiling on any tree-planting scenario: converting a
    cell entirely to tree cover cannot buy more cooling than the difference the tile
    actually shows. Phase 2 measured 2.60 degC on a single Apr-Jun composite and the plan
    asks whether per-window composites raise it. Reported here rather than assumed,
    because a literature value silently becomes wrong when the season changes - and if it
    stays this low, that belongs in the pitch instead of a borrowed 3-6 degC.
    """
    landcover = np.asarray(ds["landcover"].values)
    trees = landcover == TREE_COVER_CLASS
    built = landcover == BUILT_UP_CLASS
    if not (trees.any() and built.any()):
        print("  tree-vs-built contrast: not computable (a class is absent)\n")
        return

    print("  observed tree-vs-built LST contrast (the ceiling on any planting scenario)")
    print(f"    {'window':<16} {'tree p50':>10} {'built p50':>10} {'contrast':>10}")
    print(f"    {'-' * 16} {'-' * 10} {'-' * 10} {'-' * 10}")
    for label in labels:
        lst = np.asarray(select_window(ds, label)["lst_c"].values)
        tree_median = float(np.nanmedian(lst[trees])) if np.isfinite(lst[trees]).any() else np.nan
        built_median = float(np.nanmedian(lst[built])) if np.isfinite(lst[built]).any() else np.nan
        contrast = built_median - tree_median
        if not np.isfinite(contrast):
            print(f"    {label:<16} {'no LST for this window':>32}")
            continue
        print(f"    {label:<16} {tree_median:>10.2f} {built_median:>10.2f} {contrast:>10.2f}")
    print("\n    Phase 2's single Apr-Jun composite gave 2.60 degC. A higher number here\n"
          "    means per-window compositing recovered contrast that the wider composite\n"
          "    averaged away; a similar one means it did not, and the pitch should say so.\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zarr", type=Path, default=None)
    parser.add_argument(
        "--per-window",
        action="store_true",
        help="also print full value ranges for every window separately",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    grid = grid_for_tile(settings.tile)
    path = args.zarr or settings.zarr_store

    if not path.exists():
        print(f"no cube at {path} - run scripts/build_tile.py first")
        return 1

    ds = open_cube(path)
    summary = summarise(ds, grid)
    labels = window_labels(ds)

    print(f"\n{'=' * 78}")
    print(f"  State Cube  {path}")
    print(f"{'=' * 78}")
    print(f"  CRS          {summary.crs}")
    print(f"  resolution   {summary.resolution_m} m")
    print(f"  shape        {summary.shape[0]} x {summary.shape[1]} (y, x) "
          f"= {summary.shape[0] * summary.shape[1]:,} pixels")
    print(f"  bounds       {ds.attrs.get('bounds')}")
    print(f"  dims         {dict(ds.sizes)}")
    print(f"  windows      {len(labels)}: {', '.join(labels)}")
    print(f"  variables    {len(ds.data_vars)}\n")

    _print_variables(ds, summary, "all windows pooled")

    if args.per_window:
        for label in labels:
            sliced = select_window(ds, label)
            _print_variables(sliced, summarise(sliced, grid), f"window {label}")
    else:
        # The one per-window number that always matters: how much of each map survived
        # the cloud masking in that season. Winter composites are the ones at risk.
        print(f"  valid-pixel share per window ({'--per-window for full ranges'})")
        temporal = [s.name for s in CUBE_VARIABLES if s.dims is Dims.TIME_SPACE]
        print(f"    {'window':<16} " + " ".join(f"{n:>10}" for n in temporal))
        for label in labels:
            sliced = select_window(ds, label)
            by_name = {v.name: v for v in summarise(sliced, grid).variables}
            cells = " ".join(f"{by_name[n].valid_fraction:>10.1%}" for n in temporal)
            print(f"    {label:<16} " + cells)
        print()

    if "lst_c" in ds.data_vars and "landcover" in ds.data_vars:
        _print_contrast(ds, labels)

    if "landcover" in ds.data_vars:
        codes, counts = np.unique(np.asarray(ds["landcover"].values), return_counts=True)
        total = int(counts.sum())
        print(f"  landcover composition ({total:,} px)")
        order = np.argsort(counts)[::-1]
        for i in order:
            code = int(codes[i])
            label = "NO DATA" if code == 0 else WORLDCOVER_LEGEND.get(code, f"unknown ({code})")
            print(f"    {code:>4}  {label:<20} {counts[i] / total:>6.1%}  ({counts[i]:,} px)")
        print()

    if "population" in ds.data_vars:
        people = np.asarray(ds["population"].values)
        finite = people[np.isfinite(people)]
        # The tile total is the number to sanity-check against: this bbox covers most of
        # Lahore proper, whose metro population is on the order of 11-13 million.
        print(f"  population    {finite.sum():,.0f} residents on the tile "
              f"({(finite > 0).mean():.1%} of cells inhabited, "
              f"max {finite.max():,.0f} per 1 ha cell)\n")

    if settings.duckdb_path.exists():
        with connect_catalog(settings.duckdb_path) as conn:
            # Scope to the single most recent build; joining without this mixes rows
            # from every historical build under one heading.
            rows = conn.execute(
                "SELECT b.build_id, b.built_at, b.duration_s, s.window_label, "
                "s.collection_id, s.n_found, s.n_kept, s.n_composited "
                "FROM builds b JOIN build_sources s USING (build_id) "
                "WHERE b.built_at = (SELECT max(built_at) FROM builds) "
                "ORDER BY s.window_label NULLS FIRST, s.collection_id"
            ).fetchall()
        if rows:
            print("  latest catalogued build")
            # duration_s is nullable in the schema, so it cannot be format-specced blind.
            duration = "unknown" if rows[0][2] is None else f"{rows[0][2]:.1f}s"
            print(f"    {rows[0][0]}  at {rows[0][1]}  ({duration})")
            for _, _, _, window, coll, found, kept, comp in rows:
                label = window or "(static)"
                print(f"    {label:<14} {coll:<24} found={found:<4} "
                      f"cloud-ok={kept:<4} composited={comp}")
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
