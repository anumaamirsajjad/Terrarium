"""Source-by-source ingest onto the canonical grid.

Each `_ingest_*` function owns one source: it searches, masks, converts raw digital
numbers into physical units, composites over time, and returns grid-aligned DataArrays
keyed by cube variable name. Reprojection and resampling are delegated to odc-stac via
the geobox, using the method each variable declares in `state.cube`.

Sources split in two. **Temporal** ones (Sentinel-2, Landsat, meteorology) are run once
per seasonal window and produce one slice each; **static** ones (DEM, land cover,
population) are run once for the whole cube, because nothing about them varies between
windows and re-fetching them per window would multiply the build time for identical
numbers.

A source that fails is logged and skipped, not fatal. A cube missing its DEM is still a
useful cube; a build that dies because one collection was briefly unavailable is not.
Failure is isolated *per window*, too: one cloudy winter must not cost you the summers.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from functools import partial
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, cast

import numpy as np
import rioxarray
import xarray as xr
from rasterio.enums import Resampling as RioResampling

from terrarium.config import (
    COLLECTION_DEM,
    COLLECTION_LANDSAT,
    COLLECTION_SENTINEL2,
    COLLECTION_WORLDCOVER,
    SOURCE_OPEN_METEO,
    SOURCE_OSM,
    SOURCE_WORLDPOP,
    SeasonWindow,
    Settings,
)
from terrarium.ingest.client import (
    SearchResult,
    configure_gdal_for_cog_reads,
    load_items,
    search_collection,
    select_clearest,
)
from terrarium.ingest.osm import emission_grid, fetch_overpass
from terrarium.state.cube import (
    VARIABLES_BY_NAME,
    Dims,
    VariableSpec,
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
# Liang (2001) narrowband-to-broadband shortwave albedo. The coefficients are his
# Landsat ones, remapped onto the equivalent Sentinel-2 bands (B02/B04/B08/B11/B12) as
# validated by Bonafoni & Sekertekin (2020). The trailing division is part of the
# published formula, not a fudge factor - dropping it biases every pixel ~1.6% high.
LIANG_COEFFICIENTS = (0.356, 0.130, 0.373, 0.085, 0.072)
LIANG_INTERCEPT = -0.0018
LIANG_NORMALISATION = 1.016

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


def _composite_with_depth(data: xr.DataArray) -> tuple[xr.DataArray, tuple[int, float] | None]:
    """Median composite, plus how many clear looks each pixel actually contributed.

    Scene count is a poor proxy for whether a composite is thin. Cloud is patchy, so a
    window with five scenes can still leave a pixel with one usable look, while a sixth
    partly-cloudy scene genuinely deepens the pixels it happens to be clear over. Depth
    is the number that settles it, and it is otherwise invisible: a composite reports
    100 % valid whether every pixel had ten looks or one.

    A minimum of 0 means some pixel had no clear observation at all, so its composite is
    a hole rather than a measurement — worth a warning, because at 40,602 pixels a
    handful of holes rounds to "100 % valid" in any report that shows one decimal.

    Returns `(composite, (min_depth, median_depth))`, or `(data, None)` when there is no
    time axis to reduce. Both reductions come from one `compute()` so the bands are read
    once rather than twice.
    """
    if "time" not in data.dims:
        return data, None

    reduced = xr.Dataset(
        {
            "composite": data.median(dim="time", skipna=True),
            "depth": data.notnull().sum(dim="time"),
        }
    ).compute()
    depth = np.asarray(reduced["depth"].values)
    return reduced["composite"], (int(depth.min()), float(np.median(depth)))


def _search(
    catalog: pystac_client.Client,
    settings: Settings,
    collection_id: str,
    *,
    window: SeasonWindow | None = None,
    cloud_filtered: bool = True,
) -> SearchResult:
    """Search one collection, restricted to `window` if the source varies over time."""
    return search_collection(
        catalog,
        collection_id,
        settings.tile.bbox,
        datetime=window.stac_datetime if window is not None else None,
        max_cloud_cover=settings.max_cloud_cover if cloud_filtered else None,
    )


def _record(
    result: SearchResult, n_composited: int, window: SeasonWindow | None = None
) -> SourceRecord:
    return SourceRecord(
        collection_id=result.collection_id,
        n_found=result.n_found,
        n_kept=result.n_kept,
        n_composited=n_composited,
        max_cloud_cover=result.max_cloud_cover,
        window=None if window is None else window.label,
    )


def _capped(result: SearchResult, settings: Settings) -> SearchResult:
    return select_clearest(result, settings.max_scenes_per_collection)


def _resampling(name: str) -> str:
    """The odc-stac resampling name a gridded variable declares.

    Meteorology declares `None` because it never touches the grid, so a source asking for
    a method by variable name is asking about a raster — and if that variable has no
    method, the spec and the ingestor disagree and the build should say so.
    """
    method = VARIABLES_BY_NAME[name].resampling
    if method is None:
        raise ValueError(f"{name} declares no resampling method; it is not a gridded variable")
    return method.value


def _s2_offset_dn(result: SearchResult) -> float:
    """Reflectance offset to subtract, inferred from the scenes' processing baseline.

    Getting this wrong silently biases every index derived from the bands, so it is
    derived from item metadata rather than assumed from the date.
    """
    baselines = {float(item.properties.get("s2:processing_baseline", 0.0)) for item in result.items}
    needs_offset = {b >= S2_OFFSET_BASELINE for b in baselines}
    if len(needs_offset) > 1:
        logger.warning(
            "mixed Sentinel-2 processing baselines %s; applying the -%s DN offset to all",
            sorted(baselines),
            S2_BOA_OFFSET_DN,
        )
    return S2_BOA_OFFSET_DN if any(needs_offset) else 0.0


def _ingest_sentinel2(
    catalog: pystac_client.Client, settings: Settings, grid: Grid, window: SeasonWindow
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """NDVI, NDBI and broadband albedo from cloud-masked Sentinel-2 L2A, one window."""
    found = _search(catalog, settings, COLLECTION_SENTINEL2, window=window)
    result = _capped(found, settings)
    record = _record(found, len(result.items), window)
    if not result.items:
        return {}, record

    raw = load_items(
        result.items,
        bands=S2_BANDS,
        grid=grid,
        # SCL is a class code: averaging it would invent classes that mean nothing.
        resampling={"*": _resampling("ndvi"), "SCL": "nearest"},
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

    blue, red, nir, swir16, swir22 = (reflectance(b) for b in ("B02", "B04", "B08", "B11", "B12"))

    ndvi = _collapse_time((nir - red) / (nir + red))
    ndbi = _collapse_time((swir16 - nir) / (swir16 + nir))
    c_blue, c_red, c_nir, c_swir16, c_swir22 = LIANG_COEFFICIENTS
    albedo = _collapse_time(
        (
            c_blue * blue
            + c_red * red
            + c_nir * nir
            + c_swir16 * swir16
            + c_swir22 * swir22
            + LIANG_INTERCEPT
        )
        / LIANG_NORMALISATION
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
    catalog: pystac_client.Client, settings: Settings, grid: Grid, window: SeasonWindow
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """Mid-morning land surface temperature from Landsat 8/9 C2 L2, one window."""
    result = _search(catalog, settings, COLLECTION_LANDSAT, window=window)

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
    record = _record(found, len(result.items), window)
    if not result.items:
        return {}, record

    raw = load_items(
        result.items,
        bands=LANDSAT_BANDS,
        grid=grid,
        # QA_PIXEL is a bitmask; interpolating it would scramble every flag.
        resampling={"lwir11": _resampling("lst_c"), "qa_pixel": "nearest"},
        groupby="solar_day",
    )

    clear = (raw["qa_pixel"].astype("uint16") & QA_PIXEL_BAD_BITS) == 0
    st_dn = raw["lwir11"].where(raw["lwir11"] > 0)
    # Plausibility is enforced centrally from the variable's declared valid_range once
    # the composite is assembled; see build_cube.
    lst_c = (st_dn * ST_SCALE + ST_OFFSET - KELVIN_TO_C).where(clear)

    # LST is the target variable and the one the tree-vs-built contrast is read off, so
    # it is the composite whose depth is worth paying an extra reduction for. Landsat is
    # also the cheap source here — two bands over a handful of scenes.
    composite, depth = _composite_with_depth(lst_c)
    if depth is not None:
        depth_min, depth_p50 = depth
        record = record.model_copy(update={"obs_depth_min": depth_min, "obs_depth_p50": depth_p50})
        logger.info(
            "%s %s: clear looks per pixel min=%d median=%.0f over %d scenes",
            window.label,
            COLLECTION_LANDSAT,
            depth_min,
            depth_p50,
            len(result.items),
        )
        if depth_min == 0:
            logger.warning(
                "%s %s: some pixels have no clear observation at all - their composite "
                "is a gap, not a measurement, and will read as ~100%% valid in a report "
                "rounded to one decimal",
                window.label,
                COLLECTION_LANDSAT,
            )

    return {"lst_c": composite.astype("float32")}, record


def _ingest_dem(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """Elevation from Copernicus DEM GLO-30. Static, so no date or cloud filter."""
    result = _search(catalog, settings, COLLECTION_DEM, cloud_filtered=False)
    record = _record(result, len(result.items))
    if not result.items:
        return {}, record

    raw = load_items(
        result.items,
        bands=("data",),
        grid=grid,
        resampling=_resampling("elevation_m"),
        groupby="solar_day",
    )
    return {"elevation_m": _collapse_time(raw["data"]).astype("float32")}, record


def _ingest_worldcover(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """ESA WorldCover class codes. Categorical — nearest neighbour, never interpolated."""
    result = _search(catalog, settings, COLLECTION_WORLDCOVER, cloud_filtered=False)

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
        resampling=_resampling("landcover"),
        groupby="solar_day",
    )

    data = raw["map"]
    if "time" in data.dims:
        # One epoch can still span several acquisition days across adjacent map tiles;
        # they do not overlap, so the last slice after mosaicking is the whole epoch.
        data = data.isel(time=-1)
    return {"landcover": data.fillna(0).astype("uint8")}, record


# --- Open-Meteo (meteorology) ----------------------------------------------------

# Landsat crosses Lahore at ~10:30 local, so 10:00 is the nearest whole hour the
# reanalysis publishes. Sampling the daily mean instead would describe a different time
# of day than the surface temperature it is meant to explain.
OVERPASS_HOUR = 10
# Open-Meteo's hourly field name -> our cube variable name.
METEO_FIELDS: dict[str, str] = {
    "temperature_2m": "air_temp_c",
    "wind_speed_10m": "wind_speed_ms",
    "wind_direction_10m": "wind_direction_deg",
    "relative_humidity_2m": "relative_humidity_pct",
}
# The one field that cannot be reduced by median: it is an angle, so 350 deg and 10 deg
# average to 180 - a plume pointing exactly backwards, with nothing downstream to catch it.
CIRCULAR_FIELDS = frozenset({"wind_direction_deg"})


def _reduce_meteorology(name: str, series: list[float]) -> float:
    """Window reduction for one meteorology field. Median, except for angles."""
    if not series:
        return float("nan")
    if name not in CIRCULAR_FIELDS:
        return float(np.median(series))

    # Vector mean: average the unit vectors, then take the resultant's bearing. This also
    # makes a rose with no prevailing direction average to something arbitrary rather than
    # to a confident wrong answer - but the tile's winter wind is steady, so that case does
    # not arise here and a resultant-length check would be a guard on nothing.
    radians = np.radians(series)
    mean = np.degrees(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean()))
    return float(mean % 360.0)


def _fetch_open_meteo(settings: Settings, window: SeasonWindow) -> dict[str, list[float | None]]:
    """Hourly reanalysis for the tile centre over one window. Keyless and free (D13).

    `timezone=auto` makes the returned timestamps *local*, which is what lets us pick the
    overpass hour by string match rather than juggling Pakistan's UTC+5 offset here.
    """
    lon, lat = settings.tile.centroid
    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": window.start.isoformat(),
            "end_date": window.end.isoformat(),
            "hourly": ",".join(METEO_FIELDS),
            # Default is km/h; the cube declares m/s.
            "wind_speed_unit": "ms",
            "timezone": "auto",
        }
    )
    url = f"{settings.open_meteo_url}?{query}"
    with urllib.request.urlopen(url, timeout=settings.http_timeout_s) as response:
        payload = json.load(response)

    hourly: dict[str, list[float | None]] = payload["hourly"]
    return hourly


def _ingest_meteorology(
    catalog: pystac_client.Client, settings: Settings, grid: Grid, window: SeasonWindow
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """One meteorology value per window, at the overpass hour.

    Reduced by **median over the window's days**, deliberately matching how the LST
    composite is built — pairing a median surface temperature with a mean air temperature
    would put the two variables on different days of the same window.

    Scalars, not maps: one reanalysis point cannot resolve anything inside a 20 km tile,
    and painting it across 40,602 pixels would imply a spatial signal that was never
    measured. See `Dims.TIME` in `state/cube.py`.
    """
    hourly = _fetch_open_meteo(settings, window)
    stamps = [str(t) for t in hourly["time"]]
    at_overpass = [i for i, t in enumerate(stamps) if t.endswith(f"T{OVERPASS_HOUR:02d}:00")]

    arrays: dict[str, xr.DataArray] = {}
    for field, name in METEO_FIELDS.items():
        series = [hourly[field][i] for i in at_overpass]
        finite = [float(v) for v in series if v is not None]
        arrays[name] = xr.DataArray(np.float32(_reduce_meteorology(name, finite)))

    logger.info(
        "%s %s: %d overpass hours from %d hourly records",
        SOURCE_OPEN_METEO,
        window.label,
        len(at_overpass),
        len(stamps),
    )
    return arrays, SourceRecord(
        collection_id=SOURCE_OPEN_METEO,
        n_found=len(stamps),
        n_kept=len(at_overpass),
        n_composited=len(at_overpass),
        window=window.label,
    )


# --- WorldPop (population) -------------------------------------------------------

# Padding on the WGS84 clip, in degrees. Roughly 5 km — enough that reprojection into UTM
# never reaches past the clipped edge for a pixel inside the tile.
WORLDPOP_CLIP_PAD_DEG = 0.05
# How far the tile's head count may drift from the source before it is a defect rather
# than geometry. The grid is snapped outward so a small positive residual is expected
# (+0.9% on Lahore); bilinear resampling would show roughly -26%.
POPULATION_DRIFT_WARN_PCT = 5.0


def _download_once(url: str, path: Path, timeout_s: float) -> Path:
    """Fetch `url` to `path` unless it is already there. Static file, so cache it.

    WorldPop's server does not honour HTTP range requests, so GDAL cannot read the raster
    in place over /vsicurl. It is 33 MB and never changes, so one download reused across
    every rebuild is both simpler and faster than any streaming arrangement.
    """
    if path.exists() and path.stat().st_size > 0:
        logger.info("using cached %s", path)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    logger.info("downloading %s -> %s", url, path)
    with (
        urllib.request.urlopen(url, timeout=timeout_s) as response,
        partial.open("wb") as handle,
    ):
        declared = response.headers.get("Content-Length")
        while chunk := response.read(1 << 20):
            handle.write(chunk)

    # This server drops connections mid-transfer, and a short read raises nothing: you
    # get a valid-looking GeoTIFF with the bottom rows missing, which over a country-wide
    # raster could quietly be the half containing Lahore. Verify, then let the retry
    # wrapper have another go.
    written = partial.stat().st_size
    if declared is None:
        # Chunked transfer encoding, or a server that simply omits the header. There is
        # then nothing to compare against and this guard silently does nothing, so say so
        # rather than letting a passing build imply a check that did not run. The read
        # in _ingest_population is the remaining net: a truncated GeoTIFF fails there,
        # inside the same retry wrapper.
        logger.warning("%s served no Content-Length; cannot verify the download completed", url)
    elif written != int(declared):
        partial.unlink(missing_ok=True)
        raise OSError(f"truncated download: got {written} of {declared} bytes from {url}")

    # Rename only once complete, so an interrupted download is never mistaken for a cache
    # hit on the next run.
    partial.replace(path)
    return path


def _ingest_population(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """Residents per cell from WorldPop 2020 constrained, 100 m.

    Population is *extensive*: the value is a head count for that pixel, not a rate. So
    this resamples by SUM, and the check that it worked is that the tile total survives
    reprojection — an interpolating method would quietly redistribute people.
    """
    path = _download_once(settings.worldpop_url, settings.worldpop_cache, settings.http_timeout_s)

    # open_rasterio's return type is a union over Dataset/DataArray/list; a single-band
    # GeoTIFF is always the DataArray case.
    raw = cast("xr.DataArray", rioxarray.open_rasterio(path, masked=True)).squeeze(drop=True)
    west, south, east, north = settings.tile.bbox
    pad = WORLDPOP_CLIP_PAD_DEG
    clipped = raw.rio.clip_box(west - pad, south - pad, east + pad, north + pad)

    # WorldPop's nodata means "no settlement counted here", which as a head count is
    # zero, not unknown. Filling before the warp also keeps nodata out of the sum.
    counts = clipped.fillna(0.0)
    counts = counts.rio.write_nodata(0.0)

    # Match the canonical grid rather than restating its transform: `grid.empty()` is
    # already the authority on shape, bounds and pixel centres, so there is no second
    # place for the two to drift apart.
    template = grid.empty().rio.write_crs(grid.crs)
    warped = counts.rio.reproject_match(template, resampling=RioResampling.sum, nodata=0.0)

    # Conservation check, and it has to compare like with like. The clip above carries
    # ~5 km of padding so the warp has data to reach for at the edges, so its total is
    # naturally ~30% larger than the tile - comparing against *that* reports a huge fake
    # loss and makes correct SUM resampling look broken in the build log. The honest
    # baseline is the unpadded bbox. Expect a small positive residual: the grid is
    # snapped outward, so it covers slightly more ground than the bbox does.
    inside_bbox = float(np.nansum(np.asarray(raw.rio.clip_box(west, south, east, north).values)))
    warped_total = float(np.asarray(warped.values).sum())
    drift = 100 * (warped_total - inside_bbox) / inside_bbox if inside_bbox else 0.0
    logger.info(
        "%s: %.0f people in the bbox, %.0f on the grid (%+.2f%%)",
        SOURCE_WORLDPOP,
        inside_bbox,
        warped_total,
        drift,
    )
    # Sum-resampling conserves; interpolation does not. Bilinear loses ~26% here, so a
    # drift of this size means the declared method is not the one that ran.
    if abs(drift) > POPULATION_DRIFT_WARN_PCT:
        logger.warning(
            "%s: tile total drifted %+.1f%% from the source - population is extensive "
            "and must resample by SUM; check the declared method",
            SOURCE_WORLDPOP,
            drift,
        )

    return {"population": warped.astype("float32")}, SourceRecord(
        collection_id=SOURCE_WORLDPOP,
        n_found=1,
        n_kept=1,
        n_composited=1,
    )


# --- OpenStreetMap (emission inventory) ------------------------------------------


def _ingest_osm(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[dict[str, xr.DataArray], SourceRecord]:
    """PM2.5 emissions per cell from OSM roads and brick kilns. See `ingest/osm.py`.

    Static: the road network does not change between two seasons of the same year, and
    re-querying Overpass per window would multiply a rate-limited request by four for
    identical geometry. The *seasonality* of the sources is real - kilns fire in winter -
    but it belongs in the air core's parameters, not in a second copy of the inventory.
    """
    payload = fetch_overpass(settings.overpass_url, settings.tile.bbox, settings.http_timeout_s)
    emissions = emission_grid(payload, grid)

    return {
        "pm25_emission_g_s": xr.DataArray(emissions, dims=("y", "x"), coords=grid.coords())
    }, SourceRecord(
        collection_id=SOURCE_OSM,
        n_found=len(payload.get("elements", [])),
        n_kept=len(payload.get("elements", [])),
        n_composited=1,
    )


def _newest_epoch(result: SearchResult) -> SearchResult:
    """Keep only the items from the most recent year present in the search result."""
    years = {item.datetime.year for item in result.items if item.datetime is not None}
    if not years:
        return result

    newest = max(years)
    items = [
        item for item in result.items if item.datetime is not None and item.datetime.year == newest
    ]
    logger.info("%s: using epoch %d (%d tiles)", result.collection_id, newest, len(items))
    return result.model_copy(update={"items": items})


# One source's ingest: search, mask, convert, and return arrays keyed by cube variable
# name, alongside the scene accounting for the build report. Static sources see no
# window; temporal ones produce exactly one window's slice per call.
StaticIngestor = Callable[
    ["pystac_client.Client", Settings, Grid],
    tuple[dict[str, xr.DataArray], SourceRecord],
]
TemporalIngestor = Callable[
    ["pystac_client.Client", Settings, Grid, SeasonWindow],
    tuple[dict[str, xr.DataArray], SourceRecord],
]

_STATIC_INGESTORS: tuple[tuple[str, StaticIngestor], ...] = (
    ("copernicus-dem", _ingest_dem),
    ("worldcover", _ingest_worldcover),
    ("worldpop", _ingest_population),
    ("osm", _ingest_osm),
)

_TEMPORAL_INGESTORS: tuple[tuple[str, TemporalIngestor], ...] = (
    ("sentinel-2", _ingest_sentinel2),
    ("landsat", _ingest_landsat),
    ("open-meteo", _ingest_meteorology),
)


def build_cube(
    catalog: pystac_client.Client, settings: Settings, grid: Grid
) -> tuple[xr.Dataset, list[SourceRecord]]:
    """Run every source over every window and assemble the State Cube.

    Returns the cube plus per-source, per-window scene accounting. Variables whose source
    failed or returned nothing are left at their declared fill value — present in the
    schema, reported as unpopulated. A failure is scoped to one source *and one window*,
    so a cloudy winter costs you that slice and nothing else.
    """
    configure_gdal_for_cog_reads()
    windows = settings.windows
    cube = empty_cube(grid, windows)
    records: list[SourceRecord] = []

    for label, static in _STATIC_INGESTORS:
        outcome = _ingest_with_retries(
            label, partial(static, catalog, settings, grid), settings, grid
        )
        if outcome is None:
            continue
        arrays, record = outcome
        records.append(record)
        for name, array in arrays.items():
            _assign(cube, name, array, None)

    for index, window in enumerate(windows):
        for label, temporal in _TEMPORAL_INGESTORS:
            outcome = _ingest_with_retries(
                f"{label} [{window.label}]",
                partial(temporal, catalog, settings, grid, window),
                settings,
                grid,
            )
            if outcome is None:
                continue
            arrays, record = outcome
            records.append(record)
            for name, array in arrays.items():
                _assign(cube, name, array, index)

    validate_cube(cube, grid, windows)
    return cube, records


def _assign(cube: xr.Dataset, name: str, aligned: xr.DataArray, index: int | None) -> None:
    """Write one cleaned array into its place in the cube.

    Writing through `.values` rather than `cube.update` is what keeps the assignment
    per-window: `update` would replace the whole variable, so a single window's slice
    would blow away the ones already loaded.
    """
    spec = VARIABLES_BY_NAME[name]
    if spec.dims is Dims.SPACE:
        cube[name].values[...] = aligned.values
    elif index is None:
        raise ValueError(f"{name} varies over time but was ingested without a window")
    else:
        cube[name].values[index] = aligned.values


def _ingest_with_retries(
    label: str,
    run: Callable[[], tuple[dict[str, xr.DataArray], SourceRecord]],
    settings: Settings,
    grid: Grid,
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
            arrays, record = run()
            # Materialise *inside* the guard. odc-stac returns lazy dask arrays, so a
            # failed remote read does not raise until the array is computed - if that
            # happened outside this try, one dead collection would kill the whole build.
            aligned = {
                name: _clean(array, VARIABLES_BY_NAME[name], grid) for name, array in arrays.items()
            }
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


def _clean(source: xr.DataArray, spec: VariableSpec, grid: Grid) -> xr.DataArray:
    """Force a loaded array onto the canonical grid's exact dims and coords.

    odc-stac already warped to the right geobox, so for the raster sources this is a
    contract check that materialises the array — not a second resampling. Coordinate
    values are overwritten with the canonical ones to eliminate float drift between
    sources. Physically impossible values are dropped here against the variable's
    declared `valid_range`.

    Scalar (`Dims.TIME`) variables skip the grid entirely — there is no geometry to
    check — but still go through the range enforcement, because a units bug in the
    meteorology fetch should surface the same way a scaling bug in Landsat does.
    """
    if spec.dims is Dims.TIME:
        aligned = source.astype(spec.dtype)
    else:
        data = np.asarray(source.transpose("y", "x").values)
        if data.shape != grid.shape:
            raise ValueError(f"{spec.name}: loaded shape {data.shape} != grid shape {grid.shape}")
        aligned = xr.DataArray(data.astype(spec.dtype), dims=("y", "x"), coords=grid.coords())

    aligned, n_dropped = enforce_valid_range(aligned, spec)
    if n_dropped:
        logger.warning(
            "%s: dropped %d of %d value(s) outside the physical range %s - check scaling",
            spec.name,
            n_dropped,
            aligned.size,
            spec.valid_range,
        )
    return aligned
