"""What the search is trying to do, what it has tried, and what it may spend.

Every type here is frozen and free of I/O, for the same reason `dsl/schema.py` is: these
are the contracts the graph, the deterministic control and the HTTP layer all agree on,
and a mutable one shared across a graph's nodes is how a search loop stops being
reproducible.

`Candidate` lives here rather than in `api/candidates.py` because it is the shape the
*agent* reasons about; the API layer computes it, because the API layer is the one that
owns the grid (D6, D26). That direction of dependency is deliberate — `api/candidates.py`
imports this module and nothing here imports `api/`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from terrarium.dsl.schema import Plan

# What the search maximises. Three metrics rather than one because "best" is genuinely
# ambiguous here and the ambiguity is the user's to resolve: the coldest plan, the plan
# that reaches the most people, and the plan that cuts the most traffic PM2.5 are routinely
# three different plans on this tile. `pm25_reduction` exists because a `restrict_vehicles`
# plan changes no temperature — scored against either thermal metric it is always zero and
# can never win a search, however much air it cleans.
Metric = Literal["cooling", "person_degrees", "pm25_reduction"]


class Objective(BaseModel):
    """The goal, in numbers. Produced from a sentence by `nodes.parse_goal`.

    The constraint is separate from the metric on purpose. *"Get 1 degC off somewhere,
    reaching as many people as possible"* is one metric (`person_degrees`) and one
    constraint, and collapsing them into a single weighted score would let the search
    trade the constraint away instead of honouring it as hard.
    """

    model_config = ConfigDict(frozen=True)

    metric: Metric = Field(
        default="person_degrees",
        description="What to maximise. The constraint below is separate and is not traded off",
    )
    target_cooling_c: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "Mean cooling inside the polygon that would satisfy the goal, in degC, stated "
            "positive. **Compared against the hindcast-corrected figure**, never the raw "
            "model output — a target met only before the 2.5x correction is a target missed"
        ),
    )
    window: str | None = Field(
        default=None, description="Exact window label; null lets the runtime default apply"
    )
    description: str = Field(
        default="Cool this tile and reach as many people as possible",
        max_length=200,
        description="The goal in words, for the trace and the report",
    )


class Candidate(BaseModel):
    """One block of the lattice, with everything the model needs to choose between them.

    Carries no cube and no arrays — a candidate is what gets serialised into a prompt and
    onto the wire, so it is a handful of scalars plus the polygon the UI draws. The mask
    is rebuilt from the row/column bounds by `api.candidates`, which is cheap and cannot
    drift from the geometry the way a cached mask could.
    """

    model_config = ConfigDict(frozen=True)

    region_id: str = Field(description="Stable lattice id, e.g. 'r03c05'")
    # Half-open, in grid cells: rows [row0, row1), columns [col0, col1).
    row0: int
    row1: int
    col0: int
    col1: int

    cells: int = Field(gt=0)
    area_m2: float = Field(gt=0.0)
    plantable_canopy_m2: float = Field(
        ge=0.0,
        description=(
            "Summed from the thermal core's own per-cell headroom, so the lattice and the "
            "physics cannot disagree about how green a block already is"
        ),
    )
    max_trees: int = Field(ge=0)
    mean_lst_c: float | None = Field(
        default=None, description="Null where the block is entirely no-data"
    )
    population: float = Field(ge=0.0, description="Summed — extensive, never averaged")
    emission_g_s: float = Field(ge=0.0)
    # WGS84, for the UI overlay and for handing the winner back to /simulate.
    geometry: dict[str, Any]

    @property
    def opportunity(self) -> float:
        """Plantable canopy x residents: the greedy control's ranking, in one place.

        Both factors are necessary and neither is sufficient. Room to plant with nobody
        living there is a cooled field; a dense block with no headroom is a plan that gets
        refused. Their product is the cheapest honest proxy for "worth simulating".
        """
        return self.plantable_canopy_m2 * self.population


class Outcome(BaseModel):
    """What the cores said about one attempt. Every figure here came out of a core."""

    model_config = ConfigDict(frozen=True)

    mean_delta_inside_c: float = Field(description="Raw model output, negative for cooling")
    expected_cooling_c: float = Field(
        description=(
            "Cooling after the hindcast correction, stated positive. This is the figure "
            "targets are checked against and the one a report may quote"
        )
    )
    person_degrees: float = Field(description="Population x cooling, from cores.equity")
    people_reached: float = Field(ge=0.0)
    tree_count: int = Field(ge=0)
    area_km2: float = Field(gt=0.0)
    delta_pm25: float | None = Field(
        default=None, description="Mean local PM2.5 change inside, when the plan removed any"
    )


class Attempt(BaseModel):
    """One trip round the loop, kept whether it worked or not.

    A refused attempt is as much a part of the trace as a scored one — more, actually,
    because the refusal carries the arithmetic that explains it and is what the next
    proposal is conditioned on. A search that only reported its successes would be a
    search whose failures nobody could audit.
    """

    model_config = ConfigDict(frozen=True)

    step: int
    region_ids: tuple[str, ...]
    plan: Plan
    status: Literal["scored", "refused"]
    # Only two producers now. The lattice sweep that used to fill in for a missing key was
    # removed when the agent became key-required: it was a different, worse procedure
    # wearing the same result shape, and a `fallback` value here was how that reached a
    # reader — as a footnote on a result that otherwise looked like a search.
    proposer: Literal["model", "greedy"] = "model"
    # The validator's own sentence, verbatim. This is the feedback signal.
    reason: str | None = None
    score: float | None = None
    outcome: Outcome | None = None


class SearchBudget(BaseModel):
    """What one search may spend. Exceeded is not an error — it is how a search ends.

    The cores measure ~0.84 s warm per `/simulate`, so ten simulations is about 8 s and
    the model is the rest of the 60 s. That split is why streaming is not optional here:
    a 40-second spinner is a broken feature and a 40-second visible search is the feature.
    """

    model_config = ConfigDict(frozen=True)

    max_simulations: int = Field(default=10, ge=1, le=40)
    max_llm_calls: int = Field(default=20, ge=0, le=60)
    wall_clock_s: float = Field(default=60.0, gt=0.0, le=300.0)


class SearchResult(BaseModel):
    """The answer: the winner, the control it has to beat, and everything tried."""

    model_config = ConfigDict(frozen=True)

    search_id: str
    goal: str
    objective: Objective
    window: str
    season: str
    best: Attempt | None
    # The greedy control (A2). **Not a fallback** — a search result with nothing to beat
    # is a claim, and this project does not ship claims. If the model never beats greedy,
    # that is a finding worth publishing, and it is only visible because this is here.
    baseline: Attempt | None
    beat_baseline: bool = Field(
        description="Whether the model's winner scored above the deterministic control"
    )
    tried: tuple[Attempt, ...]
    simulations_used: int
    llm_calls_used: int
    elapsed_s: float
    stopped_because: str
    report: tuple[str, ...] = Field(
        default=(),
        description=(
            "The search narrated, under `dsl.llm`'s faithfulness guards. Empty when the "
            "model could not write it or drifted — the numbers above came from the cores "
            "and are the result either way, so a search that cannot be narrated is still "
            "a search that ran"
        ),
    )
    report_source: str = Field(
        default="unavailable", description="The provider chain, or 'unavailable'"
    )


class SearchEvent(BaseModel):
    """One node transition, as the client sees it.

    The search takes tens of seconds, so **streaming is not optional**: a 40-second spinner
    is a broken feature and a 40-second visible search is the feature. One event per node
    means the trace on screen is the graph's actual execution, not a progress animation
    guessing at it.
    """

    model_config = ConfigDict(frozen=True)

    node: str = Field(description="Which node just ran, or 'error'")
    message: str = Field(description="One line for the trace")
    attempt: Attempt | None = Field(
        default=None, description="Present on the events that produced one"
    )
    result: SearchResult | None = Field(default=None, description="Present on the final event only")
