"""Source-by-source ingest onto the canonical grid.

Each `_ingest_*` function owns one collection: it searches, masks, converts raw digital
numbers into physical units, composites over time, and returns grid-aligned DataArrays
keyed by cube variable name. Reprojection and resampling are delegated to odc-stac via
the geobox, using the method each variable declares in `state.cube`.

A source that fails is logged and skipped, not fatal. A cube missing its DEM is still a
useful cube; a build that dies because one collection was briefly unavailable is not.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from time import sleep
from typing import TYPE_CHECKING

import numpy as np
import xarray as xr

from terrarium.config import (
    COLLECTION_DEM,
    COLLECTION_LANDSAT,
    COLLECTION_SENTINEL2,
    COLLECTION_WORLDCOVER,
    Settings,
)
from terrarium.ingest.client import (
    SearchResult,
    configure_gdal_for_cog_reads,
    load_items,
    search_collection,
    select_clearest,
)
from terrarium.state.cube import (
    VARIABLES_BY_NAME,
    empty_cube,
    enforce_valid_range,
    validate_cube,
)
from terrarium.state.grid import Grid
from terrarium.state.store import SourceRecord

if TYPE_CHECKING:
    import pystac_client

logger = logging.getLogger(__name__)

# --- Sentinel-2 ------------------------------------------------------------------

# Scene Classification Layer codes that represent usable surface observations.
# Everything else is cloud, cloud shadow, cirrus, saturation, snow, or nodata.
SCL_CLEAR_CLASSES = (4, 5, 6, 7)  # vegetation, bare soil, water, unclassified
S2_BANDS = ("B02", "B04", "B08", "B11", "B12", "SCL")
S2_SCALE = 10_000.0
# Processing baseline 04.00 (2022-01-25 onward) shifts reflectance by -1000 DN.
S2_OFFSET_BASELINE = 4.0
S2_BOA_OFFSET_DN = 1000.0
# Upper bound on plausible surface reflectance. Physically it is 1.0, but specular
# glint and bright cloud edges legitimately overshoot after atmospheric correction, so
# allow headroom rather than punching holes in the composite.
REFLECTANCE_MAX = 1.6

# --- Landsat ---------------------------------------------------------------------

# Only Landsat 8/9 carry the TIRS thermal band exposed as the `lwir11` asset.
LANDSAT_THERMAL_PLATFORMS = frozenset({"landsat-8", "landsat-9"})
LANDSAT_BANDS = ("lwir11", "qa_pixel")
# QA_PIXEL bits 0-4: fill, dilated cloud, cirrus, cloud, cloud shadow.
QA_PIXEL_BAD_BITS = 0b11111
# Collection 2 Level-2 surface temperature scaling, to Kelvin.
ST_SCALE = 0.00341802
ST_OFFSET = 149.0
KELVIN_TO_C = 273.15


def _collapse_time(data: xr.DataArray) -> xr.DataArray:
    """Reduce a time-stacked array to a single clear-sky median composite.

    Median rather than mean: it is robust to the handful of cloud pixels that survive
    any QA mask, which would otherwise drag a summertime LST composite several degrees
    cold.
    """
    if "time" in data.dims:
        return data.median(dim="time", skipna=True)
    return data


def _search(
    catalog: pystac_client.Client,
    settings: Settings,
    collection_id: str,
    *,
    dated: bool = True,
    cloud_filtered: bool = True,
) -> SearchResult:
    return search_collection(
        catalog,
        collection_id,
        settings.tile.bbox,
        datetime=f"{settings.search_start}/{settings.search_end}" if dated else None,
        max_cloud_cover=settings.max_cloud_cover if cloud_filtered else None,
    )


def _record(result: SearchResult, n_composited: int) -> SourceRecord:
    return SourceRecord(
        collection_id=result.collection_id,
        n_found=result.n_found,
        n_kept=result.n_kept,
        n_composited=n_composited,
        max_cloud_cover=result.max_cloud_cover,
    )


def _capped(result: SearchResult, settings: Settings) -> SearchResult:
    return select_clearest(result, settings.max_scenes_per_collection)


def _s2_offset_dn(result: SearchResult) -> float:
    """Reflectance offset to subtract, inferred from the scenes' processing baseline.

    Getting this wrong silently biases every index derived from the bands, so it is
    derived from item metadata rather than assumed from the date.
    """
    baselines = {
        float(item.properties.get("s2:processing_baseline", 0.0)) for item in result.items
    }
    needs_offset = {b >= S2_OFFSET_BASELINE for b in baselines}
    if len(needs_offset) > 1:
        logger.warning(
            "mixed Sentinel-2 processing baselines %s; applying the -%s DN offset to all",
            sorted(baselines),
            S2_BOA_OFFSET_DN,
        )
    return S2_BOA_OFFSET_DN if any(needs_offset) else 0.0


def _ingest_sentinel2(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """NDVI, NDBI and broadband albedo from cloud-masked Sentinel-2 L2A."""
    found = _search(catalog, settings, COLLECTION_SENTINEL2)
    result = _capped(found, settings)
    record = _record(found, len(result.items))
    if not result.items:
        return {}, record

    raw = load_items(
        result.items,
        bands=S2_BANDS,
        grid=grid,
        # SCL is a class code: averaging it would invent classes that mean nothing.
        resampling={"*": VARIABLES_BY_NAME["ndvi"].resampling.value, "SCL": "nearest"},
        groupby="solar_day",
    )

    clear = raw["SCL"].isin(SCL_CLEAR_CLASSES)
    offset = _s2_offset_dn(result)

    def reflectance(band: str) -> xr.DataArray:
        # 0 is the L2A nodata sentinel; keep it out of the composite.
        dn = raw[band].where(raw[band] > 0)
        value = (dn - offset) / S2_SCALE
        # Screen on *reflectance*, not on the raw DN. With the baseline 04.00+ offset in
        # play, any DN below 1000 maps to negative reflectance, which is unphysical and
        # makes a normalised difference explode: (nir-red)/(nir+red) is only guaranteed
        # to land in [-1, 1] when both terms are positive. Screening the DN instead let
        # NDVI reach -2.36 in the first real build.
        physical = (value > 0) & (value <= REFLECTANCE_MAX)
        return value.where(physical).where(clear)

    blue, red, nir, swir16, swir22 = (
        reflectance(b) for b in ("B02", "B04", "B08", "B11", "B12")
    )

    ndvi = _collapse_time((nir - red) / (nir + red))
    ndbi = _collapse_time((swir16 - nir) / (swir16 + nir))
    # Bonafoni & Sekertekin (2020) Sentinel-2 broadband shortwave albedo.
    albedo = _collapse_time(
        0.356 * blue + 0.130 * red + 0.373 * nir + 0.085 * swir16 + 0.072 * swir22 - 0.0018
    )

    return (
        {
            "ndvi": ndvi.astype("float32"),
            "ndbi": ndbi.astype("float32"),
            "albedo": albedo.astype("float32"),
        },
        record,
    )


def _ingest_landsat(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """Land surface temperature from cloud-masked Landsat 8/9 Collection 2 Level-2."""
    result = _search(catalog, settings, COLLECTION_LANDSAT)

    thermal_items = [
        item
        for item in result.items
        if item.properties.get("platform") in LANDSAT_THERMAL_PLATFORMS
    ]
    dropped = result.n_kept - len(thermal_items)
    if dropped:
        logger.info("dropped %d Landsat scenes without a TIRS thermal band", dropped)

    found = result.model_copy(update={"items": thermal_items})
    result = _capped(found, settings)
    record = _record(found, len(result.items))
    if not result.items:
        return {}, record

    raw = load_items(
        result.items,
        bands=LANDSAT_BANDS,
        grid=grid,
        # QA_PIXEL is a bitmask; interpolating it would scramble every flag.
        resampling={"lwir11": VARIABLES_BY_NAME["lst_c"].resampling.value, "qa_pixel": "nearest"},
        groupby="solar_day",
    )

    clear = (raw["qa_pixel"].astype("uint16") & QA_PIXEL_BAD_BITS) == 0
    st_dn = raw["lwir11"].where(raw["lwir11"] > 0)
    # Plausibility is enforced centrally from the variable's declared valid_range once
    # the composite is assembled; see build_cube.
    lst_c = (st_dn * ST_SCALE + ST_OFFSET - KELVIN_TO_C).where(clear)

    return {"lst_c": _collapse_time(lst_c).astype("float32")}, record


def _ingest_dem(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """Elevation from Copernicus DEM GLO-30. Static, so no date or cloud filter."""
    result = _search(catalog, settings, COLLECTION_DEM, dated=False, cloud_filtered=False)
    record = _record(result, len(result.items))
    if not result.items:
        return {}, record

    raw = load_items(
        result.items,
        bands=("data",),
        grid=grid,
        resampling=VARIABLES_BY_NAME["elevation_m"].resampling.value,
        groupby="solar_day",
    )
    return {"elevation_m": _collapse_time(raw["data"]).astype("float32")}, record


def _ingest_worldcover(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """ESA WorldCover class codes. Categorical — nearest neighbour, never interpolated."""
    result = _search(catalog, settings, COLLECTION_WORLDCOVER, dated=False, cloud_filtered=False)

    # WorldCover ships one map per epoch (v100 = 2020, v200 = 2021). Mixing epochs makes
    # no sense for a snapshot of land cover, and there is no valid way to reduce class
    # codes across time - a median of {10, 50} is 30, a class that was never observed.
    # So restrict to the newest epoch present and load only that.
    result = _newest_epoch(result)
    record = _record(result, len(result.items))
    if not result.items:
        return {}, record

    raw = load_items(
        result.items,
        bands=("map",),
        grid=grid,
        resampling=VARIABLES_BY_NAME["landcover"].resampling.value,
        groupby="solar_day",
    )

    data = raw["map"]
    if "time" in data.dims:
        # One epoch can still span several acquisition days across adjacent map tiles;
        # they do not overlap, so the last slice after mosaicking is the whole epoch.
        data = data.isel(time=-1)
    return {"landcover": data.fillna(0).astype("uint8")}, record


def _newest_epoch(result: SearchResult) -> SearchResult:
    """Keep only the items from the most recent year present in the search result."""
    years = {item.datetime.year for item in result.items if item.datetime is not None}
    if not years:
        return result

    newest = max(years)
    items = [
        item
        for item in result.items
        if item.datetime is not None and item.datetime.year == newest
    ]
    logger.info("%s: using epoch %d (%d tiles)", result.collection_id, newest, len(items))
    return result.model_copy(update={"items": items})


# One collection's ingest: search, mask, convert, and return arrays keyed by cube
# variable name, alongside the scene accounting for the build report.
Ingestor = Callable[
    ["pystac_client.Client", Settings, Grid],
    tuple[dict[str, xr.DataArray], SourceRecord],
]

_INGESTORS: tuple[tuple[str, Ingestor], ...] = (
    ("sentinel-2", _ingest_sentinel2),
    ("landsat", _ingest_landsat),
    ("copernicus-dem", _ingest_dem),
    ("worldcover", _ingest_worldcover),
)


def build_cube(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[xr.Dataset, list[SourceRecord]]:
    """Run every source and assemble the State Cube.

    Returns the cube plus per-collection scene accounting. Variables whose source failed
    or returned nothing are left at their declared fill value — present in the schema,
    reported as unpopulated.
    """
    configure_gdal_for_cog_reads()
    cube = empty_cube(grid)
    records: list[SourceRecord] = []

    for label, ingest in _INGESTORS:
        outcome = _ingest_with_retries(label, ingest, catalog, settings, grid, cube)
        if outcome is None:
            continue
        aligned, record = outcome
        records.append(record)
        cube.update(aligned)

    validate_cube(cube, grid)
    return cube, records


def _ingest_with_retries(
    label: str,
    ingest: Ingestor,
    catalog: pystac_client.Client,
    settings: Settings,
    grid: Grid,
    cube: xr.Dataset,
) -> tuple[dict[str, xr.DataArray], SourceRecord] | None:
    """Run one source, retrying transient remote failures. `None` means give up.

    Remote reads fail for two very different reasons and they need different handling: a
    dropped connection or DNS blip is worth retrying, whereas a coding error will fail
    identically every time. We cannot reliably distinguish them from the exception type
    that rasterio surfaces, so we retry a bounded number of times and let the build
    report the variable as missing if every attempt fails.
    """
    attempts = max(1, settings.ingest_attempts)

    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            arrays, record = ingest(catalog, settings, grid)
            # Materialise *inside* the guard. odc-stac returns lazy dask arrays, so a
            # failed remote read does not raise until the array is computed - if that
            # happened outside this try, one dead collection would kill the whole build.
            aligned = {name: _align(array, cube[name], name) for name, array in arrays.items()}
        except Exception as exc:
            elapsed = time.perf_counter() - started
            if attempt < attempts:
                # Exponential backoff. A flat few seconds burns every attempt inside a
                # single DNS flap, which is exactly the failure we are trying to survive.
                delay = settings.ingest_retry_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    "%s ingest attempt %d/%d failed after %.1fs (%s); retrying in %.0fs",
                    label,
                    attempt,
                    attempts,
                    elapsed,
                    exc,
                    delay,
                )
                sleep(delay)
                continue
            logger.exception(
                "%s ingest failed after %d attempts; leaving its variables unpopulated",
                label,
                attempts,
            )
            return None

        logger.info(
            "%s: %d variable(s) in %.1fs (attempt %d)",
            label,
            len(aligned),
            time.perf_counter() - started,
            attempt,
        )
        return aligned, record

    return None


def _align(source: xr.DataArray, template: xr.DataArray, name: str) -> xr.DataArray:
    """Force a loaded array onto the cube's exact dims, coords, and attrs.

    odc-stac already warped to the right geobox, so this is a contract check that
    materialises the array — not a second resampling. Coordinate values are overwritten
    with the canonical ones to eliminate float drift between sources. Physically
    impossible values are dropped here against the variable's declared `valid_range`.
    """
    data = np.asarray(source.transpose("y", "x").values)
    if data.shape != template.shape:
        raise ValueError(f"{name}: loaded shape {data.shape} != cube shape {template.shape}")

    aligned = xr.DataArray(
        data.astype(template.dtype),
        dims=("y", "x"),
        coords=template.coords,
        attrs=template.attrs,
    )

    spec = VARIABLES_BY_NAME[name]
    aligned, n_dropped = enforce_valid_range(aligned, spec)
    if n_dropped:
        logger.warning(
            "%s: dropped %d px (%.3f%%) outside the physical range %s - check scaling",
            name,
            n_dropped,
            100 * n_dropped / aligned.size,
            spec.valid_range,
        )
    return aligned
