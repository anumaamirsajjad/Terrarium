/**
 * The same road, under two boundary layers, at the same time.
 *
 * The previous version alternated between the seasons, which put the comparison in the
 * viewer's memory rather than on the screen: by the time winter had loaded up, summer was
 * eight seconds gone and there was nothing left to compare it to. Both slices now run
 * together, side by side, from an identical source — so the factor is something you read
 * off the picture instead of something the caption asserts.
 *
 * Each slice is a vertical cut through the tile: the road at the left edge, 20 km of
 * downwind air to the right, a kilometre of sky above. The shading is the Gaussian plume in
 * `plume.ts` — the model `cores/air.py` runs — evaluated once per season and normalised
 * against the *stronger* of the two, so one exposure serves both panels. Normalising each
 * panel to its own peak would make them look equally dirty, which is exactly the thing this
 * exists to disprove.
 *
 * The motes are decoration, but they carry the flow, and they obey the same lid: each one
 * rides a fixed deviate through a spread that widens downwind and mirrors off the
 * inversion. Winter holds more of them on screen at once because its air is slower — that
 * is residence time, and it is why the emission rate being identical is worth stating.
 *
 * **7.0x is derived, not typed.** It falls out of 800 m and 2.6 m/s against 250 m and
 * 1.1 m/s, and `plume.test.ts` fails if a constant is nudged for looks and the derived
 * factor leaves the 6.3x-8.9x band the core measured across 2023, 2024 and 2025.
 *
 * The colour is violet and deliberately not the map's diverging ramp: the map ships a
 * delta, this is a field of levels, and one palette across both would say they were the
 * same kind of quantity.
 */

import { useReducedMotion } from "motion/react";
import { useMemo, useRef } from "react";

import {
  DOMAIN_X_M,
  DOMAIN_Z_M,
  groundLevel,
  reflect,
  SEASONS,
  type Season,
  sigmaZ,
  slice,
  SUMMER,
} from "./plume";
import { Counter } from "./primitives";
import { scratchCanvas, useCanvasLoop } from "./useCanvasLoop";

const FIELD_W = 200;
const FIELD_H = 90;

/**
 * Seconds of animation per metre-per-second of real wind. A time compression, identical for
 * both seasons — which is what leaves the residence-time difference between them intact.
 */
const TIME_SCALE = 1_300;

/** Motes on screen in the fastest season. The slower one gets proportionally more. */
const MOTES = 130;

interface Mote {
  /** Metres downwind. */
  x: number;
  /** Standard deviates from the plume centreline. Fixed per mote: it rides one streamline. */
  n: number;
}

/** Box-Muller, so the motes sit where the Gaussian says they should. */
function deviate(): number {
  return (
    Math.sqrt(-2 * Math.log(1 - Math.random())) * Math.cos(2 * Math.PI * Math.random())
  );
}

function Slice({ season, active }: { season: Season; active: boolean }) {
  const reduced = useReducedMotion();

  const motes = useMemo<Mote[]>(() => {
    // The emission rate is the same in both slices; the count differs because slower air
    // holds what it was given for longer. count * wind / distance is constant, by
    // construction — that is the one line making "identical emissions" true rather than
    // asserted.
    const count = Math.round((MOTES * SUMMER.windMs) / season.windMs);
    return Array.from({ length: count }, () => ({
      x: Math.random() * DOMAIN_X_M,
      n: deviate(),
    }));
  }, [season]);

  const plume = useRef<HTMLCanvasElement | null>(null);
  const last = useRef(0);

  const ref = useCanvasLoop(
    (ctx, { width, height }, elapsed) => {
      // Built on the first frame and never again: the field is static per season, and only
      // the motes move. It is also why a paused pane — inactive, or reduced motion — still
      // shows the whole plume rather than an empty box waiting for a loop that never runs.
      if (!plume.current) {
        const staged = scratchCanvas(FIELD_W, FIELD_H);
        const image = staged.createImageData(FIELD_W, FIELD_H);
        const field = slice(season, FIELD_W, FIELD_H);
        for (let i = 0; i < field.length; i++) {
          // Gamma, so the long thin tail downwind is visible at all. Linear, the far field
          // is two dozen levels off black and the plume looks like it stops at 4 km.
          const value = Math.pow(field[i]!, 0.5);
          image.data[i * 4] = 92 + value * 128;
          image.data[i * 4 + 1] = 44 + value * 92;
          image.data[i * 4 + 2] = 138 + value * 117;
          image.data[i * 4 + 3] = value * 242;
        }
        staged.putImageData(image, 0, 0);
        plume.current = staged.canvas;
      }

      // `elapsed` restarts near 0 every time this pane goes from inactive to active —
      // `useCanvasLoop` remounts its rAF loop on that transition. Without this guard
      // `last.current` still held the pre-pause value, so `elapsed - last.current` went
      // deeply negative, clamped to 0, and every mote froze until `elapsed` caught back up
      // to wherever it had been — tens of seconds of a motionless plume.
      const dt = elapsed < last.current ? 0 : Math.min(0.05, elapsed - last.current);
      last.current = elapsed;

      ctx.clearRect(0, 0, width, height);
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(plume.current, 0, 0, width, height);

      const toX = (xM: number) => (xM / DOMAIN_X_M) * width;
      const toY = (zM: number) => height * (1 - zM / DOMAIN_Z_M);

      // The lid. Solid and bright in winter, faint in summer, because in summer it is a
      // ceiling the plume never reaches inside this tile.
      ctx.strokeStyle =
        season.key === "winter" ? "rgba(216,180,254,0.85)" : "rgba(216,180,254,0.3)";
      ctx.lineWidth = 1;
      ctx.setLineDash(season.key === "winter" ? [] : [4, 6]);
      ctx.beginPath();
      ctx.moveTo(0, toY(season.lidM));
      ctx.lineTo(width, toY(season.lidM));
      ctx.stroke();
      ctx.setLineDash([]);

      for (const mote of motes) {
        mote.x += season.windMs * TIME_SCALE * dt;
        if (mote.x > DOMAIN_X_M) {
          mote.x -= DOMAIN_X_M;
          mote.n = deviate();
        }

        const zM = reflect(mote.n * sigmaZ(mote.x, season), season.lidM);
        // Faded in at the kerb and out at the edge of the tile, so neither end of the
        // slice is a line of dots appearing out of nothing.
        const edge = Math.min(1, mote.x / 900, (DOMAIN_X_M - mote.x) / 2_200);
        ctx.fillStyle = `rgba(233,213,255,${0.5 * edge})`;
        ctx.beginPath();
        ctx.arc(toX(mote.x), toY(zM), 1.1, 0, Math.PI * 2);
        ctx.fill();
      }

      // The source: one road, pulsing at the same rate in both slices.
      const pulse = 0.55 + Math.sin(elapsed * 2.4) * 0.2;
      ctx.strokeStyle = `rgba(255,255,255,${pulse})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(1, height);
      ctx.lineTo(1, height - 16);
      ctx.stroke();
    },
    active && !reduced,
  );

  const factor = groundLevel(season) / groundLevel(SUMMER);

  return (
    <div className="flex flex-1 flex-col">
      {/* Every label sits outside the canvas. In the canvas they collided with the picture
          — summer's lid runs across the top of its own slice, winter's plume fills the
          bottom of its — and canvas type at this size is soft besides. */}
      <div className="flex items-baseline justify-between gap-3 pb-1.5">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[0.7rem] tracking-[0.18em] text-white/85 uppercase">
            {season.name}
          </span>
          <span className="text-[0.62rem] text-white/35">{season.note}</span>
        </div>
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-[0.58rem] whitespace-nowrap text-white/30">
            {season.windMs} m/s · lid {season.lidM} m
          </span>
          <span className="font-mono text-xl leading-none text-violet-200 tabular-nums">
            {active ? <Counter to={factor} decimals={1} suffix="×" /> : "—"}
          </span>
        </div>
      </div>

      <div className="relative flex-1 overflow-hidden rounded-md border border-white/10">
        <canvas ref={ref} className="absolute inset-0 size-full" aria-hidden />
        <span
          className="absolute left-2 font-mono text-[0.55rem] text-violet-200/60"
          style={{ bottom: `calc(${(season.lidM / DOMAIN_Z_M) * 100}% + 2px)` }}
        >
          {season.lidM} m
        </span>
      </div>
    </div>
  );
}

export default function PlumeViz({ active }: { active: boolean }) {
  return (
    <div className="flex size-full flex-col gap-3 p-6">
      <div className="flex items-baseline justify-between font-mono text-[0.62rem] text-white/35">
        <span>LOCALLY-GENERATED PM2.5</span>
        <span>× VS SUMMER, AT THE TILE&rsquo;S EDGE</span>
      </div>

      {SEASONS.map((season) => (
        <Slice key={season.key} season={season} active={active} />
      ))}

      <div className="flex items-baseline justify-between font-mono text-[0.58rem] text-white/30">
        <span>road</span>
        <span>20 km downwind →</span>
      </div>

      <p className="mt-1 text-[0.68rem] leading-relaxed text-white/45">
        The same road, the same emission rate. Winter holds it in a layer a third as deep
        under half the wind, so the identical source reads{" "}
        <span className="text-violet-200">6.3–8.9×</span> higher — measured across 2023,
        2024 and 2025.
      </p>
      <p className="text-[0.62rem] leading-relaxed text-white/30">
        A local increment, not what a monitor reads: the regional background that dominates
        Lahore&rsquo;s PM2.5 is absent by construction, and these magnitudes are
        uncalibrated.
      </p>
    </div>
  );
}
