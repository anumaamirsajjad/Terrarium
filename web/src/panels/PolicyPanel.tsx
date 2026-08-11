/**
 * What a published policy would deliver on this tile (Phase D).
 *
 * `scripts/extract_policy.py` is a build step, not a route: it needs the Gemini key and has
 * no offline fallback, so it runs once, offline, and writes what it found to DuckDB. This
 * panel is the read side — `GET /policy/measures` needs no key at all, because reading back
 * an extraction someone already paid for costs nothing.
 *
 * **Most measures carry no lever, and that is shown, not hidden.** A canopy fraction and an
 * emission fraction cannot express a fuel sulfur limit or a catalytic-converter mandate, and
 * quietly listing only the ones that fit would describe a document this tool understands far
 * better than it does — the same reporting choice `policy.to_plan` makes.
 */

import type { MappedPlan, PolicyMeasureItem } from "../api/client";

export interface PolicyPanelProps {
  measures: PolicyMeasureItem[];
  loading: boolean;
  error: string | null;
  onApply: (mapped: MappedPlan) => void;
  /** True once a polygon is drawn. A measure needs one to be checked against, same as a
   * preset does — applying with none drawn would otherwise do nothing and say nothing. */
  hasPolygon: boolean;
}

const SECTOR_LABEL: Record<string, string> = {
  transport: "transport",
  urban_greening: "urban greening",
  industry: "industry",
  waste: "waste",
  agriculture: "agriculture",
  other: "other",
};

function Lever({ mapped }: { mapped: MappedPlan }) {
  const action = mapped.plan.actions[0];
  if (!action) return null;
  const label =
    action.kind === "restrict_vehicles"
      ? `emissions -${Math.round(action.emission_fraction_removed * 100)}%`
      : `canopy +${Math.round((action.canopy_fraction_added ?? 0) * 100)}%`;
  return (
    <span className="policy__lever">
      {label}
      {mapped.assumed && <em className="policy__tag">assumed</em>}
    </span>
  );
}

function Row({
  item,
  onApply,
  hasPolygon,
}: {
  item: PolicyMeasureItem;
  onApply: (mapped: MappedPlan) => void;
  hasPolygon: boolean;
}) {
  const { measure, mapped } = item;
  return (
    <li className="policy__row">
      <div className="policy__row-head">
        <em className="policy__tag policy__sector">
          {SECTOR_LABEL[measure.sector] ?? measure.sector}
        </em>
        <span className="policy__title">{measure.title}</span>
        {mapped ? <Lever mapped={mapped} /> : <span className="muted">no lever for this</span>}
      </div>

      <blockquote className="policy__quote">"{measure.quote}"</blockquote>

      <div className="policy__meta">
        {measure.document}
        {measure.source_page && `, p. ${measure.source_page}`}
        {measure.target && ` · target: ${measure.target}`}
        {measure.target_year && ` by ${measure.target_year}`}
      </div>

      {mapped && (
        <>
          <p className="policy__basis">{mapped.basis}</p>
          <div className="row">
            <button
              type="button"
              className="agent__apply"
              disabled={!hasPolygon}
              title={hasPolygon ? undefined : "Draw a polygon on the map first"}
              onClick={() => onApply(mapped)}
            >
              Apply this measure
            </button>
          </div>
        </>
      )}
    </li>
  );
}

export default function PolicyPanel({
  measures,
  loading,
  error,
  onApply,
  hasPolygon,
}: PolicyPanelProps) {
  const expressible = measures.filter((item) => item.mapped !== null).length;

  return (
    <section className="control policy" aria-labelledby="policy-heading">
      <h2 id="policy-heading">Published policy</h2>

      {loading && <p className="hint">Loading…</p>}
      {error && <p className="error-text">{error}</p>}

      {!loading && !error && measures.length === 0 && (
        <p className="hint">
          Nothing extracted yet. This is a maintainer build step —
          <code> scripts/ingest_policy.py</code> then <code>scripts/extract_policy.py</code>{" "}
          — run once, offline, against the Punjab Clean Air Action Plan. Its output writes
          straight to this project's DuckDB catalogue; this panel only reads it back.
        </p>
      )}

      {!loading && !error && measures.length > 0 && (
        <>
          <p className="hint">
            {measures.length} measure{measures.length === 1 ? "" : "s"} extracted from a
            published plan, {expressible} expressible with this tile's two levers. The rest
            are real commitments — fuel standards, vehicle technology, enforcement — this
            project has no lever for.
          </p>

          {!hasPolygon && (
            <p className="hint">Draw a polygon on the map to apply one of these.</p>
          )}

          <ul className="policy__rows" aria-label="Extracted policy measures">
            {measures.map((item) => (
              <Row key={item.measure.quote} item={item} onApply={onApply} hasPolygon={hasPolygon} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
