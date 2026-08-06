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
export interface Brief {
  headline: string;
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

// ------------------------------------------------------- citizen observations ---

export type ObservationCategory = "shade_deficit" | "canopy" | "air_source" | "other";

/**
 * One citizen photo as a vision model read it. **A report, not a measurement** — it is
 * drawn on the same grid as the modelled layers so the two can be compared, and it never
 * enters the cube.
 */
export interface Observation {
  category: ObservationCategory;
  description: string;
  /** 1 = worth noting, 5 = severe. The model's judgement. */
  severity: number;
  /** The model's own confidence. Shown, never used as a filter. */
  confidence: number;
}

export interface StoredObservation {
  id: number;
  observation: Observation;
  lon: number;
  lat: number;
  /** The cell the API placed it in, from the submitted coordinates. */
  row: number;
  col: number;
}

export interface ObservationListResponse {
  observations: StoredObservation[];
  /** A model name, or a sentence saying none is configured — this path has no fallback. */
  reader: string;
  /** Always false. Observations vanish when the process restarts. */
  persisted: boolean;
}

export interface ObservationLayerResponse {
  layer: LayerResponse;
  count: number;
  /** Always false, and the reason this is not served from /cube/layer. */
  measured: boolean;
}

export interface ObservationRequest {
  image_base64: string;
  mime_type: string;
  lon: number;
  lat: number;
}

export interface PlanRequest {
  geometry: GeoJsonPolygon;
  /** Exactly one of these three. */
  text?: string;
  preset?: string;
  plan?: Plan;
  window?: string | null;
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

  simulate: (body: SimulateRequest) =>
    request<SimulateResponse>("/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /** The costed intervention library. Answers even when the cube failed to load. */
  presets: () => request<PresetsResponse>("/plan/presets"),

  /**
   * Validate and cost a plan *before* running it. A refusal here — 422 with the
   * arithmetic — is the point: a plan that cannot fit must not come back as a small
   * delta that reads like a plan which merely worked badly.
   */
  plan: (body: PlanRequest) =>
    request<PlanResponse>("/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /**
   * Read a citizen photo. **503 when no vision model is configured** — the one endpoint
   * here with no offline fallback, because no rule parser can read a photograph.
   */
  submitObservation: (body: ObservationRequest) =>
    request<StoredObservation>("/observations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  observations: () => request<ObservationListResponse>("/observations"),

  observationLayer: () => request<ObservationLayerResponse>("/observations/layer"),
};

export { API_BASE };
