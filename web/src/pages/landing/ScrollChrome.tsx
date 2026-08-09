/**
 * Page chrome: a progress hairline and a coordinate readout.
 *
 * The readout maps scroll position onto the tile's own latitude span — the real bbox from
 * `config.py`, north edge at the top of the page to south edge at the bottom. It is a
 * scrollbar that speaks the instrument's units, which is the only reason it earns its
 * place; a percentage would have said the same thing in a language this page does not use.
 */

import { motion, useScroll, useSpring, useTransform } from "motion/react";

/** [west, south, east, north] — config.py's Lahore tile. */
const BBOX = [74.2533, 31.4305, 74.4641, 31.6103] as const;

export default function ScrollChrome() {
  const { scrollYProgress } = useScroll();
  // Damped, because a raw scroll value on a trackpad jitters the last decimal place
  // several times a second and reads as a fault rather than as precision.
  const smooth = useSpring(scrollYProgress, { stiffness: 120, damping: 30, restDelta: 0.0005 });

  const latitude = useTransform(smooth, (progress) =>
    (BBOX[3] - progress * (BBOX[3] - BBOX[1])).toFixed(4),
  );

  return (
    <>
      <motion.div
        style={{ scaleX: smooth }}
        className="from-tide via-shoal to-straw fixed inset-x-0 top-0 z-40 h-px origin-left
                   bg-gradient-to-r"
      />

      <div
        className="pointer-events-none fixed bottom-6 left-8 z-30 hidden font-mono text-[0.65rem]
                   tracking-[0.15em] text-white/25 lg:block"
      >
        <motion.span>{latitude}</motion.span>
        <span> N · 74.3587 E</span>
      </div>
    </>
  );
}
