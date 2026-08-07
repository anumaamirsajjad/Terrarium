"""`hindcast.py`'s window selection and before/after split.

The estimator in `cores/thermal/hindcast.py` is tested next to itself. What is tested here
is the part that decides *which windows are allowed into the comparison at all*, which is
the script's own logic and is what stands between a truncated tile read and a headline
number.
"""

from __future__ import annotations

import hindcast
import numpy as np
import pytest
import xarray as xr


def _cube(valid_by_window: dict[str, dict[str, float]]) -> xr.Dataset:
    """A cube whose per-window valid fractions are exactly as specified.

    Built by making the requested share of each window's pixels finite, since
    `window_valid_fractions` measures the array rather than reading an attribute.
    """
    labels = list(valid_by_window)
    height = width = 10
    cells = height * width

    data = {}
    for name in ("ndvi", "lst_c"):
        stack = np.full((len(labels), height, width), np.nan, dtype="float32")
        for i, label in enumerate(labels):
            keep = round(valid_by_window[label].get(name, 1.0) * cells)
            flat = stack[i].reshape(-1)
            flat[:keep] = 1.0
        data[name] = (("time", "y", "x"), stack)

    return xr.Dataset(
        data,
        coords={
            "y": np.arange(height, dtype="float64")[::-1] * 100.0,
            "x": np.arange(width, dtype="float64") * 100.0,
            "time": np.arange(len(labels)).astype("datetime64[Y]").astype("datetime64[ns]"),
            "window": ("time", np.array(labels, dtype="<U32")),
            "season": (
                "time",
                np.array(["summer" if "summer" in x else "winter" for x in labels], dtype="<U16"),
            ),
        },
    )


def test_only_summers_are_considered() -> None:
    """A hindcast compares like seasons. A winter in the split would swamp the effect."""
    cube = _cube(
        {
            "2016-summer": {},
            "2016-winter": {},
            "2017-summer": {},
            "2017-winter": {},
        }
    )

    usable, rejected = hindcast._usable_summers(cube)

    assert usable == ["2016-summer", "2017-summer"]
    assert rejected == []


def test_an_underpopulated_window_is_rejected_with_its_reason() -> None:
    """One truncated Planetary Computer read leaves a window's Sentinel-2 unpopulated.

    Averaged silently into the `before` baseline it shifts the observed change and nothing
    says why, which is exactly the failure the threshold exists to prevent.
    """
    cube = _cube(
        {
            "2016-summer": {},
            "2017-summer": {"ndvi": 0.1},
            "2018-summer": {},
        }
    )

    usable, rejected = hindcast._usable_summers(cube)

    assert usable == ["2016-summer", "2018-summer"]
    assert len(rejected) == 1
    label, variable, fraction = rejected[0]
    assert (label, variable) == ("2017-summer", "ndvi")
    assert fraction == pytest.approx(0.1, abs=0.02)


def test_the_worst_variable_is_the_one_reported() -> None:
    """Both required variables are checked, and the reason names the weaker one."""
    cube = _cube({"2016-summer": {"ndvi": 0.9, "lst_c": 0.2}})

    _, rejected = hindcast._usable_summers(cube)

    assert rejected[0][1] == "lst_c"


def test_a_window_exactly_at_the_threshold_is_kept() -> None:
    cube = _cube({"2016-summer": {"ndvi": hindcast.MIN_WINDOW_VALID}})

    usable, rejected = hindcast._usable_summers(cube)

    assert usable == ["2016-summer"]
    assert rejected == []


def test_the_default_split_is_two_halves() -> None:
    """Not "all but the last": a change has to be sustained to be a real transition."""
    summers = [f"{year}-summer" for year in range(2016, 2024)]

    before, after = hindcast._split(summers, None, None)

    assert before == summers[:4]
    assert after == summers[4:]
    assert not set(before) & set(after), "a window cannot be on both sides"


def test_an_explicit_split_is_honoured() -> None:
    before, after = hindcast._split(
        ["a", "b", "c", "d"], ["2016-summer"], ["2024-summer"]
    )

    assert (before, after) == (["2016-summer"], ["2024-summer"])


def test_too_few_summers_refuses_rather_than_splitting_thin() -> None:
    """Three summers cannot make two credible sides, and the message says what to do."""
    with pytest.raises(SystemExit, match="at least 4 summer windows"):
        hindcast._split(["2016-summer", "2017-summer", "2018-summer"], None, None)


def test_an_odd_number_of_summers_puts_the_extra_window_after() -> None:
    """The later side gets the spare, so `before` stays the cleaner baseline."""
    summers = [f"{year}-summer" for year in range(2016, 2023)]

    before, after = hindcast._split(summers, None, None)

    assert len(before) == 3
    assert len(after) == 4
    assert before + after == summers
