"""The brief: what it must always say, whatever the numbers were.

Most of these assert on the *presence* of a caveat rather than on prose. That is the point
of a deterministic explainer — the caveats are structural, so they can be tested, and a
generative one could drop any of them on any given call without failing anything.
"""

from __future__ import annotations

import pytest

from terrarium.dsl.explain import (
    HINDCAST_OVERPREDICTION,
    AirInputs,
    BriefInputs,
    EquityInputs,
    brief_for,
)

PLANTING = BriefInputs(
    plan_name="Street trees",
    window="2024-summer",
    season="summer",
    area_km2=4.2,
    tree_count=25_000,
    cost_total_usd=375_000.0,
    mean_delta_inside=-0.51,
    mean_delta_spillover=-0.12,
    spillover_cells=980,
    min_delta=-1.44,
    tree_built_contrast_c=2.60,
    mean_canopy_added=0.18,
    ratio_to_linear=0.72,
)


def test_the_headline_carries_the_corrected_figure_not_the_raw_one() -> None:
    brief = brief_for(PLANTING)
    assert brief.expected_cooling_c == pytest.approx(-0.51 / HINDCAST_OVERPREDICTION)
    assert "0.20 degC" in brief.headline  # 0.51 / 2.5
    assert "2024-summer" in brief.headline


def test_the_raw_modelled_figure_is_still_shown_in_the_uncertainties() -> None:
    # A correction that hides the number it corrected is not a correction, it is a second
    # unexplained figure.
    assert any("-0.51" in line for line in brief_for(PLANTING).uncertainties)


def test_every_brief_names_its_window_and_the_hindcast_correction() -> None:
    brief = brief_for(PLANTING)
    joined = " ".join(brief.uncertainties)
    assert "2024-summer" in joined
    assert "2.5x" in joined
    assert "land surface" in joined


def test_uncertainties_are_never_empty() -> None:
    barren = PLANTING.model_copy(update={"mean_canopy_added": 0.0, "mean_delta_inside": 0.0})
    assert brief_for(barren).uncertainties


def test_confidence_is_never_high() -> None:
    # Nothing in this project has earned the word: the thermal core is hindcast-corrected
    # and the air core has never been calibrated.
    assert brief_for(PLANTING).confidence == "moderate"


def test_the_ceiling_is_quoted_beside_the_cooling() -> None:
    assert any("2.60 degC" in finding for finding in brief_for(PLANTING).findings)


def test_a_winter_planting_explains_why_the_ratio_is_missing() -> None:
    winter = PLANTING.model_copy(
        update={
            "window": "2024-winter",
            "season": "winter",
            "tree_built_contrast_c": 0.42,
            "ratio_to_linear": None,
            "mean_delta_inside": -0.13,
        }
    )
    joined = " ".join(brief_for(winter).uncertainties)
    assert "0.42 degC" in joined
    assert "withheld" in joined


def test_an_air_result_still_carries_the_brick_kiln_gap_and_low_summer_confidence() -> None:
    """The uncalibrated/unvalidated prose was removed on request; `confidence` was not.

    The underlying facts (literature emission factors, no summer validation) did not
    change, only whether the brief spells them out - so a summer air figure still reads
    'low', it is just no longer told why in words.
    """
    with_air = PLANTING.model_copy(
        update={
            "air": AirInputs(
                mean_delta_inside=-0.91,
                mean_delta_spillover=-0.36,
                spillover_cells=980,
                units="ug/m3",
                mixing_height_m=250.0,
                emission_fraction_removed=1.0,
            )
        }
    )
    brief = brief_for(with_air)
    joined = " ".join(brief.uncertainties)

    assert "local increment" not in joined
    assert "unvalidated" not in joined
    assert brief.confidence == "low"
    # The missing source category is structural and belongs on every air figure.
    assert "no brick kilns" in joined


def test_a_winter_air_result_is_moderate_confidence_with_no_validation_prose() -> None:
    winter_air = PLANTING.model_copy(
        update={
            "window": "2025-winter",
            "season": "winter",
            "air": AirInputs(
                mean_delta_inside=-3.18,
                mean_delta_spillover=-1.20,
                spillover_cells=980,
                units="ug/m3",
                mixing_height_m=250.0,
                emission_fraction_removed=1.0,
            ),
        }
    )
    brief = brief_for(winter_air)
    joined = " ".join(brief.uncertainties)

    assert "53 OpenAQ monitors" not in joined
    assert brief.confidence == "moderate"


def test_a_restriction_only_plan_leads_with_the_air_number() -> None:
    lez = BriefInputs(
        plan_name="Low-emission zone",
        window="2024-winter",
        season="winter",
        area_km2=4.0,
        mean_delta_inside=0.0,
        mean_delta_spillover=0.0,
        spillover_cells=0,
        min_delta=0.0,
        tree_built_contrast_c=0.42,
        mean_canopy_added=0.0,
        air=AirInputs(
            mean_delta_inside=-0.91,
            mean_delta_spillover=-0.36,
            spillover_cells=980,
            units="ug/m3",
            mixing_height_m=250.0,
            emission_fraction_removed=1.0,
        ),
    )
    brief = brief_for(lez)
    assert "PM2.5" in brief.headline
    assert "changes no temperature" in brief.headline


def test_a_plan_that_quotes_no_temperature_carries_no_temperature_caveats() -> None:
    # A caveat attaches to a figure, not to a plan. Shipping the hindcast correction with a
    # traffic-only plan describes a number nobody was given, and noise is how a real caveat
    # stops being read.
    lez = BriefInputs(
        plan_name="Low-emission zone",
        window="2024-winter",
        season="winter",
        area_km2=4.0,
        mean_delta_inside=0.0,
        mean_delta_spillover=0.0,
        spillover_cells=0,
        min_delta=0.0,
        tree_built_contrast_c=0.42,
        mean_canopy_added=0.0,
        emission_fraction_requested=1.0,
        equity=EquityInputs(
            top_three_share=20.1,
            densest_decile_share=-4.0,
            concentrated=False,
            shares_reliable=False,
            uninhabited_fraction=0.1,
        ),
        air=AirInputs(
            mean_delta_inside=-0.91,
            mean_delta_spillover=-0.36,
            spillover_cells=980,
            units="ug/m3",
            mixing_height_m=250.0,
            emission_fraction_removed=1.0,
        ),
    )
    brief = brief_for(lez)
    joined = " ".join(brief.uncertainties)

    assert "2.5x" not in joined
    assert "land surface" not in joined
    # The window still matters - more here than anywhere, because of the inversion.
    assert "2024-winter" in joined
    assert "6-9x" in joined
    # And the equity shares, which divide by a zero temperature delta, are not mentioned
    # at all rather than reported as unreliable.
    assert not any("equity split" in finding for finding in brief.findings)


def test_a_cube_that_cannot_answer_the_air_question_says_so() -> None:
    # `air is None` means two opposite things - "the plan does not touch traffic" and "it
    # does, and this cube has no inventory". Only the second is a missing layer, and
    # reporting it as "no effect" would be reporting an absent measurement as a result.
    unanswerable = BriefInputs(
        plan_name="Low-emission zone",
        window="2024-summer",
        season="summer",
        area_km2=6.4,
        mean_delta_inside=0.0,
        mean_delta_spillover=0.0,
        spillover_cells=0,
        min_delta=0.0,
        tree_built_contrast_c=2.6,
        mean_canopy_added=0.0,
        emission_fraction_requested=1.0,
    )
    brief = brief_for(unanswerable)

    assert "could not be modelled" in brief.headline
    assert any("missing layer, not a modelled finding" in f for f in brief.findings)
    assert brief.confidence == "low"


def test_equity_is_reported_with_the_even_share_beside_it() -> None:
    fair = PLANTING.model_copy(
        update={
            "equity": EquityInputs(
                top_three_share=0.62,
                densest_decile_share=0.02,
                concentrated=True,
                shares_reliable=True,
                uninhabited_fraction=0.4,
            )
        }
    )
    findings = " ".join(brief_for(fair).findings)
    assert "62%" in findings
    assert "even split is 30 %" in findings
    assert "nobody lives" in findings
    # And the stratifier is named, because density is not deprivation.
    assert any("not deprivation" in line for line in brief_for(fair).uncertainties)


def test_unreliable_equity_shares_are_described_rather_than_drawn() -> None:
    unreliable = PLANTING.model_copy(
        update={
            "equity": EquityInputs(
                top_three_share=20.1,
                densest_decile_share=-4.0,
                concentrated=False,
                shares_reliable=False,
                uninhabited_fraction=0.1,
            )
        }
    )
    findings = " ".join(brief_for(unreliable).findings)
    assert "withheld" in findings
    assert "20.1" not in findings


def test_plan_notes_survive_into_the_findings() -> None:
    noted = PLANTING.model_copy(update={"plan_notes": ("the polygon can only take 9 %",)})
    assert "the polygon can only take 9 %" in brief_for(noted).findings


def test_the_brief_is_deterministic() -> None:
    assert brief_for(PLANTING) == brief_for(PLANTING)


def test_the_headline_says_who_receives_the_cooling() -> None:
    """Equity is the only figure here carrying no correction and no seasonal caveat.

    The thermal number is divided by a hindcast factor and the air number is validated in
    one season and not the other. Distribution is plain arithmetic over a population
    raster, and it is the question a council actually has to answer — so it belongs in the
    first sentence rather than three paragraphs down.
    """
    reliable = EquityInputs(
        top_three_share=41.0,
        densest_decile_share=0.18,
        concentrated=False,
        shares_reliable=True,
        uninhabited_fraction=0.05,
    )
    brief = brief_for(PLANTING.model_copy(update={"equity": reliable}))

    assert "densest tenth" in brief.headline
    assert "18%" in brief.headline


def test_an_unreliable_equity_share_stays_out_of_the_headline() -> None:
    """A share over a mostly-uninhabited polygon is arithmetic, not a finding."""
    unreliable = EquityInputs(
        top_three_share=41.0,
        densest_decile_share=0.18,
        concentrated=False,
        shares_reliable=False,
        uninhabited_fraction=0.9,
    )

    headline = brief_for(PLANTING.model_copy(update={"equity": unreliable})).headline
    assert "densest tenth" not in headline
    # And a plan with no equity at all must not grow a sentence about it.
    assert "densest tenth" not in brief_for(PLANTING).headline


# --- the plain summary's verdict, and the case it used to get wrong -------------------


def test_the_verdict_is_measured_against_the_tile_not_against_a_fixed_degree() -> None:
    """The whole reason the verdict is a share and not a threshold in degrees.

    The identical cooling is a big deal under a 2.6 degC ceiling and unremarkable under an
    8 degC one, so a fixed cut-off would have to be wrong in one of the two. If this ever
    fails because someone swapped the share for an absolute number, that is the bug.
    """
    strong = PLANTING.model_copy(update={"mean_delta_inside": -2.6})

    wider = strong.model_copy(update={"tree_built_contrast_c": 8.0})

    assert brief_for(strong).plain.verdict == "large"
    assert brief_for(wider).plain.verdict == "small"


def test_winter_can_never_report_a_large_change() -> None:
    """Winter's ceiling is a few tenths of a degree; nothing large is available in it."""
    winter = PLANTING.model_copy(
        update={
            "window": "2024-winter",
            "season": "winter",
            "tree_built_contrast_c": 0.55,
            "mean_delta_inside": -3.0,
        }
    )

    assert brief_for(winter).plain.verdict in {"small", "marginal"}


def test_a_traffic_plan_is_not_told_that_planting_had_no_room() -> None:
    """The failure this branch exists for.

    A low-emission zone changes no temperature, which used to fall into the 'nothing
    happened' block and answer a question nobody asked - so the one screen most people read
    reported a working plan as a dud.
    """
    traffic = PLANTING.model_copy(
        update={
            "tree_count": 0,
            "mean_canopy_added": 0.0,
            "mean_delta_inside": 0.0,
            "min_delta": 0.0,
            "emission_fraction_requested": 0.6,
            "air": AirInputs(
                mean_delta_inside=-4.2,
                mean_delta_spillover=-0.9,
                spillover_cells=1200,
                units="ug/m3",
                mixing_height_m=250.0,
                emission_fraction_removed=0.6,
            ),
        }
    )
    plain = brief_for(traffic).plain

    assert "planting" not in plain.headline.lower()
    assert "4.2" in plain.headline
    # Uncalibrated magnitudes cannot be sized, and saying so is the honest verdict.
    assert plain.verdict == "unrated"


def test_a_traffic_plan_with_no_inventory_says_so_rather_than_reporting_no_effect() -> None:
    """A missing layer and a null result are opposite answers, in plain words too."""
    plain = brief_for(
        PLANTING.model_copy(
            update={
                "tree_count": 0,
                "mean_canopy_added": 0.0,
                "mean_delta_inside": 0.0,
                "min_delta": 0.0,
                "emission_fraction_requested": 0.6,
            }
        )
    ).plain

    assert plain.verdict == "none"
    assert "not a finding" in " ".join(plain.points)
