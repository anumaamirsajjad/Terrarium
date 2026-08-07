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

from terrarium.dsl.llm import (
    FallbackAdapter,
    GeminiAdapter,
    GroqAdapter,
    LLMUnavailable,
    adapter_from_key,
    groq_adapter_from_key,
    resolve_adapter,
)


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


# --------------------------------------------------------------------------------
# Groq: the second provider
# --------------------------------------------------------------------------------


def _choice(text: str | None) -> dict[str, object]:
    return {"choices": [{"message": {"content": text}}]}


def test_groq_key_builds_a_named_adapter() -> None:
    assert groq_adapter_from_key(None) is None
    assert groq_adapter_from_key("") is None
    adapter = groq_adapter_from_key("secret", model="qwen/qwen3.6-27b")
    assert adapter is not None
    assert adapter.name == "groq:qwen/qwen3.6-27b"


def test_groq_sends_a_user_agent_and_a_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both headers are load-bearing, and one of them fails invisibly without a test.

    Groq is behind Cloudflare, which answers urllib's default `Python-urllib/3.12` with
    `403 error code: 1010`. `/plan` turns that into a rule-parser fallback and a 200, so a
    blocked deployment looks like a working one that simply preferred the regex.
    """
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float | None = None) -> _Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode())
        return _Response(_choice('{"name": "x"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    adapter = GroqAdapter(api_key="secret")
    assert adapter.complete_json(system="s", user="u") == '{"name": "x"}'

    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["authorization"] == "Bearer secret"
    assert "python-urllib" not in headers["user-agent"].lower()
    # Reasoning off, or Groq's own json_object validator rejects the <think> prefix and
    # returns an empty completion.
    assert captured["body"]["reasoning_effort"] == "none"
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_groq_inlines_the_image_as_a_data_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float | None = None) -> _Response:
        captured["body"] = json.loads(request.data.decode())
        return _Response(_choice('{"category": "canopy"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    GroqAdapter(api_key="secret").complete_json_with_image(
        system="s", user="u", image_base64="QUJD", mime_type="image/jpeg"
    )

    parts = captured["body"]["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "u"}
    assert parts[1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def test_an_empty_groq_completion_is_unavailable_not_an_empty_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`json_validate_failed`, a refusal and a length stop all arrive as `""`.

    Returned as-is it would parse as a plan with nothing in it, which is a silent wrong
    answer rather than a fallback to the rule parser.
    """
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *_, **__: _Response(_choice(""))
    )
    with pytest.raises(LLMUnavailable, match="empty completion"):
        GroqAdapter(api_key="secret").complete_json(system="s", user="u")


def test_both_keys_build_a_chain_with_groq_first() -> None:
    """Two providers exist so one retirement is a config change, not an outage."""

    class _Settings:
        groq_api_key = "g"
        groq_model = "qwen/qwen3.6-27b"
        gemini_api_key = "m"
        gemini_model = "gemini-3.6-flash"

    resolved = resolve_adapter(_Settings())
    assert isinstance(resolved, FallbackAdapter)
    assert resolved.name == "groq:qwen/qwen3.6-27b -> gemini:gemini-3.6-flash"


class _Dead:
    name = "dead:provider"

    def complete_json(self, **_: object) -> str:
        raise LLMUnavailable("401 Invalid API Key")

    def complete_json_with_image(self, **_: object) -> str:
        raise LLMUnavailable("401 Invalid API Key")


class _Live:
    name = "live:provider"

    def complete_json(self, **_: object) -> str:
        return '{"from": "text"}'

    def complete_json_with_image(self, **_: object) -> str:
        return '{"from": "vision"}'


def test_a_dead_primary_does_not_shadow_a_working_secondary() -> None:
    """The whole point of a second provider, and it was missing until measured.

    `resolve_adapter` chooses on which key is *set*, which says nothing about whether it
    works. Mid-audit the Groq key started answering `401 Invalid API Key` and the
    deployment fell back to the rule parser while a configured Gemini key sat unused.
    """
    chain = FallbackAdapter(adapters=(_Dead(), _Live()))

    assert chain.complete_json(system="s", user="u") == '{"from": "text"}'
    assert (
        chain.complete_json_with_image(
            system="s", user="u", image_base64="QQ==", mime_type="image/jpeg"
        )
        == '{"from": "vision"}'
    )


def test_the_chain_stops_at_the_first_provider_that_answers() -> None:
    chain = FallbackAdapter(adapters=(_Live(), _Dead()))

    assert chain.complete_json(system="s", user="u") == '{"from": "text"}'


def test_every_provider_failing_names_all_of_them() -> None:
    """The caller falls back to the rule parser either way; the log has to say why."""
    chain = FallbackAdapter(adapters=(_Dead(), _Dead()))

    with pytest.raises(LLMUnavailable, match="no provider answered"):
        chain.complete_json(system="s", user="u")


def test_gemini_answers_when_only_it_has_a_key() -> None:
    class _Settings:
        groq_api_key = None
        gemini_api_key = "m"
        gemini_model = "gemini-3.6-flash"

    resolved = resolve_adapter(_Settings())
    assert resolved is not None and resolved.name == "gemini:gemini-3.6-flash"


def test_no_keys_at_all_resolves_to_nothing() -> None:
    class _Settings:
        groq_api_key = None
        gemini_api_key = None

    assert resolve_adapter(_Settings()) is None
