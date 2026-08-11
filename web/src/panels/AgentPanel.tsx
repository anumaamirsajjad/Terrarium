/**
 * The search agent, watched rather than waited for.
 *
 * The search runs the real cores ten times and takes tens of seconds. A spinner over that
 * is a broken feature; the trace *is* the feature — each region proposed, each refusal with
 * the validator's own arithmetic, and the greedy control's score sitting beside the
 * agent's the whole way.
 *
 * Two things this panel is careful about, both borrowed from the rest of the product:
 *
 * - **The control is shown even when the agent lost.** `beat_baseline` is rendered either
 *   way. A search that only published its wins would be the one component here exempt from
 *   the standard the hindcast and the air validation set.
 * - **A refusal reads as information, not as an error.** It is coloured like a note, and
 *   its reason is quoted in full, because "5,000 trees need 0.125 km² of crown but this
 *   0.031 km² block has only 0.031 km² plantable" is the most useful sentence the search
 *   produces.
 */

import { useState } from "react";

import type { Attempt, SearchResult } from "../api/client";

export interface AgentPanelProps {
  /** The live trace, oldest first. Grows as events arrive. */
  trace: Attempt[];
  /** One line for the node that just ran, shown while the search is in flight. */
  status: string | null;
  result: SearchResult | null;
  running: boolean;
  error: string | null;
  /** What the search will be scored against. Shown so the model's reach is visible. */
  planner: string;
  onSearch: (goal: string) => void;
  onStop: () => void;
  /** Writes the winning region into the drawn polygon and the levers. */
  onApply: (attempt: Attempt) => void;
}

const EXAMPLE = "get 1 degC off somewhere, reaching as many people as possible";

function scoreLabel(metric: string): string {
  if (metric === "cooling") return "°C expected cooling";
  return "person-degrees";
}

function AttemptRow({ attempt }: { attempt: Attempt }) {
  const refused = attempt.status === "refused";
  return (
    <li
      className={`agent__attempt${refused ? " is-refused" : ""}${
        attempt.proposer === "greedy" ? " is-control" : ""
      }`}
    >
      <span className="agent__regions">{attempt.region_ids.join(", ")}</span>
      <span className="agent__proposer">{attempt.proposer}</span>
      <p className="agent__reason">
        {refused ? "refused — " : ""}
        {attempt.reason}
      </p>
    </li>
  );
}

export default function AgentPanel({
  trace,
  status,
  result,
  running,
  error,
  planner,
  onSearch,
  onStop,
  onApply,
}: AgentPanelProps) {
  const [goal, setGoal] = useState("");
  const [appliedPartial, setAppliedPartial] = useState<Attempt | null>(null);

  const handleApply = (attempt: Attempt) => {
    onApply(attempt);
    // The drawn-polygon state this feeds is a single ring: a merged, multi-region
    // winner only carries its first region across. Say so, rather than let the
    // applied area silently read smaller than the one that was scored.
    setAppliedPartial(attempt.region_ids.length > 1 ? attempt : null);
  };

  const best = result?.best ?? null;
  const baseline = result?.baseline ?? null;

  return (
    <section className="control agent" aria-labelledby="agent-heading">
      <h2 id="agent-heading">Search the tile</h2>

      <form
        className="agent__goal"
        onSubmit={(event) => {
          event.preventDefault();
          const text = goal.trim() || EXAMPLE;
          onSearch(text);
        }}
      >
        <label className="field">
          <span>What are you looking for?</span>
          <input
            type="text"
            value={goal}
            placeholder={EXAMPLE}
            onChange={(event) => setGoal(event.target.value)}
            disabled={running}
            dir="auto"
          />
        </label>

        <div className="row">
          <button type="submit" disabled={running}>
            {running ? "Searching…" : "Search"}
          </button>
          {running && (
            <button type="button" onClick={onStop}>
              Stop
            </button>
          )}
        </div>

        <p className="hint">
          No polygon needed — the agent picks from a fixed 2 km lattice the grid layer
          generated, so it never invents coordinates. Proposals come from{" "}
          <code>{planner}</code>, and every one is checked against what the tile can
          actually hold before a core runs. A model is required: there is no deterministic
          search behind this, because a lattice sweep would be a different, worse procedure
          returning the same shape.
        </p>
        <p className="hint">
          Already know where? Draw a zone and press <code>/</code> to describe a plan for it
          instead — this panel finds a place, that one checks a place you picked.
        </p>
      </form>

      {error && <p className="error-text">{error}</p>}

      {running && status && <p className="agent__status">{status}</p>}

      {trace.length > 0 && (
        <ol className="agent__trace" aria-label="What the search tried">
          {trace.map((attempt, index) => (
            <AttemptRow key={`${attempt.step}-${index}`} attempt={attempt} />
          ))}
        </ol>
      )}

      {result && (
        <div className="agent__result">
          {best?.outcome ? (
            <>
              <p className="agent__headline">
                <strong>{best.plan.name}</strong> over {best.region_ids.join(", ")} —{" "}
                {best.outcome.expected_cooling_c.toFixed(2)} °C across{" "}
                {best.outcome.area_km2.toFixed(1)} km²
              </p>

              <dl className="result__stats">
                <div>
                  <dt>Residents in the region</dt>
                  <dd>{Math.round(best.outcome.people_reached).toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Trees</dt>
                  <dd>{best.outcome.tree_count.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Score</dt>
                  <dd>
                    {(best.score ?? 0).toLocaleString(undefined, {
                      maximumFractionDigits: 2,
                    })}
                    <span className="muted"> {scoreLabel(result.objective.metric)}</span>
                  </dd>
                </div>
              </dl>

              {/* The control, whichever way it went. */}
              <p className={`agent__control${result.beat_baseline ? " is-win" : " is-loss"}`}>
                {baseline?.score != null
                  ? result.beat_baseline
                    ? `Beat the greedy control (${baseline.score.toLocaleString(undefined, {
                        maximumFractionDigits: 2,
                      })} against ${(best.score ?? 0).toLocaleString(undefined, {
                        maximumFractionDigits: 2,
                      })}) on the same candidates and the same cores.`
                    : `Did not beat the greedy control (${baseline.score.toLocaleString(
                        undefined,
                        { maximumFractionDigits: 2 },
                      )} against ${(best.score ?? 0).toLocaleString(undefined, {
                        maximumFractionDigits: 2,
                      })}). That is a finding, not a bug.`
                  : "The greedy control produced no runnable plan, so there is nothing to compare this against."}
              </p>

              <button type="button" className="agent__apply" onClick={() => handleApply(best)}>
                Apply this plan
              </button>

              {appliedPartial && (
                <p className="result__note">
                  Applied region {appliedPartial.region_ids[0]} only — the winning plan merged{" "}
                  {appliedPartial.region_ids.length} regions ({appliedPartial.region_ids.join(", ")}
                  ), but the drawn area can hold one shape at a time. Redraw around the others to
                  include them.
                </p>
              )}
            </>
          ) : (
            <p className="agent__headline">
              The search found no runnable plan. Every proposal was refused before a core ran.
            </p>
          )}

          {result.report.map((line) => (
            <p className="result__note" key={line}>
              {line}
            </p>
          ))}

          <p className="result__caveat">
            {result.simulations_used} simulations and {result.llm_calls_used} model calls in{" "}
            {result.elapsed_s.toFixed(0)} s — {result.stopped_because}.{" "}
            {result.report_source === "unavailable"
              ? "No account was written: the model either could not answer or produced a figure the cores did not, and there is no template behind it. The numbers above are unaffected — they came from the cores."
              : `Written by ${result.report_source}, which may only reword the figures the cores produced; one it invented would have been rejected.`}
          </p>
        </div>
      )}
    </section>
  );
}
