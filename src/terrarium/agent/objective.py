"""Turning an `Outcome` into one number the search can compare, and nothing else.

Pure arithmetic over two frozen models. No cube, no cores, no model — which is what makes
the search's *ranking* testable without any of them, and is the reason this is its own
module rather than three lines inside `nodes.py`.

The rule the whole file exists to hold: **targets are checked against the corrected
figure.** `Outcome.expected_cooling_c` is already divided by the hindcast factor. A target
met only before that correction is a target missed, and checking the raw number would make
the agent's headline the one figure in the project that skips the correction every brief
carries.
"""

from __future__ import annotations

from terrarium.agent.state import Attempt, Objective, Outcome


def score(objective: Objective, outcome: Outcome) -> float:
    """Higher is better, always. `-inf` when a hard constraint is broken.

    The two metrics answer two genuinely different questions and routinely pick two
    different plans on this tile — the coldest block is rarely the most populated one.
    """
    if objective.metric == "cooling":
        return outcome.expected_cooling_c
    return outcome.person_degrees


def satisfies(objective: Objective, outcome: Outcome) -> bool:
    """Whether this outcome meets the goal as stated, so the search may stop early.

    Only a stated cooling target can be satisfied early: it is the one number a goal can
    name that has a clear "met it" line to cross. `metric` alone has no such line — "reach
    as many people as possible" has no point at which it stops being true.
    """
    if objective.target_cooling_c is not None:
        return outcome.expected_cooling_c >= objective.target_cooling_c
    # No target means "do the best you can", which is never satisfied early — the search
    # ends on its budget instead, having tried everything it could afford.
    return False


def better(candidate: Attempt, incumbent: Attempt | None) -> bool:
    """Whether `candidate` should replace `incumbent` as the search's best.

    An unscored attempt never wins, including against `None`. That matters: without it the
    first refused proposal becomes `best`, and the search reports a plan the validator
    rejected as its answer.
    """
    if candidate.score is None:
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
    }[objective.metric]
