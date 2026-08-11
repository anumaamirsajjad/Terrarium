"""`POST /evidence/ask` — ask this project's own record.

**No runtime dependency.** The corpus is markdown in the repository, not the cube, so this
answers on a deployment whose Zarr store failed to load — which is precisely the deployment
where somebody most wants to ask the docs what went wrong. `/plan/presets` is mounted for
the same reason.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from terrarium.api.deps import require_model
from terrarium.config import Settings, get_settings
from terrarium.evidence.answer import Answer, EvidenceUnavailable, answer_question
from terrarium.evidence.corpus import anchors, cached_corpus
from terrarium.evidence.retrieve import Index

router = APIRouter(prefix="/evidence", tags=["evidence"])

# How many sections reach the prompt. Six is what fits a question's worth of context
# without turning every answer into a survey; see `answer.SECTION_CHARS` for the other half
# of that budget.
TOP_K = 6


@lru_cache(maxsize=4)
def _index(root: Path) -> tuple[Index, frozenset[str]]:
    """The BM25 index and the set of resolvable anchors, built once per process.

    Together, because they must describe the same corpus: an index built from one snapshot
    and an anchor set from another would reject citations to sections that were retrieved.
    """
    sections = cached_corpus(root)
    return Index(sections), frozenset(anchors(sections))


class AskRequest(BaseModel):
    """A question about the project."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(
        min_length=3,
        max_length=500,
        description=(
            "e.g. 'why is the cooling divided by 2.5?'. Answered from the repository's own "
            "documentation and from nothing else — every citation is checked to resolve, "
            "and one that does not rejects the whole answer back to the passages."
        ),
    )
    top_k: int = Field(default=TOP_K, ge=1, le=12, description="Sections to retrieve")


@router.post(
    "/ask",
    response_model=Answer,
    summary="Ask the project's own documentation, with checked citations",
)
async def ask(
    request: AskRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Answer:
    # Called here rather than as a route dependency so that body validation wins: a
    # three-character question is a bad request on every deployment, and answering it
    # 503 would blame the configuration for the caller's typo.
    require_model(settings)

    index, _known = _index(settings.docs_root)
    passages = [section for section, _ in index.search(request.question, limit=request.top_k)]

    try:
        # No `known_anchors`: the default is the passages the model was actually shown,
        # which is the strict reading of 'answer from the excerpts'.
        return answer_question(request.question, passages, settings=settings)
    except EvidenceUnavailable as exc:
        # 422 when the corpus simply has nothing — that is a fact about the question, and
        # the request was fine. 502 when a model was reached and its answer was thrown
        # away, because the fault is upstream and a retry may well work.
        #
        # The passages ride along either way. They are what the answer would have been
        # drawn from, and a caller that wants to show them should not have to ask twice.
        raise HTTPException(
            status_code=422 if not passages else 502,
            detail={
                "message": str(exc),
                "rejected_citations": list(exc.rejected_citations),
                "passages": [section.model_dump() for section in exc.passages],
            },
        ) from None
