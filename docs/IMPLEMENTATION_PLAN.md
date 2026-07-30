# Terrarium — Implementation Plan

Status as of **2026-07-31**. Rewritten after a full repository audit and a decision pass
with the team.

| | Phase | Status |
|---|---|---|
| 0 | Foundations — repo, tooling, API skeleton, frontend skeleton | ✅ **DONE** |
| 1 | State Cube v1 — six variables, one date, Lahore | ✅ **DONE** |
| 2 | Thermal core — LightGBM ΔLST emulator | ✅ **DONE** |
| 3 | State Cube v2 — time dimension + winter windows | ▶ **NEXT** |
| 4 | Thermal core v2 — multi-date retrain + meteorology | ⬜ |
| 5 | API — expose cube and simulation | ⬜ |
| 6 | Frontend — map, draw, compare | ⬜ |
| 7 | Hindcast validation | ⬜ |
| 8 | Equity | ⬜ |
| 9 | Air dispersion core | ⬜ |
| 10 | DSL + agent layer | ⬜ |
| 11 | Voice, VLM, council brief | ⬜ |
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
| OpenStreetMap / Overpass (Phase 9) | Free |
| OpenAQ (air validation, Phase 9) | Free |
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

## Phase 3 — State Cube v2: time + seasons ▶ NEXT

The biggest hidden dependency in the plan: Phases 4, 7, 8 and 9 all need it.

- Widen `search_start/end` into a list of per-year seasonal windows.
- **Summer (Apr–Jun) and winter (Nov–Jan)** per year (D10) — winter now, so the air core
  never forces a second ingest expansion.
- Add `time` to the grid contract, `empty_cube`, and `validate_cube`.
- Store per-window composites rather than one.
- Add **meteorology** (Open-Meteo, keyless) and **population** (WorldPop 100 m).

Revisit `max_scenes_per_collection` here — only 8 of 49 usable Sentinel-2 scenes are
currently composited. That was a deliberate speed/token-expiry trade, worth re-tuning
once builds are per-window.

## Phase 4 — Thermal core v2 ⬜

Retrain on multi-date. Meteorology becomes a real feature. Re-run the blocked CV and
compare against the Phase 2 number — the delta between them tells you how much the
single-date model was leaning on cross-sectional contrast alone.

## Phase 5 — API ⬜

- `GET /cube/summary` — reuse `state.cube.summarise`.
- `GET /cube/layer/{name}` — one variable as a bitmap or base64 float32 array with bounds.
  **Not** 40,602 GeoJSON features.
- `POST /simulate` — GeoJSON polygon → boolean mask (D6) → core → delta layer + stats.

Cube and model load **once at startup**. Routes stay thin. Target < 3 s warm.

## Phase 6 — Frontend map ⬜

MapLibre basemap centred from `/health`, deck.gl `BitmapLayer` for the raster, polygon
draw → `POST /simulate`, split-screen or swipe compare, diverging ΔLST ramp.

**Basemap tiles: OpenFreeMap Positron**, not MapTiler or Stadia — see the cost register.
The tile service is a separate thing from the MapLibre library and is the one place in
the frontend where a key would silently creep in.

## Phase 7 — Hindcast validation ⬜

**The credibility weapon.** Needs Phase 3.

Since no target site is known (D7), start with **change detection**: scan the archive for
cells with large sustained NDVI or land-cover transitions between years, rank candidates,
pick one big enough to resolve at 100 m. Then train strictly on data *before* the change,
predict the post-change field, and compare to observed Landsat ST_B10. Report MAE and
spatial R² — including if they are bad.

## Phase 8 — Equity ⬜

Needs population from Phase 3. A pure function, not yet a full core:
`benefit_distribution(delta_lst, population, deprivation_proxy) -> deciles`.

Output: share of total cooling person-degrees per population decile, and a flag when the
top three deciles capture the majority. This is the panel that critiques the user's own
plan — the moment the description says wins the room.

## Phase 9 — Air dispersion core ⬜

Gaussian puff on the 100 m grid, winter inversion parameterisation, emission inventory
from OSM (road class × fleet mix, kiln points), canopy-weighted deposition. Needs an OSM
ingest expansion (`osmnx`) — budget for it honestly rather than assuming the cube has it.

Validation: leave-one-station-out against OpenAQ.

## Phase 10 — DSL + agent layer ⬜

`dsl/` first: Pydantic intervention schema, validators, costed intervention library. This
is where tree count → canopy fraction lives, and where "you cannot plant 5,000 trees in
0.3 km²" gets rejected.

Then LangGraph planner (NL → validated DSL) and explainer (tensors → brief with
uncertainty). Cores are already pure functions, so exposing them as typed tool-calls is
mechanical. **Fallback:** preset scenario buttons emitting the same DSL — the DSL is what
matters, the LLM is a nicer front door onto it.

**Free tier: Google Gemini via AI Studio** (D13). Keep the provider behind one adapter so
it can be swapped if credits appear. The DSL and validators are provider-independent by
construction and are the part worth building carefully.

## Phase 11 — Voice, VLM, council brief ⬜

Citizen-photo VLM writing observations back into the cube — **reuse the Phase 10 Gemini
key**, its free tier is multimodal, so this costs nothing extra.

Voice in English and Urdu. LiveKit Cloud meters minutes, so prefer the free path:
**browser Web Speech API** for capture, or self-hosted **Whisper** for transcription.
Verify Urdu quality early — it is weaker than English in every engine, and this is the
phase most likely to be dropped anyway.

Council brief: render to PDF locally (WeasyPrint or the browser's print-to-PDF). No
service required.

## Phase 12 — Deployment ⬜

**Hugging Face Spaces** (CPU Basic, no card) for the API, **Vercel** or **Cloudflare
Pages** for the web. Not Modal or Fly.io — neither is reliably free (D13). The cube is
~1 MB, so it ships inside the container rather than needing object storage. Last, by
decision (D12) — never blocks physics work.

---

## Tracks — two people (D2)

| | Track A — data & physics | Track B — product & interface |
|---|---|---|
| now | **Phase 2** thermal core | **Phase 5** API skeleton against a stubbed core |
| then | Phase 3 cube v2 → Phase 4 retrain | Phase 6 map, drawing, compare UI |
| then | Phase 7 hindcast → Phase 9 air core | Phase 8 equity panel → Phase 10 DSL |

**Freeze the `CoreResult` and `/simulate` contracts first** — that is the only interface
between the tracks. Once both sides agree on the shape, they can proceed independently
and integrate late without a merge fight.

## Cut order, worst case

1. One tile ✅
2. Thermal core + tree-planting delta
3. Map showing the delta
4. The hindcast number
5. The equity panel

That alone is a complete, defensible submission. Everything after is upside.

## Standing risks

- **Space-for-time substitution.** A single-date cube teaches only *why this pixel is
  hotter than that one*. Using it for interventions assumes contrast-between-places
  equals effect-of-changing-a-place. Standard and defensible, but it is the model's
  softest spot — put it in limitations before anyone asks. Phase 3 partly relieves it.
- **Phase 3 blocks four later phases.** Do not let it drift.
- **Build fragility.** Planetary Computer drops connections; retries handle it, but keep
  a known-good Zarr and never rebuild the night before a demo.
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
2. `CoreResult` is frozen in `cores/base.py` — Track B can build `/simulate` against it.
   `Intervention.mask` is `(y, x)` bool on the canonical grid; the API owns
   GeoJSON → mask. **The `/simulate` HTTP schema itself is still unwritten** — that is
   Track B's first Phase 5 task, and it is the remaining half of this action.
3. Phase 3, and while there **check whether per-window composites raise the tree-vs-built
   LST contrast above 2.60 °C**. If the contrast stays this low, say so in the pitch
   rather than quoting literature numbers the data does not support.
