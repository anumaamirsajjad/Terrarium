/**
 * Terrarium — draw an intervention, see the modelled surface-temperature change.
 *
 * The composition root for the frontend: it owns the selected window, the drawn polygon,
 * and the simulation result, and derives every overlay from them. Nothing here does
 * physics; it decodes what the API returns and colours it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type CubeSummaryResponse,
  type HealthResponse,
  type SimulateResponse,
  api,
} from "./api/client";
import { type LoadedLayer, loadLayer, useCubeLayer } from "./hooks/useCubeLayer";
import MapView, { type RasterOverlay } from "./map/MapView";
import { type Position, useDrawnPolygon } from "./map/useDrawnPolygon";
import Legend from "./panels/Legend";
import EquityPanel from "./panels/EquityPanel";
import ResultPanel from "./panels/ResultPanel";
import { decodeLayer } from "./raster/decode";
import { toCanvas } from "./raster/canvas";
import {
  addRasters,
  colourise,
  columnToLongitude,
  finiteExtent,
  splitRasters,
} from "./raster/image";
import { DIVERGING, HEAT, rampForVariable, symmetricDomain } from "./raster/ramp";
import { labelWithUnits } from "./units";
import "./App.css";

/** The variables worth putting on a map. Meteorology is (time,) and has no map. */
const MAPPABLE = ["lst_c", "ndvi", "ndbi", "albedo", "elevation_m", "population"];

const TEMPERATURE = "lst_c";
const MIN_VERTICES = 3;

type View = "baseline" | "delta" | "compare";

type Boot =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; health: HealthResponse; summary: CubeSummaryResponse };

export default function App() {
  const [boot, setBoot] = useState<Boot>({ kind: "loading" });
  const [selectedWindow, setSelectedWindow] = useState<string | null>(null);
  const [variable, setVariable] = useState(TEMPERATURE);
  const [opacity, setOpacity] = useState(0.85);
  const [canopy, setCanopy] = useState(0.3);
  const [view, setView] = useState<View>("baseline");
  const [result, setResult] = useState<SimulateResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [simulateError, setSimulateError] = useState<string | null>(null);
  const [splitFraction, setSplitFraction] = useState(0.5);
  const [scenarioBase, setScenarioBase] = useState<LoadedLayer | null>(null);

  const draw = useDrawnPolygon();

  // ------------------------------------------------------------------ boot ---

  useEffect(() => {
    let cancelled = false;

    Promise.all([api.health(), api.cubeSummary()])
      .then(([health, summary]) => {
        if (cancelled) return;
        setBoot({ kind: "ready", health, summary });
        // The API's own default: the latest *summer*, not whichever window is last.
        setSelectedWindow(summary.default_window);
      })
      .catch((error: Error) => {
        if (!cancelled) setBoot({ kind: "error", message: error.message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const baseline = useCubeLayer(variable, selectedWindow);

  // Compare always works in temperature, whatever the base-layer picker shows, because
  // the delta the core returns is a temperature field.
  useEffect(() => {
    if (!result) {
      setScenarioBase(null);
      return;
    }
    let cancelled = false;
    loadLayer(TEMPERATURE, result.window)
      .then((loaded) => {
        if (!cancelled) setScenarioBase(loaded);
      })
      .catch(() => {
        if (!cancelled) setScenarioBase(null);
      });
    return () => {
      cancelled = true;
    };
  }, [result]);

  const deltaRaster = useMemo(() => (result ? decodeLayer(result.delta) : null), [result]);

  const deltaDomain = useMemo<[number, number] | null>(() => {
    if (!deltaRaster) return null;
    const extent = finiteExtent(deltaRaster) ?? [-1, 1];
    return symmetricDomain(extent[0], extent[1]);
  }, [deltaRaster]);

  // ------------------------------------------------------------- simulate ---

  const runSimulation = useCallback(async () => {
    if (!draw.geometry || !selectedWindow) return;

    setRunning(true);
    setSimulateError(null);
    try {
      const response = await api.simulate({
        geometry: draw.geometry,
        canopy_fraction_added: canopy,
        window: selectedWindow,
      });
      setResult(response);
      setView("delta");
    } catch (error) {
      setSimulateError((error as Error).message);
      setResult(null);
    } finally {
      setRunning(false);
    }
  }, [draw.geometry, selectedWindow, canopy]);

  const handleMapClick = useCallback(
    (position: Position) => {
      if (draw.drawing) draw.addVertex(position);
    },
    [draw],
  );

  const resetScenario = useCallback(() => {
    draw.clear();
    setResult(null);
    setSimulateError(null);
    setView("baseline");
  }, [draw]);

  // -------------------------------------------------------------- overlay ---

  const overlay = useMemo<RasterOverlay | null>(() => {
    if (view === "baseline" || !result || !deltaRaster) {
      if (!baseline.data) return null;
      const { raster, layer } = baseline.data;
      const extent = finiteExtent(raster);
      if (!extent) return null;
      return {
        id: `baseline-${variable}-${layer.window ?? "static"}`,
        image: toCanvas(
          colourise(raster, { ramp: rampForVariable(variable), domain: extent, opacity }),
          raster,
        ),
        bounds: layer.grid.bounds_wgs84,
      };
    }

    if (view === "delta") {
      return {
        id: `delta-${result.window}-${result.stats.n_cells_changed}`,
        image: toCanvas(
          colourise(deltaRaster, {
            ramp: DIVERGING,
            domain: deltaDomain ?? [-1, 1],
            transparentAtZero: true,
            opacity,
          }),
          deltaRaster,
        ),
        bounds: result.delta.grid.bounds_wgs84,
      };
    }

    // Compare: today west of the divider, the planted scenario east of it.
    if (!scenarioBase) return null;
    const before = scenarioBase.raster;
    const after = addRasters(before, deltaRaster);
    const cut = Math.round(splitFraction * before.width);
    const composited = splitRasters(before, after, cut);

    // One shared domain across both halves. Rescaling each side to its own range would
    // render identical temperatures as different colours either side of the line.
    const extent = finiteExtent(before) ?? [0, 1];
    return {
      id: `compare-${result.window}-${cut}`,
      image: toCanvas(colourise(composited, { ramp: HEAT, domain: extent, opacity }), composited),
      bounds: scenarioBase.layer.grid.bounds_wgs84,
    };
  }, [view, baseline.data, variable, opacity, result, deltaRaster, deltaDomain, scenarioBase, splitFraction]);

  const dividerLongitude = useMemo(() => {
    if (view !== "compare" || !scenarioBase) return null;
    const { raster, layer } = scenarioBase;
    return columnToLongitude(
      Math.round(splitFraction * raster.width),
      raster.width,
      layer.grid.bounds_wgs84,
    );
  }, [view, scenarioBase, splitFraction]);

  const legend = useMemo(() => {
    if (view === "delta" && deltaDomain) {
      return { ramp: DIVERGING, domain: deltaDomain, units: "°C", diverging: true };
    }
    if (view === "compare" && scenarioBase) {
      const extent = finiteExtent(scenarioBase.raster) ?? [0, 1];
      return { ramp: HEAT, domain: extent, units: "°C", diverging: false };
    }
    if (baseline.data) {
      const extent = finiteExtent(baseline.data.raster);
      if (extent) {
        return {
          ramp: rampForVariable(variable),
          domain: extent,
          units: baseline.data.layer.units,
          diverging: false,
        };
      }
    }
    return null;
  }, [view, deltaDomain, scenarioBase, baseline.data, variable]);

  // ----------------------------------------------------------------- view ---

  if (boot.kind === "loading") {
    return <main className="boot">Connecting to the Terrarium API…</main>;
  }

  if (boot.kind === "error") {
    return (
      <main className="boot boot--error">
        <h1>Cannot reach the API</h1>
        <p>{boot.message}</p>
        <p className="muted">
          Start it with <code>uv run terrarium-api</code>, then reload. If it is running
          but has no cube, it serves <code>/health</code> only — check its startup log.
        </p>
      </main>
    );
  }

  const { health, summary } = boot;
  const remaining = MIN_VERTICES - draw.vertices.length;

  return (
    <div className="app">
      <MapView
        tile={health.tile}
        overlay={overlay}
        vertices={draw.vertices}
        polygonClosed={draw.complete}
        drawing={draw.drawing}
        dividerLongitude={dividerLongitude}
        onMapClick={handleMapClick}
      />

      <aside className="sidebar">
        <header className="sidebar__header">
          <h1>Terrarium</h1>
          <p className="muted">
            {health.tile.name}, {health.tile.country} · {summary.shape[0]}×{summary.shape[1]} @{" "}
            {summary.resolution_m}&nbsp;m
          </p>
        </header>

        <section className="control">
          <h2>Seasonal window</h2>
          <div className="segmented segmented--wrap">
            {summary.windows.map((label) => (
              <button
                key={label}
                type="button"
                className={label === selectedWindow ? "is-active" : ""}
                onClick={() => setSelectedWindow(label)}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="hint">
            The same planting cools several times more in summer than in winter, so the
            window is part of the answer rather than a detail.
          </p>
        </section>

        <section className="control">
          <h2>Base layer</h2>
          <select value={variable} onChange={(event) => setVariable(event.target.value)}>
            {summary.variables
              .filter((v) => MAPPABLE.includes(v.name))
              .map((v) => (
                <option key={v.name} value={v.name}>
                  {labelWithUnits(v.name, v.units)}
                </option>
              ))}
          </select>
          {baseline.loading && <p className="hint">Loading…</p>}
          {baseline.data && <p className="hint">{baseline.data.layer.description}</p>}
          {baseline.error && <p className="error-text">{baseline.error}</p>}
        </section>

        <section className="control">
          <h2>Intervention</h2>

          {!draw.complete && !draw.drawing && (
            <button type="button" className="primary" onClick={draw.startDrawing}>
              Draw a polygon
            </button>
          )}

          {!draw.complete && draw.drawing && (
            <div className="stack">
              <p className="hint">
                Click the map to place corners. {draw.vertices.length} placed
                {remaining > 0 ? ` — ${remaining} more needed` : ""}.
              </p>
              <div className="row">
                <button
                  type="button"
                  className="primary"
                  disabled={!draw.canComplete}
                  onClick={draw.completePolygon}
                >
                  Close polygon
                </button>
                <button
                  type="button"
                  disabled={draw.vertices.length === 0}
                  onClick={draw.undoVertex}
                >
                  Undo
                </button>
                <button type="button" onClick={resetScenario}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          {draw.complete && (
            <div className="stack">
              <label className="field">
                <span>
                  Canopy added: <strong>{(canopy * 100).toFixed(0)}%</strong>
                </span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={canopy}
                  onChange={(event) => setCanopy(Number(event.target.value))}
                />
              </label>
              <p className="hint">
                A ceiling, not a promise — each cell is capped at what is still plantable
                there, and water is never planted.
              </p>
              <div className="row">
                <button
                  type="button"
                  className="primary"
                  onClick={() => void runSimulation()}
                  disabled={running}
                >
                  {running ? "Simulating…" : "Run simulation"}
                </button>
                <button type="button" onClick={resetScenario}>
                  Clear
                </button>
              </div>
            </div>
          )}

          {simulateError && <p className="error-text">{simulateError}</p>}
        </section>

        {result && (
          <section className="control">
            <h2>View</h2>
            <div className="segmented">
              <button
                type="button"
                className={view === "baseline" ? "is-active" : ""}
                onClick={() => setView("baseline")}
              >
                Base
              </button>
              <button
                type="button"
                className={view === "delta" ? "is-active" : ""}
                onClick={() => setView("delta")}
              >
                ΔLST
              </button>
              <button
                type="button"
                className={view === "compare" ? "is-active" : ""}
                onClick={() => setView("compare")}
              >
                Compare
              </button>
            </div>

            {view === "compare" && (
              <label className="field">
                <span>Split position</span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={splitFraction}
                  onChange={(event) => setSplitFraction(Number(event.target.value))}
                />
                <span className="hint">
                  West of the line: today. East: after planting. The divider is a fixed
                  line of longitude, so both halves always describe the same places.
                </span>
              </label>
            )}
          </section>
        )}

        <section className="control">
          <h2>Overlay opacity</h2>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={opacity}
            onChange={(event) => setOpacity(Number(event.target.value))}
          />
        </section>

        {legend && (
          <section className="control">
            <h2>Legend</h2>
            <Legend
              ramp={legend.ramp}
              domain={legend.domain}
              units={legend.units}
              diverging={legend.diverging}
            />
          </section>
        )}

        {result && <ResultPanel result={result} />}
        {result && <EquityPanel equity={result.equity} />}

        <footer className="sidebar__footer muted">
          {/* D9: this label must appear wherever the temperature does. Worded as a
              statement about lst_c and ΔLST specifically, because it sits below a layer
              picker that can be showing NDVI. */}
          <strong>lst_c</strong> and <strong>ΔLST</strong> are mid-morning land surface
          temperature (~10:30 local, Landsat ST_B10) — the radiating surface, not air
          temperature, and not the afternoon peak.
        </footer>
      </aside>
    </div>
  );
}
