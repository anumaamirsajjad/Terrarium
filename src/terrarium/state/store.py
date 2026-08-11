"""Persistence for the State Cube: Zarr for the arrays, DuckDB for the catalogue.

Zarr holds the pixels. DuckDB holds the *provenance* — which build ran when, against
which collections, how many scenes each contributed, and what the resulting value ranges
were. Keeping the catalogue separate means you can ask "was this cube built from cloudy
scenes?" without opening a single chunk.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import duckdb
import rioxarray  # noqa: F401  -- registers the .rio accessor used below
import xarray as xr
from pydantic import BaseModel, ConfigDict, Field

from terrarium.state.cube import CubeSummary
from terrarium.state.grid import Grid

logger = logging.getLogger(__name__)

_CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS builds (
    build_id      VARCHAR PRIMARY KEY,
    built_at      TIMESTAMP NOT NULL,
    tile_name     VARCHAR NOT NULL,
    crs           VARCHAR NOT NULL,
    resolution_m  INTEGER NOT NULL,
    height        INTEGER NOT NULL,
    width         INTEGER NOT NULL,
    zarr_path     VARCHAR NOT NULL,
    duration_s    DOUBLE
);

CREATE TABLE IF NOT EXISTS build_sources (
    build_id      VARCHAR NOT NULL,
    collection_id VARCHAR NOT NULL,
    n_found       INTEGER NOT NULL,
    n_kept        INTEGER NOT NULL,
    n_composited  INTEGER NOT NULL,
    max_cloud_cover DOUBLE
);

CREATE TABLE IF NOT EXISTS build_variables (
    build_id       VARCHAR NOT NULL,
    variable       VARCHAR NOT NULL,
    populated      BOOLEAN NOT NULL,
    valid_fraction DOUBLE NOT NULL,
    vmin           DOUBLE,
    vmax           DOUBLE,
    vmean          DOUBLE
);

-- Phase 3 columns, added rather than baked into the CREATEs above so an existing
-- catalogue from a Phase 1/2 build keeps its history instead of needing a delete.
-- Every INSERT below names its columns, so appended columns cannot shift a value into
-- the wrong slot.
--
-- `window_label`, not `window`: the latter is a reserved word in DuckDB (window
-- functions) and every reference to it would need quoting forever after.
ALTER TABLE builds        ADD COLUMN IF NOT EXISTS windows      VARCHAR;
ALTER TABLE build_sources ADD COLUMN IF NOT EXISTS window_label VARCHAR;
ALTER TABLE build_sources ADD COLUMN IF NOT EXISTS obs_depth_min INTEGER;
ALTER TABLE build_sources ADD COLUMN IF NOT EXISTS obs_depth_p50 DOUBLE;
"""


class SourceRecord(BaseModel):
    """Per-collection scene accounting for one build.

    Three counts, deliberately: `n_found` is what the catalogue holds for this bbox and
    date range, `n_kept` is what survived the cloud filter, and `n_composited` is what
    actually went into the cube after the per-collection scene cap. Collapsing these
    would hide whether a thin variable means bad weather or an over-tight cap.
    """

    model_config = ConfigDict(frozen=True)

    collection_id: str
    n_found: int
    n_kept: int
    n_composited: int
    max_cloud_cover: float | None = None
    # Which seasonal window these counts belong to. `None` for the static sources, which
    # are ingested once for the whole cube rather than per window.
    window: str | None = None
    # How many *clear observations* each pixel actually contributed to the composite.
    # Scene count is a poor proxy: cloud is patchy, so five scenes can still leave a
    # pixel with a single usable look while a sixth partly-cloudy one genuinely deepens
    # the pixels it is clear over. `obs_depth_min` of 0 means some pixel was never
    # observed and its composite is a hole. None for sources with no time axis to reduce.
    obs_depth_min: int | None = None
    obs_depth_p50: float | None = None


class BuildRecord(BaseModel):
    """One end-to-end cube build, as recorded in the catalogue."""

    model_config = ConfigDict(frozen=True)

    build_id: str
    tile_name: str
    zarr_path: Path
    summary: CubeSummary
    sources: list[SourceRecord] = Field(default_factory=list)
    duration_s: float | None = None
    built_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def write_cube(ds: xr.Dataset, path: Path, *, grid: Grid) -> Path:
    """Write the cube to Zarr, replacing anything already there.

    `mode="w"` is deliberate: a tile build is idempotent and total. There is no partial
    update path in v1, so a half-written store from a crashed run should not survive to
    be silently read back.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    ds = ds.copy()
    ds.attrs["grid"] = json.dumps(
        {
            "crs": grid.crs,
            "resolution_m": grid.resolution_m,
            "bounds": list(grid.bounds),
            "shape": list(grid.shape),
        }
    )
    # F9: also written bare, not only inside the `grid` blob. `open_cube` restores the CRS
    # from this key, and a cube that has already been through one open/write cycle carries
    # `spatial_ref` (a coordinate, not an attr) rather than `attrs["crs"]` - dropped two
    # lines below without ever being copied back here, which silently lost the CRS on the
    # *second* write of a cube's life, not the first, which is why it went unnoticed.
    ds.attrs["crs"] = grid.crs
    # rioxarray stashes the CRS on a non-serialisable object; the attrs above carry it.
    ds = ds.drop_vars("spatial_ref", errors="ignore")

    # Chunk spatially, one window per chunk along time: every read is either "one
    # variable, one window" or "one pixel through time", and neither wants time bundled
    # into the same chunk as a 512 x 512 tile. Scalar time series are left unchunked.
    encoding = {}
    for name, var in ds.data_vars.items():
        if not {"y", "x"} <= set(var.dims):
            continue
        encoding[str(name)] = {
            "chunks": tuple(min(512, ds.sizes[dim]) if dim in ("y", "x") else 1 for dim in var.dims)
        }
    ds.to_zarr(path, mode="w", encoding=encoding, consolidated=True)
    logger.info("wrote cube to %s", path)
    return path


def open_cube(path: Path) -> xr.Dataset:
    """Open a previously written cube. The CRS is restored from attrs."""
    ds = cast("xr.Dataset", xr.open_zarr(path, consolidated=True))
    crs = ds.attrs.get("crs")
    if not crs and ds.attrs.get("grid"):
        # F9: repairs a cube written before `write_cube` started setting `attrs["crs"]`
        # bare - its `grid` blob still has the CRS, `write_cube` just never re-read it.
        crs = json.loads(ds.attrs["grid"]).get("crs")
    if crs:
        # rioxarray's accessor is untyped, hence the cast.
        ds = cast("xr.Dataset", ds.rio.write_crs(crs))
    return ds


def connect_catalog(path: Path) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the DuckDB catalogue with its schema applied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    conn.execute(_CATALOG_SCHEMA)
    return conn


def record_build(conn: duckdb.DuckDBPyConnection, record: BuildRecord) -> None:
    """Insert one build and its per-source and per-variable rows."""
    height, width = record.summary.shape

    conn.execute("DELETE FROM builds WHERE build_id = ?", [record.build_id])
    conn.execute("DELETE FROM build_sources WHERE build_id = ?", [record.build_id])
    conn.execute("DELETE FROM build_variables WHERE build_id = ?", [record.build_id])

    conn.execute(
        "INSERT INTO builds (build_id, built_at, tile_name, crs, resolution_m, height, "
        "width, zarr_path, duration_s, windows) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            record.build_id,
            record.built_at,
            record.tile_name,
            record.summary.crs,
            record.summary.resolution_m,
            height,
            width,
            str(record.zarr_path),
            record.duration_s,
            ",".join(record.summary.windows),
        ],
    )

    for source in record.sources:
        conn.execute(
            "INSERT INTO build_sources (build_id, collection_id, n_found, n_kept, "
            "n_composited, max_cloud_cover, window_label, obs_depth_min, obs_depth_p50) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                record.build_id,
                source.collection_id,
                source.n_found,
                source.n_kept,
                source.n_composited,
                source.max_cloud_cover,
                source.window,
                source.obs_depth_min,
                source.obs_depth_p50,
            ],
        )

    for var in record.summary.variables:
        conn.execute(
            "INSERT INTO build_variables (build_id, variable, populated, valid_fraction, "
            "vmin, vmax, vmean) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                record.build_id,
                var.name,
                var.populated,
                var.valid_fraction,
                var.vmin,
                var.vmax,
                var.vmean,
            ],
        )

    conn.commit()
    logger.info("catalogued build %s", record.build_id)
