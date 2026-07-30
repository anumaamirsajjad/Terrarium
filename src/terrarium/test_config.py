"""Tests for tile and window configuration.

The bbox is the one hardcoded fact v1 is built around; these guard it from drift. The
seasonal windows are the cube's time axis, and the winter one straddles a year boundary,
which is the part that is easy to get quietly wrong.
"""

from __future__ import annotations

import itertools
import math
from datetime import date

from terrarium.config import ACTIVE_TILE, Season, Settings, season_windows

# Degrees of latitude per kilometre.
KM_PER_DEG_LAT = 111.32


def test_bbox_is_well_formed() -> None:
    west, south, east, north = ACTIVE_TILE.bbox

    assert west < east
    assert south < north


def test_tile_is_approximately_20km_square() -> None:
    """v1 targets a ~20 km tile; allow 10% slack for the rounded bbox."""
    west, south, east, north = ACTIVE_TILE.bbox
    _, lat = ACTIVE_TILE.centroid

    height_km = (north - south) * KM_PER_DEG_LAT
    width_km = (east - west) * KM_PER_DEG_LAT * math.cos(math.radians(lat))

    assert 18.0 <= height_km <= 22.0
    assert 18.0 <= width_km <= 22.0


def test_settings_expose_the_active_tile() -> None:
    assert Settings().tile is ACTIVE_TILE


# ------------------------------------------------------------ seasonal windows ---


def test_winter_runs_into_the_following_year() -> None:
    """The bug this exists to prevent: a Nov-Jan range built inside one calendar year.

    `2024-11-01/2024-01-31` is an inverted interval — STAC returns nothing for it, and a
    build would report an empty winter as bad weather rather than as a date bug.
    """
    winter = next(w for w in season_windows([2024]) if w.season is Season.WINTER)

    assert winter.start == date(2024, 11, 1)
    assert winter.end == date(2025, 1, 31)
    assert winter.start < winter.end
    # Labelled by the year it starts in, so one cold season is not split across two.
    assert winter.label == "2024-winter"


def test_each_year_contributes_a_summer_and_a_winter() -> None:
    windows = season_windows([2023, 2024])

    assert [w.label for w in windows] == [
        "2023-summer",
        "2023-winter",
        "2024-summer",
        "2024-winter",
    ]
    # Ascending by start date: the cube's time coordinate must be monotonic.
    starts = [w.start for w in windows]
    assert starts == sorted(starts)


def test_windows_do_not_overlap() -> None:
    """Overlapping windows would composite the same scene into two different slices."""
    windows = season_windows([2023, 2024, 2025])
    for earlier, later in itertools.pairwise(windows):
        assert earlier.end < later.start, f"{earlier.label} overlaps {later.label}"


def test_midpoint_falls_inside_the_window() -> None:
    """The time coordinate must sit in the range it summarises, including over new year."""
    for window in season_windows([2024]):
        assert window.start <= window.midpoint <= window.end


def test_stac_datetime_is_a_closed_interval() -> None:
    summer = next(w for w in season_windows([2024]) if w.season is Season.SUMMER)
    assert summer.stac_datetime == "2024-04-01/2024-06-30"
