/**
 * Click-to-draw polygon state.
 *
 * Deliberately hand-rolled rather than pulling in a draw plugin. What this phase needs
 * is "click a few points, close the ring" — a couple of dozen lines — where the drawing
 * libraries in this ecosystem bring a peer-dependency matrix against MapLibre and
 * deck.gl majors that is a standing upgrade hazard for a feature this small.
 */

import { useCallback, useMemo, useState } from "react";

import type { GeoJsonPolygon } from "../api/client";

/** [longitude, latitude]. */
export type Position = [number, number];

/** A ring needs three distinct corners before it encloses any area at all. */
export const MIN_VERTICES = 3;

export interface DrawnPolygon {
  vertices: Position[];
  /** True once the ring is closed and can be sent to the API. */
  complete: boolean;
}

export interface PolygonDraw {
  vertices: Position[];
  complete: boolean;
  drawing: boolean;
  canComplete: boolean;
  /** A closed GeoJSON polygon, or null while there is not yet an area. */
  geometry: GeoJsonPolygon | null;
  startDrawing: () => void;
  addVertex: (position: Position) => void;
  completePolygon: () => void;
  undoVertex: () => void;
  clear: () => void;
  /**
   * Replace the ring wholesale with one that was not drawn by hand.
   *
   * The search agent's "Apply this plan" writes its winning region here, so the polygon
   * the user then edits, re-runs and prints is the same one the search scored. Arrives
   * complete, because a region that came back from the API is by definition closed —
   * dropping the user into a half-drawn state would invite an undo that loses it.
   *
   * A duplicated closing vertex is stripped: GeoJSON repeats the first position as the
   * last, and this hook's `vertices` are the distinct corners. Keeping the repeat would
   * show a phantom vertex on the map and let one `undoVertex` appear to do nothing.
   */
  setPolygon: (vertices: Position[]) => void;
}

/** Drop GeoJSON's repeated closing position, if there is one. */
export function openRing(ring: Position[]): Position[] {
  const last = ring.at(-1);
  const first = ring[0];
  if (ring.length > 1 && first && last && first[0] === last[0] && first[1] === last[1]) {
    return ring.slice(0, -1);
  }
  return [...ring];
}

/**
 * Close a ring for GeoJSON: the first position must be repeated as the last.
 *
 * Left open, rasterio still rasterises it — shapely closes rings implicitly — so the
 * bug would not surface as an error, only as a polygon subtly different from the one
 * drawn on screen.
 */
export function toGeoJson(vertices: Position[]): GeoJsonPolygon | null {
  if (vertices.length < MIN_VERTICES) return null;
  const ring: Position[] = [...vertices, vertices[0]!];
  return { type: "Polygon", coordinates: [ring] };
}

export function useDrawnPolygon(): PolygonDraw {
  const [vertices, setVertices] = useState<Position[]>([]);
  const [complete, setComplete] = useState(false);
  const [drawing, setDrawing] = useState(false);

  const startDrawing = useCallback(() => {
    setVertices([]);
    setComplete(false);
    setDrawing(true);
  }, []);

  const addVertex = useCallback((position: Position) => {
    setVertices((current) => [...current, position]);
  }, []);

  const completePolygon = useCallback(() => {
    setVertices((current) => {
      if (current.length < MIN_VERTICES) return current;
      setComplete(true);
      setDrawing(false);
      return current;
    });
  }, []);

  const undoVertex = useCallback(() => {
    setVertices((current) => current.slice(0, -1));
    setComplete(false);
    setDrawing(true);
  }, []);

  const clear = useCallback(() => {
    setVertices([]);
    setComplete(false);
    setDrawing(false);
  }, []);

  const setPolygon = useCallback((next: Position[]) => {
    const opened = openRing(next);
    setVertices(opened);
    setComplete(opened.length >= MIN_VERTICES);
    setDrawing(false);
  }, []);

  const geometry = useMemo(() => (complete ? toGeoJson(vertices) : null), [complete, vertices]);

  return {
    vertices,
    complete,
    drawing,
    canComplete: vertices.length >= MIN_VERTICES,
    geometry,
    startDrawing,
    addVertex,
    completePolygon,
    undoVertex,
    clear,
    setPolygon,
  };
}
