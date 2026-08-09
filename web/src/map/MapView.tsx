/**
 * MapLibre basemap with a deck.gl overlay.
 *
 * **Basemap tiles are OpenFreeMap Positron** — no key, no registration, no request
 * limit. MapLibre GL is the free *library*; the tiles are a separate service, and the
 * usual tutorials point at MapTiler or Stadia, both of which meter usage behind an API
 * key. That is the single easiest way to put a credit card into this project (D13), and
 * it would break a claim in the pitch, not just the budget. Positron is also a pale
 * basemap, which is what you want underneath a heat overlay.
 *
 * This component owns everything that needs the map instance itself: the projection the
 * compare grip is positioned by, and the camera. Nothing above it knows about either.
 */

import { MapboxOverlay } from "@deck.gl/mapbox";
import {
  BitmapLayer,
  PolygonLayer,
  PathLayer,
  ScatterplotLayer,
  LineLayer,
} from "@deck.gl/layers";
import {
  AmbientLight,
  LightingEffect,
  _SunLight as SunLight,
  type Layer,
} from "@deck.gl/core";
import { ChevronsLeftRight } from "lucide-react";
// maplibre-gl v6 dropped its default export; import the classes by name.
import { Map as MapLibreMap, NavigationControl, ScaleControl } from "maplibre-gl";
import type { MapMouseEvent } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";
import "maplibre-gl/dist/maplibre-gl.css";
// Side-effect import: must run before any Map is constructed. See the module for why
// MapLibre cannot find its own worker under Vite.
import "./maplibreWorker";

import type { TileInfo } from "../api/client";
import { antSegments, DASH, GAP, PERIOD } from "./ants";
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
  /** Called while the compare grip is dragged, with a fraction of the tile's width. */
  onSplitChange?: (fraction: number) => void;
  onMapClick: (position: Position) => void;
}

const DRAW_FILL: [number, number, number, number] = [56, 189, 248, 55];
const DRAW_FILL_OPEN: [number, number, number, number] = [56, 189, 248, 12];
const DRAW_LINE: [number, number, number, number] = [56, 189, 248, 255];
const DIVIDER_COLOUR: [number, number, number, number] = [255, 255, 255, 200];

/**
 * Metres. About two pixels at the tile's default zoom — visible as a wall, not a tower.
 *
 * The zone is a *drawn intention*, so it should read as sitting on the city rather than
 * looming over it; the raster underneath is the thing being looked at.
 */
const ZONE_ELEVATION_M = 220;

/** Degrees the camera tips once a ring closes, so the extrusion reads at all. */
const CLOSED_PITCH = 45;

/** Milliseconds between ant phase steps. 20 fps is plenty, at a fifth of the rebuilds. */
const ANT_TICK_MS = 50;
const ANT_STEP = PERIOD / 12;

export default function MapView({
  tile,
  overlay,
  vertices,
  polygonClosed,
  drawing,
  dividerLongitude,
  onSplitChange,
  onMapClick,
}: MapViewProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const deck = useRef<MapboxOverlay | null>(null);
  // The click handler changes identity every render; keep it in a ref so the map
  // listener can stay attached for the map's whole life instead of being rebound.
  const clickHandler = useRef(onMapClick);
  clickHandler.current = onMapClick;

  const [phase, setPhase] = useState(0);
  /** Screen x of the compare divider, or null when there is nothing to grip. */
  const [gripX, setGripX] = useState<number | null>(null);

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

    // Both controls to the bottom-right: the top-right corner is where the results stack
    // now lives, and a navigation puck underneath a glass panel is unreachable.
    instance.addControl(new NavigationControl({ showCompass: false }), "bottom-right");
    instance.addControl(new ScaleControl({ unit: "metric" }), "bottom-right");

    // An extruded polygon with no lighting renders as a flat silhouette — the walls take
    // the same colour as the roof and the extrusion is invisible. The sun is placed at
    // 10:30 Lahore time (UTC+5), which is when Landsat passes over: the shadows fall where
    // the overpass put them, for the cost of one timestamp.
    const lighting = new LightingEffect({
      ambient: new AmbientLight({ color: [255, 255, 255], intensity: 1.4 }),
      sun: new SunLight({
        timestamp: Date.UTC(2024, 5, 15, 5, 30),
        color: [255, 245, 230],
        intensity: 1.1,
      }),
    });

    const overlayInstance = new MapboxOverlay({
      interleaved: false,
      layers: [],
      effects: [lighting],
    });
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

  // The camera tips only once a ring closes. Pitching while drawing would move the ground
  // under a click the user is aiming, and an open ring has no wall to show anyway.
  useEffect(() => {
    map.current?.easeTo({ pitch: polygonClosed ? CLOSED_PITCH : 0, duration: 900 });
  }, [polygonClosed]);

  // Marching ants. Guarded on the preference, because a border that crawls forever with no
  // way to stop it is precisely what that rule exists for — the dashes stay, they just
  // stand still.
  useEffect(() => {
    if (!polygonClosed) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = setInterval(() => setPhase((p) => (p + ANT_STEP) % PERIOD), ANT_TICK_MS);
    return () => clearInterval(id);
  }, [polygonClosed]);

  // ------------------------------------------------------------ compare grip ---

  /**
   * Where the divider currently is on screen.
   *
   * Recomputed on every camera move, because the line is a fixed line of *longitude* and
   * the grip is only a handle for it. Deriving the grip from the container width instead
   * would leave the two disagreeing the moment anybody panned.
   */
  const syncGrip = useCallback(() => {
    if (dividerLongitude === null || !map.current) {
      setGripX(null);
      return;
    }
    const centre = (tile.bbox[1] + tile.bbox[3]) / 2;
    setGripX(map.current.project([dividerLongitude, centre]).x);
  }, [dividerLongitude, tile.bbox]);

  useEffect(() => {
    syncGrip();
    const instance = map.current;
    if (!instance) return;
    instance.on("move", syncGrip);
    return () => {
      instance.off("move", syncGrip);
    };
  }, [syncGrip]);

  const startGripDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!onSplitChange || !map.current) return;
      event.preventDefault();
      const instance = map.current;
      const [west, , east] = tile.bbox;

      const move = (pointer: PointerEvent) => {
        const box = instance.getContainer().getBoundingClientRect();
        // Through the map's own projection rather than the raw container fraction: at a
        // pitch of 45° screen x and longitude are no longer proportional, and the grip
        // would slide away from the line it is supposed to be holding.
        const centre = (tile.bbox[1] + tile.bbox[3]) / 2;
        const y = instance.project([west, centre]).y;
        const lng = instance.unproject([pointer.clientX - box.left, y]).lng;
        onSplitChange(Math.min(1, Math.max(0, (lng - west) / (east - west))));
      };

      const stop = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop);
    },
    [onSplitChange, tile.bbox],
  );

  // ---------------------------------------------------------------- layers ---

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
      const ring: Position[] = [...vertices, vertices[0]!];
      layers.push(
        new PolygonLayer({
          id: "drawn-polygon",
          data: [{ polygon: ring }],
          getPolygon: (d: { polygon: Position[] }) => d.polygon,
          // Filled only once closed, so "is this finished?" is visible at a glance.
          getFillColor: polygonClosed ? DRAW_FILL : DRAW_FILL_OPEN,
          getLineColor: DRAW_LINE,
          getLineWidth: 2,
          lineWidthUnits: "pixels",
          filled: true,
          stroked: true,
          // Only once closed. Extruding an open ring builds a wall across an area the user
          // has not finished defining.
          extruded: polygonClosed,
          getElevation: ZONE_ELEVATION_M,
          wireframe: true,
          material: {
            ambient: 0.5,
            diffuse: 0.6,
            shininess: 32,
            specularColor: [80, 200, 230],
          },
          pickable: false,
        }),
      );

      if (polygonClosed) {
        layers.push(
          new PathLayer({
            id: "drawn-ants",
            data: antSegments(ring, DASH, GAP, phase),
            // Lifted to the top of the extrusion, not left on the ground. At ground level
            // the dashes are underneath the zone's own roof and simply never appear — the
            // ants were running the whole time, inside a solid. Up here they crown the
            // wall, which is also where a border belongs on an object that has height.
            getPath: (d: Position[]) =>
              d.map(([lng, lat]) => [lng, lat, ZONE_ELEVATION_M] as [number, number, number]),
            getColor: [255, 255, 255, 220],
            getWidth: 2,
            widthUnits: "pixels",
            pickable: false,
          }),
        );
      }
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
  }, [overlay, vertices, polygonClosed, dividerLongitude, tile.bbox, phase]);

  return (
    <div ref={container} className="map">
      {gripX !== null && (
        <div
          role="presentation"
          onPointerDown={startGripDrag}
          style={{ left: gripX }}
          className="absolute inset-y-0 z-10 -ml-4 flex w-8 cursor-ew-resize items-center
                     justify-center"
        >
          <span className="absolute inset-y-0 left-1/2 w-px bg-gradient-to-b from-transparent
                           via-white/80 to-transparent shadow-[0_0_18px_rgba(255,255,255,0.5)]" />
          <span className="glass relative grid size-9 place-items-center rounded-full
                           text-white/80">
            <ChevronsLeftRight className="size-4" aria-hidden />
          </span>
        </div>
      )}
    </div>
  );
}
