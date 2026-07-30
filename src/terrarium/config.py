"""Central configuration.

Every constant that describes *where* and *at what resolution* Terrarium operates lives
here. Nothing else in the codebase should hardcode a bounding box, CRS, or pixel size.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


class Tile(BaseModel):
    """The single geographic tile v1 operates on.

    v1 scope: this is hardcoded and there is exactly one. No tile selection, no
    multi-city support. See CLAUDE.md > Scope for v1.
    """

    name: str
    country: str
    # WGS84 degrees, in STAC/GeoJSON order: [west, south, east, north]
    bbox: tuple[float, float, float, float]
    # Metric CRS used for all analysis. Everything is reprojected into this.
    crs: str
    # THE analysis grid resolution, in metres. Every source is resampled *into* this,
    # and every physics core assumes it. Not to be confused with any source's native
    # resolution (see NATIVE_RESOLUTION_M).
    target_resolution_m: int = 100

    @computed_field  # type: ignore[prop-decorator]
    @property
    def centroid(self) -> tuple[float, float]:
        """(longitude, latitude) of the tile centre."""
        west, south, east, north = self.bbox
        return ((west + east) / 2, (south + north) / 2)


# ~20 km x 20 km centred on 31.5204 N, 74.3587 E.
LAHORE = Tile(
    name="Lahore",
    country="PK",
    bbox=(74.2533, 31.4305, 74.4641, 31.6103),
    crs="EPSG:32643",  # UTM zone 43N
    target_resolution_m=100,
)

ACTIVE_TILE = LAHORE

# --------------------------------------------------------------- data sources ---

# Planetary Computer collection IDs. See CLAUDE.md > Data source.
COLLECTION_SENTINEL2 = "sentinel-2-l2a"
COLLECTION_LANDSAT = "landsat-c2-l2"
COLLECTION_DEM = "cop-dem-glo-30"
COLLECTION_WORLDCOVER = "esa-worldcover"

# Native ground sample distance of each source, in metres, *before* resampling onto
# the target grid. Kept separate from Tile.target_resolution_m so the two concepts
# never collide: these describe the inputs, that describes the output.
NATIVE_RESOLUTION_M: dict[str, int] = {
    COLLECTION_SENTINEL2: 10,  # visible / NIR bands
    COLLECTION_LANDSAT: 30,  # optical; ST_B10 is delivered resampled to 30 m
    COLLECTION_DEM: 30,  # Copernicus DEM GLO-30
    COLLECTION_WORLDCOVER: 10,  # ESA WorldCover
}

DEM_NATIVE_RESOLUTION_M = NATIVE_RESOLUTION_M[COLLECTION_DEM]


class Settings(BaseSettings):
    """Runtime settings. Override via environment or a local `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TERRARIUM_",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # Vite dev server. Needed so the browser can call the API cross-origin.
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # STAC catalogue. See CLAUDE.md > Data source.
    stac_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"

    # Search window for imagery, as a STAC datetime interval. Lahore's pre-monsoon
    # dry season: hot, clear, and the period the thermal core actually cares about.
    search_start: str = "2024-04-01"
    search_end: str = "2024-06-30"
    # Scene-level cloud cover ceiling, percent. Applied as a STAC query filter.
    max_cloud_cover: float = 20.0
    # Cap on scenes composited per collection, least-cloudy first.
    #
    # Lahore's dry season yields ~49 usable Sentinel-2 scenes; compositing all of them
    # means ~300 COG reads and over an hour of warping, during which the Planetary
    # Computer SAS tokens minted at search time expire and the load fails mid-flight. A
    # median over the clearest handful is both faster and no less representative.
    max_scenes_per_collection: int = 8
    # Attempts per collection before giving up and leaving its variables unpopulated.
    # Planetary Computer reads fail transiently often enough (DNS blips, dropped TLS
    # connections mid-tile) that a single attempt makes builds needlessly flaky.
    # Delay doubles each attempt: 10s, 20s, 40s. Flat short delays burn every attempt
    # inside a single DNS flap, which defeats the point of retrying at all.
    ingest_attempts: int = 4
    ingest_retry_delay_s: float = 10.0

    zarr_store: Path = DATA_DIR / "processed" / "cube.zarr"
    duckdb_path: Path = DATA_DIR / "processed" / "terrarium.duckdb"

    @property
    def tile(self) -> Tile:
        return ACTIVE_TILE


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Use this as a FastAPI dependency."""
    return Settings()
