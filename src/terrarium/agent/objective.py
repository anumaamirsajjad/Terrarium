"""Turning an `Outcome` into one number the search can compare, and nothing else.

Pure arithmetic over two frozen models. No cube, no cores, no model — which is what makes
the search's *ranking* testable without any of them, and is the reason this is its own
module rather than three lines inside `nodes.py`.

Two rules the whole file exists to hold:

- **Constraints are not scored, they are enforced.** A goal with a budget in it means the
  budget is hard. Folding it into a weighted score lets a search buy past it by cooling
  harder, which is exactly the answer nobody asked for.
- **Targets are checked against the corrected figure.** `Outcome.expected_cooling_c` is
  already divided by the hindcast factor. A target met only before that correction is a
  target missed, and checking the raw number would make the agent's headline the one
  figure in the project that skips the correction every brief carries.
"""

from __future__ import annotations

from terrarium.agent.state import Attempt, Objective, Outcome

# What a score of "worse than anything" is. Used for an outcome that breaks a constraint,
# so it can never become `best` however good its metric looks.
INFEASIBLE = float("-inf")


def score(objective: Objective, outcome: Outcome) -> float:
    """Higher is better, always. `-inf` when a hard constraint is broken.

    The three metrics answer three genuinely different questions and routinely pick three
    different plans on this tile — the coldest block is rarely the most populated one, and
    neither is usually the cheapest per person reached.
    """
    if objective.max_cost_usd is not None and outcome.cost_usd > objective.max_cost_usd:
        return INFEASIBLE

    if objective.metric == "cooling":
        return outcome.expected_cooling_c
    if objective.metric == "person_degrees":
        return outcome.person_degrees
    # cost_effectiveness. A free plan is not infinitely good, it is a plan that did
    # nothing: a zero cost means no trees were planted and no zone was drawn, so there is
    # no ratio to take and the honest score is zero.
    return outcome.person_degrees / outcome.cost_usd if outcome.cost_usd > 0 else 0.0


def satisfies(objective: Objective, outcome: Outcome) -> bool:
    """Whether this outcome meets the goal as stated, so the search may stop early.

    Both constraints, not just the metric. A plan that hits 1 degC at twice the budget has
    not answered *"1 degC for under $500k"* — it has answered a different question, and
    stopping on it would report a success the user did not ask for.
    """
    if objective.max_cost_usd is not None and outcome.cost_usd > objective.max_cost_usd:
        return False
    if objective.target_cooling_c is not None:
        return outcome.expected_cooling_c >= objective.target_cooling_c
    # No target means "do the best you can", which is never satisfied early — the search
    # ends on its budget instead, having tried everything it could afford.
    return False


def better(candidate: Attempt, incumbent: Attempt | None) -> bool:
    """Whether `candidate` should replace `incumbent` as the search's best.

    An unscored or infeasible attempt never wins, including against `None`. That matters:
    without it the first refused proposal becomes `best`, and the search reports a plan
    the validator rejected as its answer.
    """
    if candidate.score is None or candidate.score == INFEASIBLE:
        return False
    if incumbent is None or incumbent.score is None:
        return True
    return candidate.score > incumbent.score


def units_for(objective: Objective) -> str:
    """What the score is measured in, for the trace and the report.

    A bare number on a screen is unreadable and a bare number in a prompt is worse — the
    report node is handed these words so it does not have to guess what 4,812 means.
    """
    return {
        "cooling": "degC of expected cooling inside the region",
        "person_degrees": "person-degrees (residents x degC of cooling)",
        "cost_effectiveness": "person-degrees per USD",
    }[objective.metric]
