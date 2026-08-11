"""The citation guard, which is the whole reason this feature is defensible.

**A fabricated citation rejects the whole answer**, not just the citation. That is the
assertion this file exists for. A partly-fabricated reference list is worse than none: the
resolvable half lends the fabricated half its credibility, and a reader who spot-checks one
reference and finds it good stops checking.

No network. The fake adapter is the pattern from `dsl/test_llm.py`.
"""

from __future__ import annotations

import json

import pytest

from terrarium.evidence import answer as answer_module
from terrarium.evidence.answer import (
    Answer,
    EvidenceUnavailable,
    answer_question,
    citations_in,
)
from terrarium.evidence.corpus import Section

HINDCAST = Section(
    file="docs/IMPLEMENTATION_PLAN.md",
    heading="Phase 7 — hindcast",
    anchor="docs/IMPLEMENTATION_PLAN.md#phase-7-hindcast",
    body="The emulator over-predicted cooling by about 2.5x in 12 of 12 configurations.",
)
D17 = Section(
    file="docs/IMPLEMENTATION_PLAN.md",
    heading="D17",
    anchor="docs/IMPLEMENTATION_PLAN.md#d17",
    body="No LangGraph. The planner is two nodes with no cycle.",
)

PASSAGES = (HINDCAST, D17)
KNOWN = {section.anchor for section in PASSAGES}


class FakeAdapter:
    """Answers with one canned string, and counts whether it was asked at all."""

    name = "fake:test"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete_json(self, *, system: str, user: str) -> str:
        self.calls += 1
        return self.text


def _ask(reply: str, monkeypatch: pytest.MonkeyPatch) -> Answer:
    """Answer with a scripted model. `monkeypatch` restores the real resolver for us."""
    adapter = FakeAdapter(reply)
    monkeypatch.setattr(answer_module, "resolve_adapter", lambda _settings, **_kw: adapter)
    return answer_question("why 2.5x?", PASSAGES, settings=object(), known_anchors=KNOWN)


def _json(text: str) -> str:
    return json.dumps({"answer": text})


def test_citations_are_found_in_prose() -> None:
    found = citations_in(
        "It over-predicts (docs/IMPLEMENTATION_PLAN.md#phase-7-hindcast), see CLAUDE.md#scope."
    )
    assert found == ["docs/IMPLEMENTATION_PLAN.md#phase-7-hindcast", "CLAUDE.md#scope"]


def test_a_multi_word_heading_is_not_truncated() -> None:
    """The bug slugs exist to prevent. An anchor with spaces ends at the first one, so
    `#phase 7 hindcast` would be read as `#phase` — a good citation rejected as fake."""
    found = citations_in("see docs/AUDIT.md#the-2-5x-correction for the figure")
    assert found == ["docs/AUDIT.md#the-2-5x-correction"]


def test_a_real_citation_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _ask(
        _json(
            "The emulator over-predicted cooling by about 2.5x "
            "(docs/IMPLEMENTATION_PLAN.md#d17)."
        ),
        monkeypatch,
    )

    assert result.source == "fake:test"
    assert [c.anchor for c in result.citations] == ["docs/IMPLEMENTATION_PLAN.md#d17"]


def test_a_fabricated_citation_rejects_the_whole_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assertion this module exists for.

    The prose here is otherwise perfect and one of its two citations is real. The answer
    is still discarded in full — and it now *raises*, rather than coming back as the
    passages with a flag set. Those are different events and used to look identical.
    """
    with pytest.raises(EvidenceUnavailable) as excinfo:
        _ask(
            _json(
                "The emulator over-predicts (docs/IMPLEMENTATION_PLAN.md#d17), as recorded "
                "in the validation appendix (docs/VALIDATION.md#hindcast-method)."
            ),
            monkeypatch,
        )

    # The fabrication is named rather than merely absent.
    assert excinfo.value.rejected_citations == ("docs/VALIDATION.md#hindcast-method",)
    # The evidence still rides along, so the route can show what it was given.
    assert excinfo.value.passages == PASSAGES


def test_a_plausible_but_wrong_heading_is_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dangerous case: a real file, a heading that sounds exactly like one of ours."""
    with pytest.raises(EvidenceUnavailable):
        _ask(
            _json("See (docs/IMPLEMENTATION_PLAN.md#phase-7-hindcast-validation) for it."),
            monkeypatch,
        )


def test_no_key_refuses_rather_than_answering_with_the_passages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning the retrieved sections read as graceful degradation and was not one.

    "Here is what the documentation says" and "a model wrote this and we threw it away"
    are different events, and dressing the second as the first hid the guard firing.
    """
    monkeypatch.setattr(answer_module, "resolve_adapter", lambda _settings, **_kw: None)

    with pytest.raises(EvidenceUnavailable) as excinfo:
        answer_question("why 2.5x?", PASSAGES, settings=object(), known_anchors=KNOWN)

    assert "needs a model" in str(excinfo.value)
    assert excinfo.value.passages == PASSAGES


def test_malformed_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(EvidenceUnavailable):
        _ask("I'm afraid I can't do that.", monkeypatch)


def test_an_empty_answer_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(EvidenceUnavailable):
        _ask(_json("   "), monkeypatch)


def test_no_passages_says_so_without_calling_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A question the corpus cannot address must not reach a model at all: with nothing
    retrieved there is nothing to ground an answer in, and asking anyway is an invitation
    to answer from general knowledge."""
    adapter = FakeAdapter(_json("something"))
    monkeypatch.setattr(answer_module, "resolve_adapter", lambda _settings, **_kw: adapter)

    with pytest.raises(EvidenceUnavailable) as excinfo:
        answer_question("capital of Peru", (), settings=object(), known_anchors=KNOWN)

    assert adapter.calls == 0
    assert "Nothing in this project's documentation" in str(excinfo.value)


def test_repeated_citations_collapse_to_one_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _ask(
        _json(
            "It over-predicts (docs/IMPLEMENTATION_PLAN.md#d17). See also "
            "(docs/IMPLEMENTATION_PLAN.md#d17)."
        ),
        monkeypatch,
    )
    assert len(result.citations) == 1


def test_the_passages_are_always_returned_so_the_answer_can_be_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _ask(_json("Yes (docs/IMPLEMENTATION_PLAN.md#d17)."), monkeypatch)
    assert result.passages == PASSAGES
