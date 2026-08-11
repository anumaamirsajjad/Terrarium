"""Request and response contracts for `POST /agent/search`.

The agent's own frozen models (`Objective`, `Candidate`, `Attempt`, `SearchResult`,
`SearchEvent`) cross the wire unchanged, for the reason `schemas/plan.py` gives about the
DSL's: they are frozen Pydantic models in the same layer, and mirroring them into a second
near-identical set would create a seam that exists only to drift.

What is here is the request, and the response envelope for the non-streaming read-back.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from terrarium.agent.state import Candidate, SearchBudget, SearchResult


class SearchRequest(BaseModel):
    """A goal in words, and what the search may spend answering it.

    **No geometry.** That is the point of D26: the agent searches the whole tile and picks
    from a lattice the grid layer generated, so there is nothing for a caller to draw and
    nothing for a model to hallucinate.
    """

    # `extra="forbid"`, matching the other request schemas: a misspelled `budget` that
    # silently meant "the default budget" would be indistinguishable from not setting one.
    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str = Field(
        min_length=3,
        max_length=500,
        description=(
            "What to look for, in plain language — e.g. 'get 1 degC off somewhere for "
            "under $500k, reaching as many people as possible'. Parsed by a model when "
            "one is configured and by a deterministic parser otherwise; the response says "
            "which objective it settled on."
        ),
    )
    window: str | None = Field(
        default=None,
        description=(
            "Overrides whatever the goal says. Falls back to the goal's window, then to "
            "the cube default — the latest *summer*."
        ),
    )
    budget: SearchBudget = Field(
        default_factory=SearchBudget,
        description="Simulations, model calls and wall clock. Exceeded is how a search ends",
    )


class CandidatesResponse(BaseModel):
    """The lattice the agent chooses from, for the map overlay.

    Served separately from a search so the UI can draw the regions before anything runs —
    which is what makes the live trace legible: the highlighted block is already on screen
    when the agent names it.
    """

    model_config = ConfigDict(frozen=True)

    window: str
    block_cells: int = Field(description="Lattice granularity, in grid cells per side")
    candidates: tuple[Candidate, ...]


class SearchResponse(BaseModel):
    """A finished search, read back by id.

    The stream is the primary interface; this exists so a result survives the page being
    reloaded mid-search and so a script can `POST` then `GET` rather than parse SSE.
    """

    model_config = ConfigDict(frozen=True)

    result: SearchResult
