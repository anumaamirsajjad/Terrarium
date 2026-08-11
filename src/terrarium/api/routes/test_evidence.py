"""`/evidence/ask` over the real documentation.

The route needs a model now: it used to answer a keyless request with the retrieved
passages, which read as graceful degradation and was not one — "here is what the docs say"
and "a model wrote this and we threw it away" are different events, and they arrived
looking identical.

The corpus is still real markdown in the repository, so the retrieval half is exercised
against the actual files rather than a fixture.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

from fastapi.testclient import TestClient

from terrarium.api.conftest import ScriptedAdapter
from terrarium.api.main import create_app
from terrarium.config import Settings

Configure = Callable[[Sequence[str | Callable[[str], str]]], ScriptedAdapter]


def _answer(text: str) -> str:
    return json.dumps({"answer": text})


def _cite_what_it_was_shown(prompt: str) -> str:
    """Answer citing the first excerpt actually handed over.

    Computed from the prompt rather than hardcoded, because the guard now accepts only
    anchors the model was shown, and which sections BM25 retrieves for a given question is
    not something a test should be pinning down.
    """
    anchor = json.loads(prompt)["excerpts"][0]["anchor"]
    return _answer(f"The documentation says so ({anchor}).")


def test_without_a_model_it_is_503(client: TestClient) -> None:
    """The change: a status code rather than the passages wearing an answer's shape."""
    response = client.post("/evidence/ask", json={"question": "why 2.5x?"})

    assert response.status_code == 503
    assert "TERRARIUM_GEMINI_API_KEY" in response.json()["detail"]


def test_the_worked_example_lands_on_the_hindcast(
    client: TestClient, with_model: Configure
) -> None:
    """*"Why is the cooling divided by 2.5?"* — the plan's own example question.

    The model is scripted, so what is under test is the retrieval and the citation guard,
    not the prose. The citation below has to resolve against the *real* corpus.
    """
    with_model([_cite_what_it_was_shown])

    response = client.post(
        "/evidence/ask", json={"question": "why is the cooling divided by 2.5?"}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["passages"], "nothing retrieved for the question this feature exists for"
    joined = " ".join(section["body"].lower() for section in body["passages"])
    assert "hindcast" in joined


def test_a_fabricated_citation_is_502_with_the_offending_anchor(
    client: TestClient, with_model: Configure
) -> None:
    """The guard, as an HTTP contract.

    502 rather than 200: the fault is upstream and a retry may well work, and the caller
    is entitled to know its answer was discarded rather than never written.
    """
    with_model([_answer("See (docs/INVENTED.md#not-a-real-heading) for the figure.")])

    response = client.post("/evidence/ask", json={"question": "why no LangGraph?"})
    assert response.status_code == 502

    detail = response.json()["detail"]
    assert detail["rejected_citations"] == ["docs/INVENTED.md#not-a-real-heading"]
    # The evidence still comes back, so a client can show what the model was given.
    assert detail["passages"]


def test_every_citation_returned_resolves_to_a_passage(
    client: TestClient, with_model: Configure
) -> None:
    with_model([_cite_what_it_was_shown])

    body = client.post("/evidence/ask", json={"question": "how is equity measured?"}).json()

    anchors = {section["anchor"] for section in body["passages"]}
    for citation in body["citations"]:
        assert citation["anchor"] in anchors


def test_a_question_the_corpus_cannot_answer_is_422_and_reaches_no_model(
    client: TestClient, with_model: Configure
) -> None:
    """422, not 502: the request was fine and the corpus simply has nothing. And the
    model is never asked, because a question with no evidence behind it is an invitation
    to answer from general knowledge."""
    adapter = with_model([_cite_what_it_was_shown])

    response = client.post(
        "/evidence/ask", json={"question": "zzzqqq xylophone marmalade quokka"}
    )
    assert response.status_code == 422
    assert adapter.prompts == []
    assert "Nothing in this project's documentation" in response.json()["detail"]["message"]


def test_a_too_short_question_is_422(client: TestClient) -> None:
    assert client.post("/evidence/ask", json={"question": "?"}).status_code == 422


def test_it_answers_without_a_cube(with_model: Configure) -> None:
    """The deployment where somebody most wants to ask the docs what went wrong is the one
    whose cube failed to load. This route takes no runtime dependency for that reason."""
    with_model([_cite_what_it_was_shown])

    app = create_app(
        Settings(
            env="test",
            serve_zarr_store=Path("does/not/exist"),
            # `require_model` reads the settings, not the patched resolver, so the key has
            # to be present here for the dependency to pass — the patch then makes sure no
            # request ever leaves the process.
            groq_api_key="test-key",
        )
    )
    with TestClient(app) as offline:
        # The data routes are 503 on this app, which is what makes the next line mean
        # something rather than being a fixture accident.
        assert offline.get("/cube/summary").status_code == 503

        response = offline.post("/evidence/ask", json={"question": "why is the LLM optional?"})
        assert response.status_code == 200
        assert response.json()["passages"]
