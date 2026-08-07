"""The one place a language model is spoken to.

Everything else in `dsl/` is arithmetic and grammar and runs offline. This module is the
single seam where a provider exists, and it is deliberately thin: one method, strings in
and a string out. The planner treats it as optional and the explainer never calls it at
all, so **the product works with no key, no model and no network** — which is the fallback
the roadmap names, and also the only way the test suite can honour the no-network rule.

Two rules govern this file:

- **The provider never leaks past it.** `LLMAdapter` is what `dsl.planner` depends on.
  Swapping Gemini for anything else is a new class here and nothing anywhere else (D13).
- **Network I/O is confined, not licensed.** `ingest/` is the only layer that may fetch
  *data for the cube*; this is a different kind of call — a request the user just made,
  in Layer 3, with nothing downstream trusting its output as a measurement. It is isolated
  in one module for the same reason `ingest/` is, and everything it returns goes through
  `Plan` validation before it can touch a core.

Free tier by default: Google AI Studio, ~1,500 requests/day, no card (D13).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# Kept in step with `Settings.gemini_model`, which is what the API actually passes. This
# is the fallback for a direct caller. Both were `gemini-2.5-flash` until it started
# answering 404 "no longer available to new users" — see the note in `config.py`.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

DEFAULT_GROQ_URL = "https://api.groq.com/openai/v1"
# The only vision-capable model on Groq's free tier: the other fourteen are text, audio or
# prompt-guard. Since `dsl.observe` needs vision and the planner does not, one model that
# does both keeps this adapter the same shape as Gemini's rather than sprouting a second
# model field that only one code path reads.
DEFAULT_GROQ_MODEL = "qwen/qwen3.6-27b"


class LLMUnavailable(RuntimeError):
    """The model could not be reached or did not answer usefully.

    Always recoverable: every caller falls back to the deterministic path, so this is a
    reason to log and continue, never a reason to fail a request.
    """


class LLMAdapter(Protocol):
    """Structural type for anything that can answer a prompt with JSON text."""

    @property
    def name(self) -> str:
        """Provider and model, for attribution in the response."""
        ...

    def complete_json(self, *, system: str, user: str) -> str:
        """Return the model's raw text. Raises `LLMUnavailable` on any failure."""
        ...


class VisionAdapter(Protocol):
    """The same seam, with an image attached.

    Separate from `LLMAdapter` because the two capabilities are separable: a text-only
    provider can serve the planner and not the photo reader, and typing them as one would
    make `dsl.observe` accept adapters that cannot do the job. Gemini implements both, and
    the free tier is multimodal, which is why Phase 11 costs nothing extra over Phase 10.
    """

    @property
    def name(self) -> str: ...

    def complete_json_with_image(
        self, *, system: str, user: str, image_base64: str, mime_type: str
    ) -> str: ...


@dataclass(frozen=True)
class GeminiAdapter:
    """Google AI Studio's `generateContent`, over plain urllib.

    urllib rather than a client library because the request is one POST of one JSON body
    and the project already pays that price nowhere else — `ingest/` talks to Overpass and
    Open-Meteo exactly this way, and adding an SDK for one endpoint would put a dependency
    in the tree that only the optional path uses.

    `temperature=0` and `responseMimeType=application/json`: the model is being asked to
    fill in a schema, not to write. Structured output is what makes a Pydantic validation
    on the far side meaningful rather than a parser fighting prose.
    """

    api_key: str
    model: str = DEFAULT_GEMINI_MODEL
    base_url: str = DEFAULT_GEMINI_URL
    timeout_s: float = 20.0

    @property
    def name(self) -> str:
        return f"gemini:{self.model}"

    def complete_json(self, *, system: str, user: str) -> str:
        return self._generate([{"text": user}], system=system)

    def complete_json_with_image(
        self, *, system: str, user: str, image_base64: str, mime_type: str
    ) -> str:
        """The same call with an inline image part.

        Inline rather than the Files API: a citizen photo is a few hundred kB, it is used
        once, and uploading it first would mean two round trips and a resource on Google's
        side that this project would then own the lifecycle of.
        """
        return self._generate(
            [
                {"text": user},
                {"inlineData": {"mimeType": mime_type, "data": image_base64}},
            ],
            system=system,
        )

    def _generate(self, parts: list[dict[str, Any]], *, system: str) -> str:
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            # The key is in the URL, so never let the provider's message reach a client
            # verbatim; `str(exc)` on an HTTPError includes the request line.
            logger.warning("Gemini call failed: %s", type(exc).__name__)
            raise LLMUnavailable(f"{self.name} did not answer ({type(exc).__name__})") from exc

        try:
            return str(body["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            # A blocked or empty candidate has this shape too, which is why the reason is
            # not "bad JSON": the call succeeded and the model declined to answer.
            raise LLMUnavailable(f"{self.name} returned no usable candidate") from exc


@dataclass(frozen=True)
class GroqAdapter:
    """Groq's OpenAI-compatible `chat/completions`, over the same plain urllib.

    A second provider rather than a replacement, because the failure that prompted it was
    a *model retirement*, not a bad provider: `gemini-2.5-flash` kept appearing in the
    model list and kept advertising `generateContent` while answering 404 to any key issued
    after it was withdrawn. Two providers means that failure costs a config change instead
    of an outage on the one route with no offline fallback.

    Deliberately not an SDK and deliberately not LangChain (D17, D18). The request is one
    POST of one JSON body; `openai` or `langchain-groq` would put a dependency tree in
    front of it whose only user is the optional path.

    Two provider-specific details, both discovered by running it rather than reading docs:

    - **`reasoning_effort="none"`.** The vision model is a reasoning model and prefixes its
      answer with a `<think>` block. With that block present Groq's own `json_object`
      validator rejects the generation and returns `json_validate_failed` with an empty
      body — so structured output and reasoning are mutually exclusive here, and the
      schema-filling is what matters.
    - **The key travels in a header**, not the URL as Gemini's does. That makes provider
      error text safe to log, though it is still not returned to a client.
    - **An explicit `User-Agent` is required.** Groq sits behind Cloudflare, which rejects
      urllib's default `Python-urllib/3.12` with `403 error code: 1010` — a browser-
      signature ban, not an auth or quota failure. Any non-default value passes. This is
      worth a line of comment because of how it fails: `/plan` catches `LLMUnavailable` and
      falls back to the rule parser, so a blocked deployment answers **200 with
      `source: "rules"`** forever and reads as "the model was no better than the regex"
      rather than "the model was never reached".
    """

    api_key: str
    model: str = DEFAULT_GROQ_MODEL
    base_url: str = DEFAULT_GROQ_URL
    timeout_s: float = 30.0
    # Anything but urllib's default; see the Cloudflare note above.
    user_agent: str = "terrarium/0.1"

    @property
    def name(self) -> str:
        return f"groq:{self.model}"

    def complete_json(self, *, system: str, user: str) -> str:
        return self._chat(user, system=system)

    def complete_json_with_image(
        self, *, system: str, user: str, image_base64: str, mime_type: str
    ) -> str:
        """The same call with the photo inlined as a data URI, per the OpenAI content parts."""
        return self._chat(
            [
                {"type": "text", "text": user},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                },
            ],
            system=system,
        )

    def _chat(self, content: Any, *, system: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.0,
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("Groq call failed: %s", type(exc).__name__)
            raise LLMUnavailable(f"{self.name} did not answer ({type(exc).__name__})") from exc

        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable(f"{self.name} returned no usable choice") from exc
        if not text:
            # A refusal, a length stop, or a `json_validate_failed` all land here as an
            # empty string rather than an error, and an empty string parses as no plan.
            raise LLMUnavailable(f"{self.name} returned an empty completion")
        return str(text)


def adapter_from_key(
    api_key: str | None,
    *,
    model: str = DEFAULT_GEMINI_MODEL,
    base_url: str = DEFAULT_GEMINI_URL,
) -> GeminiAdapter | None:
    """Build an adapter, or `None` when no key is configured.

    `None` is the expected state, not a degraded one. The rule-based parser handles the
    phrasings a demo actually uses, so a missing key costs flexibility, not function — and
    a deployment with no key still answers `/plan`. The one thing it does cost is
    `/observations`, which has no offline reading of a photograph to fall back to.

    Returns the concrete adapter rather than `LLMAdapter`, because Gemini satisfies both
    that protocol and `VisionAdapter`, and narrowing the return type here would make the
    photo path unable to use the same construction.
    """
    if not api_key:
        return None
    return GeminiAdapter(api_key=api_key, model=model, base_url=base_url)


def groq_adapter_from_key(
    api_key: str | None,
    *,
    model: str = DEFAULT_GROQ_MODEL,
    base_url: str = DEFAULT_GROQ_URL,
) -> GroqAdapter | None:
    """The Groq counterpart of `adapter_from_key`. `None` when no key is configured."""
    if not api_key:
        return None
    return GroqAdapter(api_key=api_key, model=model, base_url=base_url)


@dataclass(frozen=True)
class FallbackAdapter:
    """Try each provider in order, moving on when one is unavailable.

    Without this, two providers are decorative. `resolve_adapter` can only choose on which
    *key is set*, and a key being set says nothing about whether it works — so a dead
    primary key shadows a perfectly good secondary one, and the arrangement fails exactly
    where it was supposed to help. That is not hypothetical: during this audit the Groq key
    began answering `401 Invalid API Key` mid-session, and the deployment fell back to the
    rule parser while a configured Gemini key sat unused behind it.

    Only `LLMUnavailable` is caught, which is the recoverable failure by construction —
    unreachable, refused, empty, malformed. A `ValueError` from the caller's own parsing is
    not a provider problem and is left alone.

    `name` reports the whole chain rather than the primary, because it is what the response
    attributes the answer to, and naming a provider that did not answer is worse than
    naming two.
    """

    adapters: tuple[Any, ...]

    @property
    def name(self) -> str:
        return " -> ".join(adapter.name for adapter in self.adapters)

    def complete_json(self, *, system: str, user: str) -> str:
        return self._first(lambda a: a.complete_json(system=system, user=user))

    def complete_json_with_image(
        self, *, system: str, user: str, image_base64: str, mime_type: str
    ) -> str:
        return self._first(
            lambda a: a.complete_json_with_image(
                system=system, user=user, image_base64=image_base64, mime_type=mime_type
            )
        )

    def _first(self, call: Any) -> str:
        failures: list[str] = []
        for adapter in self.adapters:
            try:
                return str(call(adapter))
            except LLMUnavailable as exc:
                logger.warning("%s unavailable, trying the next provider: %s", adapter.name, exc)
                failures.append(f"{adapter.name}: {exc}")
        raise LLMUnavailable(f"no provider answered ({'; '.join(failures)})")


def resolve_adapter(settings: Any) -> Any:
    """Whichever provider this deployment has a key for, or `None` for neither.

    **Groq first**, for one reason worth stating: it is the provider whose free tier needs
    no card *and* whose current model is not scheduled for withdrawal. Gemini stays as the
    second choice rather than being deleted, because it is verified working and a single
    provider is exactly the arrangement that produced the 404 this function exists to
    survive.

    `None` remains the expected state. Both keys unset is a working deployment: the planner
    falls back to the rule parser and only `/observations` refuses, which it already
    documents.

    Typed against `Any` rather than importing `Settings`, so this module stays a leaf that
    `config` can be imported *by* without a cycle — the same reason `config` spells the
    model names as plain strings instead of importing them from here.
    """
    configured = [
        adapter
        for adapter in (
            groq_adapter_from_key(
                getattr(settings, "groq_api_key", None),
                model=getattr(settings, "groq_model", DEFAULT_GROQ_MODEL),
            ),
            adapter_from_key(
                getattr(settings, "gemini_api_key", None),
                model=getattr(settings, "gemini_model", DEFAULT_GEMINI_MODEL),
            ),
        )
        if adapter is not None
    ]
    if not configured:
        return None
    if len(configured) == 1:
        return configured[0]
    return FallbackAdapter(adapters=tuple(configured))
