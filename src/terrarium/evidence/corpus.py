"""The repository's markdown, split into citable sections.

Two units, because the corpus really has two shapes:

- **A section** is a heading and the prose under it, addressed as `file#heading`. That is
  a citation a reader can follow with ctrl-F, which is the whole reason the anchor is the
  heading text rather than a chunk index.
- **A decision** is one row of the register in `IMPLEMENTATION_PLAN.md`. *Why* questions
  land on decisions far more often than on prose — "why no LangGraph", "why is the LLM
  optional", "why person-degrees" — and a D-entry is the natural unit of retrieval for
  them, so it is parsed out rather than left inside a 400-line table nobody can quote.

File I/O lives here rather than in `retrieve.py` or `answer.py` so those two stay pure and
testable against a corpus built in memory.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# What gets indexed, in the order a reader should prefer them. `docs/` plus the two
# root files that are genuinely documentation; nothing generated, nothing under `data/`.
CORPUS_GLOBS: tuple[str, ...] = ("docs/*.md", "CLAUDE.md", "README.md", "NOTES.md")

# Sections shorter than this are headings with nothing under them — a table of contents
# entry or a divider. They retrieve badly and cite worse.
MIN_SECTION_CHARS = 80

_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.M)
# One row of the decisions register: `| D17 | Agent framework | **No LangGraph.** … |`
_DECISION_ROW = re.compile(r"^\|\s*(D\d+)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$", re.M)

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(heading: str) -> str:
    """A GitHub-style heading anchor: lowercase, words joined by hyphens.

    **An anchor must contain no whitespace**, and that is a hard requirement rather than a
    convention. A citation appears inside the model's prose as `(docs/AUDIT.md#the-2-5x-
    correction)`, and the guard finds it with a regex — a heading with spaces in it has no
    reliable end, so `docs/X.md#Phase 7 — hindcast` truncates at the first space and a
    perfectly good citation is rejected as fabricated. Slugs also happen to be what GitHub
    itself uses, so a citation is a link somebody can follow.
    """
    return _NOT_SLUG.sub("-", heading.lower()).strip("-") or "section"


def _unique(anchor: str, seen: set[str]) -> str:
    """Disambiguate a repeated heading the way GitHub does: `-1`, `-2`, and so on.

    Not cosmetic. `## Delivered` appears in several phases of the plan, and without this
    they collapse to one anchor — so a citation resolves to whichever section happened to
    be indexed first, which is a citation that points at the wrong evidence while passing
    the guard.
    """
    if anchor not in seen:
        seen.add(anchor)
        return anchor
    suffix = 1
    while f"{anchor}-{suffix}" in seen:
        suffix += 1
    seen.add(f"{anchor}-{suffix}")
    return f"{anchor}-{suffix}"


class Section(BaseModel):
    """One heading's worth of prose, and where to find it again."""

    model_config = ConfigDict(frozen=True)

    file: str = Field(description="Repo-relative path, e.g. 'docs/AUDIT.md'")
    heading: str
    # `file#heading`. What a citation must match, and what the guard resolves against.
    anchor: str
    body: str
    # Heading depth, or 0 for a decision row. Used only to break retrieval ties toward the
    # more specific section, which is almost always the more useful answer.
    level: int = 0

    @property
    def text(self) -> str:
        """Heading and body together — what gets indexed and what gets scored."""
        return f"{self.heading}\n{self.body}"


def split_markdown(text: str, *, file: str) -> list[Section]:
    """Split one markdown document at its headings.

    Prose before the first heading is kept under a synthetic `(top)` heading rather than
    discarded: several files in this repo put their most quotable paragraph in the first
    twenty lines, above any `##`.
    """
    sections: list[Section] = []
    matches = list(_HEADING.finditer(text))
    seen: set[str] = set()

    preamble = text[: matches[0].start()] if matches else text
    if len(preamble.strip()) >= MIN_SECTION_CHARS:
        sections.append(
            Section(
                file=file,
                heading="(top)",
                anchor=_unique(f"{file}#top", seen),
                body=preamble.strip(),
            )
        )

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if len(body) < MIN_SECTION_CHARS:
            continue
        heading = match.group(2).strip()
        sections.append(
            Section(
                file=file,
                heading=heading,
                anchor=_unique(f"{file}#{slugify(heading)}", seen),
                body=body,
                level=len(match.group(1)),
            )
        )

    return sections


def parse_decisions(text: str, *, file: str) -> list[Section]:
    """Pull each row of the decisions register out as its own citable unit.

    Deliberately loose about *which* table it reads: the register is the only table in this
    repository whose first column matches `D\\d+`, so a pattern rather than a position
    survives the file being reordered — which it has been, twice.
    """
    return [
        Section(
            file=file,
            heading=f"{match.group(1)} — {match.group(2)}",
            anchor=f"{file}#{match.group(1).lower()}",
            body=match.group(3).strip(),
        )
        for match in _DECISION_ROW.finditer(text)
        # The header separator row (`|---|---|`) cannot match `D\d+`, but a row whose
        # decision text is empty is a stub and is worth skipping.
        if len(match.group(3).strip()) >= MIN_SECTION_CHARS
    ]


def build_corpus(root: Path, *, globs: tuple[str, ...] = CORPUS_GLOBS) -> tuple[Section, ...]:
    """Read and split every documentation file under `root`.

    Sorted by path so retrieval ties break the same way on every machine — an answer whose
    citations reorder between runs is one nobody can check twice.
    """
    sections: list[Section] = []
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # A missing or unreadable doc is not worth failing a request over: the rest
                # of the corpus still answers, and the citation guard cannot cite what was
                # never indexed, so there is no way for this to produce a wrong answer.
                continue
            name = path.relative_to(root).as_posix()
            sections.extend(split_markdown(text, file=name))
            sections.extend(parse_decisions(text, file=name))
    return tuple(sections)


@lru_cache(maxsize=4)
def cached_corpus(root: Path) -> tuple[Section, ...]:
    """The corpus, read once per process.

    Cached because it is ~490 KB of file reads for a request that answers in milliseconds
    otherwise, and because the docs do not change under a running server. Keyed on the root
    so a test can build a corpus from a temporary directory without poisoning the real one.
    """
    return build_corpus(root)


def anchors(sections: tuple[Section, ...]) -> set[str]:
    """Every citation that resolves. This is what `answer.py`'s guard checks against."""
    return {section.anchor for section in sections}
