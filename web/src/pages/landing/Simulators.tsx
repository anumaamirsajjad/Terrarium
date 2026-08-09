/**
 * Three simulators, as a sticky-scroll narrative.
 *
 * The eyebrow is each core's module path, not a sequence number. These three run in
 * parallel and nothing orders them — numbering them 01/02/03 would assert a progression
 * that does not exist, where `cores/thermal/` tells a reader something true and checkable.
 *
 * The copy states each core's own limitation rather than its best case. The thermal
 * core's 2.5x over-prediction is the most useful thing this project knows about it, and a
 * page that hides it is describing different software.
 */

import { motion, useMotionValueEvent, useScroll, useTransform } from "motion/react";
import { useRef, useState } from "react";

import { GLIDE } from "../../motion/springs";
import EquityViz from "./EquityViz";
import { useMarginNote } from "./marginNote";
import PlumeViz from "./PlumeViz";
import ThermalViz from "./ThermalViz";
import { Counter, Eyebrow } from "./primitives";

const PANES = [
  {
    key: "thermal",
    module: "cores/thermal/",
    title: "Thermal core",
    body: "Predicts mid-morning land surface temperature. 100 m resolution. Built-in hindcast validation exposes our own 2.5× space-for-time over-prediction.",
    note: "A LightGBM emulator trained against observed Landsat ST_B10, so it learns this city's empirical relationship rather than a hand-rolled surface energy balance. Surface, not air: ~10:30 local, and never the afternoon peak.",
    figures: [
      { value: 2.5, decimals: 1, suffix: "×", label: "over-prediction, hindcast" },
      { value: 12, decimals: 0, suffix: " / 12", label: "test configs it ran hot in" },
    ],
    Viz: ThermalViz,
    margin: {
      subject: "Blocked cross-validation proves generalisation across space, not across change.",
      body: "It says the emulator predicts an unseen part of the tile. It says nothing about whether it predicts the effect of an intervention — which is what the hindcast was built to answer, and the hindcast says it over-predicts.",
    },
  },
  {
    key: "air",
    module: "cores/air.py",
    title: "Air dispersion",
    body: "Simulates locally-generated PM2.5 via OSM and seasonal FFT dispersion. Winter inversion captures 6–9× the concentration of identical summer sources.",
    note: "A uniform wind across a 20 km tile makes superposition a convolution, so the whole tile resolves in one FFT. Scored against 53 OpenAQ monitors it beats the null model in winter — MAE 40.6 against 51.0, correlation +0.53.",
    figures: [
      { value: 40.6, decimals: 1, suffix: "", label: "winter MAE vs null 51.0" },
      { value: 53, decimals: 0, suffix: "", label: "monitors, leave-one-out" },
    ],
    Viz: PlumeViz,
    margin: {
      subject: "Summer is unvalidated, and the winter number is optimistic.",
      body: "The one fitted parameter was chosen on the same 53 stations the error is quoted from. Summer beats the null at no setting: its boundary layer is well mixed by mid-morning, so local sources disperse before they make a pattern.",
    },
  },
  {
    key: "equity",
    module: "cores/equity.py",
    title: "Equity core",
    body: "Measures person-degrees, not just pixels. Because a plan that cools an empty riverbank fails the city.",
    note: "Population is resampled by sum and never interpolated — on the real WorldPop raster, bilinear loses 26 % of this tile's residents, and every figure this core produces divides by that number.",
    figures: [
      { value: 66, decimals: 0, suffix: " %", label: "to three deciles, canal plan" },
      { value: 0, decimals: 1, suffix: " %", label: "to the most crowded decile" },
    ],
    Viz: EquityViz,
    margin: {
      subject: "Ordered by population density, not by deprivation.",
      body: "No free, keyless deprivation layer exists for Lahore, and substituting built-up density for one would be a claim the data cannot support. This answers a narrower question: does the cooling reach the crowded places?",
    },
  },
] as const;

export default function Simulators() {
  const track = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: track,
    offset: ["start start", "end end"],
  });
  const [active, setActive] = useState(0);

  useMotionValueEvent(scrollYProgress, "change", (progress) => {
    setActive(Math.min(PANES.length - 1, Math.max(0, Math.floor(progress * PANES.length))));
  });

  // The rail fills continuously rather than in three jumps, so the scroll has a readout.
  const railScale = useTransform(scrollYProgress, [0, 1], [0, 1]);

  // The ref goes on the sticky pane, not on the 320vh track. `useInView` asks for a
  // fraction of the *element* to be visible, and 40 % of three-and-a-bit screens can
  // never be on screen at once — so a note attached to the track would never once fire.
  const pane = useMarginNote<HTMLDivElement>(`simulator-${PANES[active]!.key}`, PANES[active]!.margin);

  return (
    <section>
      <div ref={track} className="relative h-[320vh]">
        <div ref={pane} className="sticky top-0 flex h-screen items-center overflow-hidden">
          <div className="mx-auto grid w-full max-w-[92rem] items-center gap-12 px-8 lg:grid-cols-2">
            <div className="relative">
              {/* A continuous progress rail beside the copy: three panes, one scroll. */}
              <div className="absolute top-0 -left-8 hidden h-full w-px bg-white/10 lg:block">
                <motion.div
                  style={{ scaleY: railScale }}
                  className="from-tide to-shoal h-full w-px origin-top bg-gradient-to-b"
                />
              </div>

              <div className="relative min-h-[26rem]">
                {PANES.map((pane, index) => (
                  <motion.div
                    key={pane.key}
                    animate={{
                      opacity: active === index ? 1 : 0,
                      y: active === index ? 0 : 20,
                      filter: active === index ? "blur(0px)" : "blur(4px)",
                    }}
                    transition={GLIDE}
                    className={active === index ? "relative" : "pointer-events-none absolute inset-0"}
                    aria-hidden={active !== index}
                  >
                    <Eyebrow>{pane.module}</Eyebrow>
                    <h2 className="mt-5 font-mono text-4xl tracking-tight text-white md:text-5xl">
                      {pane.title}
                    </h2>
                    <p className="mt-6 max-w-md text-lg leading-relaxed text-white/60">
                      {pane.body}
                    </p>
                    <p className="mt-4 max-w-md text-sm leading-relaxed text-white/35">
                      {pane.note}
                    </p>

                    <dl className="mt-9 flex gap-10">
                      {pane.figures.map((figure) => (
                        <div key={figure.label}>
                          <dd className="text-shoal font-mono text-3xl tracking-tight">
                            {active === index ? (
                              <Counter
                                to={figure.value}
                                decimals={figure.decimals}
                                suffix={figure.suffix}
                              />
                            ) : (
                              "—"
                            )}
                          </dd>
                          <dt className="mt-2 max-w-[9rem] text-[0.7rem] leading-snug text-white/35">
                            {figure.label}
                          </dt>
                        </div>
                      ))}
                    </dl>
                  </motion.div>
                ))}
              </div>
            </div>

            <div className="glass noise aspect-square w-full overflow-hidden">
              {PANES.map(({ key, Viz }, index) => (
                <div key={key} className={active === index ? "size-full" : "hidden"}>
                  {/* Each visualisation handles reduced motion itself, by jumping to its
                      end state rather than by not rendering. Gating it here left three
                      blank panes for anyone with the preference set. */}
                  <Viz active={active === index} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
