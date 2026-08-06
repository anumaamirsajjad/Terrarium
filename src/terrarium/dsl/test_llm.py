"""The provider seam.

Every test here stubs `urllib.request.urlopen`. **No test may touch the network** — and
this is the one module in the package that could, which is exactly why it is stubbed
explicitly rather than left to whether a key happens to be set in the environment.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.message import Message
from io import BytesIO
from typing import Any

import pytest

from terrarium.dsl.llm import GeminiAdapter, LLMUnavailable, adapter_from_key


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = BytesIO(json.dumps(payload).encode())

    def read(self) -> bytes:
        return self._body.read()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _candidate(text: str) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_no_key_means_no_adapter() -> None:
    # The expected state, not a degraded one: the rule parser answers instead.
    assert adapter_from_key(None) is None
    assert adapter_from_key("") is None


def test_a_key_builds_a_named_adapter() -> None:
    adapter = adapter_from_key("secret", model="gemini-2.5-flash")
    assert adapter is not None
    assert adapter.name == "gemini:gemini-2.5-flash"


def test_the_reply_text_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: Any, timeout: float | None = None) -> _Response:
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        return _Response(_candidate('{"name": "x"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    adapter = GeminiAdapter(api_key="secret", model="gemini-2.5-flash")
    assert adapter.complete_json(system="sys", user="usr") == '{"name": "x"}'

    body = captured["body"]
    assert isinstance(body, dict)
    # Structured output at temperature 0: the model is filling in a schema, not writing.
    assert body["generationConfig"] == {"temperature": 0.0, "responseMimeType": "application/json"}
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"


def test_a_transport_failure_becomes_llm_unavailable_without_leaking_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(request: Any, timeout: float | None = None) -> _Response:
        # An HTTPError's str() includes the request line, and the key is in the URL.
        raise urllib.error.HTTPError(
            "https://example/v1?key=secret", 429, "Too Many Requests", Message(), None
        )

    monkeypatch.setattr(urllib.request, "urlopen", explode)

    with pytest.raises(LLMUnavailable) as exc:
        GeminiAdapter(api_key="secret").complete_json(system="s", user="u")

    assert "secret" not in str(exc.value)


def test_a_blocked_or_empty_candidate_is_unavailable_rather_than_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: _Response({"candidates": []}),
    )

    with pytest.raises(LLMUnavailable, match="no usable candidate"):
        GeminiAdapter(api_key="secret").complete_json(system="s", user="u")
