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
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


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
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
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


def adapter_from_key(
    api_key: str | None,
    *,
    model: str = DEFAULT_GEMINI_MODEL,
    base_url: str = DEFAULT_GEMINI_URL,
) -> LLMAdapter | None:
    """Build an adapter, or `None` when no key is configured.

    `None` is the expected state, not a degraded one. The rule-based parser handles the
    phrasings a demo actually uses, so a missing key costs flexibility, not function — and
    a deployment with no key still answers `/plan`.
    """
    if not api_key:
        return None
    return GeminiAdapter(api_key=api_key, model=model, base_url=base_url)
