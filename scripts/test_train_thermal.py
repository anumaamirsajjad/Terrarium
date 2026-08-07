"""`train_thermal.py`'s one piece of real arithmetic: the worked-intervention mask.

Everything else in the script is printing around `cores.thermal.model`, which is tested
next to itself. `circular_mask` is not: it converts a lon/lat and a radius into the grid
mask the worked example is run on, and a wrong one would quietly change the headline
intervention number the script reports.
"""

from __future__ import annotations

import numpy as np
from train_thermal import circular_mask

from terrarium.config import ACTIVE_TILE
from terrarium.state.grid import grid_for_tile

GRID = grid_for_tile(ACTIVE_TILE)
CENTRE_LON, CENTRE_LAT = ACTIVE_TILE.centroid


def test_the_mask_is_grid_shaped_and_boolean() -> None:
    mask = circular_mask(GRID, CENTRE_LON, CENTRE_LAT, 1000.0)

    assert mask.shape == GRID.shape
    assert mask.dtype == bool


def test_the_area_matches_the_requested_radius() -> None:
    """A disc of radius r covers pi*r^2, which at 100 m cells is a countable number.

    Worth asserting rather than eyeballing: a mask built with the axes transposed, or with
    a radius read as a diameter, is still a plausible-looking blob.
    """
    radius_m = 2000.0
    mask = circular_mask(GRID, CENTRE_LON, CENTRE_LAT, radius_m)

    cell_area = GRID.resolution_m**2
    expected_cells = np.pi * radius_m**2 / cell_area

    assert abs(mask.sum() - expected_cells) / expected_cells < 0.02


def test_a_bigger_radius_contains_a_smaller_one() -> None:
    small = circular_mask(GRID, CENTRE_LON, CENTRE_LAT, 1000.0)
    large = circular_mask(GRID, CENTRE_LON, CENTRE_LAT, 3000.0)

    assert small.sum() < large.sum()
    assert np.all(large[small]), "the larger disc must contain the smaller one"


def test_the_disc_is_centred_where_it_was_asked_for() -> None:
    """The centroid of the mask must land on the requested point, not mirrored.

    The canonical grid's rows run north to south, so a mask built without that in mind is
    still symmetric and still wrong — it sits reflected about the tile's middle.
    """
    mask = circular_mask(GRID, CENTRE_LON, CENTRE_LAT, 2000.0)
    rows, cols = np.nonzero(mask)

    assert abs(rows.mean() - (GRID.shape[0] - 1) / 2) < 1.0
    assert abs(cols.mean() - (GRID.shape[1] - 1) / 2) < 1.0


def test_a_point_outside_the_tile_selects_nothing() -> None:
    """Karachi is not on this tile, and must not quietly clamp to an edge."""
    mask = circular_mask(GRID, 67.0, 24.86, 1000.0)

    assert not mask.any()


def test_an_offset_centre_moves_the_disc_the_right_way() -> None:
    """North of centre must be a *lower* row index on the canonical grid."""
    north = circular_mask(GRID, CENTRE_LON, CENTRE_LAT + 0.03, 1500.0)
    south = circular_mask(GRID, CENTRE_LON, CENTRE_LAT - 0.03, 1500.0)

    assert np.nonzero(north)[0].mean() < np.nonzero(south)[0].mean()

    east = circular_mask(GRID, CENTRE_LON + 0.03, CENTRE_LAT, 1500.0)
    west = circular_mask(GRID, CENTRE_LON - 0.03, CENTRE_LAT, 1500.0)

    assert np.nonzero(east)[1].mean() > np.nonzero(west)[1].mean()
