"""Presets.

The preset library is the fallback path the roadmap names, so its job is to be *runnable*:
every entry must be a plan the validator accepts and the API can turn into a `/simulate`
body without a model, a key or a network.
"""

from __future__ import annotations

import pytest

from terrarium.dsl.library import PRESETS, preset, trees_for_canopy
from terrarium.dsl.schema import TREE_CANOPY_M2
from terrarium.dsl.validate import PolygonMeasurement, resolve

ROOMY = PolygonMeasurement(cells=100, area_m2=1_000_000.0, plantable_canopy_m2=500_000.0)


def test_every_preset_resolves_against_a_plausible_polygon() -> None:
    for entry in PRESETS:
        resolved = resolve(entry.plan, ROOMY)
        assert resolved.canopy_fraction_added > 0 or resolved.emission_fraction_removed > 0


def test_slugs_are_unique() -> None:
    slugs = [entry.slug for entry in PRESETS]
    assert len(set(slugs)) == len(slugs)


def test_every_preset_states_what_it_does_not_do() -> None:
    # A caveat per button. The preset is the demo path, so it is where an overclaim would
    # be most visible and least examined.
    for entry in PRESETS:
        assert entry.caveat.strip()
        assert entry.summary.strip()


def test_the_winter_preset_carries_its_season() -> None:
    # Without it the same restriction would be scored in summer and read 6-9x weaker.
    assert str(preset("winter-inversion").plan.season) == "winter"


def test_an_unknown_slug_names_the_ones_that_exist() -> None:
    with pytest.raises(KeyError, match="street-trees"):
        preset("plant-everything")


def test_canopy_converts_back_to_a_tree_count() -> None:
    assert trees_for_canopy(0.5, 1_000_000.0) == int(0.5 * 1_000_000.0 / TREE_CANOPY_M2)
    assert trees_for_canopy(0.0, 1_000_000.0) == 0
