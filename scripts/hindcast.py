"""Hindcast validation: find a change, train before it, and check the prediction after.

    uv run python scripts/hindcast.py --zarr data/processed/cube_hindcast.zarr

This is the credibility test. Every other number the thermal core produces measures
generalisation across *space* or across *dates*. This one measures whether the model gets
the effect of a **change on the ground** right — which is the only thing an intervention
tool is really claiming.

I/O lives here; the estimator lives in `cores/thermal/hindcast.py` and is pure.

Read `change-effect error` before `MAE`. A hindcast window is a date the model has never
seen, so its MAE carries a whole-window offset that says nothing about the change. The
change-effect error is a difference of biases between changed and unchanged cells in the
same window, so that offset cancels out of it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import xarray as xr
from pyproj import Transformer

from terrarium.config import get_settings
from terrarium.cores.thermal.hindcast import (
    DEFAULT_SIGMA,
    MIN_SITE_CELLS,
    ChangeField,
    HindcastReport,
    detect_change,
    run_hindcast,
)
from terrarium.state.cube import window_labels, window_valid_fractions
from terrarium.state.grid import Grid, grid_for_tile
from terrarium.state.store import open_cube

RULE = "-" * 72

# A window must be at least this populated in the variables a hindcast reads before it is
# allowed into either side of the comparison. Same bar the API applies before serving.
#
# This is not hypothetical: a single truncated tile read from Planetary Computer
# ("got 245245 bytes, expected 283424") leaves one window's Sentinel-2 unpopulated while
# every other window builds fine. Silently averaging that NaN window into the `before`
# baseline would shift the observed change and nothing would say why.
MIN_WINDOW_VALID = 0.5
# What the hindcast actually reads: NDVI drives change detection, LST is the target.
REQUIRED_VARIABLES = ("ndvi", "lst_c")


def _usable_summers(cube: xr.Dataset) -> tuple[list[str], list[tuple[str, str, float]]]:
    """Summer windows populated enough to use, plus what was rejected and why."""
    fractions = window_valid_fractions(cube)
    usable: list[str] = []
    rejected: list[tuple[str, str, float]] = []

    for label in window_labels(cube):
        if not label.endswith("summer"):
            continue
        worst = min(
            ((name, fractions[label].get(name, 0.0)) for name in REQUIRED_VARIABLES),
            key=lambda pair: pair[1],
        )
        if worst[1] < MIN_WINDOW_VALID:
            rejected.append((label, worst[0], worst[1]))
        else:
            usable.append(label)

    return usable, rejected


def _banner(title: str) -> None:
    print(f"\n{RULE}\n  {title}\n{RULE}")


def _split(
    summers: list[str], before: list[str] | None, after: list[str] | None
) -> tuple[list[str], list[str]]:
    """Default split: the earlier half is 'before', the later half is 'after'.

    Deliberately not "all but the last window". The change has to be *sustained* to be a
    real land-surface transition rather than one odd summer, and judging that needs
    several windows on each side.
    """
    if before and after:
        return before, after
    if len(summers) < 4:
        raise SystemExit(
            f"need at least 4 summer windows to split before/after, have {len(summers)}: "
            f"{summers}. Rebuild with more --years."
        )
    half = len(summers) // 2
    return summers[:half], summers[half:]


def _print_sites(field: ChangeField, grid: Grid, top: int) -> None:
    _banner("CHANGE DETECTION")
    print(f"  median tile-wide NDVI drift   {field.median_drift:+.4f}")
    print(f"  threshold beyond that drift   {field.threshold:.4f}"
          f"  ({DEFAULT_SIGMA:.0f} robust sigma)")
    print(f"  cells over threshold          {field.n_changed:,}")
    print(f"  cells inside a site           {field.n_in_sites:,}"
          f"  (patches >= {MIN_SITE_CELLS} cells)")
    print(f"  sites found                   {len(field.sites)}")

    if not field.sites:
        print("\n  Nothing changed enough to validate against. That is a finding about")
        print("  the tile, not a failure - say so rather than lowering the threshold")
        print("  until something appears.")
        return

    # The grid, not the cube's attrs: `config` is the single source of truth for the CRS,
    # and a Zarr round-trip does not necessarily carry the attr through.
    to_wgs84 = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform
    print(f"\n  largest {min(top, len(field.sites))} sites")
    print(f"    {'kind':<9} {'cells':>6} {'dNDVI':>8}  {'centre (lat, lon)':>24}")
    print(f"    {'-' * 9} {'-' * 6} {'-' * 8}  {'-' * 24}")
    for site in field.sites[:top]:
        lon, lat = to_wgs84(site.centre_x, site.centre_y)
        kind = "greening" if site.greening else "browning"
        print(f"    {kind:<9} {site.n_cells:>6,} {site.mean_ndvi_change:>+8.3f} "
              f" {lat:>11.4f}, {lon:>10.4f}")


def _print_report(report: HindcastReport) -> None:
    _banner("HINDCAST")
    print(f"  trained on   {', '.join(report.before_windows)}")
    print(f"  predicting   {report.after_window}  (never seen in training)")

    print("\n  observed change on the ground")
    print(f"    dLST at changed cells         {report.observed_lst_change_changed:+.3f} degC")
    print(f"    dLST at unchanged cells       {report.observed_lst_change_unchanged:+.3f} degC")
    print(f"    net observed effect           {report.observed_change_effect:+.3f} degC")
    if abs(report.observed_change_effect) < 0.2:
        print("    NOTE: the ground barely moved thermally, so this hindcast cannot")
        print("          discriminate a good model from a bad one. Report it as such.")

    print(
        f"\n  {'group':<11} {'cells':>7} {'MAE':>7} {'bias':>8} {'sR2':>7}"
        f"  | {'MAE':>7} {'sR2':>7}   <- offset removed"
    )
    print(f"  {'-' * 11} {'-' * 7} {'-' * 7} {'-' * 8} {'-' * 7}  | {'-' * 7} {'-' * 7}")
    for name, score in (
        ("all", report.overall),
        ("changed", report.changed),
        ("unchanged", report.unchanged),
    ):
        print(
            f"  {name:<11} {score.n_cells:>7,} {score.mae:>7.3f} "
            f"{score.bias:>+8.3f} {score.spatial_r2:>7.2f}  | "
            f"{score.mae_debiased:>7.3f} {score.spatial_r2_debiased:>7.2f}"
        )
    print("    A large negative bias means the model could not reach an unseen year's")
    print("    absolute temperature. The right-hand pair says whether it still ranked")
    print("    the tile correctly - a different failure, with a different fix.")

    error = report.change_effect_error
    matched = report.matched_effect_error
    matched_observed = report.matched_observed_effect

    print(f"\n  {'':<28} {'raw':>9} {'matched':>9}")
    print(f"  {'-' * 28} {'-' * 9} {'-' * 9}")
    print(
        f"  {'observed effect':<28} {report.observed_change_effect:>+9.3f} "
        f"{matched_observed.value:>+9.3f}"
    )
    print(f"  {'CHANGE-EFFECT ERROR':<28} {error:>+9.3f} {matched.value:>+9.3f}  degC")
    print(
        f"\n    matched on land cover x baseline-NDVI decile: "
        f"{matched.matched_cells:,} of {matched.matched_cells + matched.unmatched_cells:,} "
        f"changed cells had a control"
    )
    if matched.unmatched_cells:
        print(f"    {matched.unmatched_cells:,} changed cells had no comparable control "
              "and are excluded")
    print("    Quote the matched column. Land that greens starts low-NDVI and often")
    print("    urban-fringe, so the raw column compares it against a tile it differs")
    print("    from in exactly the ways that predict temperature.")
    print("\n    = model bias at changed cells minus bias at controls. The whole-window")
    print("    offset cancels. Positive = under-predicted the cooling.")

    # Judged against the observed effect rather than a fixed band: getting a 3 degC
    # change wrong by 0.5 degC is a different result from getting a 0.4 degC change
    # wrong by the same amount.
    observed = abs(report.observed_change_effect)
    if observed < 0.2:
        print("\n  INCONCLUSIVE  nothing measurable happened on the ground.")
    elif abs(error) <= 0.5 * observed:
        print(f"\n  OK    the model captured most of a {observed:.2f} degC observed effect.")
    else:
        print(f"\n  WEAK  the model missed more than half of a {observed:.2f} degC effect.")
        print("        Report this number. It is the honest limit of the claim.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zarr", type=Path, default=None, help="Cube to validate")
    parser.add_argument("--before", nargs="*", default=None, help="Training windows")
    parser.add_argument("--after", nargs="*", default=None, help="Post-change windows")
    parser.add_argument("--predict", default=None, help="Window to hindcast (default: last after)")
    parser.add_argument("--sigma", type=float, default=DEFAULT_SIGMA)
    parser.add_argument("--min-site-cells", type=int, default=MIN_SITE_CELLS)
    parser.add_argument("--top", type=int, default=8, help="Sites to list")
    args = parser.parse_args(argv)

    settings = get_settings()
    path = args.zarr or settings.serve_zarr_store
    if not path.exists():
        print(f"no cube at {path}", file=sys.stderr)
        return 1

    cube = open_cube(path)
    grid = grid_for_tile(settings.tile)

    # Summers only. Winter's tree-vs-built contrast is 0.31-0.80 degC, small enough that
    # a hindcast there measures composite noise rather than the effect of a change.
    summers, rejected = _usable_summers(cube)
    before, after = _split(summers, args.before, args.after)
    predict = args.predict or after[-1]

    print(f"  cube      {path}")
    print(f"  summers   {len(summers)} usable: {', '.join(summers)}")
    for label, variable, fraction in rejected:
        print(f"  SKIPPED   {label}: {variable} only {fraction:.1%} valid")

    field = detect_change(
        cube, before, after, sigma=args.sigma, min_site_cells=args.min_site_cells
    )
    _print_sites(field, grid, args.top)

    if not field.sites:
        return 1

    report = run_hindcast(cube, before, predict, field)
    _print_report(report)
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
