# Notes

Transient state and things to do before demo. Anything that turns out to be a durable
rule belongs in `CLAUDE.md` instead — this file is meant to shrink.

## TODO

### Rebuild the cube before demo / submission — stale `elevation_m` metadata

**Status:** open. Affects metadata only; the pixel data is correct.

The on-disk Zarr at `data/processed/cube.zarr` was built before the `elevation_m`
description was corrected, so its stored attrs still read:

> `Ground elevation above EGM2008 geoid, Copernicus DEM GLO-30`

`state/cube.py` now correctly says it is a Digital **Surface** Model — GLO-30 includes
buildings and canopy, which is visible in the render as the built-up core sitting ~7 m
above its surroundings. It is an urban-form proxy, not terrain height.

Variable attrs are written into the Zarr store at build time, so editing `cube.py` alone
does not update what is on disk. One rebuild fixes it:

```bash
uv run python scripts/build_tile.py
uv run python scripts/inspect_cube.py   # confirm the wording, then delete this entry
```

**Why it matters:** anything reading the cube's own metadata — the API, a future data
dictionary, a reviewer opening the Zarr directly — currently gets the wrong description.
Nothing downstream reads that attr today, so this is not urgent, only pre-demo hygiene.

**Cost:** ~4–6 minutes, network permitting. The build is idempotent and total
(`mode="w"`), so a *partial* run overwrites a good cube — rerun until it exits 0 rather
than accepting a run that reports `MISSING` variables. This machine's DNS drops
individual Azure hosts intermittently, so expect to need more than one attempt.
