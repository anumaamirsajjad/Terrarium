/**
 * The DSL, as a front door.
 *
 * Three ways in — a preset button, a sentence, or the sliders below this panel — and all
 * three end up as the same three numbers. The panel's job is to make that visible: when a
 * plan resolves, it shows what the polygon can actually hold, what the plan costs, and
 * every note the validator attached, *before* anything is simulated.
 *
 * The refusal is the feature. A plan that cannot fit comes back as an error with the
 * arithmetic in it ("5,000 trees need 0.125 km² of crown; 0.031 km² is plantable"), and
 * that is shown as prominently as a success would be — a silently trimmed plan returns a
 * small delta, which looks exactly like a plan that simply worked badly.
 */

import { useState } from "react";

import type { PlanResponse, Preset } from "../api/client";

export interface ScenarioPanelProps {
  presets: Preset[];
  /** What free text will be parsed with: a model, or the deterministic rule parser. */
  planner: string;
  /** Null until a polygon is closed — a plan cannot be checked without one. */
  hasPolygon: boolean;
  plan: PlanResponse | null;
  error: string | null;
  busy: boolean;
  onPreset: (slug: string) => void;
  onText: (text: string) => void;
  onClear: () => void;
}

function usd(value: number): string {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}k`;
  return `$${value.toFixed(0)}`;
}

export default function ScenarioPanel({
  presets,
  planner,
  hasPolygon,
  plan,
  error,
  busy,
  onPreset,
  onText,
  onClear,
}: ScenarioPanelProps) {
  const [text, setText] = useState("");
  const selected = plan?.plan.name;

  return (
    <section className="control scenario" aria-labelledby="scenario-heading">
      <h2 id="scenario-heading">Scenario</h2>

      <div className="scenario__presets">
        {presets.map((preset) => (
          <button
            key={preset.slug}
            type="button"
            className={`scenario__preset${selected === preset.plan.name ? " is-active" : ""}`}
            disabled={!hasPolygon || busy}
            onClick={() => onPreset(preset.slug)}
            title={preset.caveat}
          >
            <strong>{preset.label}</strong>
            <span className="muted">{preset.summary}</span>
          </button>
        ))}
      </div>

      <form
        className="scenario__text"
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim()) onText(text.trim());
        }}
      >
        <label className="field">
          <span>Or describe it</span>
          <input
            type="text"
            value={text}
            placeholder="plant 5,000 trees and ban cars here in winter"
            onChange={(event) => setText(event.target.value)}
            disabled={!hasPolygon || busy}
          />
        </label>
        <div className="row">
          <button type="submit" disabled={!hasPolygon || busy || !text.trim()}>
            {busy ? "Checking…" : "Check plan"}
          </button>
          {plan && (
            <button type="button" onClick={onClear}>
              Clear plan
            </button>
          )}
        </div>
        <p className="hint">
          Parsed by <code>{planner}</code>. Whatever reads it, the result is checked against
          the tile before anything runs — the model is a front door, not the arbiter.
        </p>
      </form>

      {!hasPolygon && (
        <p className="hint">
          Draw a polygon first. Whether 5,000 trees fit is a question about a place, and
          there is no answer without one.
        </p>
      )}

      {error && <p className="error-text">{error}</p>}

      {plan && (
        <div className="scenario__resolved">
          <p className="scenario__headline">
            <strong>{plan.plan.name}</strong> over {plan.area_km2.toFixed(2)} km²,{" "}
            {plan.window}
          </p>

          <dl className="result__stats">
            {plan.canopy_fraction_added > 0 && (
              <>
                <div>
                  <dt>Trees</dt>
                  <dd>
                    {plan.tree_count.toLocaleString()}
                    <span className="muted"> of {plan.max_trees.toLocaleString()} that fit</span>
                  </dd>
                </div>
                <div>
                  <dt>Canopy requested</dt>
                  <dd>{(plan.canopy_fraction_added * 100).toFixed(0)}%</dd>
                </div>
              </>
            )}
            {plan.emission_fraction_removed > 0 && (
              <div>
                <dt>Vehicle PM2.5 removed</dt>
                <dd>{(plan.emission_fraction_removed * 100).toFixed(0)}%</dd>
              </div>
            )}
            <div>
              <dt>Indicative cost</dt>
              <dd>
                {usd(plan.cost.total_usd)}
                <span className="muted"> · not calibrated</span>
              </dd>
            </div>
          </dl>

          {plan.warnings.map((warning) => (
            <p className="result__note" key={warning}>
              {warning}
            </p>
          ))}
          {plan.notes.map((note) => (
            <p className="result__note" key={note}>
              {note}
            </p>
          ))}

          <p className="result__caveat">{plan.basis}</p>
        </div>
      )}
    </section>
  );
}
