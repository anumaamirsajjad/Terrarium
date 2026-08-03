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

export interface SimulateResponse {
  variable: string;
  units: string;
  /** The window actually simulated. Never assume it matches what you asked for. */
  window: string;
  season: string;
  stats: DeltaStats;
  context: PlausibilityContext;
  delta: LayerResponse;
}

export interface GeoJsonPolygon {
  type: "Polygon";
  /** Rings of [longitude, latitude]. The first ring is the exterior. */
  coordinates: [number, number][][];
}

export interface SimulateRequest {
  geometry: GeoJsonPolygon;
  canopy_fraction_added: number;
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
};

export { API_BASE };
