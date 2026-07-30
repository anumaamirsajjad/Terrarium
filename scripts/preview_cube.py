"""Render the State Cube as PNGs for visual review.

    uv run python scripts/preview_cube.py --out <folder>

One image per variable plus a combined overview, all plotted on a lat/lon axis so they
can be compared against a real map of Lahore. Read-only: this never modifies the cube.

Purely a human sanity-check tool. Nothing in the package imports it, and it is not a
substitute for the assertions in `state/cube.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # No display on this box; render straight to file.

import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from matplotlib.colors import BoundaryNorm, ListedColormap
from pyproj import Transformer

from terrarium.config import get_settings
from terrarium.state.cube import CUBE_VARIABLES, VariableSpec
from terrarium.state.grid import Grid, grid_for_tile
from terrarium.state.store import open_cube

# Official ESA WorldCover palette, so the land-cover render matches published maps.
WORLDCOVER_STYLE: dict[int, tuple[str, str]] = {
    10: ("#006400", "tree cover"),
    20: ("#ffbb22", "shrubland"),
    30: ("#ffff4c", "grassland"),
    40: ("#f096ff", "cropland"),
    50: ("#fa0000", "built-up"),
    60: ("#b4b4b4", "bare / sparse"),
    70: ("#f0f0f0", "snow and ice"),
    80: ("#0064c8", "permanent water"),
    90: ("#0096a0", "herbaceous wetland"),
    95: ("#00cf75", "mangroves"),
    100: ("#fae6a0", "moss and lichen"),
}

# Continuous variables read best on different ramps: temperature diverging-warm,
# vegetation green, elevation terrain.
COLORMAPS: dict[str, str] = {
    "lst_c": "inferno",
    "ndvi": "RdYlGn",
    "ndbi": "RdBu_r",
    "albedo": "cividis",
    "elevation_m": "terrain",
}

# Point landmarks for orientation. Approximate centres, good to a few hundred metres —
# enough to answer "is the old city roughly here?", not survey data.
LANDMARKS: tuple[tuple[float, float, str], ...] = (
    (74.3142, 31.5880, "Walled City / Fort"),
    (74.3294, 31.5497, "Mall Rd / centre"),
    (74.4036, 31.5216, "Airport"),
    (74.2600, 31.4800, "Thokar Niaz Baig"),
)


def _to_wgs84(ds: xr.Dataset, spec: VariableSpec, grid: Grid) -> xr.DataArray:
    """Reproject one variable to EPSG:4326 purely so it can be plotted on lat/lon axes.

    Display only — the cube on disk stays in its projected CRS.
    """
    data = ds[spec.name]
    if spec.is_categorical:
        # Class codes must not be interpolated, even for a picture.
        data = data.where(data != spec.fill_value)
        resampling = "nearest"
    else:
        resampling = "bilinear"

    out = data.rio.write_crs(grid.crs).rio.reproject("EPSG:4326", resampling=_rio_enum(resampling))
    return out.where(out != out.rio.nodata)


def _rio_enum(name: str) -> Any:
    from rasterio.enums import Resampling as RioResampling

    return RioResampling[name]


def _extent(data: xr.DataArray) -> list[float]:
    """imshow extent [left, right, bottom, top] in degrees."""
    x = np.asarray(data["x"].values)
    y = np.asarray(data["y"].values)
    return [float(x.min()), float(x.max()), float(y.min()), float(y.max())]


def _decorate(ax: Any, extent: list[float], *, landmarks: bool = True) -> None:
    ax.set_xlabel("longitude (deg E)")
    ax.set_ylabel("latitude (deg N)")
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.grid(color="white", alpha=0.25, linewidth=0.5, linestyle=":")

    if not landmarks:
        return
    for lon, lat, label in LANDMARKS:
        if extent[0] <= lon <= extent[1] and extent[2] <= lat <= extent[3]:
            ax.plot(lon, lat, marker="o", markersize=5, color="white", markeredgecolor="black")
            ax.annotate(
                label,
                (lon, lat),
                textcoords="offset points",
                xytext=(6, 4),
                fontsize=7,
                color="white",
                path_effects=None,
                bbox={"boxstyle": "round,pad=0.2", "fc": "black", "alpha": 0.55, "lw": 0},
            )


def _plot_categorical(ax: Any, data: xr.DataArray, extent: list[float]) -> list[Any]:
    values = np.asarray(data.values)
    present = sorted({int(v) for v in np.unique(values[np.isfinite(values)])})
    present = [c for c in present if c in WORLDCOVER_STYLE]

    cmap = ListedColormap([WORLDCOVER_STYLE[c][0] for c in present])
    bounds = [*range(len(present) + 1)]
    norm = BoundaryNorm(bounds, cmap.N)

    # Remap sparse class codes onto dense indices so the discrete colormap lines up.
    indexed = np.full(values.shape, np.nan)
    for i, code in enumerate(present):
        indexed[values == code] = i

    ax.imshow(indexed, extent=extent, origin="upper", cmap=cmap, norm=norm, interpolation="none")
    return [
        plt.Rectangle(
            (0, 0), 1, 1, fc=WORLDCOVER_STYLE[c][0], ec="none",
            label=f"{c}  {WORLDCOVER_STYLE[c][1]}",
        )
        for c in present
    ]


def _render_variable(ds: xr.Dataset, spec: VariableSpec, grid: Grid, out_dir: Path) -> Path | None:
    data = _to_wgs84(ds, spec, grid)
    values = np.asarray(data.values)
    if not np.isfinite(values).any():
        print(f"  {spec.name:<14} SKIPPED (no valid data)")
        return None

    extent = _extent(data)
    fig, ax = plt.subplots(figsize=(7.5, 7.0), dpi=140)

    if spec.is_categorical:
        handles = _plot_categorical(ax, data, extent)
        # Outside the axes: in-plot the legend sat over the north-west corner and hid
        # the River Ravi, which is one of the main things to check on this map.
        ax.legend(
            handles=handles,
            loc="upper left",
            bbox_to_anchor=(1.02, 1.0),
            fontsize=8,
            frameon=False,
        )
        ax.set_title(f"{spec.name}  [{spec.units}]\n{spec.description}", fontsize=9)
    else:
        finite = values[np.isfinite(values)]
        # Clip the display range to 2-98% so a handful of outliers cannot flatten the
        # whole image into one colour. The data itself is untouched.
        vmin, vmax = np.percentile(finite, [2, 98])
        im = ax.imshow(
            values,
            extent=extent,
            origin="upper",
            cmap=COLORMAPS.get(spec.name, "viridis"),
            vmin=vmin,
            vmax=vmax,
            interpolation="nearest",
        )
        bar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
        bar.set_label(f"{spec.name} [{spec.units}]  (2-98% stretch)", fontsize=8)
        ax.set_title(
            f"{spec.name}  [{spec.units}]\n{spec.description}\n"
            f"actual range {finite.min():.3f} to {finite.max():.3f}",
            fontsize=9,
        )

    _decorate(ax, extent)
    fig.tight_layout()

    path = out_dir / f"{spec.name}.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  {spec.name:<14} -> {path.name}")
    return path


def _render_overview(ds: xr.Dataset, grid: Grid, out_dir: Path) -> Path:
    """All variables side by side, for spotting misalignment between layers at a glance."""
    specs = list(CUBE_VARIABLES)
    fig, axes = plt.subplots(2, 3, figsize=(16, 11), dpi=130)

    for ax, spec in zip(axes.ravel(), specs, strict=False):
        data = _to_wgs84(ds, spec, grid)
        values = np.asarray(data.values)
        extent = _extent(data)

        if not np.isfinite(values).any():
            ax.text(0.5, 0.5, f"{spec.name}\nMISSING", ha="center", va="center", fontsize=12)
            ax.set_axis_off()
            continue

        if spec.is_categorical:
            _plot_categorical(ax, data, extent)
        else:
            finite = values[np.isfinite(values)]
            vmin, vmax = np.percentile(finite, [2, 98])
            im = ax.imshow(
                values,
                extent=extent,
                origin="upper",
                cmap=COLORMAPS.get(spec.name, "viridis"),
                vmin=vmin,
                vmax=vmax,
                interpolation="nearest",
            )
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)

        ax.set_title(f"{spec.name} [{spec.units}]", fontsize=10)
        _decorate(ax, extent)

    fig.suptitle(
        "Terrarium State Cube - Lahore tile, EPSG:32643 @ 100 m, plotted on WGS84 axes",
        fontsize=12,
    )
    fig.tight_layout()
    path = out_dir / "overview.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _render_footprint(grid: Grid, ds: xr.Dataset, out_dir: Path) -> Path:
    """The tile outline with landmarks and detected water, for geographic orientation."""
    to_wgs = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    left, bottom, right, top = grid.bounds
    xs = [left, right, right, left, left]
    ys = [bottom, bottom, top, top, bottom]
    lons, lats = to_wgs.transform(xs, ys)

    fig, ax = plt.subplots(figsize=(8, 8), dpi=140)
    ax.plot(lons, lats, color="black", linewidth=1.6, label="tile bbox")

    water = _water_lonlat(ds, grid)
    if water is not None:
        wlon, wlat = water
        ax.scatter(wlon, wlat, s=3, color="#0064c8", label="water pixels (landcover=80)")
        ax.plot(
            wlon.mean(),
            wlat.mean(),
            marker="X",
            markersize=13,
            color="#0064c8",
            markeredgecolor="white",
            markeredgewidth=1.4,
            linestyle="none",
            label="water centroid",
        )

    for lon, lat, label in LANDMARKS:
        ax.plot(lon, lat, marker="o", markersize=6, color="crimson", linestyle="none")
        ax.annotate(label, (lon, lat), textcoords="offset points", xytext=(7, 4), fontsize=8)

    pad = 0.02
    ax.set_xlim(min(lons) - pad, max(lons) + pad)
    ax.set_ylim(min(lats) - pad, max(lats) + pad)
    ax.set_xlabel("longitude (deg E)")
    ax.set_ylabel("latitude (deg N)")
    ax.set_title(
        "Lahore tile footprint (WGS84)\n"
        "water pixels trace the River Ravi; landmarks are approximate centres",
        fontsize=10,
    )
    ax.grid(alpha=0.3, linestyle=":")
    ax.legend(loc="lower left", fontsize=8)
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()

    path = out_dir / "footprint.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _water_lonlat(ds: xr.Dataset, grid: Grid) -> tuple[np.ndarray, np.ndarray] | None:
    """Longitude/latitude of every permanent-water pixel, in the cube's own projection."""
    if "landcover" not in ds.data_vars:
        return None
    mask = np.asarray(ds["landcover"].values) == 80
    if not mask.any():
        return None

    rows, cols = np.nonzero(mask)
    xs = grid.x_coords()[cols]
    ys = grid.y_coords()[rows]
    to_wgs = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    lon, lat = to_wgs.transform(xs, ys)
    return np.asarray(lon), np.asarray(lat)


def _print_summary(ds: xr.Dataset, grid: Grid, out_dir: Path) -> None:
    to_wgs = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    left, bottom, right, top = grid.bounds

    corners = {
        "SW": (left, bottom),
        "SE": (right, bottom),
        "NE": (right, top),
        "NW": (left, top),
    }

    print(f"\n{'=' * 74}")
    print("  GEOGRAPHIC SUMMARY")
    print(f"{'=' * 74}")
    print(f"  grid shape        {grid.shape[0]} x {grid.shape[1]} (y, x) = "
          f"{grid.shape[0] * grid.shape[1]:,} pixels @ {grid.resolution_m} m")
    print(f"  projected CRS     {grid.crs}")
    print(f"  projected bounds  {tuple(round(b) for b in grid.bounds)}\n")

    print("  bbox corners (WGS84)")
    for name, (x, y) in corners.items():
        lon, lat = to_wgs.transform(x, y)
        print(f"    {name}   {lat:9.5f} N,  {lon:9.5f} E")

    cx, cy = (left + right) / 2, (bottom + top) / 2
    clon, clat = to_wgs.transform(cx, cy)
    print(f"    mid  {clat:9.5f} N,  {clon:9.5f} E\n")

    water = _water_lonlat(ds, grid)
    if water is None:
        print("  water pixels      none found (landcover=80 absent)\n")
    else:
        wlon, wlat = water
        print(f"  water pixels      {len(wlon):,} px ({len(wlon) / ds['landcover'].size:.2%})")
        print(f"    centroid        {wlat.mean():9.5f} N,  {wlon.mean():9.5f} E")
        print(f"    lat range       {wlat.min():9.5f} .. {wlat.max():9.5f} N")
        print(f"    lon range       {wlon.min():9.5f} .. {wlon.max():9.5f} E")
        print("    (expected: north-west of the tile, tracing the River Ravi)\n")

    print(f"  images written to {out_dir}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zarr", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True, help="output folder for PNGs")
    args = parser.parse_args(argv)

    settings = get_settings()
    grid = grid_for_tile(settings.tile)
    path = args.zarr or settings.zarr_store

    if not path.exists():
        print(f"no cube at {path} - run scripts/build_tile.py first")
        return 1

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    ds = open_cube(path)

    print(f"\n  rendering {len(CUBE_VARIABLES)} variables from {path}")
    for spec in CUBE_VARIABLES:
        _render_variable(ds, spec, grid, out_dir)

    print(f"  {'overview':<14} -> {_render_overview(ds, grid, out_dir).name}")
    print(f"  {'footprint':<14} -> {_render_footprint(grid, ds, out_dir).name}")

    _print_summary(ds, grid, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
