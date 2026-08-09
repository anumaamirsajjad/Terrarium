/**
 * Where the cooling actually goes.
 *
 * The distribution is **measured**, not illustrative: the canal-side planting on
 * `cube_phase4.zarr`, 2024-summer, the same figures the equity panel's own test fixture
 * asserts against. 66 % of the benefit lands in three deciles, and the most crowded decile
 * — a tenth of the city's people, the ones most exposed to heat — receives 0.0 %.
 *
 * Two layers, and the split is deliberate. The stream on top is the *process*: person-
 * degrees leaving the plan and arriving somewhere, with the tenth chute receiving nothing
 * for as long as anyone watches. The bars below are the *result*. A bar chart alone states
 * the finding; watching decile ten stay empty is what makes it land.
 *
 * Deciles hold a tenth of the population each, not a tenth of the map, so an even split is
 * 10 % everywhere and any skew shows without arithmetic.
 */

import { motion, useReducedMotion } from "motion/react";
import { useMemo, useRef } from "react";

import { SETTLE } from "../../motion/springs";
import { useCanvasLoop } from "./useCanvasLoop";

/** Measured shares of total person-degrees. Decile 1 = least dense, 10 = most dense. */
const SHARES = [0.003, 0.016, 0.077, 0.127, 0.235, 0.253, 0.17, 0.103, 0.016, 0.0];
const EVEN_SHARE = 0.1;
const WIDEST = Math.max(...SHARES);

/** Well under an even share, for a decile that holds a tenth of the people. */
const STARVED = 0.02;

/** Cumulative shares, so a uniform draw picks a decile in proportion to what it receives. */
const CUMULATIVE = SHARES.reduce<number[]>((running, share) => {
  running.push((running.at(-1) ?? 0) + share);
  return running;
}, []);

interface Mote {
  t: number;
  speed: number;
  lane: number;
  wobble: number;
}

function pickLane(): number {
  const draw = Math.random() * (CUMULATIVE.at(-1) ?? 1);
  for (let i = 0; i < CUMULATIVE.length; i++) if (draw <= CUMULATIVE[i]!) return i;
  return CUMULATIVE.length - 1;
}

function Stream({ active }: { active: boolean }) {
  const reduced = useReducedMotion();
  const motes = useRef<Mote[]>(
    Array.from({ length: 150 }, (_, i) => ({
      t: i / 150,
      speed: 0.0032 + Math.random() * 0.0034,
      lane: pickLane(),
      wobble: Math.random() * Math.PI * 2,
    })),
  );

  const ref = useCanvasLoop(
    (ctx, { width, height }, elapsed) => {
      ctx.clearRect(0, 0, width, height);

      const sourceX = width * 0.06;
      const sourceY = height * 0.5;
      const chuteY = height - 18;
      const gutter = 26;
      const span = width - gutter - 12;

      // The ten chutes, drawn whether or not anything arrives in them. An empty chute has
      // to be visibly a chute, or decile ten reads as missing rather than as unserved.
      for (let lane = 0; lane < 10; lane++) {
        const x = gutter + (span * (lane + 0.5)) / 10;
        const starved = SHARES[lane]! < STARVED && lane >= 7;
        ctx.strokeStyle = starved ? "rgba(248,113,113,0.45)" : "rgba(255,255,255,0.13)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, chuteY - 12);
        ctx.lineTo(x, chuteY);
        ctx.stroke();

        // Numbered, because the chutes run across and the bars below run down — without
        // the label there is nothing saying the two charts share a decile.
        ctx.font = "500 8px ui-monospace, monospace";
        ctx.fillStyle = starved ? "rgba(248,113,113,0.7)" : "rgba(255,255,255,0.3)";
        ctx.textAlign = "center";
        ctx.fillText(String(lane + 1), x, chuteY + 9);
        ctx.textAlign = "left";
      }

      ctx.fillStyle = "rgba(255,255,255,0.85)";
      ctx.beginPath();
      ctx.arc(sourceX, sourceY, 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.font = "500 9px ui-monospace, monospace";
      ctx.fillStyle = "rgba(255,255,255,0.4)";
      ctx.fillText("plan", sourceX - 10, sourceY - 12);

      for (const mote of motes.current) {
        mote.t += mote.speed;
        if (mote.t > 1) {
          mote.t = 0;
          mote.lane = pickLane();
          mote.wobble = Math.random() * Math.PI * 2;
        }

        const targetX = gutter + (span * (mote.lane + 0.5)) / 10;
        const eased = mote.t * mote.t * (3 - 2 * mote.t);
        const x = sourceX + (targetX - sourceX) * eased;
        // Arc up and over, so a hundred and fifty motes are not one straight line.
        const lift = Math.sin(mote.t * Math.PI) * (height * 0.3);
        const y = sourceY + (chuteY - sourceY) * eased - lift + Math.sin(mote.wobble + elapsed * 2) * 2;

        const arriving = Math.min(1, mote.t * 3);
        ctx.fillStyle = `rgba(110,190,175,${0.2 + arriving * 0.55})`;
        ctx.beginPath();
        ctx.arc(x, y, 1.5, 0, Math.PI * 2);
        ctx.fill();
      }
    },
    active && !reduced,
  );

  return <canvas ref={ref} className="h-full w-full" aria-hidden />;
}

export default function EquityViz({ active }: { active: boolean }) {
  const evenLeft = useMemo(() => `calc(2rem + (100% - 2rem) * ${EVEN_SHARE / WIDEST})`, []);

  return (
    <div className="flex size-full flex-col p-7">
      <div className="mb-1 flex items-baseline justify-between font-mono text-[0.62rem] text-white/35">
        <span>PERSON-DEGREES DELIVERED</span>
        <span>EVEN SPLIT = 10%</span>
      </div>

      <div className="h-[34%] min-h-[5rem]">
        <Stream active={active} />
      </div>

      <div className="relative mt-1 flex flex-1 flex-col justify-center gap-1">
        <div className="absolute inset-y-0 z-10 w-px bg-white/25" style={{ left: evenLeft }} />

        {SHARES.map((share, index) => {
          const decile = index + 1;
          // The most crowded deciles receiving least is the finding. Glow those, rather
          // than every short bar — decile 1 being small is expected, decile 10 is not.
          const starved = share < STARVED && decile >= 8;
          return (
            <div key={decile} className="flex items-center gap-2">
              <span className="w-8 text-right font-mono text-[0.6rem] text-white/30">
                {decile}
              </span>
              <div className="h-2.5 flex-1">
                <motion.div
                  initial={{ scaleX: 0 }}
                  animate={{ scaleX: active ? share / WIDEST : 0 }}
                  transition={{ ...SETTLE, delay: active ? index * 0.045 : 0 }}
                  style={{ transformOrigin: "left" }}
                  className={`h-full rounded-[2px] ${
                    starved ? "bg-red-500" : "from-tide to-shoal bg-gradient-to-r"
                  }`}
                />
              </div>
              <span
                className={`w-9 font-mono text-[0.6rem] ${
                  starved ? "text-red-400" : "text-white/40"
                }`}
              >
                {(share * 100).toFixed(1)}
              </span>
            </div>
          );
        })}
      </div>

      <motion.p
        animate={active ? { opacity: [1, 0.5, 1] } : { opacity: 0.5 }}
        transition={active ? { duration: 2.4, repeat: Infinity } : undefined}
        className="mt-4 text-[0.72rem] leading-relaxed text-red-400/90"
      >
        The most crowded decile receives <span className="font-mono">0.0%</span> of this
        plan&rsquo;s cooling.
      </motion.p>
      <p className="mt-1.5 text-[0.66rem] leading-relaxed text-white/35">
        Canal-side planting, 2024-summer, measured. Ordered by population density — not by
        deprivation, because no free, keyless deprivation layer exists.
      </p>
    </div>
  );
}
