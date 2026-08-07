"""Add the Phase 9 air layers to a cube that was built before they existed.

    uv run python scripts/build_air_layers.py --zarr data/processed/cube_phase4.zarr

Two variables: `pm25_emission_g_s` from Overpass, and `wind_direction_deg` from
Open-Meteo. Both are cheap - one Overpass query and one reanalysis call per window,
seconds rather than the ~70 s per window a satellite rebuild costs - which is the whole
reason this exists rather than "just rebuild the tile". The known-good cube stays the
known-good cube, and Planetary Computer never gets the chance to drop a connection the
night before a demo.

`build_tile.py` already ingests both for any *new* build. This is the migration path for
the ones already on disk.

Writes in place by default. That is safe here and nowhere else in this repo: the cube is
~1 MB, so it is read fully into memory before the store is touched - unlike a build, which
streams from the network and can die halfway.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import xarray as xr

from terrarium.config import SeasonWindow, get_settings, season_windows
from terrarium.ingest.osm import emission_grid, fetch_overpass
from terrarium.ingest.pipeline import _ingest_meteorology
from terrarium.state.cube import VARIABLES_BY_NAME, window_labels
from terrarium.state.grid import grid_for_tile
from terrarium.state.store import open_cube, write_cube

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("build_air_layers")

EMISSIONS = "pm25_emission_g_s"
DIRECTION = "wind_direction_deg"


def _window_for(label: str) -> SeasonWindow | None:
    """The dated window a cube label refers to, derived from the label itself.

    **Not** looked up in `settings.windows`, which only covers `window_years` and so
    describes the *next* build rather than the cube in front of us. A cube built with
    `--years 2025` carries windows the default settings have never heard of, and resolving
    them against that map returned `None` for every one of them.
    """
    try:
        year = int(label.split("-")[0])
    except ValueError:
        return None
    return next((w for w in season_windows([year]) if w.label == label), None)


def _wind_direction(settings, grid, windows: list[str], existing: np.ndarray | None) -> np.ndarray:
    """The overpass-hour wind direction for each window.

    Runs the *real* meteorology ingestor and keeps one of its outputs, rather than
    re-deriving the overpass-hour selection and the vector-mean reduction here. A second
    copy of that arithmetic is a second chance to average an angle wrongly.

    `existing` is whatever the cube already holds, and a window this function cannot
    resolve or fetch **keeps that value rather than being overwritten with NaN**. This
    script writes in place by default, so a failed fetch that clobbers a good column is
    data loss, not a no-op - and it happened: run against a 2025 cube whose wind direction
    `build_tile.py` had already populated, the old lookup resolved nothing and wrote NaN
    over both windows, leaving a cube the air core could no longer run on.
    """
    values = np.full(len(windows), np.nan, dtype="float32")
    if existing is not None and existing.shape == values.shape:
        values[:] = existing

    for i, label in enumerate(windows):
        window = _window_for(label)
        if window is None:
            logger.warning("%s is not a window label this build understands; keeping %s",
                           label, values[i])
            continue
        try:
            arrays, _ = _ingest_meteorology(None, settings, grid, window)
            fetched = float(np.asarray(arrays[DIRECTION].values))
        except Exception as exc:
            logger.warning("%s: meteorology fetch failed (%s); keeping %s", label, exc, values[i])
            continue
        if not np.isfinite(fetched):
            logger.warning("%s: fetch returned NaN; keeping %s", label, values[i])
            continue
        values[i] = fetched
        logger.info("%s: wind from %.0f deg", label, values[i])

    return values


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--zarr", type=Path, default=settings.serve_zarr_store, help="cube to add layers to"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="where to write (default: in place)"
    )
    args = parser.parse_args()

    if not args.zarr.exists():
        print(f"no cube at {args.zarr}", file=sys.stderr)
        return 1

    grid = grid_for_tile(settings.tile)
    # Fully into memory before anything is written: the destination may be the source.
    cube = open_cube(args.zarr).load()
    labels = window_labels(cube)
    logger.info("loaded %s: %d windows", args.zarr, len(labels))

    payload = fetch_overpass(settings.overpass_url, settings.tile.bbox, settings.http_timeout_s)
    emissions = emission_grid(payload, grid)
    cube[EMISSIONS] = xr.DataArray(emissions, dims=("y", "x"), coords=grid.coords())
    previous = np.asarray(cube[DIRECTION].values, dtype="float32") if DIRECTION in cube else None
    cube[DIRECTION] = xr.DataArray(
        _wind_direction(settings, grid, labels, previous), dims=("time",)
    )

    for name in (EMISSIONS, DIRECTION):
        spec = VARIABLES_BY_NAME[name]
        cube[name].attrs.update({"description": spec.description, "units": spec.units})

    out = args.out or args.zarr
    write_cube(cube, out, grid=grid)

    print(f"\nwrote {out}")
    print(
        f"  {EMISSIONS}: {float(emissions.sum()):.3f} g/s over the tile, "
        f"{int((emissions > 0).sum()):,} of {emissions.size:,} cells with a source"
    )
    directions = np.asarray(cube[DIRECTION].values)
    for label, value in zip(labels, directions, strict=True):
        print(f"  {DIRECTION} [{label}]: {value:.0f}")
    if not np.isfinite(directions).all():
        print("\n  WARNING: some windows have no wind direction; the air core cannot run "
              "on those. See the log above.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
