# Terrarium

A neighbourhood-scale **digital twin**. A user draws or selects an intervention on a real
city tile — plant 5,000 trees along these streets, ban combustion vehicles inside this
ring — and Terrarium returns modelled deltas in **mid-morning land surface temperature,
air quality, and equity of exposure**, rendered on the map.

The claim we are making is *"here is what this specific street would feel like"*, not
*"here is a national climate scenario"*. Everything in the architecture follows from that:
high spatial resolution, small spatial extent, fast enough to feel interactive.

**[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) is the roadmap and the
decisions register**, and **[docs/AUDIT.md](docs/AUDIT.md) is what is currently broken** —
a snapshot with the evidence for each finding and the command that verifies its fix. The
plan says what was built; the audit says what runs. They disagree today, and the audit is
the one to check before a demo. This file is the repo's operating rules — how to write code here.
That file is what to build next and what was decided. When the two disagree, the plan
wins and this file is wrong; fix it rather than working around it. Phase status is
deliberately *not* duplicated here, because a duplicated status is a stale status.

---

## Architecture: three layers

Data flows strictly downward. Nothing in a lower layer imports from a higher one.

```
┌──────────────────────────────────────────────────────────────┐
│  3. INTELLIGENCE      api/          FastAPI, scenario diffs, │
│                       dsl/          plan language + brief    │
│                       agent/        search over the cores    │
├──────────────────────────────────────────────────────────────┤
│  2. PHYSICS CORE      cores/        pure simulators:         │
│                                     thermal, air, equity     │
├──────────────────────────────────────────────────────────────┤
│  1. STATE CUBE        ingest/       satellite + vector →     │
│                       state/        one aligned raster cube  │
└──────────────────────────────────────────────────────────────┘
```

### Layer 1 — State Cube (`ingest/`, `state/`)

Turns messy external reality into **one analysis-ready xarray Dataset** on a fixed grid.

- `ingest/` talks to the outside world: STAC search, COG reads, plain HTTP fetches,
  OSM/vector pulls. This is the *only* place network I/O is allowed.
- `state/` owns the canonical grid definition, alignment/reprojection, the Zarr store, and
  the DuckDB catalogue of what has been ingested.
- Output contract: every variable shares one CRS, one resolution, one bounding box, one
  set of coordinates. If two layers don't align, that is a `state/` bug, not a core bug.

The cube is the **single source of truth**. A physics core never re-reads a GeoTIFF.

**The cube has a time axis, and not everything sits on it.** `Dims` in `state/cube.py`
declares which axes each variable actually varies along — `(time, y, x)` for per-window
composites, `(y, x)` for genuinely static layers, `(time,)` for values that vary between
windows but not across the tile. Broadcasting a single reanalysis number across 40,602
pixels would imply a spatial signal nobody measured; do not do it to make shapes match.

One time step is one **seasonal window** — a summer (Apr–Jun) or winter (Nov–Jan) of a
given year, defined by `SeasonWindow` in `config.py` and driven by `window_years`. The
`time` coordinate is the window's *midpoint*, which no satellite ever flew over; the
`window` and `season` coordinates carry the meaning and are what you cite.

Cores still consume one composite. `state.cube.select_window(cube, label)` returns that
2-D view, and choosing the window is the **caller's** job — that is what kept the time
dimension out of `cores/`.

### Layer 2 — Physics Core (`cores/`)

Simulators. Each core answers one question and nothing else.

A core is a **pure function**:

```python
core(cube: xr.Dataset, intervention: Intervention, model) -> CoreResult
```

The `Core` protocol in `cores/base.py` types `model` as `object`, because each core knows
its own artefact type and the protocol should not care. The thermal core narrows it to
`lgb.Booster` at its own signature.

No file reads. No network. No database. No global state. No logging side effects that
change behaviour. Given the same inputs it returns the same outputs, always. This is what
makes cores testable, cacheable, parallelisable, and swappable.

**The trained model is an argument, never trained or loaded inside the core.** Training
per call kills the interactivity claim; opening the artefact file breaks purity. Loading
it is the caller's job — `scripts/` today, the API's startup hook later.

`Intervention`, `CoreResult` and `DeltaStats` are frozen in `cores/base.py`. They are the
only interface between the physics track and the product track, so changing their shape
is a two-person decision, not a refactor.

Geometry reaches a core as a boolean `(y, x)` mask on the canonical grid. Converting
GeoJSON to that mask is the API's job, because it is the layer that knows about the grid.

The first core is **thermal**: a LightGBM emulator predicting mid-morning land surface
temperature from NDVI, NDBI, albedo, elevation, land cover, 500 m neighbourhood means of
NDVI and NDBI, **and the window's meteorology**. It is trained against observed Landsat
ST_B10 so it learns the local empirical relationship rather than us hand-rolling a surface
energy balance.

Meteorology arrived with the time dimension and immediately dominated: `air_temp_c` carries
**91.5 %** of the model's gain, because across pooled windows it works as a *window
identifier* and identifying the window explains most of a target spanning 22 °C. Two things
follow, and neither is optional reading before quoting a number:

- **It does not contaminate an intervention.** `simulate` holds meteorology fixed across
  baseline and scenario, so every meteorology split lands identically on both sides and
  cancels in the difference. The gain ranking describes what separates windows, not what
  drives the intervention.
- **It is a weak temporal feature.** Across nine summers `air_temp_c` correlates with mean
  summer LST at only r = 0.55. That is why an unseen year costs 1.9–5.3 °C of offset — the
  model has nothing else that varies with the year.

`cores/equity.py` is the second, and is a pure function rather than a `Core`: it takes a
delta field and a population field and returns who received the cooling.

The third is **air**: a steady-state Gaussian plume over the OSM emission inventory. A
uniform wind across a 20 km tile makes superposition a **convolution**, so the whole tile
is one FFT — which is what keeps it interactive. `AirParameters` occupies the `model`
argument slot the thermal core gives its booster, for the same reason (a core takes its
model as an argument), with one difference that has to be stated whenever a number is
quoted: **these are literature constants, not fitted ones.** Nothing in this core has seen
Lahore's air.

Two things about it are as load-bearing as the LST naming rule:

- **It models a local increment, never a concentration a monitor reads.** The inventory
  covers this tile's roads and nothing else, so the regional background that dominates
  Lahore's PM2.5 is absent by construction. It cancels in a difference, which is why the
  API ships a delta and never a level. Call it **locally-generated PM2.5** everywhere.
- **Winter is not a scale factor on summer.** The inversion drops the mixing height ~3x
  and the season's winds are lighter, so identical emissions produce **6-9x** the
  concentration (6.3x-8.9x measured across 2023, 2024 and 2025). The
  season is read from the cube rather than defaulted, because getting it wrong is worth
  that factor.

### Layer 3 — Intelligence (`api/`, `dsl/`)

The composition root. Loads the cube and the model **once at startup**, validates
requests, converts GeoJSON to masks, calls cores, and serialises deltas for the map.
Routes stay thin.

`dsl/` is the intervention language that sits in front of all of it. A `Plan` is what a
person says — "5,000 trees", "ban combustion vehicles" — and it deliberately carries **no
geometry**, for the same reason a core does not (D6): a plan says *what*, the polygon says
*where*, and keeping them apart is what makes a preset apply to whatever the user drew.
`dsl.validate.resolve` turns a plan into the two fractions `/simulate` takes, or refuses it.

Four rules govern this package:

- **The refusal is the product, not the error path.** "5,000 trees need 0.125 km² of crown
  at 25 m² each, but this polygon has only 0.031 km² still plantable" arrives from `/plan`
  *before* a core runs. Silently trimming an impossible plan returns a small delta, which
  is indistinguishable from a plan that merely worked badly.
- **What fits is measured, never assumed.** The headroom is the thermal core's own
  `effective_fraction` asked for a canopy of 1.0, via `api/measure.py`. A parallel rule of
  thumb would let the DSL and the physics disagree about how green a cell already is.
- **A tree count refuses where a canopy fraction warns.** Not an inconsistency — the two
  units already have different contracts. A fraction is documented as a ceiling the core
  caps per cell; a count is a quantity somebody would procure.
- **The model is reachable from `dsl/llm.py` and `agent/nodes.py`, and every call site
  carries a post-check on the model's own output** (D25, replacing D18; narrowed further
  by D29 and D30). `dsl/llm.py` holds every actual request to a provider — planner,
  narrate, describe_pattern, and `answer_result_question` for `/simulate/chat` — so a new
  caller is a new function inside it, not a new module holding its own adapter.
  `agent/nodes.py` is the one place that still calls the adapter directly, for the search
  loop's goal, proposal and report steps. `evidence/answer.py` and `policy/extract.py`
  were the other two call sites; both are gone, removed with the features that used them
  (D29, D30). The rule was never the file count, it was *the model's output is never
  trusted*: a new call site is a new decision, and a call site with no post-check is a bug.
  The post-checks in place are numeral faithfulness (narrate, describe_pattern,
  `answer_result_question`), `Plan` re-validation plus `dsl.validate.resolve` (planner,
  agent).

  **The key is required for the AI layer and optional for everything else** (D27). With no
  key, `/plan` still parses text with the deterministic rule parser and `/explain/spatial`
  still returns its region table — those are the product, not substitutes for it — while
  `/agent/search` and `/simulate/chat` answer 503. Whatever produces a plan — a
  model, a button, a regex — it is re-validated as a `Plan` and then against the tile
  before a core sees a number. That is the entire safety argument for putting a free-tier
  model in front of a simulator, and it is unaffected by whether a fallback exists.
- **A model may reword a number; it may never source one** (D24). `dsl/llm.py` also holds
  `narrate`, a LangChain chain that rewrites `explain.plain_summary`'s jargon-free block
  into friendlier prose for the dashboard. What makes that safe is not the prompt:
  `_numbers_are_faithful` is a **post-check on the model's own output**, and any numeral
  that was not in the template's version rejects the whole rewrite back to the template.
  Rounding 16.7 km² to 17 km² counts as inventing a figure, deliberately. `narrate` cannot
  raise, so a missing key, a dead provider or malformed JSON all return the template and
  the response keeps its shape.
- **Three things the narrator is not handed and cannot touch**, each earned by watching a
  real call get it wrong. **The caveat** is not sent and not read back: it went round once
  and returned as "the outcome may be less than predicted", which reads like a hedge but is
  a different claim — the template says the figure has *already* been scaled down, and the
  rewrite quietly re-applied the correction. No numeral changed, so the faithfulness guard
  had nothing to catch. **`PlainSummary.verdict`** — how big the change is, on the tile's
  own bare-to-leafy scale — is computed in `explain._impact` and excluded from the update,
  so a model cannot talk a marginal plan up. And **the headline's figures must survive**:
  `_numbers_are_faithful` is one-directional, so a model told loudly enough never to invent
  a number complies by dropping every number, which passed every check and produced "many
  trees are needed to achieve this small change". `_headline_figures_survive` is the second,
  opposite guard — invent nothing, gut nothing.
- **A follow-up question is answered from the result, not the repository** (D29).
  `POST /simulate/chat` replaces `/evidence/ask`, withdrawn the same day: asking the
  project's own markdown was a fine question about the codebase and the wrong one about a
  specific run — a councillor asking "why did it cool that much?" wants the answer for
  *this* plan, not a BM25 hit on a decision record. `dsl.llm.answer_result_question`
  builds its facts block from the `Brief` the client already has (headline, findings,
  plain-language points, uncertainties — never a raw cube read), and the same
  `_numbers_are_faithful` guard applies: an answer may explain what a figure in the brief
  means and may not introduce one the brief did not contain. `history` is the session's
  prior turns, replayed as a transcript, so a follow-up can reference an earlier answer.
  No template stands behind it — a question the guard rejects gets a 503, not a quieter
  wrong answer, the same asymmetry `translate` used to carry against `narrate`.

`dsl/explain.py` writes the brief, and writes it from templates. A generative explainer
would occasionally restate a figure it did not receive and would smooth a caveat into a
hedge; a template can do neither, and its caveats are structural, so they can be tested.
That still holds for `brief_for`, which writes the text somebody would be asked to defend.
`plain_summary` is the same numbers with the jargon removed, for the dashboard — also a
template, and the only thing a model is allowed near, under the guard described above.
Every brief carries the hindcast correction, the window, and the surface-versus-air
distinction, `uncertainties` is never empty, and `confidence` has no `high`. **A caveat
attaches to a figure, not to a plan** — a traffic-only plan carries no thermal caveats,
because a caveat about a number nobody was given is noise, and noise is how a real caveat
stops being read.

**Costs were removed entirely on 2026-08-11** (D31): `dsl/library.py`'s `estimate_cost`,
every `$` figure in the UI, the brief, and the agent's report, and the agent's
`cost_effectiveness` objective and budget constraint. Not a display change — the product
no longer computes or reasons about a dollar figure anywhere. See D31 for why.

**Citizen photos and voice capture were removed on 2026-08-07.** Both were built and
worked; both were cut because they were the only features that could not be defended
offline. The photo path was the single route in the project that *required* a key — no
rule parser can read a photograph — and voice was the single feature no automated test
could drive. Removing them made every route answer with no key at all — which held until
**D27** required one for the AI layer on 2026-08-11; the *zero-budget* half of the claim is
unaffected, since the keys are free and need no card. D19 and D20 are closed as withdrawn, not as failed; the
reasoning they recorded is still the reason not to reintroduce them casually.

**Urdu support was removed on 2026-08-11** (D28), at the product owner's request rather
than a technical failure: the rule parser's Urdu vocabulary and digit folding, `dsl/llm.py`'s
`translate`, and `?lang=ur` on `/plan` and `/simulate` are all gone, along with the
frontend's language toggle and the self-hosted Nastaliq font. This reverses the reasoning
D20 recorded for keeping Urdu after voice capture was cut — that reasoning was sound at the
time and is not wrong in retrospect, just superseded. The rule parser is English-only again.

Three rules this layer earned the hard way:

- **Validate the artefact when you load it, not when you built it.** A cube whose ingest
  died partway keeps its full time axis with the unreached windows still holding fill
  values, and shapes, coordinates and whole-cube summaries all still pass.
  `state.cube.validate_windows` checks per *variable-window*, and `api/runtime.py` refuses
  to serve a cube that fails it. The **model** gets the same treatment: a booster whose
  `feature_name()` does not match `FEATURE_NAMES` exactly — order included, since LightGBM
  matches positionally — is refused at startup rather than 500-ing on `/simulate`. Startup
  logs and keeps `/health` up while the data routes answer **503 with the reason**; it does
  not die, because a readiness probe needs an answer rather than a restart loop, and it
  does not 404, because that would claim the endpoint never existed.
- **A raster crosses the wire as base64 float32 plus bounds, never as GeoJSON features.**
  40,602 cells as features is tens of megabytes describing a grid three numbers already
  define. The encoding is named in the payload so a client never guesses.
- **The window is part of every answer.** The same planting cools ~0.51 °C in summer and
  ~0.13 °C in winter, so a response that does not name its window is unquotable. Requests
  may omit it; responses never do. The default is the latest *summer*, not whichever slice
  happens to be last.

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
| Array / tabular    | **numpy**, **pandas**, **scipy.ndimage**  | scipy only for the neighbourhood filters in `cores/thermal/features.py` |
| Agent runtime      | **LangGraph**                             | D17 reopened on its own condition, and only for the search loop: a cycle, a conditional edge and a budget. The planner still has none |
| PDF text           | **pypdf**                                 | Only to check an extracted quote against the document. Never to read meaning — the target PDF's text comes out shredded |
| Frontend           | **React** + **Vite**                      | |
| Map                | **MapLibre GL** + **deck.gl**             | MapLibre for basemap, deck.gl for the data overlays |
| Basemap tiles      | **OpenFreeMap** Positron                  | Keyless and unmetered. See the zero-budget rule below |

### Data source

Microsoft Planetary Computer, anonymous access with request signing via
`planetary_computer.sign_inplace`. Primary collections:

- `landsat-c2-l2` — surface temperature (ST_B10) and optical bands, 30 m native
- `sentinel-2-l2a` — NDVI / NDBI / albedo, 10–20 m native
- `cop-dem-glo-30` — elevation, 30 m native. A Digital *Surface* Model: it includes
  buildings and canopy, so over dense Lahore it reads above bare ground. Useful as an
  urban-form proxy, but it is not terrain height
- `esa-worldcover` — land cover classification, 10 m native

Three sources are **not** on Planetary Computer and arrive over plain HTTP, still free:

- **Open-Meteo** ERA5 archive — air temperature, wind and humidity, sampled at the ~10:30
  local overpass hour and reduced by median over the window, matching how the LST
  composite is built
- **WorldPop** 2020 constrained, 100 m — population. Downloaded once into `data/raw/` and
  cached, because the server does not support HTTP range requests and so cannot be read
  in place. It also drops connections mid-transfer, so the download verifies
  `Content-Length` before renaming into place — a short read yields a valid-looking
  GeoTIFF with rows missing
- **Overpass / OpenStreetMap** — road centrelines and brick kilns, binned into the PM2.5
  emission inventory. Keyless. Deliberately *not* via `osmnx`: that builds a routable
  graph, and an inventory does not route — it needs geometry, one tag, and length per
  cell, which is one POST and a histogram

**OpenAQ v3 is the one source in this project that needs a key.** Free, no card, so it
stays inside the zero-budget rule, but v2 was retired and v3 authenticates every request.
It is used only by `scripts/validate_air.py`, which refuses to run without
`TERRARIUM_OPENAQ_KEY` rather than half-validating.

These are *source* resolutions. Everything is resampled onto the single analysis grid —
**100 m**, `Tile.target_resolution_m` in `config.py` — which is what every physics core
assumes. Do not confuse the two: `NATIVE_RESOLUTION_M` describes the inputs,
`target_resolution_m` describes the cube. Over the Lahore bbox that grid is **201 × 202
= 40,602 pixels**.

Resampling method is a property of the variable's *meaning*, declared once in
`state/cube.py`:

- **nearest** for *labels* — land cover classes, QA bitmasks. Averaging class 10 with
  class 50 gives class 30, which is a lie.
- **bilinear** for *intensive* measurements — a temperature or an index means the same
  thing whatever the pixel's area.
- **sum** for *extensive* ones — population is a head count per cell, not a rate.
  Interpolating it invents or destroys people: on the real WorldPop raster, bilinear
  loses 26 % of the tile's residents. Phase 8 divides cooling by that number.

Meteorology declares `None`: nothing is resampled onto a dimension that does not exist.

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
│   │   ├── osm.py          #   Overpass -> PM2.5 emission inventory on the grid
│   │   └── pipeline.py     #   masking, unit conversion, cube assembly
│   ├── state/              # the cube: grid, alignment, Zarr, DuckDB catalog
│   │   ├── grid.py         #   canonical CRS / resolution / transform (spatial only)
│   │   ├── cube.py         #   variable + dims + resampling contract; validate,
│   │   │                   #   summarise, select_window
│   │   └── store.py        #   Zarr + DuckDB persistence
│   ├── cores/              # ← PURE. no I/O, no network, no globals.
│   │   ├── base.py         #   Intervention, CoreResult, DeltaStats, Core protocol
│   │   ├── air.py          #   Gaussian plume ΔPM2.5 + leave-one-station-out scoring
│   │   ├── equity.py       #   who receives the cooling: person-degrees by decile
│   │   └── thermal/
│   │       ├── features.py #   cube → feature matrix, incl. neighbourhood means
│   │       ├── model.py    #   LightGBM train / predict / spatially blocked CV
│   │       └── simulate.py #   apply intervention, return whole-tile delta
│   ├── dsl/                # the intervention language. Pure arithmetic + one adapter
│   │   ├── schema.py       #   Plan, PlantTrees, RestrictVehicles. No geometry, by design
│   │   ├── validate.py     #   plan + measured polygon -> what /simulate takes, or refusal
│   │   ├── library.py      #   the preset library. No cost anywhere (D31)
│   │   ├── planner.py      #   text -> Plan: model first, deterministic regex always
│   │   ├── explain.py      #   numbers -> brief. Templates, never a model
│   │   └── llm.py          #   adapters, narrate, describe_pattern, answer_result_question (D25)
│   ├── agent/              # the intervention search agent. Runs the cores in a loop
│   │   ├── state.py        #   Objective, Candidate, Outcome, Attempt, SearchBudget
│   │   ├── objective.py    #   pure scoring. The constraint is enforced, never traded off
│   │   ├── evaluate.py     #   the cores, once, shared by the control and the graph
│   │   ├── baseline.py     #   the greedy CONTROL. Not a fallback — the thing to beat
│   │   ├── nodes.py        #   ← the only file here that talks to a model (D25)
│   │   └── graph.py        #   LangGraph wiring, budget, SSE events. Only langgraph user
│   └── api/
│       ├── main.py         #   app factory, CORS, router wiring, loads the runtime
│       ├── runtime.py      #   cube + model loaded ONCE; per-window validation
│       ├── geometry.py     #   GeoJSON -> mask, lon/lat -> cell (D6). Only place doing this
│       ├── measure.py      #   what the tile says a polygon can hold, for dsl/validate
│       ├── candidates.py   #   the 2 km lattice the agent picks from (D26). No model
│       ├── explain_spatial.py # delta field -> per-region table. Deterministic
│       ├── deps.py         #   the runtime as a FastAPI dependency
│       ├── routes/         #   HTTP endpoints (thin), incl. chat.py for /simulate/chat (D29)
│       └── schemas/        #   Pydantic request/response contracts
├── web/                    # React + Vite + MapLibre + deck.gl
│                           #   src/panels/BriefDocument.tsx  print-to-PDF council brief
├── data/                   # gitignored: raw/ interim/ processed/
│                           #   raw/pak_ppp_2020_constrained.tif (WorldPop, cached)
│                           #   processed/cube.zarr, terrarium.duckdb, thermal.txt
├── scripts/                # one-off CLI entrypoints. I/O around the cores lives here.
│   ├── build_tile.py       #   full ingest -> State Cube, with a build report
│   ├── build_air_layers.py #   add emissions + wind direction to an existing cube
│   ├── validate_air.py     #   leave-one-station-out against OpenAQ (needs a key)
│   ├── inspect_cube.py     #   read back and summarise a built cube
│   ├── preview_cube.py     #   render PNGs for visual alignment checks
│   ├── train_thermal.py    #   train + blocked CV + one worked intervention
│   └── hindcast.py         #   change detection -> train before -> score after
└── docs/
    └── IMPLEMENTATION_PLAN.md   # phases, decisions register, measured results
```

---

## Coding conventions

**Type hints everywhere.** Every function signature — parameters and return. `mypy
--strict` is the target and is currently clean. Untyped third-party libraries get an
`ignore_missing_imports` override in `pyproject.toml`, never a bare `# type: ignore` at
the call site.

**`cores/` is pure.** No `open()`, no `requests`, no `xr.open_zarr`, no `datetime.now()`,
no reading config. Everything a core needs arrives as an argument. If you find yourself
wanting I/O inside a core, the data should have been put in the cube by `state/` instead,
or the file should have been opened by the caller. This is the single most important rule
in the codebase — it is what lets us test physics without a network and swap emulators
without touching the API. `cores/` importing `terrarium.config` is the canary.

**Pydantic models for all data contracts.** Every boundary — HTTP request, HTTP response,
core input, core output, config — is a Pydantic model. No bare dicts crossing a module
boundary. Models live in `api/schemas/` for HTTP and next to the core for core contracts.
Frozen unless there is a reason not to be.

**Tests alongside each module.** `foo.py` is tested by `test_foo.py` in the same directory.
Not a mirrored `tests/` tree — proximity keeps them honest and makes deletion obvious when
a module dies. They are excluded from the built wheel. **No test may touch the network.**

**Name the concentration precisely too.** The air core produces this tile's *own*
contribution to PM2.5 from its roads, in a single pass, at the same ~10:30 hour. It is not
what a monitor reads and it is not "air quality" unqualified — call it **locally-generated
PM2.5**, and quote deltas rather than levels. Also say the magnitudes are **uncalibrated**:
the emission factors are literature figures for a South Asian fleet, not measurements of
Lahore's.

**`validate_air.py` has run, and the core now passes for winter.** Scored against 53
OpenAQ monitors over 2025-winter it **beats the null model** — MAE **40.6** against a null
**51.0**, correlation **+0.53**. That took a change of model, not a change of wording:

- **The dispersion kernel is `seasonal_kernel`, not `plume_kernel`.** Every window in the
  cube is a *season*, and a single-direction plume answers a question about one *hour*.
  Over 2025-winter the overpass-hour wind spans 68°–331°, so the plume pointed a narrow
  streak one way while the season's air went everywhere. The plume scored corr **+0.157**
  and *lost* to the null model. Averaging the plume over a 12-bin wind rose is **worse**
  still (+0.018) — the error is in the kernel's radial profile, not only its direction.
- **`seasonal_sigma_m` is the one fitted number in this core.** 1 km, chosen where
  leave-one-station-out minimises error, on the same 53 stations the MAE is quoted from —
  so that MAE is optimistic. The optimum is broad (400 m–2 km all beat the null), which is
  the reassurance that it is not a knife edge.

**Summer is still not validated and beats the null at no sigma.** Its boundary layer is
800 m and well mixed by mid-morning, so local sources disperse before they make a pattern,
and only 15 monitors span 44–56 µg/m³. Quote winter results; say summer is unvalidated.

The delta was never in doubt — the background is identical either side of an intervention
and cancels, which is why the API ships a difference and never a level.

**Name the temperature precisely.** Landsat crosses Lahore at ~10:30 local and ST_B10
measures the *surface*, not the air. Surface temperature runs several degrees above air
temperature and peaks after the overpass. Everywhere it appears — variable descriptions,
API schemas, UI copy, narration, the pitch — call it **mid-morning land surface
temperature**. Never "temperature" unqualified, never "afternoon".

**Naming.** `snake_case` functions, `PascalCase` models, `SCREAMING_CASE` constants.
Geospatial variables carry units in the name: `temp_c`, `area_m2`, `dist_m`.

**xarray discipline.** Always name dimensions (`("y", "x")`, never positional). Always
carry CRS in `.rio.crs`. Never `.values` a lazy array until you actually need it in memory.

---

## Scope — read this before adding anything

The point is one convincing vertical slice, not four shallow ones. Scope is **phase-gated,
not permanently closed**: the plan schedules equity, an air dispersion core, a DSL, agents,
and an agent layer into later phases. That is a roadmap, not a licence to start them.

**The rule:** build the phase that is currently open, in full, and nothing from a later
one. Adding a placeholder module, an empty interface, or a "just the stub for now" ahead
of its phase is a scope violation, not foresight — the layer boundaries above already
guarantee those phases can land later without a rewrite, and that is the only concession
made to them.

Fixed for the whole project unless a decision reopens it:

- **ONE tile.** Lahore, Pakistan. Hardcoded bbox in `config.py`:
  `[74.2533, 31.4305, 74.4641, 31.6103]` — roughly 20 km × 20 km centred on
  31.5204 N, 74.3587 E. No tile selection UI, no multi-city support, no dynamic bbox.
- **No auth, no user accounts, no persistence of user scenarios, no multi-tenancy.**
  These are not in any phase.
- **Flood risk** appears in the product vision but has no scheduled core. Do not build one
  without reopening the plan.

### Zero budget — this is a design constraint, not just a wallet

**Nothing in this project may require a credit card.** It is a claim in the pitch — *any
city on Earth can run this at zero marginal cost* — so a paid dependency breaks the
argument, not just the budget. The whole data layer, every library, and the basemap tiles
are free and keyless today.

The two easy ways to break this: **basemap tiles** (MapLibre is the free library; MapTiler
and Stadia are metered services — use OpenFreeMap) and **the LLM** (route through a free
tier, behind one adapter, so the provider never leaks into the agent logic). If a phase
cannot be done free, the plan changes, not the budget.

**Zero budget, not zero configuration (D27).** The keys are still free-tier and still need
no card, so the pitch's claim is intact — but the AI layer *requires* one as of
2026-08-11, and the deterministic stand-ins that used to cover for its absence are deleted.
Each of them was a different procedure returning the same response shape: a lattice sweep
is not a search, the retrieved passages are not an answer, English is not a translation.
Silently substituting one reported a number the feature had not produced, under field names
saying it had.

So the line runs between **a deterministic answer that is the product** and **a
deterministic answer standing in for a missing one**:

- **Still answer with no key**, because their output is the source of truth a model may
  only reword: `/simulate`, `/plan` (the rule parser is Phase 11's and stays, English-only
  since D28), `/cube/*`, `/plan/presets`, and `/explain/spatial`'s region table. `narrate`
  also still falls back silently, because rewording prose the reader can already read costs
  them nothing they asked for.
- **503 with the variable named**: `/agent/search` and `/simulate/chat`. Answering those
  without a model means answering a different question.

Do not reintroduce a stand-in for the second group. If one looks tempting, the test is
whether it produces *the same kind of thing* — and a sweep or an invented answer both
fail it.

Phase 11's **council brief PDF** is the browser's own print dialog, rather than WeasyPrint
and a font stack, to reproduce a button every browser already ships.

---

## Commands

```bash
uv sync --extra dev              # install everything
uv run terrarium-api             # API on :8000, docs at /docs
                                 #   GET  /health          tile + liveness
                                 #   GET  /cube/summary    variables, windows, validity
                                 #   GET  /cube/layer/lst_c?window=2024-summer
                                 #   GET  /plan/presets    the intervention library.
                                 #                         Answers without a cube
                                 #   POST /plan            text | preset | Plan -> a checked
                                 #                         /simulate body, or 422 with the
                                 #                         arithmetic that refused it
                                 #   POST /simulate        GeoJSON polygon -> ΔLST
                                 #                         + equity deciles
                                 #                         + ΔPM2.5 when the request
                                 #                           removes emissions
                                 #                         + brief (findings + uncertainties)
                                 #   GET  /agent/candidates the 2 km lattice (D26)
                                 #   POST /agent/search     a goal -> the winning plan, as
                                 #                         SSE, one event per node, with
                                 #                         the greedy control beside it.
                                 #                         503 without a key (D27)
                                 #   GET  /agent/search/{id} read a finished search back
                                 #   POST /explain/spatial  where the cooling landed, and
                                 #                         what the cube says was there
                                 #   POST /simulate/chat    a follow-up question about a
                                 #                         result already in hand, grounded
                                 #                         in its own brief. Needs no cube;
                                 #                         needs a key (D27, D29)
                                 #   serves TERRARIUM_SERVE_ZARR_STORE, not the build path
uv run pytest                    # tests
uv run ruff check src/ scripts/  # lint
uv run mypy                      # types

# requirements.txt is generated, never hand-edited, and regenerated by nothing but this
# line. Do not delete it - Hugging Face Spaces (Phase 12, D13) installs from it - and do
# run this whenever a dependency changes, because it drifts from uv.lock silently and the
# way that gets discovered is a deployment installing the wrong versions.
uv export --no-hashes --no-dev --no-emit-project --format requirements-txt \
  -o requirements.txt

uv run python scripts/build_tile.py       # ingest -> State Cube (needs network)
                                          #   ~70 s per window; --years 2024 for a fast
                                          #   two-window smoke test
uv run python scripts/inspect_cube.py     # what is in the cube, incl. the tree-vs-built
                                          #   LST contrast per window; --per-window
uv run python scripts/preview_cube.py     # PNG renders; one --window per run
uv run python scripts/train_thermal.py    # train + blocked CV + worked intervention,
                                          #   on one --window (default: earliest summer)
uv run python scripts/hindcast.py         # find a change, train before it, score after.
                                          #   Needs >= 4 summer windows, so point --zarr
                                          #   at a multi-year build, not the 4-window one
uv run python scripts/build_air_layers.py # add pm25_emission_g_s + wind_direction_deg to
                                          #   an existing cube. Seconds, not a rebuild:
                                          #   --out a new path, then move serve_zarr_store
uv run python scripts/validate_air.py     # OpenAQ leave-one-station-out. Needs
                                          #   TERRARIUM_OPENAQ_KEY (free, no card)

cd web && npm install && npm run dev      # frontend on :5173 (the API's CORS allowlist
                                          #   is 5173 only — do not accept Vite's
                                          #   fallback port, free 5173 instead)
cd web && npm run test                    # vitest: raster decode, ramps, compare split,
                                          #   equity verdicts + panel render, scenario
                                          #   presets + brief render, printable brief
cd web && npm run build                   # tsc -b + production bundle
```

## Conventions for working in this repo

- Check `config.py` before hardcoding any constant — the bbox, CRS, resolution, and the
  seasonal window definitions live there and nowhere else.
- When adding a dependency, add it to `pyproject.toml`, never `pip install` into the venv.
  If it is imported directly rather than pulled in transitively, say so in a comment.
- If a change makes a core impure, stop and reconsider the design.
- **Prefer a number the tile actually shows over a number from the literature.** A
  hardcoded expected range silently becomes wrong when the composite, the season, or the
  city changes; a threshold derived from the cube does not. The thermal core's acceptance
  band is built this way, after a literature band gave a false failure.
- Keep a known-good Zarr. Planetary Computer drops connections, and never rebuild the
  night before a demo. Build to `--out` a new path rather than over the good one. This is
  why `serve_zarr_store` (what the API serves) is a separate setting from `zarr_store`
  (where a build writes) — the serving path only moves once a build has been checked.
- **A cube that opens is not a cube that is complete.** `data/processed/cube.zarr` has
  four time slices of which two are entirely empty, from a build that died partway. Check
  per window — `validate_windows`, or `inspect_cube.py --per-window` — before trusting one.
- **A remote fetch that succeeds is not a fetch that completed.** WorldPop's server
  truncates transfers without raising; verify the length and write through a `.partial`
  rename, so a short read can never be mistaken for a cache hit.
- Report validation honestly, including when it is unflattering, and label what a number
  does **not** prove. Spatially blocked CV shows the model generalises across space; it
  says nothing about whether it predicts the effect of a change.
