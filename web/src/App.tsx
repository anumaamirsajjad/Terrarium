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
 *
 * **Floating over the map costs the map.** At `lg` and up there is room either side of it
 * for that to be free. On a phone there is not: the chrome that reads as a light dusting on
 * a 1440 px desktop covered the top half of a 390 px viewport, and the top half of the map
 * is where a user taps to draw. Every polygon drawn on a phone came out with two of its
 * four vertices missing, because two of the four taps landed on a glass panel. So the
 * narrow layout is not the wide one reflowed — it is a different arrangement with the same
 * parts: one merged bar at the top, a compact legend under it, the levers and the toolbar
 * at the bottom, and the results in a sheet that is summoned rather than always present.
 */

import { AnimatePresence, motion } from "motion/react";
import {
  BookOpen,
  Check,
  ChevronDown,
  Landmark,
  PenLine,
  Play,
  Printer,
  Search,
  Sparkles,
  Trash2,
  Undo2,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";

import {
  type Answer,
  type Attempt,
  type Candidate,
  type CubeSummaryResponse,
  type HealthResponse,
  type MappedPlan,
  type Plan,
  type PlanResponse,
  type PolicyMeasureItem,
  type Preset,
  type SearchResult,
  type SimulateResponse,
  type SpatialExplanation,
  api,
  searchStream,
} from "./api/client";
import { type LoadedLayer, loadLayer, useCubeLayer } from "./hooks/useCubeLayer";
import MapView, { type RasterOverlay } from "./map/MapView";
import { type Position, useDrawnPolygon } from "./map/useDrawnPolygon";
import { GLIDE, SNAP } from "./motion/springs";
import Legend from "./panels/Legend";
import AgentPanel from "./panels/AgentPanel";
import AirPanel from "./panels/AirPanel";
import EvidencePanel from "./panels/EvidencePanel";
import PatternPanel from "./panels/PatternPanel";
import PolicyPanel from "./panels/PolicyPanel";
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
   * Is the results sheet up? Narrow layout only — at `lg` and up the results are a rail
   * beside the map and there is nothing to open or shut.
   *
   * A sheet rather than a permanent panel because on a phone the two cannot both win: a
   * panel tall enough to hold the brief is a panel that hides the field it describes. It
   * opens itself when a run lands, since that is what the user just asked for, and the
   * chip in the top cluster puts it back.
   */
  const [sheetOpen, setSheetOpen] = useState(false);

  // --- the search agent (Phase A). Everything here is inert until the panel is opened. ---
  const [agentOpen, setAgentOpen] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [trace, setTrace] = useState<Attempt[]>([]);
  const [searchStatus, setSearchStatus] = useState<string | null>(null);
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  /** Regions the agent is evaluating right now, highlighted on the lattice overlay. */
  const [litRegions, setLitRegions] = useState<string[]>([]);
  const searchAbort = useRef<AbortController | null>(null);

  /**
   * Which language the brief comes back in (Phase C).
   *
   * The rule parser already reads Urdu, so a resident who typed Urdu was being answered
   * only in English. This is a *request*, not a guarantee: the server returns the English
   * template whenever no key is configured or the translation drifts, and the panel reads
   * `brief.plain.source` rather than this to decide whether to set `dir="rtl"`.
   */
  const [lang, setLang] = useState<"en" | "ur">("en");

  // --- where it landed (Phase E). Re-runs the core, so it is opt-in rather than on
  //     every /simulate: a second model call on the main endpoint would cost every user
  //     latency for a panel most of them never open. ---
  const [pattern, setPattern] = useState<SpatialExplanation | null>(null);
  const [patternBusy, setPatternBusy] = useState(false);
  const [patternError, setPatternError] = useState<string | null>(null);

  // --- ask the record (Phase B). Needs no cube, so it works on a broken deployment. ---
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidenceQuestion, setEvidenceQuestion] = useState("");
  const [evidenceAnswer, setEvidenceAnswer] = useState<Answer | null>(null);
  const [evidenceBusy, setEvidenceBusy] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);

  // --- published policy (Phase D). A read of what a build step already extracted, so it
  //     is fetched once on open rather than on every run, like the presets library. ---
  const [policyOpen, setPolicyOpen] = useState(false);
  const [policyMeasures, setPolicyMeasures] = useState<PolicyMeasureItem[]>([]);
  const [policyLoaded, setPolicyLoaded] = useState(false);
  const [policyLoading, setPolicyLoading] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);

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
    async (body: { preset?: string; text?: string; plan?: Plan }) => {
      if (!draw.geometry) return;

      setPlanning(true);
      setPlanError(null);
      try {
        const response = await api.plan({ geometry: draw.geometry, ...body }, lang);
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
    [draw.geometry, lang],
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
        lang,
      );
      setResult(response);
      setSheetOpen(true);
      // Land on whichever result the plan actually produced. A traffic-only plan changes
      // no temperature, so opening on ΔLST would show an empty map for a run that worked.
      setView(response.air && response.stats.n_cells_changed === 0 ? "air" : "delta");
    } catch (error) {
      setSimulateError((error as Error).message);
      setResult(null);
    } finally {
      setRunning(false);
    }
  }, [draw.geometry, selectedWindow, canopy, emissions, plan, lang]);

  const handleMapClick = useCallback(
    (position: Position) => {
      if (draw.drawing) {
        draw.addVertex(position);
        return;
      }
    },
    [draw],
  );

  // -------------------------------------------------------- the search agent ---

  /**
   * Run one search, rendering the trace as it arrives.
   *
   * The lattice is fetched on the first search rather than at boot: it is 121 polygons and
   * a full grid pass server-side, and most sessions never open this panel. Once fetched it
   * stays, keyed to nothing — the blocks are the grid's, not a window's.
   */
  const runSearch = useCallback(
    async (goal: string) => {
      const controller = new AbortController();
      searchAbort.current = controller;

      setSearching(true);
      setSearchError(null);
      setSearchResult(null);
      setTrace([]);
      setSearchStatus("starting the search");

      try {
        if (candidates.length === 0) {
          const lattice = await api.candidates(selectedWindow ?? undefined);
          setCandidates(lattice.candidates);
        }

        await searchStream(
          { goal, window: selectedWindow },
          (event) => {
            setSearchStatus(event.message);
            if (event.attempt) {
              setTrace((current) => [...current, event.attempt!]);
              setLitRegions(event.attempt.region_ids);
            }
            if (event.result) setSearchResult(event.result);
          },
          controller.signal,
        );
      } catch (error) {
        // An abort is the user pressing Stop, not a failure worth a red panel.
        if ((error as Error).name !== "AbortError") {
          setSearchError((error as Error).message);
        }
      } finally {
        setSearching(false);
        setSearchStatus(null);
        setLitRegions([]);
        searchAbort.current = null;
      }
    },
    [candidates.length, selectedWindow],
  );

  const stopSearch = useCallback(() => searchAbort.current?.abort(), []);

  /**
   * Take the search's winner into the ordinary flow: its polygon becomes the drawn one and
   * its levers become the sliders.
   *
   * Deliberately *not* a second result path. The search's own numbers came from the same
   * cores, but the user then has a polygon they can edit, re-run, compare and print — which
   * is the product, and a search result that could only be looked at would be a dead end.
   */
  const applySearchPlan = useCallback(
    (attempt: Attempt) => {
      const regions = new Map(candidates.map((region) => [region.region_id, region]));
      const chosen = attempt.region_ids
        .map((id) => regions.get(id))
        .filter((region): region is Candidate => region !== undefined);
      if (chosen.length === 0) return;

      // One region applies exactly. Several are a MultiPolygon server-side, which this
      // hand-rolled single-ring draw state cannot hold — so the first is applied and the
      // panel says so rather than silently dropping the rest.
      draw.setPolygon(chosen[0]!.geometry.coordinates[0] as Position[]);

      const planting = attempt.plan.actions.find((action) => action.kind === "plant_trees");
      const restriction = attempt.plan.actions.find(
        (action) => action.kind === "restrict_vehicles",
      );
      if (planting && "canopy_fraction_added" in planting && planting.canopy_fraction_added) {
        setCanopy(planting.canopy_fraction_added);
      }
      setEmissions(restriction ? restriction.emission_fraction_removed : 0);
      setPlan(null);
      setPlanError(null);
      setAgentOpen(false);
    },
    [candidates, draw],
  );

  // ----------------------------------------------------------- where it landed ---

  /**
   * Explain the pattern of the run currently on screen.
   *
   * Posts the *same body* `/simulate` was given, because the route re-runs the core: the
   * pattern being described has to be the pattern being looked at, and the only way to
   * guarantee that without trusting a client-supplied delta field is to send the request
   * again rather than the result.
   */
  const explainPattern = useCallback(async () => {
    if (!draw.geometry || !result) return;

    setPatternBusy(true);
    setPatternError(null);
    try {
      // The same 121 blocks the explanation is keyed to, so hovering a row can light one
      // up on the map. Fetched once and reused, exactly as the agent panel does.
      if (candidates.length === 0) {
        setCandidates((await api.candidates(result.window)).candidates);
      }
      setPattern(
        await api.explainSpatial(
          plan
            ? { ...plan.simulate_request, window: result.window }
            : {
                geometry: draw.geometry,
                canopy_fraction_added: canopy,
                emission_fraction_removed: emissions,
                window: result.window,
              },
        ),
      );
    } catch (error) {
      setPatternError((error as Error).message);
      setPattern(null);
    } finally {
      setPatternBusy(false);
    }
  }, [draw.geometry, result, plan, canopy, emissions, candidates.length]);

  // ------------------------------------------------------------ ask the record ---

  const askEvidence = useCallback(async (question: string) => {
    setEvidenceBusy(true);
    setEvidenceError(null);
    try {
      setEvidenceAnswer(await api.ask(question));
    } catch (error) {
      // The message already carries the server's own reason, including the anchors a
      // rejected answer invented. Showing it verbatim is the point: a guard that fired
      // silently is indistinguishable from a feature that never worked.
      setEvidenceError((error as Error).message);
      setEvidenceAnswer(null);
    } finally {
      setEvidenceBusy(false);
    }
  }, []);

  // ------------------------------------------------------ published policy (Phase D) ---

  /**
   * Fetched once, on first open — not eagerly on mount like presets, because unlike the
   * preset library this can be empty on a fresh checkout (nobody has run the extraction
   * yet) and there is no point paying a request for a panel most sessions never open.
   */
  const openPolicy = useCallback(() => {
    setPolicyOpen((open) => !open);
    setSheetOpen(true);
    if (policyLoaded) return;

    setPolicyLoading(true);
    setPolicyError(null);
    api
      .policyMeasures()
      .then((response) => {
        setPolicyMeasures(response.measures);
        setPolicyLoaded(true);
      })
      .catch((error: Error) => setPolicyError(error.message))
      .finally(() => setPolicyLoading(false));
  }, [policyLoaded]);

  /**
   * A policy measure's plan is an explicit `Plan`, checked against the drawn polygon the
   * same way a preset is — `/plan` re-validates it and hands back what `/simulate` takes,
   * so a measure that does not fit refuses with the arithmetic rather than quietly running.
   */
  const applyPolicyPlan = useCallback(
    (mapped: MappedPlan) => {
      setPolicyOpen(false);
      void buildPlan({ plan: mapped.plan });
    },
    [buildPlan],
  );

  /**
   * "Explain this" on a figure that is already on screen.
   *
   * The two worth wiring are the ones a reader most often disbelieves: the 2.5x hindcast
   * correction, and the air validation. Opening the drawer with the question already asked
   * is the difference between an answer and a search box.
   */
  const explainFigure = useCallback(
    (question: string) => {
      setEvidenceQuestion(question);
      setEvidenceOpen(true);
      setSheetOpen(true);
      void askEvidence(question);
    },
    [askEvidence],
  );

  const resetScenario = useCallback(() => {
    draw.clear();
    setResult(null);
    setSimulateError(null);
    setPlan(null);
    setPlanError(null);
    setView("baseline");
    setSheetOpen(false);
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
        // Only while something is using it. The lattice is scaffolding — for a search, or
        // for a pattern breakdown whose rows highlight blocks — and leaving 121 outlines on
        // the map afterwards would clutter the field they describe.
        candidates={agentOpen || pattern ? candidates : undefined}
        highlightedRegions={litRegions}
      />

      {/* ------------------------------------------------------------ top cluster --- */}
      {/* Below `lg` the brand, the pickers, the view switcher and the legend are one
          column pinned to the top edge; `lg:contents` drops this wrapper from the box model
          at the desktop breakpoint so each child's own `lg:absolute` puts it back exactly
          where it always sat. The wrapper takes no pointer events, so only the cards
          themselves are ever between a tap and the map — which is why the legend below is
          as short as it is, and why the results are not in here at all. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex flex-col gap-2 p-3 lg:contents">
        {/* brand + pickers. One card on a phone, two on a desktop: at `lg` the brand
            detaches to the top-left corner and the pickers to the top centre, which is
            what `lg:absolute` on each of them does. Merged, because two cards stacked with
            a gap cost 40 px of map for one line of text. */}
        <div className="glass noise pointer-events-auto z-10 flex items-center gap-3 px-3 py-2
                        lg:absolute lg:top-5 lg:left-5 lg:block lg:px-4 lg:py-3">
          <h1 className="font-mono text-sm tracking-tight whitespace-nowrap text-white">
            Terrarium
          </h1>
          <p className="truncate font-mono text-[0.6rem] text-white/40 lg:mt-0.5 lg:text-[0.65rem]">
            {health.tile.name}, {health.tile.country} · {summary.shape[0]}×{summary.shape[1]} @{" "}
            {summary.resolution_m}&nbsp;m
          </p>
        </div>

        {/* window + layer, top centre */}
        <motion.div
          animate={{ opacity: pending ? 0.45 : 1 }}
          transition={SNAP}
          aria-busy={pending}
          className="glass pointer-events-auto z-10 px-3 py-2 lg:absolute lg:top-5 lg:left-1/2 lg:-translate-x-1/2"
        >
          {/* `w-auto` on both, and no wrap. App.css gives every `select` `width: 100%`,
              which is invisible while this card is shrink-to-fit at `lg` and up — and made
              each picker claim a full row the moment the card went full-width on a phone,
              turning a 40 px bar into a 100 px one. */}
          <div className="flex items-center gap-2">
            <select
              value={selectedWindow ?? ""}
              onChange={(event) => selectWindow(event.target.value)}
              className="w-auto shrink-0 bg-transparent font-mono text-xs text-white outline-none"
              aria-label="Seasonal window"
            >
              {summary.windows.map((label) => (
                <option key={label} value={label} className="bg-[#0a0d12]">
                  {label}
                </option>
              ))}
            </select>
            <span className="h-4 w-px shrink-0 bg-white/10" />
            <select
              value={variable}
              onChange={(event) => setVariable(event.target.value)}
              className="w-auto min-w-0 flex-1 bg-transparent font-mono text-xs text-white/70 outline-none lg:max-w-56 lg:flex-none"
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
            <span className="h-4 w-px shrink-0 bg-white/10" />
            {/* Urdu (Phase C). The rule parser has read Urdu since Phase 11, so a Lahore
                resident could type a plan in Urdu and be answered only in English. It sits
                in the top cluster rather than in a settings menu because it changes what
                the next run comes back as, not a preference. */}
            <select
              value={lang}
              onChange={(event) => setLang(event.target.value as "en" | "ur")}
              className="w-auto shrink-0 bg-transparent font-mono text-xs text-white/70 outline-none"
              aria-label="Brief language"
            >
              <option value="en" className="bg-[#0a0d12]">
                EN
              </option>
              <option value="ur" className="bg-[#0a0d12]">
                اردو
              </option>
            </select>
          </div>
          {/* The window is part of the answer, not a detail: the same planting cools about
              four times more in summer than in winter. Two lines of prose about the layer
              is a desktop luxury; on a phone the error still shows, because an error is
              not a blurb. */}
          <p
            className={`mt-1 max-w-md text-[0.62rem] leading-snug text-white/35 ${
              baseline.error ? "" : "hidden lg:block"
            }`}
          >
            {baseline.loading || pending
              ? "Loading…"
              : (baseline.error ?? plainVariableBlurb(variable))}
          </p>
        </motion.div>

        {/* view switcher, under the pill, once there is something to switch between.
            `overflow-x-auto` on a phone: four labels plus a split slider is wider than
            390 px, and a switcher that clips its last tab has lost a view. */}
        <AnimatePresence>
          {result && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={GLIDE}
              className="glass pointer-events-auto z-10 flex items-center gap-1 overflow-x-auto p-1
                         lg:absolute lg:top-[6.4rem] lg:left-1/2 lg:flex-wrap lg:overflow-visible
                         lg:-translate-x-1/2"
            >
              {/* Phone only, and first in the row rather than last: this is what puts the
                  results sheet back after it has been shut, and the row scrolls
                  horizontally — an `ml-auto` chip at the end of it is a control nobody can
                  see. There is no room for a permanent rail at this width, so the result
                  needs a door rather than a wall. */}
              <button
                type="button"
                onClick={() => setSheetOpen((open) => !open)}
                aria-expanded={sheetOpen}
                className="mr-1 flex shrink-0 items-center gap-1 rounded-md bg-emerald-400/10
                           px-2.5 py-1.5 font-mono text-[0.68rem] text-emerald-200 lg:hidden"
              >
                Brief
                <ChevronDown
                  aria-hidden="true"
                  className={`size-3 transition-transform ${sheetOpen ? "" : "rotate-180"}`}
                />
              </button>

              {views
                .filter((entry) => entry.show)
                .map((entry) => (
                  <button
                    key={entry.key}
                    type="button"
                    onClick={() => setView(entry.key)}
                    className={`shrink-0 rounded-md px-3 py-1.5 font-mono text-[0.68rem] whitespace-nowrap transition-colors ${
                      view === entry.key
                        ? "bg-white/12 text-white"
                        : "text-white/45 hover:text-white/80"
                    }`}
                  >
                    {entry.label}
                  </button>
                ))}

              {view === "compare" && (
                <label className="ml-2 flex shrink-0 items-center gap-2 pr-2">
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
                    className="w-24 accent-white/70 lg:w-28"
                  />
                </label>
              )}

            </motion.div>
          )}
        </AnimatePresence>

        {/* legend + opacity. Bottom left at `lg` and up; in the narrow column it follows
            the pickers, because the bottom edge of a phone already carries the levers, the
            toolbar and the basemap's own attribution and controls. Compact there: the ramp
            and the opacity slider share a row, and the D9 sentence — which must appear
            wherever the temperature does, so it is never the thing that gets dropped —
            sits under them at the smallest size this design uses. */}
        <div className="glass noise pointer-events-auto z-10 px-3 py-2.5 lg:absolute lg:bottom-6 lg:left-5 lg:w-80 lg:p-4">
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

          <label className="mt-2 flex items-center gap-3 lg:mt-3">
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
          <p className="mt-2 border-t border-white/10 pt-2 text-[0.58rem] leading-relaxed text-white/35 lg:mt-3 lg:pt-3 lg:text-[0.62rem]">
            <strong className="text-white/55">lst_c</strong> and{" "}
            <strong className="text-white/55">ΔLST</strong> are mid-morning land surface
            temperature (~10:30 local, Landsat ST_B10) — the radiating surface, not air
            temperature, and not the afternoon peak.
          </p>
        </div>
      </div>

      {/* ------------------------------------------------------ results, rail or sheet --- */}
      {/* One tree, two placements. At `lg` and up it is the floating rail down the right
          hand side it has always been. Below that it is a sheet against the bottom edge,
          over everything, dismissible — because a phone cannot show a brief and the field
          the brief is about at the same time, and pretending otherwise is what produced a
          results column with 50vh of scroll inside a 100vh viewport. */}
      <div
        className={`pointer-events-auto z-30 flex flex-col gap-3 overflow-x-hidden
                    overflow-y-auto lg:absolute lg:inset-x-auto lg:top-5 lg:right-5 lg:bottom-auto
                    lg:z-10 lg:max-h-[calc(100dvh-2.5rem)] lg:w-96 ${
                      (result || agentOpen || evidenceOpen || policyOpen) && sheetOpen
                        ? "absolute inset-x-0 bottom-0 max-h-[78dvh] rounded-t-2xl bg-[#0a0d12]/80 p-3 backdrop-blur-xl lg:rounded-none lg:bg-transparent lg:p-0 lg:backdrop-blur-none"
                        : "max-lg:hidden"
                    }`}
      >
        {/* The sheet's own handle. `lg:hidden` because the rail is never in the way. */}
        <button
          type="button"
          onClick={() => setSheetOpen(false)}
          className="sticky top-0 -mt-1 flex w-full shrink-0 justify-center py-1 lg:hidden"
          aria-label="Hide the brief"
        >
          <span className="h-1 w-10 rounded-full bg-white/25" />
        </button>

        <AnimatePresence mode="popLayout">
          {/* First in the rail while it is open: the search is what the user just asked
              for, and it is also the thing that produces the polygon everything below
              describes. */}
          {agentOpen && (
            <FloatingPanel key="agent">
              <AgentPanel
                trace={trace}
                status={searchStatus}
                result={searchResult}
                running={searching}
                error={searchError}
                planner={planner}
                onSearch={(goal) => void runSearch(goal)}
                onStop={stopSearch}
                onApply={applySearchPlan}
              />
            </FloatingPanel>
          )}

          {evidenceOpen && (
            <FloatingPanel key="evidence">
              <EvidencePanel
                answer={evidenceAnswer}
                busy={evidenceBusy}
                error={evidenceError}
                onAsk={(question) => void askEvidence(question)}
                initialQuestion={evidenceQuestion}
              />
            </FloatingPanel>
          )}

          {policyOpen && (
            <FloatingPanel key="policy">
              <PolicyPanel
                measures={policyMeasures}
                loading={policyLoading}
                error={policyError}
                onApply={applyPolicyPlan}
                hasPolygon={!!draw.geometry}
              />
            </FloatingPanel>
          )}

          {result && (
            <FloatingPanel key="plain">
              <PlainPanel brief={result.brief} />

              {/* "Explain this" on the figure a reader most often disbelieves. The 2.5x
                  correction is stated in every brief and justified in the plan; this is
                  the shortest path between the two. */}
              <button
                type="button"
                onClick={() =>
                  explainFigure(
                    "Why is the modelled cooling divided by 2.5 before it is quoted?",
                  )
                }
                className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg
                           border border-white/8 py-1.5 text-[0.68rem] text-white/50
                           hover:bg-white/8"
              >
                <BookOpen className="size-3" />
                Why is this corrected by 2.5×?
              </button>

              <button
                type="button"
                onClick={() => window.print()}
                className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg
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
                  <PatternPanel
                    explanation={pattern}
                    busy={patternBusy}
                    error={patternError}
                    onExplain={() => void explainPattern()}
                    onHighlight={setLitRegions}
                  />
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

      {/* ------------------------------------------------------- levers, above toolbar --- */}
      <AnimatePresence>
        {draw.complete && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            transition={GLIDE}
            className="glass noise absolute bottom-[4.75rem] left-1/2 z-10 max-h-[38dvh]
                       w-[min(36rem,calc(100vw-1.5rem))] -translate-x-1/2 overflow-y-auto p-3
                       lg:bottom-60 lg:max-h-none lg:w-[min(36rem,calc(100vw-3rem))] lg:p-4 xl:bottom-24"
          >
            <div className="grid gap-3 lg:grid-cols-2 lg:gap-4">
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
      {/* MapLibre puts its attribution across the bottom edge and its zoom puck and scale
          bar in the bottom-right corner. At `lg` the toolbar is centred and clears both;
          at 390 px it sat directly on the attribution line, so it is lifted clear here. */}
      <motion.div
        layout
        transition={GLIDE}
        className="glass absolute bottom-9 left-1/2 z-20 flex -translate-x-1/2 items-center
                   gap-1 p-1.5 lg:bottom-6"
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

          {/* The search agent needs no polygon — that is D26's whole point — so this sits
              beside "Draw zone" rather than behind it: the two are alternative ways in, not
              a sequence. */}
          {!draw.drawing && (
            <ToolButton
              key="agent"
              icon={Sparkles}
              label={agentOpen ? "Hide search" : "Search the tile"}
              onClick={() => {
                setAgentOpen((open) => !open);
                setSheetOpen(true);
              }}
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
          {/* Needs neither a cube nor a polygon — the corpus is markdown in the repo — so
              it is always available, including on a deployment whose Zarr store failed. */}
          {!draw.drawing && (
            <ToolButton
              key="evidence"
              icon={BookOpen}
              label={evidenceOpen ? "Hide record" : "Ask the record"}
              onClick={() => {
                setEvidenceOpen((open) => !open);
                setSheetOpen(true);
              }}
            />
          )}
          {/* A read of a DuckDB table, same as the presets library — no cube needed. */}
          {!draw.drawing && (
            <ToolButton
              key="policy"
              icon={Landmark}
              label={policyOpen ? "Hide policy" : "Published policy"}
              onClick={openPolicy}
            />
          )}

          {(draw.complete || draw.drawing) && (
            <ToolButton key="clear" icon={Trash2} label="Clear" onClick={resetScenario} />
          )}
        </AnimatePresence>
      </motion.div>

      {/* Hidden below `sm`: a keyboard shortcut is not an affordance on a phone, and
          this line sat on top of the basemap attribution there. The palette is still one
          tap away from the toolbar's own Plan button. */}
      {!draw.complete && !draw.drawing && (
        <p
          className="pointer-events-none absolute bottom-2 left-1/2 z-10 hidden -translate-x-1/2
                     font-mono text-[0.6rem] tracking-[0.18em] text-white/25 uppercase lg:block"
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
