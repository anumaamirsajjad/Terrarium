"""BM25 ranking, and the corpus split that feeds it.

Both run against sections built in this file, plus one pass over the real repository — the
second is what catches a heading pattern this project actually uses that the parser does
not, which is a thing a synthetic corpus can never notice.
"""

from __future__ import annotations

from pathlib import Path

from terrarium.evidence.corpus import (
    Section,
    anchors,
    build_corpus,
    parse_decisions,
    split_markdown,
)
from terrarium.evidence.retrieve import Index, tokenise

REPO = Path(__file__).resolve().parents[3]

SAMPLE = """\
Some preamble prose that is comfortably longer than the minimum section length, so that
it survives the filter and can be cited as the file's top section.

## The hindcast correction

Across nine summers of real greening the emulator implied about 2.5x the cooling that
actually materialised, so every modelled cooling is divided by that factor before it is
offered as an expectation.

### A heading with nothing under it

## Air dispersion

The seasonal kernel replaced the plume because every window in the cube is a season and a
single-direction plume answers a question about one hour instead.
"""

# A register row is one line by construction — the parser matches `^|...|$` — so these
# stay long. Wrapping them would test a table shape the real file does not have.
REGISTER = (
    "| # | Question | Decision |\n"
    "|---|---|---|\n"
    "| D17 | Agent framework | **No LangGraph.** It is a graph runtime and the planner "
    "is two nodes with no branching, no cycles and no state to checkpoint at all. |\n"
    "| D18 | Where the LLM may live | **One module.** Everything it returns is "
    "re-validated as a Plan before a core can see it, which is the safety argument. |\n"
    "| D99 | Too short | tiny |\n"
)


def test_sections_split_at_headings_and_keep_the_preamble() -> None:
    sections = split_markdown(SAMPLE, file="docs/X.md")
    headings = [section.heading for section in sections]

    assert headings[0] == "(top)"
    assert "The hindcast correction" in headings
    # An empty heading is a divider, not a section: it retrieves badly and cites worse.
    assert "A heading with nothing under it" not in headings


def test_anchors_are_followable() -> None:
    section = split_markdown(SAMPLE, file="docs/X.md")[1]
    assert section.anchor == "docs/X.md#the-hindcast-correction"


def test_decisions_are_their_own_unit() -> None:
    """Why-questions land on the register far more often than on prose, and a D-entry
    buried in a 400-line table is not something a reader can be pointed at."""
    decisions = parse_decisions(REGISTER, file="docs/PLAN.md")
    anchors_found = {section.anchor for section in decisions}

    assert "docs/PLAN.md#d17" in anchors_found
    assert "docs/PLAN.md#d18" in anchors_found
    # A stub row is skipped rather than indexed as a citable claim.
    assert "docs/PLAN.md#d99" not in anchors_found
    assert "LangGraph" in next(s for s in decisions if s.anchor.endswith("#d17")).body


def test_tokeniser_keeps_the_identifiers_this_corpus_is_about() -> None:
    """`pm2.5`, `lst_c` and `d17` are single terms here. Splitting them turns the most
    specific query in this corpus into the least specific one."""
    tokens = tokenise("Why does PM2.5 use lst_c in D17 and ST_B10?")
    assert "pm2.5" in tokens
    assert "lst_c" in tokens
    assert "d17" in tokens
    assert "st_b10" in tokens
    # Stopwords, including the one that is a stopword only in this corpus.
    assert "why" not in tokens and "the" not in tokens


def _index() -> Index:
    return Index(
        [*split_markdown(SAMPLE, file="docs/X.md"), *parse_decisions(REGISTER, file="docs/PLAN.md")]
    )


def test_a_question_lands_on_the_section_that_answers_it() -> None:
    hits = _index().search("why is the cooling divided by 2.5?")
    assert hits
    assert hits[0][0].heading == "The hindcast correction"


def test_a_why_question_lands_on_the_decision_register() -> None:
    hits = _index().search("why no LangGraph for the agent framework?")
    assert hits
    assert hits[0][0].anchor == "docs/PLAN.md#d17"


def test_a_question_with_nothing_in_the_corpus_retrieves_nothing() -> None:
    """Nothing is a real answer. Returning the longest sections instead would let the
    answerer produce something confident about a question the corpus cannot address."""
    assert _index().search("what is the capital of Peru") == []
    assert _index().search("") == []


def test_scores_are_never_negative() -> None:
    """BM25's IDF goes negative for a term in more than half the corpus without the +1,
    and a document is then *penalised* for containing a word the user asked about."""
    for _, score in _index().search("the cooling the the"):
        assert score > 0


# --- against the real repository -----------------------------------------------------


def test_the_real_corpus_parses_into_something_worth_searching() -> None:
    sections = build_corpus(REPO)

    assert len(sections) > 200, f"only {len(sections)} sections from the real docs"
    files = {section.file for section in sections}
    assert "docs/IMPLEMENTATION_PLAN.md" in files
    assert "CLAUDE.md" in files
    # The register really did parse, not just the prose around it.
    assert "docs/IMPLEMENTATION_PLAN.md#d17" in anchors(sections)
    assert "docs/IMPLEMENTATION_PLAN.md#d25" in anchors(sections)


def test_the_2_5x_question_lands_on_the_hindcast_in_the_real_docs() -> None:
    """The plan's own worked example. If this stops finding the hindcast material, the
    feature has quietly stopped working on the corpus it exists for."""
    hits = Index(build_corpus(REPO)).search("why is the cooling divided by 2.5?", limit=6)

    assert hits
    joined = " ".join(section.text.lower() for section, _ in hits)
    assert "hindcast" in joined
    assert "2.5" in joined


def test_every_section_has_a_unique_followable_anchor() -> None:
    """Uniqueness is not cosmetic. `## Delivered` appears in several phases of the plan,
    and if they collapsed to one anchor a citation would resolve to whichever was indexed
    first — pointing at the wrong evidence while passing the guard."""
    sections = build_corpus(REPO)
    seen = [section.anchor for section in sections]

    assert len(set(seen)) == len(seen)
    for section in sections:
        assert section.anchor.startswith(f"{section.file}#")
        # No whitespace, ever: an anchor with a space in it truncates in `citations_in`
        # and a perfectly good citation is then rejected as fabricated.
        assert " " not in section.anchor


def test_sections_are_ordered_deterministically() -> None:
    """An answer whose citations reorder between runs is one nobody can check twice."""
    first = [section.anchor for section in build_corpus(REPO)]
    second = [section.anchor for section in build_corpus(REPO)]
    assert first == second


def test_an_unreadable_file_does_not_fail_the_corpus(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "good.md").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "docs" / "binary.md").write_bytes(b"\xff\xfe\x00\x00 not text at all")

    sections = build_corpus(tmp_path)
    assert any(section.file == "docs/good.md" for section in sections)


def test_sections_carry_their_body_not_just_their_heading() -> None:
    """Guard the guard: a splitter that returned empty bodies would index nothing and
    every search above would still pass by matching headings alone."""
    for section in build_corpus(REPO)[:50]:
        assert isinstance(section, Section)
        assert len(section.body) >= 80
