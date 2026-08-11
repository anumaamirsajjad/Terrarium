/**
 * The panel has one job the rest of the product depends on it not fudging: **show the
 * control's score whichever way the comparison went.** A search UI that only rendered its
 * wins would be the one screen here exempt from the standard the hindcast correction and
 * the air validation set.
 *
 * The other property worth a test is that a refusal renders as a readable sentence rather
 * than being swallowed — the validator's arithmetic is the most useful line the search
 * produces, and it arrives on the attempts that failed.
 *
 * Server-rendered to a string, like every other panel test here: `react-dom` is already a
 * dependency, so this needs no testing-library and no jsdom. It cannot test the Apply
 * click, which is a wiring question `App.tsx` owns; what it does catch is the panel
 * crashing, and an unflattering figure being quietly dropped from the markup.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Attempt, SearchResult } from "../api/client";
import AgentPanel, { type AgentPanelProps } from "./AgentPanel";

function attempt(overrides: Partial<Attempt> = {}): Attempt {
  return {
    step: 1,
    region_ids: ["r03c05"],
    plan: { name: "Street trees", actions: [{ kind: "plant_trees", tree_count: 4000 }] },
    status: "scored",
    proposer: "model",
    reason: "0.41 degC expected cooling, 4.0 km2, 4,000 trees",
    score: 4812,
    outcome: {
      mean_delta_inside_c: -1.02,
      expected_cooling_c: 0.41,
      person_degrees: 4812,
      people_reached: 48000,
      tree_count: 4000,
      area_km2: 4.0,
      delta_pm25: null,
    },
    ...overrides,
  };
}

function result(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    search_id: "abc123",
    goal: "cool this tile",
    objective: {
      metric: "person_degrees",
      target_cooling_c: null,
      window: null,
      description: "cool this tile",
    },
    window: "2024-summer",
    season: "summer",
    best: attempt(),
    baseline: attempt({ proposer: "greedy", score: 3000, region_ids: ["r01c04"] }),
    beat_baseline: true,
    tried: [],
    simulations_used: 6,
    llm_calls_used: 0,
    elapsed_s: 12,
    stopped_because: "the simulation budget ran out (10)",
    report: ["Best plan: Street trees over r03c05."],
    report_source: "groq:test",
    ...overrides,
  };
}

const noop = () => {};

function markup(props: Partial<AgentPanelProps> = {}): string {
  return renderToStaticMarkup(
    <AgentPanel
      trace={[]}
      status={null}
      result={null}
      running={false}
      error={null}
      planner="rules (no model configured)"
      onSearch={noop}
      onStop={noop}
      onApply={noop}
      {...props}
    />,
  );
}

describe("AgentPanel", () => {
  it("says it needs no polygon", () => {
    expect(markup()).toContain("No polygon needed");
  });

  it("attributes the proposer, including the keyless one", () => {
    expect(markup()).toContain("rules (no model configured)");
  });

  it("reports beating the control", () => {
    const html = markup({ result: result() });
    expect(html).toContain("Beat the greedy control");
    expect(html).toContain("3,000");
    expect(html).toContain("4,812");
  });

  it("reports LOSING to the control just as plainly", () => {
    const html = markup({
      result: result({
        beat_baseline: false,
        baseline: attempt({ proposer: "greedy", score: 9000 }),
      }),
    });

    expect(html).toContain("Did not beat the greedy control");
    expect(html).toContain("That is a finding, not a bug");
    // The losing comparison still carries both numbers, so the reader can check it.
    expect(html).toContain("9,000");
  });

  it("says when there was no control to compare against", () => {
    const html = markup({ result: result({ baseline: null, beat_baseline: false }) });
    expect(html).toContain("nothing to compare this against");
  });

  it("quotes a refusal's arithmetic in full", () => {
    const refusal = attempt({
      status: "refused",
      score: null,
      outcome: null,
      reason:
        "900,000 trees need 22.500 km2 of crown at 25 m2 each, but this 4.000 km2 " +
        "polygon has only 3.146 km2 still plantable",
    });
    const html = markup({ trace: [refusal] });

    expect(html).toContain("22.500 km2 of crown");
    expect(html).toContain("3.146 km2 still plantable");
    expect(html).toContain("refused");
  });

  it("offers Apply only when there is a plan to apply", () => {
    expect(markup({ result: result() })).toContain("Apply this plan");
    expect(markup({ result: result({ best: null }) })).not.toContain("Apply this plan");
  });

  it("says a search that found nothing found nothing", () => {
    expect(markup({ result: result({ best: null }) })).toContain("found no runnable plan");
  });

  it("carries the corrected cooling figure, not the raw model output", () => {
    const html = markup({ result: result() });
    expect(html).toContain("0.41");
    // -1.02 is the raw delta. It belongs in the report's own line, never in the headline.
    expect(html).not.toContain("1.02 °C");
  });

  it("attributes the report to whoever wrote it", () => {
    const html = markup({ result: result() });
    expect(html).toContain("Written by groq:test");
    expect(html).toContain("6 simulations and 0 model calls");
  });

  it("says plainly when no account was written, and keeps the numbers", () => {
    // There is no template report any more, so an unwritten one is a real gap — but the
    // figures came from the cores and are unaffected, which is what the copy has to say.
    const html = markup({ result: result({ report: [], report_source: "unavailable" }) });

    expect(html).toContain("No account was written");
    expect(html).toContain("came from the cores");
    // The result itself still renders in full.
    expect(html).toContain("Apply this plan");
    expect(html).toContain("0.41");
  });
});
