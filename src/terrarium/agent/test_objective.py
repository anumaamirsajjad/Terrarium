"""Pure scoring. No cube, no cores, no model — which is the point of `objective.py`
being its own module: the search's *ranking* is testable without any of them."""

from __future__ import annotations

from typing import Any, Literal

from terrarium.agent.objective import better, satisfies, score, units_for
from terrarium.agent.state import Attempt, Metric, Objective, Outcome
from terrarium.dsl.schema import Plan, PlantTrees


def outcome(**overrides: Any) -> Outcome:
    base = {
        "mean_delta_inside_c": -1.0,
        "expected_cooling_c": 0.4,
        "person_degrees": 5_000.0,
        "people_reached": 12_000.0,
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


def test_pm25_reduction_scores_the_improvement_positive() -> None:
    """`delta_pm25` is negative when air improved — the score flips the sign so a bigger
    cut is still a bigger number, matching every other metric's higher-is-better contract."""
    objective = Objective(metric="pm25_reduction")
    assert score(objective, outcome(delta_pm25=-4.03)) == 4.03


def test_a_plan_that_touched_no_emissions_scores_zero_on_pm25_reduction() -> None:
    """A `plant_trees`-only outcome carries `delta_pm25=None`. It has to score something
    comparable rather than raising, so a tree-only proposal simply never wins an air search
    instead of crashing one."""
    objective = Objective(metric="pm25_reduction")
    assert score(objective, outcome(delta_pm25=None)) == 0.0


def test_the_target_is_checked_against_the_corrected_figure() -> None:
    """`expected_cooling_c` is already divided by the hindcast factor of 2.5.

    A raw model delta of -1.0 degC is 0.4 degC expected, so a 1 degC target is *not* met —
    checking `mean_delta_inside_c` instead would report success on the one figure the rest
    of the project always corrects before quoting.
    """
    objective = Objective(target_cooling_c=1.0)

    assert not satisfies(objective, outcome(mean_delta_inside_c=-1.0, expected_cooling_c=0.4))
    assert satisfies(objective, outcome(mean_delta_inside_c=-2.6, expected_cooling_c=1.04))


def test_no_target_never_satisfies_early() -> None:
    """'Do the best you can' ends on the budget, having tried everything it could afford."""
    assert not satisfies(Objective(), outcome(expected_cooling_c=99.0))


def test_a_refused_attempt_never_becomes_best_even_against_nothing() -> None:
    """Without this the first refusal becomes `best` and the search reports a plan the
    validator rejected as its answer."""
    assert not better(attempt(None, status="refused"), None)
    assert better(attempt(1.0), None)
    assert better(attempt(2.0), attempt(1.0))
    assert not better(attempt(1.0), attempt(1.0))


def test_every_metric_has_units() -> None:
    metrics: list[Metric] = ["cooling", "person_degrees", "pm25_reduction"]
    for metric in metrics:
        assert units_for(Objective(metric=metric))
