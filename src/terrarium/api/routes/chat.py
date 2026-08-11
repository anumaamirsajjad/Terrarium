"""`POST /simulate/chat` — a follow-up question about a result already in hand.

No cube dependency and no polygon: the client already ran `/simulate` and has the `Brief`
that came back, and this route only ever reasons over that brief plus the conversation held
about it so far. Needs a model (D25-style: this *is* a model doing something, not a model
decorating something), so it 503s exactly like `/agent/search` when none is configured.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from terrarium.api.deps import require_model
from terrarium.api.schemas.chat import ResultChatRequest, ResultChatResponse
from terrarium.config import Settings, get_settings
from terrarium.dsl.explain import Brief
from terrarium.dsl.llm import answer_result_question

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulate", tags=["chat"])


def _facts(request: ResultChatRequest) -> str:
    """Everything the answer may cite a number from, and nothing else.

    Built from the brief the client already has, not recomputed from a cube: the brief is
    the councillor-facing narrative, already stripped to sentences and figures, so reusing
    it is what keeps the chat unable to say anything `/simulate` did not already say.
    """
    brief: Brief = request.brief
    lines = [
        f"window: {request.window} ({request.season})",
        brief.headline,
        *brief.findings,
        *brief.plain.points,
        *brief.uncertainties,
        f"confidence: {brief.confidence}",
    ]
    if request.plan_name:
        lines.append(f"plan: {request.plan_name}")
    return "\n".join(lines)


@router.post(
    "/chat",
    response_model=ResultChatResponse,
    summary="Ask a follow-up question about a result",
)
async def chat_about_result(
    request: ResultChatRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResultChatResponse:
    require_model(settings)

    answered = answer_result_question(
        facts=_facts(request),
        history=tuple((turn.role, turn.content) for turn in request.history),
        question=request.question,
        settings=settings,
    )
    if answered is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "could not answer that. Either no model is configured, or answering it "
                "would have meant citing a figure the brief did not contain."
            ),
        )

    answer, source = answered
    return ResultChatResponse(answer=answer, source=source)
