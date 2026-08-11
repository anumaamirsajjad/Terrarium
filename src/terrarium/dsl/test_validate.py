"""Whether a plan fits the polygon it was aimed at.

The refusals matter more than the acceptances here: a plan that is silently trimmed comes
back as a small delta, which is indistinguishable from a plan that simply worked badly.
"""

from __future__ import annotations

import pytest

from terrarium.dsl.schema import TREE_CANOPY_M2, Plan, PlantTrees, RestrictVehicles
from terrarium.dsl.validate import PlanError, PolygonMeasurement, resolve

# A 1 km2 polygon (100 cells of 100 m) with a fifth of its area still plantable.
ROOMY = PolygonMeasurement(cells=100, area_m2=1_000_000.0, plantable_canopy_m2=200_000.0)
# 0.3 km2, and mostly built: the roadmap's own worked refusal.
TIGHT = PolygonMeasurement(cells=30, area_m2=300_000.0, plantable_canopy_m2=30_000.0)


def _trees(count: int) -> Plan:
    return Plan(name="Planting", actions=(PlantTrees(tree_count=count),))


def _canopy(fraction: float) -> Plan:
    return Plan(name="Planting", actions=(PlantTrees(canopy_fraction_added=fraction),))


def test_a_tree_count_becomes_a_canopy_fraction() -> None:
    resolved = resolve(_trees(4_000), ROOMY)
    # 4,000 x 25 m2 = 100,000 m2 of crown over 1,000,000 m2 of polygon.
    assert resolved.canopy_fraction_added == pytest.approx(0.10)
    assert resolved.tree_count == 4_000


def test_too_many_trees_for_the_polygon_is_refused_with_the_arithmetic() -> None:
    # 5,000 trees need 0.125 km2 of crown; this polygon has 0.030 km2 left. This is the
    # refusal the roadmap named, and the message has to carry the numbers - "no" alone
    # gives the user nothing to argue with.
    with pytest.raises(PlanError) as exc:
        resolve(_trees(5_000), TIGHT)

    message = str(exc.value)
    assert "5,000 trees" in message
    assert "0.125 km2" in message
    assert "0.030 km2" in message
    assert f"{TIGHT.max_trees:,}" in message


def test_exactly_the_available_canopy_is_allowed() -> None:
    # The boundary is inclusive: a plan that uses every plantable metre is tight, not
    # impossible, and rounding it into a refusal would be arbitrary.
    fitting = int(TIGHT.plantable_canopy_m2 // TREE_CANOPY_M2)
    assert resolve(_trees(fitting), TIGHT).tree_count == fitting


def test_a_canopy_fraction_over_the_headroom_warns_instead_of_refusing() -> None:
    # The asymmetry is deliberate and is the two units' existing contracts: a fraction is
    # documented as a ceiling the core caps per cell, a tree count is a quantity that has
    # to physically fit.
    resolved = resolve(_canopy(0.40), TIGHT)
    assert resolved.canopy_fraction_added == 0.40
    assert resolved.canopy_utilisation == pytest.approx(4.0)
    assert any("more than this polygon can take" in note for note in resolved.notes)


def test_a_canopy_fraction_within_the_headroom_has_no_capping_note() -> None:
    resolved = resolve(_canopy(0.10), ROOMY)
    assert resolved.canopy_utilisation == pytest.approx(0.5)
    assert not any("more than this polygon can take" in note for note in resolved.notes)


def test_a_fraction_has_its_equivalent_tree_count() -> None:
    resolved = resolve(_canopy(0.10), ROOMY)
    assert resolved.tree_count == 4_000


def test_an_unplantable_polygon_refuses_a_planting() -> None:
    water = PolygonMeasurement(cells=50, area_m2=500_000.0, plantable_canopy_m2=0.0)
    with pytest.raises(PlanError, match="nothing in this polygon can be planted"):
        resolve(_trees(10), water)


def test_a_restriction_plants_nothing() -> None:
    plan = Plan(name="LEZ", actions=(RestrictVehicles(emission_fraction_removed=1.0),))
    resolved = resolve(plan, ROOMY)

    assert resolved.canopy_fraction_added == 0.0
    assert resolved.tree_count == 0
    assert any("no temperature change" in note for note in resolved.notes)


def test_a_planting_says_it_will_not_move_the_air() -> None:
    resolved = resolve(_trees(10), ROOMY)
    assert any("thermal instrument" in note for note in resolved.notes)
    # F19: the naming rule applies here too - "air quality" unqualified is never the
    # phrase, because this project's air core is not what a monitor reads.
    joined = " ".join(resolved.notes)
    assert "locally-generated PM2.5" in joined
    assert "air quality" not in joined


def test_a_combined_plan_carries_both_levers_and_neither_note() -> None:
    plan = Plan(
        name="Both",
        actions=(
            PlantTrees(canopy_fraction_added=0.10),
            RestrictVehicles(emission_fraction_removed=0.5),
        ),
    )
    resolved = resolve(plan, ROOMY)

    assert resolved.canopy_fraction_added == 0.10
    assert resolved.emission_fraction_removed == 0.5
    assert resolved.notes == ()


def test_the_measurement_reports_what_the_polygon_can_hold() -> None:
    assert ROOMY.max_canopy_fraction == pytest.approx(0.2)
    assert ROOMY.max_trees == 8_000
    assert ROOMY.area_km2 == pytest.approx(1.0)
