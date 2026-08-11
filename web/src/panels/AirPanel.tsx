/**
 * What the air core returned.
 *
 * Mirrors `ResultPanel`. Named "Locally-generated PM2.5", never "air quality" — the
 * inventory covers this tile's own roads and nothing else, so this is a delta and never a
 * level, and not what a monitor reads. The uncalibrated/unvalidated caveats that used to
 * sit below the stats (here and in `dsl/explain.py`'s technical brief) were removed by
 * request; the underlying facts (literature emission factors, no summer validation) are
 * still true and still drive `confidence`, they are simply no longer spelled out in prose.
 *
 * The season is shown because it is worth a factor of 6–7, not as a detail: the winter
 * inversion drops the mixing height ~3x and slows lateral spread, so identical emissions
 * produce several times the concentration. That is why the mixing height sits next to the
 * result rather than in a tooltip.
 */

import type { Air } from "../api/client";
import { compassPoint } from "./air";

export interface AirPanelProps {
  air: Air;
  /** The window the simulation ran in — the air answer is only readable with it. */
  window: string;
  season: string;
}

function signed(value: number, digits = 3): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export default function AirPanel({ air, window, season }: AirPanelProps) {
  const { stats } = air;
  const areaKm2 = (stats.n_cells_changed * 0.01).toFixed(2);
  const winter = season === "winter";

  return (
    <section className="control">
      <h2>Locally-generated PM2.5</h2>

      <div className="result">
        <div className="result__headline">
          <span className="result__value">
            {signed(stats.mean_delta_inside, 2)} {air.units}
          </span>
          <span className="result__caption">
            mean change in this tile&rsquo;s own PM2.5, inside the polygon
          </span>
        </div>

        <p className="result__ceiling">
          from removing <strong>{(air.emission_fraction_removed * 100).toFixed(0)}%</strong>{" "}
          of vehicle emissions there. Removed, not electrified — brake, tyre and road wear
          are roughly half of road PM2.5 and go away only when the traffic does.
        </p>

        <dl className="result__stats">
          <div>
            <dt>Window</dt>
            <dd>
              {window} <span className="muted">({season})</span>
            </dd>
          </div>
          <div>
            <dt>Area restricted</dt>
            <dd>
              {stats.n_cells_changed.toLocaleString()} cells · {areaKm2} km²
            </dd>
          </div>
          <div>
            <dt>Downwind of it</dt>
            <dd>
              {signed(stats.mean_delta_spillover)} {air.units}
              <span className="muted">
                {" "}
                over {stats.spillover_cells.toLocaleString()} cells
              </span>
            </dd>
          </div>
          <div>
            <dt>Strongest reduction</dt>
            <dd>
              {signed(stats.min_delta)} {air.units}
            </dd>
          </div>
          <div>
            <dt>Mixing height</dt>
            <dd>
              {air.mixing_height_m.toFixed(0)} m
              <span className="muted"> {winter ? "· inversion" : "· well mixed"}</span>
            </dd>
          </div>
          <div>
            <dt>Wind</dt>
            <dd>
              {air.wind_speed_ms.toFixed(1)} m/s from{" "}
              {compassPoint(air.wind_direction_deg)}
              <span className="muted"> ({air.wind_direction_deg.toFixed(0)}°)</span>
            </dd>
          </div>
        </dl>

        <p className="result__note">
          Identical emissions produce <strong>6–9&times;</strong> the concentration in
          winter than in summer.
        </p>

      </div>
    </section>
  );
}
