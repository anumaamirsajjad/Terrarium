"""The grammar's own rules: what a plan may and may not say."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from terrarium.config import Season
from terrarium.dsl.schema import Plan, PlantTrees, RestrictVehicles


def test_a_planting_takes_one_unit_or_the_other() -> None:
    assert PlantTrees(tree_count=5_000).tree_count == 5_000
    assert PlantTrees(canopy_fraction_added=0.3).canopy_fraction_added == 0.3


def test_both_units_at_once_is_refused() -> None:
    # They are two spellings of one quantity and there is no rule for reconciling them,
    # so accepting both would mean silently picking a winner.
    with pytest.raises(ValidationError, match="not both"):
        PlantTrees(tree_count=100, canopy_fraction_added=0.3)


def test_neither_unit_is_refused() -> None:
    with pytest.raises(ValidationError, match="not neither"):
        PlantTrees()


def test_a_planting_of_zero_trees_is_not_a_plan() -> None:
    with pytest.raises(ValidationError):
        PlantTrees(tree_count=0)


def test_a_misspelled_field_is_refused_rather_than_silently_dropped() -> None:
    """F18: `Plan` is the model an LLM emits, so it needs `extra="forbid"` the most - a
    typo'd `tree_counts` used to parse as "no trees specified" (both units omitted) rather
    than as the actually-provided count nobody read."""
    with pytest.raises(ValidationError, match="tree_counts"):
        PlantTrees(tree_counts=5_000)  # type: ignore[call-arg]


def test_plan_itself_refuses_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Plan(name="x", actions=[PlantTrees(tree_count=100)], cost=5)  # type: ignore[call-arg]


def test_emission_removal_must_be_a_fraction() -> None:
    assert RestrictVehicles(emission_fraction_removed=1.0).emission_fraction_removed == 1.0
    with pytest.raises(ValidationError):
        RestrictVehicles(emission_fraction_removed=1.5)
    # Zero is not "a restriction of nothing", it is the absence of the action. Leaving the
    # action out is how a plan says nothing about traffic (D14).
    with pytest.raises(ValidationError):
        RestrictVehicles(emission_fraction_removed=0.0)


def test_a_plan_needs_at_least_one_action() -> None:
    with pytest.raises(ValidationError):
        Plan(name="Nothing", actions=())


def test_two_actions_of_the_same_kind_are_a_contradiction() -> None:
    with pytest.raises(ValidationError, match="Say it once"):
        Plan(
            name="Twice",
            actions=(PlantTrees(tree_count=10), PlantTrees(canopy_fraction_added=0.2)),
        )


def test_actions_are_reachable_by_kind() -> None:
    plan = Plan(
        name="Both",
        actions=(PlantTrees(tree_count=10), RestrictVehicles(emission_fraction_removed=0.5)),
    )
    assert plan.planting is not None
    assert plan.restriction is not None

    trees_only = Plan(name="Trees", actions=(PlantTrees(tree_count=10),))
    assert trees_only.restriction is None


def test_actions_round_trip_through_json() -> None:
    # The discriminated union is what lets an LLM's JSON become a typed action without a
    # hand-written dispatch, so it is worth a test of its own.
    plan = Plan(
        name="Both",
        actions=(PlantTrees(tree_count=10), RestrictVehicles(emission_fraction_removed=0.5)),
        window="2024-winter",
    )
    restored = Plan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert isinstance(restored.actions[0], PlantTrees)


def test_a_season_is_accepted_without_a_year() -> None:
    assert Plan(name="Winter", actions=(PlantTrees(tree_count=1),), season=Season.WINTER).season
