"""The search graph's node functions. **The only file in this package that talks to a
model** (D25), and it does so in exactly three of the eight nodes.

Everything else — the candidates, the headroom, the refusal, the cores, the score — is
deterministic. What the model chooses is *what to try next*, which is the one part of a
search that is genuinely a judgement call.

**A model is required here.** The three deterministic stand-ins this file used to carry —
a regex goal parser, a lattice sweep in rank order, and a template report — were removed
when the key stopped being optional, and the reason is the same for all three: each was a
*different procedure* returning the same shape, so a run that quietly became one reported
a number the agent had not found. `SearchUnavailable` is raised instead, and
`/agent/search` refuses with 503 before the stream opens.

What survives is the part that was always doing the work: the post-checks.

Every figure in `tried` and `best` came out of a core. The model never produces a number
that reaches a result: `parse_goal` produces a schema-validated `Objective`, `propose`
produces a `region_id` and a `Plan` that `dsl.validate.resolve` then refuses or converts,
and `report` produces prose that `dsl.llm`'s own faithfulness guards check against the
figures it was handed. Those three post-checks are what D25 requires of a call site.

**The refusal loop is the whole idea.** `dsl.validate.resolve` already raises `PlanError`
carrying the arithmetic — *"5,000 trees need 0.125 km2 of crown at 25 m2 each, but this
0.031 km2 polygon…"*. That string goes into `tried` and the edge routes back to `propose`.
The validator built as a product feature turns out to be a usable reward signal with no
extra work, which is the most interesting thing about this phase.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

import xarray as xr
from pydantic import ValidationError

from terrarium.agent.baseline import greedy_best, rank
from terrarium.agent.evaluate import evaluate, summarise
from terrarium.agent.objective import better, satisfies, score, units_for
from terrarium.agent.state import (
    Attempt,
    Candidate,
    Objective,
    Outcome,
    SearchBudget,
)
from terrarium.api.candidates import region_measurement
from terrarium.api.runtime import Runtime
from terrarium.dsl.llm import (
    LLMUnavailable,
    _headline_figures_survive,
    _numbers_are_faithful,
    resolve_adapter,
)
from terrarium.dsl.schema import Plan
from terrarium.dsl.validate import PlanError, ResolvedPlan, resolve

logger = logging.getLogger(__name__)


class SearchUnavailable(RuntimeError):
    """No model could be reached, so there is no search to run.

    **Not recoverable, deliberately.** Every other model seam in this project degrades to a
    deterministic answer; this one used to and no longer does. A search is the model
    *choosing what to try*, and the deterministic version of that — sweep the lattice in
    rank order — is a different, worse procedure wearing the same result shape. Returning
    it under the same field names would report a number the agent did not find.

    `/agent/search` refuses with 503 before the stream opens when no key is configured, so
    this is normally reached only by a provider dying mid-run.
    """


# How many regions the proposer is shown. All 121 blocks is a prompt nobody reads and a
# cost nobody needs; the top slice by opportunity plus everything already tried is the
# information a next move is actually made from. Regions outside it are still reachable —
# the model may name any id, and unknown ids are what the post-check rejects.
CANDIDATES_SHOWN = 24


class SearchState(TypedDict, total=False):
    """The graph's mutable state. Plain values only, so a checkpoint is a JSON document.

    The cube, the booster, the grid and the settings are deliberately *not* here: they are
    per-deployment context, they are enormous, and putting them on a checkpointed channel
    would serialise a Zarr store between every node. They live on `SearchContext`, which
    the nodes close over.
    """

    goal: str
    objective: Objective
    candidates: list[Candidate]
    baseline: Attempt | None
    tried: list[Attempt]
    best: Attempt | None

    # Handed from `propose` to `check`, and from `check` to `run`. Cleared on a refusal so
    # a stale proposal can never be simulated twice.
    proposal: tuple[tuple[str, ...], Plan] | None
    resolved: ResolvedPlan | None
    outcome: Outcome | None

    step: int
    simulations: int
    llm_calls: int
    started_at: float
    stopped_because: str
    report: tuple[str, ...]
    report_source: str


@dataclass(frozen=True)
class SearchContext:
    """Everything the nodes need that is not search state.

    A frozen dataclass of loaded artefacts, built once per request from the API's runtime.
    The node methods below are bound to it and handed to LangGraph as plain callables.
    """

    runtime: Runtime
    window: xr.Dataset
    label: str
    candidates: tuple[Candidate, ...]
    budget: SearchBudget
    settings: Any

    # ---------------------------------------------------------------- parse_goal ---

    def parse_goal(self, state: SearchState) -> SearchState:
        """Natural language in, a schema-validated `Objective` out.

        **Raises `SearchUnavailable` if the model cannot answer.** There is no rule parser
        behind this any more: a goal is the one input the whole search is conditioned on,
        and a search that silently substituted a default objective would answer a question
        nobody asked while looking exactly like a search that worked.

        The post-check survives the fallback's removal and is the part that mattered
        (D25): whatever the model returns is validated as an `Objective` or it is nothing.
        """
        goal = state["goal"]
        adapter = resolve_adapter(self.settings, task="agent")
        if adapter is None:
            raise SearchUnavailable(
                "the search agent needs a model. Set TERRARIUM_GROQ_API_KEY or "
                "TERRARIUM_GEMINI_API_KEY."
            )

        calls = state.get("llm_calls", 0) + 1
        try:
            raw = adapter.complete_json(system=GOAL_SYSTEM, user=goal.strip())
            parsed = Objective.model_validate(json.loads(raw))
        except (LLMUnavailable, ValueError, ValidationError, TypeError) as exc:
            raise SearchUnavailable(
                f"{adapter.name} could not read the goal ({type(exc).__name__})"
            ) from exc

        # The window is the runtime's to validate, not the model's to invent. A label that
        # is not in this cube is dropped rather than 404-ing a search that is otherwise
        # perfectly runnable.
        if parsed.window is not None and parsed.window not in self.runtime.windows:
            parsed = parsed.model_copy(update={"window": None})
        return {"objective": parsed, "llm_calls": calls}

    # -------------------------------------------------------------------- survey ---

    def survey(self, state: SearchState) -> SearchState:
        """Rank the candidates and run the deterministic control. No model.

        The control runs *before* the agent, so its simulations are spent whatever happens
        next and the comparison exists even if the model is unreachable and every proposal
        falls back. Its calls count against the same simulation budget, because they cost
        the same wall clock and the user is waiting for both.
        """
        objective = state["objective"]
        baseline, used = greedy_best(
            runtime=self.runtime,
            window=self.window,
            candidates=self.candidates,
            objective=objective,
        )
        return {
            "candidates": rank(self.candidates),
            "baseline": baseline,
            # The control is a real attempt and belongs in the trace. It is also the
            # incumbent the model has to beat, which is what makes `beat_baseline`
            # something the search has to earn rather than something the report asserts.
            "tried": [baseline] if baseline else [],
            "best": baseline,
            "simulations": state.get("simulations", 0) + used,
        }

    # ------------------------------------------------------------------- propose ---

    def propose(self, state: SearchState) -> SearchState:
        """Pick regions and levers. **The only creative step in the graph.**

        The model returns `region_ids` and a `Plan` — never coordinates (D26). Both go
        through a post-check: ids must exist in the lattice, and the plan must validate as
        a `Plan`.

        **A failed proposal ends the search rather than substituting a lattice sweep.**
        The sweep that used to sit here was a worse search wearing the same result shape,
        and a run that quietly became it would report a number the agent did not find. The
        search still returns everything it had already scored — the greedy control and any
        earlier winner — so a provider dying mid-run costs the remaining steps, not the
        work already done.
        """
        state_step = state.get("step", 0) + 1
        adapter = resolve_adapter(self.settings, task="agent")
        if adapter is None:
            raise SearchUnavailable("the search agent needs a model")

        calls = state.get("llm_calls", 0) + 1
        try:
            raw = adapter.complete_json(
                system=PROPOSE_SYSTEM, user=self._propose_prompt(state)
            )
            payload = json.loads(raw)
            ids = tuple(str(value) for value in payload["region_ids"])
            plan = Plan.model_validate(payload["plan"])
        except (LLMUnavailable, ValueError, ValidationError, TypeError, KeyError) as exc:
            logger.warning("proposal unusable, ending the search: %s", type(exc).__name__)
            return {
                "proposal": None,
                "step": state_step,
                "llm_calls": calls,
                "stopped_because": (
                    f"{adapter.name} stopped producing usable proposals "
                    f"({type(exc).__name__})"
                ),
            }

        known = {candidate.region_id for candidate in self.candidates}
        unknown = [region for region in ids if region not in known]
        if not ids or unknown:
            # A hallucinated id is the failure D26 exists to make cheap: it is caught by a
            # set lookup rather than by a simulation over a polygon selecting no cells.
            # Recorded as a refused attempt so the model sees it in `tried` and the next
            # proposal is conditioned on having got it wrong.
            refusal = Attempt(
                step=state_step,
                region_ids=ids,
                plan=plan,
                status="refused",
                reason=(
                    f"no such region: {', '.join(unknown) or '(none named)'}. Choose ids "
                    "from the regions list."
                ),
            )
            return {
                "tried": [*state.get("tried", []), refusal],
                "proposal": None,
                "step": state_step,
                "llm_calls": calls,
            }

        return {"proposal": (ids, plan), "step": state_step, "llm_calls": calls}

    def _propose_prompt(self, state: SearchState) -> str:
        """The regions on offer and everything already tried, as compact JSON.

        The refusals carry their full text. That is the point: *"5,000 trees need 0.125 km2
        of crown but this polygon has only 0.031 km2 still plantable"* tells a model both
        what went wrong and by how much, which is a far stronger signal than a rejection.
        """
        objective = state["objective"]
        tried = state.get("tried", [])
        seen = {region for attempt in tried for region in attempt.region_ids}

        offered = [
            candidate
            for candidate in (state.get("candidates") or rank(self.candidates))
            if candidate.region_id not in seen
        ][:CANDIDATES_SHOWN]

        return json.dumps(
            {
                "goal": state["goal"],
                "maximise": objective.metric,
                "score_units": units_for(objective),
                "target_cooling_c": objective.target_cooling_c,
                "max_cost_usd": objective.max_cost_usd,
                "window": self.label,
                "regions": [
                    {
                        "region_id": candidate.region_id,
                        "km2": round(candidate.area_m2 / 1e6, 2),
                        "max_trees": candidate.max_trees,
                        "plantable_canopy_km2": round(candidate.plantable_canopy_m2 / 1e6, 3),
                        "residents": round(candidate.population),
                        "mean_lst_c": (
                            round(candidate.mean_lst_c, 1)
                            if candidate.mean_lst_c is not None
                            else None
                        ),
                        "road_emission_g_s": round(candidate.emission_g_s, 4),
                    }
                    for candidate in offered
                ],
                "tried": [
                    {
                        "regions": list(attempt.region_ids),
                        "status": attempt.status,
                        "reason": attempt.reason,
                        "score": attempt.score,
                    }
                    for attempt in tried
                ],
            },
            ensure_ascii=False,
        )

    # --------------------------------------------------------------------- check ---

    def check(self, state: SearchState) -> SearchState:
        """`measure` + `dsl.validate.resolve`. Pure, and the source of the feedback signal.

        A refusal is recorded as an `Attempt` and the proposal is cleared, which is what
        routes the graph back to `propose` without ever reaching a core. A plan that cannot
        physically fit costs microseconds here instead of a simulation.
        """
        proposal = state.get("proposal")
        if proposal is None:
            return {"resolved": None}

        ids, plan = proposal
        regions = self._regions(ids)

        try:
            resolved = resolve(plan, region_measurement(regions))
        except (PlanError, ValueError) as exc:
            refusal = Attempt(
                step=state.get("step", 0),
                region_ids=ids,
                plan=plan,
                status="refused",
                reason=str(exc),
            )
            return {
                "tried": [*state.get("tried", []), refusal],
                "proposal": None,
                "resolved": None,
            }

        return {"resolved": resolved}

    # ----------------------------------------------------------------------- run ---

    def run(self, state: SearchState) -> SearchState:
        """The cores. Thermal, equity and air, through the shared `evaluate`.

        Shared with the control on purpose: the two have to be scored the same way or
        "the agent beat greedy" is not a comparison.
        """
        proposal = state.get("proposal")
        resolved = state.get("resolved")
        if proposal is None or resolved is None:
            return {"outcome": None}

        ids, _ = proposal
        outcome = evaluate(
            runtime=self.runtime,
            window=self.window,
            regions=self._regions(ids),
            resolved=resolved,
        )
        return {"outcome": outcome, "simulations": state.get("simulations", 0) + 1}

    # --------------------------------------------------------------------- score ---

    def score(self, state: SearchState) -> SearchState:
        """`Objective` -> scalar, and update `best`. Pure arithmetic over an `Outcome`."""
        proposal = state.get("proposal")
        outcome = state.get("outcome")
        if proposal is None or outcome is None:
            return {}

        ids, plan = proposal
        objective = state["objective"]
        value = score(objective, outcome)
        attempt = Attempt(
            step=state.get("step", 0),
            region_ids=ids,
            plan=plan,
            status="scored",
            score=value,
            outcome=outcome,
            reason=summarise(outcome, score_value=value, units=units_for(objective)),
        )

        incumbent = state.get("best")
        return {
            "tried": [*state.get("tried", []), attempt],
            "best": attempt if better(attempt, incumbent) else incumbent,
            "proposal": None,
            "resolved": None,
            "outcome": None,
        }

    # -------------------------------------------------------------------- decide ---

    def decide(self, state: SearchState) -> str:
        """`propose` again, or `report`. The conditional edge, and the only place the
        budget is enforced.

        Four ways a search ends, and the trace names which one: the target was met, the
        simulations ran out, the model calls ran out, or the clock ran out. "It stopped"
        with no reason is the sort of answer that makes a search look like a spinner.
        """
        objective = state["objective"]
        best = state.get("best")

        if best is not None and best.outcome is not None and satisfies(objective, best.outcome):
            return "report"
        if state.get("proposal") is None and state.get("stopped_because"):
            return "report"
        if state.get("simulations", 0) >= self.budget.max_simulations:
            return "report"
        if state.get("llm_calls", 0) >= self.budget.max_llm_calls:
            return "report"
        if time.monotonic() - state.get("started_at", 0.0) >= self.budget.wall_clock_s:
            return "report"
        # A keyless search spends no LLM calls, so without a step ceiling the refusal loop
        # would be bounded by nothing. Refusals are cheap but they are not free.
        if state.get("step", 0) >= self.budget.max_simulations + self.budget.max_llm_calls:
            return "report"
        return "propose"

    def stop_reason(self, state: SearchState) -> str:
        """Why `decide` sent the search to `report`, in words, evaluated at the end."""
        objective = state["objective"]
        best = state.get("best")
        if best is not None and best.outcome is not None and satisfies(objective, best.outcome):
            return "the goal was met"
        if state.get("stopped_because"):
            return str(state["stopped_because"])
        if state.get("simulations", 0) >= self.budget.max_simulations:
            return f"the simulation budget ran out ({self.budget.max_simulations})"
        if state.get("llm_calls", 0) >= self.budget.max_llm_calls:
            return f"the model-call budget ran out ({self.budget.max_llm_calls})"
        if time.monotonic() - state.get("started_at", 0.0) >= self.budget.wall_clock_s:
            return f"the {self.budget.wall_clock_s:.0f} s time budget ran out"
        return "the search ran out of regions to try"
    # -------------------------------------------------------------------- report ---

    def report(self, state: SearchState) -> SearchState:
        """Narrate the search, under `dsl/llm.py`'s existing guards.

        The model is handed a **facts block, not prose** — the figures the cores produced,
        with no sentences over them — and writes the account from that. There is no
        template report behind it any more: a deterministic prose version was a second
        writer of the same claims, and the only thing worth keeping from it was the
        arithmetic, which is what the facts block is.

        `_numbers_are_faithful` and `_headline_figures_survive` are **imported, not
        reimplemented** — a second copy of a guard is a guard that drifts, and this one is
        the reason a model is allowed near a search result at all. They now compare against
        the facts, which is a stricter source than prose was: every figure in the report
        has to be one a core actually returned.

        A failure here loses the prose and nothing else. The result still carries the
        numbers, and they came from the cores, so a search that cannot be narrated is
        still a search that ran.
        """
        stopped = self.stop_reason(state)
        facts = self._facts(state)
        headline = self._headline_figures(state)

        adapter = resolve_adapter(self.settings, task="agent")
        if adapter is None or state.get("llm_calls", 0) >= self.budget.max_llm_calls:
            return {"report": (), "report_source": "unavailable", "stopped_because": stopped}

        calls = state.get("llm_calls", 0) + 1
        unwritten: SearchState = {
            "report": (),
            "report_source": "unavailable",
            "stopped_because": stopped,
            "llm_calls": calls,
        }

        try:
            raw = adapter.complete_json(system=REPORT_SYSTEM, user=facts)
            payload = json.loads(raw)
            written_lines = tuple(str(line) for line in payload["lines"])
        except (LLMUnavailable, ValueError, TypeError, KeyError) as exc:
            logger.warning("search report unavailable: %s", type(exc).__name__)
            return unwritten

        written = "\n".join(written_lines)
        if not written_lines or not _numbers_are_faithful(source=facts, rewritten=written):
            logger.warning("search report invented figures, dropping it")
            return unwritten
        if not _headline_figures_survive(headline=headline, rewritten=written):
            logger.warning("search report dropped the figures it is about, dropping it")
            return unwritten

        return {
            "report": written_lines,
            "report_source": adapter.name,
            "stopped_because": stopped,
            "llm_calls": calls,
        }

    def _facts(self, state: SearchState) -> str:
        """Everything the report may mention, and nothing else.

        **Every figure here came out of a core.** The guard compares the model's output
        against exactly this string, so a line dropped from here is a number the report is
        silently forbidden to use — and a line added here is one it is newly allowed to.

        The control's score is always present, including when the agent lost to it. A
        search that only published its wins would be exactly the kind of claim this project
        does not ship, and the way to stop that is to make losing a *fact the writer was
        handed* rather than a branch the writer could choose not to take.
        """
        objective = state["objective"]
        best = state.get("best")
        baseline = state.get("baseline")
        tried = state.get("tried", [])
        refused = [attempt for attempt in tried if attempt.status == "refused"]

        rows = [
            f"window: {self.label}",
            f"goal: {state['goal']}",
            f"scored on: {units_for(objective)}",
            f"simulations run: {state.get('simulations', 0)}",
            f"model calls used: {state.get('llm_calls', 0)}",
            f"elapsed: {time.monotonic() - state.get('started_at', 0.0):.0f} s",
            f"proposals refused before a core ran: {len(refused)} of {len(tried)}",
        ]
        if refused:
            rows.append(f"first refusal: {refused[0].reason}")

        if best is None or best.outcome is None:
            rows.append("outcome: no runnable plan was found")
        else:
            outcome = best.outcome
            rows += [
                f"best plan: {best.plan.name}",
                f"best regions: {', '.join(best.region_ids)}",
                f"expected cooling: {outcome.expected_cooling_c:.2f} degC, already divided "
                "by the hindcast correction of 2.5",
                f"raw model output inside the region: {outcome.mean_delta_inside_c:+.2f} degC",
                f"area: {outcome.area_km2:.1f} km2",
                f"residents in the region: {outcome.people_reached:,.0f}",
                f"trees: {outcome.tree_count:,}",
                f"indicative cost: ${outcome.cost_usd:,.0f}, from literature unit costs "
                "rather than a quote",
                (
                    f"agent score: {best.score:,.2f}"
                    if best.score is not None
                    else "agent score: not scored"
                ),
            ]
            if outcome.delta_pm25 is not None:
                rows.append(
                    f"locally-generated PM2.5 inside the region: {outcome.delta_pm25:+.2f} "
                    "ug/m3, uncalibrated - literature emission factors for a South Asian "
                    "fleet, not measurements of this city"
                )

        if (
            baseline is not None
            and baseline.score is not None
            and best is not None
            and best.score is not None
        ):
            verdict = (
                "beat"
                if best.score > baseline.score
                else "matched"
                if best.score == baseline.score
                else "LOST TO"
            )
            rows.append(
                f"greedy control score: {baseline.score:,.2f}, and the agent {verdict} it, "
                "on the same candidates and the same cores"
            )
        else:
            rows.append(
                "greedy control: produced no runnable plan, so there is nothing to compare "
                "the agent against"
            )

        rows.append(
            "caveat that must survive: this models mid-morning land surface temperature, "
            "not the air a thermometer reads, and the same planting cools roughly four "
            "times more in summer than in winter"
        )
        return "\n".join(rows)

    def _headline_figures(self, state: SearchState) -> str:
        """The figures a report is *about*, which it may not drop.

        A model told loudly never to invent a number complies by dropping every number —
        the failure `_headline_figures_survive` exists for. What a reader cannot lose is
        what the winning plan buys, what it costs, and what the control scored, so those
        are required to survive; everything else in `_facts` is the writer's to leave out.
        """
        best = state.get("best")
        baseline = state.get("baseline")
        if best is None or best.outcome is None:
            return ""

        figures = [
            f"{best.outcome.expected_cooling_c:.2f}",
            f"{best.outcome.area_km2:.1f}",
            f"{best.outcome.cost_usd:,.0f}",
        ]
        if baseline is not None and baseline.score is not None:
            figures.append(f"{baseline.score:,.2f}")
        return " ".join(figures)

    # ------------------------------------------------------------------ internals ---

    def _regions(self, ids: Sequence[str]) -> list[Candidate]:
        by_id = {candidate.region_id: candidate for candidate in self.candidates}
        return [by_id[region] for region in ids if region in by_id]


GOAL_SYSTEM = """You convert a city planner's goal into one JSON object. Output JSON only.

Schema:
{
  "metric": "cooling" | "person_degrees" | "cost_effectiveness",
  "target_cooling_c": number > 0, or null,
  "max_cost_usd": number > 0, or null,
  "window": string like "2024-winter", or null,
  "description": the goal restated in under 200 characters
}

Rules:
- "metric" is what to MAXIMISE. "cooling" = coldest result. "person_degrees" = reach the
  most residents. "cost_effectiveness" = most residents cooled per dollar.
- "target_cooling_c" and "max_cost_usd" are HARD CONSTRAINTS, not preferences. Set them
  only when the text states a figure. "$500k" -> 500000. "1 degree" -> 1.0.
- Never invent a constraint the text does not state. null is the correct answer far more
  often than a number is.
"""

PROPOSE_SYSTEM = """You are searching a city tile for the best tree-planting or traffic
intervention. Output JSON only.

You are given regions of the tile and everything already tried. Choose what to try next.

Schema:
{
  "region_ids": [one or more ids from the "regions" list, exactly as written],
  "plan": {
    "name": string, max 80 chars,
    "actions": [ one or both of:
        {"kind": "plant_trees", "tree_count": integer >= 1}
        {"kind": "plant_trees", "canopy_fraction_added": number in (0, 1]}
        {"kind": "restrict_vehicles", "emission_fraction_removed": number in (0, 1]}
    ]
  }
}

Rules:
- NEVER invent a region id. Use only ids from the "regions" list. An id that does not
  exist wastes the step.
- You may select SEVERAL region ids to cover a larger area. They need not be adjacent.
- "max_trees" is how many trees that region can physically hold. A tree_count above the
  selected regions' combined max_trees WILL BE REFUSED, and the refusal is in "tried"
  with the arithmetic. A canopy_fraction_added is capped per cell instead of refused.
- Read "tried". A refused attempt tells you exactly how much room there actually was.
  A scored attempt tells you what that region was worth. Do not repeat either.
- Regions with "plantable_canopy_km2" near zero are water or already fully wooded.
  Planting there is refused every time.
- "restrict_vehicles" only changes air quality, never temperature. It is only worth
  proposing where "road_emission_g_s" is meaningfully above zero.
"""

REPORT_SYSTEM = """You rewrite an account of an automated search for a city councillor.

Rules, in order of importance:
1. NEVER introduce a number, percentage, quantity or unit that is not in the input. Copy
every number EXACTLY as written - do not round, rescale, convert or combine them.
2. KEEP the input's numbers, especially the comparison against the greedy control. If the
search lost to the control, say so as plainly as the input does. That is the finding.
3. Do not add a fact, a cause or a claim the input does not make.
4. Plain words. Say "ground temperature" not "land surface temperature".
5. Short sentences. British English. No exclamation marks, no salesmanship.

Return JSON only, with exactly these keys:
{"lines": [str, ...]}"""
