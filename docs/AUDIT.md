# Terrarium — repository audit

**Produced 2026-08-06, immediately after Phase 11 was marked done** (`d2f46f2`). Working
tree clean, Phases 0–11 complete, Phase 12 untouched.

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
| **A11** | The hindcast result cannot be reproduced: its cube is gone | **P3** | validation | a long rebuild |
| **A12** | Voice has no automated test | **P3** | web | accepted, see below |
| ~~A13~~ | ~~No CI~~ | **CLOSED** | repo | fixed 2026-08-06 |
| ~~A14~~ | ~~README describes a thermal-only product that stopped existing at Phase 8~~ | **CLOSED** | docs | fixed 2026-08-06 |
| ~~A15~~ | ~~`NOTES.md`'s open TODO is two phases stale~~ | **CLOSED** | docs | fixed 2026-08-06 |
| ~~A16~~ | ~~Plan header still dated 2026-07-31~~ | **CLOSED** | docs | fixed 2026-08-06 |
| ~~A17~~ | ~~`requirements.txt` is an unmaintained second source of truth~~ | **CLOSED** | repo | fixed 2026-08-06 |
| **A18** | The hand-mirrored TypeScript types have already drifted once | **P4** | web | ~2 h to generate |
| ~~A19~~ | ~~`_plan_name` calls a do-nothing request a planting~~ | **CLOSED** | api | fixed 2026-08-06 |
| ~~A20~~ | ~~`ponytail:` marker in `cores/air.py`~~ | **CLOSED** | cores | fixed 2026-08-06 |
| **A21** | No rate limiting on `POST /observations` | **P5** | api | defer to Phase 12 |
| **A22** | `scripts/` has no tests | **P5** | tests | accepted, see below |

Severities: **P0** the demo does not run · **P1** shipped work the user cannot see ·
**P2** a correctness hole with no symptom yet · **P3** claims resting on unrun validation ·
**P4** repo and documentation hygiene · **P5** small, real, cheap.

**Fifteen of the twenty-two are now closed** (A1–A8, A13–A17, A19, A20), all on
2026-08-06. What is left is five findings and one of them is a real piece of work:

- **A9, A10** — blocked on a free, no-card registration rather than on code. Until they
  run, the air magnitudes are uncalibrated and the whole photo path is built but unproven.
  Both are disclosed everywhere they matter; closing them means running what already
  exists.
- **A11** — needs a wide multi-year rebuild (~70 s per window against Planetary Computer)
  before the 2.5× hindcast correction has a reproducible artefact behind it again.
- **A18** — generate the TypeScript types from `/openapi.json`. The only remaining item
  with real engineering in it.
- **A12, A22** — accepted rather than fixed, with the reasoning below.
- **A21** — deferred to Phase 12, where it stops being hypothetical.

**Nothing in this document now stops the product working**, and nothing shipped is
invisible: the air core, the citizen raster, the emission lever and the photo marker all
reached the interface. A13 means the checks that guard all of it now run on every push
instead of when somebody remembers.

**What this audit did not look at.** Artefacts on disk, startup paths, route wiring, the
API-to-frontend surface, docs against reality, and lint/type/test state — those were
checked by running them. A physics review of the cores was **not** done (the plan's
measured results are the evidence there), nor a security review, nor any performance
profiling: the "< 3 s warm" claim in particular is unverified today, because no cube loads.

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

## A11 — The hindcast result cannot be reproduced

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
  which is what would catch the client/schema drift in A18.
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

## A18 — The hand-mirrored TypeScript types have already drifted

`web/src/api/client.ts` mirrors `api/schemas/` by hand, and the plan names this as "the seam
most likely to drift". It has drifted, once, measurably: `SimulateResponse` had **no `air`
field for the entire life of Phase 9**. It was added during Phase 10 while wiring the
presets — which means a client written against those types could not have read the air
result even if a panel had existed for it (A3).

**Fix.** Generate from `/openapi.json`. The plan already sanctions this; the drift has now
happened once, which is the evidence it was waiting for.

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

## A21 — No rate limiting on `POST /observations`

The bounded store caps memory (500 reports, oldest dropped), and the base64 size cap bounds
a single request. What is unbounded is **spend against a rate-limited free tier**: an
unauthenticated POST that consumes Gemini quota. Fine while the API is on localhost;
worth a per-IP cap before Phase 12 puts it on a public URL. No auth (that is scope), just a
ceiling.

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

What is left, in the order it pays:

7. **A18** — generate the TypeScript types from `/openapi.json`. The only open item with
   real engineering in it, and the one the new CI would have caught. Two fixtures written
   by hand today needed three corrections against the real schema before `tsc` accepted
   them, which is the same drift in miniature.
8. **A11** — the wide rebuild (`--years 2016 … 2024`), so the 2.5× correction has a
   reproducible artefact behind it again. It is quoted in every cooling figure the product
   shows and currently cannot be re-derived on this machine.
9. **A9, A10** — two free, no-card registrations, then run what is already built. A9 is
   what lets the air panel drop the word **uncalibrated**; A10 is the only way to find out
   whether the VLM's categories survive contact with real Lahore street photos.
10. **Phase 12**, folding in **A21**.
