"""Cube endpoints. Served from an in-memory synthetic cube — no Zarr, no network."""

from __future__ import annotations

import base64
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from terrarium.api.runtime import Runtime
from terrarium.api.schemas.cube import ARRAY_ENCODING
from terrarium.state.cube import select_window


def decode(payload: dict[str, Any]) -> np.ndarray:
    """Undo the wire encoding, the way a client would."""
    assert payload["encoding"] == ARRAY_ENCODING
    height, width = payload["grid"]["shape"]
    raw = np.frombuffer(base64.b64decode(payload["data"]), dtype="<f4")
    return raw.reshape(height, width)


# ---------------------------------------------------------------- /cube/summary ---


def test_summary_reports_the_windows_and_the_default(client: TestClient) -> None:
    body = client.get("/cube/summary").json()

    assert body["windows"] == ["2024-summer", "2024-winter"]
    # Not simply the first or last window: the latest *summer*.
    assert body["default_window"] == "2024-summer"
    assert body["shape"] == [201, 202]
    assert body["resolution_m"] == 100


def test_summary_reports_validity_per_window(client: TestClient) -> None:
    """The whole-cube figure hides a half-built cube; the per-window one does not."""
    body = client.get("/cube/summary").json()
    per_window = body["window_valid_fractions"]

    assert set(per_window) == {"2024-summer", "2024-winter"}
    for window in per_window.values():
        assert window["lst_c"] == 1.0
    # Static variables have nothing per-window to say and are omitted rather than
    # repeated identically for every slice.
    assert "elevation_m" not in per_window["2024-summer"]


# ------------------------------------------------------------ /cube/layer/{name} ---


def test_a_layer_round_trips_the_actual_values(
    client: TestClient, synthetic_runtime: Runtime
) -> None:
    payload = client.get("/cube/layer/lst_c", params={"window": "2024-summer"}).json()

    expected = np.asarray(
        select_window(synthetic_runtime.cube, "2024-summer")["lst_c"].values
    )
    assert np.allclose(decode(payload), expected, equal_nan=True)


def test_a_layer_defaults_to_the_latest_summer(client: TestClient) -> None:
    defaulted = client.get("/cube/layer/lst_c").json()
    explicit = client.get("/cube/layer/lst_c", params={"window": "2024-summer"}).json()

    assert defaulted["window"] == "2024-summer"
    assert defaulted["data"] == explicit["data"]


def test_windows_differ_so_the_parameter_is_doing_something(client: TestClient) -> None:
    summer = decode(client.get("/cube/layer/lst_c", params={"window": "2024-summer"}).json())
    winter = decode(client.get("/cube/layer/lst_c", params={"window": "2024-winter"}).json())

    assert not np.allclose(summer, winter)
    assert np.nanmean(summer) > np.nanmean(winter)


def test_a_static_variable_reports_no_window(client: TestClient) -> None:
    """Elevation does not belong to a window; claiming one would imply a variation."""
    payload = client.get("/cube/layer/elevation_m", params={"window": "2024-winter"}).json()

    assert payload["window"] is None


def test_a_layer_carries_bounds_in_both_crs(client: TestClient) -> None:
    grid = client.get("/cube/layer/ndvi").json()["grid"]

    assert grid["crs"] == "EPSG:32643"
    west, south, east, north = grid["bounds_wgs84"]
    # The Lahore tile, give or take the projection envelope.
    assert 74.2 < west < 74.3 and 74.45 < east < 74.55
    assert 31.4 < south < 31.5 and 31.6 < north < 31.7
    # Projected bounds are metres, not degrees - a swap would be silent otherwise.
    assert grid["bounds"][2] - grid["bounds"][0] > 10_000


def test_a_layer_reports_its_value_range_for_the_colour_ramp(client: TestClient) -> None:
    payload = client.get("/cube/layer/lst_c").json()
    values = decode(payload)

    assert payload["vmin"] == np.float32(np.nanmin(values)).item()
    assert payload["vmax"] == np.float32(np.nanmax(values)).item()
    assert payload["valid_fraction"] == 1.0


def test_the_temperature_layer_is_described_as_mid_morning_and_surface(client: TestClient) -> None:
    """D9. The description reaches the UI, so it is the one that must not say 'afternoon'."""
    description = client.get("/cube/layer/lst_c").json()["description"].lower()

    assert "surface" in description
    assert "10:30" in description
    assert "afternoon" not in description


def test_an_unknown_variable_is_404(client: TestClient) -> None:
    response = client.get("/cube/layer/not_a_variable")

    assert response.status_code == 404
    assert "not_a_variable" in response.json()["detail"]


def test_an_unknown_window_is_404(client: TestClient) -> None:
    response = client.get("/cube/layer/lst_c", params={"window": "1999-summer"})

    assert response.status_code == 404
    assert "1999-summer" in response.json()["detail"]


def test_meteorology_cannot_be_requested_as_a_map(client: TestClient) -> None:
    """It is one value per window. A raster of it would imply a field nobody measured."""
    response = client.get("/cube/layer/air_temp_c")

    assert response.status_code == 400
    assert "not a map" in response.json()["detail"]
