# Terrarium

A neighbourhood-scale digital twin for climate interventions.

Pick a real 20 km city tile, apply an intervention — plant trees, ban vehicles, add
green infrastructure — and see the modelled effect on temperature, pollution, flood
risk, and equity of exposure, rendered on the map.

**v1 tile:** Lahore, Pakistan — `[74.2533, 31.4305, 74.4641, 31.6103]`
**v1 grid:** EPSG:32643 (UTM 43N), 100 m, 201 × 202 pixels
**v1 core:** thermal only (LightGBM land-surface-temperature emulator)

See [CLAUDE.md](CLAUDE.md) for architecture, conventions, and scope boundaries, and
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for what is built today and
what is next.

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
per-asset debug logging, `--out PATH` to write elsewhere.

If a collection fails after its retries — Planetary Computer drops connections
occasionally — the build continues and reports those variables as `MISSING`. Re-run it;
the build is idempotent and total.

### 5. Run the frontend

In a **second terminal**, leaving the API running:

```bash
cd web
npm install
npm run dev
```

Open http://localhost:5173. The page calls `/health` and renders the tile details.
If it shows a connection error, the API in step 3 isn't running.

### Everyday commands

```bash
uv run pytest              # tests (co-located with modules as test_*.py)
uv run ruff check src/     # lint
uv run ruff format src/    # format
uv run mypy                # type check

uv add <package>           # add a dependency (updates pyproject.toml + uv.lock)
uv add --dev <package>     # add a dev-only dependency
uv sync                    # re-sync after pulling teammates' changes
```

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
| 2. Physics Core | `cores/` | Simulators | Pure functions — no I/O of any kind |
| 3. Intelligence | `api/` | HTTP, orchestration, scenario diffs | Composition root |

Data flows strictly downward; no lower layer imports from a higher one.
