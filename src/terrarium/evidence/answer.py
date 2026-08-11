"""Answer a question from the retrieved sections, with every citation checked to exist.

One of the four modules that may reach a model (D25), and it carries the post-check D25
requires: **every citation must resolve to a section the model was actually shown.** One
unresolvable citation rejects the whole answer.

"Shown", not "exists anywhere in the corpus", and the difference matters: the prompt says
answer from the excerpts, so a citation to a real section that was never handed over is a
citation from memory with a checkable-looking reference stapled to it — which is the
failure this file is for, wearing its best disguise.

That is the same discipline as `_numbers_are_faithful`, for the same reason. A fabricated
citation is the characteristic failure of this shape of feature, and it is worse than a
wrong answer because it comes with a reference that makes it look checked. Checking the
citation *after* the fact is a proof; asking the prompt nicely is not.

**A rejected answer is an error, not a quieter answer.** This route used to fall back to
returning the retrieved sections verbatim, which read as a graceful degradation and was
not one: "here is what the documentation says" and "a model wrote this and we threw it
away" are different events, and dressing the second as the first hid the guard firing.
`EvidenceUnavailable` names which happened, and the passages ride along on the exception
so the caller can still show them if it wants to.

`passages` stays on a *successful* answer for the other half of the argument: the claim
this feature makes is that the answer came out of the repository, and a reader can only
check that with the evidence on the same screen.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from terrarium.dsl.llm import LLMUnavailable, resolve_adapter
from terrarium.evidence.corpus import Section

logger = logging.getLogger(__name__)

# How much of each retrieved section reaches the prompt. The whole corpus is ~60k tokens
# and would fit in Gemini's context, but sending all of it makes every answer cost the
# whole corpus and gives the model 400 sections to pick a citation from — which is more
# ways to be wrong, not fewer. Six sections at 2,000 characters is the passage a person
# would have been handed.
SECTION_CHARS = 2_000

# A citation as it appears in the model's output. Deliberately the same string the corpus
# uses as an anchor, so "does it resolve" is a set membership test rather than a fuzzy match.
#
# The anchor half is a slug precisely so this pattern has an unambiguous end — see
# `corpus.slugify`. A heading with spaces in it would truncate here and a good citation
# would be rejected as fabricated.
_CITATION = re.compile(r"([\w./-]+\.md#[a-z0-9-]+)")


class Citation(BaseModel):
    """One reference, and whether it points at something real."""

    model_config = ConfigDict(frozen=True)

    anchor: str
    file: str
    heading: str


class EvidenceUnavailable(RuntimeError):
    """The question could not be answered from the documentation.

    Carries `passages` so the route can still show the evidence, and
    `rejected_citations` so a fabrication is *visible* rather than merely absent — the
    same reason `Coverage` keeps its dropped quotes.
    """

    def __init__(
        self,
        message: str,
        *,
        passages: Sequence[Section] = (),
        rejected_citations: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.passages = tuple(passages)
        self.rejected_citations = tuple(rejected_citations)


class Answer(BaseModel):
    """What `/evidence/ask` returns, and it returns this only when the guard passed.

    There is no half-answer any more: every citation below resolves to a section that
    exists, and an answer where one did not is an `EvidenceUnavailable`, not a version of
    this with a flag set.
    """

    model_config = ConfigDict(frozen=True)

    question: str
    answer: str
    citations: tuple[Citation, ...] = Field(
        description="Every citation resolves to a section that exists. Checked, not asked for"
    )
    passages: tuple[Section, ...] = Field(
        description="What was retrieved. Always present, so the answer can be checked"
    )
    source: str = Field(description="The provider chain that wrote it")


ANSWER_SYSTEM = """You answer questions about a software project from excerpts of its own \
documentation, and from nothing else.

Rules, in order of importance:
1. Answer ONLY from the excerpts. If they do not contain the answer, say so plainly. Never \
fill a gap from general knowledge about climate models, machine learning or urban planning.
2. TAG EVERY CLAIM with the anchor of the excerpt it came from, written exactly as given \
in "anchor" - for example (docs/IMPLEMENTATION_PLAN.md#D17). Copy the anchor character for \
character. An anchor you did not receive will be detected and the whole answer discarded.
3. Prefer the project's own words for anything it is careful about - a correction factor, \
a validation result, a limitation. Do not round a figure and do not soften a caveat.
4. Be direct. Two or three short paragraphs at most. No preamble, no summary of the \
question back at the user.
5. British English.

Return JSON only, with exactly this key:
{"answer": str}"""


def _prompt(question: str, passages: Sequence[Section]) -> str:
    return json.dumps(
        {
            "question": question,
            "excerpts": [
                {
                    "anchor": section.anchor,
                    "file": section.file,
                    "heading": section.heading,
                    "text": section.body[:SECTION_CHARS],
                }
                for section in passages
            ],
        },
        ensure_ascii=False,
    )


def citations_in(text: str) -> list[str]:
    """Every `file.md#heading` reference in a string, in the order they appear."""
    return _CITATION.findall(text)


def answer_question(
    question: str,
    passages: Sequence[Section],
    *,
    settings: object,
    known_anchors: set[str] | None = None,
) -> Answer:
    """Answer from `passages`, or raise `EvidenceUnavailable` saying why not.

    `known_anchors` defaults to **the passages' own anchors**, which is the strict reading
    and the right one: the model is told to answer from the excerpts, so a citation to a
    section that exists but was not shown to it is a citation from memory. Widening this
    to the whole corpus would let exactly that through — a real anchor, attached to a
    claim the evidence never made.

    Four ways it raises, and the route maps each to its own status: nothing retrieved (the
    corpus cannot address the question), no model configured, the model unreachable or
    unusable, and — the one this whole module exists for — **a citation that does not
    resolve**.
    """
    if not passages:
        raise EvidenceUnavailable(
            "Nothing in this project's documentation matches that question."
        )

    adapter = resolve_adapter(settings, task="evidence")
    if adapter is None:
        raise EvidenceUnavailable(
            "answering from the documentation needs a model. Set TERRARIUM_GEMINI_API_KEY "
            "or TERRARIUM_GROQ_API_KEY.",
            passages=passages,
        )

    try:
        raw = adapter.complete_json(system=ANSWER_SYSTEM, user=_prompt(question, passages))
        text = str(json.loads(raw)["answer"])
    except (LLMUnavailable, ValueError, KeyError, TypeError) as exc:
        raise EvidenceUnavailable(
            f"{adapter.name} could not answer ({type(exc).__name__})", passages=passages
        ) from exc

    if not text.strip():
        raise EvidenceUnavailable(f"{adapter.name} returned an empty answer", passages=passages)

    # The guard. One unresolvable citation rejects the whole answer — not the citation, the
    # answer. A partly-fabricated reference list is worse than none, because the resolvable
    # half lends the fabricated half its credibility.
    allowed = known_anchors if known_anchors is not None else {s.anchor for s in passages}
    cited = citations_in(text)
    unresolved = tuple(anchor for anchor in cited if anchor not in allowed)
    if unresolved:
        logger.warning("evidence answer cited %s, which do not exist — rejecting it", unresolved)
        raise EvidenceUnavailable(
            f"{adapter.name} cited sections that do not exist, so its whole answer was "
            "discarded. The passages it was given are below.",
            passages=passages,
            rejected_citations=unresolved,
        )

    return Answer(
        question=question,
        answer=text,
        citations=tuple(
            Citation(
                anchor=anchor,
                file=anchor.split("#", 1)[0],
                heading=anchor.split("#", 1)[1],
            )
            # Deduplicated but order-preserving: a claim cited three times is one reference.
            for anchor in dict.fromkeys(cited)
        ),
        passages=tuple(passages),
        source=adapter.name,
    )
