"""Tests for the health endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from terrarium.api.main import create_app
from terrarium.config import ACTIVE_TILE, Settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "terrarium"


def test_health_reports_the_active_tile(client: TestClient) -> None:
    """The frontend centres its map from this payload, so it must match config."""
    tile = client.get("/health").json()["tile"]

    assert tile["name"] == ACTIVE_TILE.name
    assert tuple(tile["bbox"]) == ACTIVE_TILE.bbox
    assert tile["target_resolution_m"] == ACTIVE_TILE.target_resolution_m


def test_injected_settings_reach_the_routes() -> None:
    """`create_app(settings=...)` must override what endpoints see, not just CORS.

    Routes resolve settings via `Depends(get_settings)`, which returns the lru_cached
    global. Without a dependency override the injected object reached the middleware and
    nothing else, so every endpoint answered from the real config regardless.
    """
    app = create_app(Settings(env="test"))

    assert TestClient(app).get("/health").json()["env"] == "test"


def test_tile_centroid_falls_inside_its_own_bbox() -> None:
    west, south, east, north = ACTIVE_TILE.bbox
    lon, lat = ACTIVE_TILE.centroid

    assert west < lon < east
    assert south < lat < north
