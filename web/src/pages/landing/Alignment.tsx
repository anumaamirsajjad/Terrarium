/**
 * Seven sources, one grid — the layer the rest of the page stands on.
 *
 * Every figure the simulators quote is a figure about *one* dataset: a single CRS, a single
 * resolution, a single bounding box, one set of coordinates. Nothing arrives that way.
 * Landsat is 30 m, Sentinel-2 is 10, WorldCover is 10, the DEM is 30, WorldPop is 100, the
 * roads are not a raster at all, and the reanalysis has no spatial dimension to resample
 * onto. Scrolling this section is watching that resolved, because if two layers do not
 * align it is a bug here and never in a core.
 *
 * The second half is the part that is easy to get wrong quietly. Resampling method is a
 * property of what a variable *means*, not a default, and population is the variable that
 * proves it: measured on the real WorldPop raster, the bilinear that is correct for every
 * temperature on the tile deletes 1.6 million residents. Every equity figure divides by
 * that number, so the scroll ticks them out of existence rather than asserting it in prose.
 *
 * 2D, deliberately. `Architecture` a section later is the page's 3D idiom, and register is
 * a two-dimensional idea — sheets landing on each other read as alignment where the same
 * sheets in perspective read as a stack.
 */

import {
  type MotionValue,
  motion,
  useMotionTemplate,
  useScroll,
  useTransform,
} from "motion/react";
import { useRef } from "react";

import { useMarginNote } from "./marginNote";
import { Eyebrow, Rule } from "./primitives";

/** The analysis grid, as pixels on this stage. One cell is 100 m in the cube. */
const LOCKED_CELL = 32;

/**
 * A clamped 0→1 ramp between two points on a scroll progress.
 *
 * The transformer form, never `useTransform(scroll, [a, b], [0, 1])`, and that is not a
 * style preference. Motion hands a *range*-form transform driven by scroll to a native
 * scroll-driven animation when the target property is one the compositor accelerates —
 * opacity is — and it binds that animation to the element's own `ViewTimeline` rather than
 * to this section's track. Every sheet then faded in on cue and faded straight back out as
 * the section left, with the correct value still sitting in the inline style underneath. A
 * transformer function cannot be expressed as keyframes, so it stays in JS and reads the
 * motion value it was actually given.
 */
function useRamp(value: MotionValue<number>, from: number, to: number): MotionValue<number> {
  return useTransform(value, (v) => Math.min(1, Math.max(0, (v - from) / (to - from))));
}

/** The page's own raster substrate, reused so the sheets are ruled like everything else. */
const GRID_IMAGE = [
  "linear-gradient(to right, rgba(255,255,255,0.16) 1px, transparent 1px)",
  "linear-gradient(to bottom, rgba(255,255,255,0.16) 1px, transparent 1px)",
].join(", ");

interface Source {
  id: string;
  /** Native resolution, as the collection publishes it. */
  native: string;
  gives: string;
  method: "nearest" | "bilinear" | "sum" | "binned";
  /** Cell size on this stage before alignment, px. Proportional to the native metres. */
  cell: number;
  /** Scattered offset, as a percentage of the sheet — so it survives a narrow viewport. */
  x: number;
  y: number;
  rotate: number;
  scale: number;
  /** Vector, not raster: it has no cell until the ingest gives it one. */
  vector?: boolean;
}

/**
 * Colour is the resampling method, not the dataset.
 *
 * It is the one thing on this stage worth encoding, because it is the thing that is chosen
 * rather than reported — and the ledger beside the stage lights the same three colours.
 */
const METHOD_COLOUR: Record<Source["method"], string> = {
  nearest: "#f0d26e",
  bilinear: "#1e78aa",
  sum: "#6ebeaf",
  binned: "#d66e32",
};

const SOURCES: readonly Source[] = [
  {
    id: "landsat-c2-l2",
    native: "30 m",
    gives: "ST_B10 → lst_c",
    method: "bilinear",
    cell: 9.6,
    x: -38,
    y: -30,
    rotate: -8,
    scale: 0.6,
  },
  {
    id: "sentinel-2-l2a",
    native: "10 m",
    gives: "ndvi · ndbi · albedo",
    method: "bilinear",
    cell: 3.2,
    x: 35,
    y: -36,
    rotate: 7,
    scale: 0.55,
  },
  {
    id: "cop-dem-glo-30",
    native: "30 m",
    gives: "elevation_m",
    method: "bilinear",
    cell: 9.6,
    x: -40,
    y: 27,
    rotate: 6,
    scale: 0.58,
  },
  {
    id: "esa-worldcover",
    native: "10 m",
    gives: "land cover class",
    method: "nearest",
    cell: 3.2,
    x: 38,
    y: 31,
    rotate: -6,
    scale: 0.54,
  },
  {
    id: "worldpop 2020",
    native: "100 m",
    gives: "population",
    method: "sum",
    cell: 32,
    x: 3,
    y: -45,
    rotate: 3,
    scale: 0.5,
  },
  {
    id: "overpass / osm",
    native: "vector",
    gives: "pm25_emission_g_s",
    method: "binned",
    cell: 14,
    x: -4,
    y: 45,
    rotate: -4,
    scale: 0.52,
    vector: true,
  },
];

/**
 * One source, landing.
 *
 * Its own component because it needs six `useTransform` calls, and hooks cannot be called
 * from a loop body — with a constant-length array it happens to work, which is exactly the
 * kind of thing that stops working quietly when someone adds an eighth source.
 */
function Sheet({
  source,
  index,
  progress,
  collapse,
}: {
  source: Source;
  index: number;
  progress: MotionValue<number>;
  collapse: MotionValue<number>;
}) {
  // Staggered, so the sheets lock one after another rather than as one snap. The last one
  // is in register well before the deck collapses.
  const start = 0.1 + index * 0.06;
  const land = useRamp(progress, start, start + 0.3);

  // Both phases in one expression per axis: scattered while landing, then a tidy deck
  // offset that the collapse takes back out. Two motion values fighting for the same
  // property would make the sheet jump the moment the second phase started.
  const x = useTransform(
    [land, collapse] as const,
    ([l, c]: number[]) => `${source.x * (1 - l!) + index * 1.6 * l! * (1 - c!)}%`,
  );
  const y = useTransform(
    [land, collapse] as const,
    ([l, c]: number[]) => `${source.y * (1 - l!) - index * 1.6 * l! * (1 - c!)}%`,
  );
  const rotate = useTransform(land, [0, 1], [source.rotate, 0]);
  const scale = useTransform(land, [0, 1], [source.scale, 1]);
  // Every sheet is on screen before the first one locks, and they fade in together. Tying
  // this to `land` instead made them arrive one at a time already half-aligned, so the
  // scattered state the section is about was never once visible.
  const opacity = useRamp(progress, 0.01, 0.07);

  // The grid itself converges. A sheet arriving at the right place with the wrong cell size
  // is still not aligned, and the cell size is the thing the reader is being told about.
  const cell = useTransform(land, [0, 1], [source.cell, LOCKED_CELL]);
  const cellSize = useMotionTemplate`${cell}px ${cell}px`;
  // A vector layer has no cell until the ingest gives it one, so its grid arrives with it.
  const gridOpacity = useTransform(land, (l) => (source.vector ? l : 1));

  // The labels overlap once the deck closes; they hand off to the cube's own.
  const labelOpacity = useTransform(collapse, (c) => 1 - Math.min(1, c / 0.45));
  const colour = METHOD_COLOUR[source.method];

  // The deck closes into one thing, so what is on top stops being a source. Without these
  // two the last sheet in the array — the road network — simply sat over the others and
  // the cube read as a page of orange diagonals rather than as a grid.
  const roadOpacity = useTransform(collapse, (c) => (1 - c) * 0.7);
  const border = useTransform(collapse, [0, 1], [colour, "rgba(255,255,255,0.18)"]);

  return (
    <motion.div
      style={{ x, y, rotate, scale, opacity, borderColor: border }}
      className="absolute inset-[19%] overflow-hidden rounded-sm border bg-black/45 backdrop-blur-[2px]"
    >
      {source.vector && (
        <motion.div
          aria-hidden
          className="absolute inset-0"
          style={{
            opacity: roadOpacity,
            backgroundImage: `repeating-linear-gradient(58deg, ${colour}00 0 9px, ${colour}80 9px 10px),
                              repeating-linear-gradient(-24deg, ${colour}00 0 17px, ${colour}55 17px 18px)`,
          }}
        />
      )}
      <motion.div
        aria-hidden
        className="absolute inset-0"
        style={{ backgroundImage: GRID_IMAGE, backgroundSize: cellSize, opacity: gridOpacity }}
      />
      <motion.div
        style={{ opacity: labelOpacity }}
        className="absolute inset-x-0 top-0 flex items-baseline justify-between gap-2 px-2 py-1.5
                   font-mono text-[0.55rem] whitespace-nowrap"
      >
        <span className="text-white/75">{source.id}</span>
        <span style={{ color: colour }}>{source.native}</span>
      </motion.div>
    </motion.div>
  );
}

/** The three rules, in the order the cube declares them. */
const RULES: readonly { method: Source["method"]; kind: string; why: string }[] = [
  {
    method: "nearest",
    kind: "labels",
    why: "Averaging class 10 with class 50 gives class 30, which is a lie.",
  },
  {
    method: "bilinear",
    kind: "intensive",
    why: "A temperature means the same thing whatever the pixel's area.",
  },
  {
    method: "sum",
    kind: "extensive",
    why: "Population is a head count per cell, not a rate.",
  },
];

export default function Alignment() {
  const track = useRef<HTMLDivElement>(null);
  // The ref goes on the sticky pane, never on the tall track: `useInView` wants a fraction
  // of the *element* on screen, and 40 % of two and a half viewports never is.
  const pane = useMarginNote<HTMLDivElement>("alignment", {
    subject: "A remote fetch that succeeds is not a fetch that completed.",
    body: "WorldPop's server drops connections mid-transfer without raising, and a short read yields a valid-looking GeoTIFF with rows missing. The ingest verifies the content length and renames a partial file into place, because otherwise a truncated download looks exactly like a cache hit.",
  });

  const { scrollYProgress } = useScroll({ target: track, offset: ["start start", "end end"] });

  // Three beats, and they do not overlap: the sheets land, the deck closes into one cube,
  // then the population drains. Overlapping the last two put the wrong number on screen
  // while the cube was still forming, which read as the cube deleting the people.
  const collapse = useRamp(scrollYProgress, 0.72, 0.84);
  const cubeOpacity = useRamp(scrollYProgress, 0.74, 0.86);
  const drain = useRamp(scrollYProgress, 0.88, 0.99);

  const head = useTransform(drain, (d) => 6_259_308 + (4_575_662 - 6_259_308) * d);
  const headText = useTransform(head, (value: number) => Math.round(value).toLocaleString());
  const headColour = useTransform(drain, [0, 1], ["#6ebeaf", "#f87171"]);
  const sumLabel = useTransform(drain, (d) => 1 - Math.min(1, d / 0.35));
  const bilinearLabel = useRamp(drain, 0.3, 0.7);
  const lossOpacity = useRamp(drain, 0.5, 0.85);

  return (
    <section className="mx-auto max-w-[92rem] px-8">
      <div ref={track} className="relative h-[260vh]">
        <div ref={pane} className="sticky top-0 flex h-screen items-center">
          <div className="grid w-full items-center gap-16 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
            <div>
              <Eyebrow>ingest/ · state/</Eyebrow>
              <h2 className="mt-6 font-mono text-4xl leading-[0.95] tracking-tight text-white md:text-5xl">
                Seven sources.
                <br />
                One grid.
              </h2>
              <Rule className="mt-8" />
              <p className="mt-8 max-w-md text-sm leading-relaxed text-white/50">
                Each arrives on its own grid, in its own projection, with its own idea of
                where a pixel starts. What a simulator reads is what is left after that is
                resolved — one CRS, one resolution, one bounding box, one set of
                coordinates, on a 100 m cell.
              </p>

              <dl className="mt-9 space-y-3">
                {RULES.map((rule) => (
                  <div key={rule.method} className="flex gap-3">
                    <dt
                      className="w-16 shrink-0 font-mono text-[0.7rem]"
                      style={{ color: METHOD_COLOUR[rule.method] }}
                    >
                      {rule.method}
                    </dt>
                    <dd className="text-[0.72rem] leading-relaxed text-white/40">
                      <span className="text-white/70">{rule.kind}</span> — {rule.why}
                    </dd>
                  </div>
                ))}
                <div className="flex gap-3">
                  <dt className="w-16 shrink-0 font-mono text-[0.7rem] text-white/25">none</dt>
                  <dd className="text-[0.72rem] leading-relaxed text-white/40">
                    <span className="text-white/70">the reanalysis</span> — the seventh
                    source has no spatial dimension, and nothing is resampled onto a
                    dimension that does not exist.
                  </dd>
                </div>
              </dl>

              {/* The number the method costs. It is scrubbed rather than triggered: the
                  reader scrolls the residents out of existence, which is what a wrong
                  resampling verb does silently. */}
              <div className="mt-10 border-t border-white/10 pt-6">
                <p className="font-mono text-[0.62rem] tracking-[0.18em] text-white/30 uppercase">
                  WorldPop · this tile
                </p>
                <motion.p
                  style={{ color: headColour }}
                  className="mt-2 font-mono text-3xl tracking-tight tabular-nums"
                >
                  {headText}
                </motion.p>
                <div className="relative mt-1 h-4">
                  <motion.span
                    style={{ opacity: sumLabel }}
                    className="absolute inset-0 font-mono text-[0.65rem] text-white/40"
                  >
                    residents, resampled by <span className="text-shoal">sum</span>
                  </motion.span>
                  <motion.span
                    style={{ opacity: bilinearLabel }}
                    className="absolute inset-0 font-mono text-[0.65rem] text-white/40"
                  >
                    the same tile, resampled <span className="text-red-400">bilinear</span>
                  </motion.span>
                </div>
                <motion.p
                  style={{ opacity: lossOpacity }}
                  className="mt-3 max-w-md text-[0.72rem] leading-relaxed text-white/45"
                >
                  <span className="text-red-400">−26 %.</span> 1.6 million people deleted by
                  interpolating a head count. Every equity figure divides by this number,
                  and nothing in the shapes, the coordinates or the cube summary would have
                  said so.
                </motion.p>
              </div>
            </div>

            <div className="relative mx-auto aspect-square w-full max-w-[34rem]">
              {SOURCES.map((source, index) => (
                <Sheet
                  key={source.id}
                  source={source}
                  index={index}
                  progress={scrollYProgress}
                  collapse={collapse}
                />
              ))}

              {/* What the deck became. Fades in under the closing sheets rather than
                  replacing them, so the cube is visibly the same rectangle. */}
              <motion.div
                style={{ opacity: cubeOpacity }}
                className="pointer-events-none absolute inset-[19%] flex flex-col justify-end
                           rounded-sm p-3"
              >
                <div className="flex items-baseline justify-between font-mono text-[0.6rem]">
                  <span className="text-white/80">cube.zarr</span>
                  <span className="text-shoal">201 × 202 · 100 m</span>
                </div>
                <div className="mt-1 h-px w-full bg-white/15" />
                <p className="mt-1.5 font-mono text-[0.55rem] text-white/35">
                  EPSG:32643 · one bounding box · one set of coordinates
                </p>
              </motion.div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
