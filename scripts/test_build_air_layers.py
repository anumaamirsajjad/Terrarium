"""`build_air_layers.py`: window resolution, and the rule that it must not destroy data.

This script writes **in place** by default, which its own docstring argues is safe because
the cube is small enough to load fully before the store is touched. That argument covers a
crash. It does not cover writing a valid-looking but empty column, which is what it did.
"""

from __future__ import annotations

from typing import Any

import build_air_layers
import numpy as np
import pytest


def test_a_label_resolves_to_its_own_dates() -> None:
    window = build_air_layers._window_for("2025-winter")

    assert window is not None
    assert window.label == "2025-winter"
    assert (window.start.year, window.start.month) == (2025, 11)
    assert window.end.year == 2026, "winter runs into the following January"


def test_a_year_outside_the_configured_ones_still_resolves() -> None:
    """The bug: windows were looked up in `settings.windows`, which covers `window_years`.

    Default `window_years` is [2023, 2024], so on a cube built with `--years 2025` every
    lookup returned `None` — and the caller then wrote NaN for every window.
    """
    assert build_air_layers._window_for("2025-summer") is not None
    assert build_air_layers._window_for("2019-winter") is not None
    assert build_air_layers._window_for("2031-summer") is not None


def test_nonsense_labels_resolve_to_nothing_rather_than_raising() -> None:
    assert build_air_layers._window_for("not-a-window") is None
    assert build_air_layers._window_for("2025-autumn") is None


def _stub_meteorology(monkeypatch: pytest.MonkeyPatch, by_label: dict[str, Any]) -> None:
    """Return a fetched direction per window label; an Exception instance is raised."""

    def fake_ingest(_client: Any, _settings: Any, _grid: Any, window: Any) -> tuple[Any, Any]:
        answer = by_label[window.label]
        if isinstance(answer, Exception):
            raise answer
        return {build_air_layers.DIRECTION: xr_scalar(answer)}, None

    monkeypatch.setattr(build_air_layers, "_ingest_meteorology", fake_ingest)


def xr_scalar(value: float) -> Any:
    import xarray as xr

    return xr.DataArray(np.float32(value))


def test_a_successful_fetch_replaces_the_existing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_meteorology(monkeypatch, {"2025-summer": 123.0, "2025-winter": 289.0})

    values = build_air_layers._wind_direction(
        None, None, ["2025-summer", "2025-winter"], np.array([np.nan, np.nan], dtype="float32")
    )

    assert values == pytest.approx([123.0, 289.0])


def test_a_failed_fetch_keeps_what_the_cube_already_had(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The data-loss bug, pinned.

    `build_tile.py` had already populated both windows. This script then failed to resolve
    them and wrote NaN over the top, leaving a cube the air core refuses to run on — and it
    did so in place, so the good values were simply gone.
    """
    _stub_meteorology(
        monkeypatch, {"2025-summer": RuntimeError("open-meteo down"), "2025-winter": 289.0}
    )
    existing = np.array([122.8, 999.0], dtype="float32")

    values = build_air_layers._wind_direction(
        None, None, ["2025-summer", "2025-winter"], existing
    )

    assert values[0] == pytest.approx(122.8), "a failed fetch must not erase a good value"
    assert values[1] == pytest.approx(289.0), "a successful one must still overwrite"


def test_an_unresolvable_label_keeps_what_the_cube_already_had(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_meteorology(monkeypatch, {})

    values = build_air_layers._wind_direction(
        None, None, ["not-a-window"], np.array([77.0], dtype="float32")
    )

    assert values[0] == pytest.approx(77.0)


def test_a_nan_from_the_fetch_does_not_overwrite_a_good_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetch that "succeeds" and returns NaN is the same loss by a quieter route."""
    _stub_meteorology(monkeypatch, {"2025-winter": float("nan")})

    values = build_air_layers._wind_direction(
        None, None, ["2025-winter"], np.array([289.0], dtype="float32")
    )

    assert values[0] == pytest.approx(289.0)


def test_no_existing_column_still_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cube that predates the variable has nothing to preserve, and must still fill."""
    _stub_meteorology(monkeypatch, {"2025-winter": 289.0})

    values = build_air_layers._wind_direction(None, None, ["2025-winter"], None)

    assert values[0] == pytest.approx(289.0)


def test_a_mismatched_existing_column_is_ignored_rather_than_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cube with a different window count must not have its values smeared across."""
    _stub_meteorology(monkeypatch, {"2025-winter": 289.0})

    values = build_air_layers._wind_direction(
        None, None, ["2025-winter"], np.array([1.0, 2.0, 3.0], dtype="float32")
    )

    assert values[0] == pytest.approx(289.0)
