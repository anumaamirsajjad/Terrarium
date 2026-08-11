"""Tests for the ingest pipeline.

Nothing here touches the network. STAC search and asset loading are both replaced with
synthetic fixtures, so what is under test is exactly the part we own: resampling method
selection, reprojection onto the canonical grid, QA masking, and unit conversion.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast

import numpy as np
import pystac
import pytest
import xarray as xr
from odc.geo.geobox import GeoBox
from odc.geo.xr import wrap_xr, xr_reproject
from pyproj import Transformer

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
    Tile,
)
from terrarium.ingest import pipeline
from terrarium.ingest.client import (
    SearchResult,
    configure_gdal_for_cog_reads,
    geobox_for,
    select_clearest,
)
from terrarium.state.cube import (
    CUBE_VARIABLES,
    VARIABLES_BY_NAME,
    Resampling,
    enforce_valid_range,
    select_window,
    summarise,
)
from terrarium.state.grid import Grid, grid_for_tile
from terrarium.state.store import SourceRecord

if TYPE_CHECKING:
    import pystac_client

# The fakes never dereference the catalog; this only satisfies the signature.
FAKE_CATALOG = cast("pystac_client.Client", object())

# A small tile so the whole suite stays in-memory and fast.
TEST_TILE = Tile(
    name="TestTile",
    country="PK",
    bbox=(74.30, 31.50, 74.32, 31.52),
    crs="EPSG:32643",
    target_resolution_m=100,
)

# One year, so the mocked builds stay small and fast: a summer and a winter window is
# enough to exercise every per-window code path.
TEST_YEARS = [2024]


@pytest.fixture
def grid() -> Grid:
    return grid_for_tile(TEST_TILE)


@pytest.fixture
def windows() -> tuple[SeasonWindow, ...]:
    return Settings(window_years=TEST_YEARS).windows


@pytest.fixture
def settings() -> Settings:
    # No retries and no delay: retry behaviour gets its own tests, and paying the real
    # backoff in every failure test would make the suite slow for no extra coverage.
    return Settings(
        window_years=TEST_YEARS,
        max_cloud_cover=20.0,
        ingest_attempts=1,
        ingest_retry_delay_s=0.0,
    )


# --------------------------------------------------------------------------------
# Reprojection + resampling on synthetic data
# --------------------------------------------------------------------------------


def _fine_geobox(grid: Grid) -> GeoBox:
    """A source grid deliberately misaligned with the target.

    3x finer resolution, and offset by 17 m — coprime with both 100 m and 33.3 m — so
    target pixel centres never land on a source pixel centre. Without that offset the
    resampler degenerates into a pass-through and these tests would prove nothing.
    The margin guarantees full coverage, so no nodata leaks into the assertions.
    """
    left, bottom, right, top = grid.bounds
    margin = grid.resolution_m * 2
    return GeoBox.from_bbox(
        (left - margin + 17, bottom - margin + 17, right + margin + 17, top + margin + 17),
        crs=grid.crs,
        resolution=grid.resolution_m / 3,
        tight=True,
    )


def _synthetic_source(grid: Grid, data: np.ndarray, *, dtype: str) -> xr.DataArray:
    fine = _fine_geobox(grid)
    assert data.shape == fine.shape
    return wrap_xr(data.astype(dtype), fine, nodata="auto")


def _random_classes(grid: Grid, classes: np.ndarray) -> np.ndarray:
    """Spatially incoherent class codes — the hardest case for a resampler to fake."""
    rng = np.random.default_rng(1234)
    return rng.choice(classes, size=_fine_geobox(grid).shape)


def _x_ramp(grid: Grid, low: float, high: float) -> np.ndarray:
    """A ramp that is linear in *projected space*, constant down each column."""
    shape = _fine_geobox(grid).shape
    row = np.linspace(low, high, shape[1], dtype="float64")
    return np.broadcast_to(row, shape).copy()


WORLDCOVER_CLASSES = np.array([10, 30, 50, 80], dtype="uint8")


def test_nearest_resampling_preserves_categorical_class_codes(grid: Grid) -> None:
    """The whole reason land cover uses nearest: averaging class codes invents classes.

    The source holds only WorldCover codes {10, 30, 50, 80}. After nearest resampling the
    output must be a subset of exactly those. Any other value is a fabricated class that
    means nothing in the WorldCover legend.
    """
    source = _synthetic_source(grid, _random_classes(grid, WORLDCOVER_CLASSES), dtype="uint8")

    out = xr_reproject(source, geobox_for(grid), resampling=Resampling.NEAREST.value)
    produced = set(np.unique(np.asarray(out.values)).tolist())

    assert produced <= set(WORLDCOVER_CLASSES.tolist()), f"nearest invented classes: {produced}"
    # Sanity: the test data must actually be varied, or the assertion above is vacuous.
    assert len(produced) == len(WORLDCOVER_CLASSES)


def test_bilinear_resampling_would_corrupt_class_codes(grid: Grid) -> None:
    """The counter-example that justifies the rule rather than merely restating it."""
    source = _synthetic_source(
        grid, _random_classes(grid, WORLDCOVER_CLASSES).astype("float32"), dtype="float32"
    )

    out = xr_reproject(source, geobox_for(grid), resampling=Resampling.BILINEAR.value)
    produced = set(np.unique(np.asarray(out.values)).tolist())

    assert not produced <= set(WORLDCOVER_CLASSES.astype("float32").tolist()), (
        "expected bilinear to blend class codes into meaningless intermediates"
    )


def test_bilinear_resampling_preserves_a_linear_gradient(grid: Grid) -> None:
    """A ramp linear in projected space must come out linear, bounded, and monotonic."""
    source = _synthetic_source(grid, _x_ramp(grid, 0.0, 100.0), dtype="float32")

    out = xr_reproject(source, geobox_for(grid), resampling=Resampling.BILINEAR.value)
    values = np.asarray(out.values)

    assert np.isfinite(values).all(), "fully-covering source should leave no nodata"
    # Interpolation must never extrapolate outside the source's range.
    assert values.min() >= -0.01
    assert values.max() <= 100.01
    # Monotonic left-to-right, and identical down every column.
    row = values[values.shape[0] // 2, :]
    assert np.all(np.diff(row) > 0)
    assert np.allclose(values, row[np.newaxis, :], atol=1e-3)
    # Evenly spaced: a ramp resampled bilinearly stays a ramp.
    steps = np.diff(row)
    assert steps.std() < 0.01 * steps.mean()


def test_bilinear_averages_neighbours_where_nearest_discards_them(grid: Grid) -> None:
    """Why continuous variables are bilinear: downsampling should average, not sample.

    On a noisy field, nearest keeps whichever single source pixel a target centre lands
    on and throws the other eight away, so the noise passes straight through. Bilinear
    blends neighbours and therefore low-pass filters. Downsampling 30 m Landsat to 100 m
    with nearest would keep single-pixel outliers as if they were 100 m truths.

    (A *linear* ramp would not distinguish the two — bilinear reproduces a linear
    function exactly — which is why this uses noise.)
    """
    rng = np.random.default_rng(7)
    field = _x_ramp(grid, 0.0, 100.0) + rng.normal(0.0, 10.0, _fine_geobox(grid).shape)
    geobox = geobox_for(grid)

    nearest = np.asarray(
        xr_reproject(
            _synthetic_source(grid, field, dtype="float32"), geobox, resampling="nearest"
        ).values
    )
    bilinear = np.asarray(
        xr_reproject(
            _synthetic_source(grid, field, dtype="float32"), geobox, resampling="bilinear"
        ).values
    )

    # Residual noise after removing the shared ramp: bilinear must be quieter.
    noise_n = np.diff(nearest, axis=0).std()
    noise_b = np.diff(bilinear, axis=0).std()
    assert noise_b < noise_n, f"bilinear ({noise_b:.3f}) should smooth vs nearest ({noise_n:.3f})"


def test_reprojection_lands_exactly_on_the_canonical_grid(grid: Grid) -> None:
    """Alignment is the State Cube's core promise; check it on the real warp path."""
    source = _synthetic_source(grid, _x_ramp(grid, 0.0, 1.0), dtype="float32")
    out = xr_reproject(source, geobox_for(grid), resampling="bilinear")

    assert out.shape == grid.shape
    assert np.allclose(np.asarray(out["x"].values), grid.x_coords())
    assert np.allclose(np.asarray(out["y"].values), grid.y_coords())


def test_reprojection_from_wgs84_lands_on_the_canonical_grid(grid: Grid) -> None:
    """The real pipeline crosses CRS boundaries; make sure the geobox still governs."""
    fine = _fine_geobox(grid)
    source = wrap_xr(_x_ramp(grid, 0.0, 50.0).astype("float32"), fine, nodata="auto")
    in_wgs84 = xr_reproject(source, "EPSG:4326", resampling="bilinear")

    out = xr_reproject(in_wgs84, geobox_for(grid), resampling="bilinear")

    assert out.shape == grid.shape
    assert out.odc.crs == grid.crs
    assert np.allclose(np.asarray(out["x"].values), grid.x_coords())
    assert np.allclose(np.asarray(out["y"].values), grid.y_coords())


def test_every_gridded_variable_declares_a_resampling_method() -> None:
    for spec in CUBE_VARIABLES:
        if spec.dims.is_spatial:
            assert spec.resampling is not None, f"{spec.name} lands on the grid but has no method"
        else:
            # Nothing is resampled onto a dimension that does not exist.
            assert spec.resampling is None, f"{spec.name} is a scalar series"
    # Land cover is the categorical one; nothing else may be.
    categorical = {spec.name for spec in CUBE_VARIABLES if spec.is_categorical}
    assert categorical == {"landcover"}


# --------------------------------------------------------------------------------
# Mocked STAC end-to-end
# --------------------------------------------------------------------------------


def _item(
    collection_id: str,
    *,
    cloud: float = 5.0,
    properties: dict[str, object] | None = None,
) -> pystac.Item:
    """A minimal STAC item. Only the properties the pipeline reads are populated."""
    props: dict[str, object] = {"eo:cloud_cover": cloud, **(properties or {})}
    return pystac.Item(
        id=f"{collection_id}-test",
        geometry=None,
        bbox=list(TEST_TILE.bbox),
        datetime=datetime(2024, 5, 1, tzinfo=UTC),
        properties=props,
        collection=collection_id,
    )


# Digital numbers chosen so the expected physical values are exact and checkable.
S2_RED_DN = 1500.0  # -> 0.05 reflectance after the -1000 DN baseline offset
S2_NIR_DN = 4000.0  # -> 0.30
S2_SWIR16_DN = 2000.0  # -> 0.10
S2_SWIR22_DN = 1500.0  # -> 0.05
S2_BLUE_DN = 1200.0  # -> 0.02
EXPECTED_NDVI = (0.30 - 0.05) / (0.30 + 0.05)
EXPECTED_NDBI = (0.10 - 0.30) / (0.10 + 0.30)
EXPECTED_ALBEDO = (
    0.356 * 0.02 + 0.130 * 0.05 + 0.373 * 0.30 + 0.085 * 0.10 + 0.072 * 0.05 - 0.0018
) / 1.016

LST_TARGET_C = 35.0
LST_DN = (LST_TARGET_C + pipeline.KELVIN_TO_C - pipeline.ST_OFFSET) / pipeline.ST_SCALE

CLOUD_BIT = 1 << 3


class LoadCall(NamedTuple):
    """One recorded `load_items` invocation: which bands, and with what resampling."""

    bands: tuple[str, ...]
    resampling: str | dict[str, str]


class FakeLoader:
    """Stand-in for `load_items`, returning synthetic, already-aligned bands.

    The left half of every scene is flagged cloudy so masking is observable: those pixels
    must come out NaN regardless of what the band values say. Every call is recorded so
    tests can assert on the *resampling method requested per band*, which is the part of
    the contract that no amount of inspecting output pixels would reveal.
    """

    def __init__(self, grid: Grid) -> None:
        self.height, self.width = grid.shape
        self.coords = {"y": grid.y_coords(), "x": grid.x_coords()}
        self.calls: list[LoadCall] = []

    def _const(self, value: float, dtype: str = "float32") -> xr.DataArray:
        return xr.DataArray(
            np.full((self.height, self.width), value, dtype=dtype),
            dims=("y", "x"),
            coords=self.coords,
        )

    def _half_flagged(self, clear: int, flagged: int, dtype: str) -> xr.DataArray:
        data = np.full((self.height, self.width), clear, dtype=dtype)
        data[:, : self.width // 2] = flagged
        return xr.DataArray(data, dims=("y", "x"), coords=self.coords)

    def __call__(
        self,
        items: list[pystac.Item],
        bands: tuple[str, ...],
        grid: Grid,
        *,
        resampling: str | dict[str, str],
        groupby: str | None = None,
        **kwargs: object,
    ) -> xr.Dataset:
        self.calls.append(LoadCall(bands=tuple(bands), resampling=resampling))

        if set(bands) >= {"B04", "B08"}:
            return xr.Dataset(
                {
                    "B02": self._const(S2_BLUE_DN),
                    "B04": self._const(S2_RED_DN),
                    "B08": self._const(S2_NIR_DN),
                    "B11": self._const(S2_SWIR16_DN),
                    "B12": self._const(S2_SWIR22_DN),
                    # 4 = vegetation (clear), 9 = high-probability cloud.
                    "SCL": self._half_flagged(clear=4, flagged=9, dtype="uint8"),
                }
            )
        if "lwir11" in bands:
            return xr.Dataset(
                {
                    "lwir11": self._const(LST_DN),
                    "qa_pixel": self._half_flagged(clear=0, flagged=CLOUD_BIT, dtype="uint16"),
                }
            )
        if "data" in bands:
            return xr.Dataset({"data": self._const(217.0)})
        if "map" in bands:
            return xr.Dataset({"map": self._const(50, dtype="uint8")})
        raise AssertionError(f"unexpected bands {bands}")


def _fake_search(
    counts: dict[str, tuple[int, int]],
) -> Callable[..., SearchResult]:
    """Stand-in for `search_collection`. `counts` maps collection -> (found, kept)."""

    def search(
        catalog: object,
        collection_id: str,
        bbox: tuple[float, float, float, float],
        *,
        datetime: str | None = None,
        max_cloud_cover: float | None = None,
    ) -> SearchResult:
        found, kept = counts.get(collection_id, (0, 0))
        items = [
            _item(
                collection_id,
                properties={"platform": "landsat-9", "s2:processing_baseline": "05.00"},
            )
            for _ in range(kept)
        ]
        return SearchResult(
            collection_id=collection_id,
            items=items,
            n_found=found,
            max_cloud_cover=max_cloud_cover,
        )

    return search


# --------------------------------------------------------------------------------
# The two non-STAC sources: WorldPop over plain HTTP, Open-Meteo over JSON
# --------------------------------------------------------------------------------

# People per source pixel in the synthetic population raster. Uniform, so the tile total
# is exactly (pixel count x this) and any loss during reprojection is arithmetic rather
# than an artefact of an awkward distribution.
FAKE_PEOPLE_PER_PIXEL = 4.0
# WorldPop's native grid: 3 arc-seconds, ~100 m.
WORLDPOP_DEG = 1.0 / 1200.0


def _write_fake_worldpop(path: Path, tile: Tile) -> Path:
    """A WGS84 population raster covering `tile` with a known, uniform head count."""
    west, south, east, north = tile.bbox
    pad = pipeline.WORLDPOP_CLIP_PAD_DEG * 2
    lons = np.arange(west - pad, east + pad, WORLDPOP_DEG)
    lats = np.arange(north + pad, south - pad, -WORLDPOP_DEG)

    data = np.full((len(lats), len(lons)), FAKE_PEOPLE_PER_PIXEL, dtype="float32")
    array = xr.DataArray(data, dims=("y", "x"), coords={"y": lats, "x": lons})
    array = array.rio.write_crs("EPSG:4326").rio.write_nodata(np.nan)
    path.parent.mkdir(parents=True, exist_ok=True)
    array.rio.to_raster(path)
    return path


def _fake_open_meteo(settings: Settings, window: SeasonWindow) -> dict[str, list[float | None]]:
    """Hourly series whose values encode the hour, so picking the wrong one is visible.

    Temperature is 20 + hour, so the overpass-hour median must come out at exactly
    20 + OVERPASS_HOUR. A daily mean, or an off-by-one on the hour, lands somewhere else.
    """
    stamps: list[str] = []
    temps: list[float | None] = []
    winds: list[float | None] = []
    directions: list[float | None] = []
    humidity: list[float | None] = []

    day = window.start
    while day <= window.end:
        for hour in range(24):
            stamps.append(f"{day.isoformat()}T{hour:02d}:00")
            temps.append(20.0 + hour)
            winds.append(hour / 10.0)
            # A northerly that straddles 0: alternate days sit either side of it. Any
            # scalar reduction lands near 180 - a plume pointing exactly backwards - so
            # this is the series that separates a vector mean from a median.
            directions.append(350.0 if day.toordinal() % 2 == 0 else 10.0)
            humidity.append(float(hour))
        day += timedelta(days=1)

    return {
        "time": cast("list[float | None]", stamps),
        "temperature_2m": temps,
        "wind_speed_10m": winds,
        "wind_direction_10m": directions,
        "relative_humidity_2m": humidity,
    }


def _fake_overpass(grid: Grid) -> dict[str, object]:
    """One road and one kiln, placed on the test grid rather than on a real city."""
    to_wgs84 = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform
    left, _, _, top = grid.bounds
    y = top - 2.5 * grid.resolution_m
    start = to_wgs84(left + 1.5 * grid.resolution_m, y)
    end = to_wgs84(left + 4.5 * grid.resolution_m, y)

    return {
        "elements": [
            {
                "type": "way",
                "tags": {"highway": "primary"},
                "geometry": [
                    {"lon": start[0], "lat": start[1]},
                    {"lon": end[0], "lat": end[1]},
                ],
            },
            {
                "type": "node",
                "tags": {"man_made": "kiln"},
                "lon": to_wgs84(left + 6.5 * grid.resolution_m, y)[0],
                "lat": to_wgs84(left + 6.5 * grid.resolution_m, y)[1],
            },
        ]
    }


@pytest.fixture
def mocked(monkeypatch: pytest.MonkeyPatch, grid: Grid, tmp_path: Path) -> FakeLoader:
    """Stub every network boundary: STAC search + load, the WorldPop GET, Open-Meteo.

    The two new sources are stubbed at their *fetch* functions rather than at the whole
    ingestor, so the clipping, the sum-resampling and the overpass-hour reduction all stay
    under test — those are the parts we own.
    """
    loader = FakeLoader(grid)
    counts = {
        COLLECTION_SENTINEL2: (30, 6),
        COLLECTION_LANDSAT: (12, 4),
        COLLECTION_DEM: (2, 2),
        COLLECTION_WORLDCOVER: (1, 1),
    }
    monkeypatch.setattr(pipeline, "load_items", loader)
    monkeypatch.setattr(pipeline, "search_collection", _fake_search(counts))

    worldpop = _write_fake_worldpop(tmp_path / "worldpop.tif", TEST_TILE)
    monkeypatch.setattr(pipeline, "_download_once", lambda url, path, timeout_s: worldpop)
    monkeypatch.setattr(pipeline, "_fetch_open_meteo", _fake_open_meteo)
    monkeypatch.setattr(
        pipeline, "fetch_overpass", lambda url, bbox, timeout_s: _fake_overpass(grid)
    )
    return loader


def test_build_cube_populates_every_variable(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    cube, records = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    summary = summarise(cube, grid)

    assert summary.missing == []
    assert summary.shape == grid.shape
    assert {r.collection_id for r in records} == {
        COLLECTION_SENTINEL2,
        COLLECTION_LANDSAT,
        COLLECTION_DEM,
        COLLECTION_WORLDCOVER,
        SOURCE_OPEN_METEO,
        SOURCE_WORLDPOP,
        SOURCE_OSM,
    }


def test_every_variable_lands_on_the_axes_it_declares(
    mocked: FakeLoader, settings: Settings, grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """The Phase 3 contract: maps per window, static maps, and per-window scalars."""
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    n = len(windows)

    assert cube["lst_c"].dims == ("time", "y", "x")
    assert cube["lst_c"].shape == (n, *grid.shape)
    # Elevation does not change between seasons; storing n copies of it would be a lie
    # about what was measured.
    assert cube["elevation_m"].dims == ("y", "x")
    assert cube["population"].dims == ("y", "x")
    # One reanalysis point cannot resolve anything inside a 20 km tile.
    assert cube["air_temp_c"].dims == ("time",)
    assert cube["air_temp_c"].shape == (n,)

    assert [str(w) for w in cube["window"].values] == [w.label for w in windows]
    assert sorted(set(str(s) for s in cube["season"].values)) == ["summer", "winter"]


def test_every_window_gets_its_own_composite(
    monkeypatch: pytest.MonkeyPatch,
    mocked: FakeLoader,
    settings: Settings,
    grid: Grid,
    windows: tuple[SeasonWindow, ...],
) -> None:
    """Each window must be searched over its own dates, not the whole span at once."""
    searched: list[str | None] = []
    # `mocked` already replaced the real search; wrap whatever is currently installed.
    inner = _fake_search(
        {
            COLLECTION_SENTINEL2: (30, 6),
            COLLECTION_LANDSAT: (12, 4),
            COLLECTION_DEM: (2, 2),
            COLLECTION_WORLDCOVER: (1, 1),
        }
    )

    def recording_search(
        catalog: object,
        collection_id: str,
        bbox: tuple[float, float, float, float],
        *,
        datetime: str | None = None,
        max_cloud_cover: float | None = None,
    ) -> SearchResult:
        if collection_id == COLLECTION_SENTINEL2:
            searched.append(datetime)
        return inner(
            catalog, collection_id, bbox, datetime=datetime, max_cloud_cover=max_cloud_cover
        )

    monkeypatch.setattr(pipeline, "search_collection", recording_search)
    pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)

    assert searched == [w.stac_datetime for w in windows]
    # And winter really does straddle the new year, which is the case a naive
    # "same year, month 11 to month 1" range would silently return nothing for.
    winter = next(w for w in windows if w.season == "winter")
    assert winter.start.year + 1 == winter.end.year


def test_scene_counts_survive_to_the_report(
    mocked: FakeLoader, settings: Settings, grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    _, records = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    s2 = [r for r in records if r.collection_id == COLLECTION_SENTINEL2]

    # One row per window, not one row for the whole build: a season that came back thin
    # has to be visible as that season.
    assert [r.window for r in s2] == [w.label for w in windows]
    assert all(r.n_found == 30 and r.n_kept == 6 for r in s2)
    assert all(r.max_cloud_cover == 20.0 for r in s2)

    dem = next(r for r in records if r.collection_id == COLLECTION_DEM)
    # The DEM is static: it must not be cloud-filtered, and it belongs to no window.
    assert dem.max_cloud_cover is None
    assert dem.window is None


def test_categorical_bands_are_loaded_with_nearest(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    """QA masks and class codes must never be requested with an interpolating method."""
    pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)

    def resampling_for(band: str) -> str | dict[str, str]:
        return next(call.resampling for call in mocked.calls if band in call.bands)

    s2 = resampling_for("SCL")
    assert isinstance(s2, dict)
    assert s2["SCL"] == "nearest"
    assert s2["*"] == "bilinear"

    landsat = resampling_for("lwir11")
    assert isinstance(landsat, dict)
    assert landsat["qa_pixel"] == "nearest"
    assert landsat["lwir11"] == "bilinear"

    assert resampling_for("map") == "nearest"
    assert resampling_for("data") == "bilinear"


def test_cloud_masking_blanks_flagged_pixels(
    mocked: FakeLoader, settings: Settings, grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    width = grid.shape[1]

    # Every window, not just the first: the masking runs once per slice.
    for window in windows:
        sliced = select_window(cube, window.label)
        for name in ("ndvi", "lst_c"):
            values = np.asarray(sliced[name].values)
            assert np.isnan(values[:, : width // 2]).all(), f"{name}: cloudy half not masked"
            assert np.isfinite(values[:, width // 2 :]).all(), f"{name}: clear half was masked"


def test_sentinel2_indices_use_baseline_corrected_reflectance(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    clear = np.s_[:, :, grid.shape[1] // 2 :]

    assert np.allclose(cube["ndvi"].values[clear], EXPECTED_NDVI, atol=1e-5)
    assert np.allclose(cube["ndbi"].values[clear], EXPECTED_NDBI, atol=1e-5)
    assert np.allclose(cube["albedo"].values[clear], EXPECTED_ALBEDO, atol=1e-5)


def test_landsat_digital_numbers_become_celsius(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    clear = np.s_[:, :, grid.shape[1] // 2 :]

    assert np.allclose(cube["lst_c"].values[clear], LST_TARGET_C, atol=0.01)


# --------------------------------------------------------------------------------
# Meteorology and population
# --------------------------------------------------------------------------------


def test_meteorology_is_sampled_at_the_overpass_hour(
    mocked: FakeLoader, settings: Settings, grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """A daily mean would describe a different time of day than the LST it explains.

    The fixture's temperature is 20 + hour, so the only value that can come out of a
    correct reduction is 20 + OVERPASS_HOUR. A 24-hour mean gives 31.5; an off-by-one
    gives 31 or 29.
    """
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)

    assert np.allclose(cube["air_temp_c"].values, 20.0 + pipeline.OVERPASS_HOUR)
    assert np.allclose(cube["wind_speed_ms"].values, pipeline.OVERPASS_HOUR / 10.0)
    assert np.allclose(cube["relative_humidity_pct"].values, pipeline.OVERPASS_HOUR)
    assert cube["air_temp_c"].shape == (len(windows),)


def test_wind_direction_is_reduced_as_an_angle_not_a_number(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    """350 deg and 10 deg are 20 deg apart, and their median is 180 - the exact opposite.

    The air core turns this number into the direction a plume travels, so a scalar
    reduction across the 0/360 seam does not degrade the answer, it reverses it.
    """
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)

    values = np.asarray(cube["wind_direction_deg"].values)
    # Near due north, either side of the seam. Cosine, because 359.5 and 0.5 are both right.
    assert np.all(np.cos(np.radians(values)) > 0.95), values


def test_the_emission_inventory_lands_on_the_grid(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    """OSM is vector, and the cube is a raster. This is where the two meet."""
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)

    emissions = np.asarray(cube["pm25_emission_g_s"].values)
    assert emissions.shape == grid.shape
    # Finite everywhere: "no road here" is a real zero, and only a build whose OSM ingest
    # never ran leaves NaN. Sources on some cells but not most, which is what a road
    # network looks like.
    assert np.isfinite(emissions).all()
    assert 0 < (emissions > 0).sum() < emissions.size


def test_population_survives_reprojection_as_a_head_count(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    """The whole reason population declares SUM: people are extensive, not a rate.

    The synthetic source is uniform, so the expected tile total is just the grid area in
    source pixels. Bilinear, which is right for every other continuous variable here,
    loses roughly a quarter of them on the real WorldPop raster.
    """
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    people = np.asarray(cube["population"].values)

    assert np.isfinite(people).all()
    # Grid area / source pixel area, times the per-source-pixel count. The source is
    # ~92.6 m at this latitude in x and ~92.6 m in y, so this is not 1:1 with our 100 m
    # cells - which is precisely why an interpolating method would not conserve.
    source_px_m2 = (WORLDPOP_DEG * 111_320) ** 2 * np.cos(np.radians(TEST_TILE.centroid[1]))
    cell_px = grid.resolution_m**2 / source_px_m2
    expected_per_cell = FAKE_PEOPLE_PER_PIXEL * cell_px

    assert people.mean() == pytest.approx(expected_per_cell, rel=0.05)


def test_population_is_not_resampled_by_interpolation() -> None:
    """Guard the declaration itself — the bug would be a quiet edit to the spec."""
    assert VARIABLES_BY_NAME["population"].resampling is Resampling.SUM
    # SUM must not be mistaken for a categorical method by anything downstream.
    assert not VARIABLES_BY_NAME["population"].is_categorical


def test_landcover_stays_integral(mocked: FakeLoader, settings: Settings, grid: Grid) -> None:
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)

    assert cube["landcover"].dtype == np.uint8
    assert set(np.unique(cube["landcover"].values).tolist()) == {50}


def test_a_failing_source_leaves_its_variables_missing_but_the_cube_valid(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, grid: Grid, mocked: FakeLoader
) -> None:
    """One dead collection must not take the whole build down."""

    def exploding_search(*args: object, **kwargs: object) -> SearchResult:
        if args[1] == COLLECTION_LANDSAT:
            raise RuntimeError("STAC is having a day")
        return _fake_search(
            {COLLECTION_SENTINEL2: (30, 6), COLLECTION_DEM: (2, 2), COLLECTION_WORLDCOVER: (1, 1)}
        )(*args, **kwargs)

    monkeypatch.setattr(pipeline, "search_collection", exploding_search)
    cube, records = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    summary = summarise(cube, grid)

    assert summary.missing == ["lst_c"]
    assert "ndvi" in summary.populated
    assert COLLECTION_LANDSAT not in {r.collection_id for r in records}


def test_one_bad_window_does_not_cost_the_others(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    grid: Grid,
    mocked: FakeLoader,
    windows: tuple[SeasonWindow, ...],
) -> None:
    """Failure is isolated per window, not just per source.

    A season whose scenes are all cloudy, or whose reads time out, must cost you that
    slice and nothing else. Before the time dimension there was only one slice to lose,
    so this is a genuinely new failure mode.
    """
    doomed = windows[-1]
    inner = _fake_search(
        {
            COLLECTION_SENTINEL2: (30, 6),
            COLLECTION_LANDSAT: (12, 4),
            COLLECTION_DEM: (2, 2),
            COLLECTION_WORLDCOVER: (1, 1),
        }
    )

    def flaky_search(
        catalog: object,
        collection_id: str,
        bbox: tuple[float, float, float, float],
        *,
        datetime: str | None = None,
        max_cloud_cover: float | None = None,
    ) -> SearchResult:
        if collection_id == COLLECTION_LANDSAT and datetime == doomed.stac_datetime:
            raise RuntimeError("every scene in this window was cloud")
        return inner(
            catalog, collection_id, bbox, datetime=datetime, max_cloud_cover=max_cloud_cover
        )

    monkeypatch.setattr(pipeline, "search_collection", flaky_search)
    cube, records = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)

    for window in windows[:-1]:
        values = np.asarray(select_window(cube, window.label)["lst_c"].values)
        assert np.isfinite(values).any(), f"{window.label} lost its LST too"
    assert np.isnan(np.asarray(select_window(cube, doomed.label)["lst_c"].values)).all()

    # And the other sources for the doomed window still landed.
    assert np.isfinite(select_window(cube, doomed.label)["ndvi"].values).any()
    assert doomed.label not in {r.window for r in records if r.collection_id == COLLECTION_LANDSAT}


def test_observation_depth_is_measured_and_an_unobserved_pixel_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    grid: Grid,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scene count says nothing about whether a given pixel was ever seen clearly.

    Three scenes can still leave a pixel cloudy in all three, and the median composite
    reports that pixel as one NaN among 40,602 — which rounds to "100 % valid" in any
    report showing a single decimal. Depth is what makes it visible.
    """
    height, width = grid.shape
    coords = {"y": grid.y_coords(), "x": grid.x_coords()}
    n_scenes = 3
    cloud_bit = 1 << 3

    def load(
        items: list[pystac.Item],
        bands: tuple[str, ...],
        grid: Grid,
        *,
        resampling: str | dict[str, str],
        groupby: str | None = None,
        **kwargs: object,
    ) -> xr.Dataset:
        qa = np.zeros((n_scenes, height, width), dtype="uint16")
        qa[:, 0, 0] = cloud_bit  # cloudy in every scene -> never observed
        qa[:2, 0, 1] = cloud_bit  # cloudy in all but one -> a single look
        stack = {"time": np.arange(n_scenes), **coords}
        return xr.Dataset(
            {
                "lwir11": xr.DataArray(
                    np.full((n_scenes, height, width), 45_000.0, dtype="float32"),
                    dims=("time", "y", "x"),
                    coords=stack,
                ),
                "qa_pixel": xr.DataArray(qa, dims=("time", "y", "x"), coords=stack),
            }
        )

    monkeypatch.setattr(pipeline, "load_items", load)
    monkeypatch.setattr(
        pipeline, "search_collection", _fake_search({COLLECTION_LANDSAT: (n_scenes, n_scenes)})
    )

    with caplog.at_level(logging.WARNING):
        cube, records = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)

    landsat = [r for r in records if r.collection_id == COLLECTION_LANDSAT]
    assert landsat, "no Landsat record to carry the depth"
    for record in landsat:
        assert record.obs_depth_min == 0, "the always-cloudy pixel must show as unobserved"
        assert record.obs_depth_p50 == n_scenes, "the rest of the tile saw every scene"

    assert "no clear observation" in caplog.text

    # And the thing depth exists to expose: a real gap that coverage alone rounds away.
    lst = np.asarray(select_window(cube, cube["window"].values[0]).lst_c.values)
    assert np.isnan(lst[0, 0]), "a never-observed pixel must be NaN, not interpolated"
    assert not np.isnan(lst[0, 1]), "one clear look is still a measurement"


def test_implausible_temperatures_are_rejected(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, grid: Grid
) -> None:
    """A scaling regression should show up as missing data, not as a plausible-looking cube."""
    height, width = grid.shape
    coords = {"y": grid.y_coords(), "x": grid.x_coords()}

    def load(
        items: list[pystac.Item],
        bands: tuple[str, ...],
        grid: Grid,
        *,
        resampling: str | dict[str, str],
        groupby: str | None = None,
        **kwargs: object,
    ) -> xr.Dataset:
        return xr.Dataset(
            {
                # Raw Kelvin-scale DN left unscaled: ~1500 C if the scaling were skipped.
                "lwir11": xr.DataArray(
                    np.full((height, width), 500_000.0, dtype="float32"),
                    dims=("y", "x"),
                    coords=coords,
                ),
                "qa_pixel": xr.DataArray(
                    np.zeros((height, width), dtype="uint16"), dims=("y", "x"), coords=coords
                ),
            }
        )

    monkeypatch.setattr(pipeline, "load_items", load)
    monkeypatch.setattr(pipeline, "search_collection", _fake_search({COLLECTION_LANDSAT: (5, 5)}))

    # Enforcement lives in _align against the variable's declared valid_range, so this
    # goes through the assembly path rather than the ingestor alone.
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)

    assert np.isnan(cube["lst_c"].values).all()
    assert "lst_c" in summarise(cube, grid).missing


# --------------------------------------------------------------------------------
# Scene capping and failure isolation
# --------------------------------------------------------------------------------


def test_select_clearest_keeps_the_least_cloudy_scenes() -> None:
    result = SearchResult(
        collection_id=COLLECTION_SENTINEL2,
        items=[_item(COLLECTION_SENTINEL2, cloud=c) for c in (40.0, 5.0, 90.0, 1.0, 12.0)],
        n_found=5,
        max_cloud_cover=100.0,
    )

    capped = select_clearest(result, 3)

    clouds = [item.properties["eo:cloud_cover"] for item in capped.items]
    assert clouds == [1.0, 5.0, 12.0]
    # The pre-cap accounting must survive so the report can show both numbers.
    assert capped.n_found == 5


def test_select_clearest_is_a_no_op_below_the_limit() -> None:
    result = SearchResult(
        collection_id=COLLECTION_SENTINEL2,
        items=[_item(COLLECTION_SENTINEL2, cloud=1.0)],
        n_found=1,
    )
    assert select_clearest(result, 8).items == result.items
    assert select_clearest(result, 0).items == result.items


def test_scene_cap_is_reported_separately_from_the_cloud_filter(
    mocked: FakeLoader, grid: Grid
) -> None:
    """found > passed-cloud > composited must all be visible, not collapsed into one."""
    settings = Settings(max_cloud_cover=20.0, max_scenes_per_collection=2)
    _, records = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    s2 = next(r for r in records if r.collection_id == COLLECTION_SENTINEL2)

    assert (s2.n_found, s2.n_kept, s2.n_composited) == (30, 6, 2)


def test_a_lazy_read_failure_is_caught_and_isolated(
    mocked: FakeLoader, monkeypatch: pytest.MonkeyPatch, settings: Settings, grid: Grid
) -> None:
    """The regression that broke the first real build.

    odc-stac returns dask arrays, so a failed remote read raises when the array is
    *computed*, not when it is constructed. If materialisation happens outside the
    per-source guard, one collection's network failure kills the entire build. Here
    Sentinel-2 explodes only on compute; the other sources must still land.

    Takes `mocked` for the plain-HTTP stubs and then overrides the STAC ones on top.
    Without it this test stubbed only the STAC boundary and genuinely reached out to
    WorldPop, Open-Meteo and Overpass - it passed because those sources happened to
    succeed, which is exactly the kind of pass "no test touches the network" exists to
    prevent.
    """
    import dask
    import dask.array as da

    # Pin the synchronous scheduler for this test. Under the default threaded scheduler
    # the delayed exception surfaces from a worker thread, and which of the several
    # per-band tasks raises first is a race - the test passed alone and failed
    # intermittently in the full suite. What is under test is that build_cube catches a
    # compute-time failure at all, not how dask happens to schedule it.
    monkeypatch.setitem(dask.config.config, "scheduler", "synchronous")

    real_loader = FakeLoader(grid)

    def exploding_on_compute() -> np.ndarray:
        raise RuntimeError("TIFFReadEncodedTile() failed")

    def load(
        items: list[pystac.Item],
        bands: tuple[str, ...],
        grid: Grid,
        *,
        resampling: str | dict[str, str],
        groupby: str | None = None,
        **kwargs: object,
    ) -> xr.Dataset:
        if set(bands) >= {"B04", "B08"}:
            lazy = da.from_delayed(
                dask.delayed(exploding_on_compute)(), shape=grid.shape, dtype="float32"
            )
            coords = {"y": grid.y_coords(), "x": grid.x_coords()}
            return xr.Dataset(
                {
                    band: xr.DataArray(lazy, dims=("y", "x"), coords=coords)
                    for band in ("B02", "B04", "B08", "B11", "B12", "SCL")
                }
            )
        return real_loader(items, bands, grid, resampling=resampling, groupby=groupby)

    monkeypatch.setattr(pipeline, "load_items", load)
    monkeypatch.setattr(
        pipeline,
        "search_collection",
        _fake_search(
            {
                COLLECTION_SENTINEL2: (30, 6),
                COLLECTION_LANDSAT: (12, 4),
                COLLECTION_DEM: (1, 1),
                COLLECTION_WORLDCOVER: (1, 1),
            }
        ),
    )

    cube, records = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    summary = summarise(cube, grid)

    assert sorted(summary.missing) == ["albedo", "ndbi", "ndvi"]
    assert "lst_c" in summary.populated
    assert "elevation_m" in summary.populated
    assert COLLECTION_SENTINEL2 not in {r.collection_id for r in records}


def test_opening_the_catalog_survives_a_transient_dns_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build's first network call must be as retry-protected as the ingestors.

    It used to be the only one outside the retry logic, so a DNS blip at second zero
    aborted the build with a traceback and no report.
    """
    import pystac_client

    from terrarium.ingest import client

    calls: list[int] = []

    def flaky_open(url: str, modifier: object = None) -> pystac_client.Client:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("Failed to resolve 'planetarycomputer.microsoft.com'")
        return FAKE_CATALOG

    monkeypatch.setattr(pystac_client.Client, "open", flaky_open)
    monkeypatch.setattr(client, "sleep", lambda _: None)

    settings = Settings(ingest_attempts=3, ingest_retry_delay_s=0.0)

    assert client.open_catalog(settings) is FAKE_CATALOG
    assert len(calls) == 3


def test_opening_the_catalog_gives_up_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """No catalogue means nothing to build, so this must not fail quietly."""
    import pystac_client

    from terrarium.ingest import client

    def always_fails(url: str, modifier: object = None) -> pystac_client.Client:
        raise RuntimeError("permanently offline")

    monkeypatch.setattr(pystac_client.Client, "open", always_fails)
    monkeypatch.setattr(client, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="permanently offline"):
        client.open_catalog(Settings(ingest_attempts=2, ingest_retry_delay_s=0.0))


def test_gdal_configuration_is_applied() -> None:
    configure_gdal_for_cog_reads()
    # The setting that turns hundreds of container listings into zero.
    assert os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] == "EMPTY_DIR"
    assert int(os.environ["GDAL_HTTP_MAX_RETRY"]) >= 1
    # A stalled read must be bounded. Without a timeout the per-source retry never gets
    # to run - one real build hung 16.4 hours inside a single attempt.
    assert 0 < int(os.environ["GDAL_HTTP_TIMEOUT"]) <= 600
    assert 0 < int(os.environ["GDAL_HTTP_CONNECTTIMEOUT"]) <= 60


def test_a_transient_failure_is_retried_and_succeeds() -> None:
    """A DNS blip on the first attempt must not cost us the variable."""
    grid = grid_for_tile(TEST_TILE)
    settings = Settings(ingest_attempts=3, ingest_retry_delay_s=0.0)
    calls: list[int] = []

    def flaky() -> tuple[dict[str, xr.DataArray], SourceRecord]:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("Could not resolve host: sentinel2l2a01.blob.core.windows.net")
        return {"ndvi": grid.empty(fill=0.5)}, SourceRecord(
            collection_id=COLLECTION_SENTINEL2, n_found=1, n_kept=1, n_composited=1
        )

    outcome = pipeline._ingest_with_retries("s2", flaky, settings, grid)

    assert outcome is not None
    arrays, record = outcome
    assert len(calls) == 3
    assert np.allclose(arrays["ndvi"].values, 0.5)
    assert record.collection_id == COLLECTION_SENTINEL2


def test_retries_are_bounded_and_then_give_up() -> None:
    grid = grid_for_tile(TEST_TILE)
    settings = Settings(ingest_attempts=2, ingest_retry_delay_s=0.0)
    calls: list[int] = []

    def always_fails() -> tuple[dict[str, xr.DataArray], SourceRecord]:
        calls.append(1)
        raise RuntimeError("permanently broken")

    outcome = pipeline._ingest_with_retries("s2", always_fails, settings, grid)

    assert outcome is None
    assert len(calls) == 2


def test_retry_backoff_is_exponential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flat delays exhaust every attempt inside one outage; doubling rides it out."""
    grid = grid_for_tile(TEST_TILE)
    settings = Settings(ingest_attempts=4, ingest_retry_delay_s=10.0)
    slept: list[float] = []
    monkeypatch.setattr(pipeline, "sleep", slept.append)

    def always_fails() -> tuple[dict[str, xr.DataArray], SourceRecord]:
        raise RuntimeError("Could not resolve host")

    assert pipeline._ingest_with_retries("s2", always_fails, settings, grid) is None
    # Three sleeps for four attempts, doubling each time.
    assert slept == [10.0, 20.0, 40.0]


class _ShortResponse:
    """A response that promises 1000 bytes and delivers 100, then stops. No exception."""

    def __init__(self) -> None:
        self.headers = {"Content-Length": "1000"}
        self._sent = False

    def read(self, _size: int) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return b"x" * 100

    def __enter__(self) -> _ShortResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_a_truncated_download_is_rejected_rather_than_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The failure that actually happened while building this: a short read.

    WorldPop's server drops connections mid-transfer, and a short read raises nothing —
    you get a valid-looking GeoTIFF missing its bottom rows. If that were renamed into
    place, every later build would reuse a population layer with a hole in it and never
    notice, because the only check would be "does the file exist".
    """
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: _ShortResponse())

    target = tmp_path / "worldpop.tif"
    with pytest.raises(OSError, match="truncated"):
        pipeline._download_once("http://example.invalid/x.tif", target, 1.0)

    assert not target.exists(), "a truncated download must not become a cache hit"
    assert not target.with_suffix(".tif.partial").exists()


def test_a_complete_download_is_cached_and_not_refetched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """33 MB of static raster should be fetched once, not once per build."""
    target = tmp_path / "worldpop.tif"
    target.write_bytes(b"already here")

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("cached file was re-downloaded")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    assert pipeline._download_once("http://example.invalid/x.tif", target, 1.0) == target


class _CompleteResponse:
    """A response that delivers exactly what it promised."""

    def __init__(self, size: int = 1000) -> None:
        self.headers = {"Content-Length": str(size)}
        self._payload: bytes | None = b"x" * size

    def read(self, _size: int) -> bytes:
        payload, self._payload = self._payload, None
        return payload or b""

    def __enter__(self) -> _CompleteResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_a_truncated_download_recovers_on_the_next_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rejecting a short read is only half the guard — the retry has to be able to win.

    A rejected download that left a stale `.partial` behind, or that somehow poisoned the
    target path, would turn one transient truncation into a permanently failing build.
    This is the composition the retry wrapper actually relies on, and it was previously
    tested only as far as the rejection.
    """
    responses = iter([_ShortResponse(), _CompleteResponse()])
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: next(responses))
    target = tmp_path / "worldpop.tif"
    url = "http://example.invalid/x.tif"

    with pytest.raises(OSError, match="truncated"):
        pipeline._download_once(url, target, 1.0)

    # Exactly what _ingest_with_retries does next.
    assert pipeline._download_once(url, target, 1.0) == target
    assert target.read_bytes() == b"x" * 1000


class _NoLengthResponse(_CompleteResponse):
    """A server using chunked encoding: no Content-Length to check against."""

    def __init__(self) -> None:
        super().__init__()
        self.headers = {}


def test_a_download_without_content_length_says_it_could_not_be_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Silence here would mean a passing build implying a check that never ran."""
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: _NoLengthResponse())
    target = tmp_path / "worldpop.tif"

    with caplog.at_level(logging.WARNING):
        assert pipeline._download_once("http://example.invalid/x.tif", target, 1.0) == target

    assert target.exists(), "an unverifiable download is still the best we have"
    assert "Content-Length" in caplog.text


# --------------------------------------------------------------------------------
# WorldCover epoch handling
# --------------------------------------------------------------------------------


def _dated_item(collection_id: str, year: int) -> pystac.Item:
    return pystac.Item(
        id=f"{collection_id}-{year}",
        geometry=None,
        bbox=list(TEST_TILE.bbox),
        datetime=datetime(year, 1, 1, tzinfo=UTC),
        properties={},
        collection=collection_id,
    )


def test_newest_epoch_discards_older_worldcover_maps() -> None:
    """v100 (2020) and v200 (2021) must never be mixed into one land-cover snapshot."""
    result = SearchResult(
        collection_id=COLLECTION_WORLDCOVER,
        items=[
            _dated_item(COLLECTION_WORLDCOVER, 2020),
            _dated_item(COLLECTION_WORLDCOVER, 2021),
            _dated_item(COLLECTION_WORLDCOVER, 2021),
        ],
        n_found=3,
    )

    newest = pipeline._newest_epoch(result)

    assert len(newest.items) == 2
    assert {i.datetime.year for i in newest.items if i.datetime} == {2021}
    assert newest.n_found == 3


def test_landcover_survives_a_multi_epoch_time_dimension(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, grid: Grid
) -> None:
    """Regression: reducing this with ffill needed the optional `bottleneck` package.

    The first real build failed here with ModuleNotFoundError. Selecting a slice must not
    depend on an optional accelerator, and must not blend class codes.
    """
    height, width = grid.shape
    coords = {"y": grid.y_coords(), "x": grid.x_coords()}

    def load(
        items: list[pystac.Item],
        bands: tuple[str, ...],
        grid: Grid,
        *,
        resampling: str | dict[str, str],
        groupby: str | None = None,
        **kwargs: object,
    ) -> xr.Dataset:
        stacked = np.stack(
            [
                np.full((height, width), 10, dtype="uint8"),
                np.full((height, width), 50, dtype="uint8"),
            ]
        )
        return xr.Dataset(
            {
                "map": xr.DataArray(
                    stacked, dims=("time", "y", "x"), coords={"time": [0, 1], **coords}
                )
            }
        )

    monkeypatch.setattr(pipeline, "load_items", load)
    monkeypatch.setattr(
        pipeline, "search_collection", _fake_search({COLLECTION_WORLDCOVER: (2, 2)})
    )

    arrays, _ = pipeline._ingest_worldcover(FAKE_CATALOG, settings, grid)
    values = np.unique(arrays["landcover"].values)

    # The last slice, untouched - not a blend of 10 and 50.
    assert values.tolist() == [50]
    assert arrays["landcover"].dtype == np.uint8


# --------------------------------------------------------------------------------
# Physical plausibility
# --------------------------------------------------------------------------------


def test_every_variable_declares_a_valid_range() -> None:
    for spec in CUBE_VARIABLES:
        low, high = spec.valid_range
        assert low < high, spec.name
    # Normalised differences are mathematically bounded; the spec must say so.
    assert VARIABLES_BY_NAME["ndvi"].valid_range == (-1.0, 1.0)
    assert VARIABLES_BY_NAME["ndbi"].valid_range == (-1.0, 1.0)


def test_enforce_valid_range_drops_continuous_outliers() -> None:
    spec = VARIABLES_BY_NAME["ndvi"]
    data = xr.DataArray(np.array([[-2.364, 0.5, 1.201, np.nan]], dtype="float32"), dims=("y", "x"))

    cleaned, n_dropped = enforce_valid_range(data, spec)

    # Two impossible values dropped; the pre-existing NaN is not counted as a drop.
    assert n_dropped == 2
    assert np.isnan(cleaned.values[0, 0])
    assert cleaned.values[0, 1] == pytest.approx(0.5)
    assert np.isnan(cleaned.values[0, 2])


def test_enforce_valid_range_uses_the_sentinel_for_categoricals() -> None:
    """uint8 cannot hold NaN, so land cover falls back to its declared fill value."""
    spec = VARIABLES_BY_NAME["landcover"]
    data = xr.DataArray(np.array([[10, 50, 200, 0]], dtype="uint8"), dims=("y", "x"))

    cleaned, n_dropped = enforce_valid_range(data, spec)

    assert cleaned.dtype == np.uint8
    assert cleaned.values.tolist() == [[10, 50, 0, 0]]
    assert n_dropped == 2  # 200 is not a class; 0 is already nodata


def test_negative_reflectance_cannot_push_ndvi_out_of_bounds(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, grid: Grid
) -> None:
    """The regression the first real build exposed: NDVI reached -2.364.

    A DN below the 1000 baseline offset maps to negative reflectance. Screening the raw DN
    (> 0) let those through, and (nir-red)/(nir+red) is only bounded to [-1, 1] when both
    terms are positive. Here red is a legitimate 0.05 while nir is DN 400 -> -0.06
    reflectance, which under the old code gave (-0.06-0.05)/(-0.06+0.05) = +11.
    """
    height, width = grid.shape
    coords = {"y": grid.y_coords(), "x": grid.x_coords()}

    def const(value: float, dtype: str = "float32") -> xr.DataArray:
        return xr.DataArray(
            np.full((height, width), value, dtype=dtype), dims=("y", "x"), coords=coords
        )

    def load(
        items: list[pystac.Item],
        bands: tuple[str, ...],
        grid: Grid,
        *,
        resampling: str | dict[str, str],
        groupby: str | None = None,
        **kwargs: object,
    ) -> xr.Dataset:
        return xr.Dataset(
            {
                "B02": const(1200.0),
                "B04": const(1500.0),
                "B08": const(400.0),  # below the offset -> negative reflectance
                "B11": const(2000.0),
                "B12": const(1500.0),
                "SCL": const(4, dtype="uint8"),
            }
        )

    monkeypatch.setattr(pipeline, "load_items", load)
    monkeypatch.setattr(pipeline, "search_collection", _fake_search({COLLECTION_SENTINEL2: (5, 5)}))

    arrays, _ = pipeline._ingest_sentinel2(FAKE_CATALOG, settings, grid, settings.windows[0])
    ndvi = np.asarray(arrays["ndvi"].values)

    # The unphysical NIR is discarded outright rather than producing a bogus index.
    assert np.isnan(ndvi).all()


def test_valid_reflectance_still_produces_in_bounds_indices(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    """The screening must not be so aggressive that legitimate data is thrown away."""
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    clear = np.s_[:, :, grid.shape[1] // 2 :]

    for name in ("ndvi", "ndbi"):
        values = np.asarray(cube[name].values)[clear]
        assert np.isfinite(values).all()
        assert values.min() >= -1.0
        assert values.max() <= 1.0
