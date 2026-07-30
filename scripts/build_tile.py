"""Build the Lahore State Cube end to end.

    uv run python scripts/build_tile.py

Reports scene counts per collection, the cloud filter's effect, the final grid shape,
and which variables came back populated versus empty. Exits non-zero if any declared
variable is unpopulated, so a silently half-built cube fails loudly.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from pathlib import Path

from terrarium.config import Settings, get_settings
from terrarium.ingest.client import open_catalog
from terrarium.ingest.pipeline import build_cube
from terrarium.state.cube import CubeSummary, Dims, select_window, summarise
from terrarium.state.grid import Grid, grid_for_tile
from terrarium.state.store import (
    BuildRecord,
    SourceRecord,
    connect_catalog,
    record_build,
    write_cube,
)

logger = logging.getLogger("build_tile")


def _print_header(settings: Settings, grid: Grid) -> None:
    tile = settings.tile
    print(f"\n{'=' * 72}")
    print(f"  Terrarium tile build - {tile.name}, {tile.country}")
    print(f"{'=' * 72}")
    print(f"  bbox (WGS84)   {tile.bbox}")
    print(f"  target CRS     {tile.crs}")
    print(f"  resolution     {tile.target_resolution_m} m")
    print(f"  grid shape     {grid.shape[0]} x {grid.shape[1]}  (y, x)")
    print(f"  bounds (UTM)   {tuple(round(b) for b in grid.bounds)}")
    print(f"  cloud ceiling  {settings.max_cloud_cover}%")
    print(f"  max scenes     {settings.max_scenes_per_collection} per collection, per window")
    print(f"  windows        {len(settings.windows)}")
    for window in settings.windows:
        print(f"    {window.label:<16} {window.start} .. {window.end}")
    print()


def _print_sources(records: list[SourceRecord]) -> None:
    print(
        f"  {'window':<14} {'collection':<24} {'found':>7} {'passed cloud':>13} "
        f"{'composited':>11} {'cloud <=':>10}"
    )
    print(f"  {'-' * 14} {'-' * 24} {'-' * 7} {'-' * 13} {'-' * 11} {'-' * 10}")
    for record in records:
        ceiling = "-" if record.max_cloud_cover is None else f"{record.max_cloud_cover:g}%"
        # Static sources carry no window; they are ingested once for the whole cube.
        window = record.window or "(static)"
        print(
            f"  {window:<14} {record.collection_id:<24} {record.n_found:>7} "
            f"{record.n_kept:>13} {record.n_composited:>11} {ceiling:>10}"
        )
    print()


def _print_summary(summary: CubeSummary) -> None:
    print(f"  {'variable':<22} {'units':<8} {'dims':<11} {'valid':>7} "
          f"{'min':>10} {'mean':>10} {'max':>10}")
    print(f"  {'-' * 22} {'-' * 8} {'-' * 11} {'-' * 7} {'-' * 10} {'-' * 10} {'-' * 10}")
    for var in summary.variables:
        axes = ",".join(var.dims.axes)
        if var.populated:
            print(
                f"  {var.name:<22} {var.units:<8} {axes:<11} "
                f"{var.valid_fraction:>6.1%} {var.vmin:>10.3f} {var.vmean:>10.3f} "
                f"{var.vmax:>10.3f}"
            )
        else:
            print(f"  {var.name:<22} {var.units:<8} {axes:<11} {'MISSING':>7}")
    print()


def _print_per_window(cube, grid: Grid, summary: CubeSummary) -> list[str]:
    """Per-window validity for the time-varying variables. Returns any gaps found.

    The whole-cube summary above reports a variable as populated if *any* window carried
    data, which would hide one cloudy season entirely. This is the check that does not.
    """
    temporal = [v.name for v in summary.variables if v.dims is not Dims.SPACE]
    print(f"  {'window':<16} " + " ".join(f"{name:>12}" for name in temporal))
    print(f"  {'-' * 16} " + " ".join("-" * 12 for _ in temporal))

    gaps: list[str] = []
    for label in summary.windows:
        slice_summary = summarise(select_window(cube, label), grid)
        by_name = {v.name: v for v in slice_summary.variables}
        cells = []
        for name in temporal:
            var = by_name[name]
            if not var.populated:
                gaps.append(f"{label}/{name}")
                cells.append(f"{'MISSING':>12}")
            elif var.dims is Dims.TIME:
                cells.append(f"{var.vmean:>12.2f}")
            else:
                cells.append(f"{var.valid_fraction:>11.1%} ")
        print(f"  {label:<16} " + " ".join(cells))
    print("\n  (maps show valid-pixel share, scalars show the value)\n")
    return gaps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="Zarr path override")
    parser.add_argument(
        "--max-scenes",
        type=int,
        default=None,
        help="Scenes composited per collection per window, least-cloudy first "
             "(default: from config)",
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=None,
        help="Years to build seasonal windows for; each adds a summer and a winter "
             "(default: from config). One year makes for a much faster smoke test.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # These are chatty at INFO and drown the report.
    for noisy in ("urllib3", "botocore", "rasterio", "fsspec", "aiobotocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    settings = get_settings()
    overrides = {}
    if args.max_scenes is not None:
        overrides["max_scenes_per_collection"] = args.max_scenes
    if args.years is not None:
        overrides["window_years"] = args.years
    if overrides:
        settings = settings.model_copy(update=overrides)
    grid = grid_for_tile(settings.tile)
    zarr_path = args.out or settings.zarr_store

    _print_header(settings, grid)

    started = time.perf_counter()
    catalog = open_catalog(settings)

    print("  searching + loading ...\n")
    cube, records = build_cube(catalog, settings, grid)
    elapsed = time.perf_counter() - started

    summary = summarise(cube, grid)

    print(f"{'-' * 72}\n  SCENES\n{'-' * 72}")
    _print_sources(records)
    print(f"{'-' * 72}\n  CUBE (all windows)\n{'-' * 72}")
    _print_summary(summary)
    print(f"{'-' * 72}\n  PER WINDOW\n{'-' * 72}")
    gaps = _print_per_window(cube, grid, summary)

    write_cube(cube, zarr_path, grid=grid)
    build_id = uuid.uuid4().hex[:12]
    with connect_catalog(settings.duckdb_path) as conn:
        record_build(
            conn,
            BuildRecord(
                build_id=build_id,
                tile_name=settings.tile.name,
                zarr_path=zarr_path,
                summary=summary,
                sources=records,
                duration_s=elapsed,
            ),
        )

    print(f"{'-' * 72}")
    print(f"  build {build_id} finished in {elapsed:.1f}s")
    print(f"  zarr      {zarr_path}")
    print(f"  catalog   {settings.duckdb_path}")
    print(f"  windows   {len(summary.windows)}: {', '.join(summary.windows)}")
    print(f"  populated {len(summary.populated)}/{len(summary.variables)}: "
          f"{', '.join(summary.populated) or 'none'}")
    if summary.missing:
        print(f"  MISSING   {', '.join(summary.missing)}")
    if gaps:
        print(f"  GAPS      {', '.join(gaps)}")
    print(f"{'-' * 72}\n")

    return 1 if summary.missing or gaps else 0


if __name__ == "__main__":
    sys.exit(main())
