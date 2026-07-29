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
    # Analysis grid resolution in metres. 30 m matches Landsat thermal natively.
    resolution_m: int = 30

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
    resolution_m=30,
)

ACTIVE_TILE = LAHORE


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

    zarr_store: Path = DATA_DIR / "processed" / "cube.zarr"
    duckdb_path: Path = DATA_DIR / "processed" / "terrarium.duckdb"

    @property
    def tile(self) -> Tile:
        return ACTIVE_TILE


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Use this as a FastAPI dependency."""
    return Settings()
