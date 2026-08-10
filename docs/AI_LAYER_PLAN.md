# The AI layer — five phases past the narrator

**Status: planned, none of it built.** This document is what to build and why. Nothing here
exists in the tree yet, and no dependency has been added for it. When a phase lands, add its
row to `IMPLEMENTATION_PLAN.md`'s phase table and its decision to the register there; this
file is the design, that file remains the record.

---

## Why

`dsl.llm.narrate` is the only place a model touches the product today. It rewords
`explain.plain_summary` and is defended by two post-checks on its own output —
`_numbers_are_faithful` and `_headline_figures_survive`. That is D24, and it is the right
shape: **the model may reword a number, never source one.**

This plan extends the model's reach five ways without loosening that rule. Every phase keeps
the same division of labour:

> Something deterministic computes the numbers. The model chooses *what to try*, *what to
> say*, or *which language to say it in*. A post-check on the model's own output rejects it
> if it drifted.

The centrepiece is a search agent that runs the simulator in a loop. D17 already named this
as its own reopening condition — *"If agents later need to choose between interventions and
iterate, that is a real graph and this reopens"* — so LangGraph arrives on a decision the
register anticipated, not as drift.

**A note on ambition.** What makes this project serious is the hindcast that reports its own
2.5x over-prediction and the leave-one-station-out validation that beats a null model in
winter and says so about summer. Adding LLM calls does not add weight; it dilutes unless
each one is checkable. Phase A is worth building because *search over a validated simulator*
is a real technique. Phases B–E are good product. Neither is why the project is impressive,
and the README should keep saying so.

---

## Decisions to reopen

Both must be written into the register **before** the code that depends on them lands, not
after. A rule that quietly stops holding is worse than a rule that was deliberately changed.

### D25 — where the model may be reached (replaces D18)

D18 says the LLM lives in exactly one file. A tool-calling graph is not one file, and
pretending otherwise would mean either a 2,000-line `llm.py` or a rule everyone ignores.

Replace it with a narrower rule that is still enforceable:

> **The model is reachable from exactly four modules** — `dsl/llm.py`, `agent/nodes.py`,
> `evidence/answer.py`, `policy/extract.py` — **and every call site carries a post-check on
> the model's own output.** A new call site is a new decision, and a call site with no
> post-check is a bug.

The post-check is the part that matters. D18's real content was never "one file", it was
"the model's output is never trusted"; one file was the cheap way to enforce it while there
was one caller.

### D26 — how the agent proposes geometry (preserves D6)

D6 keeps geometry out of a `Plan`. The agent searches the whole tile, which sounds like it
needs geometry, and the naive reading — let the model emit polygon coordinates — is both a
D6 violation and technically bad: a model asked for coordinates on a 201x202 grid
hallucinates them and burns the step budget on polygons that select no cells.

> **The model never emits geometry. It selects from geometry the grid layer generated.**
> `api/candidates.py` computes a fixed lattice of candidate regions from the cube; the model
> returns a `region_id` and plan levers. The region id lives on the search state, not on the
> `Plan`, so a `Plan` still carries no coordinates and D6 is untouched.

---

## Phase A — the intervention search agent

**Goal:** *"Get 1 °C off somewhere in this tile for under $500k, reaching as many people as
possible."* The agent searches the tile, runs the real cores, and returns the winning plan,
its polygon, and the trace of everything it tried with the reason each failure failed.

### A1. The candidate lattice — `src/terrarium/api/candidates.py`

Deterministic, no model. Lives in `api/` for the same reason `measure.py` does: it is the
layer that owns the grid.

Tile the 201x202 grid into blocks of 20x20 cells (2 km x 2 km) — about 110 candidates. For
each, compute:

| Field | Source |
|---|---|
| `plantable_canopy_m2` | `cores.thermal.simulate.effective_fraction`, block-summed |
| `mean_lst_c` | cube, this window |
| `population` | cube, summed (extensive — never averaged) |
| `emission_g_s` | cube, summed |
| `max_trees` | `plantable_canopy_m2 // TREE_CANOPY_M2` |
| `geometry` | GeoJSON polygon, WGS84, for the UI and for `/simulate` |

**Compute `effective_fraction` once over a full-tile mask, then block-reduce.** Calling
`measure_polygon` per candidate is ~110 full-grid passes for an answer one pass already
contains. The full-tile call returns per-cell headroom with water and no-data already
zeroed, which is exactly what needs summing.

A candidate's mask is built from row/column slices directly — no rasterisation. The GeoJSON
is for the client and for handing the winning region back to `/simulate`; it needs a
`Transformer.from_crs(grid.crs, "EPSG:4326")`, the inverse of what `geometry.py` already
does.

Regions must be mergeable with their neighbours, so the agent can grow one rather than being
stuck at 2 km granularity.

### A2. The control — `src/terrarium/agent/baseline.py`

A greedy deterministic search over the same candidates: rank by an opportunity score
(`plantable_canopy_m2 x population` for a cooling objective), simulate the top few, keep the
best.

**This is not a fallback, it is the control.** Every agent result reports the baseline's
score beside its own. A search result with nothing to beat is a claim, and this project does
not ship claims. It is also what makes the agent falsifiable: if the model never beats
greedy, that is a finding worth knowing and worth publishing.

### A3. The graph — `src/terrarium/agent/graph.py`

```
parse_goal ──▶ survey ──▶ propose ◀───────────────┐
  (LLM)        (pure)      (LLM)                  │
                             │                    │
                             ▼                    │
                          check ─── refused ──────┤   the refusal is the feedback signal
                          (pure)                  │
                             │ ok                 │
                             ▼                    │
                           run ──▶ score ──▶ decide
                         (cores)   (pure)      │
                                               ├─ budget spent / target met ──▶ report (LLM)
                                               └─ otherwise ─────────────────────┘
```

| Node | Kind | What |
|---|---|---|
| `parse_goal` | LLM | natural language → `Objective` (metric, target, budget, window). Falls back to a default objective on any failure |
| `survey` | pure | rank candidates; compute the baseline |
| `propose` | LLM | given candidates + everything tried, emit `region_id` + `Plan`. The only creative step |
| `check` | pure | `measure_polygon` + `dsl.validate.resolve` |
| `run` | pure | thermal + equity + air cores |
| `score` | pure | `Objective` → scalar; update `best` |
| `decide` | pure | conditional edge on budget and target |
| `report` | LLM | narrate the search, under `narrate`'s existing guards |

**The refusal loop is the whole idea.** `dsl.validate.resolve` already raises `PlanError`
carrying the arithmetic — *"5,000 trees need 0.125 km² of crown at 25 m² each, but this
0.031 km² polygon…"*. That string goes into `tried` and the edge routes back to `propose`.
The validator you built as a product feature turns out to be a usable reward signal with no
extra work, and that is the most interesting thing about this phase.

**The LLM appears in three nodes and nowhere else.** Every figure in `tried` and `best` came
out of a core. `report` imports `_numbers_are_faithful` and `_headline_figures_survive` from
`dsl/llm.py` — it does not reimplement them.

### A4. Budget and streaming

`SearchBudget`: 10 simulate calls, 20 LLM calls, 60 s wall clock. `/simulate` measures 0.84 s
warm, so the cores are ~8 s and the model is the rest.

**At that latency streaming is not optional.** `POST /agent/search` returns SSE, one event
per node transition. A 40-second spinner is a broken feature; a 40-second visible search is
the feature.

### A5. Files

| Path | What |
|---|---|
| `src/terrarium/api/candidates.py` | the lattice (A1) |
| `src/terrarium/agent/state.py` | `SearchState`, `Objective`, `Candidate`, `Attempt`, `SearchBudget` |
| `src/terrarium/agent/objective.py` | pure scoring — cooling, person-degrees, cost-effectiveness |
| `src/terrarium/agent/baseline.py` | the greedy control (A2) |
| `src/terrarium/agent/nodes.py` | node functions; the only file here that talks to a model |
| `src/terrarium/agent/graph.py` | LangGraph wiring, budget, checkpointer |
| `src/terrarium/api/routes/agent.py` | `POST /agent/search` (SSE), `GET /agent/search/{id}` |
| `src/terrarium/api/schemas/agent.py` | request/response contracts |

Dependency: `langgraph>=0.2`, with a comment saying why the planner still does not use it.

### A6. UI — `web/src/panels/AgentPanel.tsx`

Goal box; live step trace with each attempt as *proposed → refused, with the reason* or
*proposed → scored*; the baseline's score beside the agent's; an **Apply this plan** button
that writes the winning polygon into `useDrawnPolygon` and the levers into the existing
sliders. Candidate regions render as a faint deck.gl `PolygonLayer` with the region under
evaluation highlighted.

The panel belongs in the results sheet on mobile and the right rail at `lg` — the layout
split already exists in `App.tsx`.

---

## Phase B — ask the evidence

**Goal:** *"Why is the cooling divided by 2.5?"* answered from the repo's own record, with
citations checked to exist.

The corpus is already unusually good, because this project writes down what it does not
know: `IMPLEMENTATION_PLAN.md` (1,714 lines including the D-register), `AUDIT.md` (1,350),
`USER_GUIDE.md`, `CLAUDE.md`, `README.md`. Roughly 240 KB, ~60k tokens.

**No vector store.** At this size embeddings are infrastructure that buys nothing over BM25,
and they make citation harder rather than easier.

1. `evidence/corpus.py` — split each markdown file into sections by heading. Parse the
   decisions register into `Decision(id, title, body)` rows: "why" questions land there, and
   a D-entry is the natural unit of retrieval.
2. `evidence/retrieve.py` — BM25 over sections. Top-k into the prompt.
3. `evidence/answer.py` — the model answers with every claim tagged `file#heading`.
4. **The guard: every citation must resolve to a section that exists.** One unresolvable
   citation rejects the whole answer and falls back to returning the retrieved sections
   verbatim. Post-check on the output, not an instruction in the prompt — same discipline as
   `_numbers_are_faithful`, for the same reason.

Route: `POST /evidence/ask`. UI: a drawer, plus an "explain this" affordance on the 2.5x
correction and the air-validation figures where they already appear on screen.

---

## Phase C — Urdu briefs

`dsl/planner.py` reads Urdu. `explain.py` answers only in English. You accept a Lahore
resident's sentence and hand back a language they may not read.

- `explain.plain_summary` stays the English source of truth. New `llm.translate(plain,
  target)` mirrors `narrate` exactly — same chain shape, same never-raises contract, same
  exclusion of `verdict` and `caveat`.
- **Reuse `dsl/planner.py:_normalise` (line 116) and `_URDU_DIGITS` (line 67)** to fold
  Eastern Arabic-Indic digits *before* `_numbers_are_faithful` compares numerals. Without
  the fold the guard sees ۵۰۰۰ as no number at all, every translation passes vacuously, and
  the guard is decorative. **Do not write a second folding table** — one of them will rot.
- API: `?lang=ur` on `/simulate` and `/plan`. `source` reports `langchain:<model>:ur`.
- **The UI needs a font.** `index.css` self-hosts Geist, which has no Urdu coverage — add
  `@fontsource/noto-nastaliq-urdu`, set `dir="rtl"` on the brief panel, and give
  `BriefDocument.tsx`'s print path the same treatment or the PDF comes out as boxes.

Smallest phase here, and the one with the clearest user.

---

## Phase D — real policy documents

**Goal:** *"Here is what the city's own published plan would deliver on this tile."* That
turns a sandbox into an instrument.

### Sources — verified reachable

Checked during planning, August 2026:

| Document | Status |
|---|---|
| [Punjab Clean Air Action Plan](https://epd.punjab.gov.pk/system/files/Annex%20D2%20Punjab%20Clean%20Air%20Action%20Plan_0.pdf) | **fetched** — 42 pages, 695 KB, no auth |
| [Punjab Clean Air Policy, gazette notification](https://epd.punjab.gov.pk/system/files/230419%20Gazette%20Notification%20Punjab%20Clean%20Air%20Action%20Policy%20(1).pdf) | same host, expected fine |
| [World Bank Punjab Clean Air Program P508222](https://documents1.worldbank.org/curated/en/099030425204513573/pdf/P508222-5dce0c7a-e69b-435b-9570-bc1c2332de6e.pdf) | carries a 35 % PM2.5 reduction target for Lahore Division |
| [LDA Master Plan Lahore Division 2050](https://lda.gop.pk/resource-center/lahore-master-plan) | **403 Forbidden** — not usable |

The Clean Air Action Plan contains a structured *Policy measure / Proposed action / Approx
cost / Timeline / Responsibility* table, Euro emission standards, fleet-turnover measures,
an urban tree-cover section, and — most usefully — Lahore PM2.5 source apportionment:
**diesel 28 % + two-stroke exhaust 8 % = 36 % of measured high PM2.5.** That maps straight
onto `emission_fraction_removed` and becomes a preset with a government citation behind it,
which is a much better provenance than the literature figures in `dsl/library.py`.

### The finding that shapes the design

**Naive text extraction produces garbage on this document.** The font encoding shreds words:

```
"The P unja b Clea n A ir Act ion P la n En vir on me nt Protecti o n Depart m ent"
```

So do **not** build a pypdf → text → LLM pipeline. Hand the raw PDF bytes to a model with
native PDF understanding and the extraction step disappears. This is the strongest single
argument for Gemini in this plan.

### Files

| Path | What |
|---|---|
| `scripts/ingest_policy.py` | download to `data/raw/policy/`, verify `Content-Length`, `.partial` rename, record URL + sha256 + fetched-at in DuckDB — **reuse the WorldPop discipline**, the failure mode is identical |
| `src/terrarium/policy/schema.py` | `PolicyMeasure`: title, sector, quantified target, target year, source page, **verbatim quote** |
| `src/terrarium/policy/extract.py` | PDF bytes → `list[PolicyMeasure]` |
| `src/terrarium/policy/to_plan.py` | `PolicyMeasure` → `Plan`, where the two levers can express it |
| `scripts/extract_policy.py` | CLI: extract, write to DuckDB, print the coverage table |

**Guard:** every extracted measure carries a verbatim quote that must be findable in the
document's de-spaced text. A measure whose quote does not match is dropped.

**Report the miss rate.** Most measures — fuel sulfur limits, catalytic converters, CNG
policy — cannot be expressed as a canopy fraction or an emission fraction. The output says
*"N measures extracted, M expressible on this tile"* rather than silently keeping the M.
That gap is a finding about the scope of the two levers, not a failure of the extractor, and
burying it would be the same mistake as quoting a cooling figure without its hindcast
correction.

---

## Phase E — explain the map

Templates can state *how much* cooling landed. Nothing deterministic can say *where and
why*, because the pattern differs every run — which is exactly the gap a model should fill.

1. `api/explain_spatial.py`, deterministic — segment the delta field into where cooling
   landed and where it did not; join per-region cube attributes: NDVI headroom, landcover
   class, population decile, distance from the polygon edge, spillover. Reuse
   `cores/equity.py:benefit_distribution` for the decile join rather than recomputing it.
2. The model describes the pattern **from that table only**, under the same faithfulness
   guard.
3. UI: tap a region on the ΔLST or ΔPM2.5 overlay → the explanation for that region.

---

## Model choice

| Phase | Model | Why |
|---|---|---|
| **A** (agent loop) | **Groq `openai/gpt-oss-120b`** | Native tool-calling, genuine reasoning with `reasoning_effort`, and throughput that keeps a 10-step loop in seconds. Latency compounds in a cycle in a way it does not in a single call |
| **B, C, D** | **Gemini 3.x Flash** | 1M context (whole evidence corpus, whole 42-page PDF), materially better Urdu than open-weight models, and **native PDF input** — which is what makes Phase D's extraction problem disappear |

**A technical unblock worth knowing.** `GroqAdapter`'s docstring records that
`reasoning_effort="none"` is required, because Groq's `json_object` validator rejects a
generation carrying a `<think>` block. **That constraint is specific to `response_format`
and does not apply to tool calling.** With tools, reasoning and structured output coexist —
so the agent can run a reasoning model at full effort, which the planner path could not.

**Route by task, not just by availability.** `resolve_adapter` currently picks on which key
is set, and `FallbackAdapter` handles a dead key. Add `resolve_adapter(settings, task=...)`
so Phase A prefers Groq and Phases B–D prefer Gemini, with the existing failover intact
beneath both.

**Pin the IDs in `config.py` and check the current model list before building.** This
project already carries the scar: `gemini-2.5-flash` kept advertising `generateContent`
while 404-ing keys issued after its withdrawal, which is why `GroqAdapter` exists at all.
Treat the names above as capability profiles, not literals.

---

## Sequencing

**A → C → B → E → D.**

A is the centrepiece and everything else is easier once the agent package and task routing
exist. C is small, high-value and independent. D is last because it is the only phase with
an external dependency that can rot, and because its value is a demo rather than a
capability.

Each phase is independently shippable and independently revertible.

---

## Before any of this: two blockers

1. **There is no cube and no trained model.** `data/` does not exist in this working tree —
   no `cube.zarr`, no `thermal.txt`. Every phase here can be unit-tested without them, but
   nothing can be verified end to end. Run `scripts/build_tile.py` and
   `scripts/train_thermal.py` first, or accept that "it passes its tests" is the only claim
   available.
2. **Reuse `src/terrarium/api/conftest.py`.** It already builds a structurally honest
   synthetic cube on the real canonical grid and trains a genuine booster on it in about two
   seconds, session-scoped. `synthetic_runtime` is what the agent tests should be written
   against. Do not build a second fixture.

---

## Verification

No test may touch the network. Extend the fake-adapter pattern already in `dsl/test_llm.py`.

| Phase | Tests | Manual |
|---|---|---|
| **A** | `agent/test_objective.py` (pure scoring); `agent/test_baseline.py` (greedy control on the synthetic cube); `agent/test_graph.py` with a scripted fake model — assert the graph **cycles on a refusal**, respects `SearchBudget`, and never returns a plan `dsl.validate` would reject | run a real goal through the UI with the step trace visible; check the agent beats the baseline more often than not |
| **B** | `evidence/test_retrieve.py` (BM25 ranking); `evidence/test_answer.py` — **assert a fabricated citation rejects the whole answer** | ask *"why 2.5x"*, check it lands on the hindcast section |
| **C** | `dsl/test_llm.py` gains an Urdu case — assert ۵۰۰۰ is recognised as 5000 by the folded guard, and that an invented figure still rejects | read an Urdu brief; print it and check the PDF is not boxes |
| **D** | `policy/test_extract.py` against a small committed fixture PDF; `policy/test_to_plan.py` for the mapping and the miss-rate report | `scripts/ingest_policy.py` then `scripts/extract_policy.py`; check extracted measures by eye against the PDF |
| **E** | `api/test_explain_spatial.py` — deterministic segmentation on a synthetic delta | tap a region, check the description matches the map |

Whole-project gates, unchanged:

```bash
uv run pytest
uv run ruff check src/ scripts/
uv run mypy
cd web && npm run test && npm run build
```

Regenerate `requirements.txt` whenever a dependency changes — LangGraph and the Urdu font
are both new:

```bash
uv export --no-hashes --no-dev --no-emit-project --format requirements-txt -o requirements.txt
```

---

## What not to build

Each of these is a default move for "make it a heavier AI project", and each would cost
credibility rather than add it.

- **LLM-as-judge on the narration.** `_numbers_are_faithful` is deterministic and strictly
  stronger. Never trade a proof for a vibe.
- **A model anywhere near `cores/`** — feature engineering, emulating the emulator,
  "AI-enhanced" predictions. Layer 2's purity is the architecture, and it is what lets the
  physics be tested without a network.
- **Chat-with-your-cube**, where the model generates xarray or SQL against the store. That
  is precisely the "model sources a number" failure D24 exists to prevent, wearing a
  friendlier hat.
- **A vector database** for a 240 KB markdown corpus.
