# Terrarium

A neighbourhood-scale **digital twin**. A user draws or selects an intervention on a real
city tile — plant 5,000 trees along these streets, ban combustion vehicles inside this
ring, add a retention pond here — and Terrarium returns modelled deltas in **temperature,
pollution, flood risk, and equity of exposure**, rendered on the map.

The claim we are making is *"here is what this specific street would feel like"*, not
*"here is a national climate scenario"*. Everything in the architecture follows from that:
high spatial resolution, small spatial extent, fast enough to feel interactive.

---

## Architecture: three layers

Data flows strictly downward. Nothing in a lower layer imports from a higher one.

```
┌──────────────────────────────────────────────────────────────┐
│  3. INTELLIGENCE      api/          agents, orchestration,   │
│                                     FastAPI, scenario diffs  │
├──────────────────────────────────────────────────────────────┤
│  2. PHYSICS CORE      cores/        pure simulators:         │
│                                     thermal, (later: air,    │
│                                     hydro, equity)           │
├──────────────────────────────────────────────────────────────┤
│  1. STATE CUBE        ingest/       satellite + vector →     │
│                       state/        one aligned raster cube  │
└──────────────────────────────────────────────────────────────┘
```

### Layer 1 — State Cube (`ingest/`, `state/`)

Turns messy external reality into **one analysis-ready xarray Dataset** on a fixed grid.

- `ingest/` talks to the outside world: STAC search, COG reads, OSM/vector pulls. This is
  the *only* place network I/O is allowed.
- `state/` owns the canonical grid definition, alignment/reprojection, the Zarr store, and
  the DuckDB catalogue of what has been ingested.
- Output contract: every variable shares one CRS, one resolution, one bounding box, one
  set of coordinates. If two layers don't align, that is a `state/` bug, not a core bug.

The cube is the **single source of truth**. A physics core never re-reads a GeoTIFF.

### Layer 2 — Physics Core (`cores/`)

Simulators. Each core answers one question and nothing else.

A core is a **pure function**:

```
core(baseline_cube, intervention) -> result_cube
```

No file reads. No network. No database. No global state. No logging side effects that
change behaviour. Given the same inputs it returns the same outputs, always. This is what
makes cores testable, cacheable, parallelisable, and swappable.

v1 ships exactly one core: **thermal**, a LightGBM emulator that predicts land surface
temperature from land cover, albedo, NDVI, imperviousness, and meteorology. We train it
against observed LST so it learns the local empirical relationship rather than us hand-
rolling a surface energy balance under hackathon time pressure.

### Layer 3 — Intelligence (`api/`)

The composition root. Loads the cube, validates requests, calls cores, diffs baseline
against scenario, serialises for the map. Later this layer grows agents that *choose*
interventions; in v1 it just executes what the user asked for.

---

## Tech stack

| Concern            | Choice                                    | Why |
|--------------------|-------------------------------------------|-----|
| Language           | **Python 3.12**                           | Pinned `>=3.12,<3.13`; the geo stack lags on 3.13 wheels |
| Env / packaging    | **uv**                                    | The geo dependency tree is heavy; uv resolves it in seconds |
| API                | **FastAPI** + uvicorn                     | Async, Pydantic-native, free OpenAPI docs |
| Satellite search   | **pystac-client** + **planetary-computer**| STAC search against Microsoft Planetary Computer, with token signing |
| Raster loading     | **odc-stac** → **xarray**                 | STAC items straight to a lazy, aligned, dask-backed cube |
| Raster storage     | **Zarr** (v2)                             | Chunked, cloud-shaped, appendable |
| Tabular / catalog  | **DuckDB**                                | Zero-server analytics over run metadata + zonal stats |
| Emulator           | **LightGBM**                              | Fast to train, fast to infer, handles tabular pixel features well |
| Frontend           | **React** + **Vite**                      | |
| Map                | **MapLibre GL** + **deck.gl**             | MapLibre for basemap, deck.gl for the data overlays |

### Data source

Microsoft Planetary Computer, anonymous access with request signing via
`planetary_computer.sign_inplace`. Primary collections:

- `landsat-c2-l2` — surface temperature (ST_B10) and optical bands, 30 m native
- `sentinel-2-l2a` — NDVI / NDBI / albedo, 10–20 m native
- `cop-dem-glo-30` — elevation, 30 m native
- `esa-worldcover` — land cover classification, 10 m native

These are *source* resolutions. Everything is resampled onto the single analysis grid —
**100 m**, `Tile.target_resolution_m` in `config.py` — which is what every physics core
assumes. Do not confuse the two: `NATIVE_RESOLUTION_M` describes the inputs,
`target_resolution_m` describes the cube. Over the Lahore bbox that grid is **201 × 202**.

Resampling method is a property of the variable's *meaning*, declared once in
`state/cube.py`: nearest for anything whose values are labels (land cover classes, QA
bitmasks), bilinear for anything measured on a continuous scale.

---

## Folder structure

```
terrarium/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example
├── src/terrarium/
│   ├── config.py           # settings + THE tile bbox. Single source of truth.
│   ├── ingest/             # ← ONLY layer permitted to do network I/O
│   │   ├── client.py       #   PC STAC search, signing, odc-stac load
│   │   └── pipeline.py     #   masking, unit conversion, cube assembly
│   ├── state/              # the cube: grid, alignment, Zarr, DuckDB catalog
│   │   ├── grid.py         #   canonical CRS / resolution / transform
│   │   ├── cube.py         #   variable + resampling contract; validate, summarise
│   │   └── store.py        #   Zarr + DuckDB persistence
│   ├── cores/              # ← PURE. no I/O, no network, no globals.
│   │   ├── base.py         #   Core protocol every simulator implements
│   │   └── thermal/        #   v1's only core
│   │       ├── features.py #   cube → feature matrix
│   │       ├── model.py    #   LightGBM train / predict
│   │       └── simulate.py #   apply intervention, return delta
│   └── api/
│       ├── main.py         #   app factory, CORS, router wiring
│       ├── routes/         #   HTTP endpoints (thin)
│       └── schemas/        #   Pydantic request/response contracts
├── web/                    # React + Vite + MapLibre + deck.gl
├── data/                   # gitignored: raw/ interim/ processed/
├── scripts/                # one-off CLI entrypoints
│   ├── build_tile.py       #   full ingest -> State Cube, with a build report
│   └── inspect_cube.py     #   read back and summarise a built cube
└── docs/
```

---

## Coding conventions

**Type hints everywhere.** Every function signature — parameters and return. `mypy
--strict` is the target. Untyped third-party geo libraries get an `ignore_missing_imports`
override in `pyproject.toml`, never a bare `# type: ignore` at the call site.

**`cores/` is pure.** No `open()`, no `requests`, no `xr.open_zarr`, no `datetime.now()`,
no reading config. Everything a core needs arrives as an argument. If you find yourself
wanting I/O inside a core, the data should have been put in the cube by `state/` instead.
This is the single most important rule in the codebase — it is what lets us test physics
without a network and swap emulators without touching the API.

**Pydantic models for all data contracts.** Every boundary — HTTP request, HTTP response,
core input, core output, config — is a Pydantic model. No bare dicts crossing a module
boundary. Models live in `api/schemas/` for HTTP and next to the core for core contracts.

**Tests alongside each module.** `foo.py` is tested by `test_foo.py` in the same directory.
Not a mirrored `tests/` tree — proximity keeps them honest and makes deletion obvious when
a module dies. They are excluded from the built wheel.

**Naming.** `snake_case` functions, `PascalCase` models, `SCREAMING_CASE` constants.
Geospatial variables carry units in the name: `temp_c`, `area_m2`, `dist_m`.

**xarray discipline.** Always name dimensions (`("y", "x")`, never positional). Always
carry CRS in `.rio.crs`. Never `.values` a lazy array until you actually need it in memory.

---

## Scope for v1 — read this before adding anything

v1 is deliberately narrow. The point is one convincing vertical slice, not four shallow
ones.

**In scope:**

- **ONE tile.** Lahore, Pakistan. Hardcoded bbox in `config.py`:
  `[74.2533, 31.4305, 74.4641, 31.6103]` — roughly 20 km × 20 km centred on
  31.5204 N, 74.3587 E. No tile selection UI. No multi-city support. No dynamic bbox.
- **ONE physics core.** Thermal only. LightGBM LST emulator.
- **ONE intervention type** to start: tree planting / land-cover change.
- Baseline vs scenario diff, rendered on a deck.gl layer.

**Explicitly NOT in v1** — do not scaffold, stub, or "just add the interface for" these:

- ❌ Agents, tool-calling, LLM orchestration
- ❌ Voice input
- ❌ VLM / image understanding
- ❌ Pollution, flood, and equity cores
- ❌ Auth, user accounts, persistence of user scenarios
- ❌ Multi-tenancy, deployment infra, Docker

The layer boundaries above exist so these can be added later without a rewrite. That is
the *only* concession v1 makes to them. Adding a placeholder module for a v2 feature is a
scope violation, not foresight.

---

## Commands

```bash
uv sync --extra dev              # install everything
uv run terrarium-api             # API on :8000, docs at /docs
uv run pytest                    # tests
uv run ruff check src/           # lint
uv run mypy                      # types

cd web && npm install && npm run dev    # frontend on :5173
```

## Conventions for working in this repo

- Check `config.py` before hardcoding any constant — the bbox, CRS, and resolution live
  there and nowhere else.
- When adding a dependency, add it to `pyproject.toml`, never `pip install` into the venv.
- If a change makes a core impure, stop and reconsider the design.
