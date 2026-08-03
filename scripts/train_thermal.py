"""Train the thermal emulator, validate it honestly, and run one worked intervention.

    uv run python scripts/train_thermal.py

This is the only place allowed to do I/O around the core: it opens the cube, writes the
model artefact, and prints numbers. The core itself never touches a file.

Read the sanity checks at the bottom before building anything on top of this. A positive
delta in the planted region is a sign error, not a finding; a delta larger than a full
conversion to tree cover means the model is extrapolating into dense-urban-with-canopy
conditions the tile contains few examples of.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from pyproj import Transformer

from terrarium.config import DATA_DIR, get_settings
from terrarium.cores.base import Intervention
from terrarium.cores.thermal.features import (
    BASE_VARIABLES,
    METEOROLOGY_VARIABLES,
    build_training_frame,
    meteorology_from_cube,
)
from terrarium.cores.thermal.model import (
    blocked_cv,
    importances,
    leave_one_window_out_cv,
    pooled_spatial_folds,
    train,
)
from terrarium.cores.thermal.simulate import (
    BUILT_UP_CLASS,
    effective_fraction,
    simulate,
    tree_built_contrast,
)
from terrarium.state.cube import select_window, window_labels
from terrarium.state.grid import grid_for_tile
from terrarium.state.store import open_cube

# The Lahore Canal at Canal Bank Road - dense built-up on both sides, and the obvious
# place a real greening scheme would start. Overridable, but this is the demo scenario.
DEFAULT_CENTRE_LON, DEFAULT_CENTRE_LAT = 74.3403, 31.5163
DEFAULT_RADIUS_M = 1000.0
DEFAULT_CANOPY_ADDED = 0.30

MODEL_PATH = DATA_DIR / "processed" / "thermal.txt"


def circular_mask(grid, lon: float, lat: float, radius_m: float) -> np.ndarray:
    """A disc of `radius_m` around a lon/lat, as a grid-shaped boolean mask."""
    transformer = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    cx, cy = transformer.transform(lon, lat)

    xs = grid.x_coords()[None, :]
    ys = grid.y_coords()[:, None]
    return ((xs - cx) ** 2 + (ys - cy) ** 2) <= radius_m**2


def _print_folds(report, first_column: str) -> None:
    print(f"    {first_column:>16} {'n_train':>9} {'n_test':>8} {'MAE':>8} {'naive MAE':>10}")
    for fold in report.folds:
        name = fold.label if fold.label is not None else str(fold.fold)
        print(
            f"    {name:>16} {fold.n_train:>9,} {fold.n_test:>8,} "
            f"{fold.mae:>8.3f} {fold.baseline_mae:>10.3f}"
        )
    print(f"\n    MAE          {report.mae_mean:.3f} +/- {report.mae_std:.3f} degC")
    print(f"    naive (mean) {report.baseline_mae_mean:.3f} degC")
    print(f"    skill        {report.skill:.1%} of the naive error removed\n")


def _report_spatial_cv(training, shape: tuple[int, int], rounds: int) -> None:
    """Spatial generalisation, with every window held out at the same locations."""
    folds = pooled_spatial_folds(shape, training.cell_index)
    report = blocked_cv(
        training.features,
        training.target,
        folds,
        num_boost_round=rounds,
        # Per-window means, not one pooled mean. A single mean across summer and winter
        # is a straw man - it scores ~10 degC MAE and flatters the model into the 90s.
        baseline_groups=training.window_index,
    )

    print("  spatially blocked CV (2 km blocks, 5 folds, pooled over windows)")
    _print_folds(report, "fold")
    print(
        "    Each block is held out of EVERY window at once. Assigning folds per row\n"
        "    instead would put the same grid cell in train and test via a different\n"
        "    window - land cover barely changes between them, so that is a duplicate.\n"
    )
    print(
        "    naive = that window's own mean, NOT one mean pooled over all of them.\n"
        "    Pooled, the baseline cannot tell summer from winter, scores ~10 degC, and\n"
        "    inflates skill into the 90s. This number is comparable to Phase 2/3.\n"
    )
    print(
        "    PLACEHOLDER VALIDATION. This answers 'can the model predict LST somewhere\n"
        "    unseen?' - spatial generalisation. It does NOT answer 'can it predict what\n"
        "    happens after a change?' Only the hindcast does.\n"
    )


def _report_window_cv(training, rounds: int) -> None:
    """Temporal generalisation: hold out one whole window at a time."""
    report = leave_one_window_out_cv(
        training.features,
        training.target,
        training.window_index,
        training.windows,
        num_boost_round=rounds,
    )

    print("  leave-one-window-out CV (can it reach a date it has never seen?)")
    _print_folds(report, "held-out window")
    print(
        "    Expect this to be worse than the spatial split. A held-out window carries\n"
        "    meteorology values the model never saw, so it can only fall back on the\n"
        "    nearest window it did see. The GAP between the two numbers is the honest\n"
        "    measure of how much the model leans on cross-sectional contrast alone.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zarr", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=MODEL_PATH)
    parser.add_argument(
        "--windows",
        nargs="*",
        default=None,
        help="seasonal windows to train on (default: every window in the cube). "
             "Pass one label to reproduce the Phase 2/3 single-date model.",
    )
    parser.add_argument("--rounds", type=int, default=400)
    parser.add_argument("--skip-cv", action="store_true", help="train only, no validation")
    parser.add_argument("--lon", type=float, default=DEFAULT_CENTRE_LON)
    parser.add_argument("--lat", type=float, default=DEFAULT_CENTRE_LAT)
    parser.add_argument("--radius-m", type=float, default=DEFAULT_RADIUS_M)
    parser.add_argument("--canopy", type=float, default=DEFAULT_CANOPY_ADDED)
    args = parser.parse_args(argv)

    settings = get_settings()
    grid = grid_for_tile(settings.tile)
    path = args.zarr or settings.zarr_store

    if not path.exists():
        print(f"no cube at {path} - run scripts/build_tile.py first")
        return 1

    full = open_cube(path)

    # Phase 4: the model now spans every window at once, with meteorology as a real
    # feature carrying the between-window difference. The core is still a pure function
    # of a *single* 2-D cube - stacking happens here in the training frame, and
    # `simulate` continues to take one window.
    labels = window_labels(full)
    chosen = args.windows if args.windows else labels
    unknown = [label for label in chosen if label not in labels]
    if unknown:
        print(f"no window(s) {unknown} in this cube; have {', '.join(labels)}")
        return 1

    training = build_training_frame(full, chosen)
    n_rows = len(training.target)

    print(f"\n{'=' * 78}")
    print("  Thermal core v2 - mid-morning land surface temperature emulator")
    print(f"{'=' * 78}")
    print(f"  cube         {path}")
    print(f"  windows      {len(training.windows)}: {', '.join(training.windows)}")
    cells = grid.shape[0] * grid.shape[1]
    print(f"  grid         {grid.shape[0]} x {grid.shape[1]} = {cells:,} px")
    print(f"  usable rows  {n_rows:,}  (pooled across windows)\n")

    dropped = [label for label in chosen if label not in training.windows]
    if dropped:
        print(f"  NOTE  {len(dropped)} window(s) contributed no usable rows: "
              f"{', '.join(dropped)}")
        print("        usually missing LST (winter cloud) or missing meteorology.\n")

    if n_rows < 1000:
        print("  too few usable pixels to train - check the cube build")
        return 1

    print("  meteorology per window (constant across the tile, varies between windows)")
    print(f"    {'window':<16} " + " ".join(f"{n:>22}" for n in METEOROLOGY_VARIABLES))
    for label in training.windows:
        met = meteorology_from_cube(select_window(full, label))
        print(f"    {label:<16} " + " ".join(f"{met[n]:>22.2f}" for n in METEOROLOGY_VARIABLES))
    print()

    if not args.skip_cv:
        _report_spatial_cv(training, grid.shape, args.rounds)
        if len(training.windows) > 1:
            _report_window_cv(training, args.rounds)
        else:
            print("  leave-one-window-out CV skipped: only one window in the training set.\n")

    model = train(training.features, training.target, num_boost_round=args.rounds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(args.out))
    print(f"  wrote model  {args.out}\n")

    print("  feature importance (gain)")
    for name, gain in sorted(importances(model).items(), key=lambda kv: -kv[1]):
        print(f"    {name:<18} {gain:>6.1%}")
    print(
        "    the intervention moves ndvi, ndbi and albedo but deliberately leaves\n"
        "    landcover alone, so heavy landcover gain means a muted response - and\n"
        "    flipping the class as well would count the same cooling twice.\n"
    )

    # ------------------------------------------------- worked intervention ---
    #
    # Run in EVERY window, not just one. Phase 3 measured a tree-vs-built contrast of
    # 2.6-2.8 degC in summer but only 0.3-0.8 degC in winter, so a single pooled dLST
    # number would average a real effect with a near-absent one and describe neither.
    # Reporting per season is the plan's explicit requirement, and it is also the
    # honest framing for the pitch: this intervention buys summer cooling.
    mask_base = circular_mask(grid, args.lon, args.lat, args.radius_m)

    print(f"  worked intervention: +{args.canopy:.0%} canopy, built-up cells within "
          f"{args.radius_m:.0f} m of {args.lat:.4f} N {args.lon:.4f} E")
    print("  evaluated in every window the model was trained on\n")

    print(f"    {'window':<16} {'cells':>7} {'dLST in':>9} {'spillover':>10} "
          f"{'contrast':>9} {'linear':>8} {'ratio':>7}")
    print(f"    {'-' * 16} {'-' * 7} {'-' * 9} {'-' * 10} {'-' * 9} {'-' * 8} {'-' * 7}")

    ok = True
    warnings: list[str] = []
    seen_summer = False

    for label in training.windows:
        window = select_window(full, label)
        arrays = {name: np.asarray(window[name].values) for name in BASE_VARIABLES}
        built_up = arrays["landcover"] == BUILT_UP_CLASS
        mask = mask_base & built_up
        if not mask.any():
            print(f"    {label:<16} {'no built-up cells in that disc':>52}")
            continue

        intervention = Intervention(mask=mask, canopy_fraction_added=args.canopy)
        stats = simulate(window, intervention, model).stats

        # The expected magnitude is a property of *this tile in this window*, not of the
        # literature. A hardcoded band silently becomes wrong the moment the season
        # changes - which is precisely what happens here. Shared with the API, which
        # ships this same ceiling beside every delta it returns.
        contrast = tree_built_contrast(window)
        fraction = effective_fraction(arrays, intervention)
        mean_fraction = float(fraction[mask].mean())
        expected = -mean_fraction * contrast
        ratio = stats.mean_delta_inside / expected if expected else float("nan")

        print(f"    {label:<16} {stats.n_cells_changed:>7,} "
              f"{stats.mean_delta_inside:>+9.3f} {stats.mean_delta_spillover:>+10.3f} "
              f"{contrast:>+9.2f} {expected:>+8.3f} {ratio:>7.2f}")

        is_summer = label.endswith("summer")
        seen_summer = seen_summer or is_summer

        if stats.mean_delta_inside > 0:
            warnings.append(f"FAIL {label}: planting warmed the tile. Sign error, not a finding.")
            ok = False
        elif stats.mean_delta_inside < -contrast:
            warnings.append(
                f"FAIL {label}: cooling exceeds a full conversion to tree cover - "
                "the model is extrapolating."
            )
            ok = False
        elif is_summer and not 0.3 <= ratio <= 2.0:
            # Only enforced in summer. In winter the contrast is ~0.3 degC, so the ratio
            # is a small number divided by a smaller one and swings wildly on noise
            # that means nothing physically.
            warnings.append(
                f"WARN {label}: {ratio:.2f}x the linear expectation. Plausible - the "
                "response is not linear - but worth understanding."
            )
        if is_summer and stats.mean_delta_spillover >= 0:
            warnings.append(f"WARN {label}: no spillover cooling - check neighbourhood features.")

    print()
    print("    contrast = this window's own tree-vs-built LST gap (a full conversion).")
    print("    linear   = contrast x the canopy actually added, after capping.")
    print("    ratio    = modelled / linear. Enforced in summer only: winter's contrast")
    print("               is near zero, so the ratio there divides noise by noise.\n")

    for line in warnings:
        print(f"    {line}")
    if not warnings:
        print("    OK    every window cools, in proportion to its own observed contrast.")
    if not seen_summer:
        print("    NOTE  no summer window was evaluated; winter alone understates this")
        print("          intervention for seasonal reasons, not modelling ones.")
    print()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
