"""Tests for the ingest pipeline.

Nothing here touches the network. STAC search and asset loading are both replaced with
synthetic fixtures, so what is under test is exactly the part we own: resampling method
selection, reprojection onto the canonical grid, QA masking, and unit conversion.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple, cast

import numpy as np
import pystac
import pytest
import xarray as xr
from odc.geo.geobox import GeoBox
from odc.geo.xr import wrap_xr, xr_reproject

from terrarium.config import (
    COLLECTION_DEM,
    COLLECTION_LANDSAT,
    COLLECTION_SENTINEL2,
    COLLECTION_WORLDCOVER,
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
    empty_cube,
    enforce_valid_range,
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


@pytest.fixture
def grid() -> Grid:
    return grid_for_tile(TEST_TILE)


@pytest.fixture
def settings() -> Settings:
    # No retries and no delay: retry behaviour gets its own tests, and paying the real
    # backoff in every failure test would make the suite slow for no extra coverage.
    return Settings(
        search_start="2024-04-01",
        search_end="2024-06-30",
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


def test_every_cube_variable_declares_a_resampling_method() -> None:
    for spec in CUBE_VARIABLES:
        assert spec.resampling in (Resampling.NEAREST, Resampling.BILINEAR)
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


@pytest.fixture
def mocked(monkeypatch: pytest.MonkeyPatch, grid: Grid) -> FakeLoader:
    loader = FakeLoader(grid)
    counts = {
        COLLECTION_SENTINEL2: (30, 6),
        COLLECTION_LANDSAT: (12, 4),
        COLLECTION_DEM: (2, 2),
        COLLECTION_WORLDCOVER: (1, 1),
    }
    monkeypatch.setattr(pipeline, "load_items", loader)
    monkeypatch.setattr(pipeline, "search_collection", _fake_search(counts))
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
    }


def test_scene_counts_survive_to_the_report(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    _, records = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    by_id = {r.collection_id: r for r in records}

    assert by_id[COLLECTION_SENTINEL2].n_found == 30
    assert by_id[COLLECTION_SENTINEL2].n_kept == 6
    assert by_id[COLLECTION_SENTINEL2].max_cloud_cover == 20.0
    # The DEM is static: it must not be cloud-filtered.
    assert by_id[COLLECTION_DEM].max_cloud_cover is None


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
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    width = grid.shape[1]

    for name in ("ndvi", "lst_c"):
        values = np.asarray(cube[name].values)
        assert np.isnan(values[:, : width // 2]).all(), f"{name}: cloudy half not masked"
        assert np.isfinite(values[:, width // 2 :]).all(), f"{name}: clear half was masked"


def test_sentinel2_indices_use_baseline_corrected_reflectance(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    clear = slice(grid.shape[1] // 2, None)

    assert np.allclose(cube["ndvi"].values[:, clear], EXPECTED_NDVI, atol=1e-5)
    assert np.allclose(cube["ndbi"].values[:, clear], EXPECTED_NDBI, atol=1e-5)
    assert np.allclose(cube["albedo"].values[:, clear], EXPECTED_ALBEDO, atol=1e-5)


def test_landsat_digital_numbers_become_celsius(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    clear = slice(grid.shape[1] // 2, None)

    assert np.allclose(cube["lst_c"].values[:, clear], LST_TARGET_C, atol=0.01)


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
    monkeypatch: pytest.MonkeyPatch, settings: Settings, grid: Grid
) -> None:
    """The regression that broke the first real build.

    odc-stac returns dask arrays, so a failed remote read raises when the array is
    *computed*, not when it is constructed. If materialisation happens outside the
    per-source guard, one collection's network failure kills the entire build. Here
    Sentinel-2 explodes only on compute; the other three sources must still land.
    """
    import dask
    import dask.array as da

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
    assert sorted(summary.populated) == ["elevation_m", "landcover", "lst_c"]
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


def test_a_transient_failure_is_retried_and_succeeds() -> None:
    """A DNS blip on the first attempt must not cost us the variable."""
    grid = grid_for_tile(TEST_TILE)
    settings = Settings(ingest_attempts=3, ingest_retry_delay_s=0.0)
    calls: list[int] = []

    def flaky(
        catalog: pystac_client.Client, settings_: Settings, grid_: Grid
    ) -> tuple[dict[str, xr.DataArray], SourceRecord]:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("Could not resolve host: sentinel2l2a01.blob.core.windows.net")
        return {"ndvi": grid_.empty(fill=0.5)}, SourceRecord(
            collection_id=COLLECTION_SENTINEL2, n_found=1, n_kept=1, n_composited=1
        )

    cube = empty_cube(grid)
    outcome = pipeline._ingest_with_retries("s2", flaky, FAKE_CATALOG, settings, grid, cube)

    assert outcome is not None
    arrays, record = outcome
    assert len(calls) == 3
    assert np.allclose(arrays["ndvi"].values, 0.5)
    assert record.collection_id == COLLECTION_SENTINEL2


def test_retries_are_bounded_and_then_give_up() -> None:
    grid = grid_for_tile(TEST_TILE)
    settings = Settings(ingest_attempts=2, ingest_retry_delay_s=0.0)
    calls: list[int] = []

    def always_fails(
        catalog: pystac_client.Client, settings_: Settings, grid_: Grid
    ) -> tuple[dict[str, xr.DataArray], SourceRecord]:
        calls.append(1)
        raise RuntimeError("permanently broken")

    cube = empty_cube(grid)
    outcome = pipeline._ingest_with_retries("s2", always_fails, FAKE_CATALOG, settings, grid, cube)

    assert outcome is None
    assert len(calls) == 2


def test_retry_backoff_is_exponential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flat delays exhaust every attempt inside one outage; doubling rides it out."""
    grid = grid_for_tile(TEST_TILE)
    settings = Settings(ingest_attempts=4, ingest_retry_delay_s=10.0)
    slept: list[float] = []
    monkeypatch.setattr(pipeline, "sleep", slept.append)

    def always_fails(
        catalog: pystac_client.Client, settings_: Settings, grid_: Grid
    ) -> tuple[dict[str, xr.DataArray], SourceRecord]:
        raise RuntimeError("Could not resolve host")

    assert (
        pipeline._ingest_with_retries(
            "s2", always_fails, FAKE_CATALOG, settings, grid, empty_cube(grid)
        )
        is None
    )
    # Three sleeps for four attempts, doubling each time.
    assert slept == [10.0, 20.0, 40.0]


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
    monkeypatch.setattr(
        pipeline, "search_collection", _fake_search({COLLECTION_SENTINEL2: (5, 5)})
    )

    arrays, _ = pipeline._ingest_sentinel2(FAKE_CATALOG, settings, grid)
    ndvi = np.asarray(arrays["ndvi"].values)

    # The unphysical NIR is discarded outright rather than producing a bogus index.
    assert np.isnan(ndvi).all()


def test_valid_reflectance_still_produces_in_bounds_indices(
    mocked: FakeLoader, settings: Settings, grid: Grid
) -> None:
    """The screening must not be so aggressive that legitimate data is thrown away."""
    cube, _ = pipeline.build_cube(catalog=FAKE_CATALOG, settings=settings, grid=grid)
    clear = slice(grid.shape[1] // 2, None)

    for name in ("ndvi", "ndbi"):
        values = np.asarray(cube[name].values)[:, clear]
        assert np.isfinite(values).all()
        assert values.min() >= -1.0
        assert values.max() <= 1.0
