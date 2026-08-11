"""Request and response contracts for `POST /simulate/chat`.

A follow-up question about a result the client already has in hand. The request carries
the `Brief` rather than the whole `SimulateResponse`, because the raster delta inside it is
tens of kilobytes of base64 that a chat turn never needs — the brief is the councillor-
facing narrative, already stripped of everything but sentences and figures, and it is what
`_facts_from_result` in `api/routes/chat.py` grounds the answer in.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from terrarium.dsl.explain import Brief


class ChatTurn(BaseModel):
    """One turn of the conversation so far, replayed so a follow-up can reference it."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str


class ResultChatRequest(BaseModel):
    """A question about a result, plus the conversation held about it so far."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    brief: Brief
    window: str
    season: str
    plan_name: str | None = Field(
        default=None, description="What the plan was called, if the request came from one"
    )
    history: tuple[ChatTurn, ...] = Field(
        default=(),
        description="Prior turns in this session, oldest first. Empty for the first question",
    )
    question: str = Field(min_length=1, max_length=1000)


class ResultChatResponse(BaseModel):
    """An answer grounded in the brief's own figures, or a refusal to invent one."""

    model_config = ConfigDict(frozen=True)

    answer: str
    source: str = Field(description="The provider chain that wrote it, e.g. 'langchain:<model>'")
