"""The graph, driven by a scripted fake model. No network, ever.

Three properties, and they are the three the phase was written to deliver:

1. **It cycles on a refusal.** `dsl.validate.resolve` rejecting a proposal has to put the
   validator's own sentence into `tried` and route back to `propose` — that loop is the
   entire idea, and a graph that silently dropped a refusal would look identical from the
   outside while searching a quarter as hard.
2. **It respects `SearchBudget`.** A search that can overrun its budget is a search that
   can hang a request.
3. **It never returns a plan `dsl.validate` would reject.** The whole safety argument for
   putting a free-tier model in front of a simulator is that nothing it says reaches a
   core unchecked.

The fake extends the pattern in `dsl/test_llm.py`: an object with `complete_json`, patched
in where `resolve_adapter` would have built a real adapter.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
import xarray as xr

from terrarium.agent import nodes as agent_nodes
from terrarium.agent.baseline import BASELINE_SIMULATIONS
from terrarium.agent.graph import run_search
from terrarium.agent.state import Candidate, SearchBudget, SearchEvent, SearchResult
from terrarium.api.candidates import build_lattice
from terrarium.api.runtime import Runtime
from terrarium.dsl.llm import LLMUnavailable
from terrarium.dsl.validate import PlanError, resolve
from terrarium.state.cube import select_window


class ScriptedModel:
    """Answers each call with the next scripted reply, then repeats the last one.

    Repeating rather than raising at the end: the graph decides how many times to ask, and
    a test that ran out of script would fail with a `StopIteration` describing the fixture
    instead of the behaviour under test.
    """

    name = "scripted:test"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[str] = []

    def complete_json(self, *, system: str, user: str) -> str:
        self.calls.append(user)
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[index]


class DeadModel:
    name = "dead:test"

    def complete_json(self, **_: object) -> str:
        raise LLMUnavailable("no")


def _goal_reply(**overrides: object) -> str:
    return json.dumps({"metric": "person_degrees", **overrides})


def _report_reply(*lines: str) -> str:
    return json.dumps({"lines": list(lines)})


def _propose_reply(region_ids: list[str], plan: Mapping[str, object]) -> str:
    return json.dumps({"region_ids": region_ids, "plan": plan})


def _plant(
    count: int | None = None,
    fraction: float | None = None,
    name: str = "Test planting",
) -> dict[str, Any]:
    action = (
        {"kind": "plant_trees", "tree_count": count}
        if count is not None
        else {"kind": "plant_trees", "canopy_fraction_added": fraction}
    )
    return {"name": name, "actions": [action]}


# The window slice, its label, and the lattice built from it. A tuple rather than a
# dataclass because it is unpacked at every call site and named nowhere else.
Tile = tuple[xr.Dataset, str, tuple[Candidate, ...]]


@pytest.fixture
def tile(synthetic_runtime: Runtime) -> Tile:
    label = synthetic_runtime.default_window()
    window = select_window(synthetic_runtime.cube, label)
    return window, label, build_lattice(window, synthetic_runtime.grid)


def _search(
    synthetic_runtime: Runtime,
    tile: Tile,
    model: object,
    *,
    goal: str = "cool this tile",
    budget: SearchBudget | None = None,
) -> list[SearchEvent]:
    window, label, candidates = tile
    return list(
        run_search(
            runtime=synthetic_runtime,
            window=window,
            label=label,
            season="summer",
            candidates=candidates,
            goal=goal,
            settings=object(),
            budget=budget or SearchBudget(max_simulations=4, max_llm_calls=6, wall_clock_s=120.0),
            search_id="test",
        )
    )


def _patch(monkeypatch: pytest.MonkeyPatch, model: object) -> None:
    monkeypatch.setattr(agent_nodes, "resolve_adapter", lambda _settings, **_kw: model)


def _result(events: list[SearchEvent]) -> SearchResult | None:
    return events[-1].result


# --- 1. the refusal loop -------------------------------------------------------------


def test_a_refusal_cycles_back_to_propose_with_the_arithmetic(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An impossible tree count is refused before a core runs, and the search continues.

    900,000 trees need 22.5 km2 of crown; a 4 km2 block cannot hold it whatever its NDVI,
    so `resolve` refuses with the sum that produced the refusal. That sentence must reach
    `tried` verbatim — it is what the next proposal is conditioned on.
    """
    _, _, candidates = tile
    region = candidates[60].region_id

    model = ScriptedModel(
        [
            _goal_reply(),
            _propose_reply([region], _plant(count=900_000)),
            # Second proposal: a fraction, which is capped per cell rather than refused.
            _propose_reply([candidates[61].region_id], _plant(fraction=0.3)),
        ]
    )
    _patch(monkeypatch, model)

    events = _search(synthetic_runtime, tile, model)
    result = _result(events)
    assert result is not None

    refused = [attempt for attempt in result.tried if attempt.status == "refused"]
    assert refused, "the impossible plan should have been refused"
    assert refused[0].region_ids == (region,)
    assert "900,000 trees need" in (refused[0].reason or "")
    assert "still plantable" in (refused[0].reason or "")

    # It cycled: something was proposed and scored *after* the refusal.
    scored_after = [
        attempt
        for attempt in result.tried
        if attempt.status == "scored" and attempt.step > refused[0].step
    ]
    assert scored_after


def test_a_refusal_costs_no_simulation(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The economic argument for checking before running: a plan that cannot fit is
    rejected in microseconds rather than spending one of ten simulations."""
    _, _, candidates = tile
    model = ScriptedModel(
        [_goal_reply(), _propose_reply([candidates[60].region_id], _plant(count=5_000_000))]
    )
    _patch(monkeypatch, model)

    result = _result(_search(synthetic_runtime, tile, model))
    assert result is not None

    refused = [a for a in result.tried if a.status == "refused"]
    assert len(refused) >= 2, "the repeated impossible proposal should keep being refused"
    # The control's simulations and not one more. `greedy_best` records only its winner in
    # `tried` but spends `BASELINE_SIMULATIONS` runs, so the count to compare against is
    # the control's, not the length of the trace.
    assert result.simulations_used == BASELINE_SIMULATIONS
    assert result.stopped_because.startswith("the model-call budget ran out")


# --- 2. the budget -------------------------------------------------------------------


def test_the_simulation_budget_is_respected(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, candidates = tile
    model = ScriptedModel(
        [_goal_reply(), _propose_reply([candidates[61].region_id], _plant(fraction=0.3))]
    )
    _patch(monkeypatch, model)

    budget = SearchBudget(max_simulations=4, max_llm_calls=30, wall_clock_s=120.0)
    result = _result(_search(synthetic_runtime, tile, model, budget=budget))

    assert result is not None
    assert result.simulations_used <= budget.max_simulations
    assert "budget ran out" in result.stopped_because


def test_the_model_call_budget_is_respected(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, candidates = tile
    model = ScriptedModel(
        [_goal_reply(), _propose_reply([candidates[60].region_id], _plant(count=900_000))]
    )
    _patch(monkeypatch, model)

    budget = SearchBudget(max_simulations=20, max_llm_calls=5, wall_clock_s=120.0)
    result = _result(_search(synthetic_runtime, tile, model, budget=budget))

    assert result is not None
    assert result.llm_calls_used <= budget.max_llm_calls


def test_a_keyless_search_refuses_rather_than_sweeping_the_lattice(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no deterministic search behind this any more.

    The lattice sweep that used to fill in was a *different, worse procedure* returning
    the same `SearchResult` shape — so a keyless run reported a number the agent had not
    found, under field names that said it had. It raises now, and the route 503s before
    the stream even opens.
    """
    monkeypatch.setattr(agent_nodes, "resolve_adapter", lambda _settings, **_kw: None)

    events = _search(
        synthetic_runtime,
        tile,
        None,
        budget=SearchBudget(max_simulations=3, max_llm_calls=0, wall_clock_s=120.0),
    )

    # The failure arrives as an event rather than an exception out of the generator: the
    # stream may already be half-rendered, and a truncated SSE body with no reason is the
    # one thing a browser cannot report usefully.
    assert events[-1].node == "error"
    assert "needs a model" in events[-1].message
    assert _result(events) is None


def test_a_dead_model_stops_the_search_and_says_so(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, DeadModel())

    events = _search(synthetic_runtime, tile, DeadModel())
    assert events[-1].node == "error"
    assert "could not read the goal" in events[-1].message


# --- 3. nothing unchecked reaches a core ---------------------------------------------


def test_the_winning_plan_survives_the_validator(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-run `dsl.validate.resolve` on the reported winner. It must not raise.

    This is the safety claim stated as an assertion: whatever produced the plan — a model,
    a fallback, the greedy control — it went through the same validator, so re-validating
    it is a tautology *if the graph is correct* and a caught bug if it is not.
    """
    from terrarium.api.candidates import region_measurement

    _, _, candidates = tile
    by_id = {c.region_id: c for c in candidates}
    model = ScriptedModel(
        [_goal_reply(), _propose_reply([candidates[61].region_id], _plant(fraction=0.45))]
    )
    _patch(monkeypatch, model)

    result = _result(_search(synthetic_runtime, tile, model))
    assert result is not None and result.best is not None

    regions = [by_id[region] for region in result.best.region_ids]
    resolve(result.best.plan, region_measurement(regions))  # raises PlanError if invalid


def test_a_hallucinated_region_never_reaches_a_core(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D26's payoff. A model naming `r99c99` costs one fallback proposal, not a
    simulation over a polygon that selects no cells."""
    model = ScriptedModel(
        [_goal_reply(), _propose_reply(["r99c99", "the northern district"], _plant(fraction=0.3))]
    )
    _patch(monkeypatch, model)

    result = _result(_search(synthetic_runtime, tile, model))
    assert result is not None

    # It is recorded as a *refusal*, so the model sees it in `tried` and the next proposal
    # is conditioned on having got it wrong — the same feedback loop the validator drives.
    hallucinated = [a for a in result.tried if "r99c99" in a.region_ids]
    assert hallucinated and all(a.status == "refused" for a in hallucinated)
    assert "no such region" in (hallucinated[0].reason or "")
    # And it never reached a core: the control's simulations, and not one more.
    assert result.simulations_used == BASELINE_SIMULATIONS


def test_a_plan_the_model_could_not_form_is_dropped(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two plantings in one plan is a `Plan` validation error, not a sum. It falls back."""
    _, _, candidates = tile
    bad = {
        "name": "Two plantings",
        "actions": [
            {"kind": "plant_trees", "tree_count": 100},
            {"kind": "plant_trees", "tree_count": 200},
        ],
    }
    model = ScriptedModel([_goal_reply(), _propose_reply([candidates[61].region_id], bad)])
    _patch(monkeypatch, model)

    result = _result(_search(synthetic_runtime, tile, model))
    assert result is not None
    assert all(len(a.plan.actions) == 1 for a in result.tried)


# --- the control, and the trace ------------------------------------------------------


def test_the_result_always_carries_the_control_to_compare_against(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A search result with nothing to beat is a claim. `beat_baseline` has to be earned,
    including when the answer is `False`."""
    _, _, candidates = tile
    model = ScriptedModel(
        [_goal_reply(), _propose_reply([candidates[61].region_id], _plant(fraction=0.3))]
    )
    _patch(monkeypatch, model)

    result = _result(_search(synthetic_runtime, tile, model))
    assert result is not None
    assert result.baseline is not None
    assert result.baseline.proposer == "greedy"
    assert isinstance(result.beat_baseline, bool)


def test_events_stream_one_per_node_and_the_last_carries_the_result(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, candidates = tile
    model = ScriptedModel(
        [_goal_reply(), _propose_reply([candidates[61].region_id], _plant(fraction=0.3))]
    )
    _patch(monkeypatch, model)

    events = _search(synthetic_runtime, tile, model)

    assert events[0].node == "parse_goal"
    assert events[-1].node == "done"
    assert events[-1].result is not None
    assert all(event.result is None for event in events[:-1])
    assert {"survey", "propose", "check", "run", "score", "report"} <= {e.node for e in events}


def test_the_goal_is_taken_from_the_model_and_schema_checked(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The post-check is what survived the rule parser's removal: whatever the model
    returns is validated as an `Objective` or the search stops."""
    _, _, candidates = tile
    model = ScriptedModel(
        [
            _goal_reply(target_cooling_c=1.5, max_cost_usd=500_000.0),
            _propose_reply([candidates[61].region_id], _plant(fraction=0.3)),
        ]
    )
    _patch(monkeypatch, model)

    result = _result(_search(synthetic_runtime, tile, model))

    assert result is not None
    assert result.objective.target_cooling_c == 1.5
    assert result.objective.max_cost_usd == 500_000.0


def test_a_goal_the_model_mangles_stops_the_search(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A negative budget is not an `Objective`. Nothing substitutes a default."""
    _patch(monkeypatch, ScriptedModel([json.dumps({"metric": "vibes", "max_cost_usd": -5})]))

    events = _search(synthetic_runtime, tile, ScriptedModel([]))
    assert events[-1].node == "error"
    assert "could not read the goal" in events[-1].message


def test_an_over_budget_plan_never_becomes_the_answer(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, candidates = tile
    model = ScriptedModel(
        [
            _goal_reply(max_cost_usd=1.0),
            _propose_reply([candidates[61].region_id], _plant(fraction=0.3)),
        ]
    )
    _patch(monkeypatch, model)

    result = _result(_search(synthetic_runtime, tile, model))

    assert result is not None
    assert result.objective.max_cost_usd == 1.0
    # A $1 budget makes every plan infeasible, so `best` stays the control only if the
    # control itself is free — which it is not. Either nothing won, or what won was within
    # budget; there is no third outcome that would be acceptable.
    if result.best is not None and result.best.outcome is not None:
        assert result.best.outcome.cost_usd <= 1.0


def test_a_refusal_is_a_sentence_a_person_could_act_on(
    synthetic_runtime: Runtime, tile: Tile
) -> None:
    """Guards the shape of the feedback signal itself, without the graph in the way."""
    from terrarium.api.candidates import region_measurement
    from terrarium.dsl.schema import Plan, PlantTrees

    _, _, candidates = tile
    plantable = next(c for c in candidates if c.plantable_canopy_m2 > 0)

    with pytest.raises(PlanError) as excinfo:
        resolve(
            Plan(
                name="too many",
                actions=(PlantTrees(tree_count=plantable.max_trees + 1_000_000),),
            ),
            region_measurement([plantable]),
        )

    message = str(excinfo.value)
    assert "km2 of crown" in message and "still plantable" in message


# --- the report, which is now the model's or nobody's --------------------------------


def _search_to_report(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch, report: str
) -> SearchResult | None:
    """One scored proposal, then the report reply under test."""
    _, _, candidates = tile
    model = ScriptedModel(
        [
            _goal_reply(),
            _propose_reply([candidates[61].region_id], _plant(fraction=0.3)),
            report,
        ]
    )
    _patch(monkeypatch, model)
    return _result(
        _search(
            synthetic_runtime,
            tile,
            model,
            budget=SearchBudget(max_simulations=4, max_llm_calls=3, wall_clock_s=120.0),
        )
    )


class EchoingModel(ScriptedModel):
    """Scripted, except that the report call is answered with the facts it was handed.

    Echoing is the cheapest way to be *certain* a reply is faithful: every numeral in it
    came from the facts block by construction, and none is missing. So a rejection from
    this model would mean the guard is wrong, not the reply — which is what makes the
    acceptance test worth having.
    """

    def complete_json(self, *, system: str, user: str) -> str:
        if "caveat that must survive:" in user:
            self.calls.append(user)
            return json.dumps({"lines": user.split("\n")})
        return super().complete_json(system=system, user=user)


def test_a_faithful_report_is_accepted(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every figure came from the facts block, and the headline figures all survive."""
    _, _, candidates = tile
    model = EchoingModel(
        [
            _goal_reply(),
            _propose_reply([candidates[61].region_id], _plant(fraction=0.3)),
        ]
    )
    _patch(monkeypatch, model)
    result = _result(
        _search(
            synthetic_runtime,
            tile,
            model,
            budget=SearchBudget(max_simulations=4, max_llm_calls=3, wall_clock_s=120.0),
        )
    )

    assert result is not None
    assert result.report_source == "scripted:test"
    assert any("greedy control score:" in line for line in result.report)


def test_a_report_that_invents_a_figure_is_dropped_entirely(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No template to fall back to any more — so the prose goes and the numbers stay.

    That is the trade the removal makes explicit: a search that cannot be narrated is
    still a search that ran, because `best` came out of the cores rather than out of the
    sentence describing it.
    """
    result = _search_to_report(
        synthetic_runtime,
        tile,
        monkeypatch,
        _report_reply("The plan cools by a full 47.3 degC and costs $12."),
    )

    assert result is not None
    assert result.report == ()
    assert result.report_source == "unavailable"
    # The result itself is untouched.
    assert result.best is not None and result.best.outcome is not None


def test_a_report_that_drops_every_figure_is_also_dropped(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model told never to invent a number complies by writing none at all."""
    result = _search_to_report(
        synthetic_runtime,
        tile,
        monkeypatch,
        _report_reply("The search found a good place to plant some trees."),
    )

    assert result is not None
    assert result.report == ()


def test_the_facts_block_always_carries_the_control(
    synthetic_runtime: Runtime, tile: Tile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer cannot omit a loss it was never told about, so it is told.

    Reaching into the prompt rather than the output on purpose: what is asserted here is
    that the *input* makes losing unavoidable, which is stronger than checking that one
    scripted reply happened to mention it.
    """
    _, _, candidates = tile
    model = ScriptedModel(
        [
            _goal_reply(),
            _propose_reply([candidates[61].region_id], _plant(fraction=0.3)),
            _report_reply("anything"),
        ]
    )
    _patch(monkeypatch, model)
    _search(
        synthetic_runtime,
        tile,
        model,
        budget=SearchBudget(max_simulations=4, max_llm_calls=3, wall_clock_s=120.0),
    )

    facts = model.calls[-1]
    assert "greedy control score:" in facts
    assert "the agent" in facts
    assert "caveat that must survive:" in facts
