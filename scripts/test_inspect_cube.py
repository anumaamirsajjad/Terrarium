"""`inspect_cube.py`'s tree-vs-built contrast — the number that caps every planting claim.

Converting a cell entirely to tree cover cannot buy more cooling than the difference the
tile itself shows between trees and buildings. That makes this the sanity bound on any
scenario the product reports, and the repo's own rule says to prefer it over a literature
figure. Which means it has to be computed right, and it has to refuse rather than invent a
number when a window has no data.
"""

from __future__ import annotations

import inspect_cube
import numpy as np
import pytest
import xarray as xr

from terrarium.cores.thermal.simulate import BUILT_UP_CLASS, TREE_COVER_CLASS, WATER_CLASS

WINDOWS = ("2024-summer", "2024-winter")


def _cube(
    *,
    tree_lst: tuple[float, float] = (28.0, 14.0),
    built_lst: tuple[float, float] = (31.0, 15.0),
    all_nan_windows: tuple[str, ...] = (),
    landcover_classes: tuple[int, ...] = (TREE_COVER_CLASS, BUILT_UP_CLASS),
) -> xr.Dataset:
    """A tiny cube whose tree and built pixels carry exactly the requested temperatures."""
    height, width = 4, 4
    landcover = np.full((height, width), WATER_CLASS, dtype="uint8")
    if TREE_COVER_CLASS in landcover_classes:
        landcover[0, :] = TREE_COVER_CLASS
    if BUILT_UP_CLASS in landcover_classes:
        landcover[1, :] = BUILT_UP_CLASS

    lst = np.zeros((len(WINDOWS), height, width), dtype="float32")
    for i, label in enumerate(WINDOWS):
        if label in all_nan_windows:
            lst[i] = np.nan
            continue
        lst[i] = 20.0
        lst[i][landcover == TREE_COVER_CLASS] = tree_lst[i]
        lst[i][landcover == BUILT_UP_CLASS] = built_lst[i]

    return xr.Dataset(
        {
            "lst_c": (("time", "y", "x"), lst),
            "landcover": (("y", "x"), landcover),
        },
        coords={
            "y": np.arange(height, dtype="float64")[::-1] * 100.0,
            "x": np.arange(width, dtype="float64") * 100.0,
            "time": np.array(["2024-05-16", "2024-12-16"], dtype="datetime64[ns]"),
            "window": ("time", np.array(WINDOWS, dtype="<U32")),
            "season": ("time", np.array(["summer", "winter"], dtype="<U16")),
        },
    )


def test_the_contrast_is_built_minus_tree(capsys: pytest.CaptureFixture[str]) -> None:
    """Sign matters: trees are the cooler class, so the contrast must come out positive."""
    inspect_cube._print_contrast(_cube(), list(WINDOWS))

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip().startswith("2024-")]
    assert len(lines) == 2

    summer = lines[0].split()
    assert float(summer[1]) == 28.0, "tree median"
    assert float(summer[2]) == 31.0, "built median"
    assert float(summer[3]) == 3.0, "contrast is built minus tree, and positive"


def test_each_window_gets_its_own_contrast(capsys: pytest.CaptureFixture[str]) -> None:
    """The whole reason this is per-window: summer and winter differ, and both are quoted."""
    inspect_cube._print_contrast(_cube(), list(WINDOWS))

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip().startswith("2024-")]

    assert float(lines[0].split()[3]) == 3.0
    assert float(lines[1].split()[3]) == 1.0


def test_a_window_with_no_lst_says_so_rather_than_printing_nan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A partial build leaves a window empty, and a NaN contrast must not read as a value."""
    inspect_cube._print_contrast(_cube(all_nan_windows=("2024-winter",)), list(WINDOWS))

    out = capsys.readouterr().out
    assert "no LST for this window" in out
    assert "nan" not in out.lower()


def test_a_cube_missing_a_class_refuses_to_compute(capsys: pytest.CaptureFixture[str]) -> None:
    """With no tree pixels there is no contrast, and zero would be a wrong answer.

    A tile that is genuinely all built-up should say the number is not computable rather
    than report a difference against an empty set.
    """
    inspect_cube._print_contrast(_cube(landcover_classes=(BUILT_UP_CLASS,)), list(WINDOWS))

    out = capsys.readouterr().out
    assert "not computable" in out


def test_water_is_excluded_from_both_medians(capsys: pytest.CaptureFixture[str]) -> None:
    """Water sits at 20 C in the fixture and would drag either median if it were counted."""
    inspect_cube._print_contrast(_cube(), list(WINDOWS))

    out = capsys.readouterr().out
    summer = next(line for line in out.splitlines() if line.strip().startswith("2024-")).split()
    assert float(summer[1]) == 28.0
    assert float(summer[2]) == 31.0
