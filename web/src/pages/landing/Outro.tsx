/**
 * What comes out, and where to go next.
 *
 * The brief mockup lists what the real document carries, in the real order. Two of those
 * are structural guarantees rather than usual practice: `uncertainties` is never empty,
 * and `confidence` has no "high" — nothing in this project has earned the word.
 */

import { ArrowUpRight } from "lucide-react";
import { motion, useScroll, useTransform } from "motion/react";
import { useRef } from "react";

import { RISE, STAGGER } from "../../motion/springs";
import { Eyebrow, Rule } from "./primitives";

export default function Outro() {
  const sheet = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: sheet, offset: ["start end", "end start"] });
  // A slow counter-drift, so the sheet sits in its own plane rather than on the page.
  const lift = useTransform(scrollYProgress, [0, 1], [40, -40]);

  return (
    <>
      <motion.section
        variants={STAGGER}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, amount: 0.25 }}
        className="mx-auto grid max-w-[92rem] items-center gap-10 px-5 py-20 sm:gap-16 sm:px-8 sm:py-32 lg:grid-cols-2"
      >
        <motion.div variants={RISE}>
          <Eyebrow>dsl/explain.py</Eyebrow>
          <h2 className="mt-6 font-mono text-4xl tracking-tight text-white md:text-5xl">
            A council brief, not a screenshot.
          </h2>
          <Rule className="mt-8" />
          <p className="mt-6 max-w-md leading-relaxed text-white/55">
            One sheet with the modelled change, the hindcast correction, who receives the
            cooling, and every caveat that attaches to a figure on it. Written from
            templates on the server: a generative explainer would occasionally restate a
            number it was never given, and would smooth a caveat into a hedge.
          </p>
          <p className="mt-4 max-w-md leading-relaxed text-white/55">
            It prints through the browser&rsquo;s own print dialog. No rendering service, no
            font stack, no extra dependency: a button every browser already ships.
          </p>
          <p className="mt-4 max-w-md leading-relaxed text-white/55">
            Not sure what to draw? <strong className="text-white/80">Search the tile</strong>{" "}
            runs the same cores across a 2&nbsp;km lattice and scores each attempt against a
            greedy baseline it has to beat. Once you have a result,{" "}
            <strong className="text-white/80">Ask about this result</strong> answers
            follow-up questions grounded only in that result&rsquo;s own brief, never the
            wider web. Both need a free key configured on the server; without one, they say
            so rather than guess.
          </p>
        </motion.div>

        <motion.div
          ref={sheet}
          variants={RISE}
          style={{ y: lift }}
          whileHover={{ rotate: 0, scale: 1.02 }}
          className="glass noise hover:shadow-shoal/20 aspect-[1/1.294] w-full max-w-sm rotate-[-2deg]
                     justify-self-center p-8 transition-shadow hover:shadow-[0_0_60px_-15px]"
        >
          <p className="border-b border-white/20 pb-3 text-lg font-semibold tracking-tight text-white">
            Street trees along the Canal
          </p>
          <p className="mt-1 font-mono text-[0.65rem] text-white/35">
            Lahore, Pakistan · 2024-summer · 6.38 km²
          </p>

          <dl className="mt-6 space-y-2 font-mono text-[0.7rem]">
            {[
              ["Mean ΔLST inside", "−0.51 °C"],
              ["After 2.5× correction", "−0.20 °C"],
              ["Ceiling for this window", "2.60 °C"],
              ["Confidence", "moderate"],
            ].map(([term, value]) => (
              <div key={term} className="flex justify-between border-b border-white/5 pb-2">
                <dt className="text-white/35">{term}</dt>
                <dd className="text-white/75">{value}</dd>
              </div>
            ))}
          </dl>

          <p className="mt-6 font-mono text-[0.6rem] tracking-wider text-white/30 uppercase">
            What this does not prove
          </p>
          <ul className="mt-2 space-y-1.5 text-[0.7rem] leading-relaxed text-white/45">
            <li>· Surface temperature at ~10:30, not air temperature.</li>
            <li>· Modelled, and against reality it runs hot.</li>
          </ul>
        </motion.div>
      </motion.section>

      <footer className="border-t border-white/10">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <div className="flex flex-col gap-10 md:flex-row md:items-end md:justify-between">
            <div>
              <p className="text-3xl tracking-tight text-white">Terrarium</p>
              <p className="mt-2 max-w-sm text-sm text-white/40">
                Draw an intervention on a real street. Get modelled deltas in surface
                temperature, locally-generated PM2.5, and who receives them.
              </p>
            </div>

            <nav className="flex flex-wrap gap-2">
              <a
                href="#/app"
                className="group inline-flex items-center gap-2 rounded-full border border-shoal/30
                           bg-shoal/10 px-5 py-2.5 text-sm text-white transition-colors
                           hover:bg-shoal/20"
              >
                Open the dashboard
                <ArrowUpRight className="size-4 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
              </a>
            </nav>
          </div>

          {/* D9. This label must appear wherever the temperature does, and the landing
              page is the first place a visitor meets the number. */}
          <p className="mt-14 border-t border-white/5 pt-8 text-xs leading-relaxed text-white/30">
            <span className="font-mono text-white/50">lst_c</span> and{" "}
            <span className="font-mono text-white/50">ΔLST</span> are mid-morning land
            surface temperature (~10:30 local, Landsat ST_B10): the radiating surface, not
            air temperature, and not the afternoon peak. PM2.5 figures are this tile&rsquo;s
            own locally-generated increment from its roads, quoted as a delta and never as a
            level: the regional background that dominates Lahore&rsquo;s absolute PM2.5 is
            absent by construction, and the emission factors are uncalibrated literature
            values for a South Asian fleet.
          </p>
        </div>
      </footer>
    </>
  );
}
