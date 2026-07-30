# Terrarium — Implementation Plan

Written 2026-07-30 after a full read of the repository at commit `29bff69`.

Two halves:

- **Part A — what already exists**, verified by reading and running the code, not by
  trusting the README.
- **Part B — what to build next**, in the order that keeps a demo shippable at every
  step.

`CLAUDE.md` remains the authority on architecture and conventions. This file is the
authority on *sequence*. Where the two disagree, `CLAUDE.md` wins and this file is wrong.

---

## Part A — Current state

### A.1 Layer map

| Layer | Package | State | Notes |
|---|---|---|---|
| 1. State Cube | `ingest/`, `state/` | **Done for v1's six variables** | Real STAC ingest, aligned, persisted, catalogued, tested |
| 2. Physics Core | `cores/` | **Empty** | Two docstring-only `__init__.py`. No `base.py`, no thermal core |
| 3. Intelligence | `api/` | **Skeleton** | One endpoint (`/health`). No cube access, no simulate |
| Frontend | `web/` | **Skeleton** | Calls `/health`, renders a text panel. deck.gl + MapLibre installed but unused |

### A.2 Module-by-module

**`config.py` — complete.**
Lahore tile hardcoded at `[74.2533, 31.4305, 74.4641, 31.6103]`, EPSG:32643, 100 m.
Pydantic-settings with `TERRARIUM_` env prefix. Collection IDs and `NATIVE_RESOLUTION_M`
kept deliberately separate from `target_resolution_m`. Search window is a *single*
season: `2024-04-01 .. 2024-06-30`, cloud ≤ 20 %, ≤ 8 scenes per collection.
→ *Consequence for Layer 2, see B.1: the cube is a single-date snapshot, so it supports
spatial regression but not yet temporal training or hindcasting.*

**`state/grid.py` — complete.**
One canonical grid, derived from the tile. Projects all four bbox corners (not two) and
snaps outward to whole 100 m multiples, so repeated builds are byte-identical and the
grid never clips the requested extent. Pixel-centre coordinates, `y` descending.
Well covered by `test_grid.py`.

**`state/cube.py` — complete for the current variable set.**
Six variables declared with units, dtype, resampling policy, fill value and physical
`valid_range`: `lst_c`, `ndvi`, `ndbi`, `albedo`, `elevation_m`, `landcover`.
Resampling is a property of the variable's *meaning* (nearest for labels, bilinear for
measurements) and is declared once here — good. `empty_cube` / `validate_cube` /
`enforce_valid_range` / `summarise` all present.

**`ingest/client.py` — complete.**
The network boundary. PC STAC with `sign_inplace`, client-side cloud filter that
preserves the pre-filter count (so an empty cube is diagnosable), `select_clearest`
scene cap, GDAL tuning for remote COG reads, and a `GeoBox` built from the grid's own
snapped bounds so every source lands on identical coordinates.

**`ingest/pipeline.py` — complete, and the most carefully built file in the repo.**
Four ingestors. Each masks (SCL for S2, QA_PIXEL bitmask for Landsat), converts DNs to
physical units, composites with a median over `solar_day` groups, and returns arrays
keyed by cube variable name. Notable correctness work already done and regression-tested:

- Sentinel-2 baseline 04.00+ `-1000 DN` offset inferred from item metadata, not the date.
- Reflectance screened *after* the offset, because screening the raw DN let NDVI reach
  −2.36 on the first real build.
- WorldCover restricted to a single epoch — a median across v100/v200 would invent
  class codes that were never observed.
- Per-source retry with exponential backoff, and materialisation of the lazy dask arrays
  *inside* the guard, so one dead collection cannot kill the whole build.

**`state/store.py` — complete.**
Zarr (`mode="w"`, total and idempotent) plus a DuckDB catalogue of build provenance:
three separate scene counts per collection (`n_found` / `n_kept` / `n_composited`) so a
thin variable can be diagnosed as bad weather versus an over-tight cap.

**`api/` — skeleton.**
`create_app()` factory, CORS from settings, one router. `/health` returns the active
tile so the frontend never duplicates the bbox in JavaScript.

**`scripts/` — three working CLIs.**
`build_tile.py` (full build with a report; exits non-zero if any variable is empty),
`inspect_cube.py` (read-back summary + landcover composition + latest catalogued build),
`preview_cube.py` (PNG renders on lat/lon axes with landmarks, for eyeballing alignment).

**`web/` — skeleton.**
React 19 + Vite 8. `api/client.ts` mirrors the Pydantic models by hand. `App.tsx` shows
the API connection state and tile details. deck.gl 9 and MapLibre 6 are installed but
not imported anywhere yet.

**Tests — 50, all offline, ~5 s.**
`test_grid.py`, `test_config.py`, `test_health.py`, and a substantial `test_pipeline.py`
that fakes STAC search and asset loading, then asserts on the parts we actually own:
resampling method *per band*, cloud masking, DN→physical conversion, scene capping,
retry/backoff, failure isolation, and physical plausibility.

### A.3 Defects found and fixed in this pass

| # | Where | Defect | Fix |
|---|---|---|---|
| 1 | `api/main.py` | `create_app(settings=…)` documented as "accepts injected settings so tests can override configuration", but routes resolve `Depends(get_settings)` — the global cached singleton. Injection reached CORS only; every route silently ignored it. | Register a `dependency_overrides` entry for `get_settings` when settings are injected. |
| 2 | `ingest/pipeline.py` | Broadband albedo used Liang's narrowband→broadband coefficients without his `/1.016` normalisation, biasing albedo ~1.6 % high across the whole tile. | Divide by `LIANG_NORMALISATION = 1.016`. |
| 3 | `ingest/pipeline.py` vs `state/cube.py` | Same quantity attributed to two different papers ("Bonafoni & Sekertekin (2020)" vs "Liang (2001)"). The coefficients are Liang's; Bonafoni & Sekertekin is the Sentinel-2 validation. | One consistent citation naming both roles. |
| 4 | `scripts/inspect_cube.py` | `duration_s` is nullable in the schema but formatted with `:.1f`; a catalogued build without a duration crashes the inspector. | Format defensively. |
| 5 | `ingest/client.py` | `open_catalog` was the only network call **outside** the retry logic. Every ingestor retries with backoff, but the catalogue open that precedes them did not — so a DNS blip at second zero aborted the build with a raw traceback and no report. `NOTES.md` documents this machine's DNS as intermittently flaky, which is exactly the failure this leaves unguarded. Found by an actual failed build, not by reading. | Same attempt count and exponential backoff as the ingestors. Re-raises when exhausted: unlike a source, a missing catalogue cannot be skipped. |

Three housekeeping gaps closed alongside them:

- `web/README.md` was the **unmodified Vite starter README** ("This template provides a
  minimal setup…") — replaced with actual project instructions.
- `web/tsconfig.app.json` had no `"strict"`. The Python side targets `mypy --strict`
  while TypeScript was silently permissive, and `src/api/client.ts` is a *hand-mirrored*
  copy of the Pydantic models — exactly where an unnoticed `any` bites. Enabled; the
  build passes with no source changes needed.
- `NOTES.md`'s open TODO (stale `elevation_m` attrs in the on-disk Zarr) is resolved —
  see A.5.

**Reconciliation with the earlier hand-written status note.** That note credits two bugs
fixed to date: the NDVI-goes-negative maths error and the mislabelled elevation variable.
Both are real and both are in git history — but the count is now **seven**: those two,
plus the five in the table above. Any status summary written before this audit
understates what has been fixed.

Nothing else found. In particular these were checked and are **correct** as written:
Landsat ST scaling (`0.00341802 / 149.0`), QA_PIXEL bits 0–4, the S2 baseline offset
inference, WorldCover epoch selection, grid snapping direction, `enforce_valid_range`'s
NaN accounting, and the retry backoff sequence.

### A.4 Independent visual verification — already done, and it counts as a result

Before this audit, the cube was checked *visually* against a real map of Lahore via
`preview_cube.py`, not merely checked for "ran without crashing". Confirmed:

- The **airport runway** appears exactly where it should.
- The **River Ravi** appears in the right place, and **three independent measurements
  agree on it** (land cover, NDVI, LST) — a strong alignment check, because a
  reprojection bug would desynchronise them.
- The **Lahore Canal** reads as a cool, green strip along its true course.
- Dense built-up areas are hot and parks/water are cool — the expected inverse pattern.

This is cross-layer alignment evidence, which is exactly what `state/`'s contract
promises and what `validate_cube` can only partially prove. **Keep the PNGs and put the
three-layer river agreement in the submission** — it is cheap credibility and it is
already earned. It does not replace the Phase 4 hindcast; it is a different claim
("the mirror is aligned") from the hindcast's claim ("the physics predicts").

### A.5 Known non-defects worth remembering

- **A full build was run as part of this audit** and the cube is now on disk
  (`build e6e3c768f392`, 116 s, 6/6 variables at 100 % valid pixels):

  | | min | mean | max |
  |---|---|---|---|
  | `lst_c` (°C) | 29.31 | 46.57 | 53.53 |
  | `ndvi` | −0.458 | 0.220 | 0.779 |
  | `ndbi` | −0.543 | 0.027 | 0.261 |
  | `albedo` | 0.024 | 0.187 | 0.401 |
  | `elevation_m` | 198.1 | 215.9 | 239.9 |

  Land cover: 73.9 % built-up, 9.8 % cropland, 6.8 % tree cover, 6.7 % grassland,
  0.5 % permanent water. Elevation centres on ~216 m, which is Lahore's actual height.
  Every range is physically sane — no scaling or masking bug is hiding in the current
  cube. Scene accounting: S2 74 found → 49 cloud-ok → 8 composited; Landsat 15 → 12 → 8.

- **Only 8 of 49 usable Sentinel-2 scenes are composited** (`max_scenes_per_collection`).
  That is a deliberate speed/SAS-token-expiry trade, not a bug — but it is worth
  revisiting once the cube gains a time dimension in Phase 4.
- `geopandas`, `shapely`, `numba`, `scikit-learn` are declared but not yet imported.
  They are for Layer 2 work; leave them.

---

## Part B — What to build

### B.0 The scope tension, resolved

`CLAUDE.md` says v1 is **one tile, one core, one intervention**, and explicitly forbids
scaffolding agents, extra cores, voice, or VLM. The project description wants all of
them, and names the equity panel and the hindcast number as the two moments that win.

These are not in conflict if you read `CLAUDE.md` as *ordering*, not as a ceiling:

> Ship the thermal vertical slice end-to-end first. Add nothing horizontally until a
> user can draw a polygon and see a cooling map.

So: **Phases 1–4 below are the v1 `CLAUDE.md` sanctions.** Phases 5–8 are the rest of
the hackathon description, and each one is only started when the phase before it is
demo-able. Do not create placeholder modules for Phase 6+ while working on Phase 1 —
that is the scope violation `CLAUDE.md` is warning about.

---

### Phase 1 — Thermal core *(never cut; this is the demo)*

**Goal:** `core(baseline_cube, intervention, model) -> result_cube` for tree planting,
pure, offline, sub-second.

#### 1.0 Four decisions to settle before writing code

These came out of reviewing a proposed Phase-1 spec against this plan. Settle them
first; each one is cheap now and expensive after the code exists.

**(i) File layout: `cores/thermal/` package, not a single `cores/thermal.py`.**
The hackathon description's architecture sketch shows a flat `cores/thermal.py`, and that
is where the confusion comes from — but `CLAUDE.md` explicitly specifies the split
(`features.py` / `model.py` / `simulate.py` under `cores/thermal/`), and
`cores/thermal/__init__.py` already exists as a package with its docstring. `CLAUDE.md`
wins. Going flat is a legitimate choice, but it is a `CLAUDE.md` edit, not a silent
deviation — the whole point of that file is that it does not drift.

**(ii) The trained model is an *argument*, never trained or loaded inside the core.**
A signature of `simulate(cube, intervention)` forces the core to either train on every
call (seconds, not milliseconds — the interactivity claim dies) or read a file (impure —
the rule `CLAUDE.md` calls "the single most important rule in the codebase"). Load the
artefact in `api/` or in the script and pass it down.

**(iii) Return the ΔLST field over the *whole tile*, not just the intervention region.**
This is not pedantry. Once neighbourhood features are in play (1b), planting inside
region R changes the neighbourhood statistics of cells *outside* R, so ΔLST is genuinely
non-zero beyond the polygon. That spillover is real physics — cooling does not stop at a
property line — and it is one of the more convincing things on screen. Clipping the
output to R throws it away and quietly makes the model look worse than it is.

**(iv) The intervention's input is a canopy fraction, not a tree count.**
`+0.30 canopy fraction over this region` is the core's unit. Converting *"5,000 trees of
species X at 6 m spacing"* into a fraction needs crown-area and survival-rate constants,
which are cost-library facts, not physics — they belong in the DSL / API layer (Phase 7),
where the planner agent also does its "you cannot fit 5,000 trees in 0.3 km²" check.
Keeping them out of the core keeps the core free of species tables *and* portable to
another city. This is a correction to an earlier draft of 1d, which had the count
conversion inside the core.

#### 1a. `cores/base.py` — the Core protocol

```python
class Intervention(BaseModel):      # frozen
    ...
class CoreResult(BaseModel):        # frozen; carries the delta cube + scalar summary
    ...
class Core(Protocol):
    def __call__(
        self, cube: xr.Dataset, intervention: Intervention, model: ThermalModel
    ) -> CoreResult: ...
```

One protocol, one implementation, because the API needs a stable return contract — not
because a second core exists yet.

#### 1b. `cores/thermal/features.py` — cube → feature matrix

`build_features(cube) -> tuple[pd.DataFrame, np.ndarray]` returning one row per pixel.

Feature set for v1:

| Feature | Source | Why |
|---|---|---|
| `ndvi`, `ndbi`, `albedo`, `elevation_m` | cube, direct | the continuous predictors |
| `landcover` one-hot (or as a LightGBM categorical) | cube | class label, never averaged |
| `ndvi_mean_500m`, `ndbi_mean_500m` | 5×5 uniform filter | LST at a pixel is driven by its *neighbourhood*, not just itself — this is the single highest-value engineered feature and it is cheap |

**Deliberately excluded from v1: meteorology.** The cube is one seasonal composite, so
wind and air temperature are constant across all 40,000 pixels and carry exactly zero
information. Add them the day the cube gains a time dimension (Phase 4), not before.

Rows where `lst_c` is NaN are dropped for training but kept for prediction.

#### 1c. `cores/thermal/model.py` — train / predict

`train(features, target) -> ThermalModel` and `ThermalModel.predict(features)`.

**Validation must be spatially blocked, not random k-fold.** Neighbouring 100 m pixels
are strongly autocorrelated; random pixel CV leaks the answer across the split and will
report a flatteringly small MAE that means nothing.

**Fold over many blocks — do not hold out one.** Split the tile into a checkerboard of
~2 km blocks and run *k-fold over the blocks* (5 folds), reporting mean MAE and the
spread across folds. A single held-out block is one draw from a high-variance
distribution: the tile is 74 % built-up but also contains the Ravi, the canal, cropland
and the airport, so one block can land almost entirely on water and return a number that
says nothing about the model. The spread across folds is itself a result — it is the
honest uncertainty band the explainer agent is later required to quote.

**Label this validation as a placeholder, explicitly and on screen.** It answers *"can
the model predict LST somewhere it has not seen?"* — a spatial-generalisation question.
It does **not** answer *"can the model predict what happens after a change?"*, which is
the causal question the product actually makes claims about. Only the Phase 4 hindcast
answers that, and it needs multi-date history the cube does not yet have. Stating this
distinction plainly is worth more than a good MAE; conflating them is the single easiest
thing for a judge to catch.

**The assumption this rests on, which must be said out loud: space-for-time
substitution.** A single-date composite can only teach the model a *cross-sectional*
relationship — why this pixel is hotter than that one. Using it to predict an
intervention assumes that contrast between neighbouring places equals the effect of
changing one place. That is a standard, defensible move in urban remote sensing, and it
is also the model's biggest soft spot. Put it in the limitations section rather than
waiting to be asked.

Model artefact is saved outside the core (`data/processed/thermal.txt`) and *passed in*;
the core never reads a file.

**Watch for double-counting between `landcover` and `ndvi`.** If the intervention flips
cells to class 10 *and* raises NDVI, and the model learned "class 10 is cool" largely
because class-10 pixels have high NDVI, the same cooling gets counted twice. Check the
feature importances after the first train; if `landcover` and `ndvi` are both dominant,
prefer perturbing the continuous features only and leaving the class code alone.

#### 1d. `cores/thermal/simulate.py` — apply intervention, return delta

The honest hard part is turning *"more canopy here"* into a feature perturbation.
Recommended approach, because it invents no constants:

1. Take **added canopy fraction** `f` and a region as the intervention's input — see
   1.0(iv); the tree-count conversion lives in the DSL layer, not here. Cap `f` at the
   remaining plantable fraction (already-canopy and open-water cells cannot absorb more).
2. Compute the **observed median feature vector of `landcover == 10` (tree cover) pixels
   within this same tile**.
3. Move each affected cell's features fraction `f` of the way toward that vector.

The claim then becomes *"this cell now looks like a canopy fraction f mixture of what it
was and what tree cover in Lahore actually measures"* — empirical, defensible, and it
degrades gracefully when the tile changes city.

4. **Recompute the neighbourhood features from the perturbed field**, not from the
   baseline. This is the step that produces the spillover in 1.0(iii), and it is easy to
   forget because the per-pixel features are perturbed directly.
5. Re-run `predict` on the perturbed features → `lst_scenario`.
6. `delta = lst_scenario - lst_baseline` **over the whole tile**, returned as a cube
   variable plus a summary (mean ΔLST inside the region, mean outside, max ΔLST, area
   cooled > 0.5 °C).

**Compare against the model's own baseline prediction, not the observed `lst_c`.**
`delta` must be `predict(perturbed) - predict(baseline)`. Differencing against observed
LST folds the model's residual error into every scenario, so an untouched pixel would
show a spurious ΔLST equal to its residual. Differencing two predictions cancels that
error and leaves only the intervention's effect.

#### 1e. `scripts/train_thermal.py` — the runnable entry point

The one place that is allowed to do I/O around the core. Mirrors `build_tile.py`'s shape:
a printed report, non-zero exit on anything unusable.

1. Load the cube from Zarr (`state.store.open_cube`).
2. Build features, train, and write the artefact to `data/processed/thermal.txt`.
3. Run the blocked spatial CV and print **mean MAE ± spread across folds**, alongside a
   naive baseline (predict the tile mean) — an MAE only means something next to the
   number it has to beat.
4. Run one worked intervention — *+30 % canopy fraction in the built-up core near the
   canal* — and print mean/max ΔLST inside the region, mean ΔLST outside it (the
   spillover), and area cooled > 0.5 °C.

**Report those numbers and stop there.** They decide whether the feature set is adequate
before any API or map work is built on top of it. Two specific things to sanity-check
before trusting the output:

- **ΔLST must be negative** in the planted region. A positive number means the
  perturbation direction is wrong (likely an NDBI sign) — not a "surprising finding".
- **Magnitude should land in roughly −1 to −4 °C** for a 30 % canopy increase. Much more
  than that and the model is extrapolating outside its training distribution; the
  built-up core has few high-canopy analogues, which is exactly where a
  gradient-boosted model fails silently rather than loudly.

**Acceptance:** a pytest in `cores/thermal/test_simulate.py` builds a synthetic cube in
memory, plants trees in a hot built-up patch, and asserts ΔLST is negative there and
~zero far away. No network, no files.

---

### Phase 2 — API: expose the cube and the simulation

- `GET /cube/summary` — the `CubeSummary` already produced by `state.cube.summarise`.
- `GET /cube/layer/{name}` — one variable as a compact array for deck.gl. Return
  `{bounds, width, height, values}` with values as float32 base64 or a PNG-encoded
  texture; do **not** ship 40,000 GeoJSON features.
- `POST /simulate` — `{intervention}` → `{baseline_stats, scenario_stats, delta_layer}`.

Cube and model load **once at startup** into app state. Routes stay thin: parse,
delegate to the core, serialise. All contracts in `api/schemas/`.

**Acceptance:** `/simulate` returns in < 3 s warm, and `cores/` still contains no `open`,
`requests`, or `xr.open_zarr`.

---

### Phase 3 — Frontend: the map

deck.gl and MapLibre are already installed and unused. Wire them:

- MapLibre basemap centred on `tile.centroid` from `/health`.
- A `BitmapLayer` (not a grid of polygons) for the LST raster.
- Polygon draw → intervention JSON → `POST /simulate`.
- Split-screen or swipe compare of baseline vs scenario, plus a diverging ΔLST ramp.

**Acceptance:** draw, submit, see the cooling map — no page reload.

---

### Phase 4 — Hindcast validation *(never cut; this is the credibility)*

Requires the cube to gain a **time dimension**, which is the one real Layer-1 change
still outstanding:

- Widen `config.search_start/end` into a list of yearly seasonal windows.
- Add `time` to the grid contract and to `validate_cube`.
- Store per-year composites rather than one.

Then: pick a real Lahore land-cover change with a known date, train strictly on data
before it, predict the post-change field, compare to observed Landsat ST_B10. Report MAE
and spatial R². Put the honest number in the UI and the README, including if it is bad.

Once `time` exists, meteorology (Open-Meteo, keyless) becomes a *useful* feature and
should be added to the cube and to `features.py`.

---

### Phase 5 — Equity *(never cut; this is what wins the room)*

Needs one new cube variable: **population** (WorldPop 100 m, or GHSL). Add it as a
`VariableSpec` with bilinear resampling; the ingest pattern is already established.

Then a small pure function — not yet a full core:
`benefit_distribution(delta_lst, population, deprivation_proxy) -> deciles`.
Deprivation proxy for v1: building density from the existing `ndbi` plus nightlights if
cheap; do not build an informal-settlement classifier.

Output: share of total cooling person-degrees accruing to each population decile, and
the flag when the top three deciles capture the majority.

---

### Phase 6 — Second core

Pick **one**. Air dispersion is the better demo (winter smog is the Lahore story);
hydrology is the easier physics. Do not start both.

New Layer-1 inputs required either way — OSM roads/kilns via `osmnx` for air, soil group
and flow accumulation for water. That is a real ingest expansion, so budget for it
honestly rather than assuming the cube already has what the core needs.

---

### Phase 7 — Agent layer

`dsl/` (Pydantic intervention schema + validators + costed library), then a LangGraph
planner that emits validated DSL and an explainer that renders results into a brief.
Cores are already pure functions, so exposing them as typed tool-calls is mechanical.

Fallback if time runs out: preset scenario buttons that emit the same DSL. The DSL is
what matters; the LLM is the nicer front door onto it.

---

### Phase 8 — Voice, PDF brief, VLM

First things to drop. Only start if Phases 1–5 are demo-able and rehearsed.

---

### B.1 Cut order, worst case

If everything goes wrong, ship in this order and stop wherever the clock stops:

1. One tile (**done**)
2. Thermal core + a tree-planting delta (Phase 1)
3. Map showing the delta (Phases 2–3)
4. The hindcast MAE (Phase 4)
5. The equity panel (Phase 5)

That alone is a complete, defensible submission. Everything after it is upside.

### B.2 Standing risks

- **Single-date cube.** Phases 4 and 5 both quietly depend on the time dimension.
  It is the biggest hidden dependency in the plan; do it before it blocks two phases.
- **Build fragility.** PC drops connections; the retry logic handles it but a build
  still needs a clean run. Build early, keep the Zarr, do not rebuild the night before.
- **Intervention → feature mapping** is where a reviewer will push hardest. The
  "move toward observed tree-cover pixels" method in 1d is the answer; state it on screen.
- **Purity drift.** The moment a core reads config or opens a file, the API caching and
  the offline tests both break. `cores/` importing anything from `terrarium.config` is
  the canary.
