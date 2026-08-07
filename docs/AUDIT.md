# Terrarium — repository audit

**Produced 2026-08-06, immediately after Phase 11 was marked done** (`d2f46f2`).
**Re-checked and extended 2026-08-07**, when re-running the open findings showed that two
of them (A11, A18) did not say what was actually true, and reading the code added four more
(A23–A26). Phases 0–11 complete, Phase 12 untouched.

This is a **snapshot, not a register.** [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
remains the roadmap and the decisions register; `CLAUDE.md` remains the operating rules.
This file records what is broken, missing, or invisible *right now*, with the evidence that
established it and the command that verifies each fix. Findings get closed by editing this
file, and the file gets deleted when it is empty — a stale audit is worse than none,
because it invites people to re-investigate things that were fixed in the meantime.

Every finding below was established by running something, not by reading a doc. The
commands are included so each one can be re-checked rather than believed.

---

## Summary

| # | Finding | Severity | Area | Effort |
|---|---|---|---|---|
| ~~A1~~ | ~~No servable cube exists — the API cannot serve `/cube/*` or `/simulate`~~ | **CLOSED** | data | fixed 2026-08-06 |
| ~~A2~~ | ~~The running dev server is a pre-Phase-10 build~~ | **CLOSED** | ops | fixed 2026-08-06 |
| ~~A3~~ | ~~The air core has no UI at all — Phase 9 is invisible~~ | **CLOSED** | web | fixed 2026-08-06 |
| ~~A4~~ | ~~`api.observationLayer()` is dead code; the citizen raster is never drawn~~ | **CLOSED** | web | fixed 2026-08-06 |
| ~~A5~~ | ~~No emission-fraction control outside the presets~~ | **CLOSED** | web | fixed 2026-08-06 |
| ~~A6~~ | ~~The photo location has no marker on the map~~ | **CLOSED** | web | fixed 2026-08-06 |
| ~~A7~~ | ~~Static cube variables are never checked at startup~~ | **CLOSED** | api | fixed 2026-08-06 |
| ~~A8~~ | ~~A cube refused for a missing variable is told to do a full rebuild~~ | **CLOSED** | state | fixed 2026-08-06 |
| **A9** | OpenAQ calibration has never run — air magnitudes uncalibrated | **P3** | validation | needs a free key |
| **A10** | Gemini has never run — planner LLM path and the whole photo path unproven | **P3** | validation | needs a free key |
| ~~A11~~ | ~~The hindcast result cannot be reproduced: its cube is gone~~ | **CLOSED** | validation | never true — fixed 2026-08-07 |
| **A12** | Voice has no automated test | **P3** | web | accepted, see below |
| ~~A13~~ | ~~No CI~~ | **CLOSED** | repo | fixed 2026-08-06 |
| ~~A14~~ | ~~README describes a thermal-only product that stopped existing at Phase 8~~ | **CLOSED** | docs | fixed 2026-08-06 |
| ~~A15~~ | ~~`NOTES.md`'s open TODO is two phases stale~~ | **CLOSED** | docs | fixed 2026-08-06 |
| ~~A16~~ | ~~Plan header still dated 2026-07-31~~ | **CLOSED** | docs | fixed 2026-08-06 |
| ~~A17~~ | ~~`requirements.txt` is an unmaintained second source of truth~~ | **CLOSED** | repo | fixed 2026-08-06 |
| ~~A18~~ | ~~The hand-mirrored TypeScript types have already drifted once~~ | **CLOSED** | web | fixed 2026-08-07 |
| ~~A19~~ | ~~`_plan_name` calls a do-nothing request a planting~~ | **CLOSED** | api | fixed 2026-08-06 |
| ~~A20~~ | ~~`ponytail:` marker in `cores/air.py`~~ | **CLOSED** | cores | fixed 2026-08-06 |
| ~~A21~~ | ~~No rate limiting on `POST /observations`~~ | **CLOSED** | api | fixed 2026-08-07 |
| **A22** | `scripts/` has no tests | **P5** | tests | accepted, see below |
| ~~A23~~ | ~~`cell_from_lonlat` accepted a coordinate it then mapped out of the grid~~ | **CLOSED** | api | fixed 2026-08-07 |
| ~~A24~~ | ~~CI claimed `tsc -b` guarded A18; it cannot~~ | **CLOSED** | repo | fixed 2026-08-07 |
| ~~A25~~ | ~~`leave_one_station_out` fits a degenerate fold and reports it as skill~~ | **CLOSED** | cores | fixed 2026-08-07 |
| ~~A26~~ | ~~The "< 3 s warm" claim was unverified~~ | **CLOSED** | api | measured 2026-08-07 |

Severities: **P0** the demo does not run · **P1** shipped work the user cannot see ·
**P2** a correctness hole with no symptom yet · **P3** claims resting on unrun validation ·
**P4** repo and documentation hygiene · **P5** small, real, cheap.

**Twenty-two of the twenty-six are now closed** — A1–A8, A13–A17, A19, A20 on 2026-08-06,
and A11, A18, A21, A23–A26 on 2026-08-07. What is left is four findings, none of which is
code:

- **A9, A10** — blocked on a free, no-card registration rather than on code. Until they
  run, the air magnitudes are uncalibrated and the whole photo path is built but unproven.
  Both are disclosed everywhere they matter; closing them means running what already
  exists. **These are the only two open findings that change what the product may claim.**
- **A12, A22** — accepted rather than fixed, with the reasoning below.

### What the 2026-08-07 pass changed

It began as a re-check of the open items and found that **two of them were not what this
document said they were**:

- **A11 was never true.** `data/processed/cube_hindcast.zarr` is on disk with eighteen
  windows, nine of them summers, and `hindcast.py` runs against it today. The finding
  asserted the cube was gone without looking. See its entry for the reproduction.
- **A18's guard did not exist.** Both this document (A13) and `ci.yml` claimed `tsc -b`
  catches client/schema drift. It cannot — TypeScript is checked against TypeScript and
  never sees a Pydantic model — so the one finding CI was said to cover was the one thing
  it could not. Recorded separately as **A24**, because a check that is believed to exist
  is worse than one that is known to be missing.

**A23 and A25 were found by reading code rather than by re-running a claim**, and both are
latent defects in guards that already existed:

- `cell_from_lonlat` had its half-open bounds the wrong way round on the y axis, so it
  refused a point on the tile's northern edge and accepted one on the southern edge that it
  then mapped to row 201 of a 201-row grid.
- `leave_one_station_out` checked for a degenerate fit **once on the whole station set**
  while fitting **once per fold**, so a set with spread but a flat fold reported an invented
  scale factor as validated skill — the exact failure that guard's docstring describes.

Neither has fired. A23 needs a coordinate that projects exactly onto the tile boundary;
A25 needs the OpenAQ key A9 is waiting on, and would have fired the first time A9 closed,
silently, in the direction of looking better.

**A26** is the 2026-08-06 audit's own closing caveat, discharged: `POST /simulate` is
**0.84 s warm**, against a claimed budget of 3 s.

**Nothing in this document now stops the product working.** Verified end to end on
2026-08-07 against `cube_phase9.zarr`: all six GETs 200, `POST /simulate` 200 with `stats`,
`equity`, `delta`, `air` and `brief` populated (−0.086 °C, −0.578 µg/m³ at 2024-winter),
`POST /observations` 503 with its reason. 391 Python tests and 96 browser tests pass,
`ruff` and `mypy --strict` clean over 74 files, `oxlint` clean, `npm run build` succeeds,
and the full Python suite passes with **sockets blocked**.

**What this audit did not look at.** Artefacts on disk, startup paths, route wiring, the
API-to-frontend surface, docs against reality, and lint/type/test state — those were
checked by running them, and on 2026-08-07 the cores, the DSL validator and the geometry
module were read line by line as well. The "< 3 s warm" claim is now **measured** (A26).
Still **not** done: a physics review — the plan's measured results remain the only evidence
that the numbers are right, and A25 is a reminder that the *scoring* code around them is
worth reading too — and a full security review beyond the quota-spend hole A21 names.

---

# P0 — the demo could not run

## A1 — No servable cube exists — **CLOSED 2026-08-06**

`config.py:251` served `data/processed/cube_phase9.zarr`, which was not on disk, and
neither cube that *was* on disk passed startup validation — `cube_phase4.zarr` predated
`pm25_emission_g_s` and `wind_direction_deg`, and `cube.zarr` was a partial build with two
empty 2024 windows. `/cube/summary`, `/cube/layer/*` and `/simulate` all returned **503**
and the frontend booted to its error screen.

**Closed by grafting the Phase 9 air layers onto `cube_phase4.zarr`**, as the finding
prescribed — not a rebuild. `overpass-api.de` returned `HTTP 504: Gateway Timeout` again on
retry, so the mirror was used, which is a config change and not a code change:

```bash
TERRARIUM_OVERPASS_URL=https://overpass.kumi.systems/api/interpreter \
  uv run python scripts/build_air_layers.py --zarr data/processed/cube_phase4.zarr \
                                            --out  data/processed/cube_phase9.zarr
```

```
overpass returned 35355 elements
emission inventory: 35355 roads, 0 kilns, 37.776 g/s over the tile
pm25_emission_g_s: 37.776 g/s, 31,834 of 40,602 cells with a source
wind_direction_deg: 2023-summer 131 · 2023-winter 102 · 2024-summer 300 · 2024-winter 66
```

Checked before serving, per the `zarr_store` / `serve_zarr_store` split:
`inspect_cube.py --per-window` reports **all 12 variables at 100 % valid in all four
windows**. `serve_zarr_store` needed no edit — it already pointed here.

**Verification, both conditions met:**

```
load_runtime OK
/health 200 · /cube/summary 200 · windows ['2023-summer','2023-winter','2024-summer','2024-winter']
/cube/layer/lst_c?window=2024-summer 200
```

`POST /simulate` was exercised end-to-end as well (0.3 canopy, 0.5 emissions removed,
`2024-winter`): **200**, with `stats`, `equity`, `delta`, **`air`** and `brief` all
populated — −0.117 °C mean mid-morning LST inside the polygon and −0.467 µg/m³
locally-generated PM2.5, the latter still **uncalibrated** until A9 closes. The winter
cooling magnitude is consistent with the ~0.13 °C figure `CLAUDE.md` quotes for the season.

**One caveat worth carrying forward.** The kiln count is **0**. The tile-wide emission
total is roads only, which is what the mirror's response contained; whether that is a real
absence inside this bbox or a query/mirror difference was not established here.

## A2 — The running dev server is a stale build — **CLOSED 2026-08-06**

A `terrarium-api` process (PID 30224 at the time of writing) is serving a build from before
Phase 10. It answers `/health` and 404s `/plan` and `/observations`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health        # 200
curl -s http://127.0.0.1:8000/observations                                    # {"detail":"Not Found"}
```

A fresh app build mounts both routers — confirmed in-process, so this is a stale process and
not a wiring bug:

```
/health 200 · /plan/presets 200 · /observations 200 · /observations/layer 200
POST /observations 503 (no vision model configured — the expected answer without a key)
```

**Fix.** Restart it. Worth knowing *why* it went stale: `uvicorn --reload` watches the
source tree, but a process started before a module existed will not pick up a new router in
every case, and the failure is silent — a 404 that looks exactly like a typo'd URL.

**Closed, and the recorded diagnosis was wrong in a way worth keeping.** This was not a
`--reload` watcher failing to pick up a new router. It was **two processes bound to
127.0.0.1:8000 at once**, and Windows delivering the connections to the older one.

Killing PID 30224 did not stop the server. The tree survived it: PID 31420, started
`03/08 17:27`, kept the listening socket, and so did its child 28044. A fresh
`uv run terrarium-api` then **bound the same port successfully** — no "address in use"
anywhere — and logged not one request, because every connection went to the 03/08 process.
From outside it looked exactly like a stale build:

```
netstat -ano | grep :8000
  TCP  127.0.0.1:8000  LISTENING  31420   <- 03/08, serving
  TCP  127.0.0.1:8000  LISTENING  22168   <- today, serving nobody
```

Four things this cost, all of which would have cost the next person the same:

- **`Stop-Process -Force` on the root does not take the tree.** The survivors were
  `python.exe`, not `terrarium-api.exe`, so `Get-Process | where ProcessName -match
  terrarium` reported **0** while the server was still up and answering.
- **A successful bind is not an exclusive bind.** Without `SO_EXCLUSIVEADDRUSE` a second
  listener on Windows starts cleanly and silently loses. Trust the *request log*, not the
  startup banner: a server logging no requests while curl gets answers is not the server
  answering.
- **`/health` returning 200 proves nothing about which build is up.** Ask `/openapi.json`
  what it serves. The stale process declared four paths; the current one declares eight.
- The dead process also held `Scripts/terrarium-api.exe` open, so `uv run` failed with
  `os error 32` during its sync step. **`uv run --no-sync`** sidesteps that when the
  environment is already correct, which is what unblocked A1.

**Verified fixed:** `/health`, `/cube/summary`, `/cube/layer/*`, `/plan`, `/plan/presets`,
`/simulate`, `/observations` and `/observations/layer` all answer 200 from a single
listener, and `/openapi.json` lists all eight paths.

---

# P1 — shipped, paid for, and invisible — **ALL CLOSED 2026-08-06**

## A3 — The air core has no UI whatsoever — **CLOSED 2026-08-06**

```bash
grep -rn "\.air\|AirPanel\|pm25" web/src --include=*.tsx --include=*.ts | grep -v test
# (no output)
```

`POST /simulate` returns a complete `AirResponse` — ΔPM2.5 statistics, mixing height, wind
speed and direction, and a whole-tile base64 raster — and **nothing in the frontend reads
it.** There is no panel, no map overlay, no legend entry, and no view-switcher tab. The
emission inventory cannot be inspected either: `MAPPABLE` at [App.tsx:44](../web/src/App.tsx#L44)
lists `lst_c, ndvi, ndbi, albedo, elevation_m, population` and omits `pm25_emission_g_s`.

**Consequence.** Phase 9 — 36 tests, an FFT convolution over the whole tile, a winter
inversion parameterisation, the measured 6–7× seasonal factor and the worked low-emission
zone — is invisible in the product. A user can trigger it (the `low-emission-zone` preset
sets `emission_fraction_removed`, and the response carries the air block) and then sees
nothing change on screen, which is worse than not offering it: it reads as a broken button.

This is the largest gap between work done and value visible in the entire repository.

**Fix**, in the order that pays:

1. `web/src/panels/AirPanel.tsx`, mirroring `ResultPanel` — ΔPM2.5 inside, the 1 km ring,
   the mixing height, the wind, and the two caveats the API already carries in its schema
   (**locally-generated**, and **uncalibrated** until A9 closes).
2. A `ΔPM2.5` entry in the view switcher, reusing `DIVERGING` and the existing decode path.
   The raster arrives in the same `LayerResponse` shape the cube layers use, so
   `decodeLayer` and `colourise` need no changes.
3. `pm25_emission_g_s` added to `MAPPABLE`, so the inventory itself can be drawn.

**Closed.** All three parts, in the order the finding proposed:

1. **`web/src/panels/AirPanel.tsx`** — ΔPM2.5 inside, the downwind spillover, the mixing
   height, the wind (as a compass point, because "from 66°" is the one sign in this core
   nothing else would catch being wrong), and both caveats: **locally-generated** and
   **uncalibrated**. Nine tests assert the caveats specifically, since the whole risk of
   making this visible is that the number gets quoted without them.
2. **A `ΔPM2.5` entry in the view switcher**, reusing `DIVERGING` and the existing decode
   path unchanged — the raster arrives in the same `LayerResponse` shape, exactly as the
   finding predicted. The tab renders **only when `air` is non-null**, so a plan that never
   touched traffic does not get a button that paints an empty map.
3. **`pm25_emission_g_s` added to `MAPPABLE`**, with an `EMISSION` ramp rather than
   `NEUTRAL`: emissions are genuinely zero over 8,768 cells, and greyscale rendered those
   the same dark as the basemap's labels.

One behaviour change beyond the finding: a traffic-only plan now **opens on ΔPM2.5**
instead of ΔLST. It changes no temperature, so landing on the thermal view showed an empty
map for a run that worked — the same "reads as a broken button" failure, one step later.

**Verified fixed**, against the live server on `cube_phase9.zarr`, 80 % of traffic removed
over 12.64 km²:

```
2024-winter  -0.7298 ug m-3 inside · -0.3932 over 1,610 spillover cells · mixing height 250 m
2024-summer  -0.0770 ug m-3 inside ·                                      mixing height 800 m
raster: 162,408 bytes, base64:float32:little:row-major
```

## A4 — `api.observationLayer()` is dead code — **CLOSED 2026-08-06**

Defined at [client.ts:418](../web/src/api/client.ts#L418), called from nowhere:

```bash
grep -rn "observationLayer" web/src     # one hit: the definition
```

The server builds a citizen-severity raster on the canonical 201×202 grid — worst severity
per cell, NaN where nobody reported — and no client draws it. Phase 11's claim that
observations "land on the grid" is true of the API and only half true of the product: the
list renders, the map does not.

**Closed.** A `Citizen reports (not measured)` option in the base-layer picker, loading from
`/observations/layer`. The work was in the picker, as predicted — the renderer is untouched.

Two things kept deliberately separate rather than folded into `useCubeLayer`, both falling
out of D19:

- **Its own hook** (`hooks/useObservationLayer.ts`). Sharing the loader would be the first
  step towards sharing the cache, then the legend, then the question "what does the cube
  say".
- **Never cached, and keyed on the report count**, so a newly submitted photo redraws the
  map instead of sitting in the list while the raster still says nobody reported anything.
  A cube layer is immutable for a given (variable, window); this one is not.

The legend uses a fixed **1–5** severity domain rather than the observed range — stretching
it would render one mild report in the same red as a burning waste pile — and the hint says
`Not measured`, plus that an uncoloured cell is one **nobody photographed**, not one that is
fine. That NaN-versus-zero distinction is the store's, and it now survives to the screen.

## A5 — No emission-fraction control outside the presets — **CLOSED 2026-08-06**

**Closed.** A second slider beside the canopy one, carrying the schema's own hint: *1.0
means the traffic is gone, not electrified — brake, tyre and road wear are roughly half of
road PM2.5 and stay until the vehicles do*, and that at 0 the plan says nothing about
traffic and no air block comes back.

One bug found while wiring it, which the finding did not anticipate: `buildPlan` adopted
only `canopy_fraction_added` from a resolved plan. Now that the emission lever is on screen,
that left the slider reading 0 % while the button posted a plan removing 80 % of the
traffic. Both fractions are adopted.

## A6 — The photo location has no marker — **CLOSED 2026-08-06**

**Closed.** An amber `ScatterplotLayer` with a white ring, drawn last so it stays on top of
the overlay. Deliberately **not** the draw palette's cyan: the same click places either a
polygon vertex or a photo location depending on mode, and the two answer different
questions. The panel's copy now says "marked in amber on the map; click elsewhere to move
it" rather than asking for a click it gave no feedback on.

---

# P2 — a correctness hole with no symptom yet — **ALL CLOSED 2026-08-06**

## A7 — Static cube variables are never checked at startup — **CLOSED 2026-08-06**

[`window_valid_fractions`](../src/terrarium/state/cube.py#L469) skips `Dims.SPACE`
variables, correctly — a static layer is identical in every window, so there is nothing
per-window to say about it. The consequence is not correct: **`validate_windows` therefore
never notices that a static variable is missing entirely.**

`pm25_emission_g_s` is static. A cube without it passes the per-window check. The API would
start, serve maps, simulate temperatures, and return `air: null` for every low-emission-zone
request — with a `logger.warning` inside `_air` as the only trace.

That null is exactly the ambiguity the Phase 11 schema work was written to remove: from the
client's side, "this cube has no emission inventory" and "this plan says nothing about
traffic" are the same value, and they are opposite findings.

`cube_phase4.zarr` is refused today **by accident**: `wind_direction_deg` happens to be
time-varying, so the per-window check catches it. `pm25_emission_g_s`, equally absent, was
never looked at.

**Closed**, with one deliberate departure from the proposed fix: the check went into
`state.cube.validate_windows` rather than into `load_runtime`. `load_runtime` already calls
it and wraps its `ValueError`, so the API gets the refusal for free — and `inspect_cube.py`
and `hindcast.py`, which also validate, get it too. One refusal path, three callers.

Two new functions in `state/cube.py`:

- **`absent_variables(ds)`** — declared variables the cube does not carry at all, across
  *every* `Dims` rather than only the per-window ones. This is the hole as written.
- **`static_valid_fractions(ds)`** — the other half of it, which the finding did not name:
  a static variable that is **present but entirely fill value** is as useless as an absent
  one, and no per-window pass will ever look at it either. Caught now.

`window_valid_fractions` keeps its signature and still reports 0.0 for an absent
time-varying variable, so `/cube/summary` and its mirrored TypeScript type are unchanged.

**Verified fixed:** a cube with `pm25_emission_g_s` dropped is refused by name, and so is
one where it is present and all-NaN. Five tests in `api/test_runtime.py`.

## A8 — The refusal names the wrong remedy — **CLOSED 2026-08-06**

```
cube has unpopulated variable-windows, so it is a partial build: … Rebuild it -
see scripts/build_tile.py.
```

For `cube_phase4.zarr` that sentence is wrong in both halves. Nothing is partial: the cube
is complete for the schema it was built against, and simply predates a variable. And the
remedy is not `build_tile.py` — a network-heavy multi-minute rebuild against Planetary
Computer — it is `build_air_layers.py`, which takes seconds.

The comment at [cube.py:472](../src/terrarium/state/cube.py#L472) shows the collapse is
deliberate ("Zero valid, not a KeyError"), and returning 0.0 for an absent variable is the
right call. What is wrong is only the message: the two cases have different fixes and should
say so.

**Closed.** `validate_windows` now emits up to three sentences, and the remedy is *declared*
rather than branched on: `VariableSpec` gained an `added_by` field, defaulting to
`scripts/build_tile.py` and set to `scripts/build_air_layers.py` for the two Phase 9
variables. Adding a variable and naming the script that backfills it are one edit in one
place.

What the two cubes on disk say now:

```
cube_phase4.zarr:
  cube does not carry pm25_emission_g_s, wind_direction_deg at all, so it predates these
  variables rather than being a partial build. Add them with scripts/build_air_layers.py.

cube.zarr:
  ...same sentence... cube has unpopulated variable-windows, so it is a partial build:
  ndvi@2024-summer (0.0% valid), ... Rebuild it - see scripts/build_tile.py.
```

`cube.zarr` gets both, because it genuinely is both — stale *and* half-built. That is the
case the collapsed message could never express.

---

# P3 — claims resting on validation that has not run

## A9 — OpenAQ calibration has never run

`scripts/validate_air.py` implements leave-one-station-out with an affine fit, scored
against a null model, and refuses to run without `TERRARIUM_OPENAQ_KEY` (D16). No key is
set. The arithmetic is tested on synthetic data; it has never seen a station.

**Until it runs, the air core's emission factors are literature values for a South Asian
fleet and every modelled magnitude is uncalibrated.** Deltas between two plans survive that;
absolute numbers do not. The API, the brief and `CLAUDE.md` all say so — this finding is
about closing it, not about disclosing it.

Cost to close: a free, no-card registration at explore.openaq.org, then one command.

## A10 — Gemini has never run

`TERRARIUM_GEMINI_API_KEY` is unset. Two consequences of different weight:

- **The planner degrades cleanly.** No key means the rule parser, which is deterministic,
  offline, tested, and handles the phrasings a demo uses in English and Urdu. Nothing is
  blocked.
- **The photo path does not degrade at all.** No rule parser can read a photograph, so
  `POST /observations` answers 503. Everything about it is verified except the only thing
  that matters — whether the model's categories, severities and confidences are any good.
  That is completely unmeasured.

Treat the VLM feature as **built and unproven**, in the same words Phase 9 uses about the
air core. Cost to close: a free, no-card key from AI Studio, then submit half a dozen real
Lahore street photos and see whether the categories survive contact.

## A11 — The hindcast result cannot be reproduced — **CLOSED 2026-08-07, never true**

Phase 7's headline — greening cools **−0.47 °C** observed against **−1.18 °C** modelled,
over-predicting in 12 of 12 configurations — is quoted in `/simulate`'s brief, in
`ResultPanel`, in `dsl/explain.py` as `HINDCAST_OVERPREDICTION = 2.5`, and throughout the
plan and `CLAUDE.md`. It is the single most load-bearing number in the project, because
every cooling figure the product shows is divided by it.

`hindcast.py` needs **≥ 4 summer windows**. `window_years` defaults to `[2023, 2024]` — two
summers — and the wide multi-year build the result came from **is not in `data/processed/`**.
The number cannot be re-derived on this machine today.

Nothing suggests the number is wrong. But it is currently a figure with no reproducible
artefact behind it, which is precisely the position this repo's own rules say not to be in.

**Fix.** A wide build (`--years 2016 2017 … 2024`, roughly 70 s per window, so a coffee) to
a new `--out` path, kept alongside the serving cube. Then `hindcast.py` reproduces it, or it
does not, and either outcome is worth knowing before someone asks in a demo.

**Closed, and the finding was wrong on its facts.** The wide build has been in
`data/processed/` the whole time. The finding checked `window_years` — a *default*, which
`--years` overrides and which the build that produced this cube did override — and inferred
the artefact's absence from it instead of listing the directory. `IMPLEMENTATION_PLAN.md:878`
names the file explicitly.

```
data/processed/cube_hindcast.zarr
  18 windows: 2016-summer … 2024-winter (nine summers), 10 variables
```

No rebuild was needed. `uv run python scripts/hindcast.py --zarr data/processed/cube_hindcast.zarr`
runs today, trains on 2016–2019 summers and scores 2024-summer:

```
observed effect        -0.292 raw   -0.245 matched  degC
CHANGE-EFFECT ERROR    -0.517 raw   -0.580 matched  degC
WEAK  the model missed more than half of a 0.29 degC effect.
```

**This is consistent with the recorded result rather than identical to it, and the
difference is the point.** The plan quotes **−0.47 °C** observed against **−1.18 °C**
modelled as the median over **12 configurations**, with standard deviations of **0.533** on
the observed effect and **0.472** on the error (plan, line 942). This single default
configuration lands at −0.245 and −0.580 — both inside one standard deviation of the
recorded spread, in the same direction, with the same sign in the same 12/12 sense. A
single run was never going to return the median of twelve, and one that did would be the
surprising outcome.

`HINDCAST_OVERPREDICTION = 2.5` therefore keeps its artefact. Note that this run implies a
*larger* over-prediction (~3.4×) than the constant, so the 2.5× correction the product
applies is, on this configuration, the conservative end rather than the optimistic one.

**Verification:** `uv run python scripts/hindcast.py --zarr data/processed/cube_hindcast.zarr`

## A12 — Voice has no automated test

`SpeechRecognition` cannot be driven from vitest. Covered: support detection, transcript
assembly, the error-code translations, and that the language list matches what the parser
reads. Not covered: the recogniser itself, which was exercised by hand only.

**Accepted rather than fixed.** Testing it would mean a real browser in CI, which is a large
dependency for one button. The mitigation already shipped: the panel renders no microphone
where the API is absent, so the failure mode is a missing control, not a dead one.

---

# P4 — repo and documentation hygiene

## A13 — No CI — **CLOSED 2026-08-06**

**Closed.** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), three jobs on every
push and pull request:

- **python** — `uv sync --extra dev --frozen`, then `ruff`, `mypy`, `pytest`. `--frozen` so
  a lockfile that disagrees with `pyproject.toml` fails rather than silently resolving
  something new; CI installing different versions than a developer is how "works on my
  machine" gets built into the pipeline itself.
- **web** — `npm ci`, then `oxlint`, `vitest`, and `npm run build`. The build runs `tsc -b`,
  which catches TypeScript that no longer compiles. ~~which is what would catch the
  client/schema drift in A18.~~ **That was wrong — see A24.** `tsc` never sees a Pydantic
  model; the client/schema diff is `api/test_client_types.py`, in the python job.
- **network-isolation** — the suite again with **outbound sockets blocked**
  ([`ci/no_network.py`](../ci/no_network.py), loaded with `pytest -p`). Loopback stays
  allowed, because `TestClient` uses it.

That third job is the finding's own parenthesis taken seriously. "No test may touch the
network" is a rule in `CLAUDE.md`, and the one time it broke, the test **passed for the
wrong reason** — which is exactly the failure a green suite cannot report. Kept as its own
job so that a violation is not mistaken for a failing assertion.

**Verified:** 370 Python tests and 96 browser tests pass, `ruff` clean, `mypy --strict`
clean over 73 files, `oxlint` clean, `npm run build` succeeds — and the full Python suite
passes with sockets blocked, so the isolation job is green rather than aspirational. The
guard was checked against a real outbound connection first, so it is not a no-op.

No secrets are configured, deliberately: a workflow needing `TERRARIUM_GEMINI_API_KEY`
would make a fork's first push fail for a reason that is not the fork's fault.

## A14 — README describes a product that stopped existing at Phase 8 — **CLOSED 2026-08-06**

Line 11 still says:

> **v1 core:** thermal only — a LightGBM emulator of **mid-morning land surface temperature**

Since then: the equity core, the air core, the DSL, the costed preset library, the
deterministic brief, voice capture in English and Urdu, and citizen photos. Grepping the
README for `/plan`, `observations`, `equity`, `voice` or `air` returns two incidental
mentions in the opening paragraph and nothing else. `TERRARIUM_GEMINI_API_KEY` is not
mentioned at all, so a reader cannot discover the optional key exists.

Someone cloning this repo would not learn that two-thirds of it is there.

**Closed.** The "v1 core: thermal only" line is gone. The README now carries: a table of the
three cores and their methods; the DSL, presets, brief, observations, voice and the council
brief; both naming rules (**mid-morning land surface temperature**, **locally-generated
PM2.5**) with the reason each exists; the full route list; the zero-budget position
including `TERRARIUM_GEMINI_API_KEY` and `TERRARIUM_OPENAQ_KEY`; the
`zarr_store`/`serve_zarr_store` split and the 503-not-404 behaviour; the `build_air_layers.py`
graft with the Overpass mirror; the 2.5× hindcast correction next to what blocked CV does
*not* prove; and a pointer to this file as the thing to check before a demo.

## A15 — `NOTES.md`'s open TODO is two phases stale — **CLOSED 2026-08-06**

**Closed.** Both TODO sections marked closed with what superseded them, and pointed at this
file as the live version of the concern. `cube.zarr` stays on disk as a known-bad artefact —
`validate_windows` refuses it, and a refusal check is worth having something to refuse.

## A16 — Plan header still dated 2026-07-31 — **CLOSED 2026-08-06**

`IMPLEMENTATION_PLAN.md` opened with *"Status as of 2026-07-31"* while documenting phases
through 11, decisions through D20, and results measured in August. Fixed in the same edit
that added this document's pointer to the plan: the header is now dated, and carries the
warning that a ✅ in the phase table does not mean the software runs.

## A17 — `requirements.txt` is an unmaintained second source of truth — **CLOSED 2026-08-06**

328 lines, generated by `uv export`, regenerated by nothing. It will drift from `uv.lock`
silently and be discovered by a deployment that installs the wrong versions.

**Do not delete it** — Hugging Face Spaces (Phase 12, D13) installs from it.

**Closed, and it had already drifted.** The export command is now in `CLAUDE.md`'s commands
block, with the reason it must not be hand-edited or deleted. Regenerating it produced a
real diff — four lines:

```
  #   odc-stac
+ #   terrarium        (and the same on odc-loader, pystac, referencing)
```

**Comment lines only, no version changes**, so no deployment would have installed anything
wrong today. It is the `# via` provenance catching up with the direct-import declarations
in `pyproject.toml`. Worth stating plainly: the finding was right that it drifts, and this
particular drift was harmless.

## A18 — The hand-mirrored TypeScript types have already drifted — **CLOSED 2026-08-07**

`web/src/api/client.ts` mirrors `api/schemas/` by hand, and the plan names this as "the seam
most likely to drift". It has drifted, once, measurably: `SimulateResponse` had **no `air`
field for the entire life of Phase 9**. It was added during Phase 10 while wiring the
presets — which means a client written against those types could not have read the air
result even if a panel had existed for it (A3).

**Fix.** Generate from `/openapi.json`. The plan already sanctions this; the drift has now
happened once, which is the evidence it was waiting for.

**Closed, but not by generating them.** The types are *checked* against `/openapi.json`
rather than produced from it, in
[`api/test_client_types.py`](../src/terrarium/api/test_client_types.py) — three tests that
build the real app, read its schema, parse `client.ts`, and diff the field names.

The reasoning for checking rather than generating: codegen means a new dependency, a build
step, and a generated file in the tree, and it would replace a hand-written client whose
comments carry the reasoning for half its fields (why `share` is a fraction, why
`confidence` has no `"high"`). The failure this finding actually describes is a **missing
or misnamed field**, and that is what a diff catches — for a fraction of the moving parts,
in the suite that already runs on every push.

What it checks, in both directions, because they fail differently:

- a field the server sends and `client.ts` omits is data the UI cannot reach — the Phase 9
  `air` bug exactly;
- a field `client.ts` declares and the server never sends is an `undefined` that reads on
  screen as a legitimate zero;
- a whole **response schema** with no interface at all, which a field diff cannot see
  because an absent interface has nothing to compare.

There is also a test that the parser still finds interfaces, because a regex that silently
matched nothing would make the other two pass unconditionally — a guard that cannot fail is
the same problem as the missing guard this finding is about.

**Verified by injecting the historical drift.** Deleting `air: Air | null;` from
`SimulateResponse` — the exact line whose absence defined this finding — fails the suite
with the field named:

```
AssertionError: web/src/api/client.ts has drifted from api/schemas/:
  SimulateResponse <- SimulateResponse: server sends ['air'], client.ts does not declare it
```

**Verification:** `uv run pytest src/terrarium/api/test_client_types.py`

---

# P5 — small, real, cheap

## A19 — `_plan_name` calls a do-nothing request a planting — **CLOSED 2026-08-06**

**Closed.** Both levers at zero now returns `"No intervention"`, so the brief reads *"No
intervention over N km2 changes nothing measurable"* rather than reporting a modelled result
for a planting nobody requested.

The distinction is **requested**, not **achieved**: planting over the river still headlines
as a planting, because the plan did ask to plant and "changes nothing measurable" is then
the right answer to the right question. Both cases are tested.

## A20 — `ponytail:` marker in `cores/air.py` — **CLOSED 2026-08-06**

**A leftover, not a codeword — and the finding's evidence for "used consistently" was
wrong.** `CLAUDE.md` never mentions `ponytail`; the only references were `air.py:196`,
`air.py:267` and one line in `IMPLEMENTATION_PLAN.md`. Nothing anywhere explained it.

Renamed to `caveat:` in all three places. The limitation it introduces is real and worth
finding — one tile-mean deposition velocity, which is why planting moves PM2.5 by only
−0.0003 µg/m³ — so it now carries a marker people will recognise.

## A21 — No rate limiting on `POST /observations` — **CLOSED 2026-08-07**

The bounded store caps memory (500 reports, oldest dropped), and the base64 size cap bounds
a single request. What is unbounded is **spend against a rate-limited free tier**: an
unauthenticated POST that consumes Gemini quota. Fine while the API is on localhost;
worth a per-IP cap before Phase 12 puts it on a public URL. No auth (that is scope), just a
ceiling.

**Closed.** `RateLimiter` in [`api/observations.py`](../src/terrarium/api/observations.py):
a rolling window, **20 photos per caller per hour**, in-process and thread-safe like the
store it sits beside, and constructed *by* the store so a route cannot wire one and forget
the other. No dependency — `slowapi` would have been a new one for a dict and a deque.

Done now rather than deferred to Phase 12, which is a small departure from the finding: it
is fifteen lines guarding the one route in the project that spends someone else's quota
without auth in front of it, and the ceiling is what stops a single caller taking the
feature down for everybody. It is a ceiling, not authentication — auth remains scope.

Three ordering decisions, each of which is what the tests actually pin:

- **After the 503.** A deployment with no key configured refuses for the honest reason
  rather than rationing calls it never makes.
- **Before the model call**, which is the only thing on the route that costs anything.
  `test_a_refused_call_never_reaches_the_model` asserts the stub's call count stays at 1
  across five refusals — a limiter that ran after the spend would pass every other test.
- **After validation**, so a malformed request is refused without burning a slot. A 422 is
  free; only the model call is rationed.

Two bounds rather than one: the rolling window per caller, and a cap on **how many callers
are tracked**, because a dict keyed on a stranger-supplied value is the same memory leak
with a public door that the store's own capacity exists to prevent.

The window rolls rather than resetting on a boundary, and a **refused call does not extend
it** — otherwise hammering the endpoint would keep it permanently closed. Reads are not
rationed at all: `GET /observations` costs nothing upstream.

Keyed on `request.client.host`, which behind a proxy is the proxy. Stated in the docstring
rather than papered over: this is a ceiling on obvious abuse, not a defence against a
distributed one, and the thing being protected is a free API key.

**Verification:** `uv run pytest src/terrarium/api/test_observations.py src/terrarium/api/routes/test_observations.py`
— 5 unit tests on the window and the caller bound, 4 through the real route with the
provider stubbed at the adapter seam, so none of them opens a socket.

## A23 — `cell_from_lonlat` accepted a coordinate it then mapped off the grid — **CLOSED 2026-08-07**

Found by reading [`api/geometry.py`](../src/terrarium/api/geometry.py), not by a failing
test — there were **no tests for `cell_from_lonlat` at all**, though it is the function
`POST /observations` uses to place every citizen photo.

The bounds check and the arithmetic two lines below it disagreed:

```python
if not (left <= x < right and bottom <= y < top):   # accepted y == bottom
...
return (int((top - y) // grid.resolution_m), ...)   # y == bottom -> row 201
```

A north-up grid counts rows *down* from `top`, so `top` is row 0 and `bottom` is one row
past the last. The check had the y axis half-open the wrong way round, and both ends were
wrong in opposite directions:

- a photo on the tile's **northern** edge (`y == top`) was **refused** as outside the tile;
- a photo on the **southern** edge (`y == bottom`) was **accepted** and mapped to row 201
  of a 201-row grid — an `IndexError` in `severity_raster`, on a coordinate the validator
  had just called valid.

The x axis was already correct, because columns count up from `left` in the same direction
the check reads.

Reachable only from a coordinate that projects to exactly `y == bottom`, so this is a
latent defect rather than one that has fired: a WGS84 round-trip essentially never lands on
the boundary exactly, which is why nothing noticed. It is on the trust boundary
nonetheless — the value comes from an HTTP body — and the fix is the comparison, not a
clamp downstream.

Fixed by making the accepted set match the arithmetic: `bottom < y <= top`. **Six tests
added**, including the tile's four corners and an assertion that the north-west corner is
cell `(0, 0)`; the last of these fails against the old comparison, which is what
establishes it as a fix rather than a rewrite.

**Verification:** `uv run pytest src/terrarium/api/test_geometry.py`

## A24 — CI claimed `tsc -b` guarded A18, and it cannot — **CLOSED 2026-08-07**

[`ci.yml`](../.github/workflows/ci.yml) carried:

> `tsc -b` runs inside this, which is what catches the client/schema drift that shipped a
> SimulateResponse with no `air` field for the whole of Phase 9 (A18).

and A13 in this document said the same. **It is false.** `tsc` type-checks TypeScript
against TypeScript. It has no knowledge of `api/schemas/`, never sees `/openapi.json`, and
cannot form an opinion about whether `client.ts` matches a Pydantic model — a field added
to a response schema and forgotten in the client compiles perfectly and is invisible until
something reads `undefined` at runtime.

```bash
grep -rln "openapi" src/ web/src/ ci/     # web/src/api/client.ts, and nothing else
```

Nothing in the repository compared the two sides. So the one open finding CI was described
as covering was precisely the one thing it did not, and the claim is what made that
comfortable to leave open.

**Closed on both sides:** the comment now says what `tsc -b` does and does not do and
points at `api/test_client_types.py` (A18) as the check that actually looks at both
schemas, and A13's claim is corrected below.

Worth keeping as its own finding rather than folding into A18: the missing guard and the
false belief that it existed are different failures. The first is a gap; the second is what
stops a gap from being noticed.

## A25 — `leave_one_station_out` fits a degenerate fold — **CLOSED 2026-08-07**

`cores/air.py` refuses a station set with no spread in the modelled concentration, and says
exactly why:

> A slope through points that share an x is arbitrary, and numpy fits one anyway with only
> a `RankWarning`. That report would carry a confident scale factor invented from nothing.

**The guard ran once on the whole set; the fit runs once per fold.** A set with spread can
contain a fold without any — three monitors reading the same modelled concentration plus
one reading a different one — and holding out the odd one leaves three identical `x`.
Measured, not reasoned about:

```
full-set ptp: 4.0  -> existing guard passes
warnings raised: ['RankWarning']
scale: 7.25   background: 3.75
  d: observed 40.0, predicted 33.0
beats_null: True
```

That is the failure the guard's own docstring describes, reported as skill. It matters
because **`scale` is the correction the emission inventory would be calibrated by** — the
number A9 exists to produce — and `beats_null` is the only claim the report is supposed to
make. With a handful of monitors in one city, three of them outside the modelled plumes is
an ordinary arrangement rather than a pathological one.

Latent today: this path only runs with an OpenAQ key, so it has never executed. It would
have fired the first time A9 was closed, silently, in the direction of looking better.

**Closed** by checking every fold where the fit happens, keeping the whole-set check ahead
of it because that case has a clearer cause and deserves its own message. Two tests: the
degenerate fold is refused by name (`holding out d`), and a well-spread set still fits with
`warnings.simplefilter("error")` so a future `RankWarning` fails the suite rather than
scrolling past.

**Verification:** `uv run pytest src/terrarium/cores/test_air.py`

## A26 — The "< 3 s warm" claim was unverified — **CLOSED 2026-08-07**

The 2026-08-06 audit closed with *"the '< 3 s warm' claim in particular is unverified today,
because no cube loads."* A1 fixed the cube and nobody went back and measured it.

Measured against `cube_phase9.zarr`, 2024-winter, a ~44 km² polygon with both levers set,
five runs after one warm-up:

```
POST /simulate      0.84 s mean (0.82 min, 0.89 max)
GET /cube/layer     0.04 s
```

**Comfortably inside the claim**, with the whole-tile FFT convolution, the equity pass and
the brief all included. The interactivity argument in `CLAUDE.md` — one kernel, one FFT,
the whole tile at once — holds at the measured budget rather than only in principle.

## A22 — `scripts/` has no tests

Seven scripts — the entire operational surface, including the two that build the artefacts
everything else depends on. `pytest.ini_options.testpaths` is `src/terrarium`, so they are
not even collected.

**Accepted rather than fixed**, with a caveat: they are thin I/O wrappers over tested
library code, and testing them properly means fixtures for the network they exist to talk
to. But `build_air_layers.py` and `build_tile.py` are the two things whose failure produces
A1, and the argument for leaving them untested gets weaker every time that happens.

---

## Things that look like findings and are not

Recorded so nobody spends an afternoon re-deciding them:

- **`air: null` from `/simulate`** is correct when a plan does not touch traffic (D14). The
  brief distinguishes that from a cube that cannot answer — see `emission_fraction_requested`
  in `dsl/explain.py`.
- **Observations not being written into the cube** is D19, deliberate, and argued in
  `dsl/observe.py`'s module docstring.
- **No LangGraph** is D17. The planner is two steps with nothing to checkpoint.
- **No auth, no user accounts, no persisted scenarios** are permanent scope boundaries in
  `CLAUDE.md`, not omissions.
- **No flood core** is scope. There is deliberately no flood category in the observation
  schema either, so the product does not collect reports it cannot answer.
- **`confidence` never being `"high"`** is intentional in `dsl/explain.py`.
- **Costs being `calibrated: false`** is intentional and will stay false until somebody
  prices a real planting contract.

---

## Recommended order

Items 1–6 were **done on 2026-08-06**, in this order:

1. ~~**A1**~~ — the cube, via the Overpass mirror. ~~**A2**~~ fell out of the same session,
   though not for the recorded reason — see its entry.
2. ~~**A13**~~ — CI, plus a network-isolation job.
3. ~~**A7 + A8**~~ — the startup check and the split refusal messages.
4. ~~**A3**~~ — the air UI.
5. ~~**A4, A5, A6**~~ — the remaining interface gaps.
6. ~~**A14, A15, A16, A17, A19, A20**~~ — documentation and the cheap P5 items, in one pass.

Items 7–10 were **done on 2026-08-07**:

7. ~~**A18**~~ — closed by *checking* the types against `/openapi.json` rather than
   generating them, and verified by injecting the historical `air` drift.
8. ~~**A11**~~ — no rebuild needed. The cube was on disk the whole time; the finding was
   wrong.
9. ~~**A24**~~ — the CI comment that made A18 look guarded when nothing guarded it.
10. ~~**A23**~~, ~~**A25**~~, ~~**A21**~~ — the two latent guard defects found while
    reading, and the rate limit, pulled forward from Phase 12 because it is fifteen lines.
    ~~**A26**~~ — the performance claim, measured at last.

What is left:

11. **A9, A10** — two free, no-card registrations, then run what is already built. A9 is
    what lets the air panel drop the word **uncalibrated**; A10 is the only way to find out
    whether the VLM's categories survive contact with real Lahore street photos. **These
    are the only open findings that change what the product may claim**, and neither is
    blocked on code.
12. **Phase 12.**

**A12 and A22 stay accepted**, unchanged: voice needs a real browser in CI for one button,
and `scripts/` are thin I/O wrappers whose honest tests are fixtures for the network they
exist to talk to. A22's caveat still stands, and A11 has now made it slightly weaker rather
than stronger — the artefact that finding declared missing was produced by one of those
untested scripts and was fine.
