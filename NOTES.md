# Notes

Transient state and things to do before demo. Anything that turns out to be a durable
rule belongs in `CLAUDE.md` instead — this file is meant to shrink.

## TODO

### The cube is missing both 2024 windows

**Status:** open. Blocks the leave-one-window-out CV from meaning anything.

`data/processed/cube.zarr` has the right schema (4 windows, 10 variables) but only
`2023-summer` and `2023-winter` carry usable data:

| window | lst_c | ndvi / ndbi / albedo |
|---|---|---|
| 2023-summer | 100 % | 100 % |
| 2023-winter | 100 % | 100 % |
| 2024-summer | 100 % | **0 %** |
| 2024-winter | **0 %** | **0 %** |

Sentinel-2 failed to ingest for both 2024 windows across repeated attempts — network, not
code (see below). `build_tile.py` correctly exited non-zero each time.

**Why it matters:** Phase 4's leave-one-window-out CV currently trains on one season and
is scored on the other, which is degenerate — it reports MAE 17.7 °C and 3.8 % skill. That
is a floor, not a measurement. Four windows would make it a real test of whether the model
reaches an unseen date.

```bash
uv run python scripts/build_tile.py            # rerun until it exits 0
uv run python scripts/train_thermal.py         # then the LOWO number becomes meaningful
```

`write_cube` is `mode="w"`, so a *partial* run overwrites a better one — rerun the whole
build until exit 0 rather than accepting one that reports `MISSING`.

### Rebuild also refreshes the stale `elevation_m` wording

Folded into the rebuild above. `state/cube.py` describes GLO-30 correctly as a Digital
*Surface* Model (includes buildings and canopy); variable attrs are written into Zarr at
build time, so the store keeps the old "Ground elevation" text until the next build.
Metadata only — the pixel data is correct either way.

## Environment

**This machine's network is the main obstacle to any real build.** DNS drops individual
Azure hosts independently and intermittently, and connections stall mid-transfer. Observed
across several sessions:

- `Could not resolve host: sentinel2l2a01.blob.core.windows.net` while other hosts resolve
- `RemoteDisconnected`, `Chunk and warp failed`, truncated GeoTIFFs
- one read that hung **16.4 hours** before returning

The pipeline now guards all of these: per-source retry with exponential backoff, GDAL
HTTP/connect/low-speed timeouts, and `Content-Length` verification on the WorldPop
download. A failed build here is usually the network. Check before debugging code:

```bash
python -c "import socket; socket.gethostbyname('sentinel2l2a01.blob.core.windows.net')"
```
