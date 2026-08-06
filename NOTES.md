# Notes

Transient state and things to do before demo. Anything that turns out to be a durable
rule belongs in `CLAUDE.md` instead — this file is meant to shrink.

## TODO

### The cube is missing both 2024 windows — **closed 2026-08-06**

Superseded twice over. `data/processed/cube.zarr` is still the partial build it always was
— two of its four windows are empty — but it stopped being the live blocker at Phase 4,
when `cube_phase4.zarr` replaced it, and `cube_phase9.zarr` replaced *that* on 2026-08-06.
The API now serves the Phase 9 cube: 12 variables, four windows, all 100 % valid.

Kept only as a pointer to where the live version of this concern lives: it is not here.
**[docs/AUDIT.md](docs/AUDIT.md) is the snapshot of what is currently broken**, with the
command that verifies each fix. `cube.zarr` itself is left on disk as a known-bad artefact
— `validate_windows` refuses it, which is a check worth having something to refuse.

### Rebuild also refreshes the stale `elevation_m` wording

Also closed by the above: variable attrs are written into Zarr at build time, and
`cube_phase4.zarr` (which `cube_phase9.zarr` was grafted onto) was built after `state/cube.py`
was corrected. Metadata only — the pixel data was right either way.

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
