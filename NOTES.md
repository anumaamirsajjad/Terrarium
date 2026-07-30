# Notes

Transient state and things to do before demo. Anything that turns out to be a durable
rule belongs in `CLAUDE.md` instead — this file is meant to shrink.

Sequenced work lives in `docs/IMPLEMENTATION_PLAN.md`, not here.

## TODO

Nothing open.

## Recently closed

### Stale `elevation_m` metadata in the on-disk Zarr — **closed 2026-07-30**

The store predated the corrected description and still called GLO-30 a ground-elevation
model. Rebuilt (`build e6e3c768f392`, 116 s, 6/6 variables populated); the store now
carries *"Surface elevation (DSM, incl. buildings) above EGM2008"*. Verified with
`inspect_cube.py`.

Same rebuild also picked up the albedo normalisation fix, so the cube on disk is current
with `state/cube.py` and `ingest/pipeline.py` as of that commit.

**Rebuild whenever a `VariableSpec` description, unit, or formula changes** — variable
attrs are written into Zarr at build time, so editing `cube.py` alone does not update
what is on disk.
