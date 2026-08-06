"""OSM emission inventory tests. Offline: the Overpass payload is a fixture, not a fetch."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from pyproj import Transformer

from terrarium.config import ACTIVE_TILE
from terrarium.ingest.osm import (
    FLEET_PM25_G_PER_VEH_KM,
    KILN_PM25_G_S,
    LINK_FLOW_SHARE,
    ROAD_VEHICLES_PER_DAY,
    SECONDS_PER_DAY,
    build_query,
    emission_grid,
)
from terrarium.state.grid import Grid, grid_for_tile


@pytest.fixture
def grid() -> Grid:
    return grid_for_tile(ACTIVE_TILE)


def _lonlat(grid: Grid, x: float, y: float) -> tuple[float, float]:
    """Projected metres back to WGS84, so a fixture can name a place on the grid."""
    lon, lat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform(x, y)
    return float(lon), float(lat)


def _road(
    grid: Grid, highway: str, *, length_m: float = 1000.0, row_from_top: int = 50
) -> dict[str, Any]:
    """A straight west-east road of a known length, `row_from_top` cells below the top."""
    left, _, _, top = grid.bounds
    y = top - (row_from_top + 0.5) * grid.resolution_m
    x0 = left + 20.5 * grid.resolution_m
    start = _lonlat(grid, x0, y)
    end = _lonlat(grid, x0 + length_m, y)

    return {
        "type": "way",
        "tags": {"highway": highway},
        "geometry": [
            {"lon": start[0], "lat": start[1]},
            {"lon": end[0], "lat": end[1]},
        ],
    }


def _payload(*elements: dict[str, Any]) -> dict[str, Any]:
    return {"elements": list(elements)}


def _expected_g_s(highway: str, length_m: float) -> float:
    base = ROAD_VEHICLES_PER_DAY[highway.removesuffix("_link")]
    if highway.endswith("_link"):
        base *= LINK_FLOW_SHARE
    return base * FLEET_PM25_G_PER_VEH_KM / SECONDS_PER_DAY / 1000.0 * length_m


def test_a_road_emits_its_length_times_its_class(grid: Grid) -> None:
    field = emission_grid(_payload(_road(grid, "primary")), grid)

    assert field.shape == grid.shape
    # Projected length differs from ground length by the UTM scale factor, so allow a
    # little slack - the point is that emission scales with length, not that WGS84 and
    # UTM agree to the metre.
    assert field.sum() == pytest.approx(_expected_g_s("primary", 1000.0), rel=0.01)


def test_class_ratios_survive(grid: Grid) -> None:
    """A single calibration factor can fix the overall scale; it cannot fix these."""
    motorway = emission_grid(_payload(_road(grid, "motorway")), grid).sum()
    residential = emission_grid(_payload(_road(grid, "residential")), grid).sum()

    expected = ROAD_VEHICLES_PER_DAY["motorway"] / ROAD_VEHICLES_PER_DAY["residential"]
    assert motorway / residential == pytest.approx(expected, rel=0.01)


def test_a_slip_road_carries_a_fraction_of_its_class(grid: Grid) -> None:
    trunk = emission_grid(_payload(_road(grid, "trunk")), grid).sum()
    link = emission_grid(_payload(_road(grid, "trunk_link")), grid).sum()

    assert link / trunk == pytest.approx(LINK_FLOW_SHARE, rel=0.01)


def test_the_road_lands_where_it_was_drawn(grid: Grid) -> None:
    """The orientation check. `histogram2d` counts y upward; the grid counts rows down.

    Get this wrong and the inventory is a perfect mirror image of the city - every number
    plausible, every road in the wrong place, and nothing else in the pipeline would
    notice.
    """
    field = emission_grid(_payload(_road(grid, "primary", row_from_top=20)), grid)

    rows = np.nonzero(field.sum(axis=1))[0]
    assert rows.tolist() == [20]
    # A 1 km road starting at cell 20's *centre* ends at cell 30's centre, so it touches
    # eleven columns with half a cell's worth in each of the two end ones.
    columns = np.nonzero(field.sum(axis=0))[0]
    assert columns.tolist() == list(range(20, 31))
    assert field[20, 20] == pytest.approx(field[20, 25] / 2, rel=0.1)


def test_untagged_and_unknown_ways_contribute_nothing(grid: Grid) -> None:
    field = emission_grid(
        _payload(
            _road(grid, "footway"),
            _road(grid, "cycleway"),
            {"type": "way", "geometry": [{"lon": 74.3, "lat": 31.5}]},
        ),
        grid,
    )
    assert field.sum() == 0.0


def test_a_road_outside_the_tile_is_dropped(grid: Grid) -> None:
    far = {
        "type": "way",
        "tags": {"highway": "motorway"},
        "geometry": [{"lon": 70.0, "lat": 30.0}, {"lon": 70.1, "lat": 30.0}],
    }
    assert emission_grid(_payload(far), grid).sum() == 0.0


def test_a_kiln_is_a_point_source(grid: Grid) -> None:
    left, _, _, top = grid.bounds
    lon, lat = _lonlat(grid, left + 30.5 * grid.resolution_m, top - 40.5 * grid.resolution_m)
    kiln = {"type": "node", "tags": {"man_made": "kiln"}, "lon": lon, "lat": lat}

    field = emission_grid(_payload(kiln), grid)

    assert field.sum() == pytest.approx(KILN_PM25_G_S)
    assert field[40, 30] == pytest.approx(KILN_PM25_G_S)


def test_a_kiln_mapped_as_an_area_still_lands_in_one_cell(grid: Grid) -> None:
    """OSM maps kilns as nodes, closed ways and relations, at the mapper's discretion."""
    area = _road(grid, "unclassified", length_m=200.0)
    area["tags"] = {"industrial": "brick_kiln"}

    field = emission_grid(_payload(area), grid)

    assert field.sum() == pytest.approx(KILN_PM25_G_S)
    assert int((field > 0).sum()) == 1


def test_the_query_uses_overpass_bbox_order() -> None:
    """GeoJSON is (west, south, east, north); Overpass wants (south, west, north, east).

    Swapping them returns an empty result rather than an error, so the inventory would
    simply be zero everywhere and the air core would return zeroes that look like answers.
    """
    query = build_query((74.2533, 31.4305, 74.4641, 31.6103))

    assert "(31.4305,74.2533,31.6103,74.4641)" in query
    assert "motorway" in query and "residential" in query
