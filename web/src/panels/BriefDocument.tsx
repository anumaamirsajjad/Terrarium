/**
 * The council brief as a document — the thing somebody prints and takes to a meeting.
 *
 * **PDF by the browser's own print dialog**, not by a rendering service and not by
 * WeasyPrint. Every hosted PDF API meters pages, and adding a Python renderer would put a
 * dependency in the tree to reproduce a button every browser already has. `window.print()`
 * plus a print stylesheet costs nothing and works offline, which is the same argument that
 * chose OpenFreeMap over MapTiler.
 *
 * It is hidden on screen (`.doc` is `display: none` outside `@media print`) and unfolds
 * only on the printed page, so the sidebar keeps its shape and the document keeps its own.
 *
 * Every number here comes from the API. This component computes nothing except which
 * sections exist — a printed sheet outlives the session that produced it, so a figure
 * invented on the client would be the hardest kind of error to trace.
 */

import type { PlanResponse, SimulateResponse, TileInfo } from "../api/client";

export interface BriefDocumentProps {
  result: SimulateResponse;
  plan: PlanResponse | null;
  tile: TileInfo;
  /** Rendered into the footer so a printed sheet says when it was produced. */
  producedAt: Date;
}

function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export default function BriefDocument({ result, plan, tile, producedAt }: BriefDocumentProps) {
  const { brief, stats, context, equity, air } = result;

  return (
    <article className="doc" aria-label="Printable council brief">
      <header className="doc__header">
        <h1>{plan?.plan.name ?? "Modelled intervention"}</h1>
        <p className="doc__sub">
          {tile.name}, {tile.country} · {result.window} ({result.season}) ·{" "}
          {(stats.n_cells_changed * 0.01).toFixed(2)} km² · modelled at{" "}
          {tile.target_resolution_m} m
        </p>
      </header>

      <p className="doc__headline">{brief.headline}</p>

      <section className="doc__section">
        <h2>What the model says</h2>
        <table className="doc__table">
          <tbody>
            <tr>
              {/* Spelled out, matching `ResultPanel`'s caption - a table header abbreviated
                  to "ΔLST" is exactly the bare-jargon case D9 exists to rule out; the
                  qualifier belongs on the label a reader sees, not only in a caveat below. */}
              <th scope="row">Mean land-surface temperature change (inside)</th>
              <td>{signed(stats.mean_delta_inside)} °C</td>
            </tr>
            <tr>
              <th scope="row">Expected after hindcast correction</th>
              <td>{signed(brief.expected_cooling_c)} °C</td>
            </tr>
            <tr>
              <th scope="row">Ceiling for this window</th>
              <td>{context.tree_built_contrast_c.toFixed(2)} °C</td>
            </tr>
            <tr>
              <th scope="row">Spillover ({stats.spillover_cells.toLocaleString()} cells)</th>
              <td>{signed(stats.mean_delta_spillover, 3)} °C</td>
            </tr>
            <tr>
              <th scope="row">Strongest single cell</th>
              <td>{signed(stats.min_delta)} °C</td>
            </tr>
            {air && (
              <tr>
                <th scope="row">Locally-generated PM2.5 inside</th>
                <td>
                  {signed(air.stats.mean_delta_inside)} {air.units}
                </td>
              </tr>
            )}
            {plan && (
              <tr>
                <th scope="row">Trees</th>
                <td>
                  {plan.tree_count.toLocaleString()} of {plan.max_trees.toLocaleString()} the
                  area can hold
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {/* The plain-language version, printed alongside the technical one rather than
          instead of it. It is the half a resident reads. */}
      <section className="doc__section">
        <h2>In plain words</h2>
        <div>
          <p>{brief.plain.headline}</p>
          <ul>
            {brief.plain.points.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
        </div>
        {/* Always English: the server never sends the caveat to a model, in either
            direction, because it is the one sentence a rewrite has already damaged. */}
        <p className="doc__note">{brief.plain.caveat}</p>
      </section>

      <section className="doc__section">
        <h2>Findings</h2>
        <ul>
          {brief.findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
      </section>

      {equity && equity.shares_reliable && (
        <section className="doc__section">
          <h2>Who receives it</h2>
          <p>
            Population deciles, 1 = least densely populated. An even share is 10 % each;
            these are shares of person-degrees.
          </p>
          <table className="doc__table doc__table--deciles">
            <tbody>
              <tr>
                {equity.deciles.map((decile) => (
                  <th key={decile.decile} scope="col">
                    {decile.decile}
                  </th>
                ))}
              </tr>
              <tr>
                {equity.deciles.map((decile) => (
                  <td key={decile.decile}>{(decile.share * 100).toFixed(1)}%</td>
                ))}
              </tr>
            </tbody>
          </table>
          <p className="doc__note">
            Stratified by {equity.stratified_by}, not by deprivation: no free, keyless
            deprivation layer exists for this city.
          </p>
        </section>
      )}

      <section className="doc__section doc__section--caveats">
        <h2>What this does not prove</h2>
        <ul>
          {brief.uncertainties.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <p className="doc__note">
          Confidence: <strong>{brief.confidence}</strong>. Terrarium never reports high
          confidence: the thermal emulator is hindcast-corrected and the air core has never
          been calibrated against a monitoring station.
        </p>
      </section>

      <footer className="doc__footer">
        Terrarium · modelled {result.window} · produced {producedAt.toISOString().slice(0, 10)} ·
        every figure above is a model output, not a measurement of what happened.
      </footer>
    </article>
  );
}
