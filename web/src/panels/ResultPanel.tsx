/**
 * What the simulation returned.
 *
 * Two rules govern this panel, both inherited from the API's contract:
 *
 * 1. **The window is shown, always.** The same planting cools ~0.51 °C in summer and
 *    ~0.13 °C in winter. A number without its season is not quotable.
 * 2. **The ceiling is shown next to the number.** `tree_built_contrast_c` is this
 *    window's own observed tree-vs-built gap — the most any planting could buy here.
 *    "−0.51 °C against a 2.60 °C ceiling" is honest; "−0.51 °C" alone invites the reader
 *    to compare it against the literature's 3–6 °C and conclude the model is broken,
 *    when the difference is the tile's, not the model's.
 */

import type { SimulateResponse } from "../api/client";

export interface ResultPanelProps {
  result: SimulateResponse;
}

function signed(value: number, digits = 3): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export default function ResultPanel({ result }: ResultPanelProps) {
  const { stats, context } = result;
  const areaKm2 = (stats.n_cells_changed * 0.01).toFixed(2);

  return (
    <div className="result">
      <div className="result__headline">
        <span className="result__value">{signed(stats.mean_delta_inside, 2)} °C</span>
        <span className="result__caption">
          mean change in mid-morning land surface temperature, inside the polygon
        </span>
      </div>

      <p className="result__ceiling">
        against a <strong>{context.tree_built_contrast_c.toFixed(2)} °C</strong> ceiling:
        this window&rsquo;s own observed gap between built-up and tree-cover pixels. No
        planting on this tile can beat that.
      </p>

      <dl className="result__stats">
        <div>
          <dt>Window</dt>
          <dd>
            {result.window} <span className="muted">({result.season})</span>
          </dd>
        </div>
        <div>
          <dt>Area planted</dt>
          <dd>
            {stats.n_cells_changed.toLocaleString()} cells · {areaKm2} km²
          </dd>
        </div>
        <div>
          <dt>Canopy actually added</dt>
          <dd>
            {(context.mean_canopy_added * 100).toFixed(1)}%
            <span className="muted"> after capping per cell</span>
          </dd>
        </div>
        <div>
          <dt>Spillover</dt>
          <dd>
            {signed(stats.mean_delta_spillover)} °C
            <span className="muted"> over {stats.spillover_cells.toLocaleString()} cells</span>
          </dd>
        </div>
        <div>
          <dt>Strongest cooling</dt>
          <dd>{signed(stats.min_delta)} °C</dd>
        </div>
        <div>
          <dt>vs linear expectation</dt>
          <dd>
            {signed(context.linear_expectation_c)} °C
            {context.ratio_to_linear !== null ? (
              <span className="muted"> · ratio {context.ratio_to_linear.toFixed(2)}</span>
            ) : (
              <span className="muted"> · ratio not shown, contrast too small</span>
            )}
          </dd>
        </div>
      </dl>

      {context.ratio_to_linear === null && (
        <p className="result__note">
          In winter the tree-vs-built contrast is a few tenths of a degree, so a ratio
          against it divides a small number by a smaller one and moves on noise. The API
          withholds it rather than showing a confident-looking figure.
        </p>
      )}

      <p className="result__caveat">
        Runs hot by about <strong>2.5&times;</strong> against measured plantings.
      </p>
    </div>
  );
}
