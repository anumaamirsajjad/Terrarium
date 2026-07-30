/**
 * Typed client for the Terrarium API.
 *
 * These types mirror the Pydantic models in `src/terrarium/api/schemas/`. When a schema
 * changes on the Python side, change it here too — or generate from /openapi.json.
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

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);

  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} from ${path}`);
  }

  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>("/health"),
};

export { API_BASE };
