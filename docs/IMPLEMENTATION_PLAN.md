# Terrarium — Implementation Plan

Phases and decisions current to **2026-08-06**, after Phase 11. Originally written
2026-07-31 following a full repository audit and a decision pass with the team.

**Phase status is not the same as working software.** [AUDIT.md](AUDIT.md) is the snapshot
of what is currently broken, missing or invisible — including the fact that no cube on this
machine is servable today, so a repository of eleven done phases currently answers 503 on
every data route. Read it before trusting a ✅ below to mean "runs".

| | Phase | Status |
|---|---|---|
| 0 | Foundations — repo, tooling, API skeleton, frontend skeleton | ✅ **DONE** |
| 1 | State Cube v1 — six variables, one date, Lahore | ✅ **DONE** |
| 2 | Thermal core — LightGBM ΔLST emulator | ✅ **DONE** |
| 3 | State Cube v2 — time dimension + winter windows | ✅ **DONE** |
| 4 | Thermal core v2 — multi-date retrain + meteorology | ✅ **DONE** |
| 5 | API — expose cube and simulation | ✅ **DONE** |
| 6 | Frontend — map, draw, compare | ✅ **DONE** |
| 7 | Hindcast validation | ✅ **DONE** — model over-states cooling ~2.5x |
| 8 | Equity | ✅ **DONE** — panel shipped; demo plan gives the densest decile 0 % |
| 9 | Air dispersion core | ✅ **DONE** — inventory + core shipped; OpenAQ scoring built but unrun |
| 10 | DSL + agent layer | ✅ **DONE** — DSL, presets and brief ship; no LLM key, and it does not need one |
| 11 | Voice, VLM, council brief | ✅ **DONE** — voice + brief ship keyless; the photo reader is built and has never seen a photo |
| 12 | Deployment | ⬜ |

---

## Decisions register

Settled 2026-07-31. These are closed — reopen deliberately, not by drift.

| # | Question | Decision |
|---|---|---|
| D1 | Timeline | Several weeks, no hard deadline |
| D2 | Team | Two people → work splits into two tracks, see §Tracks |
| D3 | Scope authority | **The project description governs.** `CLAUDE.md` gets a full rewrite to match |
| D4 | Thermal core layout | Package: `cores/thermal/{features,model,simulate}.py` + `cores/base.py` |
| D5 | Time dimension | **After** Phase 2 proves the feature set, not before |
| D6 | Intervention geometry | Core takes a boolean `(y, x)` mask; the API converts GeoJSON → mask |
| D7 | Hindcast target | Unknown — find candidates by change detection over the archive |
| D8 | Second core | Air dispersion |
| D9 | Temperature claims | **State precisely everywhere:** mid-morning *surface* temperature |
| D10 | Seasons | Summer (Apr–Jun) **and** winter (Nov–Jan) windows per year |
| D11 | `CLAUDE.md` | Full rewrite against the project description |
| D12 | Deployment | Late phase, once the demo is solid. Never blocks physics work |
| D13 | Budget | **Zero. Free tier only, everywhere, no credit card.** See §Cost register |
| D14 | Air intervention | `Intervention` gains `emission_fraction_removed`, defaulting to 0.0. One `Intervention` describes one plan; a plan may say nothing about traffic. Only the air core reads it — giving the thermal emulator a traffic lever would invent a link it was never trained on |
| D15 | OSM ingest | **No `osmnx`.** It was budgeted for and turned out to be the wrong tool: it builds a routable graph, and an inventory does not route. One Overpass POST plus a 2-D histogram of densified way samples, in `ingest/osm.py`, using dependencies already present |
| D16 | Air validation source | OpenAQ **v3**, which unlike every other source in this project needs a key. Free and no card, so it stays inside D13, but it is the one thing that will not run out of the box — `scripts/validate_air.py` says so and stops rather than half-validating |
| D17 | Agent framework | **No LangGraph.** Budgeted for in Phase 10 and not taken, for the reason D15 dropped `osmnx`: it is a graph runtime, and the planner is two nodes — parse, then validate — with no branching, no cycles and no shared state to checkpoint. `dsl/planner.py` is one function that tries a model and falls back to a regex parser. If agents later need to *choose* between interventions and iterate, that is a real graph and this reopens |
| D18 | Where the LLM may live | **One module, `dsl/llm.py`, and nowhere else.** `ingest/` remains the only layer that fetches *data for the cube*; a planner call is a different thing — Layer 3, made on the user's behalf, with nothing downstream treating its output as a measurement. It is confined to one adapter for the same reason, and everything it returns is re-validated as a `Plan` before a core can see it. The key is optional: with none, `/plan` uses the rule parser and the rest of the layer never calls out at all |
| D19 | Citizen observations vs the cube | **They get the grid, not the cube.** Phase 11's brief said "writing observations back into the cube"; what shipped keeps them in their own in-memory store, their own endpoint and their own layer, with `measured: false` on every response. Every cube variable is an instrument reading with a known error; a language model's reading of a phone photo is not, and merging them would make "what does the cube say" unanswerable. They render on the same 201×202 grid so the two *can* be compared — which is the whole value — and unpersisted, because storing user-submitted content means owning moderation and retention, which is out of scope in the same way user accounts are |
| D20 | Voice capture | **Browser Web Speech API**, not LiveKit (meters minutes) and not self-hosted Whisper (needs somewhere to run). Free, keyless, no dependency. Uneven support is handled by detecting the constructor and rendering no microphone where there is none. Consequence that was not obvious: capturing Urdu is worthless unless the *parser* reads Urdu, because with no LLM key the rule parser is what receives the transcript — so Phase 11 put Urdu into `dsl/planner.py`, digits included |

### Assumptions I am defaulting (overturn any of these freely)

- Population source: **WorldPop 100 m**; building height from GHSL if Phase 8 needs it.
- Deprivation proxy: nightlights + building density. **No** informal-settlement classifier.
- Model artefact: LightGBM native text format at `data/processed/thermal.txt`.

---

## Cost register (D13)

**The project must run at zero marginal cost.** This is not only a budget constraint — it
is a *claim in the pitch*: "any city on Earth can run this at zero marginal cost." A paid
dependency anywhere breaks the argument, not just the wallet.

### Already free, no account, no key — the entire data layer

| Source | Status |
|---|---|
| Microsoft Planetary Computer (Sentinel-2, Landsat, DEM, WorldCover, Sentinel-1) | Free, anonymous, no key. **Already in use** |
| Open-Meteo (meteorology, Phase 3) | Free, keyless, non-commercial |
| OpenStreetMap / Overpass (Phase 9) | Free, keyless |
| OpenAQ v3 (air validation, Phase 9) | Free, **but needs a key** — no card. The one keyed source (D16) |
| WorldPop, GHSL, VIIRS nightlights (Phase 8) | Free, open licence |

Every library in the stack — FastAPI, xarray, Zarr, DuckDB, LightGBM, React, deck.gl,
MapLibre — is open source and self-hosted. **Phases 0–9 already cost nothing.** Only
Phases 10–12 introduce anything billable, and each has a free path below.

### Three things that would have cost money — corrected

**1. Basemap tiles (Phase 6). The easiest one to miss.**
MapLibre GL is the free *library*; the **tiles are a separate service**. Most tutorials
point at MapTiler or Stadia, which need an API key and meter usage. Use
**[OpenFreeMap](https://openfreemap.org/)** — no key, no registration, no request limit,
attribution auto-added by MapLibre:

```js
new maplibregl.Map({ style: "https://tiles.openfreemap.org/styles/positron" })
```

Positron is a pale basemap, which is what you want under a heat overlay anyway.

**2. LLM for the agent layer (Phase 10). Previously assumed Anthropic API — that is paid.**
Route through a free tier instead. As of July 2026:

| Provider | Free tier | Card? |
|---|---|---|
| **Google Gemini (AI Studio)** — recommended | ~1,500 req/day, 15 RPM, 1M context, multimodal | No |
| Groq | ~1,000 req/day, 30 RPM, very fast | No |
| OpenRouter | 20 RPM, 50–1,000 req/day across 28+ free models | No |

Gemini's multimodal free tier also covers the **Phase 11 VLM** for citizen photos, so one
key serves both. **Design for provider-independence:** LangGraph with Pydantic structured
output means the model is one adapter behind an interface. If the hackathon hands out
credits, swap the adapter — do not let a provider leak into the agent logic.

**3. Deployment (Phase 12). Fly.io and Modal are not reliably free.**

| Layer | Free option |
|---|---|
| Frontend | **Vercel** or **Cloudflare Pages** hobby tier — genuinely free |
| API | **Hugging Face Spaces** CPU Basic — 2 vCPU, 16 GB RAM, no card. Best fit: it tolerates an ML model in the image |
| API (alt) | **Render** free — 750 h/month, but **spins down after 15 min idle** and takes ~1 min to wake. Fine for a link, bad for a live demo |

The cube is small enough that hosting is a non-issue: 40,602 px × 6 vars × float32 ≈
**1 MB**. Even 20 time slices stays around 20 MB — it ships inside the container.

### Standing rule

**Anything requiring a credit card is out of scope until someone says otherwise.** If a
phase cannot be done free, the plan changes — not the budget. Free tiers move often;
re-check the three tables above at the time of use rather than trusting this snapshot.

---

## Phase 0 — Foundations ✅ DONE

- Python 3.12 pinned, `uv` for env and lockfile, one-command setup (`uv sync --extra dev`).
- FastAPI app factory with CORS, settings via pydantic-settings, `/health` returning the
  active tile so the frontend never duplicates the bbox.
- React 19 + Vite frontend that calls `/health` and renders connection state.
- `ruff` + `mypy --strict` + `pytest` all wired and green.

## Phase 1 — State Cube v1 ✅ DONE

One 20 km × 20 km Lahore tile, EPSG:32643, 100 m, **201 × 202 = 40,602 pixels**, six
variables, persisted to Zarr with a DuckDB provenance catalogue.

**Delivered:**

- `ingest/` — the only network boundary. STAC search against Planetary Computer with SAS
  signing, cloud filtering that preserves pre-filter counts, scene capping, GDAL tuning
  for remote COG reads, per-source retry with exponential backoff and failure isolation.
- `state/` — canonical grid (snapped outward, deterministic), variable contract with
  per-variable resampling policy and physical `valid_range`, Zarr + DuckDB persistence.
- `scripts/` — `build_tile.py`, `inspect_cube.py`, `preview_cube.py`.
- 50 offline tests. No test touches the network.

**Verified, not assumed** — build `e6e3c768f392`, 116 s, **6/6 variables at 100 % valid**:

| variable | min | mean | max |
|---|---|---|---|
| `lst_c` (°C) | 29.31 | 46.57 | 53.53 |
| `ndvi` | −0.458 | 0.220 | 0.779 |
| `ndbi` | −0.543 | 0.027 | 0.261 |
| `albedo` | 0.024 | 0.187 | 0.401 |
| `elevation_m` | 198.1 | 215.9 | 239.9 |

Land cover: 73.9 % built-up, 9.8 % cropland, 6.8 % tree cover, 6.7 % grassland, 0.5 %
water. Elevation centres on ~216 m — Lahore's actual height.

**Visual verification passed** (`preview_cube.py`): the airport runway, the Lahore Canal
as a cool green strip, and the River Ravi all appear in their true positions — and the
Ravi is agreed on by **three independent measurements** (land cover, NDVI, LST). That
cross-layer agreement is the alignment proof `validate_cube` cannot give. Water centroid
lands at 31.583 N, 74.278 E, north-west of tile centre, as it should. **Keep these PNGs
for the submission.**

**Seven defects found and fixed:**

| Where | Defect |
|---|---|
| `ingest/pipeline.py` | Reflectance screened on raw DN instead of post-offset value — NDVI reached −2.36 |
| `state/cube.py` | GLO-30 mislabelled as ground elevation; it is a *surface* model including buildings |
| `api/main.py` | `create_app(settings=…)` reached CORS and nothing else — every route silently used the cached global |
| `ingest/client.py` | `open_catalog` was the only network call outside the retry logic; one DNS blip killed the build with a bare traceback |
| `ingest/pipeline.py` | Albedo missing Liang's `/1.016` normalisation — every pixel biased ~1.6 % high |
| pipeline vs cube | Same quantity credited to two different papers |
| `scripts/inspect_cube.py` | Nullable `duration_s` formatted with `:.1f` — crashed on a build without one |

Plus housekeeping: stock Vite README replaced, TypeScript `strict` enabled, stale
`package-lock.json` corrected.

---

## Phase 2 — Thermal core ✅ DONE

**Goal:** `core(cube, intervention, model) -> CoreResult`. Pure, offline, sub-second.

### Results — measured, not assumed

**Spatially blocked CV**, 2 km blocks, 5 folds, 40,602 usable rows (100 % of the tile):

| | MAE (°C) |
|---|---|
| model | **0.606 ± 0.054** |
| naive (predict the tile mean) | 1.354 |
| skill | **55.2 %** of the naive error removed |

Placeholder validation, as planned — it measures spatial generalisation, not response to
change. Phase 7 is what tests the latter.

**Feature importance (gain).** The neighbourhood term dominates exactly as predicted:

`ndbi_mean_500m` 60.2 % · `ndbi` 15.0 % · `elevation_m` 7.4 % · `albedo` 6.1 % ·
`ndvi_mean_500m` 5.6 % · `landcover` 2.9 % · `ndvi` 2.8 %

**No double-counting.** `landcover` carries only 2.9 % of gain, and the intervention
deliberately does not touch it — adding 30 % canopy leaves a built-up block built-up.
Flipping the class as well would have counted the same cooling through two channels.

**Worked intervention** — +30 % canopy on the 261 built-up cells within 1 km of the
Lahore Canal at Canal Bank Road (31.5163 N, 74.3403 E):

| | °C |
|---|---|
| mean ΔLST inside | **−0.498** |
| mean ΔLST in the 200 m ring outside (spillover) | **−0.119** (161 cells) |
| strongest cooling | −1.133 |
| largest warming | +0.269 |

**Spillover decays cleanly and then stops dead**, which is the strongest correctness
evidence in this phase:

| ring beyond the polygon | mean ΔLST | cells cooling |
|---|---|---|
| +100 m | −0.159 °C | 87 % |
| +200 m | −0.058 °C | 76 % |
| +300 m | −0.017 °C | 45 % |
| beyond 600 m | **exactly 0.000** | 0 % |

That final row is the proof that the delta is prediction-minus-prediction and not
prediction-minus-observed: outside the feature neighbourhood the two feature rows are
identical, so the two predictions are bit-identical and the delta is exactly zero on
39,891 cells. Had we differenced against observed LST, the whole tile would show
residual noise and the map would be unreadable.

Only 39 cells anywhere warm, all by less than +0.27 °C — boundary effects where a
neighbourhood mean shifts without the cell itself being planted.

### The magnitude finding — read this before quoting a number

**The plan's expected −1 to −4 °C band was wrong for this cube, and the model is right.**

This tile's *observed* median LST contrast between tree-cover and built-up pixels is only
**2.60 °C** (44.47 vs 47.07). That is the ceiling: converting a cell entirely to tree
cover cannot buy more than 2.6 °C, because that is all the difference the data contains.
At a mean 26.9 % canopy actually added after capping, the linear expectation is −0.70 °C
and the model returns −0.50 °C. Consistent, and slightly conservative.

The contrast is modest because the LST layer is a **median composite over Apr–Jun clear
scenes** — compositing averages away the extremes — and because at 100 m Lahore's "tree
cover" class is scattered street trees rather than closed canopy. Literature values of
3–6 °C come from single-date imagery over closed canopy, which is not what is in the cube.

Consequence: `scripts/train_thermal.py` now derives its acceptance band **from the tile's
own observed contrast** rather than a hardcoded range. A literature constant silently
becomes wrong the moment the composite, the season, or the city changes. Phase 3's
per-window composites should raise the contrast — that is a thing to check, not assume.

**Delivered:** `cores/base.py` (`Intervention`, `DeltaStats`, `CoreResult`, `Core`),
`cores/thermal/{features,model,simulate}.py`, `cores/thermal/test_simulate.py` (10 tests,
in-memory synthetic cube, no I/O), `scripts/train_thermal.py`. Artefact at
`data/processed/thermal.txt`. Whole suite 60/60, `ruff` and `mypy --strict` clean.

### 2a. `cores/base.py` — Core protocol

```python
class Intervention(BaseModel):      # frozen
    mask: np.ndarray                # (y, x) bool — see D6
    canopy_fraction_added: float    # 0..1 — see D6/2d
class CoreResult(BaseModel):        # frozen — delta field + scalar summary
class Core(Protocol):
    def __call__(self, cube, intervention, model) -> CoreResult: ...
```

The trained model is an **argument**, never trained or loaded inside the core. Training
per call kills the interactivity claim; loading a file breaks the purity rule.

### 2b. `cores/thermal/features.py`

`build_features(cube) -> (DataFrame, np.ndarray)`, one row per pixel.

| Feature | Why |
|---|---|
| `ndvi`, `ndbi`, `albedo`, `elevation_m` | continuous predictors |
| `landcover` as LightGBM categorical | class label, never averaged |
| `ndvi_mean_500m`, `ndbi_mean_500m` (5×5 uniform filter) | LST is driven by the *neighbourhood*, not the pixel. Highest-value engineered feature, and it is what produces intervention spillover |

**No meteorology in this phase.** One composite means wind and air temperature are
constant across all 40,602 pixels and carry exactly zero information. They arrive in
Phase 4.

### 2c. `cores/thermal/model.py`

- **5-fold spatially blocked CV** over a ~2 km checkerboard. Random pixel CV leaks across
  the split — neighbouring 100 m pixels are strongly autocorrelated — and reports a
  flattering MAE that means nothing.
- **Fold, do not hold out one block.** The tile is 74 % built-up but also holds the Ravi,
  the canal, cropland and the airport; a single block can land mostly on water. Report
  **mean MAE ± spread**; the spread is the honest uncertainty band.
- Report a **naive baseline** (predict the tile mean) alongside. An MAE only means
  something next to the number it must beat.
- **Label this validation a placeholder, on screen.** It answers *"can the model predict
  LST somewhere unseen?"* — spatial generalisation. It does **not** answer *"can it
  predict what happens after a change?"* Only Phase 7 answers that.

### 2d. `cores/thermal/simulate.py`

1. Take **added canopy fraction `f`** plus a boolean mask (D6). Cap `f` at the remaining
   plantable fraction. Tree-count → fraction conversion lives in the DSL (Phase 10) —
   crown area and survival rate are cost-library facts, not physics.
2. Compute the **observed median feature vector of `landcover == 10` pixels in this same
   tile**.
3. Move each masked cell's features fraction `f` toward that vector.
4. **Recompute neighbourhood features from the perturbed field.** Easy to forget, and it
   is what produces the spillover.
5. `delta = predict(perturbed) - predict(baseline)` **over the whole tile**.

Two rules that are easy to get wrong:

- **Difference two predictions, never prediction-minus-observed.** Otherwise the model's
  residual leaks into every scenario and untouched pixels show spurious ΔLST.
- **Return the whole-tile field, not just the masked region.** Neighbourhood features
  mean cooling genuinely extends past the polygon — real physics, and one of the more
  convincing things on screen.

**Watch for double-counting** between `landcover` and `ndvi`: if the model learned
"class 10 is cool" largely *because* class-10 pixels have high NDVI, flipping both counts
the same cooling twice. Check feature importances after the first train.

### 2e. `scripts/train_thermal.py`

The only place allowed to do I/O around the core. Loads the cube, trains, writes the
artefact, runs the CV, and runs one worked intervention: **+30 % canopy fraction in the
built-up core near the canal**.

**Stop here and report the numbers before building anything on top.** Sanity checks:

- **ΔLST must be negative** in the planted region. Positive means a sign error, not a
  finding.
- **Magnitude must not exceed the tile's own tree-vs-built LST contrast.** More than a
  full conversion to tree cover means the model is extrapolating: the built-up core has
  few high-canopy analogues, and gradient boosting fails silently there. *(Revised — the
  original "−1 to −4 °C" assumed a contrast this composite does not have. See Results.)*

**Acceptance:** `cores/thermal/test_simulate.py` builds a synthetic cube in memory,
plants trees in a hot patch, asserts ΔLST negative there and ~zero far away.

### 2f. Temperature labelling (D9) — do this in the same pass

Landsat crosses Lahore at **~10:30 local**, and ST_B10 is **surface**, not air,
temperature. Surface temperature runs several degrees above air temperature and peaks
after the overpass. Relabel everywhere — `lst_c`'s description, API schemas, UI copy,
narration — as **mid-morning land surface temperature**. Never "afternoon", never
"temperature" unqualified.

---

## Phase 3 — State Cube v2: time + seasons ✅ DONE

The biggest hidden dependency in the plan: Phases 4, 7, 8 and 9 all need it.

**Delivered:** `search_start/end` replaced by `window_years` → `SeasonWindow`; a `time`
axis on the cube with `window` and `season` label coordinates; per-window composites;
meteorology from Open-Meteo and population from WorldPop. 93 tests, still none touching
the network. `ruff` and `mypy --strict` clean.

### The variable table now has three shapes, not one

Adding `time` to everything would have been the wrong move. `Dims` in `state/cube.py`
declares which axes each variable actually varies along:

| dims | variables | why |
|---|---|---|
| `(time, y, x)` | `lst_c`, `ndvi`, `ndbi`, `albedo` | one composite per window |
| `(y, x)` | `elevation_m`, `landcover`, `population` | static — four identical copies would imply a variation nobody measured |
| `(time,)` | `air_temp_c`, `wind_speed_ms`, `relative_humidity_pct` | one reanalysis point cannot resolve anything inside a 20 km tile |

That last row is the one worth defending: painting a single Open-Meteo value across
40,602 pixels would look like a meteorology *field* and is not one. Phase 4 broadcasts it
as a feature; the cube stores what was measured.

**`cores/` was not touched.** `select_window(cube, label)` returns the 2-D cube the
thermal core already consumes, and the caller picks the slice. Purity intact.

### Verified, not assumed — build `3723544fdc64`, 259 s, 10/10 variables populated

The default `window_years = [2023, 2024]`, four windows, ~65 s per window. Coverage is
100 % everywhere **except 2023-winter `lst_c`, which is 99.978 %** — 9 pixels no clear
Landsat scene ever saw. This was first reported as a flat "100 % valid" because the
report rounded to one decimal; see next action 6.

| window | air temp | wind | RH | Landsat scenes |
|---|---|---|---|---|
| 2023-summer | 31.5 °C | 2.14 m/s | 43 % | 12 |
| 2023-winter | 14.9 °C | 1.06 m/s | 82 % | **3** |
| 2024-summer | 34.0 °C | 2.25 m/s | 37 % | 12 |
| 2024-winter | 14.1 °C | 0.79 m/s | 71 % | 5 |

Meteorology varies between *years* as well as seasons — 2023 and 2024 summers differ by
2.5 °C — which is what makes it a usable feature in Phase 4 rather than a season label in
disguise.

Meteorology is sampled at the **10:00 local overpass hour** and reduced by median over
the window's days — deliberately the same reduction the LST composite uses, so the two
are not describing different days or different times of day. The winter row is the air
core's whole premise arriving early: 0.79 m/s and 71 % RH is the stagnation signature.

**Population: 6,259,308 residents**, 91.5 % of cells inhabited, max 264 per hectare. The
render is a fourth independent confirmation of the tile's geography — the airport and the
River Ravi both appear as uninhabited holes in exactly the right places, agreeing with
land cover, NDVI and LST.

### Population resamples by SUM, and that is not a detail

Population is **extensive** — a head count per cell, not a rate — so it is the first
variable that is neither nearest nor bilinear. Measured on the real WorldPop raster
against the exact-bbox source total of 6,203,454:

| method | tile total | error |
|---|---|---|
| **sum** | 6,259,308 | **+0.9 %** (the grid is snapped outward, so it covers slightly more) |
| bilinear | 4,575,662 | −26 % — **1.6 million people deleted** |
| average | 4,760,930 | −23 % |

Phase 8 divides cooling by this number. Had it been resampled like every other continuous
variable, the equity panel would have been wrong by a quarter and nothing would have said
so.

### The contrast question — answered, and the answer is no

The plan asked whether per-window composites raise the tree-vs-built LST contrast above
Phase 2's 2.60 °C. **They do not**, and two independent years now say so:

| window | tree p50 | built p50 | contrast |
|---|---|---|---|
| 2023-summer | 40.41 °C | 43.20 °C | **2.78 °C** |
| 2023-winter | 23.47 °C | 24.26 °C | 0.80 °C |
| 2024-summer | 44.35 °C | 46.96 °C | **2.60 °C** |
| 2024-winter | 22.03 °C | 22.34 °C | 0.31 °C |

Summer lands at 2.6–2.8 °C in both years, on 12 Landsat scenes each rather than Phase 2's
8. So the modest contrast is not a compositing artefact that a tighter window recovers —
it is what Lahore at 100 m actually shows, where "tree cover" means scattered street trees
rather than closed canopy. **Quote 2.6–2.8 °C in the pitch, not the literature's 3–6 °C**,
and say why. Two years agreeing is a much stronger claim than one, and it is free.

Winter is a finding in its own right: 0.31–0.80 °C, i.e. tree planting buys essentially
nothing thermally in winter. Winter's value in this project is the air core, not the
thermal one, and the equity story must not claim year-round cooling. Treat the winter
numbers as the softer of the two — 2023-winter rests on **3** clear Landsat scenes, which
is also the likeliest reason it reads higher than 2024's.

Retraining the thermal core on `2024-summer` reproduces Phase 2 within noise — mean ΔLST
inside −0.500 °C (was −0.498), blocked-CV MAE 0.658 ± 0.050 (was 0.606 ± 0.054), skill
51.7 % (was 55.2 %). The cube changed shape; the physics did not.

### Two things that bit, both now guarded

- **`window` is a reserved word in DuckDB.** The catalogue column is `window_label`.
  Schema changes ship as `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, so a Phase 1/2
  catalogue migrates instead of needing deletion, and every `INSERT` names its columns.
- **WorldPop's server drops connections mid-transfer and does not support range
  requests.** A short read raises nothing — you get a valid-looking GeoTIFF missing its
  bottom rows. The download verifies `Content-Length` and only renames into place when
  complete, so a truncated fetch can never become a cache hit.

`max_scenes_per_collection` raised 8 → 14 now that windows are ~3 months rather than one
range standing in for the whole cube.

## Phase 4 — Thermal core v2 ✅ DONE

One model now spans every window, with meteorology as a real feature carrying the
between-window difference. `cores/` stayed pure and still consumes a *single* 2-D cube —
stacking happens in `features.build_training_frame`, and `simulate` takes one window.

### What shipped

- **`features.py`** — `METEOROLOGY_VARIABLES` join `FEATURE_NAMES`, broadcast constant
  across the tile from the window's scalars. `features_from_arrays` now *requires*
  meteorology rather than defaulting it: a default would let a caller train or simulate
  against the wrong weather, and because the columns are constant that mistake produces
  no NaN, no shape error, and no visible symptom anywhere downstream.
- **`TrainingFrame`** — every window stacked, carrying `window_index` *and* `cell_index`.
- **`model.py`** — `pooled_spatial_folds`, `leave_one_window_out_cv`, and
  `blocked_cv(baseline_groups=…)` so the naive comparison knows what season it is.
- **`train_thermal.py`** — `--windows` (default: all), both CV reports, per-season ΔLST.
- **24 tests** in `cores/thermal/`, fully in memory. Whole suite 107.

### The leakage trap, and why `cell_index` exists

Pooling windows and assigning CV folds *per row* is the obvious implementation and it is
wrong. Grid cell (100, 100) in 2023-summer would train while the same cell in
2024-summer is scored — and since land cover, elevation and the neighbourhood terms
barely change between windows, that is very nearly the identical row on both sides of the
split. The model looks the answer up and the MAE flatters it.

`pooled_spatial_folds` keys the fold off the **cell**, so a block is held out of *every*
window at once. `test_pooled_folds_hold_a_cell_out_of_every_window` pins it.

### Two CV numbers now, not one

| split | question it answers |
|---|---|
| pooled spatial blocks | can it predict LST somewhere unseen, in a season it knows? |
| leave-one-window-out | can it reach a **date** it has never seen? |

The second is the harder one and is where meteorology stops helping: a held-out window
carries values the model never saw, so it can only fall back on the nearest window it
did. **The gap between the two is the honest measure of how much the model leans on
cross-sectional contrast alone** — which is exactly what this phase was asked to find
out. Neither is the hindcast: every window here is a different *time*, not a different
*land surface*. Nothing has actually changed on the ground.

### Measured, on 2023-summer + 2023-winter

The 2024 windows failed to ingest (network, see below), so these numbers rest on two
windows, not four.

| | summer-only (Phase 2-comparable) | pooled summer + winter |
|---|---|---|
| spatial-block MAE | 0.634 ± 0.021 °C | **0.539 ± 0.024 °C** |
| naive baseline | 1.353 °C | 9.210 °C |
| reported "skill" | 53.1 % | 94.2 % |
| `air_temp_c` gain | 0.0 % | **92.1 %** |
| ΔLST, canal +30 % canopy | −0.482 °C | −0.507 °C |

**Do not quote the 94.2 %.** It is not an improvement on Phase 2's 51.7 % — the *baseline
moved*. Pooling two seasons that differ by ~17 °C makes the naive "predict the mean"
error jump from 1.35 °C to 9.21 °C, so most of that 94 % is the model knowing summer from
winter, which is not a skill anyone needs. The honest comparison is the MAE: **0.634 →
0.539 °C**, a real but modest gain from having twice the data. Skill percentages are only
comparable between models scored against the same baseline.

The same effect explains `air_temp_c` taking 92 % of the gain: pooled, the between-season
offset dwarfs everything the land surface does, so the tree splits on it first. That is
correct behaviour and it is also why the summer-only model remains the one to quote for
land-surface feature importance.

**The reassuring result:** ΔLST for the worked intervention barely moved, −0.482 →
−0.507 °C. The plan's worry — that pooling seasons would wash out the canopy effect —
does not materialise. Summer cooling survives pooling intact.

**Leave-one-window-out is degenerate at two windows: MAE 17.7 °C, skill 3.8 %.** Holding
out summer leaves only winter to train on, so the model has never seen a hot day and
cannot extrapolate to one. Treat this as a *floor*, not an estimate — it needs the 2024
windows before it says anything about temporal generalisation. It does already establish
the qualitative point the phase was asked for: with meteorology as the only temporal
feature, the model interpolates between seasons it has seen and cannot reach past them.

### Verified, not assumed — build `dc1af462b9c1`, 4 windows, 162,399 usable rows

Trained on all four windows of `data/processed/cube_phase4.zarr` (10/10 variables at
100 % valid, 484 s). 107 tests green, `ruff` and `mypy --strict` clean.

| split | MAE (°C) | naive (°C) | skill |
|---|---|---|---|
| pooled spatial blocks (2 km, 5 folds) | **0.550 ± 0.033** | 1.011 | **45.6 %** |
| leave-one-window-out | **2.900 ± 0.998** | 14.104 | 79.4 % |

Against Phase 2 (0.606, single window) and Phase 3's retrain (0.658, `2024-summer`), the
pooled MAE of **0.550 °C is a modest improvement** — four windows of training data help,
but not dramatically, because the extra windows are extra *dates*, not extra *places*.

**The leave-one-window-out spread is the finding.** Per held-out window:

| held out | MAE (°C) |
|---|---|
| 2023-summer | 3.857 |
| 2024-summer | 3.931 |
| 2023-winter | 2.032 |
| 2024-winter | 1.780 |

Reaching an unseen date costs **5.3× the error** of reaching an unseen place in a known
season, and summer costs roughly twice what winter does. With two seasons observed, a
held-out summer must be reconstructed largely from winter, and the model has no way to
know how far above the winter it should sit. **This is the number to quote when asked
whether Terrarium forecasts** — it does not, and 2.9 °C is the honest size of that
limitation. Widening `window_years` is the only thing that shrinks it.

### The naive baseline had to be fixed before the skill number meant anything

The first run reported **94.8 % skill**, which is not a result — it is an artefact.
Pooled across windows, "predict the mean" predicts one number for a set spanning 44 °C
summers and 22 °C winters, so its MAE is 10.6 °C and almost all of that is the seasonal
offset. Beating it demonstrates only that the model can tell summer from winter, which is
trivially true the moment air temperature is a feature.

`blocked_cv` now takes `baseline_groups`; the spatial split passes `window_index`, so the
baseline predicts *that window's own mean*. The baseline drops 10.579 → 1.011 °C and the
skill drops 94.8 % → **45.6 %**, which is the number comparable to Phase 2's 55.2 %.

Skill fell against Phase 2 while MAE improved, and both are real: the per-window baseline
averages in the winter windows, whose spatial spread is much smaller, so there is simply
less error available to remove. **Quote the MAE across phases, not the skill percentage** —
the denominator changed. Leave-one-window-out keeps the pooled baseline, because a
held-out window's own mean is precisely what is unknown.

### Meteorology took over the model, and ΔLST did not move

| feature | gain |
|---|---|
| `air_temp_c` | **91.5 %** |
| `ndbi_mean_500m` | 1.9 % |
| `wind_speed_ms` | 1.4 % |
| `albedo` · `elevation_m` | 1.2 % each |
| `ndbi` | 1.0 % |
| `ndvi_mean_500m` | 0.8 % |
| `landcover` | 0.6 % |
| `ndvi` | 0.4 % |
| `relative_humidity_pct` | 0.0 % |

Air temperature carries 91.5 % of gain and the land-surface terms together carry ~7 %,
where in Phase 2 `ndbi_mean_500m` alone carried 60.2 %. This is exactly the risk
`features.py` flags: with four windows the meteorology columns take four distinct values,
so a tree can use them as a **window identifier**, and identifying the window explains
most of the variance in a pooled target spanning 22 °C.

**It does not contaminate the intervention.** `simulate` holds meteorology fixed across
baseline and scenario, so every meteorology split lands identically on both sides and
cancels in the difference. Planting trees does not change the synoptic conditions the
window was composited under, and pretending otherwise would attribute a seasonal offset
to the intervention. The proof is the ΔLST table below: −0.506 °C in 2023-summer against
Phase 2's −0.498 °C on one window with none of these features. **The gain ranking now
describes what separates the windows, not what drives the intervention** — do not read it
as "canopy barely matters".

### Per-season ΔLST, as the plan required

The worked intervention runs in every trained window and prints ΔLST, spillover, that
window's own tree-vs-built contrast, and the linear expectation. Same scenario throughout:
+30 % canopy on built-up cells within 1 km of 31.5163 N, 74.3403 E.

| window | cells | ΔLST inside | spillover | contrast | linear | ratio |
|---|---|---|---|---|---|---|
| 2023-summer | 258 | **−0.506** | −0.144 | +2.78 | −0.742 | 0.68 |
| 2024-summer | 261 | **−0.510** | −0.126 | +2.60 | −0.704 | 0.72 |
| 2023-winter | 262 | −0.233 | −0.047 | +0.80 | −0.223 | 1.05 |
| 2024-winter | 251 | −0.131 | −0.037 | +0.31 | −0.081 | 1.63 |

Every window cools, in proportion to its **own** observed contrast, and no window trips a
sanity check. The two summers agree to 0.004 °C across independent years — the strongest
reproducibility evidence in the project so far — and both sit slightly *under* the linear
expectation, as in Phase 2.

Winter cooling is **a quarter to a half of summer's**, tracking the seasonal contrast
rather than anything the model invented. The pitch says *this intervention buys summer
cooling*; a single pooled number would have averaged a real effect with a near-absent one
and described neither.

The plausibility ratio is **enforced in summer only**: winter's contrast is 0.31–0.80 °C,
so the ratio there divides a small number by a smaller one — 2024-winter's 1.63 is that
noise, not a model defect, and it is why the check does not fire on it.

### One thing that bit, now guarded

**GDAL had no read timeout.** A rebuild attempt sat inside a *single* Sentinel-2 B11 read
for **16.4 hours** before returning. The per-source retry with exponential backoff was
already in place and was completely useless, because the attempt it guards never
returned. `configure_gdal_for_cog_reads` now sets `GDAL_HTTP_TIMEOUT`,
`GDAL_HTTP_CONNECTTIMEOUT` and the low-speed abort. A retry policy is worthless without a
bound on the thing being retried.

## Phase 5 — API ✅ DONE

Three endpoints over the loaded cube and model. `cores/` stayed pure and untouched: the
API opens the artefacts, converts GeoJSON to a mask, and calls `simulate` exactly once.

### What shipped

| route | does |
|---|---|
| `GET /health` | unchanged — liveness plus the active tile the map centres on |
| `GET /cube/summary` | `state.cube.summarise`, plus the default window and per-window validity |
| `GET /cube/layer/{name}` | one variable as base64 float32 + bounds, `?window=` |
| `POST /simulate` | GeoJSON → mask (D6) → core → whole-tile ΔLST layer + stats + context |

- **`api/runtime.py`** — `Runtime`, loaded once by the app factory and shared frozen.
- **`api/geometry.py`** — the only place that speaks both WGS84 GeoJSON and the grid.
- **`api/deps.py`** — the runtime as a dependency off `app.state`, so tests inject rather
  than monkeypatch.
- **`state/cube.py`** — `window_valid_fractions` and `validate_windows`, the guard below.
- **`cores/thermal/simulate.py`** — `tree_built_contrast`, now shared with the training
  script instead of being reimplemented in it.
- **58 API tests**, all in memory: no Zarr, no artefact, no network. Whole suite **161**.

### Verified against the real cube and model

Loading `cube_phase4.zarr` and `thermal.txt`:

| | measured |
|---|---|
| startup (cube open + model parse + validation) | **3.1 s**, once |
| `GET /cube/layer/lst_c` | **0.02 s**, 217 kB |
| `POST /simulate` | **0.37 s** warm |

Comfortably inside the < 3 s warm target — the budget was never the physics, it was going
to be the artefact loading, which is why it happens once at startup.

A 2 km box over Canal Bank Road, +30 % canopy:

| window | ΔLST inside | spillover | contrast | ratio |
|---|---|---|---|---|
| 2024-summer | −0.543 °C | −0.114 | +2.60 | 0.78 |
| 2024-winter | −0.146 °C | −0.039 | +0.31 | *null* |

Consistent with Phase 4's disc-shaped scenario (−0.510 / −0.131) — the polygon differs,
the physics does not. The winter ratio is deliberately `null` rather than a number:
below a 1 °C contrast it divides a small number by a smaller one.

### The half-built-cube guard, which is the point of this phase

Phase 4 found `cube.zarr` had two entirely empty windows and **nothing in the stack
noticed** — `validate_cube` checks shapes, `summarise` reduces over all windows at once,
and `select_window` returns a slice of NaN without complaint. An API that loads a cube
once at startup and serves it to a map is exactly where that becomes a demo failure.

`validate_windows` fails per *variable-window* rather than over the whole array, and the
API runs it at a 50 % threshold — a window that is 3 % valid will not render and will not
simulate sensibly, so accepting it only defers the failure somewhere less legible. Pointed
at the bad cube, startup now says:

```
cube at data/processed/cube.zarr is not servable: cube has unpopulated
variable-windows, so it is a partial build: ndvi@2024-summer (0.0% valid), ...
```

**It logs and degrades rather than dying.** `/health` still answers 200 — it is what the
frontend boots against and what a container's readiness probe hits — while `/cube/*` and
`/simulate` are simply not mounted. Crashing on startup would turn a missing artefact into
an opaque restart loop on Hugging Face Spaces in Phase 12.

### Decisions worth not relitigating

- **Rasters ship as base64 float32 + bounds, never GeoJSON features.** 40,602 cells as
  features is tens of megabytes of coordinate strings describing a grid already defined by
  three numbers; as bytes it is 163 kB and is what deck.gl's `BitmapLayer` samples
  directly. The encoding is named in the payload (`base64:float32:little:row-major`) so a
  client never guesses and a change to it is a visible break.
- **`bounds_wgs84` is an envelope and says so.** A UTM rectangle is not a lat/lon
  rectangle, so a north-up overlay is off by a fraction of a cell at the corners. Fine at
  20 km, stated in the schema rather than hidden.
- **Meteorology is 400, not a raster.** It is `(time,)` — one reanalysis value per window.
  Painting it across 40,602 pixels would imply a field nobody measured, which is the exact
  mistake the cube's `Dims` split exists to prevent.
- **An empty mask is 422, never a result.** A polygon outside the tile rasterises to all
  False, simulates cleanly, and returns a delta of exactly zero everywhere — which reads
  as "the model found no effect" rather than "your polygon is in the Gulf of Guinea".
  Same for points and lines, which have no area.
- **The window is echoed in every response.** The same planting is −0.54 °C in summer and
  −0.15 °C in winter; a response that did not name its window would be unquotable.
- **Every ΔLST ships with its ceiling.** `tree_built_contrast_c` and the linear
  expectation travel with the delta, so the UI cannot show −0.54 °C naked against a
  reader's memory of the literature's 3–6 °C.
- **`serve_zarr_store` is separate from `zarr_store`.** Builds write to one, the API
  serves the other, and the second only moves once a build has been checked. That split is
  what lets you rebuild without pointing the demo at a half-finished cube.

### A fixture bug worth remembering

The first synthetic cube gave built-up pixels NDVI 0.16 and tree pixels 0.62 with nothing
in between. Every split the booster learned sat inside that empty gap, so a +30 % canopy
step landed short of all of them and **the modelled delta was exactly zero** — the API
looked broken when the fixture was. Real NDVI is continuous; the fixture now varies
greenness smoothly and derives land cover from it. A test cube whose distribution is
sharper than reality will pass things the real cube fails.

## Phase 6 — Frontend map ✅ DONE

The vertical slice closes: draw a polygon on real Lahore streets, get a modelled ΔLST
back, and compare before against after. **Basemap tiles are OpenFreeMap Positron** — no
key, no registration, no limit (D13).

### What shipped

| module | does |
|---|---|
| `api/client.ts` | typed fetchers for all four routes; surfaces the API's own `detail` |
| `raster/decode.ts` | base64 float32 → `Float32Array`, encoding checked not assumed |
| `raster/ramp.ts` | sequential ramps + a diverging one that is transparent at zero |
| `raster/image.ts` | colourising, the compare split, extents, column↔longitude |
| `raster/canvas.ts` | coloured bytes → a texture source deck.gl accepts |
| `map/MapView.tsx` | MapLibre + `MapboxOverlay`, bitmap and drawing layers |
| `map/useDrawnPolygon.ts` | click-to-draw polygon state |
| `panels/` | legend, and the result readout with its ceiling |

**29 frontend tests** (vitest) over the pure logic — decode, ramps, the split, the
GeoJSON ring. `tsc -b` and `oxlint` clean. Python side untouched: **161** still pass.

### Verified by driving the real browser, not by inspection

Headless Chromium against the production bundle and the live API:

| | result |
|---|---|
| basemap vector tiles | 11 requested, all 200 |
| console / page errors | **none** |
| draw 4 corners → close → simulate | ΔLST **−0.54 °C**, 979 cells, 9.79 km² |
| ceiling shown beside it | 2.60 °C |
| canopy actually added | 26.6 % of a requested 30 %, after capping |
| switch to 2024-winter, re-run | ΔLST **−0.15 °C**, ceiling 0.31 °C |

The summer/winter pair is the phase's own end-to-end proof: the same polygon, the same
requested canopy, a 3.6× difference in the answer, and each shown against its own
window's ceiling.

### The bug that ate an hour, and would have eaten the demo

**The basemap rendered nothing and said nothing about it.** MapLibre parses vector tiles
in a web worker whose URL it resolves as `new URL('./maplibre-gl-worker.mjs',
import.meta.url)` — not statically analysable, so Vite never emits the file and it 404s.

What makes it dangerous is the failure mode. The style fetched 200. The TileJSON fetched
200. Sprites and glyphs fetched 200. Nothing threw, no console error appeared, and the
map simply **never requested a single `.pbf`** — because tile loading is dispatched
through the worker. The symptom is a blank basemap that reads as a styling or CORS
problem. It was only found by counting network requests by extension.

Fixed in `map/maplibreWorker.ts` with `setWorkerUrl` plus a `?worker&url` import, and
`worker: { format: 'es' }` in the Vite config. A plain `?url` import is the tempting
shorter fix and yields a worker that 404s on *its* dependency instead. Both lines are
load-bearing; an `optimizeDeps.exclude` that also appeared to help was tested and
dropped, because it was not what fixed it.

**Worth generalising: "the request succeeded" is not "the feature works."** This is the
third time on this project — WorldPop's truncated-but-200 download, the ingest that died
mid-build leaving a valid-looking Zarr, and now a basemap whose every request returned
200 while the map stayed empty. Check the artefact, not the transport.

### Decisions worth not relitigating

- **Colouring happens in JS, not a shader.** At 40,602 cells the cost is nil, and it buys
  exact control: NaN genuinely transparent, exact zeros invisible, and the compare split
  as an array operation rather than a GPU trick.
- **The diverging ramp is transparent at zero, not merely pale.** After a simulation
  ~39,000 of 40,602 cells are *exactly* 0.000 — outside the feature neighbourhood both
  predictions are bit-identical. Giving them a colour would paint the whole city as
  changed and bury the intervention in it. The legend says so explicitly.
- **The compare divider is a line of longitude, not a screen position.** It maps to a
  column index, so the boundary stays on the same ground when the user pans or zooms and
  the two halves always describe the same places. Both halves share one colour domain;
  rescaling each side independently would render identical temperatures as different
  colours across the line.
- **Nearest-neighbour texture filtering.** At 100 m a cell is a measured unit; smoothing
  between cells invents gradients the cube does not contain.
- **Drawing is hand-rolled, ~90 lines.** The draw plugins in this ecosystem carry a peer
  matrix against MapLibre and deck.gl majors that is a standing upgrade hazard for
  "click a few points and close the ring".
- **The window is a visible control, and the result echoes it.** Not a hidden default.

## Phase 7 — Hindcast validation ✅ DONE

**The credibility weapon.** Needs Phase 3.

Since no target site is known (D7), start with **change detection**: scan the archive for
cells with large sustained NDVI or land-cover transitions between years, rank candidates,
pick one big enough to resolve at 100 m. Then train strictly on data *before* the change,
predict the post-change field, and compare to observed Landsat ST_B10. Report MAE and
spatial R² — including if they are bad.

### Delivered

`cores/thermal/hindcast.py` (pure) + `scripts/hindcast.py` (I/O) + 11 offline tests.

**Land cover cannot be the change signal.** WorldCover ships two epochs (2020, 2021) and
the cube carries it as `(y, x)` — static. NDVI is the only variable that both varies per
window and responds to planting, so change detection runs on it alone. That is a cube
limitation, not a modelling choice, and it means a car park paved over grass is detected
while a change of building material is not.

**The archive had to be widened first.** Sentinel-2 L2A over this tile starts in **2016** —
2015 returns zero scenes — so `cube_hindcast.zarr` covers 2016–2024, nine summers.

### Why MAE alone cannot answer this, and what replaces it

Phase 4 measured **2.9 °C** of error just for reaching an unseen *date*. A hindcast window
is exactly that, so its MAE inherits a whole-window offset that has nothing to do with the
change and would swamp the tenths of a degree a real greening buys.

The estimator that survives it is a **difference-in-differences**:

```
change_effect_error = bias(changed cells) - bias(unchanged cells)
```

Both groups sit in the same window and share the same offset, so it cancels. Near zero
means the model tracked the change as well as it tracks anything that year — *whatever*
the year-level error happened to be. MAE and spatial R² are reported alongside because
the plan asks for them, not because they settle the question.

**Three design decisions worth not re-litigating:**

- **The threshold is derived from the tile, not from a paper.** Between any two periods
  the whole tile greens or browns with rainfall and phenology; only the excess over that
  common drift is a site. A fixed 0.1 cutoff reports the entire tile in a wet year and
  nothing in a dry one — there is a test that pins exactly this.
- **Sustained, not annual.** Change is a median over several windows on each side. One
  dry summer moves NDVI everywhere.
- **Sites, not cells.** A robust 3σ cutoff flags ~0.3 % of an unchanged tile *by
  construction*. Those specks are noise; the hindcast scores against contiguous patches
  of ≥ 9 cells, which is also the smallest thing resolvable as a site at 100 m.

### Known limitation, stated before the numbers

The control group is every unchanged cell, **not a matched one**. If the places that
changed differ systematically from those that did not, part of the bias difference is that
covariate gap rather than mistracking. So a non-zero change-effect error is an **upper
bound** on the model's error at the change, not a point estimate. Matching on land cover
and baseline NDVI would tighten it.

Separately, the tests surfaced the extrapolation risk concretely: a patch greener than
anything in training gets the cooling of the greenest cell the model ever saw and no more,
because a tree predicts a constant beyond its last split. `test_greening_beyond_the_
training_range_under_predicts_and_is_visible` pins it, and it is the most likely way a
real hindcast under-states a large intervention.

### Measured — build `4a812f30ad48`, 9 summers 2016–2024, 1,098 s

Change detection over 2016–2019 vs 2020–2024, threshold **0.091 NDVI** derived from the
tile (median tile-wide drift +0.025): **2,295 cells over threshold, 1,154 inside a site,
57 sites**. The eight largest are all *greening*, +0.16 to +0.26 ΔNDVI, 29–119 cells.

**Do not read a single run.** The per-run verdict swings between WEAK, OK and INCONCLUSIVE
purely on which year you score in, so the phase was re-run across a grid of **12
configurations** — three scored years × four site-size cutoffs (≥9, ≥30, ≥60, ≥100 cells,
i.e. 29 sites down to the single largest one). That grid is the result, not any row of it.

Controls are **matched** on land-cover class × baseline-NDVI decile. That is not a detail:
it changes the answer, and an earlier draft of this section got it wrong without it.

| | observed effect | change-effect error |
|---|---|---|
| raw controls, mean over 12 | +0.020 °C | −1.115 °C |
| raw, standard deviation | 1.126 | 0.880 |
| **matched controls, mean** | **−0.468 °C** | **−0.714 °C** |
| **matched, standard deviation** | **0.533** | **0.472** |
| sign consistency, matched | negative **10/12** | negative **12/12** |

**Matching halves the noise, and that is the evidence it is doing real work** — the spread
on the observed effect drops from 1.126 to 0.533, and on the error from 0.880 to 0.472.
Support is near-total: 667 of 671 changed cells find a control at the loosest cutoff, 114
of 114 at the strictest.

**1. Greening does cool this tile — by about half a degree.** Against matched land the
observed effect is **−0.47 °C**, negative in 10 of 12 configurations. *An earlier version
of this section reported +0.02 °C and concluded nothing measurable had happened. That was
an artefact of comparing greened land against the whole tile.* Land that greens starts
low-NDVI and often urban-fringe — warmer than tile average for reasons that have nothing
to do with the change — and that bias almost exactly cancelled the real cooling signal.

**2. The model over-states that cooling by roughly 2.5×.** It implies **−1.18 °C** where
matched observation shows **−0.47 °C**, an error of **−0.71 °C** that is negative in
**12 of 12** configurations. Matching shrank this from −1.12 °C, so about a third of the
apparent over-prediction was confounding and two thirds is real.

**This is the first direct evidence for the standing space-for-time risk.** Contrast-
between-places over-states effect-of-changing-a-place, here by a factor of ~2.5. The
simulator is not worthless — the sign and rough scale are right — but **every modelled
ΔLST should be read as roughly 2.5× the realised figure**, and a demo quoting −0.5 °C
should say the honest expectation is nearer −0.2 °C.

Caveats that keep this honest: the 12 configurations share sites and overlapping windows,
so they are **not** independent samples and no p-value follows from them; matching is on
land cover and baseline NDVI only, so an unobserved covariate could still confound; and
the sites are Lahore's, not anywhere else's.

The plan's "pick one site big enough to resolve at 100 m" is `--min-site-cells 100`, which
isolates the single largest patch (114 cells). It was run, and it remains the noisiest of
the twelve even matched — one site is not enough to validate against on this tile, which is
itself the answer to that instruction.

**A negative R² was hiding a working model.** Raw spatial R² is −0.68 to −7.15, which reads
like total failure. It is not — it is one constant offset:

| | raw | offset removed |
|---|---|---|
| MAE | 1.94 – 5.26 °C | **0.91 – 1.24 °C** |
| spatial R² | −0.68 – −7.15 | **+0.25 – +0.56** |

The model still ranks the tile; it cannot reach an unseen year's *absolute* level. Those
are different failures with different fixes, which is why `HindcastScore` now carries both.

### Why the offset exists — and it is not what I first assumed

The obvious explanation is that 2024 was hotter than anything in training and gradient
boosting cannot extrapolate. **Checked, and false.** 2024's air temperature is 34.0 °C,
rank 4 of 9 and comfortably inside the training range — yet its mean surface temperature
is 46.5 °C, rank 9 of 9.

Across the nine summers, `air_temp_c` correlates with mean summer LST at **r = 0.554,
r² = 0.31**. The feature carrying **91.5 % of the model's gain** (Phase 4) explains under a
third of the between-summer variation in the quantity it is supposed to predict. Pooled
across seasons it looks decisive because it separates summer from winter; between summers
it is close to useless, and the model has nothing else that varies with the year.

That single fact explains the Phase 4 leave-one-window-out result (2.9 °C), this hindcast's
1.9–5.3 °C offset, and why both grow with the gap. **It is the most actionable finding in
this phase:** the temporal features are too weak, not the spatial ones. Adding windows will
not fix it — adding a feature that actually tracks between-summer surface heating might.

## Phase 8 — Equity ✅ DONE

Needs population from Phase 3. A pure function, not yet a full core:
`benefit_distribution(delta_lst, population, deprivation_proxy) -> deciles`.

Output: share of total cooling person-degrees per population decile, and a flag when the
top three deciles capture the majority. This is the panel that critiques the user's own
plan — the moment the description says wins the room.

### Delivered

`cores/equity.py` (pure) + `EquityResponse` on `POST /simulate` + TS types + 11 tests.

**Deciles hold a tenth of the *people*, not a tenth of the pixels.** That single choice is
what makes the panel readable without arithmetic: even sharing is 10 % per decile, so any
skew is visible at a glance. Equal-pixel deciles would put most of Lahore in one bucket.

**Warming counts as negative benefit rather than being clipped to zero**, so a plan that
cools the wealthy and warms the poor cannot report the same headline as one that simply
cools less.

### The panel works — it ranks two plans opposite to how ΔLST does

Three interventions, +30 % canopy, 2024-summer, on `cube_phase4.zarr`:

| plan | mean ΔLST inside | top-3 share | concentrated | on empty land | densest decile |
|---|---|---|---|---|---|
| canal (central) | −0.507 °C | 65.9 % | yes | 0.0 % | **0.0 %** |
| river Ravi (NW) | **−0.595 °C** | 55.0 % | yes | **26.4 %** | 6.9 % |
| whole tile | −0.776 °C | 31.5 % | no | 8.0 % | 10.2 % |

**The Ravi plan cools more and helps fewer people.** It beats the canal on raw ΔLST
(−0.595 vs −0.507) while dumping **26 %** of that cooling on land where nobody lives. A
tool that reported only ΔLST would rank it the better plan. That inversion is the entire
argument for this phase existing.

And the demo intervention indicts itself: the canal planting delivers **0.0 %** of its
benefit to the densest decile — the people with the worst heat exposure — while deciles
5–7 take two thirds. Only the whole-tile plan is unconcentrated, at 31.5 % against the
even 30 %.

### Cooling on empty land is measured in degree-cells, not person-degrees

An empty cell has zero residents, so its person-degrees are zero *by construction* and
"how much of my cooling reached nobody" would answer itself with 0 %. The first
implementation had exactly this bug and a test caught it. The wasted-cooling figure is
therefore area-weighted (sum of −Δ over uninhabited cells) while everything else is
people-weighted. 8.5 % of the tile is genuinely uninhabited, so the metric is live — the
canal's 0.0 % is a real result, not a dead code path.

### The deprivation proxy is absent, deliberately, and the argument is kept

The plan's default assumption was nightlights plus building density. **Neither VIIRS
nightlights nor GHSL built-up is on Planetary Computer** — checked, zero matching
collections — so a deprivation layer needs a new external HTTP source in the style of
WorldPop. That is a cube expansion, not equity work.

What was *not* done meanwhile: substituting NDBI or built-up density and calling it
deprivation. Dense built-up in Lahore contains both the wealthiest and the poorest
districts, so that swap would produce a confident equity claim the data cannot support.
The third argument stays in the signature and is exercised by a test, so a real layer
drops in without a rewrite. Stratifying by population density answers a narrower but
honest question: **does the cooling reach the crowded places, where heat stress is worst?**
On the demo intervention, no.

### The React panel

`web/src/panels/EquityPanel.tsx`, with its interpretation logic split into a tested
`equity.ts` — the same pure-function/render split `raster/decode` and `units` already use,
so no testing-library and no jsdom were added for it. 19 new web tests.

Three rendering decisions carry the same weight as the estimator's:

- **Bars normalise to the widest bar, not to 100 %.** An even distribution puts every
  decile at 10 %, which against a fixed axis renders as ten identical slivers and shows
  nothing. The 10 % reference line moves with the scale.
- **A warmed decile is drawn, not clipped.** Distinct hue *and* a stripe pattern, so the
  difference does not rest on colour alone.
- **Wasted cooling outranks concentration in the headline.** A plan can be perfectly even
  across the deciles it reaches and still deliver a quarter of its effect to a riverbank —
  and that is the failure a ΔLST number hides most completely.

`equity: null` renders **nothing**, never a row of zeroes: "nobody counted" and "nobody
benefits" are opposite findings and must not look alike.

### Two defects found by auditing the phase after calling it done

**1. Shares divide by the net benefit, and the net benefit can vanish.** A tile split half
cooling, half warming nets out to almost nothing, so each decile's share became its own
value over a denominator near zero: shares of **±2010 %** and a `top_three_share` of
**6030 %**, which cleared the concentration threshold and would have rendered to the
screen as a confident finding. `shares_reliable` (net-to-gross ≥ 0.2) now gates it,
`concentrated` is false whenever the split is unreliable, and the panel draws no bars at
all — it says the plan has no net effect to share out. Real planting sits at net/gross
0.979, so the guard never fires on a genuine scenario.

**2. The panel used a non-null assertion where a tested safe helper already existed.**
`shares.at(-1)!` would render `NaN%` on an empty distribution; `densestShare()` handles it
and was already covered by a test. Using the helper.

### Does Phase 7's over-prediction contaminate these shares?

Shares are ratios, so a *uniform* modelling error cancels out of them entirely. A
**density-dependent** one would not — and it is. Measured on the 2024-summer hindcast,
model bias by population decile:

| decile | 1 (sparsest) | 5 | 10 (densest) |
|---|---|---|---|
| bias | −5.43 °C | −5.31 °C | −4.54 °C |

A **1.02 °C monotonic spread**: the model is systematically further off in sparse areas
than crowded ones. That is measured on *absolute* prediction, and ΔLST differences two
predictions, so most of it cancels — but "most" is not "all", so it was measured properly
rather than left as a worry.

**Measured: the tilt is bounded at 2.2 percentage points.** Planting +30 % canopy over the
*whole* tile makes the response comparable across deciles, and the share each decile
receives can then be set against the share it would receive if the model's canopy response
were identical everywhere:

| decile | 1 | 5 | 10 |
|---|---|---|---|
| actual share | 11.9 % | 9.6 % | 9.8 % |
| if response were flat | 9.7 % | 10.0 % | 10.3 % |

Worst distortion **2.18 pp**, at decile 1; every other decile is within ~0.7 pp. The
sensitivity spread looks large as a ratio (75 % of the mean) but is driven almost entirely
by decile 1 — deciles 2–10 sit between −2.5 and −3.5 °C per unit canopy.

**The Phase 8 conclusion survives this comfortably.** The canal planting gives the densest
decile **0.0 %** against an even 10 % — a ten-point gap that a 2.2-point modelling artefact
cannot produce. Quote the decile numbers, but quote them with ±2 pp of slack rather than to
one decimal.

### `ResultPanel`'s caveat was stale and is now the hindcast number

It read *"That is the hindcast, and it is not built yet."* It is built, and it found the
emulator over-states cooling ~2.5x. The panel now says so and shows the discounted figure
next to the modelled one, so the correction reaches the screen rather than living only in
this document.

## Phase 9 — Air dispersion core ✅ DONE

Steady-state Gaussian plume on the 100 m grid, winter inversion parameterisation, emission
inventory from OSM, canopy-weighted deposition. The second core (D8), and the one that
answers *"ban combustion vehicles inside this ring"*.

**Delivered:**

- `ingest/osm.py` — one Overpass query, road class × fleet mix + kiln points, densified and
  binned to g/s per cell. **No `osmnx`** (D15).
- Two new cube variables: `pm25_emission_g_s` (static) and `wind_direction_deg` (per
  window). Direction is reduced by **vector mean**, not median — 350° and 10° average to
  180° under any scalar reduction, which points every plume exactly backwards.
- `cores/air.py` — `AirParameters`, `plume_kernel`, `concentration`, `simulate`, and
  `leave_one_station_out`. Pure, like every core. The whole tile is one FFT convolution,
  because a uniform wind field makes superposition a convolution.
- `POST /simulate` gains `emission_fraction_removed` (D14) and an optional `air` block.
- `scripts/build_air_layers.py` — adds both variables to an existing cube in seconds, so
  Phase 9 never required rebuilding a known-good Zarr against Planetary Computer.
- `scripts/validate_air.py` — leave-one-station-out against OpenAQ (D16).
- 36 new offline tests. Still no test that touches the network — and fixing this phase's
  fixtures exposed one that already did (see below).

### What the tile actually contains

`build_air_layers.py` against `cube_phase4.zarr`, 4 windows:

| | |
|---|---|
| road ways | **35,405** |
| brick kilns | **0** — OSM has none tagged inside the bbox |
| total emissions | **38.19 g/s** ≈ 1,200 t/year of PM2.5 |
| cells carrying a source | 31,780 of 40,602 (**78 %**) |

Zero kilns is a finding, not a bug: Lahore's kilns ring the city outside a 20 km tile, and
OSM's coverage of them is patchy anyway. The kiln term is implemented and tested; on this
tile it contributes nothing, and the inventory is road transport alone.

### The seasonal result — this is the phase's own end-to-end proof

Modelled **locally-generated** PM2.5 across the tile, same emissions every window:

| window | wind | mixing height | mean | p95 | max |
|---|---|---|---|---|---|
| 2023-summer | 2.14 m/s from 131° | 800 m | 0.5 | 1.2 | 1.5 |
| 2023-winter | 1.06 m/s from 102° | 250 m | 2.9 | 6.0 | 8.7 |
| 2024-summer | 2.25 m/s from 300° | 800 m | 0.5 | 1.0 | 1.3 |
| **2024-winter** | **0.79 m/s from 66°** | **250 m** | **3.8** | **8.1** | **10.9** |

µg/m³. **7.4x the concentration from identical sources** in 2024 and 6.1x in 2023, which
is the entire argument for putting winter in the cube back in Phase 3, now cashed in.

A worked low-emission zone — 4 km² over the busiest cell, all vehicle emissions removed:

| | 2024-winter | 2024-summer |
|---|---|---|
| inside the zone | **−0.91 µg/m³** | −0.10 |
| 1 km ring outside (980 cells) | −0.36 | — |
| best single cell | −3.47 | −0.37 |

**The same plan buys 8.7x more in winter.** And the ring matters: unlike cooling,
which stops at the edge of a 500 m feature neighbourhood, a plume is still measurable a
kilometre downwind — which is why the air block reports spillover over 10 cells and the
thermal block over 2.

Planting alone, +30 % canopy over the same 4 km², moves air by **−0.0003 µg/m³**. That is
honest and it is small, and the `caveat:` note in `concentration` says why: deposition
uses one tile-mean velocity, so a small planting credits the whole tile with a marginally
better sink rather than crediting the polygon. Trees are a thermal instrument here, not an
air one.

### The kernel radius was a physics parameter wearing a numerical costume

It first shipped at 30 cells, on the reasoning that 3 km is far enough that the kernel is
negligible. That reasoning is right for a *point* source and wrong for an *area* source:
concentration over a city accumulates roughly linearly with the length of upwind fetch
still contributing, so truncating at 3 km returned 1.41 µg/m³ where 200 cells returns 3.84.
**A 63 % truncation that would have read as a modelling result.**

Measured, then fixed:

| radius | 3 km | 6 km | 10 km | 15 km | 20 km |
|---|---|---|---|---|---|
| tile mean, 2024-winter | 1.41 | 2.41 | 3.23 | 3.72 | **3.84** |
| cost | 4 ms | 5 ms | 10 ms | 17 ms | 37 ms |

The default is now 200 cells — the tile itself. Past that is outside what the inventory
knows about, which is the same limitation as the missing background, expressed in distance.

Worth noting what the truncation did *not* affect: the low-emission zone's delta was
identical at both radii, because a 4 km² removal is entirely near-field. The bug lived in
the level, not the difference — the same reason the delta is the quotable object.

### What this does not claim

**These are local increments, not concentrations a monitor reads.** The inventory covers
this tile's roads and nothing else, so a regional background that usually dominates
Lahore's PM2.5 is absent by construction. 3.8 µg/m³ against a real winter reading of
150–300 µg/m³ is not the model saying Lahore's air is clean; it is the model saying *this
much of it comes from these streets, in a single pass, at 10:30 in the morning*. The
background cancels in a difference, exactly as meteorology does in the thermal core, which
is why the API ships a delta and never a level.

Also absent by construction: multi-day accumulation under a persistent inversion (this is
steady-state, single-pass), street-canyon trapping (sub-100 m), and secondary aerosol
chemistry (hours, downwind of the tile).

### Validation is built and has not been run

`scripts/validate_air.py` implements leave-one-station-out with an affine fit — the
intercept is the background the core does not model, the slope is the scale error in an
inventory built from literature emission factors, and both terms are load-bearing. It is
scored against a null model (predict each station from the mean of the others), because
with a handful of monitors in one city a model that merely reproduces the city mean posts a
respectable error while resolving nothing spatial.

**It has not been run against real stations**, because OpenAQ v3 needs a key nobody has set
(D16). The leave-one-out arithmetic is tested on synthetic data — it recovers a known scale
and background exactly, refuses fewer than four stations, and refuses a set of stations that
all model the same value rather than letting numpy invent a slope from nothing. Until it
runs, **the emission factors are literature values and the modelled magnitudes are
uncalibrated.** Say so when quoting them.

### A test that was quietly using the network

Fixing this phase's fixtures surfaced it: `test_a_lazy_read_failure_is_caught_and_isolated`
stubbed only the STAC boundary, so it genuinely fetched WorldPop and Open-Meteo on every
run, and passed because those happened to succeed. Adding Overpass to the ingest is what
exposed it — the new call got a real HTTP 504 from `overpass-api.de` inside the test suite.
It now takes the `mocked` fixture like every other build test. Worth recording because it is
the exact failure mode the no-network rule exists to prevent: not a test that fails
offline, but one that passes for the wrong reason.

## Phase 10 — DSL + agent layer ✅ DONE

A typed intervention language, a validator that refuses plans the tile cannot hold, a
costed preset library, a planner that reads plain text, and a deterministic explainer. The
LLM is one optional module at the edge of all of it.

**Delivered:**

- `dsl/schema.py` — `Plan`, `PlantTrees`, `RestrictVehicles` as a discriminated union.
  A plan carries **no geometry**, for the same reason a core does not (D6): a plan says
  *what*, and the polygon is the API's business. That is also what makes a preset reusable.
- `dsl/validate.py` — `resolve(plan, measurement)`, the tree-count ↔ canopy-fraction
  conversion at 25 m² of crown per tree, and the refusals.
- `dsl/library.py` — five costed presets and two literature unit costs, `calibrated=False`
  everywhere.
- `dsl/planner.py` — text → `Plan`, model first when a key exists, regex parser always.
- `dsl/explain.py` — numbers → a brief whose `uncertainties` list is never empty.
- `dsl/llm.py` — the only file in the project that speaks to a model (D18).
- `api/measure.py`, `POST /plan`, `GET /plan/presets`, and a `brief` block on `/simulate`.
- Frontend: preset buttons, a free-text box, and a brief panel that renders the
  uncertainties **in the open** rather than behind a toggle.
- **97 new offline tests** (78 in `dsl/`, 19 on the routes).
  Still nothing touching the network — `dsl/test_llm.py` stubs `urlopen` explicitly rather
  than depending on whether a key happens to be set. Ten more in the browser suite.

### The validator is the phase, and the refusal is the product

Measured on the real cube, a 6.38 km² box over central Lahore, `2024-summer`:

| asked for | answer |
|---|---|
| "street trees" preset (15 % canopy) | 38,280 trees, **$574k**, ΔLST **−0.27 °C** |
| "plant 5,000 trees here" | 2 % canopy, **$75k**, ΔLST **−0.03 °C** |
| "plant 900,000 trees here" | **422 refused** |
| "ban combustion vehicles here in winter" | resolved to `2024-winter`, **$1.6M** |

The refusal in full, because the wording is the deliverable:

> 900,000 trees need 22.500 km² of crown at 25 m² each, but this 6.380 km² polygon has
> only **3.433 km² still plantable** — room for about 137,305. Shrink the planting or
> enlarge the polygon.

**That 3.433 km² is measured, not assumed.** It is the thermal core's own
`effective_fraction` asked for a canopy of 1.0, so what comes back per cell *is* the
headroom that cell has left, with water and no-data already zeroed. Using the core's
function rather than a parallel rule of thumb is what stops the DSL and the physics
disagreeing about how green a cell already is — and it is why a polygon over the Ravi
refuses a planting instead of quietly returning no cooling.

The two units are treated differently on purpose, and the asymmetry is the units' existing
contracts rather than a new rule: **a canopy fraction over the headroom warns, a tree count
over it refuses.** A fraction is already documented as a ceiling the core caps per cell; a
count is a quantity somebody would procure, and 900,000 trees with nowhere to go is not a
ceiling, it is a mistake.

### 5,000 trees is a much smaller plan than it sounds

The most useful thing the conversion does is deflate an intuition. 5,000 trees at 25 m² of
crown covers 0.125 km². Spread over a 6.38 km² polygon that is **2 % canopy**, which buys
−0.03 °C — against −0.27 °C for the 15 % street-trees preset costing eight times more.
Before this phase the same request went in as a slider position and came out as a number
with nothing to compare it against. Halving the crown area doubles the trees a polygon
holds, so `TREE_CANOPY_M2` is stated in every response (`basis`) rather than buried.

### The brief exists because a figure without its caveats is the wrong figure

`dsl/explain.py` is templates, not a model, and that is the point: a template cannot
restate a number it did not receive, and it cannot smooth a caveat into a hedge. Every
brief carries the hindcast correction (the headline quotes −0.11 °C where the model says
−0.27 °C), the window, and the surface-versus-air distinction. `confidence` has two values,
`low` and `moderate` — there is no `high`, because nothing here has earned it.

Two failures the first real run exposed, both fixed:

- **A caveat belongs to a figure, not to a plan.** A traffic-only plan was being shipped
  with the hindcast correction and the land-surface-temperature note attached to a
  temperature it never quoted, plus an equity block reporting its shares as "unreliable"
  because they divide by a delta of exactly zero. Noise is how a real caveat stops being
  read, so the thermal caveats now appear only when something was planted.
- **`air is None` means two opposite things.** "This plan does not touch traffic" and
  "it does, and this cube has no emission inventory" arrived identically. The brief now
  distinguishes them: *"That is a missing layer, not a modelled finding of no effect."*
  This was not hypothetical — it is exactly what the cube on the machine this was built on
  does, and the first version of the brief reported it as "changes nothing measurable".

### No LangGraph, and no key either

Two things budgeted for and not spent (D17, D18). The planner is two steps with no
branching and nothing to checkpoint, so a graph runtime would have added a dependency to
express a function call. And the LLM is optional in the strong sense: with no key, `/plan`
parses text with a regex parser that handles "5,000 trees", "30 % canopy", "ban combustion
vehicles", "remove 40 % of traffic" and "in winter", and **nothing else in the layer calls
out at all**. `GET /plan/presets` reports which parser is live rather than implying a model
read the sentence when a regex did.

The safety argument for letting a free-tier model near a simulator is the same shape: the
model's output is parsed as a `Plan` or it is nothing. Invalid JSON, an unknown action, a
planting with both units or with neither — all fall back to the rule parser with a warning
saying so, and none of them reach a core. That fallback is tested on four kinds of bad
model output; the model itself is stubbed, because no test may touch the network.

### What did not run

**The air path was verified against the synthetic cube, not the real one.** `serve_zarr_store`
points at `cube_phase9.zarr`, which is not on this machine, and Overpass returned 504 on
both attempts to rebuild the emission layers onto `cube_phase4.zarr`. So the end-to-end
numbers above are the thermal path on real Lahore data with no inventory present, and the
`/plan` → `/simulate` → air block round trip is covered by the route tests only. The
season-resolution path — "in winter" landing on `2024-winter` rather than the summer
default — did run on the real cube, which is the part of that story that mattered most.

**The LLM path has never run against Gemini.** No key is set, so every parse in every
measurement above is the rule parser's. The adapter is tested against a stubbed transport
and nothing else.

## Phase 11 — Voice, VLM, council brief ✅ DONE

Three features that share one property: each was supposed to need a paid service and none
of them does. Voice is the browser's own recogniser, the brief is the browser's own print
dialog, and the photo reader is the Phase 10 Gemini key — which nobody has, so that third
one is built, tested against a stub, and has never read a real photograph.

**Delivered:**

- `dsl/observe.py` — `Observation`, the vision prompt, and the parse. Pure: the adapter is
  an argument, so the whole path is tested offline.
- `dsl/llm.py` gains `VisionAdapter` and `complete_json_with_image`. Still **the only file
  in the project that talks to a model** (D18).
- `api/observations.py`, `api/schemas/observations.py`, `api/routes/observations.py`, and
  `cell_from_lonlat` in `api/geometry.py` — `POST /observations`, `GET /observations`,
  `GET /observations/layer`.
- **Urdu in the rule parser** (`dsl/planner.py`): digit normalisation and the vocabulary
  for the two things the DSL can express. See below — this is the part that was not on the
  plan and had to be.
- `web/src/voice/` — Web Speech capture in English and Urdu, and the honest support check.
- `web/src/panels/BriefDocument.tsx` + a print stylesheet — the council brief as a printed
  sheet, via `window.print()`.
- `web/src/panels/ObservationsPanel.tsx` — submit a photo, see what the model made of it.
- **33 new Python tests and 24 new browser tests.** Nothing touches the network: the vision
  model is stubbed at the adapter seam, exactly as the planner's is.

### Voice: the transcript was the easy half

**Web Speech API, not LiveKit and not self-hosted Whisper.** LiveKit Cloud meters minutes
and Whisper needs somewhere to run; `webkitSpeechRecognition` is already in the browser,
free, keyless, and adds no dependency — the same argument that chose OpenFreeMap over
MapTiler. The cost is uneven support: Chrome and Edge have it, Firefox does not, so
`recognitionConstructor()` returns the constructor or `null` and the panel renders no
microphone at all rather than a button that does nothing.

The transcript lands **in the text box, not in a request**. A recogniser that mishears
should cost an edit, not a wrong simulation.

### The Urdu finding: capturing Urdu is not supporting Urdu

Setting `lang = "ur-PK"` took one line and would have shipped a feature that does not work.
The speech API hands back Urdu text, that text goes to `/plan`, and `/plan` has no key — so
the **rule parser** is what reads it. The rule parser was English-only. "Voice in English
and Urdu" would have meant English, plus a microphone that produces a 422 in Urdu.

Two things had to change, and the first is the one that would have been missed:

- **Eastern Arabic-Indic digits match no `\d`.** ۵۰۰۰ is not 5000 to any pattern in the
  parser, so an Urdu sentence with a tree count in it parsed as a plan with no quantity —
  which reads as "it does not understand Urdu" rather than "it does not understand its
  digits". `_normalise` folds ۰-۹ and ٠-٩ to ASCII before any pattern runs, so one set of
  number patterns serves both scripts.
- **A small, deliberate vocabulary**: درخت, پودے, شجرکاری for planting; گاڑیوں, ٹریفک with
  پابندی for restriction; فیصد and ٪ for percentages; سردیوں and گرمیوں for the season —
  which matters most, since winter is where the air result changes by 6-7x. Urdu also puts
  the count after the noun, so there is a reversed pattern for "درخت ۲۰۰۰".

The refusal message names the Urdu vocabulary too. Someone who just spoke Urdu into a
microphone needs to be told what Urdu it understands, not what English it understands.

**The honest caveat is on screen**: Urdu recognition is materially weaker than English in
every browser engine. The parser understands Urdu; the microphone is the weak link, and the
UI says which half failed.

### The council brief: the browser already had a PDF renderer

No WeasyPrint. It would have been a Python dependency, a font stack and a second rendering
path, to reproduce a button every browser ships. `BriefDocument` is `display: none` on
screen and the only thing on the page under `@media print`, so the sidebar keeps its shape
and the document keeps its own.

Two details that make a printed sheet honest, both of which only matter because paper
outlives the session that produced it:

- **Every number comes from the API.** The component computes nothing except which sections
  exist. A figure invented on the client would be the hardest kind of error to trace once
  it is on paper and nobody can click through to what produced it.
- **`break-inside: avoid` on each section.** A caveat list split across a page break loses
  its heading, which is precisely how "what this does not prove" becomes a page nobody
  reads. The sheet carries the window, the tile, the hindcast-corrected figure beside the
  raw one, the confidence, and a footer saying these are model outputs.

### Citizen photos land on the grid and stay out of the cube

This is the phase's one real architectural decision. "Writing observations back into the
cube" was the plan's wording, and it is **not** what shipped: observations get the grid and
nothing else — the same 201×202 cells, their own store, their own endpoint, and no route
into `cube.zarr`.

The reason is the cube's contract. Every variable in it is an instrument reading with a
known error, aligned by `state/`. A language model's reading of a phone photo is not, and
mixing the two makes "what does the cube say" unanswerable. So they are drawn *beside* the
model output, which is what makes the comparison useful, and `measured: false` is on the
response so no client can lose track of which is which.

Five smaller choices, each of which has a wrong version that looks fine:

| | shipped | the wrong version |
|---|---|---|
| unreported cells | **NaN** | 0 — draws 40,000 unphotographed cells as "checked, nothing wrong" |
| two reports in one cell | **max severity** | mean — averages a shaded street with a burning waste pile |
| the cell a photo lands in | **assigned by the API** from submitted coordinates | asked of the model, which is shown pixels and no location |
| store size | **bounded at 500** | unbounded — a memory leak with a public door on it |
| no key | **503 with the reason** | an empty observation, which renders as a report nobody made |

The prompt forbids identifying a person, a plate or an address, and that instruction is
asserted in a test rather than trusted to survive an edit — it is the only thing between
this feature and a surveillance tool. Nothing reads EXIF either: the location is the one
the user clicked, so a photo cannot silently report where it was really taken.

### What did not run

**No photograph has ever been read.** `TERRARIUM_GEMINI_API_KEY` is unset, so every test of
the vision path uses a stub, and the quality of the model's categories, severities and
confidences is completely unmeasured. What is verified is the plumbing: the prompt, the
validation, the refusal of anything that is not an `Observation`, the placement on the grid,
and the 503. Treat the feature as built and unproven — the same posture Phase 9 takes about
OpenAQ, for the same reason.

**Voice was not tested in a browser by an automated test.** `SpeechRecognition` cannot be
driven from vitest, so what is covered is support detection, transcript assembly, the error
messages, and the language list matching what the parser can read. The recogniser itself was
exercised by hand only.

## Phase 12 — Deployment ⬜

**Hugging Face Spaces** (CPU Basic, no card) for the API, **Vercel** or **Cloudflare
Pages** for the web. Not Modal or Fly.io — neither is reliably free (D13). The cube is
~1 MB, so it ships inside the container rather than needing object storage. Last, by
decision (D12) — never blocks physics work.

---

## Tracks — two people (D2)

| | Track A — data & physics | Track B — product & interface |
|---|---|---|
| done | **Phase 7** hindcast, **Phase 9** air core | **Phase 8** equity, **Phase 10** DSL, **Phase 11** voice + brief + photos |
| now | rebuild `cube_phase9.zarr` when Overpass answers, then run the OpenAQ calibration once a key exists | **Phase 12** deployment — the last one |

Phases 2–6 are done, so the **cut-order minimum is met**: one tile, a thermal core, and a
map showing the delta. Everything from here is upside on a submission that already stands
up on its own.

The `CoreResult` / `/simulate` contract that had to be frozen before the tracks could
split is now code on both sides — Pydantic models in `api/schemas/`, hand-mirrored
TypeScript in `web/src/api/client.ts`. **That mirroring is by hand and is the seam most
likely to drift**; generating it from `/openapi.json` is the fix if it starts to hurt.

## Cut order, worst case

1. One tile ✅
2. Thermal core + tree-planting delta ✅
3. Map showing the delta ✅
4. The hindcast number ✅ — Phase 7
5. The equity panel ✅ — Phase 8

**Every item on the worst-case list is done.** Phases 9 and 10 are both above the cut
line: the air core and the DSL raise the ceiling rather than filling a hole, and either
could be dropped from a demo without leaving a gap on screen. The one thing that would
leave a gap is the cube — see the standing risks.

## Standing risks

- **Space-for-time substitution — no longer a suspicion, now measured.** The cube teaches
  *why this pixel is hotter than that one*; using it for interventions assumes
  contrast-between-places equals effect-of-changing-a-place. Phase 7 tested that directly
  and it holds only in sign, not magnitude: against controls matched on land cover and
  baseline NDVI, greening cools **−0.47 °C** while the model implies **−1.18 °C**, an
  error negative in 12/12 configurations. **Treat every modelled ΔLST as roughly 2.5x the
  realised figure, and say so before anyone asks.** Quantified now, not hedged.
- **An unmatched control group is worth about half a degree of bias, in the direction that
  flatters nothing.** Comparing greened cells against the whole tile rather than against
  comparable land reported the observed effect as +0.02 °C when it is −0.47 °C — the
  covariate gap almost exactly cancelled the real signal. Any future group comparison on
  this cube (Phase 8's equity deciles especially) should match before it concludes.
- ~~**Phase 3 blocks four later phases.**~~ Unblocked — 4, 7, 8 and 9 all have the cube
  they were waiting on.
- **Four windows is still a thin time axis.** Having a time dimension is not the same as
  having enough of one. Two years is enough for meteorology to vary (the summers differ
  by 2.5 °C) but nowhere near enough for hindcast change detection, which needs a decade
  to find a land-cover transition big enough to resolve at 100 m. Widening `window_years`
  is a build-time cost, not a code change — but it has to actually happen before Phase 7.
  Phase 4 put a number on the cost: **leave-one-window-out MAE is 2.9 °C against the
  spatial split's 0.55 °C**, and with only two seasons observed a held-out summer has
  little but winter to lean on. More years should close that gap, and it is the metric
  to watch when they land.
- **Meteorology can act as a window identifier.** `air_temp_c` takes 91.5 % of gain with
  four windows, because four distinct values are enough to label the window and the
  window explains most of a pooled target spanning 22 °C. Harmless for ΔLST — `simulate`
  holds meteorology fixed, so it cancels in the difference — but it means the feature
  importances no longer describe what drives the *intervention*, and anyone reading them
  as "canopy barely matters" will draw the wrong conclusion. Re-check as windows are
  added: more windows make the identifier less degenerate, not more.
- **Winter composites rest on 3–5 clear looks per pixel, summer on 6+.** Measured via
  `obs_depth_*`, not inferred from scene counts. The winter contrast varies more between
  years than the summer one almost certainly because of that, not because of the weather:
  do not read inter-annual winter differences as signal yet. Relaxing the cloud ceiling
  is **not** the remedy — it trades one extra look for ~1.7 °C of cold bias (see next
  action 5).
- **Build fragility.** Planetary Computer drops connections; retries handle it, but keep
  a known-good Zarr and never rebuild the night before a demo.
- **"200 OK" is not "it worked", and this project keeps proving it.** Five instances so
  far: WorldPop truncating a transfer without erroring, an ingest dying mid-build and
  leaving a valid-looking four-window Zarr with two empty windows, MapLibre's worker
  404ing while style, TileJSON, sprites and glyphs all returned 200 and the map rendered
  nothing, a winter composite reporting "100.0 % valid" over 9 pixels no scene ever saw,
  and a **stale `thermal.txt` that loaded cleanly, served `/health` and `/cube/*`
  perfectly, and failed only on `/simulate`**. None surfaced as an exception; each was
  found by checking the artefact rather than the transport. Assume the next one behaves
  the same way.
- **Artefacts go stale independently of the code that reads them.** The model file
  carries no version, so a booster trained before meteorology existed is
  indistinguishable from a current one until LightGBM counts columns at predict time.
  `runtime._check_model_features` now compares `Booster.feature_name()` against
  `FEATURE_NAMES` **as a sequence** — LightGBM matches positionally, so the same names in
  a different order predict confidently and wrongly with no error anywhere. The cube got
  this treatment in Phase 5 and the model did not; assume any *other* artefact added
  later needs it too.
- **The TypeScript API types are mirrored from Pydantic by hand.** `web/src/api/client.ts`
  will silently drift the first time a schema changes and nobody updates it — a renamed
  field becomes `undefined` on a panel, not a build error. Generate from `/openapi.json`
  if it bites once. (Audited 2026-08-05: all 9 interfaces match the live responses field
  for field.)
- **Purity drift.** The moment a core reads config or opens a file, API caching and
  offline tests both break. `cores/` importing `terrarium.config` is the canary.
- **Extrapolation in the built-up core.** The tile has few high-canopy dense-urban
  analogues, which is exactly where the demo intervention lands.
- **Free-tier drift.** Rate limits and free tiers change without notice, and a dead free
  tier the night before a demo is a real failure mode. Mitigations: keep the LLM behind
  one adapter (D13), keep a local Ollama fallback in mind for Phase 10, and never make a
  live third-party call part of the *core* demo path — precompute where you can.

## Immediate next actions

1. ~~**Rewrite `CLAUDE.md`**~~ ✅ done. It now carries the correct core signature, the
   D9 temperature-labelling rule, the D13 zero-budget constraint, and phase-gated scope
   instead of a permanent "not in v1" list. Phase status is deliberately *not* duplicated
   there — this file is the single source for it, because a duplicated status goes stale.
2. ~~**Write the `/simulate` HTTP schema**~~ ✅ done. `api/schemas/simulate.py` holds
   `SimulateRequest`/`SimulateResponse`, and `api/geometry.py` owns GeoJSON → mask as
   D6 requires. `CoreResult` stayed frozen — the route recomputes the capped canopy
   fraction for its context block rather than widening a contract shared with Track B.
3. ~~**Check whether per-window composites raise the contrast above 2.60 °C**~~ ✅ done.
   They do not — summer is 2.60 °C on 12 scenes, winter is 0.31 °C. **The pitch quotes
   2.60 °C and explains why**, rather than the literature's 3–6 °C.
4. **Rebuild the canonical cube with more years before Phase 7.** The known-good build is
   now **`data/processed/cube_phase4.zarr`** — `dc1af462b9c1`, the default
   `[2023, 2024]`, 4 windows, 484 s, 10/10 valid. That is what Phase 4 trained on and
   what Phase 5 should load. Hindcast change detection needs a longer archive still —
   budget ~120 s per window and go back as far as Landsat 8 allows.

   **`data/processed/cube.zarr` is a failed build, not the known-good one.** It has four
   time slices, but `ndvi`/`ndbi`/`albedo` are **entirely NaN in both 2024 windows** and
   `lst_c` is NaN in 2024-winter — the catalogue confirms build `04d9909de233` only ever
   wrote 2023's two windows. Training against it silently drops half the time axis and
   reports a degenerate leave-one-window-out (one season predicting the other). It is the
   flaky-network failure mode the ingest retries are meant to survive and did not.
   **A partially-built cube reads as valid to every consumer** — `select_window` returns
   a slice of NaN without complaint, and `build_training_frame` reports the dropped
   windows only as a NOTE. ~~Worth a per-window `validate_cube`~~ ✅ done in Phase 5:
   `state.cube.validate_windows` fails per variable-window, and the API refuses to serve
   a cube that trips it.

   Note that `build_tile.py` was never the hole here — it already reports per-window gaps
   and exits non-zero on them. This cube got onto disk by the build **dying partway**, so
   that check never ran. The lesson is that a cube's provenance cannot be trusted to a
   report that only prints on the happy path: validate the artefact when you *load* it,
   which is what the API now does.
5. **Winter Landsat is thin — but do *not* fix it by relaxing `max_cloud_cover`.**
   Measured, not assumed. Raising the ceiling from 20 % to 60 % buys about one extra
   clear look per pixel and costs **1.6–1.8 °C of cold bias**:

   | window | ceiling | scenes | depth min/median | composite median LST |
   |---|---|---|---|---|
   | 2024-winter | 20 % | 5 | 3 / 4 | 22.39 °C |
   | 2024-winter | 60 % | 7 | 4 / 5 | **20.83 °C** |
   | 2023-winter | 20 % | 3 | 0 / 3 | 24.26 °C |
   | 2023-winter | 60 % | 6 | 2 / 5 | **22.50 °C** |

   That is residual cloud and cloud shadow surviving the QA bitmask and dragging the
   median down — exactly the failure `_collapse_time`'s median is chosen to resist, and
   it swamps the depth gained. **Keep the 20 % ceiling.** The earlier suggestion here to
   relax it was written before this was measured and was wrong.

   The real fix was reporting, not filtering: `obs_depth_min` / `obs_depth_p50` now ride
   on every Landsat `SourceRecord` and into the catalogue, so "is this composite thin?"
   is a number rather than a guess. Winter's honest depth is **3–5 clear looks per
   pixel** — thinner than summer's 6+, but not the crisis "3 scenes" implied.

6. **A composite can report 100 % valid and still have holes, and one did.** 2023-winter
   `lst_c` has 9 pixels that no clear scene ever observed. `valid_fraction` of 99.978 %
   renders as `100.0%` at one decimal, so the build report showed a complete map — the
   same class of "200 OK is not it worked" failure as the other three above, this time
   in our own reporting. `VariableSummary.valid_text` now prints full coverage as `100%`
   with no decimal and escalates precision otherwise, and ingest logs a warning whenever
   `obs_depth_min` hits 0. Both signals fire on the current cube.
