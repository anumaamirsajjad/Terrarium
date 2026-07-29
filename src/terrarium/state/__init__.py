"""Layer 1b - State Cube: the single source of truth.

Owns the canonical grid (CRS, resolution, transform), alignment of every ingested
layer onto it, and persistence to Zarr + DuckDB. If two layers disagree on shape or
coordinates, the bug is here.
"""
