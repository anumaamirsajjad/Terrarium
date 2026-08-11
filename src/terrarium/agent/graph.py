"""LangGraph wiring, the budget, and the event stream.

D17 refused LangGraph for the planner and that ruling stands: parse then validate is two
nodes with no branching, no cycle and no shared state to checkpoint. **This is the case
D17 named as its own reopening condition** — *"If agents later need to choose between
interventions and iterate, that is a real graph and this reopens."* It has a cycle, a
conditional edge, shared state and a budget, which is what a graph runtime is actually for.

```
parse_goal ──▶ survey ──▶ propose ◀───────────────┐
  (LLM)        (pure)      (LLM)                  │
                             │                    │
                             ▼                    │
                          check ─── refused ──────┤   the refusal is the feedback signal
                          (pure)                  │
                             │ ok                 │
                             ▼                    │
                           run ──▶ score ──▶ decide
                         (cores)   (pure)      │
                                               ├─ budget spent / target met ──▶ report (LLM)
                                               └─ otherwise ─────────────────────┘
```

This module is the only importer of `langgraph` in the tree.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator

import xarray as xr
from langgraph.graph import END, START, StateGraph

from terrarium.agent.nodes import SearchContext, SearchState
from terrarium.agent.objective import units_for
from terrarium.agent.state import (
    Attempt,
    Candidate,
    SearchBudget,
    SearchEvent,
    SearchResult,
)
from terrarium.api.runtime import Runtime

logger = logging.getLogger(__name__)


def build_graph(context: SearchContext) -> object:
    """Compile the graph for one request's context.

    Compiled per search rather than cached per process, because the cube slice and the
    lattice are both per-window and closing over the wrong one would silently score a
    summer proposal against a winter composite. Compilation is microseconds; the cores are
    the cost here.
    """
    builder: StateGraph[SearchState, None, SearchState, SearchState] = StateGraph(
        SearchState
    )

    builder.add_node("parse_goal", context.parse_goal)
    builder.add_node("survey", context.survey)
    builder.add_node("propose", context.propose)
    builder.add_node("check", context.check)
    builder.add_node("run", context.run)
    builder.add_node("score", context.score)
    builder.add_node("report", context.report)

    builder.add_edge(START, "parse_goal")
    builder.add_edge("parse_goal", "survey")
    builder.add_edge("survey", "propose")
    builder.add_edge("propose", "check")

    # A refusal never reaches a core. It goes back to `propose` carrying the validator's
    # own arithmetic, unless the budget says the search is over.
    builder.add_conditional_edges(
        "check",
        lambda state: "run" if state.get("resolved") is not None else context.decide(state),
        {"run": "run", "propose": "propose", "report": "report"},
    )
    builder.add_edge("run", "score")
    builder.add_conditional_edges(
        "score", context.decide, {"propose": "propose", "report": "report"}
    )
    builder.add_edge("report", END)

    return builder.compile()


# LangGraph counts every node execution against one limit, and one loop here is five nodes.
# The real budget is `SearchBudget`, enforced in `decide`; this only has to be loose enough
# never to fire first, because a recursion-limit abort produces no report and no result.
_STEPS_PER_LOOP = 5
_RECURSION_HEADROOM = 12


def _recursion_limit(budget: SearchBudget) -> int:
    return (budget.max_simulations + budget.max_llm_calls) * _STEPS_PER_LOOP + _RECURSION_HEADROOM


def run_search(
    *,
    runtime: Runtime,
    window: xr.Dataset,
    label: str,
    season: str,
    candidates: tuple[Candidate, ...],
    goal: str,
    settings: object,
    budget: SearchBudget | None = None,
    search_id: str | None = None,
) -> Iterator[SearchEvent]:
    """Run one search, yielding an event per node transition. The last carries the result.

    A generator rather than a function returning a result, because the whole point of A4
    is that the client watches the search happen. The caller decides whether to render the
    events (SSE) or drain them (a test, a script).
    """
    budget = budget or SearchBudget()
    context = SearchContext(
        runtime=runtime,
        window=window,
        label=label,
        candidates=candidates,
        budget=budget,
        settings=settings,
    )
    graph = build_graph(context)

    started = time.monotonic()
    initial: SearchState = {
        "goal": goal,
        "tried": [],
        "step": 0,
        "simulations": 0,
        "llm_calls": 0,
        "started_at": started,
    }

    state: SearchState = {**initial}
    seen = 0
    try:
        for update in graph.stream(  # type: ignore[attr-defined]
            initial,
            stream_mode="updates",
            config={"recursion_limit": _recursion_limit(budget)},
        ):
            for node, patch in update.items():
                state = {**state, **(patch or {})}
                tried = state.get("tried", [])
                # Only ever the attempts this node actually added, so a refusal and the
                # scored attempt that follows it arrive as two separate trace lines.
                fresh = tried[seen:]
                seen = len(tried)
                for attempt in fresh:
                    yield SearchEvent(
                        node=node, message=_attempt_line(attempt, state), attempt=attempt
                    )
                if not fresh:
                    yield SearchEvent(node=node, message=_node_line(node, state))
    except Exception as exc:
        # A search is a long-running stream and the client has already rendered half of it.
        # Raising out of a generator mid-SSE gives the browser a truncated event stream and
        # no reason, so the failure is reported *as an event* and the stream ends cleanly.
        logger.exception("search failed")
        yield SearchEvent(node="error", message=f"the search stopped: {exc}")
        return

    yield SearchEvent(
        node="done",
        message=state.get("stopped_because", "the search finished"),
        result=_result(
            state,
            search_id=search_id or uuid.uuid4().hex[:12],
            goal=goal,
            label=label,
            season=season,
            elapsed_s=time.monotonic() - started,
        ),
    )


def _attempt_line(attempt: Attempt, state: SearchState) -> str:
    """One trace line: *proposed -> refused, with the reason*, or *proposed -> scored*."""
    regions = ", ".join(attempt.region_ids)
    if attempt.status == "refused":
        return f"{regions}: refused — {attempt.reason}"
    return f"{regions}: {attempt.reason}"


def _node_line(node: str, state: SearchState) -> str:
    if node == "parse_goal":
        objective = state.get("objective")
        return (
            f"reading the goal: maximise {objective.metric} ({units_for(objective)})"
            if objective is not None
            else "reading the goal"
        )
    if node == "survey":
        return f"ranked {len(state.get('candidates', []))} regions and ran the greedy control"
    if node == "propose":
        proposal = state.get("proposal")
        return (
            f"proposing {', '.join(proposal[0])}: {proposal[1].name}"
            if proposal is not None
            else "no region left to propose"
        )
    if node == "check":
        return "checking the plan against what the tile can hold"
    if node == "run":
        return "running the thermal, equity and air cores"
    if node == "report":
        return "writing the report"
    return node


def _result(
    state: SearchState,
    *,
    search_id: str,
    goal: str,
    label: str,
    season: str,
    elapsed_s: float,
) -> SearchResult:
    best = state.get("best")
    baseline = state.get("baseline")
    return SearchResult(
        search_id=search_id,
        goal=goal,
        objective=state["objective"],
        window=label,
        season=season,
        best=best,
        baseline=baseline,
        # Strictly greater. A tie is not a win, and the control *is* one of the attempts,
        # so a search that never improved on it reports `False` and says so in the report.
        beat_baseline=(
            best is not None
            and baseline is not None
            and best.score is not None
            and baseline.score is not None
            and best.score > baseline.score
        ),
        tried=tuple(state.get("tried", [])),
        simulations_used=state.get("simulations", 0),
        llm_calls_used=state.get("llm_calls", 0),
        elapsed_s=elapsed_s,
        stopped_because=state.get("stopped_because", "the search finished"),
        report=state.get("report", ()),
        report_source=state.get("report_source", "template"),
    )
