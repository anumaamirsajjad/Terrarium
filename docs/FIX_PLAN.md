# Terrarium — fix plan

**Input.** The 30 findings in [docs/TEST_PLAN.md](TEST_PLAN.md), F1–F30. This file does not
re-argue any of them; it says **what to change, in what order, and how you will know it
worked.** Every fix carries a verify command, because a fix nobody can re-run is a claim.

**Ordering principle.** Not by severity. By *dependency and blast radius*:

1. Things that stop you verifying anything else (Phase 0).
2. Wrong numbers reaching a user on inputs they can actually produce (Phase 1).
3. Claims in the docs that the code does not enforce (Phase 2).
4. The unauthenticated front door (Phase 3).
5. Artefact integrity, including the one item that needs a network build (Phase 4).
6. Everything small (Phase 5).

An S4 that takes one line sits in Phase 5 anyway. The point of the order is that each phase
leaves the repo in a state where the next phase's results are interpretable — the same rule
the test plan was built on.

**Rules for this work**, carried over from CLAUDE.md and worth restating because several
fixes brush against them:

- **`cores/` stays pure.** F25 adds a raise inside `cores/air.py` — a raise is not I/O, and
  it reads only its arguments. Nothing in this plan puts a file read, a clock or a config
  import into a core.
- **`Intervention`, `CoreResult` and `DeltaStats` are frozen contracts** and changing their
  shape is a two-person decision. **No fix here changes them.** F15 changes what
  `dsl/explain.py` *writes* about a `DeltaStats`, not the struct.
- **Tests live beside the module** (`foo.py` ↔ `test_foo.py`), and **no test may touch the
  network.** Every test named below is offline.
- **Keep a known-good Zarr.** Phase 4 rebuilds to a *new* `--out`. `serve_zarr_store` moves
  only after the new cube passes `validate_windows`.
- **Do not rebuild the night before a demo.** Phase 4 is the one phase with an external
  dependency and it is deliberately last of the code phases.

**Decisions needed from a human before starting.** Three, all in Phase 2 or 4:

| # | Decision | Where |
|---|---|---|
| D-a | Which of three approaches to F12 (meteorology non-additivity) | Phase 2.1 |
| D-b | Whether `build_air_layers.py` keeps writing in place by default | Phase 5.4 |
| D-c | Whether to spend a build on a 2025 cube now or accept F10 until after the demo | Phase 4.2 |

---

## Phase 0 — Unblock the machine

**Findings:** F1, F2. **No code changes.** Effort: minutes.

Nothing below Phase 0 can be verified on this machine as it stands: every data route 503s
(F1) and `uv sync` cannot repair the venv (F2). Do this first and do not skip it — every fix
in Phases 1–4 needs a working `/simulate` to verify against.

### 0.1 — Point the API at a cube that exists (F1)

`Settings.serve_zarr_store` defaults to `data/processed/cube_v2.zarr`, which is not on disk.
Three cubes are, and only `cube_phase9.zarr` passes `validate_windows`.

Create a `.env` (it is gitignored, `.gitignore:26`):

```
TERRARIUM_SERVE_ZARR_STORE=data/processed/cube_phase9.zarr
```

Do **not** fix this by editing the default in `config.py` to `cube_phase9.zarr`. The whole
reason `serve_zarr_store` is a separate setting from `zarr_store` is that the serving path
moves only when a build has been checked — encoding this machine's current artefact as the
committed default defeats that. If the default is wrong for everyone, that is a separate
conversation about what ships.

*Verify:* `curl -s localhost:8000/cube/summary` → 200 with four windows.

### 0.2 — Repair the environment (F2)

Stop the dev server holding `terrarium-api.exe` (PID 536 at the time of the audit), then:

```bash
uv sync --extra dev --frozen
uv run mypy                       # must be clean; it went red on missing langchain alone
```

`--frozen` because the lock is not in question — the audit confirmed no drift (0.1, 0.6).

*Verify:* `uv sync --extra dev --frozen --check` reports nothing outdated, and `uv run mypy`
is clean **without** `--no-sync`.

---

## Phase 1 — The three wrong-answer fixes

**Findings:** F15 (S1), F14 (S2), F25 (S2). Effort: F14 and F25 are hours; F15 is the real
work, most of it in deciding what a warming brief should say.

These are grouped because they share one property: each produces a **plausible-looking
answer that is wrong**, on an input the system accepts. Everything else in this plan is a
crash, a resource limit, or a claim.

### 1.1 — A warming result must be reported as warming (F15)

Five separate templates in `dsl/explain.py` assume `mean_delta_inside` is negative. Fix all
five; fixing the headline alone leaves a response that contradicts itself.

| Location | Today | Change |
|---|---|---|
| `explain.py:447-450` | `f"cools … by {abs(inputs.mean_delta_inside):.2f} degC"` | branch on the sign; write a warming headline with its own sentence, not `abs()` |
| `explain.py:253` (`plain_summary`) | `cooled = expected < -0.005` — a warming result falls into the *nothing happened* branch | add a third branch: warming is not "no measurable change" |
| `explain.py:500-505` | `ratio_to_linear` narrated as *"slightly conservative, which is the expected direction"* | **withhold** the finding when the net delta is positive; the sentence is only meaningful near 1.0 |
| `explain.py:513-520` | equity shares reported at `shares_reliable=True`, giving **104 %** and **−0 %** | withhold, or route to the existing "withheld" branch: the reliability guard was built for a near-zero net benefit, not a wrong-signed one |
| `explain.py:486-489` | *"The ceiling on this window is 2.60 degC … No planting here can beat that"* — printed beside a served 5.25 | assert the served mean against `tree_built_contrast_c` where the brief already quotes it as a ceiling |

`Brief.expected_cooling_c` is already signed and already correct — it is the one field that
told the truth in the audit. Use its sign as the single source for every branch above rather
than re-deriving the test in five places.

**Also decide what `Impact` says.** `_impact` returns `"none"` for a positive expected value
(`explain.py:120`). A +5.25 °C warming is not "none". Either add a `warming` member to the
`Impact` literal — which touches `PlainSummary.verdict`, `web/src`'s `VERDICT` record in
`PlainPanel.tsx:37`, and `equity.ts`'s verdicts — or keep the literal and let the headline
carry it. **Recommendation: add the member.** A structured field that cannot express
"this made it worse" is how F15 stayed invisible; and `PlainPanel`'s `VERDICT` map already
handles a `null` entry, so the UI change is small.

*Tests to add* (`dsl/test_explain.py`):
- a warming input produces a headline containing "warm" and **not** "cools"
- a warming input's `plain.headline` does not say "does not change … by a measurable amount"
- a warming input withholds `ratio_to_linear` and the equity shares
- `expected_cooling_c` keeps its sign in every branch (regression on the field that was right)

*Verify:* the one-cell polygon from the audit —
`POST /simulate` with a polygon at 74.27071 / 31.57807, `canopy_fraction_added=0.15`,
`window=2024-summer` → `mean_delta_inside=+5.25` and a headline that says so.

### 1.2 — `/plan` must not 500 on a long number (F14)

Three changes, defence in depth, because each alone leaves a gap:

1. **`dsl/planner.py:126-131`** — `_number` must reject a non-finite value rather than
   `round(inf)`. Raise `PlanParseError` with the same voice as the other refusals ("that is
   not a number of trees anybody can plant").
2. **`dsl/planner.py:84`** — cap the digit run in `_TREES`: `(\d[\d,\s]{0,15}…)`. A tree count
   nobody can procure is not a parse the DSL owes anyone.
3. **`api/routes/plan.py:85`** — widen the `except PlanParseError` to catch `ValueError`
   generally. `PlanParseError` *is* a `ValueError`, so this is a one-word change, and it means
   the next arithmetic surprise in the parser is a 422 rather than a 500.

*Tests to add* (`dsl/test_planner.py`, `api/routes/test_plan.py`):
- `"plant " + "9"*309 + " trees"` raises `PlanParseError`, not `OverflowError`
- the same string through `POST /plan` is **422**, not 500
- `"plant 5,000 trees"` still parses as 5,000 (the cap must not break the happy path)

*Verify:* `POST /plan {"text": "plant " + "9"*309 + " trees"}` → 422.

### 1.3 — An empty inventory must not answer the air question (F25)

Two raises, and **both are needed** — either alone leaves the other path open.

1. **`ingest/osm.py`, in `emission_grid`** — raise when the payload contains no usable way.
   A Lahore tile with no roads is a fault upstream, not a finding. This is where the
   `HTTPError` chain in `build_air_layers.py` already fails loudly; make the *200-with-nothing*
   case fail the same way.
2. **`cores/air.py`, in `simulate`** — raise `ValueError` when the inventory inside the mask
   is entirely zero. This makes the comment at `api/routes/simulate.py:113` true; it currently
   describes a guard that does not exist, and `_air` already catches `ValueError` and returns
   `None`, so **the API needs no change at all** once the core raises. That is the whole
   appeal of fixing it here.

Purity note: a raise reads only `window` and `intervention`. The core stays pure.

Consider also having `state.cube.validate_windows` flag an all-zero `pm25_emission_g_s` — it
cannot see this today because zeros are finite, which is the same property that makes a
`population` zero legitimate (3.7). Treat that as optional: the two raises above close the
path, and a third guard in `validate_windows` catches a cube built before them.

*Tests to add* (`ingest/test_osm.py`, `cores/test_air.py`, `api/routes/test_simulate.py`):
- `emission_grid({"elements": []}, grid)` raises
- `simulate` on an all-zero inventory raises `ValueError`
- `/simulate` with `emission_fraction_removed=1.0` against an all-zero cube returns `air: null`
  **and logs the reason** — not a zero delta

*Verify:* `emission_grid({"elements": []}, grid)` raises; and the real inventory still gives
`-4.7022 µg/m³` for the audit's winter mask (a regression check that the raise is not too eager).

---

## Phase 2 — Make the claims match the code

**Findings:** F12 (S1), F16 (S1), F7 (S2). Effort: F16 is half a day; F12 depends on D-a.

Neither F12 nor F16 is serving a wrong number today. Both mean **a number can be quoted
under a justification that is not true**, which for this project — whose pitch is its
honesty about uncertainty — is the more expensive kind of defect. Phase 2 is where the
documentation and the enforcement are brought into line, in whichever direction is right.

### 2.1 — Meteorology: decide, then say the true thing (F12) — **needs D-a**

The measured fact: holding meteorology fixed on both sides does **not** make it cancel,
because LightGBM is not additive. Varying only `air_temp_c` swings the headline **4.1×**
(−0.058 to −0.240 °C), with the step structure of tree splits.

Three approaches, and they are not mutually exclusive:

| | Approach | Cost | What it buys |
|---|---|---|---|
| **A** | **Correct CLAUDE.md and add the sensitivity to `uncertainties`.** Stop claiming cancellation; state that meteorology conditions the intervention's *scale*, and quantify it | hours | Honest immediately. Changes no number |
| **B** | Measure it per response: re-run the delta at the window's meteorology ±1 σ and report the spread | a day; ~2× the `/simulate` cost, against a 0.44 s p50 and a 3 s budget | A number quotable with its own error bar |
| **C** | Constrain the model — interaction constraints, or drop meteorology and train per season | a week; invalidates the current `thermal.txt`, the 91.5 % gain figure, and the hindcast | Makes the original claim *true* |

**Recommendation: A now, B next, C only if the plan reopens it.** A is required regardless —
the docs are wrong today and that is not contingent on anything. C is a model change that
would re-open the hindcast and every quoted skill number; it is a phase, not a fix.

Note the second-order consequence to write down under A: CLAUDE.md describes the hindcast
penalty for an unseen year as *"1.9–5.3 °C of **offset**"*. F12 shows an unseen year would also
change the intervention's **scale**, which an offset correction does not address. That
sentence needs the same correction as the cancellation claim.

*Verify:* the CLAUDE.md paragraph on meteorology no longer contains the word "cancels", and
`brief_for`'s `uncertainties` names the sensitivity for any planted plan.

### 2.2 — Write the test that would have caught it (F7)

This is the missing test F7 logged, and after 2.1 you finally know what it should assert.
**Do not assert cancellation** — it is false. Assert the property that is true and the one
that matters:

- **The plumbing property (true, and worth locking):** `simulate` passes *identical*
  meteorology columns into the baseline and scenario feature frames. Assert column equality
  between the two frames — that is what a refactor reading meteorology from the scenario cube
  would break, and it would break silently.
- **The sensitivity property (true, and currently undocumented):** varying `air_temp_c` with
  all land data fixed *does* change the delta. Assert it changes, with the measured
  magnitude as a loose bound. A test that starts failing because the delta became
  meteorology-invariant is a test reporting that approach C landed — which is information,
  not a failure.

*Tests to add:* `cores/thermal/test_simulate.py`.

*Verify:* `uv run pytest src/terrarium/cores/thermal/ -k meteorology`.

### 2.3 — The narration guards must check units and direction, not just numerals (F16)

`_numbers_are_faithful` compares two sets of numeral strings, so °C→°F, km²→m²,
million→billion and "cooler"→"HOTTER" all pass. Two new post-checks, in the same style as
the two that already work — post-checks on the model's output, never prompt instructions:

1. **Tokenise numeral + unit as one atom.** `0.16 degC` is the unit of comparison, not `0.16`.
   Cover `degC`, `degF`, `km2`, `m2`, `µg/m3`, `%`, `$`, and the magnitude words `million`
   and `billion` — the audit's `6.2 million → 6.2 billion` case passes a naive unit check
   because the *unit* is unchanged and the multiplier is a word.
2. **A direction guard.** If the source says cools/cooler/lower, the rewrite may not say
   warms/warmer/hotter/higher. Small, blunt, and it closes the case that inverts the finding
   while keeping every figure.

Accept the trade explicitly: a stricter guard rejects more rewrites and ships the template
more often. That is the correct direction — this seam is built to fail closed, and
`_headline_figures_survive` already exists to stop the opposite failure.

Leave the "Measurements confirm …" case alone unless it is cheap. It invents a *claim* rather
than a figure, and a general claim-detector is a research problem, not a guard. Record it as
a known residual instead of half-solving it.

*Tests to add* (`dsl/test_llm.py`) — one per row of the F16 table, all seven, asserting
`source == "template"`. The file's existing `_narrate_with` helper already does this shape.

*Verify:* `_numbers_are_faithful(source="cools 0.16 degC", rewritten="warms 0.16 degF")`
→ `False`, and `uv run pytest src/terrarium/dsl/test_llm.py`.

### 2.4 — Decide what to say about the cells that warm (F13)

Same root cause as F12 — the GBDT's local non-additivity — which is why it sits here and not
in Phase 1. Under a whole-tile +0.15 canopy on `cube_phase9`, 2024-summer, **1,715 of 40,602
cells carry a positive delta, the largest +4.87 °C.** The tile mean is correctly monotonic and
strongly negative, so **no aggregate number is wrong**; but "planting trees warmed this cell
by 5 °C" is not physical at this scale, and it is what somebody sees when they zoom the map
into a warm pixel inside a planting polygon.

Note the ordering dependency: **1.1 makes this visible rather than fixing it.** Once a warming
result is reported as warming, a small polygon over one of these cells produces a coherent
warming brief — which is correct behaviour reporting a model artefact, and a reasonable person
will then ask why.

Three options, in increasing cost:

- **Say so.** One line in `uncertainties` for any planted plan: the emulator is a tree
  ensemble fitted per pixel, a minority of cells move the wrong way, and the polygon mean is
  the quantity to read. Cheap, honest, and consistent with how the rest of this project handles
  its limits.
- **Suppress in the rendering only.** Clamp or flag positive deltas in the map layer for a
  planting scenario, while the served numbers keep them. Risky: it hides a real model output in
  the one place a user looks hardest, and F15 exists because a template quietly rewrote a sign.
  **Not recommended.**
- **Constrain the model** — monotone constraints on the canopy features would make added canopy
  monotonically non-warming by construction. This is approach **C** from 2.1, the same
  intervention fixing the same root cause, and the same reason it is a phase rather than a fix.

**Recommendation: the first, now; fold the third into whatever answers D-a.** If C is ever
taken, F12 and F13 close together.

*Verify:* `brief_for`'s `uncertainties` names the per-cell behaviour for any planted plan; and
re-run the whole-tile +0.15 count after any model change (1,715 cells today is the baseline).

---

## Phase 3 — Harden the front door

**Findings:** F21 (S2), F23 (S2), F20 (S3), F22 (S3). Effort: a day for all four.

All four are on the unauthenticated surface, none produces a wrong number, and all four are
cheap. Grouped so the API is touched once.

### 3.1 — Cap what one request can buy (F21)

Today: a 40 MB, 1,000,000-vertex polygon is simulated in 5.78 s; a 100 MB body is read and
parsed before `extra="forbid"` rejects it; 730 ms of CPU per 150-byte request; the middleware
stack is `['CORSMiddleware']` and nothing else.

- **A body-size limit** in the ASGI stack. Anything above ~1 MB is not a polygon a person drew.
- **A vertex/part cap** in `api/geometry.py:mask_from_geojson`, before the transform. The mask
  is 40,602 cells whatever the polygon's resolution, so refusing above ~10,000 vertices costs
  a user nothing and is a `GeometryError` → 422, consistent with every other refusal there.
- **Decide on rate limiting.** A21 added it to a route that no longer exists. For the HF
  Spaces target, 730 ms per anonymous request is the whole free-tier container. This is a
  deployment decision, not a code one — but it should be made deliberately rather than by
  omission, and the vertex cap plus a body limit may be enough on their own.

*Verify:* the 1,000,000-vertex ring → 422; the 100 MB body → rejected before parsing;
`POST /simulate` with a normal polygon still 200 in < 0.5 s.

### 3.2 — Bound the geometry recursion (F20)

`api/geometry.py:33-62` — `_as_geometry` recurses with no depth limit; 2,000 deep is a 500 for
a ~50 kB body. Pass a depth counter and raise `GeometryError` past 2 or 3. No drawing library
nests deeper than one Feature inside one FeatureCollection, which is the whole reason the
unwrapping exists.

*Verify:* a 2,000-deep nest → 422; a normal `Feature`-wrapped polygon still 200.

### 3.3 — A validation failure must report as 422, not 500 (F22)

`NaN`/`Infinity` in the body makes FastAPI's own 422 unserialisable, because the error echoes
the rejected input. Set `allow_inf_nan=False` on the float fields in
`api/schemas/simulate.py:34,44` (and the DSL's own float fields), so the error carries no
`nan` to serialise. A `RequestValidationError` handler that scrubs non-finite floats is the
belt-and-braces version; the field setting is the one-liner.

*Verify:* raw body `{"geometry": {…}, "canopy_fraction_added": NaN}` → **422 naming the
field**, on both `/simulate` and `/plan`.

### 3.4 — Close the credentialed-wildcard CORS hole (F23)

`api/main.py:47-51`, `config.py:173-175`. Three changes, all small:

- `allow_credentials=False` — nothing in this project uses credentials, and that is what makes
  the wildcard dangerous.
- A field validator on `cors_origins` refusing `"*"`, with a message saying to list the origin.
  Someone deploying to HF Spaces will reach for `'["*"]'`; the refusal should tell them what to
  do instead.
- Narrow `allow_methods` to `["GET", "POST", "OPTIONS"]`. It currently advertises DELETE, PUT
  and PATCH on an API that implements none of them.

*Verify:* `TERRARIUM_CORS_ORIGINS='["*"]'` fails to start with a readable message; a preflight
from `http://localhost:5173` still returns `access-control-allow-origin`; the web app still
loads against a locally running API.

---

## Phase 4 — Artefact integrity

**Findings:** F9 (S2), F10 (S2), F11/F27 (S3). Effort: F9 is one line each side; F10 is a
build; F11 is a deliberate session.

Last of the substantive phases because F10 and F11 need the network, and the whole point of
the known-good-Zarr rule is not to depend on that at a bad moment.

### 4.1 — Stop cubes losing their CRS on a round-trip (F9)

The mechanism, confirmed line by line:

- `state/cube.py:407-409` sets `attrs["crs"]` **after** `write_crs`, which is why a fresh
  `build_tile.py` cube is correct.
- `store.py:152-160` (`open_cube`) restores the CRS via `ds.rio.write_crs(ds.attrs["crs"])`,
  and `write_crs` **moves** the CRS out of `attrs` into a `spatial_ref` coordinate.
- `store.py:124-134` (`write_cube`) then drops `spatial_ref` as non-serialisable and writes
  `attrs["grid"]` — a JSON blob that *does* contain the CRS — but **never re-writes
  `attrs["crs"]`**.

So any open→write cycle destroys it, and `open_cube` cannot recover it. Fix both sides:

- `write_cube`: `ds.attrs["crs"] = grid.crs` alongside the `attrs["grid"]` assignment.
- `open_cube`: fall back to `json.loads(ds.attrs["grid"])["crs"]` when `attrs["crs"]` is absent
  — this is what repairs `cube_phase9.zarr` **without a rebuild**, since its grid blob is intact.

Then add a CRS assertion to `validate_windows` or `runtime.load_runtime`, so the
refuse-a-bad-artefact rule covers this too. Blast radius today is nil (the API derives its
grid from `config.py` and nothing in `api/` or `cores/` reads `.rio.crs`), which is exactly
why it went unnoticed and why the assertion matters more than the repair.

*Tests to add* (`state/test_store.py`): write → open → write → open, and assert `.rio.crs`
survives all four hops. That round trip is the test that was missing.

*Verify:* `python -c "import xarray, rioxarray;
print(xarray.open_zarr('data/processed/cube_phase9.zarr').rio.crs)"` → `EPSG:32643`, not `None`.

### 4.2 — Build a cube that holds the validated window (F10) — **needs D-c**

No cube on disk holds **2025-winter**, the only window the air core's validation result
(MAE 40.6 vs null 51.0, corr +0.53, 53 stations) was ever measured on. So the headline air
number cannot be reproduced here and the API cannot serve the window whose air results are
defensible. `window_years` already defaults to `[2023, 2024, 2025]`; the data was simply never
built on this machine.

```bash
uv run python scripts/build_tile.py --out data/processed/cube_v3.zarr
uv run python scripts/inspect_cube.py --zarr data/processed/cube_v3.zarr --per-window
# only if every variable-window is populated:
#   move TERRARIUM_SERVE_ZARR_STORE to cube_v3.zarr
```

Budget **2–3× longer than CLAUDE.md implies** — see F28: the catalogue's own four-window
builds took 484 s and 671 s, not 4 × 70 s. Six windows on a good connection is plausibly
15–20 minutes. Overpass was answering `504 Gateway Timeout` throughout the audit, so check it
is up before starting. `--out` a new path, never over `cube_phase9.zarr`.

**D-c is whether this is worth doing before a demo.** It unblocks reproducing the air
validation and closes A35's substance for good; it also costs a network build with a flaky
upstream. If the answer is no, say so in the audit rather than leaving F10 looking unattended.

### 4.3 — One deliberate fault-injection session (F11, F27)

The STAC retry/backoff, SAS-token expiry, partial-write and interrupt paths are simultaneously
the 22 % of `ingest/client.py` that coverage does not reach, untestable without the network by
design, and the thing this machine's notes record as flaky. F25 and F26 came out of one
accidental Overpass outage; a deliberate hour would be worth more than the rest of Phase 5
combined.

Run 3.14–3.19 against a **copy** of a cube, never a survivor: corrupt the cached WorldPop
file; point `stac_url` at a dead host and confirm three attempts with doubling delay; point
Overpass at a dead host; interrupt a build mid-write and confirm the target is detectably
invalid; run a failing build with `--out` at an existing good Zarr and confirm it refuses or
writes elsewhere.

*Verify:* each of 3.14–3.19 has a recorded result in TEST_PLAN.md — pass or fail, but not blank.

---

## Phase 5 — Small fixes and hygiene

**Findings:** F17, F18, F19, F26, F4, F6, F8, F28, F29, F30, and closing out F3, F5, F24.
Effort: a day for all of it, and most of it is one line each.

Grouped last because none of it changes a number, and separated so it can be landed as one
tidy-up commit without touching anything above.

### Code

| # | Finding | Change |
|---|---|---|
| 5.1 | **F17** | Give the planner adapters the narrator's budget: `GeminiAdapter.timeout_s` 20→8, `GroqAdapter.timeout_s` 30→8 (`llm.py:86,167`), or a budget that divides across the fallback chain. State it next to `NARRATOR_TIMEOUT_S` so the two cannot drift again. Today one `/plan` can hold a worker 50 s |
| 5.2 | **F18** | `extra="forbid"` on `Plan`, `PlantTrees` and `RestrictVehicles`. `PlanRequest` and `SimulateRequest` already do this and their comment explains why; `Plan` is the model an **LLM** emits, so it needs it most. Also catches a misspelled `tree_counts` |
| 5.3 | **F19** | `validate.py:176`: "air quality" → "locally-generated PM2.5". `web/src` is already disciplined about this and `AirPanel.test.tsx:68` enforces it — add the same assertion in `dsl/test_validate.py` |
| 5.4 | **F26** | Catch the transport error in `build_air_layers.py` and print one line + exit 1, like every other script. The README already documents the 504 as Overpass's expected failure mode — the script should say what the README says, not raise a traceback. **D-b:** whether `--out` keeps defaulting to in place. A27 closed the destructive half correctly and the docstring's argument is sound, so **recommendation: keep the behaviour and fix the two docs that disagree with it** (this plan's 9.2 and CLAUDE.md's command note) |
| 5.5 | **F4** | Delete the `src/terrarium/dsl/observe.py` entry from `[tool.ruff.lint.per-file-ignores]`. The file went with the citizen-observation feature on 2026-08-07; a stale ignore silently widens if that name ever returns |
| 5.6 | **F6** | `Hero.tsx:28` and `ScrollChrome.tsx:13` restate the bbox and `EPSG:32643` as literals while `/health` serves `tile.bbox` and `tile.crs`. Read them from `/health`, or accept the duplication and comment *why* — but not silently, in the one constant CLAUDE.md says lives in `config.py` and nowhere else |
| 5.7 | **F8** | Cap `pydantic<3` in `pyproject.toml:13`. `planetary-computer` uses class-based `config`, removed in Pydantic V3, and the current pin is `>=2.9` with no upper bound — so a V3 release breaks every ingest path with an import error. Regenerate `requirements.txt` after |
| 5.8 | **F30** | Run `ruff format src/ scripts/` once (31 files), then add `ruff format --check` to CI's lint step. Land it as its **own commit** so the formatting diff never mixes with a behaviour change. Or drop the command from README:231 if the project does not want it enforced — but not both, which is the state today |

### Docs

| # | Finding | Change |
|---|---|---|
| 5.9 | **F29** | CLAUDE.md:475-477: delete the orphaned *"The single exception"* — an edit removed the exception and left its opening clause with no predicate, in the paragraph stating the zero-budget claim |
| 5.10 | **F29** | AUDIT.md: strike **A12** through as **withdrawn**, alongside D19/D20. It documents a missing test for the voice feature, which was removed 2026-08-07, and its stated mitigation describes a panel that no longer exists. It is currently the audit's only apparently-open entry and nobody can act on it. While there: A27–A32 mark closure in body text rather than headers, so a header grep reports six false positives — move the marker into the headers |
| 5.11 | **F28** | Replace "~70 s per window" with the measured range. The catalogue's own builds: 484.3 s and 671.2 s for four windows = 121 s and 168 s per window, statics included. It matters because it is what somebody budgets a pre-demo rebuild against |
| 5.12 | **F9 follow-up** | Write the `train_thermal.py` worked-intervention anchor into CLAUDE.md beside the ~0.51/~0.13 °C figures: **+30 % canopy on built-up cells within 1,000 m of 31.5163 N, 74.3403 E**. The figures reproduce exactly (−0.510 / −0.131); only the configuration was missing, which is why Phase 4 initially recorded them as unreproducible |
| 5.13 | **F5, F3, F24** | Close out with a decision rather than a change. **F5:** three modules lack an adjacent `test_*.py` but are covered 78–100 % elsewhere — either add the files or note the deliberate exception to the convention. **F3:** the 11 npm advisories are unreachable (deck.gl's glTF/3D-tiles/texture paths, which this project does not load); record that and re-check when deck.gl next moves. **F24:** the six browser checks need one manual pass with the console open — schedule it before a demo, since it is the largest unverified surface after `ingest/` |

---

## Verification: the full regression set

Run this after every phase, not only at the end. Phase 0 must pass before any of it means
anything.

```bash
uv run ruff check src/ scripts/          # clean today; keep it clean
uv run ruff format --check src/ scripts/ # only after 5.8
uv run mypy                              # clean today; 67 files
uv run pytest                            # 434 tests today; each phase adds to this
PYTHONPATH=ci uv run pytest -p no_network -q   # the rule that matters most
cd web && npm run lint && npm run test && npm run build   # 92 tests, tsc -b
```

Then the live checks the audit used, against a server with a cube:

```bash
curl -s localhost:8000/health                      # 200
curl -s localhost:8000/cube/summary                # 200, four windows
curl -s localhost:8000/plan/presets                # 200, five presets
# F14
curl -s -X POST localhost:8000/plan -H 'Content-Type: application/json' \
  -d "{\"geometry\":$GEOM,\"text\":\"plant $(python -c 'print("9"*309)') trees\"}"   # 422
# F15 — the one-cell warming polygon
# F22
curl -s -X POST localhost:8000/simulate -H 'Content-Type: application/json' \
  -d "{\"geometry\":$GEOM,\"canopy_fraction_added\":NaN}"                            # 422
```

**Done means:** every finding in the TEST_PLAN log is `fixed`, `withdrawn`, or `accepted with
a reason` — and none is still `open` without one. An accepted finding with the reasoning
written down is a finished finding. An open one with nothing beside it is the only failure
state this plan has.
