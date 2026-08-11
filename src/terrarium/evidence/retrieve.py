"""BM25 over the corpus. Pure, offline, and about forty lines.

**Not a vector store, and not `rank_bm25`.** The corpus is ~490 KB of markdown; embeddings
would be a service, an index to keep, and a chunk id where a heading used to be. BM25 is
the standard answer at this size and it is short enough to write than to depend on — the
scoring below is the textbook formula and nothing else.

Pure by design: `corpus.py` does the reading, so this module can be tested against a
handful of `Section`s built in a test file.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from terrarium.evidence.corpus import Section

# Okapi BM25's usual defaults. `k1` controls how fast term frequency saturates and `b` how
# hard length normalisation bites. Not tuned: there is no labelled question set here to
# tune against, and a number fitted by eye would be a fitted number with no fit behind it.
K1 = 1.5
B = 0.75

_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]*")

# Words that carry no signal in *this* corpus specifically. "terrarium", "cube" and "model"
# appear in nearly every section, so they cost a query its discrimination without a stop
# list — and a generic English one would not contain them.
STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
        "how", "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to",
        "was", "were", "what", "when", "where", "which", "who", "why", "with",
        # Not a general-purpose stopword. It appears in nearly every section of *this*
        # corpus, so leaving it in costs every query its discrimination.
        "terrarium",
    }
)


def tokenise(text: str) -> list[str]:
    """Lowercase word tokens, keeping dots and underscores inside a token.

    `pm2.5`, `lst_c`, `st_b10` and `d17` are all single terms here and splitting them would
    turn the most specific queries in this corpus into the least specific ones.
    """
    return [
        token for token in _TOKEN.findall(text.lower()) if token not in STOPWORDS
    ]


class Index:
    """A BM25 index over a fixed corpus. Built once, queried many times.

    A class rather than a closure because the caller holds it for the process's life and
    an object with a `search` method is what that reads as.
    """

    def __init__(self, sections: Sequence[Section]) -> None:
        self.sections = list(sections)
        self.documents = [Counter(tokenise(section.text)) for section in self.sections]
        self.lengths = [sum(document.values()) for document in self.documents]
        self.average_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0

        frequencies: Counter[str] = Counter()
        for document in self.documents:
            frequencies.update(document.keys())

        total = len(self.documents)
        # Standard BM25 IDF, with the +1 that keeps it non-negative for a term appearing in
        # more than half the corpus. Without it a common term scores negatively and a
        # document is *penalised* for containing a word the user asked about.
        self.idf = {
            term: math.log(1.0 + (total - count + 0.5) / (count + 0.5))
            for term, count in frequencies.items()
        }

    def search(self, query: str, *, limit: int = 6) -> list[tuple[Section, float]]:
        """The best-scoring sections, highest first. Never raises; may return nothing.

        Nothing is a real answer — a question with no term in the corpus should retrieve
        no sections and let `answer.py` say so, rather than returning the six sections that
        happened to be longest.
        """
        terms = tokenise(query)
        if not terms:
            return []

        scored: list[tuple[Section, float]] = []
        for index, document in enumerate(self.documents):
            length = self.lengths[index]
            score = 0.0
            for term in terms:
                frequency = document.get(term, 0)
                if not frequency:
                    continue
                norm = 1.0 - B + B * (length / self.average_length if self.average_length else 1.0)
                score += self.idf.get(term, 0.0) * (
                    frequency * (K1 + 1.0) / (frequency + K1 * norm)
                )
            if score > 0.0:
                scored.append((self.sections[index], score))

        # Deeper headings first on a tie: a `####` section is the specific answer and the
        # `#` it lives under is the chapter it is in.
        scored.sort(key=lambda pair: (-pair[1], -pair[0].level, pair[0].anchor))
        return scored[:limit]
