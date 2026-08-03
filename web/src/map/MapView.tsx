/**
 * MapLibre basemap with a deck.gl overlay.
 *
 * **Basemap tiles are OpenFreeMap Positron** — no key, no registration, no request
 * limit. MapLibre GL is the free *library*; the tiles are a separate service, and the
 * usual tutorials point at MapTiler or Stadia, both of which meter usage behind an API
 * key. That is the single easiest way to put a credit card into this project (D13), and
 * it would break a claim in the pitch, not just the budget. Positron is also a pale
 * basemap, which is what you want underneath a heat overlay.
 */

import { MapboxOverlay } from "@deck.gl/mapbox";
import { BitmapLayer, PolygonLayer, ScatterplotLayer, LineLayer } from "@deck.gl/layers";
import type { Layer } from "@deck.gl/core";
// maplibre-gl v6 dropped its default export; import the classes by name.
import { Map as MapLibreMap, NavigationControl, ScaleControl } from "maplibre-gl";
import type { MapMouseEvent } from "maplibre-gl";
import { useEffect, useRef } from "react";
import "maplibre-gl/dist/maplibre-gl.css";
// Side-effect import: must run before any Map is constructed. See the module for why
// MapLibre cannot find its own worker under Vite.
import "./maplibreWorker";

import type { TileInfo } from "../api/client";
import type { Position } from "./useDrawnPolygon";

/** Keyless, unmetered, attribution added automatically by MapLibre. See D13. */
export const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";

export interface RasterOverlay {
  id: string;
  image: HTMLCanvasElement;
  /** [west, south, east, north] — the layer's own envelope, from the API. */
  bounds: [number, number, number, number];
}

export interface MapViewProps {
  tile: TileInfo;
  overlay: RasterOverlay | null;
  vertices: Position[];
  polygonClosed: boolean;
  drawing: boolean;
  /** Longitude of the compare divider, or null when not comparing. */
  dividerLongitude: number | null;
  onMapClick: (position: Position) => void;
}

const DRAW_FILL: [number, number, number, number] = [56, 189, 248, 55];
const DRAW_FILL_OPEN: [number, number, number, number] = [56, 189, 248, 12];
const DRAW_LINE: [number, number, number, number] = [56, 189, 248, 255];
const DIVIDER_COLOUR: [number, number, number, number] = [255, 255, 255, 200];

export default function MapView({
  tile,
  overlay,
  vertices,
  polygonClosed,
  drawing,
  dividerLongitude,
  onMapClick,
}: MapViewProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const deck = useRef<MapboxOverlay | null>(null);
  // The click handler changes identity every render; keep it in a ref so the map
  // listener can stay attached for the map's whole life instead of being rebound.
  const clickHandler = useRef(onMapClick);
  clickHandler.current = onMapClick;

  useEffect(() => {
    if (!container.current || map.current) return;

    const [west, south, east, north] = tile.bbox;
    const instance = new MapLibreMap({
      container: container.current,
      style: BASEMAP_STYLE,
      // Centre and zoom come from /health, so the tile's location lives in exactly one
      // place — config.py — and the frontend never duplicates the bbox.
      bounds: [
        [west, south],
        [east, north],
      ],
      fitBoundsOptions: { padding: 24 },
      attributionControl: { compact: true },
    });

    instance.addControl(new NavigationControl({ showCompass: false }), "top-right");
    instance.addControl(new ScaleControl({ unit: "metric" }), "bottom-right");

    const overlayInstance = new MapboxOverlay({ interleaved: false, layers: [] });
    instance.addControl(overlayInstance);

    instance.on("click", (event: MapMouseEvent) => {
      clickHandler.current([event.lngLat.lng, event.lngLat.lat]);
    });

    map.current = instance;
    deck.current = overlayInstance;

    return () => {
      overlayInstance.finalize();
      instance.remove();
      map.current = null;
      deck.current = null;
    };
  }, [tile.bbox]);

  // Cursor affordance: a crosshair while drawing, the default grab hand otherwise.
  useEffect(() => {
    const canvas = map.current?.getCanvas();
    if (canvas) canvas.style.cursor = drawing ? "crosshair" : "";
  }, [drawing]);

  useEffect(() => {
    if (!deck.current) return;

    const layers: Layer[] = [];

    if (overlay) {
      layers.push(
        new BitmapLayer({
          id: overlay.id,
          image: overlay.image,
          bounds: overlay.bounds,
          // Nearest-neighbour: at 100 m a cell is a real measured unit, and smoothing
          // between them would invent gradients the cube does not contain.
          textureParameters: {
            minFilter: "nearest",
            magFilter: "nearest",
          },
          pickable: false,
        }),
      );
    }

    if (dividerLongitude !== null) {
      const [, south, , north] = tile.bbox;
      layers.push(
        new LineLayer({
          id: "compare-divider",
          data: [{ from: [dividerLongitude, south], to: [dividerLongitude, north] }],
          getSourcePosition: (d: { from: Position }) => d.from,
          getTargetPosition: (d: { to: Position }) => d.to,
          getColor: DIVIDER_COLOUR,
          getWidth: 2,
        }),
      );
    }

    if (vertices.length >= 3) {
      layers.push(
        new PolygonLayer({
          id: "drawn-polygon",
          data: [{ polygon: [...vertices, vertices[0]!] }],
          getPolygon: (d: { polygon: Position[] }) => d.polygon,
          // Filled only once closed, so "is this finished?" is visible at a glance.
          // Dashes would need PathStyleExtension for one bit of state; a fill toggle
          // says the same thing with nothing extra loaded.
          getFillColor: polygonClosed ? DRAW_FILL : DRAW_FILL_OPEN,
          getLineColor: DRAW_LINE,
          getLineWidth: 2,
          lineWidthUnits: "pixels",
          filled: true,
          stroked: true,
          pickable: false,
        }),
      );
    } else if (vertices.length === 2) {
      layers.push(
        new LineLayer({
          id: "drawn-edge",
          data: [{ from: vertices[0]!, to: vertices[1]! }],
          getSourcePosition: (d: { from: Position }) => d.from,
          getTargetPosition: (d: { to: Position }) => d.to,
          getColor: DRAW_LINE,
          getWidth: 2,
        }),
      );
    }

    if (vertices.length > 0) {
      layers.push(
        new ScatterplotLayer({
          id: "drawn-vertices",
          data: vertices,
          getPosition: (d: Position) => d,
          getFillColor: DRAW_LINE,
          getRadius: 4,
          radiusUnits: "pixels",
          pickable: false,
        }),
      );
    }

    deck.current.setProps({ layers });
  }, [overlay, vertices, polygonClosed, dividerLongitude, tile.bbox]);

  return <div ref={container} className="map" />;
}
