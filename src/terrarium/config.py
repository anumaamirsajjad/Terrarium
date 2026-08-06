"""Central configuration.

Every constant that describes *where* and *at what resolution* Terrarium operates lives
here. Nothing else in the codebase should hardcode a bounding box, CRS, or pixel size.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field
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

# ------------------------------------------------------------ seasonal windows ---


class Season(StrEnum):
    """The two windows per year the cube composites over (D10).

    Summer is what the thermal core cares about — the pre-monsoon heat peak. Winter is
    ingested now rather than later because it is when Lahore's inversion traps pollution,
    so the air core lands without forcing a second expansion of the ingest.
    """

    SUMMER = "summer"
    WINTER = "winter"


# (start month, start day, end month, end day). Winter's end month is *lower* than its
# start month, which is how `season_windows` knows it runs into the following year.
SEASON_SPANS: dict[Season, tuple[int, int, int, int]] = {
    Season.SUMMER: (4, 1, 6, 30),  # Apr-Jun: pre-monsoon, hot and clear
    Season.WINTER: (11, 1, 1, 31),  # Nov-Jan: inversion season
}


class SeasonWindow(BaseModel):
    """One composite window: a season of one year, and the dates it covers."""

    model_config = ConfigDict(frozen=True)

    label: str
    season: Season
    start: date
    end: date

    @property
    def stac_datetime(self) -> str:
        """The window as a STAC datetime interval."""
        return f"{self.start.isoformat()}/{self.end.isoformat()}"

    @property
    def midpoint(self) -> date:
        """The window's centre. This is the cube's `time` coordinate value.

        A composite has no single acquisition date, so the coordinate is deliberately the
        window's midpoint rather than any real overpass — the `window` coordinate carries
        the label you should actually cite.
        """
        return self.start + (self.end - self.start) / 2


def season_windows(years: Sequence[int]) -> tuple[SeasonWindow, ...]:
    """Every seasonal window for the given years, ordered by start date.

    Winter is labelled by the year it *starts* in: `2024-winter` is Nov 2024 to Jan 2025.
    Labelling it by the January would split one continuous cold season across two labels.
    """
    windows: list[SeasonWindow] = []
    for year in sorted(years):
        for season, (m0, d0, m1, d1) in SEASON_SPANS.items():
            end_year = year + 1 if m1 < m0 else year
            windows.append(
                SeasonWindow(
                    label=f"{year}-{season}",
                    season=season,
                    start=date(year, m0, d0),
                    end=date(end_year, m1, d1),
                )
            )
    return tuple(sorted(windows, key=lambda w: w.start))


# --------------------------------------------------------------- data sources ---

# Planetary Computer collection IDs. See CLAUDE.md > Data source.
COLLECTION_SENTINEL2 = "sentinel-2-l2a"
COLLECTION_LANDSAT = "landsat-c2-l2"
COLLECTION_DEM = "cop-dem-glo-30"
COLLECTION_WORLDCOVER = "esa-worldcover"

# Not STAC collections: these are plain HTTP, outside Planetary Computer. All keyless and
# free (D13). They are named here anyway so `VariableSpec.source_collection` stays a
# single vocabulary rather than sprouting a second field.
SOURCE_OPEN_METEO = "open-meteo"
SOURCE_WORLDPOP = "worldpop"
SOURCE_OSM = "osm-overpass"

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

    # Years to build seasonal windows for. Each year contributes a summer and a winter
    # window (D10), so this is 2 x len(window_years) time slices. Two years is the
    # minimum that makes the time dimension mean anything; more is a build-time cost,
    # not a code change.
    window_years: list[int] = Field(default=[2023, 2024])
    # Scene-level cloud cover ceiling, percent. Applied as a STAC query filter.
    max_cloud_cover: float = 20.0
    # Cap on scenes composited per collection *per window*, least-cloudy first.
    #
    # Compositing every usable scene means hundreds of COG reads and over an hour of
    # warping, during which the Planetary Computer SAS tokens minted at search time
    # expire and the load fails mid-flight. A median over the clearest handful is both
    # faster and no less representative. Raised from 8 now that windows are ~3 months
    # rather than one 3-month range standing in for the whole cube: a per-window cap of 8
    # was leaving most of a season's clear scenes on the floor.
    max_scenes_per_collection: int = 14
    # Attempts per collection before giving up and leaving its variables unpopulated.
    # Planetary Computer reads fail transiently often enough (DNS blips, dropped TLS
    # connections mid-tile) that a single attempt makes builds needlessly flaky.
    # Delay doubles each attempt: 10s, 20s, 40s. Flat short delays burn every attempt
    # inside a single DNS flap, which defeats the point of retrying at all.
    ingest_attempts: int = 4
    ingest_retry_delay_s: float = 10.0

    # Open-Meteo's ERA5 reanalysis archive. Keyless, free for non-commercial use.
    open_meteo_url: str = "https://archive-api.open-meteo.com/v1/archive"
    # WorldPop 2020 constrained (building-settlement-mapped), 100 m, Pakistan. 33 MB and
    # static, so it is downloaded once into data/raw and reused. The server does not
    # support HTTP range requests, which rules out reading it in place via /vsicurl.
    worldpop_url: str = (
        "https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained"
        "/2020/BSGM/PAK/pak_ppp_2020_constrained.tif"
    )
    worldpop_cache: Path = DATA_DIR / "raw" / "pak_ppp_2020_constrained.tif"
    # Ceiling on any single plain-HTTP fetch. The retry wrapper handles the rest.
    http_timeout_s: float = 300.0

    # Overpass, for the Phase 9 emission inventory: road centrelines and brick kilns.
    # Keyless and free, but rate-limited and frequently busy - the ingest retry wrapper is
    # what makes that survivable. `osmnx` was budgeted for and not taken: it would pull
    # networkx and a routing graph we never traverse, when what the inventory needs is
    # geometry and tags, which is one Overpass query and a histogram.
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # OpenAQ v3, for air validation. **Free but no longer keyless** - v2 was retired and v3
    # requires a (free, no-card) API key, so this is the one source that needs an env var.
    # scripts/validate_air.py says so and stops rather than half-validating.
    openaq_url: str = "https://api.openaq.org/v3"
    openaq_key: str | None = None

    # Where `scripts/build_tile.py` writes by default.
    zarr_store: Path = DATA_DIR / "processed" / "cube.zarr"
    duckdb_path: Path = DATA_DIR / "processed" / "terrarium.duckdb"

    # What the API *serves*, which is deliberately not what a build writes to. Keeping
    # them separate is what lets you rebuild without pointing the demo at a half-finished
    # cube: builds go to `zarr_store` (or `--out`), and this only moves once a build has
    # been checked. `cube.zarr` is currently a failed partial build - its 2024 windows are
    # empty - which is precisely the accident this split exists to survive.
    # Moved from cube_phase4.zarr in Phase 9, only after the new one was checked: 4
    # windows, `validate_windows` clean, and a worked intervention through both cores.
    # cube_phase4.zarr is still on disk and still good, but it predates
    # `pm25_emission_g_s` and `wind_direction_deg`, so the API refuses it - which is the
    # per-variable-window check doing exactly its job.
    serve_zarr_store: Path = DATA_DIR / "processed" / "cube_phase9.zarr"
    # LightGBM native text format, written by scripts/train_thermal.py.
    thermal_model_path: Path = DATA_DIR / "processed" / "thermal.txt"

    @property
    def tile(self) -> Tile:
        return ACTIVE_TILE

    @property
    def windows(self) -> tuple[SeasonWindow, ...]:
        """The cube's time axis: every seasonal window, ascending by start date."""
        return season_windows(self.window_years)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Use this as a FastAPI dependency."""
    return Settings()
