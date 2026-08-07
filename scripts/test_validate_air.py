"""`validate_air.py`'s station fetch: what it keeps, what it drops, and why.

Every case here is one that actually happened on the first live run against OpenAQ. The
scoring this feeds (`cores.air.leave_one_station_out`) was already tested; the fetch was
not, and it was wrong in three separate ways at once.

No network: `_get` is stubbed, which is the only function in the module that opens a
socket.
"""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest
import validate_air

from terrarium.config import SeasonWindow, season_windows

WINDOW = next(w for w in season_windows([2025]) if w.label == "2025-winter")


class _Settings:
    """Only the fields `fetch_stations` reads."""

    openaq_url = "https://example.invalid/v3"
    openaq_key = "test-key"
    http_timeout_s = 5.0

    class tile:
        bbox = (74.2533, 31.4305, 74.4641, 31.6103)


def _location(name: str, sensor_id: int, lon: float = 74.35, lat: float = 31.52) -> dict[str, Any]:
    return {
        "name": name,
        "coordinates": {"longitude": lon, "latitude": lat},
        "sensors": [{"id": sensor_id, "parameter": {"id": 2}}],
    }


def _days(*values: float) -> dict[str, Any]:
    return {"results": [{"value": v} for v in values]}


def _stub_get(
    monkeypatch: pytest.MonkeyPatch,
    locations: list[dict[str, Any]],
    per_sensor: dict[int, Any],
) -> list[str]:
    """Route `/locations` to `locations` and `/sensors/N/...` to `per_sensor[N]`.

    A value that is an exception instance is raised instead of returned, which is how the
    HTTP 500 cases below are expressed.
    """
    calls: list[str] = []

    def fake_get(url: str, key: str, timeout_s: float) -> dict[str, Any]:
        calls.append(url)
        if "/locations" in url:
            return {"results": locations}
        sensor_id = int(url.split("/sensors/")[1].split("/")[0])
        answer = per_sensor[sensor_id]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(validate_air, "_get", fake_get)
    return calls


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid", code, "boom", {}, None)  # type: ignore[arg-type]


def test_a_sensor_that_500s_is_skipped_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seven of Lahore's 66 sensors answer HTTP 500 from OpenAQ's own side.

    The first live run raised on the first of them and produced no validation at all. One
    unusable monitor should cost one monitor.
    """
    _stub_get(
        monkeypatch,
        [_location("broken", 1), _location("fine", 2)],
        {1: _http_error(500), 2: _days(*[50.0] * 30)},
    )

    stations, dropped = validate_air.fetch_stations(_Settings(), WINDOW)

    assert [s["name"] for s in stations] == ["fine"]
    assert any("broken" in reason and "500" in reason for reason in dropped)


def test_the_no_data_sentinel_is_not_a_concentration(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAQ encodes "no reading" as -999, not as a missing field.

    Averaged in it drags a station's median toward nonsense, and an affine fit then has a
    negative observation to explain. Dropped values must not count toward the day quota
    either, or a station of pure sentinel passes as well covered.
    """
    _stub_get(
        monkeypatch,
        [_location("sentinel-heavy", 1)],
        {1: _days(*([-999.0] * 40 + [60.0] * 20))},
    )

    stations, _ = validate_air.fetch_stations(_Settings(), WINDOW)

    assert len(stations) == 1
    assert stations[0]["observed"] == 60.0
    assert stations[0]["days"] == 20, "sentinel readings must not count as coverage"


def test_a_station_of_only_sentinel_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_get(monkeypatch, [_location("dead", 1)], {1: _days(*[-999.0] * 90)})

    stations, dropped = validate_air.fetch_stations(_Settings(), WINDOW)

    assert stations == []
    assert any("dead" in reason and "0 day" in reason for reason in dropped)


def test_a_thin_station_is_dropped_with_its_day_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """A median of a handful of days is not a seasonal value, and the fit cannot tell."""
    _stub_get(monkeypatch, [_location("brief", 1)], {1: _days(*[40.0] * 5)})

    stations, dropped = validate_air.fetch_stations(_Settings(), WINDOW)

    assert stations == []
    assert any("brief" in reason and "5 day" in reason for reason in dropped)


def test_exactly_the_minimum_days_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_get(
        monkeypatch,
        [_location("just-enough", 1)],
        {1: _days(*[42.0] * validate_air.MIN_DAYS_IN_WINDOW)},
    )

    stations, _ = validate_air.fetch_stations(_Settings(), WINDOW)

    assert [s["name"] for s in stations] == ["just-enough"]


def test_the_window_is_what_is_requested_not_the_latest_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug that would have produced a confident calibration from an artefact.

    Lahore's monitors go quiet at different times, so `latest` returned a winter smog
    episode for one station and last night for another. The request must pin the modelled
    window's own dates.
    """
    calls = _stub_get(monkeypatch, [_location("any", 7)], {7: _days(*[30.0] * 30)})

    validate_air.fetch_stations(_Settings(), WINDOW)

    sensor_calls = [c for c in calls if "/sensors/" in c]
    assert len(sensor_calls) == 1
    url = sensor_calls[0]
    assert "measurements/daily" in url, "a daily aggregate over the window, not a snapshot"
    assert f"datetime_from={WINDOW.start.isoformat()}" in url
    assert f"datetime_to={WINDOW.end.isoformat()}" in url


def test_the_median_is_the_reduction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Median, matching how the cube's own composites are reduced.

    A mean would let one smog day carry a station, which is exactly the sensitivity the
    window comparison exists to remove.
    """
    values = [10.0] * 20 + [1000.0]
    _stub_get(monkeypatch, [_location("spiky", 1)], {1: _days(*values)})

    stations, _ = validate_air.fetch_stations(_Settings(), WINDOW)

    assert stations[0]["observed"] == 10.0


def test_a_non_pm25_sensor_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parameter 2 is PM2.5. A co-located NO2 monitor is not a second station."""
    location = _location("multi", 1)
    location["sensors"].insert(0, {"id": 99, "parameter": {"id": 5}})
    calls = _stub_get(monkeypatch, [location], {1: _days(*[25.0] * 30)})

    stations, _ = validate_air.fetch_stations(_Settings(), WINDOW)

    assert [s["name"] for s in stations] == ["multi"]
    assert not any("/sensors/99" in c for c in calls)


def test_a_window_outside_the_configured_years_still_resolves() -> None:
    """`--window 2025-winter` must work against a cube built with `--years 2025`.

    The date range is derived from the label, not looked up in `settings.window_years`,
    which describes the next build rather than the cube in hand.
    """
    window = next(w for w in season_windows([2019]) if w.label == "2019-winter")
    assert isinstance(window, SeasonWindow)
    assert window.start.year == 2019
    assert window.end.year == 2020, "winter runs into the following January"
