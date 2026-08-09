/**
 * Terrarium — draw an intervention, see the modelled surface-temperature change.
 *
 * The composition root for the frontend: it owns the selected window, the drawn polygon,
 * and the simulation result, and derives every overlay from them. Nothing here does
 * physics; it decodes what the API returns and colours it.
 *
 * The map is the page. Everything else floats over it, which is a layout decision with one
 * consequence worth stating: the results stack is ordered Result → Equity → Air → Brief and
 * that order is deliberate. Equity sits above air because it is the only output here with
 * no caveat attached — the thermal figure carries the 2.5x hindcast correction and the air
 * figure carries a different qualification in each season. Ordering is the cheapest way to
 * say which number the project stands behind.
 */

import { AnimatePresence, motion } from "motion/react";
import {
  Check,
  ChevronDown,
  PenLine,
  Play,
  Printer,
  Search,
  Trash2,
  Undo2,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, useTransition } from "react";

import {
  type CubeSummaryResponse,
  type HealthResponse,
  type PlanResponse,
  type Preset,
  type SimulateResponse,
  api,
} from "./api/client";
import { type LoadedLayer, loadLayer, useCubeLayer } from "./hooks/useCubeLayer";
import MapView, { type RasterOverlay } from "./map/MapView";
import { type Position, useDrawnPolygon } from "./map/useDrawnPolygon";
import { GLIDE, SNAP } from "./motion/springs";
import Legend from "./panels/Legend";
import AirPanel from "./panels/AirPanel";
import BriefDocument from "./panels/BriefDocument";
import BriefPanel from "./panels/BriefPanel";
import CommandPalette from "./panels/CommandPalette";
import EquityPanel from "./panels/EquityPanel";
import FloatingPanel from "./panels/FloatingPanel";
import PlainPanel from "./panels/PlainPanel";
import ResultPanel from "./panels/ResultPanel";
import { decodeLayer } from "./raster/decode";
import { toCanvas } from "./raster/canvas";
import { withGlow } from "./raster/glow";
import {
  addRasters,
  colourise,
  columnToLongitude,
  finiteExtent,
  splitRasters,
} from "./raster/image";
import { DIVERGING, HEAT, rampForVariable, symmetricDomain } from "./raster/ramp";
import { plainVariableBlurb, plainVariableLabel } from "./units";
import "./App.css";

/**
 * The variables worth putting on a map. Meteorology is (time,) and has no map.
 *
 * `pm25_emission_g_s` is here so the emission inventory itself can be inspected — without
 * it a user can run a low-emission zone and never see what it is removing.
 */
const MAPPABLE = [
  "lst_c",
  "ndvi",
  "ndbi",
  "albedo",
  "elevation_m",
  "population",
  "pm25_emission_g_s",
];

const TEMPERATURE = "lst_c";
const MIN_VERTICES = 3;

type View = "baseline" | "delta" | "air" | "compare";

type Boot =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; health: HealthResponse; summary: CubeSummaryResponse };

/** One button in the floating toolbar. Icon-only unless it carries a label. */
function ToolButton({
  icon: Icon,
  label,
  primary = false,
  disabled = false,
  onClick,
}: {
  icon: LucideIcon;
  label?: string;
  primary?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <motion.button
      layout
      type="button"
      disabled={disabled}
      onClick={onClick}
      whileTap={disabled ? undefined : { scale: 0.96 }}
      transition={SNAP}
      // `title` as well as the visible label: the icon-only variants are the undo and the
      // clear, which are exactly the two nobody should have to guess at.
      title={label}
      aria-label={label}
      className={`flex items-center gap-2 rounded-lg px-3 py-2 text-xs tracking-tight
                  transition-colors disabled:opacity-35 ${
                    primary
                      ? "bg-shoal/15 text-shoal hover:bg-shoal/25 disabled:hover:bg-shoal/15"
                      : "text-white/70 hover:bg-white/10"
                  }`}
    >
      <Icon className="size-3.5 shrink-0" />
      {/* Icon-only under `sm`: three or four of these in a row, each with a label, is wider
          than a phone screen — the pill has no scroll affordance and would bleed off both
          edges since it's centred. `title`/`aria-label` above already carry the name. */}
      {label && <span className="hidden whitespace-nowrap sm:inline">{label}</span>}
    </motion.button>
  );
}

export default function App() {
  const [boot, setBoot] = useState<Boot>({ kind: "loading" });
  const [selectedWindow, setSelectedWindow] = useState<string | null>(null);
  const [variable, setVariable] = useState(TEMPERATURE);
  const [opacity, setOpacity] = useState(0.3);
  const [canopy, setCanopy] = useState(0.3);
  // The traffic lever. 0 means the plan says nothing about emissions and no air block
  // comes back — which is a different answer from "the cube cannot model air".
  const [emissions, setEmissions] = useState(0);
  const [view, setView] = useState<View>("baseline");
  const [result, setResult] = useState<SimulateResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [simulateError, setSimulateError] = useState<string | null>(null);
  const [splitFraction, setSplitFraction] = useState(0.5);
  const [scenarioBase, setScenarioBase] = useState<LoadedLayer | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [planner, setPlanner] = useState("rules");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  /**
   * Swapping windows refetches a 40,602-cell raster, decodes it and re-colourises it.
   *
   * Worth being precise about what this buys, because it is easy to oversell: it does not
   * make the fetch faster, and `useCubeLayer` already tracks its own loading state. What it
   * removes is the synchronous decode-and-colourise blocking the click feedback, so the
   * control answers immediately and dims while the new field arrives.
   */
  const [pending, startTransition] = useTransition();

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

  // The costed intervention library. Fetched separately from the cube because the API
  // serves it without one: a deployment whose Zarr store is missing still has presets, and
  // failing this alongside /health would hide a working half of the product.
  useEffect(() => {
    let cancelled = false;
    api
      .presets()
      .then((response) => {
        if (cancelled) return;
        setPresets(response.presets);
        setPlanner(response.planner);
      })
      .catch(() => {
        if (!cancelled) setPresets([]);
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

  // The air delta arrives in the same LayerResponse shape as everything else, so it needs
  // no decode path of its own — the whole reason A3 was a panel and a switch entry rather
  // than a renderer.
  const airRaster = useMemo(
    () => (result?.air ? decodeLayer(result.air.delta) : null),
    [result],
  );

  const airDomain = useMemo<[number, number] | null>(() => {
    if (!airRaster) return null;
    const extent = finiteExtent(airRaster) ?? [-1, 1];
    return symmetricDomain(extent[0], extent[1]);
  }, [airRaster]);

  // ------------------------------------------------------------- simulate ---

  /**
   * Check a plan against the polygon before running it.
   *
   * The window comes back resolved: "in winter" has to land on a winter window, because
   * the same restriction buys several times more under the inversion. So the plan's window
   * is adopted rather than the picker's — and the picker moves to show it.
   */
  const buildPlan = useCallback(
    async (body: { preset?: string; text?: string }) => {
      if (!draw.geometry) return;

      setPlanning(true);
      setPlanError(null);
      try {
        const response = await api.plan({ geometry: draw.geometry, ...body });
        setPlan(response);
        setCanopy(response.canopy_fraction_added);
        // Both levers, not just canopy. Adopting only the canopy left the emission slider
        // showing 0 while the plan the button posted removed 80 % of the traffic.
        setEmissions(response.emission_fraction_removed);
        setSelectedWindow(response.window);
      } catch (error) {
        // A 422 here carries the validator's arithmetic. Showing it verbatim is the whole
        // point of refusing before simulating.
        setPlanError((error as Error).message);
        setPlan(null);
      } finally {
        setPlanning(false);
      }
    },
    [draw.geometry],
  );

  const runSimulation = useCallback(async () => {
    if (!draw.geometry || !selectedWindow) return;

    setPaletteOpen(false);
    setRunning(true);
    setSimulateError(null);
    try {
      // A resolved plan is posted exactly as the API handed it back, except for the
      // window, which the picker may have moved since. Rebuilding the body by hand would
      // drop `emission_fraction_removed` and silently turn a low-emission zone into a
      // planting of nothing.
      const response = await api.simulate(
        plan
          ? { ...plan.simulate_request, window: selectedWindow }
          : {
              geometry: draw.geometry,
              canopy_fraction_added: canopy,
              emission_fraction_removed: emissions,
              window: selectedWindow,
            },
      );
      setResult(response);
      // Land on whichever result the plan actually produced. A traffic-only plan changes
      // no temperature, so opening on ΔLST would show an empty map for a run that worked.
      setView(response.air && response.stats.n_cells_changed === 0 ? "air" : "delta");
    } catch (error) {
      setSimulateError((error as Error).message);
      setResult(null);
    } finally {
      setRunning(false);
    }
  }, [draw.geometry, selectedWindow, canopy, emissions, plan]);

  const handleMapClick = useCallback(
    (position: Position) => {
      if (draw.drawing) {
        draw.addVertex(position);
        return;
      }
    },
    [draw],
  );

  const resetScenario = useCallback(() => {
    draw.clear();
    setResult(null);
    setSimulateError(null);
    setPlan(null);
    setPlanError(null);
    setView("baseline");
  }, [draw]);

  const clearPlan = useCallback(() => {
    // Drops back to the raw canopy slider without discarding the polygon: the plan is one
    // way to fill in the sliders, not a mode the user is stuck in.
    setPlan(null);
    setPlanError(null);
  }, []);

  const selectWindow = useCallback(
    (label: string) => startTransition(() => setSelectedWindow(label)),
    [],
  );

  // -------------------------------------------------------------- overlay ---

  const overlay = useMemo<RasterOverlay | null>(() => {
    if (view === "baseline" || !result || !deltaRaster) {
      if (!baseline.data) return null;
      const { raster, layer } = baseline.data;
      const extent = finiteExtent(raster);
      if (!extent) return null;
      return {
        id: `baseline-${variable}-${layer.window ?? "static"}`,
        // No glow on a baseline: it is a dense field covering the whole tile, and additive
        // compositing over one of those just raises the exposure of everything.
        image: toCanvas(
          colourise(raster, {
            ramp: rampForVariable(variable),
            domain: extent,
            opacity,
          }),
          raster,
        ),
        bounds: layer.grid.bounds_wgs84,
      };
    }

    if (view === "air") {
      // Guarded rather than assumed: `air` is null whenever the plan did not touch
      // traffic, and the switch entry is hidden in that case.
      if (!airRaster || !result.air) return null;
      return {
        id: `air-${result.window}-${result.air.stats.n_cells_changed}`,
        image: withGlow(
          toCanvas(
            colourise(airRaster, {
              ramp: DIVERGING,
              domain: airDomain ?? [-1, 1],
              transparentAtZero: true,
              opacity,
            }),
            airRaster,
          ),
        ),
        bounds: result.air.delta.grid.bounds_wgs84,
      };
    }

    if (view === "delta") {
      return {
        id: `delta-${result.window}-${result.stats.n_cells_changed}`,
        // Glowed, because the signal is sparse: ~39,000 of 40,602 cells are exactly zero
        // after a simulation and stay transparent, so a halo is what lets the eye find the
        // intervention without smoothing a single measured cell.
        image: withGlow(
          toCanvas(
            colourise(deltaRaster, {
              ramp: DIVERGING,
              domain: deltaDomain ?? [-1, 1],
              transparentAtZero: true,
              opacity,
            }),
            deltaRaster,
          ),
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
  }, [
    view,
    baseline.data,
    variable,
    opacity,
    result,
    deltaRaster,
    deltaDomain,
    airRaster,
    airDomain,
    scenarioBase,
    splitFraction,
  ]);

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
    if (view === "air" && airDomain && result?.air) {
      return { ramp: DIVERGING, domain: airDomain, units: result.air.units, diverging: true };
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
  }, [view, deltaDomain, airDomain, result, scenarioBase, baseline.data, variable]);

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

  const views: { key: View; label: string; show: boolean }[] = [
    { key: "baseline", label: "Base", show: true },
    { key: "delta", label: "Temp change", show: true },
    // Only when there is an air result. Offering the tab for a plan that never touched
    // traffic would paint an empty map and read as a broken button.
    { key: "air", label: "Air change", show: Boolean(result?.air) },
    // Always the ground-temperature before/after, whatever the base-layer picker shows
    // above - see the note on `scenarioBase`. The label says "temperature" rather than
    // naming the picker's variable, so it stops implying it follows the picker.
    { key: "compare", label: "Compare temperature", show: true },
  ];

  return (
    <div className="app">
      <MapView
        tile={health.tile}
        overlay={overlay}
        vertices={draw.vertices}
        polygonClosed={draw.complete}
        drawing={draw.drawing}
        dividerLongitude={dividerLongitude}
        onSplitChange={setSplitFraction}
        onMapClick={handleMapClick}
      />

      {/* ------------------------------------------------------------ top cluster --- */}
      {/* Below `sm` these four stack in normal flow instead of floating independently:
          brand (top-left), the window/layer pill (top-centre) and the results rail
          (top-right) are all wide enough, and close enough together, that a phone-width
          viewport had nowhere to put all three without overlap. `sm:contents` drops this
          wrapper from the box model at the desktop breakpoint, so each child's own
          `sm:absolute` positions it exactly where it always sat, pixel for pixel. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex flex-col gap-3 p-4 sm:contents">
        {/* brand, top left */}
        <div className="glass noise pointer-events-auto z-10 self-start px-4 py-3 sm:absolute sm:top-5 sm:left-5">
          <h1 className="font-mono text-sm tracking-tight text-white">Terrarium</h1>
          <p className="mt-0.5 font-mono text-[0.65rem] text-white/40">
            {health.tile.name}, {health.tile.country} · {summary.shape[0]}×{summary.shape[1]} @{" "}
            {summary.resolution_m}&nbsp;m
          </p>
        </div>

        {/* window + layer, top centre */}
        <motion.div
          animate={{ opacity: pending ? 0.45 : 1 }}
          transition={SNAP}
          aria-busy={pending}
          className="glass pointer-events-auto z-10 px-3 py-2 sm:absolute sm:top-5 sm:left-1/2 sm:-translate-x-1/2"
        >
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={selectedWindow ?? ""}
              onChange={(event) => selectWindow(event.target.value)}
              className="bg-transparent font-mono text-xs text-white outline-none"
              aria-label="Seasonal window"
            >
              {summary.windows.map((label) => (
                <option key={label} value={label} className="bg-[#0a0d12]">
                  {label}
                </option>
              ))}
            </select>
            <span className="h-4 w-px bg-white/10" />
            <select
              value={variable}
              onChange={(event) => setVariable(event.target.value)}
              className="max-w-56 bg-transparent font-mono text-xs text-white/70 outline-none"
              aria-label="Base layer"
            >
              {summary.variables
                .filter((v) => MAPPABLE.includes(v.name))
                .map((v) => (
                  <option key={v.name} value={v.name} className="bg-[#0a0d12]">
                    {plainVariableLabel(v.name)}
                  </option>
                ))}
            </select>
          </div>
          {/* The window is part of the answer, not a detail: the same planting cools about
              four times more in summer than in winter. */}
          <p className="mt-1 max-w-md text-[0.62rem] leading-snug text-white/35">
            {baseline.loading || pending
              ? "Loading…"
              : (baseline.error ?? plainVariableBlurb(variable))}
          </p>
        </motion.div>

        {/* view switcher, under the pill, once there is something to switch between */}
        <AnimatePresence>
          {result && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={GLIDE}
              className="glass pointer-events-auto z-10 flex flex-wrap items-center gap-1
                         self-start p-1 sm:absolute sm:top-[6.2rem] sm:left-1/2 sm:-translate-x-1/2"
            >
              {views
                .filter((entry) => entry.show)
                .map((entry) => (
                  <button
                    key={entry.key}
                    type="button"
                    onClick={() => setView(entry.key)}
                    className={`rounded-md px-3 py-1.5 font-mono text-[0.68rem] transition-colors ${
                      view === entry.key
                        ? "bg-white/12 text-white"
                        : "text-white/45 hover:text-white/80"
                    }`}
                  >
                    {entry.label}
                  </button>
                ))}

              {view === "compare" && (
                <label className="ml-2 flex items-center gap-2 pr-2">
                  {/* Kept and focusable rather than deleted: the grip on the map is a mouse
                      affordance, and the split has to stay keyboard-operable without it. */}
                  <span className="sr-only">Split position</span>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    value={splitFraction}
                    onChange={(event) => setSplitFraction(Number(event.target.value))}
                    className="w-28 accent-white/70"
                  />
                </label>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {/* results, right rail. Capped to 50vh in the stacked mobile flow so it can never
            push the toolbar and legend off screen; the full-height scroll returns once it
            is its own floating rail at `sm` and up. */}
        <div
          className="pointer-events-auto z-10 flex max-h-[50vh] flex-col gap-3
                     overflow-x-hidden overflow-y-auto sm:absolute sm:top-5 sm:right-5
                     sm:max-h-[calc(100vh-2.5rem)] sm:w-96"
        >
          <AnimatePresence mode="popLayout">
            {result && (
              <FloatingPanel key="plain">
                <PlainPanel brief={result.brief} />

                <button
                  type="button"
                  onClick={() => window.print()}
                  className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg
                             border border-white/12 py-2 text-xs text-white/70
                             hover:bg-white/10"
                >
                  <Printer className="size-3.5" />
                  Save the full brief as PDF
                </button>
              </FloatingPanel>
            )}

            {/* Everything below is the technical read, collapsed by default.
                `<details>` rather than a `useState` toggle: the browser already ships the
                open/closed state, the keyboard handling, the ARIA expanded semantics and
                find-in-page opening the section to reveal a match. None of that is worth
                re-implementing, and the print stylesheet can force it open with one rule. */}
            {result && (
              <FloatingPanel key="detail">
                <details className="group">
                  <summary
                    className="flex cursor-pointer list-none items-center justify-between
                               text-xs text-white/55 hover:text-white/80"
                  >
                    <span>Show the technical detail</span>
                    <ChevronDown
                      aria-hidden="true"
                      className="size-3.5 transition-transform group-open:rotate-180"
                    />
                  </summary>

                  <div className="mt-4 space-y-5 border-t border-white/8 pt-4">
                    <ResultPanel result={result} />
                    <EquityPanel equity={result.equity} />
                    {result.air && (
                      <AirPanel
                        air={result.air}
                        window={result.window}
                        season={result.season}
                      />
                    )}
                    <BriefPanel brief={result.brief} />
                  </div>
                </details>
              </FloatingPanel>
            )}
          </AnimatePresence>
        </div>

        {/* legend + opacity. In the mobile stack it flows below the results rail; at `sm`
            and up it pops back out to its usual floating spot, bottom left. It used to be
            pinned there unconditionally, which put it directly under the toolbar pill on
            any narrow screen — same bottom offset, overlapping horizontal range, so the
            draw/run/clear buttons rendered half-hidden behind it. */}
        <div className="glass noise pointer-events-auto z-10 p-4 sm:absolute sm:bottom-6 sm:left-5 sm:w-80">
          {legend ? (
            <Legend
              ramp={legend.ramp}
              domain={legend.domain}
              units={legend.units}
              diverging={legend.diverging}
            />
          ) : (
            <p className="text-xs text-white/40">No layer loaded.</p>
          )}

          <label className="mt-3 flex items-center gap-3">
            <span className="font-mono text-[0.62rem] whitespace-nowrap text-white/40">
              opacity
            </span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={opacity}
              onChange={(event) => setOpacity(Number(event.target.value))}
              className="w-full accent-white/70"
            />
          </label>

          {/* D9: this label must appear wherever the temperature does. Worded as a
              statement about lst_c and ΔLST specifically, because it sits under a layer
              picker that can be showing NDVI. */}
          <p className="mt-3 border-t border-white/10 pt-3 text-[0.62rem] leading-relaxed text-white/35">
            <strong className="text-white/55">lst_c</strong> and{" "}
            <strong className="text-white/55">ΔLST</strong> are mid-morning land surface
            temperature (~10:30 local, Landsat ST_B10) — the radiating surface, not air
            temperature, and not the afternoon peak.
          </p>
        </div>
      </div>

      {/* ------------------------------------------------------- levers, above toolbar --- */}
      <AnimatePresence>
        {draw.complete && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            transition={GLIDE}
            className="glass noise absolute bottom-24 left-1/2 z-10 w-[min(36rem,calc(100vw-3rem))]
                       -translate-x-1/2 p-4"
          >
            <div className="grid gap-4 md:grid-cols-2">
              <label className="block">
                <span className="font-mono text-[0.65rem] text-white/55">
                  Canopy added <strong className="text-white">{(canopy * 100).toFixed(0)}%</strong>
                  {plan && plan.canopy_fraction_added !== canopy && (
                    <span className="text-white/35"> · edited since the plan was checked</span>
                  )}
                </span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={canopy}
                  onChange={(event) => setCanopy(Number(event.target.value))}
                  className="mt-1.5 w-full accent-emerald-400"
                />
                <span className="mt-1 block text-[0.6rem] leading-snug text-white/35">
                  A ceiling, not a promise — each cell is capped at what is still plantable
                  there, and water is never planted.
                </span>
              </label>

              <label className="block">
                <span className="font-mono text-[0.65rem] text-white/55">
                  Vehicle emissions removed{" "}
                  <strong className="text-white">{(emissions * 100).toFixed(0)}%</strong>
                  {plan && plan.emission_fraction_removed !== emissions && (
                    <span className="text-white/35"> · edited since the plan was checked</span>
                  )}
                </span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={emissions}
                  onChange={(event) => setEmissions(Number(event.target.value))}
                  className="mt-1.5 w-full accent-violet-400"
                />
                <span className="mt-1 block text-[0.6rem] leading-snug text-white/35">
                  1.0 means the traffic is gone, not electrified — brake, tyre and road wear
                  are roughly half of road PM2.5 and stay until the vehicles do. At 0 the
                  plan says nothing about traffic and no air result comes back.
                </span>
              </label>
            </div>

            {simulateError && (
              <p className="mt-3 text-xs text-red-400">{simulateError}</p>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* ------------------------------------------------------ toolbar, bottom centre --- */}
      <motion.div
        layout
        transition={GLIDE}
        className="glass absolute bottom-6 left-1/2 z-20 flex -translate-x-1/2 items-center
                   gap-1 p-1.5"
      >
        {/* `popLayout` matters: without it the exiting button holds its slot while the
            entering one appears beside it, and the pill visibly stutters. */}
        <AnimatePresence mode="popLayout" initial={false}>
          {!draw.complete && !draw.drawing && (
            <ToolButton
              key="draw"
              icon={PenLine}
              label="Draw zone"
              primary
              onClick={draw.startDrawing}
            />
          )}

          {draw.drawing && (
            <motion.span
              key="count"
              layout
              className="px-3 font-mono text-[0.68rem] whitespace-nowrap text-white/45"
            >
              {draw.vertices.length} pt{draw.vertices.length === 1 ? "" : "s"}
              {remaining > 0 ? ` · ${remaining} more` : ""}
            </motion.span>
          )}
          {draw.drawing && (
            <ToolButton
              key="close"
              icon={Check}
              label="Close"
              primary
              disabled={!draw.canComplete}
              onClick={draw.completePolygon}
            />
          )}
          {draw.drawing && (
            <ToolButton
              key="undo"
              icon={Undo2}
              label="Undo"
              disabled={draw.vertices.length === 0}
              onClick={draw.undoVertex}
            />
          )}

          {draw.complete && (
            <ToolButton
              key="plan"
              icon={Search}
              label={plan ? plan.plan.name : "Plan…"}
              onClick={() => setPaletteOpen(true)}
            />
          )}
          {draw.complete && (
            <ToolButton
              key="run"
              icon={Play}
              label={running ? "Simulating…" : "Run simulation"}
              primary
              disabled={running}
              onClick={() => void runSimulation()}
            />
          )}
          {(draw.complete || draw.drawing) && (
            <ToolButton key="clear" icon={Trash2} label="Clear" onClick={resetScenario} />
          )}
        </AnimatePresence>
      </motion.div>

      {!draw.complete && !draw.drawing && (
        <p
          className="pointer-events-none absolute bottom-2 left-1/2 z-10 -translate-x-1/2
                     font-mono text-[0.6rem] tracking-[0.18em] text-white/25 uppercase"
        >
          press / for the plan palette
        </p>
      )}

      {/* The palette owns its own shortcut, so it is mounted whether or not it is open. */}
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        presets={presets}
        planner={planner}
        hasPolygon={draw.complete}
        plan={plan}
        error={planError}
        busy={planning}
        onPreset={(slug) => void buildPlan({ preset: slug })}
        onText={(text) => void buildPlan({ text })}
        onClear={clearPlan}
      />

      {/* Hidden on screen, and the only thing on the page when printed. Rendered here
          rather than in a new window so it always describes the result currently loaded. */}
      {result && (
        <BriefDocument
          result={result}
          plan={plan}
          tile={health.tile}
          producedAt={new Date()}
        />
      )}
    </div>
  );
}
