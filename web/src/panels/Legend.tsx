/**
 * Colour ramp legend.
 *
 * The diverging case prints its zero tick explicitly. On a ΔLST map most of the tile is
 * *exactly* zero and therefore invisible, and a legend that did not say so would leave
 * "why is most of the city not coloured?" as an open question — when the answer is the
 * strongest correctness evidence the model has.
 */

import { withUnits } from "../units";
import type { Ramp } from "../raster/ramp";

export interface LegendProps {
  ramp: Ramp;
  domain: [number, number];
  units: string;
  diverging?: boolean;
}

function gradient(ramp: Ramp): string {
  const stops = ramp.stops.map((stop, index) => {
    const position = (index / (ramp.stops.length - 1)) * 100;
    return `rgba(${stop[0]}, ${stop[1]}, ${stop[2]}, ${stop[3] / 255}) ${position}%`;
  });
  return `linear-gradient(to right, ${stops.join(", ")})`;
}

function format(value: number): string {
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

export default function Legend({ ramp, domain, units, diverging = false }: LegendProps) {
  const [low, high] = domain;

  return (
    <div className="legend">
      <div className="legend__bar" style={{ background: gradient(ramp) }} />
      <div className="legend__ticks">
        <span>{withUnits(format(low), units)}</span>
        {diverging && <span className="legend__zero">0 — no change</span>}
        <span>{withUnits(format(high), units)}</span>
      </div>
    </div>
  );
}
