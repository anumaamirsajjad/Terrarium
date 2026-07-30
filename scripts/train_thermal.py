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
from terrarium.cores.thermal.features import BASE_VARIABLES, build_features, target_from_cube
from terrarium.cores.thermal.model import (
    blocked_cv,
    importances,
    spatial_folds,
    train,
)
from terrarium.cores.thermal.simulate import (
    BUILT_UP_CLASS,
    TREE_COVER_CLASS,
    effective_fraction,
    simulate,
)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zarr", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=MODEL_PATH)
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

    cube = open_cube(path)
    frame, valid = build_features(cube)
    target = target_from_cube(cube)

    rows = valid.reshape(-1) & np.isfinite(target)
    n_rows = int(rows.sum())
    print(f"\n{'=' * 78}")
    print("  Thermal core - mid-morning land surface temperature emulator")
    print(f"{'=' * 78}")
    print(f"  cube         {path}")
    print(f"  grid         {grid.shape[0]} x {grid.shape[1]} = {valid.size:,} px")
    print(f"  usable rows  {n_rows:,}  ({n_rows / valid.size:.1%})\n")

    if n_rows < 1000:
        print("  too few usable pixels to train - check the cube build")
        return 1

    if not args.skip_cv:
        folds = spatial_folds(valid.shape).reshape(-1)[rows]
        report = blocked_cv(frame[rows], target[rows], folds, num_boost_round=args.rounds)

        print("  spatially blocked CV (2 km blocks, 5 folds)")
        print(f"    {'fold':>4} {'n_train':>9} {'n_test':>8} {'MAE':>8} {'naive MAE':>10}")
        for fold in report.folds:
            print(
                f"    {fold.fold:>4} {fold.n_train:>9,} {fold.n_test:>8,} "
                f"{fold.mae:>8.3f} {fold.baseline_mae:>10.3f}"
            )
        print(f"\n    MAE          {report.mae_mean:.3f} +/- {report.mae_std:.3f} degC")
        print(f"    naive (mean) {report.baseline_mae_mean:.3f} degC")
        print(f"    skill        {report.skill:.1%} of the naive error removed")
        print(
            "\n    PLACEHOLDER VALIDATION. This answers 'can the model predict LST\n"
            "    somewhere unseen?' - spatial generalisation. It does NOT answer 'can it\n"
            "    predict what happens after a change?' Only the hindcast does.\n"
        )

    model = train(frame[rows], target[rows], num_boost_round=args.rounds)
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

    # ------------------------------------------------------- worked intervention ---
    mask = circular_mask(grid, args.lon, args.lat, args.radius_m)
    built_up = np.asarray(cube["landcover"].values) == BUILT_UP_CLASS
    mask &= built_up

    print(f"  worked intervention: +{args.canopy:.0%} canopy, built-up cells within "
          f"{args.radius_m:.0f} m of {args.lat:.4f} N {args.lon:.4f} E")
    if not mask.any():
        print("    no built-up cells in that disc - widen --radius-m")
        return 1

    intervention = Intervention(mask=mask, canopy_fraction_added=args.canopy)
    result = simulate(cube, intervention, model)
    stats = result.stats
    print(f"    cells planted     {stats.n_cells_changed:,}")
    print(f"    mean dLST inside  {stats.mean_delta_inside:+.3f} degC")
    print(f"    mean dLST spillover {stats.mean_delta_spillover:+.3f} degC  "
          f"({stats.spillover_cells:,} cells in the 200 m ring outside)")
    print(f"    strongest cooling {stats.min_delta:+.3f} degC")
    print(f"    largest warming   {stats.max_delta:+.3f} degC\n")

    # The expected magnitude is a property of *this tile*, not of the literature. A
    # hardcoded "-1 to -4 degC" band silently becomes wrong the moment the composite,
    # the season, or the city changes; the observed contrast between the tile's own tree
    # and built-up pixels does not.
    arrays = {name: np.asarray(cube[name].values) for name in BASE_VARIABLES}
    lst = np.asarray(cube["lst_c"].values)
    contrast = float(
        np.nanmedian(lst[built_up]) - np.nanmedian(lst[arrays["landcover"] == TREE_COVER_CLASS])
    )
    fraction = effective_fraction(arrays, intervention)
    mean_fraction = float(fraction[mask].mean()) if mask.any() else 0.0
    expected = -mean_fraction * contrast

    print(f"    observed tree-vs-built LST contrast  {contrast:+.2f} degC (full conversion)")
    print(f"    mean canopy actually added           {mean_fraction:.1%} after capping")
    print(f"    linear expectation                   {expected:+.3f} degC\n")

    ok = True
    if stats.mean_delta_inside >= 0:
        print("    FAIL  planting did not cool. This is a sign error, not a finding.")
        ok = False
    elif stats.mean_delta_inside < -contrast:
        print("    FAIL  cooling exceeds a full conversion to tree cover. The model is\n"
              "          extrapolating - the built-up core has few high-canopy analogues.")
        ok = False
    elif not 0.3 <= stats.mean_delta_inside / expected <= 2.0:
        print(f"    WARN  {stats.mean_delta_inside / expected:.2f}x the linear expectation.\n"
              "          Plausible - the response is not linear - but worth understanding.")
    else:
        print("    OK    cooling, and in proportion to the contrast the tile actually shows.")
    if stats.mean_delta_spillover >= 0:
        print("    WARN  no spillover cooling - check the neighbourhood features.")
    print()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
