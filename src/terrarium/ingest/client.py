"""Planetary Computer STAC access.

This module is the network boundary. It searches the catalogue, signs asset URLs, and
hands back lazy xarray Datasets already snapped onto the canonical grid. It makes no
decisions about *which* variables the cube wants or how they should be masked — that is
`ingest.pipeline`'s job.

Reprojection happens here only because odc-stac fuses read + warp into a single pass;
the resampling *policy* is passed in by the caller.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

import odc.stac
import planetary_computer
import pystac
import pystac_client
import xarray as xr
from odc.geo.geobox import GeoBox
from pydantic import BaseModel, ConfigDict, Field

from terrarium.config import Settings
from terrarium.state.grid import Grid

logger = logging.getLogger(__name__)

# STAC property carrying scene-level cloud cover for both Sentinel-2 and Landsat.
CLOUD_COVER_PROPERTY = "eo:cloud_cover"


class SearchResult(BaseModel):
    """What a single collection search returned, with the cloud filter accounted for.

    `n_found` is the raw catalogue hit count for the bbox and date range; `items` is
    what survived the cloud filter. Both are reported so an empty cube can be diagnosed
    as "nothing there" versus "everything was cloudy".
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    collection_id: str
    items: list[pystac.Item] = Field(default_factory=list)
    n_found: int = 0
    max_cloud_cover: float | None = None

    @property
    def n_kept(self) -> int:
        return len(self.items)

    @property
    def cloud_covers(self) -> list[float]:
        """Cloud cover of the kept scenes, ascending. Empty if the property is absent."""
        values = [
            float(item.properties[CLOUD_COVER_PROPERTY])
            for item in self.items
            if CLOUD_COVER_PROPERTY in item.properties
        ]
        return sorted(values)


def configure_gdal_for_cog_reads() -> None:
    """Set GDAL/rasterio environment for remote COG reads.

    Without `GDAL_DISABLE_READDIR_ON_OPEN` every open triggers a blob-container listing,
    which dominates wall time when reading hundreds of assets. The retry settings matter
    because Planetary Computer occasionally drops connections mid-tile, and the default
    is zero retries — one blip otherwise fails the whole build.
    """
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
    os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
    os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
    os.environ.setdefault("VSI_CACHE", "TRUE")
    os.environ.setdefault("VSI_CACHE_SIZE", "67108864")
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.TIF,.tiff")


def select_clearest(result: SearchResult, limit: int) -> SearchResult:
    """Keep at most `limit` scenes, least cloudy first.

    Scenes without cloud metadata sort as perfectly clear, which is correct for the
    static collections (DEM, land cover) where the concept does not apply.
    """
    if limit <= 0 or len(result.items) <= limit:
        return result

    ranked = sorted(
        result.items,
        key=lambda item: float(item.properties.get(CLOUD_COVER_PROPERTY, 0.0)),
    )
    logger.info(
        "%s: compositing the %d clearest of %d usable scenes",
        result.collection_id,
        limit,
        len(result.items),
    )
    return result.model_copy(update={"items": ranked[:limit]})


def open_catalog(settings: Settings) -> pystac_client.Client:
    """Open the PC STAC catalogue with automatic SAS-token signing.

    `sign_inplace` as a modifier means every item that comes out of a search already has
    signed asset hrefs, so nothing downstream needs to know about tokens.
    """
    return pystac_client.Client.open(
        settings.stac_url,
        modifier=planetary_computer.sign_inplace,
    )


def search_collection(
    catalog: pystac_client.Client,
    collection_id: str,
    bbox: tuple[float, float, float, float],
    *,
    datetime: str | None = None,
    max_cloud_cover: float | None = None,
) -> SearchResult:
    """Search one collection and apply the cloud filter client-side.

    Filtering in Python rather than via a STAC `query` costs nothing at this scale and
    buys us the pre-filter count, which is the difference between a useful and a useless
    diagnostic when the cube comes back empty.
    """
    search = catalog.search(collections=[collection_id], bbox=bbox, datetime=datetime)
    found = list(search.items())

    if max_cloud_cover is None:
        kept = found
    else:
        kept = [
            item
            for item in found
            # A scene with no cloud metadata (e.g. the DEM) is never cloudy.
            if float(item.properties.get(CLOUD_COVER_PROPERTY, 0.0)) <= max_cloud_cover
        ]

    logger.info(
        "%s: %d found, %d kept (cloud <= %s)", collection_id, len(found), len(kept), max_cloud_cover
    )
    return SearchResult(
        collection_id=collection_id,
        items=kept,
        n_found=len(found),
        max_cloud_cover=max_cloud_cover,
    )


def geobox_for(grid: Grid) -> GeoBox:
    """Translate our Grid into the GeoBox odc-stac warps into.

    Anchoring on the grid's own snapped bounds — rather than asking odc-stac to derive a
    box from the bbox — is what guarantees every source lands on byte-identical
    coordinates.
    """
    return GeoBox.from_bbox(
        grid.bounds,
        crs=grid.crs,
        resolution=grid.resolution_m,
        tight=True,
    )


def load_items(
    items: list[pystac.Item],
    bands: tuple[str, ...],
    grid: Grid,
    *,
    resampling: str | dict[str, str],
    groupby: str | None = "solar_day",
    chunks: dict[str, int | Literal["auto"]] | None = None,
    dtype: str | None = None,
    nodata: float | None = None,
) -> xr.Dataset:
    """Load and warp STAC assets onto the canonical grid.

    Returns a lazy, dask-backed Dataset with dims ("time", "y", "x") — or ("y", "x") when
    `groupby` is None and a single item is loaded. Nothing is read from the network until
    the result is computed.
    """
    if not items:
        raise ValueError("load_items called with no items")

    extra: dict[str, Any] = {}
    if dtype is not None:
        extra["dtype"] = dtype
    if nodata is not None:
        extra["nodata"] = nodata

    return odc.stac.load(
        items,
        bands=bands,
        geobox=geobox_for(grid),
        resampling=resampling,
        groupby=groupby,
        chunks=chunks or {"x": 512, "y": 512},
        **extra,
    )
