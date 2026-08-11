/**
 * Where the cooling landed, and what was there.
 *
 * The panel that answers the question the map raises and the brief cannot: *why did that
 * block cool and this one not?* The brief states one average; the map shows a pattern; and
 * the pattern is different every run, which is exactly why a template cannot describe it
 * and a model can.
 *
 * **The table is shown whether or not a model described it.** The rows are the answer and
 * the prose is a reading of it — `source: "table"` is a working deployment, not a missing
 * feature, and a reader who has the rows can check any sentence written about them.
 *
 * Spillover gets its own marker because it is the part of the map users most reliably
 * report as a bug: cooling outside the polygon they drew. It is the 500 m neighbourhood
 * terms doing real work, and saying so on the row is cheaper than explaining it twice.
 */

import type { RegionExplanation, SpatialExplanation } from "../api/client";

export interface PatternPanelProps {
  explanation: SpatialExplanation | null;
  busy: boolean;
  error: string | null;
  onExplain: () => void;
  /** Highlighted on the map while the row is hovered, so the two read as one thing. */
  onHighlight: (regionIds: string[]) => void;
}

function Row({
  region,
  onHighlight,
}: {
  region: RegionExplanation;
  onHighlight: (ids: string[]) => void;
}) {
  return (
    <li
      className="pattern__row"
      onMouseEnter={() => onHighlight([region.region_id])}
      onMouseLeave={() => onHighlight([])}
    >
      <span className="pattern__region">{region.region_id}</span>
      <span className="pattern__cooling">{region.expected_cooling_c.toFixed(2)} °C</span>
      <span className="pattern__tags">
        {region.spillover && <em className="pattern__tag is-spillover">spillover</em>}
        {region.water_fraction > 0.2 && (
          <em className="pattern__tag">{(region.water_fraction * 100).toFixed(0)}% water</em>
        )}
        {region.tree_cover_fraction > 0.3 && (
          <em className="pattern__tag">
            {(region.tree_cover_fraction * 100).toFixed(0)}% already wooded
          </em>
        )}
      </span>
      <span className="pattern__detail">
        {Math.round(region.residents).toLocaleString()} residents
        {region.population_decile !== null && ` · density decile ${region.population_decile}`}
        {` · ${(region.canopy_added * 100).toFixed(0)}% canopy added`}
        {` · ${region.headroom_km2.toFixed(2)} km² was plantable`}
      </span>
    </li>
  );
}

export default function PatternPanel({
  explanation,
  busy,
  error,
  onExplain,
  onHighlight,
}: PatternPanelProps) {
  return (
    <section className="control pattern" aria-labelledby="pattern-heading">
      <h2 id="pattern-heading">Where it landed</h2>

      {!explanation && (
        <p className="hint">
          The brief gives one average. This breaks the same result down by 2 km block and
          says what was in each — how much room there was to plant, who lives there, and
          which blocks cooled without being drawn on.
        </p>
      )}

      <div className="row">
        <button type="button" onClick={onExplain} disabled={busy}>
          {busy ? "Reading the map…" : explanation ? "Refresh" : "Explain the pattern"}
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}

      {explanation && explanation.regions.length === 0 && (
        <p className="hint">
          Nothing on this tile changed by a measurable amount, so there is no pattern to
          describe.
        </p>
      )}

      {explanation && explanation.regions.length > 0 && (
        <>
          {explanation.summary && (
            <div className="pattern__prose">
              <p>{explanation.summary}</p>
              {explanation.points.map((point) => (
                <p className="result__note" key={point}>
                  {point}
                </p>
              ))}
            </div>
          )}

          <ul className="pattern__rows" aria-label="Regions that changed">
            {explanation.regions.map((region) => (
              <Row key={region.region_id} region={region} onHighlight={onHighlight} />
            ))}
          </ul>

          <p className="result__caveat">
            Cooling is already divided by the model's known 2.5× over-prediction. Blocks are
            2 km squares of the analysis grid.{" "}
            {explanation.source === "table"
              ? "No model was reached, so this is the measured table with no prose over it."
              : `Described by ${explanation.source}, which may only reword these figures: a description carrying a number the table does not is rejected.`}
          </p>
        </>
      )}
    </section>
  );
}
