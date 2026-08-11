"""Pure scoring. No cube, no cores, no model — which is the point of `objective.py`
being its own module: the search's *ranking* is testable without any of them."""

from __future__ import annotations

from typing import Any, Literal

import pytest

from terrarium.agent.objective import INFEASIBLE, better, satisfies, score, units_for
from terrarium.agent.state import Attempt, Metric, Objective, Outcome
from terrarium.dsl.schema import Plan, PlantTrees


def outcome(**overrides: Any) -> Outcome:
    base = {
        "mean_delta_inside_c": -1.0,
        "expected_cooling_c": 0.4,
        "person_degrees": 5_000.0,
        "people_reached": 12_000.0,
        "cost_usd": 100_000.0,
        "tree_count": 6_666,
        "area_km2": 4.0,
    }
    return Outcome(**{**base, **overrides})


def attempt(
    score_value: float | None, *, status: Literal["scored", "refused"] = "scored"
) -> Attempt:
    return Attempt(
        step=1,
        region_ids=("r00c00",),
        plan=Plan(name="x", actions=(PlantTrees(canopy_fraction_added=0.3),)),
        status=status,
        score=score_value,
        outcome=outcome() if status == "scored" else None,
    )


def test_each_metric_reads_its_own_field() -> None:
    assert score(Objective(metric="cooling"), outcome()) == 0.4
    assert score(Objective(metric="person_degrees"), outcome()) == 5_000.0
    assert score(Objective(metric="cost_effectiveness"), outcome()) == pytest.approx(0.05)


def test_a_budget_is_enforced_not_traded_off() -> None:
    """The rule this module exists for. A plan over budget cannot buy its way back by
    cooling harder — it scores `-inf` and can never become `best`."""
    objective = Objective(metric="cooling", max_cost_usd=50_000.0)

    assert score(objective, outcome(expected_cooling_c=99.0)) == INFEASIBLE
    assert score(objective, outcome(cost_usd=49_000.0)) == 0.4


def test_a_free_plan_scores_zero_not_infinity() -> None:
    """Zero cost means nothing was planted, not infinite value per dollar."""
    assert score(Objective(metric="cost_effectiveness"), outcome(cost_usd=0.0)) == 0.0


def test_the_target_is_checked_against_the_corrected_figure() -> None:
    """`expected_cooling_c` is already divided by the hindcast factor of 2.5.

    A raw model delta of -1.0 degC is 0.4 degC expected, so a 1 degC target is *not* met —
    checking `mean_delta_inside_c` instead would report success on the one figure the rest
    of the project always corrects before quoting.
    """
    objective = Objective(target_cooling_c=1.0)

    assert not satisfies(objective, outcome(mean_delta_inside_c=-1.0, expected_cooling_c=0.4))
    assert satisfies(objective, outcome(mean_delta_inside_c=-2.6, expected_cooling_c=1.04))


def test_a_target_met_over_budget_is_not_satisfied() -> None:
    objective = Objective(target_cooling_c=0.3, max_cost_usd=50_000.0)
    assert not satisfies(objective, outcome(expected_cooling_c=0.4, cost_usd=100_000.0))


def test_no_target_never_satisfies_early() -> None:
    """'Do the best you can' ends on the budget, having tried everything it could afford."""
    assert not satisfies(Objective(), outcome(expected_cooling_c=99.0))


def test_a_refused_attempt_never_becomes_best_even_against_nothing() -> None:
    """Without this the first refusal becomes `best` and the search reports a plan the
    validator rejected as its answer."""
    assert not better(attempt(None, status="refused"), None)
    assert not better(attempt(INFEASIBLE), None)
    assert better(attempt(1.0), None)
    assert better(attempt(2.0), attempt(1.0))
    assert not better(attempt(1.0), attempt(1.0))


def test_every_metric_has_units() -> None:
    metrics: list[Metric] = ["cooling", "person_degrees", "cost_effectiveness"]
    for metric in metrics:
        assert units_for(Objective(metric=metric))
