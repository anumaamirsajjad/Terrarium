"""The mapping from a published measure to a runnable plan, and the miss rate.

Pure arithmetic, no PDF and no model. The assertion that matters most is the *negative*
one: a fuel standard maps to nothing, and this module has to say so rather than finding
some way to express it.
"""

from __future__ import annotations

from typing import Any

from terrarium.policy.schema import Coverage, PolicyMeasure
from terrarium.policy.to_plan import DEFAULT_CANOPY_FRACTION, expressible, to_plan


def measure(**overrides: Any) -> PolicyMeasure:
    base = {
        "title": "Low emission zone in the central district",
        "sector": "transport",
        "target": "36 percent of vehicle emissions",
        "target_year": 2030,
        "source_page": 12,
        "quote": "Establish a low emission zone removing 36 percent of vehicle emissions.",
        "document": "punjab-clean-air-action-plan",
        "document_sha256": "0" * 64,
    }
    return PolicyMeasure(**{**base, **overrides})


def test_the_number_this_whole_phase_is_for() -> None:
    """Diesel 28 % + two-stroke 8 % = 36 %, from the Lahore source apportionment.

    An `emission_fraction_removed` with a government citation behind it, which is a
    materially better provenance than the literature figures in `dsl/library.py`.
    """
    mapped = to_plan(measure())

    assert mapped is not None
    restriction = mapped.plan.restriction
    assert restriction is not None
    assert restriction.emission_fraction_removed == 0.36
    assert "punjab-clean-air-action-plan" in mapped.basis
    assert "p. 12" in mapped.basis
    assert not mapped.assumed


def test_a_canopy_target_becomes_a_planting() -> None:
    mapped = to_plan(
        measure(
            title="Increase urban tree canopy cover",
            sector="urban_greening",
            target="25 percent by 2030",
        )
    )

    assert mapped is not None
    planting = mapped.plan.planting
    assert planting is not None
    assert planting.canopy_fraction_added == 0.25
    assert not mapped.assumed


def test_a_planting_with_no_figure_is_expressed_and_flagged_as_assumed() -> None:
    """Refusing this would drop the entire urban-greening chapter of most of these
    documents. Expressing it silently would attribute this project's default to the
    government — so it is expressed, and `assumed` says whose number it is."""
    mapped = to_plan(
        measure(
            title="Undertake avenue plantation along arterial roads",
            sector="urban_greening",
            target="",
        )
    )

    assert mapped is not None
    assert mapped.assumed
    assert mapped.plan.planting is not None
    assert mapped.plan.planting.canopy_fraction_added == DEFAULT_CANOPY_FRACTION
    assert "this project's street-tree default" in mapped.basis
    assert "assumed" in mapped.plan.name


def test_a_traffic_measure_with_no_figure_is_NOT_given_a_default() -> None:
    """The asymmetry is deliberate. For planting, "some trees" has a defensible default —
    a street retrofit. For traffic, the whole range from 1 % to 100 % is plausible, and
    picking one would be inventing the document's ambition rather than reading it."""
    assert to_plan(measure(title="Strengthen vehicle inspection enforcement", target="")) is None


def test_a_fuel_standard_maps_to_nothing() -> None:
    """The negative assertion this module exists for. A canopy fraction and an emission
    fraction cannot say anything about sulfur content, and pretending otherwise is how a
    coverage report becomes a lie."""
    assert (
        to_plan(
            measure(
                title="Reduce sulfur content in diesel fuel",
                sector="industry",
                target="10 parts per million",
            )
        )
        is None
    )


def test_a_catalytic_converter_mandate_maps_to_nothing() -> None:
    assert (
        to_plan(
            measure(
                title="Mandate catalytic converters on newly registered vehicles",
                target="",
            )
        )
        is None
    )


def test_an_over_100_percent_target_is_clamped_not_rejected() -> None:
    """A transcription error is better capped than allowed to fail validation."""
    mapped = to_plan(measure(target="350 percent of vehicle emissions"))
    assert mapped is not None
    assert mapped.plan.restriction is not None
    assert mapped.plan.restriction.emission_fraction_removed == 1.0


def test_a_long_policy_title_still_makes_a_valid_plan() -> None:
    """`Plan.name` is capped at 80 characters and a policy title routinely is not."""
    long_title = "Establish and operationalise " + "a comprehensive " * 8 + "low emission zone"
    mapped = to_plan(measure(title=long_title))

    assert mapped is not None
    assert len(mapped.plan.name) <= 80


def test_expressible_agrees_with_to_plan() -> None:
    assert expressible(measure())
    assert not expressible(measure(title="Reduce sulfur content in fuel", target="10 ppm"))


def test_the_coverage_summary_reads_as_a_finding_not_a_failure() -> None:
    coverage = Coverage(document="punjab", extracted=20, grounded=18, expressible=5)
    summary = coverage.summary

    assert "20 measures extracted" in summary
    assert "5 expressible on this tile" in summary
    assert "13 are real commitments this model has no lever for" in summary
    assert "not measures that failed to extract" in summary
