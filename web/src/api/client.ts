/**
 * Typed client for the Terrarium API.
 *
 * These types mirror the Pydantic models in `src/terrarium/api/schemas/`. When a schema
 * changes on the Python side, change it here too — or generate from /openapi.json.
 *
 * Errors carry the API's own `detail` string wherever there is one. That matters most
 * for 422 from /simulate: "the polygon selects no grid cells" is a message the user can
 * act on, and replacing it with "Request failed" throws away the only useful part.
 */

const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export interface TileInfo {
  name: string;
  country: string;
  /** WGS84 [west, south, east, north] */
  bbox: [number, number, number, number];
  /** WGS84 [longitude, latitude] */
  centroid: [number, number];
  crs: string;
  /** The analysis grid every source is resampled into, not any source's native GSD. */
  target_resolution_m: number;
}

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  env: string;
  tile: TileInfo;
}

/** Which axes a cube variable varies along. Mirrors `state.cube.Dims`. */
export type Dims = "space" | "time_space" | "time";

export interface GridInfo {
  crs: string;
  resolution_m: number;
  /** (height, width) — rows then columns. */
  shape: [number, number];
  /** [left, bottom, right, top] in the analysis CRS. */
  bounds: [number, number, number, number];
  /**
   * [west, south, east, north] for map overlay. The *envelope* of the projected grid:
   * a UTM rectangle is not a lat/lon rectangle, so a north-up overlay is off by a
   * fraction of a cell at the corners. Fine at 20 km, and the API says so explicitly.
   */
  bounds_wgs84: [number, number, number, number];
}

export interface LayerResponse {
  variable: string;
  description: string;
  units: string;
  /** null for static variables — elevation does not belong to a window. */
  window: string | null;
  grid: GridInfo;
  /** Always "base64:float32:little:row-major". Checked on decode, never assumed. */
  encoding: string;
  data: string;
  vmin: number | null;
  vmax: number | null;
  valid_fraction: number;
}

export interface VariableSummary {
  name: string;
  units: string;
  dtype: string;
  dims: Dims;
  populated: boolean;
  valid_fraction: number;
  vmin?: number | null;
  vmax?: number | null;
  vmean?: number | null;
}

export interface CubeSummaryResponse {
  crs: string;
  resolution_m: number;
  shape: [number, number];
  windows: string[];
  default_window: string;
  variables: VariableSummary[];
  window_valid_fractions: Record<string, Record<string, number>>;
}

export interface DeltaStats {
  n_cells_changed: number;
  mean_delta_inside: number;
  mean_delta_spillover: number;
  spillover_cells: number;
  min_delta: number;
  max_delta: number;
}

export interface PlausibilityContext {
  /** This window's observed built-minus-tree LST gap: the ceiling on any planting. */
  tree_built_contrast_c: number;
  mean_canopy_added: number;
  linear_expectation_c: number;
  /** null when the contrast is too small to divide by — i.e. winter. */
  ratio_to_linear: number | null;
}

export interface DecileShare {
  /** 1 = least densely populated, 10 = most. */
  decile: number;
  people: number;
  mean_delta_c: number;
  /** Share of total person-degrees. 0.10 is an even share. */
  share: number;
}

/**
 * Who receives the cooling. Deciles hold a tenth of the *population* each, not a tenth
 * of the pixels, so an even distribution is 0.10 everywhere and any skew shows without
 * arithmetic.
 */
export interface Equity {
  deciles: DecileShare[];
  /** "population density" today — no free, keyless deprivation layer exists. */
  stratified_by: string;
  population_covered: number;
  /** Held by the three best-served deciles. Even sharing is 0.30. */
  top_three_share: number;
  /** Always false when `shares_reliable` is false. */
  concentrated: boolean;
  /**
   * False when the plan cools and warms in near-equal measure. Shares divide by the net
   * benefit, so a vanishing denominator makes them explode. Do not draw the bars.
   */
  shares_reliable: boolean;
  /** Net benefit over total temperature movement. 1.0 is pure cooling. */
  net_to_gross: number;
  /** Share of cooling, as degree-cells, landing where nobody lives. */
  uninhabited_fraction: number;
}

/**
 * The modelled change in **locally-generated** PM2.5 — this tile's own roads, not what a
 * monitor reads. The regional background that dominates Lahore's absolute PM2.5 is absent
 * by construction, which is why this block carries a delta and never a level.
 */
export interface Air {
  variable: string;
  units: string;
  stats: DeltaStats;
  /** ~250 m under the winter inversion against ~800 m in summer. */
  mixing_height_m: number;
  wind_speed_ms: number;
  /** Meteorological convention: the direction the wind comes *from*. */
  wind_direction_deg: number;
  emission_fraction_removed: number;
  delta: LayerResponse;
}

/**
 * The result as sentences, written deterministically on the server — no language model,
 * so it cannot restate a figure it did not receive. `uncertainties` is never empty.
 */
/**
 * The dashboard's version of the brief: same numbers, jargon removed.
 *
 * Always present, because it is generated from templates server-side and never depends on
 * a key. `source` is `"template"` on a keyless deployment and `langchain:<model>` when the
 * narrator reworded it — and a rewrite that introduced any figure the template did not
 * contain is rejected server-side, so the numbers here are the template's either way.
 */
export interface PlainSummary {
  /**
   * How big this is, on the tile's own bare-street-to-leafy-street scale. Computed
   * server-side from the numbers and **not** part of what the narrator may rewrite, so a
   * model cannot talk a marginal plan up into a moderate one. `"unrated"` is an
   * air-only plan, whose magnitudes are uncalibrated.
   */
  verdict: "large" | "moderate" | "small" | "marginal" | "unrated" | "none";
  headline: string;
  points: string[];
  caveat: string;
  source: string;
}

export interface Brief {
  headline: string;
  plain: PlainSummary;
  findings: string[];
  uncertainties: string[];
  /** Never "high". Nothing in this project has earned that word. */
  confidence: "low" | "moderate";
  /** Modelled cooling after the 2.5x hindcast correction — the number to quote. */
  expected_cooling_c: number;
}

export interface SimulateResponse {
  variable: string;
  units: string;
  /** The window actually simulated. Never assume it matches what you asked for. */
  window: string;
  season: string;
  stats: DeltaStats;
  context: PlausibilityContext;
  /** Null when the served cube carries no population layer — never render 0 for this. */
  equity: Equity | null;
  delta: LayerResponse;
  /** Null unless the plan removed emissions *and* the cube carries an OSM inventory. */
  air: Air | null;
  brief: Brief;
}

// ------------------------------------------------------------------- the DSL ---

export interface PlantTreesAction {
  kind: "plant_trees";
  tree_count?: number | null;
  canopy_fraction_added?: number | null;
}

export interface RestrictVehiclesAction {
  kind: "restrict_vehicles";
  emission_fraction_removed: number;
}

export type PlanAction = PlantTreesAction | RestrictVehiclesAction;

/** One named intervention, in the terms a person uses. Carries no geometry by design. */
export interface Plan {
  name: string;
  actions: PlanAction[];
  window?: string | null;
  season?: "summer" | "winter" | null;
}

export interface CostEstimate {
  planting_usd: number;
  restriction_usd: number;
  total_usd: number;
  basis: string;
  /** False everywhere. Literature unit costs, good for ranking plans, not a budget. */
  calibrated: boolean;
}

export interface Preset {
  slug: string;
  label: string;
  summary: string;
  /** What it does *not* do. Show it — every preset has one for a reason. */
  caveat: string;
  plan: Plan;
}

export interface PresetsResponse {
  presets: Preset[];
  /** A provider and model, or "rules (no model configured)". */
  planner: string;
}

export interface PlanResponse {
  plan: Plan;
  source: "llm" | "rules" | "preset" | "explicit";
  window: string;
  season: string;
  cells: number;
  area_km2: number;
  canopy_fraction_added: number;
  emission_fraction_removed: number;
  tree_count: number;
  /** What the polygon could hold at all, measured from the cube's canopy headroom. */
  max_trees: number;
  /** Requested canopy over available canopy. Above 1.0 the core caps and delivers less. */
  canopy_utilisation: number;
  cost: CostEstimate;
  notes: string[];
  warnings: string[];
  basis: string;
  /** Post this to /simulate unchanged. */
  simulate_request: SimulateRequest;
}

export interface PlanRequest {
  geometry: GeoJsonPolygon;
  /** Exactly one of these three. */
  text?: string;
  preset?: string;
  plan?: Plan;
  window?: string | null;
}

// ------------------------------------------------------- policy documents (Phase D) ---

/** The sectors Phase D's extraction sorts a measure into. `other` is most of a document. */
export type PolicySector =
  | "transport"
  | "urban_greening"
  | "industry"
  | "waste"
  | "agriculture"
  | "other";

/** One quantified commitment out of a published policy document, verbatim quote included. */
export interface PolicyMeasure {
  title: string;
  sector: PolicySector;
  /** The quantified target as stated, e.g. "35% reduction in PM2.5". Empty for most measures. */
  target: string;
  target_year: number | null;
  source_page: number | null;
  /** Verbatim from the document — checked against it server-side before this ever exists. */
  quote: string;
  document: string;
  document_sha256: string;
}

/** A measure turned into a runnable `Plan`, and the sentence tracing the number back. */
export interface MappedPlan {
  measure: PolicyMeasure;
  plan: Plan;
  basis: string;
  /** True when the document named no figure and this project's own default stands in. */
  assumed: boolean;
}

export interface PolicyMeasureItem {
  measure: PolicyMeasure;
  /** Null when neither lever — a canopy fraction, an emission fraction — can say this. */
  mapped: MappedPlan | null;
}

export interface PolicyMeasuresResponse {
  measures: PolicyMeasureItem[];
  expressible: number;
}

export interface GeoJsonPolygon {
  type: "Polygon";
  /** Rings of [longitude, latitude]. The first ring is the exterior. */
  coordinates: [number, number][][];
}

export interface SimulateRequest {
  geometry: GeoJsonPolygon;
  canopy_fraction_added: number;
  /** 0 means the plan says nothing about traffic, and no air block comes back. */
  emission_fraction_removed?: number;
  window?: string | null;
}

// --------------------------------------------------- the search agent (Phase A) ---

/**
 * One block of the lattice the agent chooses from.
 *
 * The model never emits coordinates (D26) — it names a `region_id` from this list, and the
 * geometry here is what the map draws and what "Apply this plan" posts to `/simulate`.
 */
export interface Candidate {
  region_id: string;
  row0: number;
  row1: number;
  col0: number;
  col1: number;
  cells: number;
  area_m2: number;
  /** Canopy this block can still take, from the thermal core's own per-cell headroom. */
  plantable_canopy_m2: number;
  max_trees: number;
  /** Null where the block is entirely no-data. */
  mean_lst_c: number | null;
  /** Summed — population is extensive and is never averaged. */
  population: number;
  emission_g_s: number;
  geometry: GeoJsonPolygon;
}

export interface CandidatesResponse {
  window: string;
  block_cells: number;
  candidates: Candidate[];
}

export interface Objective {
  metric: "cooling" | "person_degrees" | "cost_effectiveness";
  /** Compared against the hindcast-**corrected** figure, never the raw model output. */
  target_cooling_c: number | null;
  max_cost_usd: number | null;
  window: string | null;
  description: string;
}

export interface Outcome {
  /** Raw model output, negative for cooling. */
  mean_delta_inside_c: number;
  /** After the 2.5x hindcast correction, stated positive. The figure to quote. */
  expected_cooling_c: number;
  person_degrees: number;
  people_reached: number;
  cost_usd: number;
  tree_count: number;
  area_km2: number;
  delta_pm25: number | null;
}

/**
 * One trip round the loop, kept whether it worked or not.
 *
 * A refused attempt matters more than a scored one for the trace: `reason` is the
 * validator's own arithmetic, and it is what the next proposal was conditioned on.
 */
export interface Attempt {
  step: number;
  region_ids: string[];
  plan: Plan;
  status: "scored" | "refused";
  /** Only two producers: the model, or the deterministic greedy control. */
  proposer: "model" | "greedy";
  reason: string | null;
  score: number | null;
  outcome: Outcome | null;
}

export interface SearchResult {
  search_id: string;
  goal: string;
  objective: Objective;
  window: string;
  season: string;
  best: Attempt | null;
  /** The deterministic greedy control. **Not a fallback** — it is what the agent must beat. */
  baseline: Attempt | null;
  beat_baseline: boolean;
  tried: Attempt[];
  simulations_used: number;
  llm_calls_used: number;
  elapsed_s: number;
  stopped_because: string;
  /**
   * The search narrated. **Empty when the model could not write it or drifted** — the
   * numbers above came from the cores and are the result either way, so render them
   * regardless.
   */
  report: string[];
  /** The provider chain, or "unavailable". */
  report_source: string;
}

/** What `GET /agent/search/{id}` returns. The stream is the primary interface. */
export interface SearchResponse {
  result: SearchResult;
}

export interface SearchEvent {
  node: string;
  message: string;
  attempt: Attempt | null;
  /** Present on the final event only. */
  result: SearchResult | null;
}

export interface SearchBudget {
  max_simulations: number;
  max_llm_calls: number;
  wall_clock_s: number;
}

export interface SearchRequest {
  goal: string;
  window?: string | null;
  budget?: SearchBudget;
}

// --------------------------------------------- explain the map (Phase E) ---

/**
 * One block of the lattice, and what the cube says was in it.
 *
 * Every field is measured server-side. The description in `SpatialExplanation.summary` may
 * only reorganise these figures into prose — a rewrite carrying a number that is not in
 * this table is rejected before the response is built.
 */
export interface RegionExplanation {
  region_id: string;
  /** Hindcast-corrected and positive, like every other cooling figure the product ships. */
  expected_cooling_c: number;
  canopy_added: number;
  headroom_km2: number;
  residents: number;
  /** 1 = least densely populated tenth of the tile's residents, 10 = most. Null if unknown. */
  population_decile: number | null;
  tree_cover_fraction: number;
  water_fraction: number;
  inside_polygon: boolean;
  /**
   * Changed without being drawn on. Real physics — the 500 m neighbourhood terms carry
   * cooling past the polygon's edge — and the part of the map users most often call a bug.
   */
  spillover: boolean;
}

export interface SpatialExplanation {
  window: string;
  regions: RegionExplanation[];
  /** Null when no model was reachable. The regions are still the answer. */
  summary: string | null;
  points: string[];
  /** "table" when no model wrote the prose, which is a working deployment. */
  source: string;
}

// -------------------------------------------------- ask the evidence (Phase B) ---

/** One section of the project's own documentation. `anchor` is `file.md#heading-slug`. */
export interface Section {
  file: string;
  heading: string;
  anchor: string;
  body: string;
  level: number;
}

export interface Citation {
  anchor: string;
  file: string;
  heading: string;
}

/**
 * An answer from the repository's own record.
 *
 * Returned **only when the citation guard passed**. There is no half-answer: a model that
 * cited something it was not shown has its whole answer discarded, and that arrives as a
 * 502 whose `detail` carries `rejected_citations` and the passages — see `EvidenceFailure`.
 */
export interface Answer {
  question: string;
  answer: string;
  /** Every one of these resolves to a passage below. Checked server-side. */
  citations: Citation[];
  /** What was retrieved. Always present, so the answer can be checked against it. */
  passages: Section[];
  /** The provider chain that wrote it. */
  source: string;
}

/**
 * The `detail` of a failed `/evidence/ask`.
 *
 * 422 when the corpus has nothing for the question; 502 when a model answered and the
 * answer was thrown away; 503 when none is configured. The passages ride along either
 * way, so a client can still show the evidence rather than only an apology.
 */
export interface EvidenceFailure {
  message: string;
  /** Non-empty when the answer was discarded for citing something it was not shown. */
  rejected_citations: string[];
  passages: Section[];
}

/** An API error that carries the server's own explanation. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function parseError(response: Response, path: string): Promise<ApiError> {
  try {
    const body: unknown = await response.json();
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return new ApiError(response.status, detail);
    // FastAPI's own validation errors arrive as a list of objects.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first.msg === "string") return new ApiError(response.status, first.msg);
    }
    // `/evidence/ask` sends a structured detail so a rejected answer can name the anchors
    // it invented. Flattened into the message rather than dropped: "the model cited
    // docs/INVENTED.md#x" is the whole reason the caller is seeing an error at all.
    if (detail && typeof detail === "object") {
      const failure = detail as Partial<EvidenceFailure>;
      if (typeof failure.message === "string") {
        const fabricated = failure.rejected_citations?.length
          ? ` (${failure.rejected_citations.join(", ")})`
          : "";
        return new ApiError(response.status, `${failure.message}${fabricated}`);
      }
    }
  } catch {
    // Fall through to the status line — the body was not JSON.
  }
  return new ApiError(
    response.status,
    `${response.status} ${response.statusText} from ${path}`,
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) throw await parseError(response, path);
  return (await response.json()) as T;
}

function query(params: Record<string, string | undefined>): string {
  const pairs = Object.entries(params).filter(
    (entry): entry is [string, string] => entry[1] !== undefined,
  );
  return pairs.length ? `?${new URLSearchParams(pairs).toString()}` : "";
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  cubeSummary: () => request<CubeSummaryResponse>("/cube/summary"),

  /** One variable as a raster. `window` is ignored by the API for static variables. */
  layer: (name: string, window?: string) =>
    request<LayerResponse>(`/cube/layer/${name}${query({ window })}`),

  /**
   * `lang` is a *request*, not a guarantee. The server answers in English whenever no key
   * is configured or the translation would have invented a figure, and says which it did
   * through `brief.plain.source` — so read that, never this, to decide on `dir="rtl"`.
   */
  simulate: (body: SimulateRequest, lang?: "en" | "ur") =>
    request<SimulateResponse>(`/simulate${query({ lang })}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /** The costed intervention library. Answers even when the cube failed to load. */
  presets: () => request<PresetsResponse>("/plan/presets"),

  /**
   * What `scripts/extract_policy.py` has extracted so far (Phase D). `measures: []` before
   * it has ever run — that is the honest answer, not an error. Answers even when the cube
   * failed to load, and needs no key: the extraction spent it once, offline.
   */
  policyMeasures: () => request<PolicyMeasuresResponse>("/policy/measures"),

  /**
   * Validate and cost a plan *before* running it. A refusal here — 422 with the
   * arithmetic — is the point: a plan that cannot fit must not come back as a small
   * delta that reads like a plan which merely worked badly.
   */
  plan: (body: PlanRequest, lang?: "en" | "ur") =>
    request<PlanResponse>(`/plan${query({ lang })}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /**
   * Where the cooling landed and what was there. Takes the same body as `/simulate` and
   * re-runs the core, so the pattern explained is the pattern that was shown.
   */
  explainSpatial: (body: SimulateRequest) =>
    request<SpatialExplanation>("/explain/spatial", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /**
   * Ask the project's own documentation. Needs no cube, so it answers on a deployment
   * whose Zarr store failed to load.
   */
  ask: (question: string) =>
    request<Answer>("/evidence/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),

  /** The lattice the agent searches over, for the map overlay. */
  candidates: (window?: string) =>
    request<CandidatesResponse>(`/agent/candidates${query({ window })}`),

  /** A finished search, by id. Held in memory server-side; 404 after a restart. */
  search: (searchId: string) =>
    request<SearchResponse>(`/agent/search/${searchId}`).then((body) => body.result),
};

/**
 * Run one search, calling `onEvent` per node transition.
 *
 * Not `EventSource`: that only does GET, and the goal and budget belong in a body. This
 * reads the `fetch` stream and splits SSE frames by hand, which is about fifteen lines and
 * avoids encoding a 500-character goal into a query string.
 *
 * The search takes tens of seconds, and rendering the trace as it arrives is the feature —
 * a 40-second spinner is the thing this is written to avoid. `signal` aborts it.
 */
export async function searchStream(
  body: SearchRequest,
  onEvent: (event: SearchEvent) => void,
  signal?: AbortSignal,
): Promise<SearchResult | null> {
  const response = await fetch(`${API_BASE}/agent/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await parseError(response, "/agent/search");
  if (!response.body) throw new ApiError(500, "the search returned no stream");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: SearchResult | null = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line. A partial frame stays in the buffer —
    // splitting on every newline would parse half a JSON object roughly once a run.
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = frame
        .split("\n")
        .find((line) => line.startsWith("data: "))
        ?.slice(6);
      if (data) {
        const event = JSON.parse(data) as SearchEvent;
        if (event.result) result = event.result;
        onEvent(event);
      }
      boundary = buffer.indexOf("\n\n");
    }
  }

  return result;
}

export { API_BASE };
