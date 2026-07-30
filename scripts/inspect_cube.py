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
from terrarium.state.cube import CUBE_VARIABLES, summarise
from terrarium.state.grid import grid_for_tile
from terrarium.state.store import connect_catalog, open_cube

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zarr", type=Path, default=None)
    args = parser.parse_args(argv)

    settings = get_settings()
    grid = grid_for_tile(settings.tile)
    path = args.zarr or settings.zarr_store

    if not path.exists():
        print(f"no cube at {path} - run scripts/build_tile.py first")
        return 1

    ds = open_cube(path)
    summary = summarise(ds, grid)

    print(f"\n{'=' * 78}")
    print(f"  State Cube  {path}")
    print(f"{'=' * 78}")
    print(f"  CRS          {summary.crs}")
    print(f"  resolution   {summary.resolution_m} m")
    print(f"  shape        {summary.shape[0]} x {summary.shape[1]} (y, x) "
          f"= {summary.shape[0] * summary.shape[1]:,} pixels")
    print(f"  bounds       {ds.attrs.get('bounds')}")
    print(f"  dims         {dict(ds.sizes)}")
    print(f"  variables    {len(ds.data_vars)}\n")

    print(f"  {'variable':<14} {'units':<6} {'dtype':<8} {'valid':>7} "
          f"{'min':>10} {'p50':>10} {'mean':>10} {'max':>10}")
    print(f"  {'-' * 14} {'-' * 6} {'-' * 8} {'-' * 7} "
          f"{'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")

    for spec in CUBE_VARIABLES:
        var = next(v for v in summary.variables if v.name == spec.name)
        if not var.populated:
            print(f"  {spec.name:<14} {var.units:<6} {var.dtype:<8} {'MISSING':>7}")
            continue
        if spec.is_categorical:
            # The min/mean/max of class codes is arithmetic on labels and means nothing -
            # the mean of {tree cover, built-up} is not a land cover. See the composition
            # breakdown below instead.
            print(
                f"  {spec.name:<14} {var.units:<6} {var.dtype:<8} {var.valid_fraction:>6.1%} "
                f"{'(categorical - see composition below)':>43}"
            )
            continue
        values = np.asarray(ds[spec.name].values)
        median = float(np.median(values[np.isfinite(values)].astype("float64")))
        print(
            f"  {spec.name:<14} {var.units:<6} {var.dtype:<8} {var.valid_fraction:>6.1%} "
            f"{var.vmin:>10.3f} {median:>10.3f} {var.vmean:>10.3f} {var.vmax:>10.3f}"
        )
    print()

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

    if settings.duckdb_path.exists():
        with connect_catalog(settings.duckdb_path) as conn:
            # Scope to the single most recent build; joining without this mixes rows
            # from every historical build under one heading.
            rows = conn.execute(
                "SELECT b.build_id, b.built_at, b.duration_s, s.collection_id, "
                "s.n_found, s.n_kept, s.n_composited "
                "FROM builds b JOIN build_sources s USING (build_id) "
                "WHERE b.built_at = (SELECT max(built_at) FROM builds) "
                "ORDER BY s.collection_id"
            ).fetchall()
        if rows:
            print("  latest catalogued build")
            # duration_s is nullable in the schema, so it cannot be format-specced blind.
            duration = "unknown" if rows[0][2] is None else f"{rows[0][2]:.1f}s"
            print(f"    {rows[0][0]}  at {rows[0][1]}  ({duration})")
            for _, _, _, coll, found, kept, comp in rows:
                print(f"    {coll:<24} found={found:<4} cloud-ok={kept:<4} composited={comp}")
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
