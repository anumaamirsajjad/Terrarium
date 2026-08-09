/**
 * The three layers, exploded on scroll.
 *
 * CSS 3D rather than a second WebGL context. The hero already owns one, and mounting
 * another for what is fundamentally three stacked rectangles would cost more than the
 * whole rest of the page — `transform-style: preserve-3d` composites on the GPU and needs
 * no renderer at all.
 *
 * The order is the constraint, not a diagram convention: data flows strictly downward and
 * nothing in a lower layer imports from a higher one. Reading the stack top to bottom is
 * reading the dependency direction, which is why the arrow is drawn and labelled.
 */

import { type MotionValue, motion, useScroll, useTransform } from "motion/react";
import { useRef } from "react";

import { useMarginNote } from "./marginNote";
import { Eyebrow, InlineCaveat, Rule } from "./primitives";

const LAYERS = [
  {
    tier: "3",
    name: "Intelligence",
    modules: "api/ · dsl/",
    body: "The composition root. Loads the cube and the model once at startup, turns a drawn polygon into a mask, validates a plan against what the tile can actually hold, and refuses it with the arithmetic when it cannot.",
    tint: "from-straw/18",
    edge: "border-straw/25",
  },
  {
    tier: "2",
    name: "Physics core",
    modules: "cores/",
    body: "Pure functions. No file reads, no network, no global state — everything a simulator needs arrives as an argument, including its trained model. Given the same inputs it returns the same outputs, always.",
    tint: "from-shoal/18",
    edge: "border-shoal/25",
  },
  {
    tier: "1",
    name: "State cube",
    modules: "ingest/ · state/",
    body: "Messy external reality resolved into one analysis-ready dataset: a single CRS, one resolution, one bounding box, one set of coordinates. If two layers do not align, that is a bug here and never in a core.",
    tint: "from-tide/18",
    edge: "border-tide/25",
  },
] as const;

/**
 * One slab of the stack.
 *
 * Its own component rather than a branch inside the map, because each needs three
 * `useTransform` calls and hooks cannot be called from a loop body — with a constant-length
 * array it happens to work, which is exactly the kind of thing that stops working quietly
 * when someone adds a fourth layer.
 */
function Slab({
  layer,
  offset,
  index,
  enter,
  depart,
  tilt,
}: {
  layer: (typeof LAYERS)[number];
  offset: number;
  index: number;
  enter: MotionValue<number>;
  depart: MotionValue<number>;
  tilt: MotionValue<number>;
}) {
  // Arriving: a plain rise, staggered down the stack. No rotation — while a slab is on
  // screen it is a card to be read, and reading it through a 40° foreshortening is work
  // nobody asked the reader to do.
  const enterY = useTransform(enter, [0, 1], [70 + index * 26, 0]);
  const enterOpacity = useTransform(enter, [0, 0.7], [0, 1]);

  // Leaving: the stack fans apart and tips away, which is the moment the layering itself
  // is the point. It happens on the way out, so it never costs legibility.
  const departY = useTransform(depart, [0, 1], [0, offset * 46]);
  const z = useTransform(depart, [0, 1], [0, offset * 78]);

  const y = useTransform([enterY, departY] as const, ([a, b]: number[]) => a! + b!);

  return (
    <motion.article
      style={{ rotateX: tilt, y, z, opacity: enterOpacity }}
      className={`glass noise w-full max-w-2xl border ${layer.edge} bg-gradient-to-br
                  ${layer.tint} to-transparent p-7 [transform-style:preserve-3d]`}
    >
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="font-mono text-xl tracking-tight text-white">{layer.name}</h3>
        <span className="font-mono text-[0.7rem] text-white/35">{layer.modules}</span>
      </div>
      <p className="mt-3 max-w-xl text-sm leading-relaxed text-white/50">{layer.body}</p>
    </motion.article>
  );
}

const NOTE = {
  subject: "Layer purity is enforced by review, not by the compiler.",
  body: "Python has no import firewall. The rule that a core never reads a file holds because it is checked, and `cores/` importing the config module is the canary that it has stopped holding.",
};

export default function Architecture() {
  const track = useRef<HTMLDivElement>(null);
  const section = useMarginNote("architecture", NOTE);

  const { scrollYProgress } = useScroll({
    target: track,
    offset: ["start end", "end start"],
  });

  /*
   * Two phases, and they do not overlap.
   *
   * With this section's offsets, 0 is "top edge entering from the bottom of the viewport"
   * and 1 is "bottom edge leaving past the top". So the middle of that range is the span
   * where the stack is actually being read, and it must be flat and square-on there.
   *
   * The earlier version tilted from `0.1` — before the section had finished arriving —
   * so the cards were already lying back by the time they were legible. The tilt now
   * belongs entirely to the exit.
   */
  const enter = useTransform(scrollYProgress, [0.08, 0.32], [0, 1]);
  const depart = useTransform(scrollYProgress, [0.5, 0.86], [0, 1]);
  // 38° at full departure. The slabs are on their way off screen by then, so the
  // foreshortening costs nothing.
  const tilt = useTransform(depart, [0, 1], [0, 38]);

  return (
    <section ref={section} className="mx-auto max-w-[92rem] px-8 py-32">
      <div className="grid gap-16 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <div className="lg:sticky lg:top-32 lg:self-start">
          <Eyebrow>Three layers</Eyebrow>
          <h2 className="mt-6 font-mono text-4xl leading-[0.95] tracking-tight text-white">
            Data flows
            <br />
            one way.
          </h2>
          <Rule className="mt-8" />
          <p className="mt-8 text-sm leading-relaxed text-white/50">
            The boundaries are what let the physics be tested without a network and the
            emulator be swapped without touching the API. They are also what keep a
            simulator honest: a core cannot quietly re-read a GeoTIFF to get a number the
            cube did not give it.
          </p>

          <InlineCaveat note={NOTE} />
        </div>

        <div ref={track} className="[perspective:1400px]">
          <div className="flex flex-col items-center gap-3 py-8 [transform-style:preserve-3d]">
            {LAYERS.map((layer, index) => (
              <Slab
                key={layer.tier}
                layer={layer}
                index={index}
                // Fan out from the middle: tier 3 rises, tier 1 drops.
                offset={(index - 1) * -1}
                enter={enter}
                depart={depart}
                tilt={tilt}
              />
            ))}
          </div>

          <div className="flex items-center justify-center gap-3 font-mono text-[0.65rem] tracking-[0.2em] text-white/25 uppercase">
            <span className="h-px w-12 bg-gradient-to-r from-transparent to-white/20" />
            imports point down only
            <span className="h-px w-12 bg-gradient-to-l from-transparent to-white/20" />
          </div>
        </div>
      </div>
    </section>
  );
}
