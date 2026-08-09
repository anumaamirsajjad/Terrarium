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
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from terrarium.dsl.explain import PlainSummary

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# Kept in step with `Settings.gemini_model`, which is what the API actually passes. This
# is the fallback for a direct caller. Both were `gemini-2.5-flash` until it started
# answering 404 "no longer available to new users" — see the note in `config.py`.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

DEFAULT_GROQ_URL = "https://api.groq.com/openai/v1"
# Text-only, which is all the planner needs. Fast and on the free tier.
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


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
        return self._generate([{"text": user}], system=system)


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
    of an outage.

    Still plain urllib, and still the right call *for this path*: the planner's request is
    one POST of one JSON body whose answer is re-validated as a `Plan` regardless of who
    produced it, so a framework would buy nothing. LangChain does now live in this module
    (D24) but only under `narrate`, where one prompt has to reach two providers and the
    output is prose rather than a schema. The two paths coexist on purpose; see D24.

    Two provider-specific details, both discovered by running it rather than reading docs:

    - **`reasoning_effort="none"`.** Groq's reasoning models prefix their answer with a
      `<think>` block, and with that block present Groq's own `json_object` validator
      rejects the generation and returns `json_validate_failed` with an empty body. So
      structured output and reasoning are mutually exclusive here, and schema-filling is
      what this seam is for.
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
    nothing at all: every route answers, and free text is parsed by the rule parser.

    Returns the concrete adapter rather than `LLMAdapter` so a caller that wants the model
    name for attribution does not have to narrow it back.
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
    falls back to the rule parser and every route still answers.

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


# --- The plain-language narrator (D24) ----------------------------------------------
#
# This is the second thing in the project that may speak to a model, and it lives here
# rather than in `dsl/explain.py` because D18 says the LLM lives in exactly one file and
# that rule did not stop being useful when the narrator was added.
#
# What it may do is narrow on purpose: **reword numbers it was handed.** It never sources
# a figure, never reaches the cube, and never decides what is worth saying. The template
# in `explain.plain_summary` decides all three, and this rewrites its prose. That is what
# makes a generative explainer defensible here when `brief_for` still refuses to be one:
# `brief_for` writes the caveats a regulator would read, and those must be structural and
# testable; this writes the dashboard's welcome mat.
#
# The guard that makes it safe is `_numbers_are_faithful`, below. It is not a prompt
# instruction - it is a post-check applied to the model's own output, and any number that
# was not in the template's version rejects the whole rewrite back to the template.

NARRATOR_SYSTEM = """You rewrite short reports about urban climate models for a general \
audience: a city councillor or a resident, not a scientist. They do not want the figures \
read back to them - they want to know what the plan would actually do, whether it is worth \
doing, and what they should do differently.

Rules, in order of importance:
1. NEVER introduce a number, percentage, quantity or unit that is not in the input. \
Copy every number EXACTLY as written - do not round, rescale, convert or combine them.
2. KEEP the input's numbers. "Many trees" and "a rough cost" are useless to somebody \
deciding whether to fund this - they need the figures. Explain what each figure MEANS in \
everyday terms, alongside the figure, never instead of it.
3. Do not add a fact, a cause, a comparison to another city, or any claim the input does \
not make. Put the input's own content into plainer, more useful words - nothing else.
4. Do not change how big or small the report says the effect is. If it calls the change \
small, it stays small. Talking a weak plan up is the worst thing you can do here.
5. Plain words. No jargon: say "ground temperature" not "land surface temperature", \
"fumes" not "particulate matter", "area" not "polygon".
6. Short sentences. British English. No exclamation marks, no salesmanship.

Return JSON only, with exactly these keys:
{{"headline": str, "points": [str, ...]}}"""

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers_in(text: str) -> set[str]:
    """Every numeral in a string, with thousands separators normalised away.

    `180,905` and `180905` are the same figure written two ways, so the comparison strips
    separators before matching - otherwise the guard would reject a rewrite for correctly
    reformatting a number it had copied faithfully.
    """
    return set(_NUMBER.findall(text.replace(",", "")))


def _numbers_are_faithful(*, source: str, rewritten: str) -> bool:
    """True when the rewrite invented no figures.

    Deliberately one-directional: the rewrite may *drop* a number (it is allowed to be
    shorter) but may not contain one the source did not. Dropping a figure costs detail;
    inventing one is the failure this whole seam is defended against, and it is the exact
    failure that kept a model out of `explain.py`.

    Strict about rounding, and that is the intended trade: a model that turns 16.7 into 17
    is a model that is editing figures, and the cheapest way to be sure it is not is to
    refuse the whole rewrite and ship the template. A refusal costs nicer prose; a missed
    edit costs a wrong number on the one screen most people actually read.

    Dropping is policed separately by `_headline_figures_survive`, because "may not invent"
    and "may not gut" are different failures needing different tests.
    """
    return _numbers_in(rewritten) <= _numbers_in(source)


def _headline_figures_survive(*, headline: str, rewritten: str) -> bool:
    """True when the rewrite kept the figures the report is actually about.

    The faithfulness guard above is one-directional, and a model handed a prompt that
    shouts NEVER INVENT A NUMBER discovers the safest way to obey it: drop every number.
    That passed every check and produced "many trees are needed to achieve this small
    change" — prose with nothing in it a person could fund, refuse or quote, which is a
    worse dashboard than the template it replaced.

    Only the *headline's* figures are required, not every figure in the report. The
    headline is what the panel is about — the cooling, the area — and a rewrite that
    editorially drops the cost line is still a good rewrite. Checked against the whole
    rewrite rather than against its headline, so moving a figure into a bullet is fine.
    """
    return _numbers_in(headline) <= _numbers_in(rewritten)


def _strip_fence(text: str) -> str:
    """Unwrap a fenced code block if the model added one despite being asked for bare JSON.

    Gemini honours a JSON mime type; Groq is asked for JSON by prompt here rather than by
    `response_format`, because LangChain's Groq binding sends that differently, and a
    fenced block is the common way it leaks through.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        if "\n" in stripped:
            stripped = stripped.split("\n", 1)[1]
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


# 8 s, against a /simulate budget of 3 s that the route measures 0.84 s warm against. The
# narrator is allowed to overrun that budget because it is the last thing to run and the
# alternative is worse prose, but it is not allowed to hang a request indefinitely.
NARRATOR_TIMEOUT_S = 8.0


def _chat_model(settings: Any) -> Any:
    """A LangChain chat model for whichever provider has a key, or `None` for neither.

    Groq first, matching `resolve_adapter` - same reasoning, and having the two disagree
    about provider order would be a confusing thing to debug at a demo.

    Imported lazily because these packages pull a non-trivial import tree, and the API
    loads this module at startup on deployments that have no key at all - where every one
    of those imports would be paid for nothing.
    """
    groq_key = getattr(settings, "groq_api_key", None)
    if groq_key:
        from langchain_groq import ChatGroq

        return ChatGroq(
            api_key=groq_key,
            model=getattr(settings, "groq_model", DEFAULT_GROQ_MODEL),
            temperature=0.0,
            timeout=NARRATOR_TIMEOUT_S,
            max_retries=0,
        )

    gemini_key = getattr(settings, "gemini_api_key", None)
    if gemini_key:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            google_api_key=gemini_key,
            model=getattr(settings, "gemini_model", DEFAULT_GEMINI_MODEL),
            temperature=0.0,
            timeout=NARRATOR_TIMEOUT_S,
            max_retries=0,
        )

    return None


def narrate(plain: PlainSummary, *, settings: Any) -> PlainSummary:
    """Reword a `PlainSummary` through a model, or return it untouched.

    **Never raises and never fails a request.** Every failure path - no key, no network, a
    refusal, malformed JSON, an invented number - returns the input unchanged, so the
    caller has nothing to handle and `/simulate` keeps answering on a laptop with the
    wi-fi off. That is the same contract `dsl.planner` has with the rule parser, and it is
    what keeps the zero-budget claim unconditional: the key buys nicer prose, not function.
    """
    model = _chat_model(settings)
    if model is None:
        return plain

    # The caveat is not sent to be rewritten and is not read back. It went round once and
    # came back as "the actual outcome may be less than predicted" — which reads like a
    # hedge but is a different claim: the template says the figure has *already* been
    # scaled down, and the rewrite quietly re-applied the correction the reader was being
    # told about. No numeral changed, so the faithfulness guard had nothing to catch. The
    # sentence is already plain, so nothing is lost by fixing it.
    source = "\n".join((plain.headline, *plain.points))

    try:
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.prompts import ChatPromptTemplate

        chain = (
            ChatPromptTemplate.from_messages(
                [("system", NARRATOR_SYSTEM), ("human", "{report}")]
            )
            | model
            | StrOutputParser()
        )
        raw = chain.invoke({"report": source})
    except Exception as exc:
        # Broad by intent. This is an optional cosmetic path behind a total fallback, and
        # the alternative is enumerating three vendors' exception trees so that a new one
        # can 500 a route which already has a perfectly good answer in hand.
        logger.warning("narrator unavailable, keeping the template: %s", type(exc).__name__)
        return plain

    try:
        payload = json.loads(_strip_fence(raw))
        headline = str(payload["headline"])
        points = tuple(str(point) for point in payload["points"])
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "narrator returned unusable JSON (%s), keeping the template", type(exc).__name__
        )
        return plain

    if not points or not headline.strip():
        logger.warning("narrator dropped a required section, keeping the template")
        return plain

    rewritten = "\n".join((headline, *points))
    if not _numbers_are_faithful(source=source, rewritten=rewritten):
        invented = sorted(_numbers_in(rewritten) - _numbers_in(source))
        logger.warning("narrator invented figures %s, keeping the template", invented)
        return plain

    if not _headline_figures_survive(headline=plain.headline, rewritten=rewritten):
        dropped = sorted(_numbers_in(plain.headline) - _numbers_in(rewritten))
        logger.warning("narrator dropped headline figures %s, keeping the template", dropped)
        return plain

    # Both attributes are absent on some bindings (langchain-core's own fakes among them),
    # so the final literal is what stops the response attributing prose to "langchain:None".
    name = getattr(model, "model_name", None) or getattr(model, "model", None) or "model"
    # `caveat` and `verdict` are absent on purpose: the model rewords the report, it does
    # not get to soften the limitation or to restate how big the change was.
    return plain.model_copy(
        update={"headline": headline, "points": points, "source": f"langchain:{name}"}
    )

