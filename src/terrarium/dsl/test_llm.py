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
from langchain_core.language_models import FakeListChatModel

from terrarium.dsl import llm
from terrarium.dsl.explain import PlainSummary
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



class _Live:
    name = "live:provider"

    def complete_json(self, **_: object) -> str:
        return '{"from": "text"}'



def test_a_dead_primary_does_not_shadow_a_working_secondary() -> None:
    """The whole point of a second provider, and it was missing until measured.

    `resolve_adapter` chooses on which key is *set*, which says nothing about whether it
    works. Mid-audit the Groq key started answering `401 Invalid API Key` and the
    deployment fell back to the rule parser while a configured Gemini key sat unused.
    """
    chain = FallbackAdapter(adapters=(_Dead(), _Live()))

    assert chain.complete_json(system="s", user="u") == '{"from": "text"}'


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


# --- The plain-language narrator (D21) ----------------------------------------------
#
# The guard, not the prose, is what these cover. Whether the model writes a nice sentence
# is not testable offline and does not matter; whether a model that invents a figure can
# get that figure onto the dashboard is both, and is the only reason this seam was
# allowed to exist at all.


class _Exploding(FakeListChatModel):
    """A provider that fails mid-call, which is the common real failure.

    Subclasses langchain-core's own fake rather than a hand-rolled object, because a
    hand-rolled one is not a `Runnable` and `prompt | model | parser` refuses to build
    with it - so the test would pass for the wrong reason, never having exercised the
    chain at all.
    """

    def _call(self, *_args: Any, **_kwargs: Any) -> str:
        # `_call` rather than `invoke`: this is where a real provider's transport error
        # surfaces, so failing here exercises the same path a dead network would.
        raise RuntimeError("provider on fire")


def _plain() -> PlainSummary:
    return PlainSummary(
        verdict="small",
        headline="Planting across 16.7 km2 would cool the ground about 0.16 degC.",
        points=("About 6.2 million people live here.", "Rough cost: about $2.7 million."),
        caveat="This is a model, not a measurement.",
    )


def _narrate_with(monkeypatch: pytest.MonkeyPatch, text: str) -> PlainSummary:
    """Run `narrate` against a real LCEL chain whose model answers with `text`."""
    model = FakeListChatModel(responses=[text])
    monkeypatch.setattr(llm, "chat_model", lambda _settings, **_kw: model)
    return llm.narrate(_plain(), settings=object())


def _translate_with(monkeypatch: pytest.MonkeyPatch, text: str) -> PlainSummary:
    """Run `translate` into Urdu against a chain whose model answers with `text`."""
    model = FakeListChatModel(responses=[text])
    monkeypatch.setattr(llm, "chat_model", lambda _settings, **_kw: model)
    return llm.translate(_plain(), language="ur", settings=object())


def test_no_key_keeps_the_narrator_s_template() -> None:
    """`narrate` still degrades silently, and correctly so: it is a cosmetic rewrite of
    prose the reader could already read, so the template costs them nothing they asked
    for. `translate` is the opposite case and is tested below."""
    plain = _plain()

    # `chat_model` returns None for a settings object with no keys on it at all.
    assert llm.narrate(plain, settings=object()) is plain


def test_no_key_makes_a_translation_fail_rather_than_answer_in_english() -> None:
    """The asymmetry with `narrate` above is the whole point.

    A caller that asked for Urdu and silently got English cannot tell that from a
    working translation, from an unsupported language, or from a translation that
    invented a figure and was correctly thrown away. All four used to look identical.
    """
    with pytest.raises(llm.LLMUnavailable):
        llm.translate(_plain(), language="ur", settings=object())


def test_a_faithful_rewrite_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _narrate_with(
        monkeypatch,
        json.dumps(
            {
                "headline": "Planting over 16.7 km2 cools the ground by roughly 0.16 degC.",
                "points": ["Around 6.2 million residents live here."],
                "caveat": "A model estimate, not a measurement.",
            }
        ),
    )

    assert result.source.startswith("langchain:")
    assert "16.7" in result.headline


def test_an_invented_figure_rejects_the_whole_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure this seam exists to stop.

    A rewrite that quietly upgrades 0.16 degC to 1.6 degC is exactly the class of error
    that kept a model out of `explain.py`, and it must not reach the dashboard - not even
    with the rest of the rewrite intact, because a partly-trustworthy report is one
    somebody has to check line by line.
    """
    template = _plain()
    result = _narrate_with(
        monkeypatch,
        json.dumps(
            {
                "headline": "Planting over 16.7 km2 cools the ground by a full 1.6 degC.",
                "points": ["Around 6.2 million residents live here."],
                "caveat": "A model estimate, not a measurement.",
            }
        ),
    )

    assert result == template
    assert result.source == "template"


def test_rounding_a_figure_counts_as_inventing_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """16.7 -> 17 is an edit to a number, and the guard is deliberately strict about it."""
    result = _narrate_with(
        monkeypatch,
        json.dumps(
            {
                "headline": "Planting over 17 km2 cools the ground about 0.16 degC.",
                "points": ["About 6.2 million people live here."],
                "caveat": "A model estimate.",
            }
        ),
    )

    assert result.source == "template"


def test_dropping_a_secondary_figure_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A shorter report is fine. The cost line is editorial; the headline is not."""
    result = _narrate_with(
        monkeypatch,
        json.dumps(
            {
                "headline": "Planting over 16.7 km2 cools the ground about 0.16 degC.",
                "points": ["Millions of people live nearby."],
            }
        ),
    )

    assert result.source.startswith("langchain:")


def test_dropping_the_headline_figures_keeps_the_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure a real Groq call produced on the first run.

    Told loudly enough never to invent a number, a model finds the safest way to comply:
    drop every number. "Planting cools the ground a little" passes the faithfulness guard
    and is worthless to somebody deciding whether to fund it, so it must lose to the
    template rather than replace it.
    """
    result = _narrate_with(
        monkeypatch,
        json.dumps(
            {
                "headline": "Planting here cools the ground a little.",
                "points": ["Millions of people live nearby."],
            }
        ),
    )

    assert result.source == "template"


def test_the_caveat_is_never_the_model_s_to_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observed: "already scaled down" came back as "may be less than predicted".

    That is a different claim, and no numeral changed, so the faithfulness guard had
    nothing to catch. The caveat is not sent and not read back.
    """
    template = _plain()
    result = _narrate_with(
        monkeypatch,
        json.dumps(
            {
                "headline": "Planting over 16.7 km2 cools the ground about 0.16 degC.",
                "points": ["About 6.2 million people live here."],
                "caveat": "Results may be a little lower in practice.",
            }
        ),
    )

    assert result.source.startswith("langchain:")
    assert result.caveat == template.caveat


def test_thousands_separators_do_not_count_as_a_new_number() -> None:
    """`180,905` and `180905` are one figure written two ways, not two figures."""
    assert llm._numbers_are_faithful(source="180905 trees", rewritten="180,905 trees")


def test_malformed_json_keeps_the_template(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _narrate_with(monkeypatch, "I'm afraid I can't do that.").source == "template"


def test_a_fenced_block_is_unwrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Groq is asked for JSON by prompt, so a fence is the common way it leaks through."""
    body = json.dumps(
        {
            "headline": "Planting over 16.7 km2 cools the ground about 0.16 degC.",
            "points": ["About 6.2 million people live here."],
            "caveat": "A model estimate.",
        }
    )

    assert _narrate_with(monkeypatch, f"```json\n{body}\n```").source.startswith("langchain:")


def test_a_dropped_section_keeps_the_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """A report with no findings in it is not a shorter report, it is an empty one."""
    result = _narrate_with(
        monkeypatch,
        json.dumps({"headline": "Planting cools things.", "points": []}),
    )

    assert result.source == "template"


def test_a_provider_that_raises_keeps_the_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """`narrate` never raises: a dead provider costs prose, never a request.

    The canned response is a *valid, faithful* rewrite, so this test can only pass because
    the exception fired. Given an unparseable string instead it would land on the template
    either way and pass without ever testing the thing it is named for.
    """
    faithful = json.dumps(
        {
            "headline": "Planting over 16.7 km2 cools the ground about 0.16 degC.",
            "points": ["About 6.2 million people live here."],
            "caveat": "A model estimate.",
        }
    )
    assert _narrate_with(monkeypatch, faithful).source.startswith("langchain:")

    monkeypatch.setattr(
        llm, "chat_model", lambda _settings, **_kw: _Exploding(responses=[faithful])
    )

    assert llm.narrate(_plain(), settings=object()).source == "template"


# --- Urdu (Phase C) ------------------------------------------------------------------
#
# The guard has to survive a change of script, and the reason it does is one shared digit
# fold. Without it ۵۰۰۰ matches no `\d`, an Urdu rewrite reads as containing no numbers at
# all, every translation passes vacuously, and `_numbers_are_faithful` is a decoration.


def test_urdu_digits_fold_to_ascii_for_the_guard() -> None:
    """۵۰۰۰ and 5000 are the same figure to the check. One table, in one module."""
    assert llm.fold_digits("۵۰۰۰ درخت") == "5000 درخت"
    assert llm._numbers_in("۱۶.۷ مربع کلومیٹر") == {"16.7"}
    # And the planner's parser reads the same table, rather than carrying a second one.
    from terrarium.dsl.planner import parse_rules

    parsed = parse_rules("۵۰۰۰ درخت لگائیں")
    assert parsed.plan.actions[0].tree_count == 5000  # type: ignore[union-attr]


def test_a_faithful_urdu_translation_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _translate_with(
        monkeypatch,
        json.dumps(
            {
                "headline": "16.7 مربع کلومیٹر پر شجرکاری زمین کو تقریباً 0.16 degC ٹھنڈا کرے گی۔",
                "points": ["یہاں تقریباً 6.2 ملین لوگ رہتے ہیں۔"],
            }
        ),
    )

    assert result.source.endswith(":ur")
    assert "16.7" in result.headline


def test_an_urdu_translation_written_in_urdu_digits_still_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fold works in the accepting direction too, not only the rejecting one.

    A translator that renders 16.7 as ۱۶.۷ has copied the figure faithfully, and refusing
    it would push every Urdu brief back to English for doing its job properly.
    """
    result = _translate_with(
        monkeypatch,
        json.dumps(
            {
                "headline": "۱۶.۷ مربع کلومیٹر پر شجرکاری زمین کو تقریباً ۰.۱۶ degC ٹھنڈا کرے گی۔",
                "points": ["یہاں تقریباً ۶.۲ ملین لوگ رہتے ہیں۔"],
            }
        ),
    )

    assert result.source.endswith(":ur")


def test_an_invented_figure_rejects_an_urdu_translation_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of folding the digits. Written in Eastern Arabic-Indic numerals,
    ۱.۶ is a tenfold overstatement of 0.16 — and before the fold it was invisible.

    The guard is unchanged; what changed is that it now raises instead of quietly
    handing back English, so a caught fabrication is visible to the caller.
    """
    with pytest.raises(llm.LLMUnavailable):
        _translate_with(
            monkeypatch,
            json.dumps(
                {
                    "headline": "۱۶.۷ مربع کلومیٹر پر شجرکاری زمین کو پورے ۱.۶ degC ٹھنڈا کرے گی۔",
                    "points": ["یہاں تقریباً ۶.۲ ملین لوگ رہتے ہیں۔"],
                }
            ),
        )


def test_an_urdu_translation_that_drops_the_headline_figures_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model told loudly never to invent a number complies by dropping every number.
    That failure crosses languages unchanged, so the second guard does too."""
    with pytest.raises(llm.LLMUnavailable):
        _translate_with(
            monkeypatch,
            json.dumps(
                {
                    "headline": "شجرکاری سے زمین کچھ ٹھنڈی ہو گی۔",
                    "points": ["یہاں بہت سے لوگ رہتے ہیں۔"],
                }
            ),
        )


def test_the_caveat_stays_english_and_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one sentence a model has already damaged once by rewriting it.

    A translation is a rewrite with more room to drift, not less, and a caveat that has
    quietly become a hedge is worse than a caveat in the wrong language.
    """
    result = _translate_with(
        monkeypatch,
        json.dumps(
            {
                "headline": "16.7 مربع کلومیٹر پر شجرکاری زمین کو تقریباً 0.16 degC ٹھنڈا کرے گی۔",
                "points": ["یہاں تقریباً 6.2 ملین لوگ رہتے ہیں۔"],
                "caveat": "نتیجہ توقع سے کم ہو سکتا ہے۔",
            }
        ),
    )

    assert result.caveat == _plain().caveat
    assert result.verdict == _plain().verdict


def test_english_costs_nothing_and_an_unsupported_language_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'en' is not a translation and never was, so it never reaches a model. Anything
    else this cannot do is a request that cannot be honoured, and says which."""
    plain = _plain()
    monkeypatch.setattr(
        llm, "chat_model", lambda _settings, **_kw: pytest.fail("should not be reached")
    )

    assert llm.translate(plain, language="en", settings=object()) is plain

    with pytest.raises(llm.LLMUnavailable, match="no translation available"):
        llm.translate(plain, language="fr", settings=object())


def test_notes_translate_all_or_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A list half in each language reads as a bug; a silent English one answers a
    different question. So a short reply raises rather than doing either."""
    notes = ("This plan touches traffic only.", "Planting moves air quality by ~0.0003 ug/m3.")

    short = FakeListChatModel(responses=[json.dumps({"notes": ["صرف ٹریفک۔"]})])
    monkeypatch.setattr(llm, "chat_model", lambda _settings, **_kw: short)
    with pytest.raises(llm.LLMUnavailable, match="1 of 2 notes"):
        llm.translate_lines(notes, language="ur", settings=object())

    good = FakeListChatModel(
        responses=[json.dumps({"notes": ["صرف ٹریفک۔", "شجرکاری ~0.0003 ug/m3 بدلتی ہے۔"]})]
    )
    monkeypatch.setattr(llm, "chat_model", lambda _settings, **_kw: good)
    translated = llm.translate_lines(notes, language="ur", settings=object())
    assert translated != notes and len(translated) == 2


def test_empty_notes_need_no_model() -> None:
    """Nothing to translate is not a failure. `/plan` routinely returns no notes."""
    assert llm.translate_lines((), language="ur", settings=object()) == ()

