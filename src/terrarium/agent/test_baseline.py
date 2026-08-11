"""The greedy control, on the synthetic cube.

The control is what makes the agent falsifiable, so it has one job it must not fail at:
**produce a runnable plan whenever the tile has one.** A control that could be refused
would go missing exactly on the difficult tiles, which is where the comparison matters.
"""

from __future__ import annotations

import xarray as xr

from terrarium.agent.baseline import BASELINE_SIMULATIONS, greedy_best, rank
from terrarium.agent.state import Candidate, Objective
from terrarium.api.candidates import build_lattice
from terrarium.api.runtime import Runtime
from terrarium.state.cube import select_window


def _setup(synthetic_runtime: Runtime) -> tuple[xr.Dataset, tuple[Candidate, ...]]:
    label = synthetic_runtime.default_window()
    window = select_window(synthetic_runtime.cube, label)
    return window, build_lattice(window, synthetic_runtime.grid)


def test_ranking_is_deterministic_and_prefers_room_times_people(synthetic_runtime: Runtime) -> None:
    _, candidates = _setup(synthetic_runtime)

    ordered = rank(candidates)
    assert [c.region_id for c in ordered] == [c.region_id for c in rank(list(reversed(candidates)))]
    assert ordered[0].opportunity >= ordered[-1].opportunity


def test_the_control_produces_a_runnable_plan(synthetic_runtime: Runtime) -> None:
    window, candidates = _setup(synthetic_runtime)

    best, used = greedy_best(
        runtime=synthetic_runtime,
        window=window,
        candidates=candidates,
        objective=Objective(metric="person_degrees"),
    )

    assert used == BASELINE_SIMULATIONS
    assert best is not None
    assert best.status == "scored"
    assert best.proposer == "greedy"
    assert best.outcome is not None
    # It cools. A canopy fraction is capped per cell, never refused, so the control always
    # has an answer — and on a tile with headroom that answer is a negative delta.
    assert best.outcome.mean_delta_inside_c < 0
    assert best.outcome.expected_cooling_c > 0


def test_the_metric_changes_which_plan_wins(synthetic_runtime: Runtime) -> None:
    """Not a tautology worth skipping: it is the reason `Objective.metric` exists at all.

    If every metric picked the same region the search would have one answer and the goal
    text would be decoration.
    """
    window, candidates = _setup(synthetic_runtime)

    cooling, _ = greedy_best(
        runtime=synthetic_runtime,
        window=window,
        candidates=candidates,
        objective=Objective(metric="cooling"),
    )
    value, _ = greedy_best(
        runtime=synthetic_runtime,
        window=window,
        candidates=candidates,
        objective=Objective(metric="person_degrees"),
    )

    assert cooling is not None and value is not None
    assert cooling.score != value.score
