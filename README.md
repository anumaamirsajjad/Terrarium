# Terrarium

A neighbourhood-scale digital twin for climate interventions.

Pick a real 20 km city tile, apply an intervention — plant trees, ban vehicles, add
green infrastructure — and see the modelled effect on mid-morning land surface
temperature, air quality, and equity of exposure, rendered on the map.

**v1 tile:** Lahore, Pakistan — `[74.2533, 31.4305, 74.4641, 31.6103]`
**v1 grid:** EPSG:32643 (UTM 43N), 100 m, 201 × 202 pixels

### What it does today

Three simulators, all reading one aligned raster cube, all reached through one
`POST /simulate`:

| Core | Answers | Method |
|---|---|---|
| **thermal** | Δ **mid-morning land surface temperature** | LightGBM emulator trained on Landsat ST_B10 |
| **air** | Δ **locally-generated PM2.5** | steady-state Gaussian plume over an OSM emission inventory, one FFT over the tile |
| **equity** | who receives the cooling | person-degrees by population decile, over WorldPop |

Around them:

- **A plan language** (`dsl/`). "Plant 5,000 trees", in English or Urdu, typed or spoken.
  `POST /plan` checks it against the polygon *before* any core runs and **refuses** what
  does not fit, with the arithmetic that refused it — a plan that cannot fit must not come
  back as a small delta that reads like a plan which merely worked badly.
- **A costed preset library**, `calibrated: false` everywhere — literature unit costs, good
  for ranking two plans, not for a budget.
- **A deterministic brief.** Findings, uncertainties and a confidence that is never
  `"high"`, written from templates rather than by a model, so it cannot restate a figure it
  was not given.
- **Citizen photos.** `POST /observations` reads a street photo into a typed observation and
  places it on the same 201 × 202 grid — beside the cube, never inside it.
- **Voice capture** in English and Urdu, using the browser's own `SpeechRecognition`.
- **A council brief**, printed to PDF by the browser's own print dialog.

### Two naming rules, and they are not pedantry

- **Mid-morning land surface temperature.** Landsat crosses Lahore at ~10:30 local and
  ST_B10 measures the *surface*, which runs several degrees above air temperature and peaks
  after the overpass. Never "temperature" unqualified, never "afternoon".
- **Locally-generated PM2.5.** The inventory covers this tile's own roads, so the regional
  background that dominates Lahore's absolute PM2.5 is absent by construction. Quote deltas,
  not levels, and say the magnitudes are **uncalibrated**. `scripts/validate_air.py` has now
  run against 53 OpenAQ monitors and the core **does not beat a null model** — the delta
  still stands, because the background cancels in a difference, but the *spatial* pattern
  is tested and unevidenced. See [docs/AUDIT.md](docs/AUDIT.md) A9.

### Cost

**Nothing here requires a credit card.** Every data source, library and basemap tile is
free and keyless. The one optional key is `TERRARIUM_GEMINI_API_KEY` (free tier, no card):
without it the planner falls back to a deterministic regex parser and everything still
works — the single exception is `POST /observations`, which answers **503 with the reason**,
because no rule parser can read a photograph. `TERRARIUM_OPENAQ_KEY` (also free) is needed
only by `scripts/validate_air.py`.

See [CLAUDE.md](CLAUDE.md) for architecture, conventions, and scope boundaries;
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for what was built and what was
decided; and [docs/AUDIT.md](docs/AUDIT.md) for what is currently broken. The plan says
what was built, the audit says what runs — check the audit before a demo.

## Getting Started

Everything you need after cloning. No prior uv experience assumed.

### 1. Install uv

[uv](https://docs.astral.sh/uv/) is our Python package manager — it replaces `pip`,
`venv`, `pip-tools`, and `pyenv`. You install it **once per machine**, not per project.

**Windows** (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux**:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close and reopen your terminal, then confirm it worked:

```bash
uv --version
```

> You do **not** need to install Python yourself. uv reads the `requires-python`
> pin in `pyproject.toml` and downloads a matching Python 3.12 automatically.

### 2. Install the project

From the repository root:

```bash
uv sync --extra dev
```

This creates a virtual environment and installs every dependency at the **exact**
version recorded in `uv.lock` — the same versions everyone else on the team has.
First run downloads ~550 MB of geospatial and ML wheels and takes a few minutes;
later runs are near-instant.

There is no "activate the venv" step. Prefix commands with `uv run` and uv uses the
right environment automatically.

### 3. Run the API

```bash
uv run terrarium-api
```

| | |
|---|---|
| API | http://127.0.0.1:8000 |
| Interactive docs | http://127.0.0.1:8000/docs |
| Health check | http://127.0.0.1:8000/health |

`/health` returns the active tile (Lahore) and should respond immediately.

The routes, in the order you would meet them:

| | |
|---|---|
| `GET /health` | tile + liveness. Answers without a cube |
| `GET /cube/summary` | variables, windows, per-window validity |
| `GET /cube/layer/{name}?window=` | one variable as a raster |
| `GET /plan/presets` | the costed intervention library. Answers without a cube |
| `POST /plan` | text \| preset \| Plan → a checked, costed `/simulate` body, or **422 with the arithmetic that refused it** |
| `POST /simulate` | GeoJSON polygon → ΔLST + equity + ΔPM2.5 (when the plan removes emissions) + brief |
| `POST /observations` | citizen photo → typed observation on the grid. The one route that needs a key: **503** without one |
| `GET /observations`, `GET /observations/layer` | what has been reported this run, as a list and as a raster |

**A raster crosses the wire as base64 float32 plus bounds**, never as GeoJSON features —
40,602 cells as features is tens of megabytes describing a grid that three numbers already
define. The encoding is named in the payload so a client never has to guess.

**The API serves `TERRARIUM_SERVE_ZARR_STORE`, which is deliberately not where a build
writes.** That split is what lets you rebuild without pointing a demo at a half-finished
cube: builds go to `zarr_store` (or `--out`), and the serving path moves only once a build
has been checked. If the cube it is pointed at is missing, partial, or predates a variable,
the API **stays up on `/health` and answers the data routes with 503 and the reason** — a
readiness probe needs an answer, not a restart loop.

### 4. Build the State Cube

Everything downstream reads from one Zarr cube. Build it once:

```bash
uv run python scripts/build_tile.py     # ingest Lahore -> data/processed/cube.zarr
uv run python scripts/inspect_cube.py   # read it back and print value ranges
```

The build hits Microsoft Planetary Computer (no credentials needed) and takes a few
minutes on a good connection. It prints, per collection, how many scenes were found, how
many passed the cloud filter, and how many were actually composited — then the grid shape
and each variable's value range. **It exits non-zero if any variable came back empty**, so
a partially-built cube fails loudly rather than quietly feeding the thermal model nothing.

Useful flags: `--max-scenes N` to trade build time against composite depth, `-v` for
per-asset debug logging, `--out PATH` to write elsewhere, `--years 2024` for a fast
two-window smoke test.

If a collection fails after its retries — Planetary Computer drops connections
occasionally — the build continues and reports those variables as `MISSING`. Re-run it;
the build is idempotent and total.

**A cube that opens is not a cube that is complete.** An ingest that dies partway leaves
the time axis at full length with the unreached windows still holding fill values, and
shapes, coordinates and whole-cube summaries all still pass. Check per window before
trusting one:

```bash
uv run python scripts/inspect_cube.py --zarr data/processed/cube.zarr --per-window
```

Two variables can be **grafted onto an existing cube in seconds** rather than rebuilt —
the Phase 9 air layers, if your cube predates them:

```bash
uv run python scripts/build_air_layers.py --zarr data/processed/cube_phase4.zarr \
                                          --out  data/processed/cube_phase9.zarr
```

That reads OpenStreetMap through Overpass, which is free, keyless and frequently
saturated. A `504 Gateway Timeout` is its documented failure mode, not a code fault:
retry at a quieter hour, or point at a mirror, which is config rather than code —

```bash
TERRARIUM_OVERPASS_URL=https://overpass.kumi.systems/api/interpreter uv run python \
  scripts/build_air_layers.py --zarr data/processed/cube_phase4.zarr --out data/processed/cube_phase9.zarr
```

### 5. Train the thermal emulator

```bash
uv run python scripts/train_thermal.py    # train + spatially blocked CV + one worked
                                          #   intervention, on one --window
```

Spatially blocked CV shows the model generalises **across space**. It says nothing about
whether it predicts the effect of a *change* — that is what `scripts/hindcast.py` is for,
and the answer there is that the emulator over-predicts cooling by about **2.5×**, in 12 of
12 configurations. Every cooling figure the product shows is divided by that.

### 6. Run the frontend

In a **second terminal**, leaving the API running:

```bash
cd web
npm install
npm run dev
```

Open http://localhost:5173. Draw a polygon, pick an intervention, run it. If it shows a
connection error, the API in step 3 isn't running.

**Use port 5173.** The API's CORS allowlist is 5173 only — if Vite reports it is busy and
falls back to 5174, free 5173 rather than accepting the fallback, or every request will
fail CORS with nothing useful on screen.

### Everyday commands

```bash
uv run pytest              # tests (co-located with modules as test_*.py)
uv run ruff check src/ scripts/   # lint
uv run ruff format src/    # format
uv run mypy                # type check

cd web && npm run test     # vitest
cd web && npm run build    # tsc -b + production bundle

uv add <package>           # add a dependency (updates pyproject.toml + uv.lock)
uv add --dev <package>     # add a dev-only dependency
uv sync                    # re-sync after pulling teammates' changes
```

All of the above runs in CI on every push and pull request
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), including a job that reruns the
Python suite **with outbound sockets blocked** — no test may touch the network, and that
rule is only worth having if something enforces it.

Never `pip install` into the environment — the change won't be recorded, and it will
vanish on the next `uv sync`. Always use `uv add`.

### Windows + OneDrive note

If the repository lives inside a OneDrive-synced folder, OneDrive will lock files in
`.venv` and `uv sync` fails with `os error 32` (*"used by another process"*). The
virtual environment is ~550 MB across ~12,000 files and should never be synced. Fix it
by putting the environment outside the synced folder, once per machine:

```powershell
[Environment]::SetEnvironmentVariable("UV_PROJECT_ENVIRONMENT", "$HOME\.virtualenvs\terrarium", "User")
```

Reopen your terminal, delete any existing `.venv`, and re-run `uv sync`. This is a
local machine setting only — it is not required to work on the project.

## Dependency management

**`pyproject.toml` and `uv.lock` are the source of truth.** Both are committed. Edit
dependencies only via `uv add` / `uv remove`, and commit the resulting `uv.lock` so
everyone resolves identically.

`requirements.txt` is a **generated convenience file** for tools in our pipeline that
only understand pip. **Do not edit it by hand** — your changes will be overwritten.
Regenerate it whenever dependencies change:

```bash
uv export --no-hashes --no-dev --no-emit-project --format requirements-txt -o requirements.txt
```

## Architecture at a glance

| Layer | Package | Responsibility | Rule |
|-------|---------|----------------|------|
| 1. State Cube | `ingest/`, `state/` | External data → one aligned xarray cube | Only `ingest/` may touch the network |
| 2. Physics Core | `cores/` | Simulators: thermal, air, equity | Pure functions — no I/O of any kind |
| 3. Intelligence | `api/`, `dsl/` | HTTP, plan language, brief, orchestration | Composition root |

Data flows strictly downward; no lower layer imports from a higher one. Two consequences
worth knowing before reading the code:

- **A core takes its model as an argument** and never loads or trains one. Training per
  call kills the interactivity claim; opening the artefact file breaks purity. Loading is
  the caller's job — `scripts/`, or the API's startup hook.
- **The LLM lives in exactly one file**, `dsl/llm.py`, and is optional. Whatever produces a
  plan — a model, a button, a regex — it is re-validated as a `Plan` and then against the
  tile before a core sees a number. That is the entire safety argument for putting a
  free-tier model in front of a simulator.
