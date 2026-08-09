/**
 * A tile of surface temperature, cooling as trees land on it.
 *
 * A continuous field rather than the chunky grid this used to be: the thermal core works
 * at 100 m and its output is a smooth raster, so a visualisation made of visible tiles
 * described the picture wrongly. Rendered through `ImageData` and the `HEAT` ramp from
 * `raster/ramp.ts`, which is the same ramp the map uses for `lst_c`.
 *
 * The cycle runs on its own — settle, plant, hold, release — so the pane is never a still
 * image waiting to be scrolled past.
 *
 * **The magnitude is bounded by the real ceiling.** `tree_built_contrast_c` on this tile
 * in summer is about 2.6 °C — the observed gap between built-up and tree-cover pixels,
 * and the most any planting here could buy. The readout is scaled inside that, because a
 * visitor meets the panel quoting it about thirty seconds later.
 */

import { useReducedMotion } from "motion/react";
import { useMemo, useRef } from "react";

import { HEAT } from "../../raster/ramp";
import { scratchCanvas, useCanvasLoop } from "./useCanvasLoop";

const RES = 132;
/** The tile's own observed built-minus-tree gap, °C. Nothing here may exceed it. */
const CEILING_C = 2.6;

const PLANT_AT = 1.1;
const PLANT_FOR = 4.6;
const HOLD_FOR = 3.4;
const RELEASE_FOR = 2.2;
const CYCLE = PLANT_AT + PLANT_FOR + HOLD_FOR + RELEASE_FOR;

function noise(x: number, y: number, salt: number): number {
  const s = Math.sin(x * 127.1 + y * 311.7 + salt * 74.7) * 43758.5453;
  return s - Math.floor(s);
}

/** Smooth value noise — bilinear between lattice points, which is enough at this scale. */
function smooth(x: number, y: number, salt: number): number {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const fx = x - ix;
  const fy = y - iy;
  const ux = fx * fx * (3 - 2 * fx);
  const uy = fy * fy * (3 - 2 * fy);
  const a = noise(ix, iy, salt);
  const b = noise(ix + 1, iy, salt);
  const c = noise(ix, iy + 1, salt);
  const d = noise(ix + 1, iy + 1, salt);
  return a * (1 - ux) * (1 - uy) + b * ux * (1 - uy) + c * (1 - ux) * uy + d * ux * uy;
}

interface Tree {
  x: number;
  y: number;
  /** Fraction of the planting window at which this one goes in. */
  at: number;
  radius: number;
  strength: number;
}

export default function ThermalViz({ active }: { active: boolean }) {
  const reduced = useReducedMotion();

  const { base, trees } = useMemo(() => {
    const field = new Float32Array(RES * RES);
    for (let y = 0; y < RES; y++) {
      for (let x = 0; x < RES; x++) {
        // Two octaves plus a warm core: a dense centre cooling toward the edges, which is
        // the shape every mid-morning surface-temperature tile of a city has.
        const broad = smooth(x / 34, y / 34, 1);
        const fine = smooth(x / 11, y / 11, 2);
        const core = 1 - Math.hypot(x / RES - 0.5, y / RES - 0.5) * 1.55;
        field[y * RES + x] = Math.max(
          0.04,
          Math.min(0.97, core * 0.62 + broad * 0.3 + fine * 0.16),
        );
      }
    }

    // A planting corridor across the tile, the way a canal-side scheme actually lands.
    const list: Tree[] = [];
    for (let i = 0; i < 74; i++) {
      const along = i / 73;
      const wobble = (smooth(i / 3.5, 0, 7) - 0.5) * 26;
      list.push({
        x: along * RES,
        y: RES * 0.78 - along * RES * 0.56 + wobble,
        at: along * 0.72 + noise(i, 3, 9) * 0.28,
        radius: 9 + noise(i, 1, 4) * 7,
        strength: 0.5 + noise(i, 2, 5) * 0.5,
      });
    }

    return { base: field, trees: list };
  }, []);

  const cooling = useRef(new Float32Array(RES * RES));
  const planted = useRef(new Set<number>());
  const image = useRef<ImageData | null>(null);
  const scratch = useRef<CanvasRenderingContext2D | null>(null);
  const lastPhase = useRef(0);

  const ref = useCanvasLoop(
    (ctx, { width, height }, elapsed) => {
      const time = reduced ? PLANT_AT + PLANT_FOR : elapsed % CYCLE;

      // How far the planting has run, 0 to 1.
      let progress: number;
      if (time < PLANT_AT) progress = 0;
      else if (time < PLANT_AT + PLANT_FOR) progress = (time - PLANT_AT) / PLANT_FOR;
      else if (time < PLANT_AT + PLANT_FOR + HOLD_FOR) progress = 1;
      else progress = 1 - (time - PLANT_AT - PLANT_FOR - HOLD_FOR) / RELEASE_FOR;

      // The cycle restarted: clear what was planted and start again.
      if (time < lastPhase.current) {
        cooling.current.fill(0);
        planted.current.clear();
      }
      lastPhase.current = time;

      // Each tree splats its own cooling into the accumulation buffer once, over a small
      // window. Recomputing every tree against every pixel each frame would be a million
      // operations for a picture that only changes where a tree just appeared.
      for (let i = 0; i < trees.length; i++) {
        const tree = trees[i]!;
        if (planted.current.has(i) || progress < tree.at) continue;
        planted.current.add(i);

        const r = Math.ceil(tree.radius);
        for (let dy = -r; dy <= r; dy++) {
          for (let dx = -r; dx <= r; dx++) {
            const px = Math.round(tree.x) + dx;
            const py = Math.round(tree.y) + dy;
            if (px < 0 || py < 0 || px >= RES || py >= RES) continue;
            const distance = Math.hypot(dx, dy);
            if (distance > tree.radius) continue;
            const falloff = Math.cos((distance / tree.radius) * Math.PI * 0.5);
            const index = py * RES + px;
            cooling.current[index] = Math.min(
              1,
              cooling.current[index]! + falloff * falloff * tree.strength * 0.5,
            );
          }
        }
      }

      if (!scratch.current) scratch.current = scratchCanvas(RES, RES);
      if (!image.current) image.current = scratch.current.createImageData(RES, RES);
      const pixels = image.current.data;

      // Released: the cooling fades back out rather than vanishing between frames.
      const applied = progress < 1 ? Math.min(progress > 0 ? 1 : 0, progress * 4) : 1;
      const fade = time > PLANT_AT + PLANT_FOR + HOLD_FOR ? progress : applied;

      let coolestC = 0;
      for (let i = 0; i < RES * RES; i++) {
        const drop = cooling.current[i]! * fade;
        if (drop > coolestC) coolestC = drop;
        const [r, g, b] = HEAT.at(base[i]! - drop * 0.34);
        pixels[i * 4] = r;
        pixels[i * 4 + 1] = g;
        pixels[i * 4 + 2] = b;
        pixels[i * 4 + 3] = 255;
      }

      // Staged offscreen, then blitted. putImageData writes in device pixels and ignores
      // the DPR transform, so writing straight to the visible canvas lands the field in
      // the corner at the wrong scale.
      scratch.current.putImageData(image.current, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(scratch.current.canvas, 0, 0, width, height);

      // The trees themselves, over the field.
      const scale = width / RES;
      for (let i = 0; i < trees.length; i++) {
        if (!planted.current.has(i)) continue;
        const tree = trees[i]!;
        const size = (2.4 + tree.strength * 1.8) * scale * 0.42 * fade;
        if (size <= 0.2) continue;
        ctx.fillStyle = `rgba(46, 122, 68, ${0.85 * fade})`;
        ctx.beginPath();
        ctx.arc(tree.x * scale, tree.y * scale, size, 0, Math.PI * 2);
        ctx.fill();
      }

      // The readout, inside the tile's real ceiling.
      const delta = -(coolestC * 0.34 * CEILING_C);
      ctx.font = "500 11px ui-monospace, monospace";
      ctx.fillStyle = "rgba(255,255,255,0.75)";
      ctx.fillText(`${delta.toFixed(2)} °C strongest cell`, 14, height - 34);
      ctx.fillStyle = "rgba(255,255,255,0.35)";
      ctx.fillText(`ceiling ${CEILING_C.toFixed(2)} °C · mid-morning LST`, 14, height - 16);
    },
    active && !reduced,
  );

  return <canvas ref={ref} className="size-full" aria-hidden />;
}
