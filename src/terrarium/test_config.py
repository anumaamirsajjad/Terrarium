"""Tests for tile configuration.

The bbox is the one hardcoded fact v1 is built around; these guard it from drift.
"""

from __future__ import annotations

import math

from terrarium.config import ACTIVE_TILE, Settings

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
