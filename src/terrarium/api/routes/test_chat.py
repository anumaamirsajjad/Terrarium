"""`/simulate/chat` — a follow-up question about a result already in hand.

No cube dependency: the request carries the brief itself, so these tests build one by hand
rather than running a simulation first. `require_model` gates the route exactly as it gates
`/agent/search`, and the guard on the model's own answer is `dsl.llm`'s, tested at that
level in `dsl/test_llm.py` — these tests cover the route's wiring, not the guard again.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import FakeListChatModel

from terrarium.api.conftest import ScriptedAdapter
from terrarium.dsl import llm

Configure = Callable[[Sequence[str]], ScriptedAdapter]

BRIEF = {
    "headline": "Street trees over 4.0 km2 cool the ground by about 0.41 degC.",
    "plain": {
        "verdict": "small",
        "headline": "Street trees here would make the ground about 0.41 degC cooler.",
        "points": ["About 12,000 people live across this tile."],
        "caveat": "This is a model, not a measurement.",
        "source": "template",
    },
    "findings": ["Canopy actually added: 15% per planted cell."],
    "uncertainties": ["Modelled, not measured."],
    "confidence": "moderate",
    "expected_cooling_c": -0.41,
}


def _body(**overrides: Any) -> dict[str, Any]:
    return {
        "brief": BRIEF,
        "window": "2024-summer",
        "season": "summer",
        "question": "why did it cool that much?",
        **overrides,
    }


def _reply_with(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    model = FakeListChatModel(responses=[json.dumps({"answer": answer})])
    monkeypatch.setattr(llm, "chat_model", lambda _settings, **_kw: model)


def test_without_a_model_is_503(client: TestClient) -> None:
    response = client.post("/simulate/chat", json=_body())
    assert response.status_code == 503
    assert "TERRARIUM_GROQ_API_KEY" in response.json()["detail"]


def test_a_faithful_answer_is_returned(
    client: TestClient, with_model: Configure, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_model(["irrelevant, chat_model is stubbed separately"])
    _reply_with(monkeypatch, "It cooled by about 0.41 degC because of the added canopy.")

    response = client.post("/simulate/chat", json=_body())
    assert response.status_code == 200

    body = response.json()
    assert "0.41" in body["answer"]
    assert body["source"].startswith("langchain:")


def test_an_answer_that_invents_a_figure_is_503(
    client: TestClient, with_model: Configure, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_model(["irrelevant, chat_model is stubbed separately"])
    _reply_with(monkeypatch, "It cooled by about 1.9 degC.")

    response = client.post("/simulate/chat", json=_body())
    assert response.status_code == 503
    assert "citing a figure" in response.json()["detail"]


def test_history_is_accepted(
    client: TestClient, with_model: Configure, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second turn carries the first one back, and the route does not choke on it."""
    with_model(["irrelevant, chat_model is stubbed separately"])
    _reply_with(monkeypatch, "Yes, the same 4.0 km2 area.")

    response = client.post(
        "/simulate/chat",
        json=_body(
            history=[
                {"role": "user", "content": "how big is the area?"},
                {"role": "assistant", "content": "4.0 km2."},
            ],
            question="is that the same area as before?",
        ),
    )
    assert response.status_code == 200
