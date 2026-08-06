"""Score the air core against OpenAQ monitors, leaving one station out at a time.

    uv run python scripts/validate_air.py --window 2024-winter

**OpenAQ v3 needs a free API key.** v2 was retired, and v3 authenticates every request.
It costs nothing and needs no card, so it stays inside the zero-budget rule (D13), but it
is the one source in this project that is not keyless - so this script says so and stops
rather than half-validating:

    export TERRARIUM_OPENAQ_KEY=...        # https://explore.openaq.org/register

### What is actually being tested

The core models **this tile's own contribution** to PM2.5 - its roads and its kilns - and
nothing else. A monitor measures that plus a regional background that is often the larger
part. So the comparison is affine, `observed = scale x modelled + background`, and the two
fitted terms mean different things:

- **background** is the concentration the tile did not produce. Large is expected.
- **scale** is the correction the emission inventory needs. It is the calibration knob for
  `FLEET_PM25_G_PER_VEH_KM` and `KILN_PM25_G_S` in `ingest/osm.py`, which are literature
  figures rather than measurements of Lahore's fleet.

Leaving one station out is what stops that fit from being circular. And the fit is scored
against a **null model** - predict each station from the mean of the others - because with
a handful of monitors in one city, a model that merely reproduces the city mean can post a
respectable error while resolving nothing spatial. `beats_null` is the only claim here
worth quoting.

Expect a small N. Lahore has few public monitors, and OpenAQ may return fewer than the
four this needs, in which case the script says what it can and refuses to call it
validation.

**The request/response handling below is written against the v3 documentation and has
never been exercised against the live API**, because no key has been set. The scoring it
feeds — `cores.air.leave_one_station_out` — is tested; this fetch is not. Expect to fix
field names on first run rather than trusting it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from pyproj import Transformer

from terrarium.config import get_settings
from terrarium.cores.air import (
    AirParameters,
    concentration,
    leave_one_station_out,
    season_of,
)
from terrarium.cores.thermal.simulate import canopy_fraction
from terrarium.state.cube import select_window, window_labels
from terrarium.state.grid import grid_for_tile
from terrarium.state.store import open_cube

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("validate_air")

PM25_PARAMETER_ID = 2  # OpenAQ's parameter id for pm25
WGS84 = "EPSG:4326"


def _get(url: str, key: str, timeout_s: float) -> dict:
    request = urllib.request.Request(url, headers={"X-API-Key": key})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return dict(json.load(response))


def fetch_stations(settings) -> list[dict]:
    """PM2.5 monitors inside the tile, with their latest measured value.

    Latest, not a window mean: OpenAQ's aggregate endpoints are inconsistently populated
    for Pakistani stations, and a validation that silently compares one station's 2024
    winter to another's last Tuesday is worse than one that admits it compared the most
    recent readings. This is a *snapshot* comparison and the report says so.
    """
    west, south, east, north = settings.tile.bbox
    query = urllib.parse.urlencode(
        {
            "bbox": f"{west},{south},{east},{north}",
            "parameters_id": PM25_PARAMETER_ID,
            "limit": 100,
        }
    )
    payload = _get(f"{settings.openaq_url}/locations?{query}", settings.openaq_key,
                   settings.http_timeout_s)

    stations: list[dict] = []
    for location in payload.get("results", []):
        coords = location.get("coordinates") or {}
        for sensor in location.get("sensors", []):
            if (sensor.get("parameter") or {}).get("id") != PM25_PARAMETER_ID:
                continue
            latest = _get(
                f"{settings.openaq_url}/sensors/{sensor['id']}",
                settings.openaq_key,
                settings.http_timeout_s,
            )
            for result in latest.get("results", []):
                value = (result.get("latest") or {}).get("value")
                if value is None:
                    continue
                stations.append(
                    {
                        "name": str(location.get("name", location.get("id"))),
                        "lon": float(coords["longitude"]),
                        "lat": float(coords["latitude"]),
                        "observed": float(value),
                    }
                )
                break
    return stations


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zarr", type=Path, default=settings.serve_zarr_store)
    parser.add_argument("--window", default=None, help="default: the latest winter window")
    args = parser.parse_args()

    if not settings.openaq_key:
        print(
            "TERRARIUM_OPENAQ_KEY is not set. OpenAQ v3 authenticates every request - the "
            "key is free and needs no card (https://explore.openaq.org/register), but "
            "without it there is nothing to validate against.",
            file=sys.stderr,
        )
        return 1
    if not args.zarr.exists():
        print(f"no cube at {args.zarr}", file=sys.stderr)
        return 1

    grid = grid_for_tile(settings.tile)
    cube = open_cube(args.zarr)
    labels = window_labels(cube)
    # Winter by default: the inversion season is when Lahore's monitors read high enough
    # for a local increment to be distinguishable from the noise on the background.
    label = args.window or next(
        (w for w in reversed(labels) if w.endswith("winter")), labels[-1]
    )
    window = select_window(cube, label)

    stations = fetch_stations(settings)
    print(f"\nOpenAQ: {len(stations)} PM2.5 station(s) inside the tile\n")
    if not stations:
        print("Nothing to validate against. Lahore's public monitor coverage is thin.")
        return 1

    params = AirParameters.for_season(season_of(window))
    field = concentration(
        np.asarray(window["pm25_emission_g_s"].values, dtype="float64"),
        canopy_fraction({n: np.asarray(window[n].values) for n in ("ndvi", "landcover")}),
        params,
        wind_speed_ms=float(np.asarray(window["wind_speed_ms"].values).reshape(-1)[0]),
        wind_direction_deg=float(np.asarray(window["wind_direction_deg"].values).reshape(-1)[0]),
        resolution_m=float(grid.resolution_m),
    )

    to_grid = Transformer.from_crs(WGS84, grid.crs, always_xy=True).transform
    left, _, _, top = grid.bounds
    modelled: list[float] = []
    observed: list[float] = []
    names: list[str] = []
    for station in stations:
        x, y = to_grid(station["lon"], station["lat"])
        col = int((x - left) // grid.resolution_m)
        row = int((top - y) // grid.resolution_m)
        if not (0 <= row < grid.shape[0] and 0 <= col < grid.shape[1]):
            logger.warning("%s falls outside the grid; skipping", station["name"])
            continue
        modelled.append(float(field[row, col]))
        observed.append(station["observed"])
        names.append(station["name"])

    print(f"  window {label} ({season_of(window)}), mixing height {params.mixing_height_m:.0f} m")
    print(f"  {'station':<34} {'modelled':>10} {'observed':>10}")
    for name, m, o in zip(names, modelled, observed, strict=True):
        print(f"  {name:<34} {m:>10.2f} {o:>10.1f}")

    try:
        report = leave_one_station_out(np.array(modelled), np.array(observed), names)
    except ValueError as exc:
        print(f"\n  Not validation: {exc}")
        return 1

    print(f"\n  scale x{report.scale:.1f}   background {report.background_ugm3:.0f} ug/m3")
    print(f"  leave-one-out MAE {report.mae:.1f} ug/m3   null (mean of others) "
          f"{report.null_mae:.1f} ug/m3")
    print(f"  beats the null model: {report.beats_null}")
    print(
        "\n  Read this honestly: the observations are each station's latest reading, not "
        f"a {label} mean, and the local increment is a small part of what a monitor sees."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
