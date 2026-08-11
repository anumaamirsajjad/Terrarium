"""Reading an intervention out of a sentence.

Every test here uses the rule parser or a stub adapter. **No test may touch the network**,
which is also why the rule parser is not optional: it is the only parser CI can exercise.
"""

from __future__ import annotations

import pytest

from terrarium.dsl.llm import LLMUnavailable
from terrarium.dsl.planner import PlanParseError, _number, parse_rules, plan_from_text
from terrarium.dsl.schema import Plan, PlantTrees


class _StubAdapter:
    """An LLM that says whatever the test tells it to, without leaving the process."""

    def __init__(self, reply: str | Exception) -> None:
        self._reply = reply
        self.calls = 0

    @property
    def name(self) -> str:
        return "stub:test"

    def complete_json(self, *, system: str, user: str) -> str:
        self.calls += 1
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


# ------------------------------------------------------------------ rule parser ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("plant 5,000 trees here", 5_000),
        ("plant 5000 trees", 5_000),
        ("add 5k trees along these streets", 5_000),
        ("12 thousand trees", 12_000),
        ("plant one more tree", None),  # spelled-out numerals are out of scope
    ],
)
def test_tree_counts_are_read_in_the_forms_people_type(text: str, expected: int | None) -> None:
    if expected is None:
        with pytest.raises(PlanParseError):
            parse_rules(text)
        return

    planting = parse_rules(text).plan.planting
    assert planting is not None
    assert planting.tree_count == expected


def test_number_rejects_a_value_that_would_overflow_round() -> None:
    """F14: `float("9"*309)` is `inf`, and `round(inf)` raises a bare `OverflowError`.

    `_number` is exercised directly because the regex cap below already keeps a real
    request from reaching this path with digits long enough to overflow - this is the
    second, independent line of defence the plan asked for.
    """
    with pytest.raises(PlanParseError):
        _number("9" * 309, None)


def test_a_very_long_digit_run_does_not_crash_the_parser() -> None:
    """The regex cap (F14) truncates a runaway digit run to a large but finite count
    rather than ever handing `_number` something that overflows `float()`."""
    text = "plant " + "9" * 309 + " trees"
    planting = parse_rules(text).plan.planting
    assert planting is not None
    assert planting.tree_count < 10**16


@pytest.mark.parametrize(
    "text",
    ["add 30% canopy", "raise canopy by 30 per cent", "30 % more tree cover please"],
)
def test_canopy_percentages_are_read_either_way_round(text: str) -> None:
    planting = parse_rules(text).plan.planting
    assert planting is not None
    assert planting.canopy_fraction_added == pytest.approx(0.30)


def test_a_count_beats_a_percentage_and_says_so() -> None:
    parsed = parse_rules("plant 2,000 trees, about 30% canopy")
    planting = parsed.plan.planting
    assert planting is not None
    assert planting.tree_count == 2_000
    assert any("the count was used" in warning for warning in parsed.warnings)


@pytest.mark.parametrize(
    "text",
    [
        "ban combustion vehicles inside this ring",
        "make it car-free",
        "a low-emission zone here",
        "close this street to traffic",
    ],
)
def test_a_restriction_without_a_number_is_total_and_warns_about_it(text: str) -> None:
    parsed = parse_rules(text)
    restriction = parsed.plan.restriction
    assert restriction is not None
    assert restriction.emission_fraction_removed == 1.0
    # 1.0 is a strong reading of "ban", and it means the traffic is gone rather than
    # electrified. Saying so is the difference between a default and a silent assumption.
    assert any("not electrified" in warning for warning in parsed.warnings)


def test_a_partial_restriction_keeps_its_number() -> None:
    parsed = parse_rules("remove 40% of traffic in this area")
    restriction = parsed.plan.restriction
    assert restriction is not None
    assert restriction.emission_fraction_removed == pytest.approx(0.40)
    assert parsed.warnings == ()


def test_both_actions_in_one_sentence() -> None:
    parsed = parse_rules("plant 3,000 trees and ban cars here")
    assert parsed.plan.planting is not None
    assert parsed.plan.restriction is not None
    assert parsed.plan.name == "Planting and low-emission zone"


@pytest.mark.parametrize(
    ("text", "window", "season"),
    [
        ("plant 100 trees in 2024-winter", "2024-winter", None),
        ("plant 100 trees, winter 2023", "2023-winter", None),
        ("plant 100 trees for the summer", None, "summer"),
        ("plant 100 trees", None, None),
    ],
)
def test_the_window_is_read_when_it_is_there_and_left_null_when_it_is_not(
    text: str, window: str | None, season: str | None
) -> None:
    # A season without a year stays a season: which window that becomes depends on what
    # the cube holds, and this module does not know.
    plan = parse_rules(text).plan
    assert plan.window == window
    assert (str(plan.season) if plan.season else None) == season


def test_text_with_no_intervention_is_refused_with_the_vocabulary() -> None:
    with pytest.raises(PlanParseError) as exc:
        parse_rules("what is the weather like in Lahore")
    assert "5,000 trees" in str(exc.value)
    assert "ban combustion vehicles" in str(exc.value)


def test_an_absurd_percentage_is_clamped_rather_than_refused() -> None:
    planting = parse_rules("add 150% canopy").plan.planting
    assert planting is not None
    assert planting.canopy_fraction_added == 1.0


# --------------------------------------------------------------------- llm path ---


def test_no_adapter_means_the_rule_parser_and_no_call() -> None:
    parsed = plan_from_text("plant 500 trees")
    assert parsed.source == "rules"


def test_a_valid_model_reply_is_used() -> None:
    reply = Plan(name="Model plan", actions=(PlantTrees(tree_count=900),)).model_dump_json()
    adapter = _StubAdapter(reply)

    parsed = plan_from_text("plant nine hundred trees", adapter=adapter)

    assert adapter.calls == 1
    assert parsed.source == "llm"
    assert parsed.plan.name == "Model plan"


@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        '{"name": "Bad", "actions": [{"kind": "plant_trees"}]}',  # neither unit given
        '{"name": "Bad", "actions": []}',  # the schema's own escape hatch
        '{"name": "Bad", "actions": [{"kind": "fly_drones", "count": 3}]}',
    ],
)
def test_model_output_that_fails_validation_falls_back_rather_than_reaching_a_core(
    reply: str,
) -> None:
    # The safety argument for putting a free-tier model in front of a simulator: its output
    # is a `Plan` or it is nothing, and "nothing" is a fallback, not a 500.
    parsed = plan_from_text("plant 200 trees", adapter=_StubAdapter(reply))

    assert parsed.source == "rules"
    assert parsed.plan.planting is not None
    assert parsed.plan.planting.tree_count == 200
    assert any("could not produce a valid plan" in warning for warning in parsed.warnings)


def test_an_unreachable_model_falls_back() -> None:
    parsed = plan_from_text("plant 200 trees", adapter=_StubAdapter(LLMUnavailable("no network")))
    assert parsed.source == "rules"


def test_a_model_failure_on_unparseable_text_still_raises_the_parse_error() -> None:
    # The fallback cannot invent a plan either. A failed model plus a sentence about
    # nothing is still a sentence about nothing.
    with pytest.raises(PlanParseError):
        plan_from_text("hello there", adapter=_StubAdapter(LLMUnavailable("no network")))
