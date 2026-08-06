"""OpenStreetMap -> a PM2.5 emission inventory on the analysis grid.

Layer 1, so network I/O is allowed here and nowhere downstream. One Overpass query pulls
road centrelines and brick kilns for the tile; everything after that is arithmetic.

**This is an inventory, not a measurement.** Every number below is a literature-scale
factor applied to a geometry OSM happens to know about, and the two failure modes are
different: a missing road is a missing source, while a wrong emission factor is a wrong
*scale* on every source at once. Only the second is correctable after the fact, and
`scripts/validate_air.py` is what corrects it - the leave-one-station-out fit against
OpenAQ returns exactly the scale factor these constants got wrong.

`osmnx` was budgeted for in the plan and deliberately not taken. It builds a routable
graph, and an inventory does not route: it needs each way's geometry, its `highway` tag,
and length per grid cell. That is one HTTP POST and a 2-D histogram.

Two known biases, both absorbed by the calibration scale and neither worth code:

- **Divided carriageways count twice.** A motorway mapped as two one-way ways gets the full
  class traffic on each side. Systematic, and largest on exactly the roads that dominate
  the inventory.
- **OSM's road classes are a mapper's judgement**, not a traffic survey. `residential`
  covers a cul-de-sac and a busy through-street alike.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

import numpy as np
from pyproj import Transformer

from terrarium.state.grid import Grid

logger = logging.getLogger(__name__)

WGS84 = "EPSG:4326"

# --- the fleet mix ---------------------------------------------------------------
#
# Vehicles per day by road class, and the grams of PM2.5 an average vehicle emits per
# kilometre. Both are order-of-magnitude figures for a South Asian city fleet, not survey
# data for Lahore - AADT counts for these roads are not open, and the whole point of the
# OpenAQ fit is that this scale is the thing being calibrated.
#
# The *ratios* between classes are the part that has to be roughly right, because they are
# what makes one street differ from another and no single scale factor can fix them.
ROAD_VEHICLES_PER_DAY: dict[str, float] = {
    "motorway": 60_000.0,
    "trunk": 40_000.0,
    "primary": 25_000.0,
    "secondary": 12_000.0,
    "tertiary": 6_000.0,
    "residential": 1_200.0,
}
# Link roads (motorway_link etc.) are slip roads: same class, a fraction of the flow.
LINK_FLOW_SHARE = 0.3

# Grams of PM2.5 per vehicle-kilometre, fleet average. Exhaust plus brake, tyre and road
# wear, which together are roughly half of it and do not fall when a diesel is replaced by
# a petrol car - only when the vehicle stops driving. That matters for what a "ban
# combustion vehicles" intervention can honestly claim.
FLEET_PM25_G_PER_VEH_KM = 0.10

SECONDS_PER_DAY = 86_400.0

# Brick kilns. Lahore's are the classic winter source: seasonal, unregulated, and clustered
# in the peri-urban ring. A single fixed-chimney Bull's Trench kiln runs 1-3 g/s of PM2.5
# while firing; this is the low end, because OSM's kiln coverage is patchy and a
# generously-scaled inventory over a sparse point set is confidently wrong twice.
KILN_PM25_G_S = 1.0
KILN_TAGS: tuple[tuple[str, str], ...] = (
    ("man_made", "kiln"),
    ("industrial", "brick_kiln"),
    ("product", "brick"),
    ("product", "bricks"),
)

# How finely each way is sampled before binning, in metres. Half a cell: fine enough that
# a road crossing a cell corner lands mostly in the right cell, coarse enough that the
# whole tile's road network is a few hundred thousand points.
SAMPLE_SPACING_M = 50.0

OVERPASS_QUERY = """
[out:json][timeout:180];
(
  way["highway"~"^({classes})(_link)?$"]({south},{west},{north},{east});
  nwr["man_made"="kiln"]({south},{west},{north},{east});
  nwr["industrial"="brick_kiln"]({south},{west},{north},{east});
  nwr["product"~"^bricks?$"]({south},{west},{north},{east});
);
out geom;
"""


def build_query(bbox: tuple[float, float, float, float]) -> str:
    """The Overpass QL for one tile. `bbox` is GeoJSON order: west, south, east, north.

    Overpass wants (south, west, north, east), which is the *other* order — swapping them
    silently returns an empty result set rather than an error, so the conversion happens
    once, here.
    """
    west, south, east, north = bbox
    return OVERPASS_QUERY.format(
        classes="|".join(ROAD_VEHICLES_PER_DAY),
        south=south,
        west=west,
        north=north,
        east=east,
    )


def fetch_overpass(
    url: str, bbox: tuple[float, float, float, float], timeout_s: float
) -> dict[str, Any]:
    """POST one Overpass query and return the decoded JSON. The only I/O in this module."""
    query = build_query(bbox)
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": "terrarium/0.1 (neighbourhood digital twin)"},
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.load(response)

    elements = payload.get("elements", [])
    logger.info("overpass returned %d elements", len(elements))
    return dict(payload)


def _road_emission_g_s_per_m(tags: dict[str, str]) -> float:
    """Emission per metre of this way, or 0 if it is not a road we count."""
    highway = tags.get("highway", "")
    base = highway.removesuffix("_link")
    vehicles = ROAD_VEHICLES_PER_DAY.get(base)
    if vehicles is None:
        return 0.0
    if highway.endswith("_link"):
        vehicles *= LINK_FLOW_SHARE
    # veh/day * g/veh-km -> g/day per km -> g/s per metre.
    return vehicles * FLEET_PM25_G_PER_VEH_KM / SECONDS_PER_DAY / 1000.0


def _is_kiln(tags: dict[str, str]) -> bool:
    return any(tags.get(key) == value for key, value in KILN_TAGS)


def _element_points(element: dict[str, Any]) -> list[tuple[float, float]]:
    """(lon, lat) vertices of an element, however Overpass chose to express it."""
    if "geometry" in element:
        return [(float(p["lon"]), float(p["lat"])) for p in element["geometry"]]
    if "lon" in element and "lat" in element:
        return [(float(element["lon"]), float(element["lat"]))]
    bounds = element.get("bounds")
    if bounds:  # relations come back as a bounding box; its centre is enough for a point
        return [
            (
                (float(bounds["minlon"]) + float(bounds["maxlon"])) / 2,
                (float(bounds["minlat"]) + float(bounds["maxlat"])) / 2,
            )
        ]
    return []


def _densify(xs: np.ndarray, ys: np.ndarray, spacing_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Resample a projected polyline to points ~`spacing_m` apart, one per unit length.

    Returned points are *equally weighted*: each stands for the same length of road, which
    is what lets the caller bin them with a single weight instead of tracking segment
    lengths through the histogram.
    """
    segment_m = np.hypot(np.diff(xs), np.diff(ys))
    total = float(segment_m.sum())
    if total <= 0:
        return np.empty(0), np.empty(0)

    n = max(round(total / spacing_m), 1)
    # Distance along the line for each sample, at segment midpoints so a sample never sits
    # exactly on a cell boundary more often than chance.
    along = (np.arange(n) + 0.5) * (total / n)
    cumulative = np.concatenate([[0.0], np.cumsum(segment_m)])
    return (
        np.interp(along, cumulative, xs),
        np.interp(along, cumulative, ys),
    )


def emission_grid(payload: dict[str, Any], grid: Grid) -> np.ndarray:
    """Bin an Overpass response into g/s of PM2.5 per grid cell.

    Pure: JSON in, `(y, x)` float32 out. Cells with no source are 0.0, not NaN - the
    inventory covers the whole tile, and "no road here" is a real zero.
    """
    to_grid = Transformer.from_crs(WGS84, grid.crs, always_xy=True).transform
    height, width = grid.shape
    left, bottom, right, top = grid.bounds

    # Histogram edges in projected coordinates. y is built ascending (histogram2d needs
    # increasing edges) and the result is flipped at the end, because the grid's y
    # descends from north.
    x_edges = np.linspace(left, right, width + 1)
    y_edges = np.linspace(bottom, top, height + 1)

    totals = np.zeros((height, width), dtype="float64")
    n_roads = n_kilns = 0

    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        points = _element_points(element)
        if not points:
            continue

        lons, lats = (np.array(v, dtype="float64") for v in zip(*points, strict=True))
        xs, ys = to_grid(lons, lats)
        xs, ys = np.asarray(xs), np.asarray(ys)

        per_m = _road_emission_g_s_per_m(tags)
        if per_m > 0 and xs.size > 1:
            sx, sy = _densify(xs, ys, SAMPLE_SPACING_M)
            if sx.size == 0:
                continue
            length_m = float(np.hypot(np.diff(xs), np.diff(ys)).sum())
            # Every sample carries the same share of the way's total emission.
            weight = np.full(sx.size, per_m * length_m / sx.size)
            totals += np.histogram2d(sy, sx, bins=(y_edges, x_edges), weights=weight)[0]
            n_roads += 1
        elif _is_kiln(tags):
            # A kiln is a stack, not a line: bin its centroid.
            totals += np.histogram2d(
                ys.mean(keepdims=True),
                xs.mean(keepdims=True),
                bins=(y_edges, x_edges),
                weights=np.array([KILN_PM25_G_S]),
            )[0]
            n_kilns += 1

    logger.info(
        "emission inventory: %d roads, %d kilns, %.3f g/s over the tile",
        n_roads,
        n_kilns,
        totals.sum(),
    )
    # histogram2d's first axis ascends with y; the grid's first axis descends from north.
    return np.flipud(totals).astype("float32")
