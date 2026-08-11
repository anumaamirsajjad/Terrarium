"""What one measure out of a published policy document looks like.

Frozen Pydantic, like every other contract in the project. The field that does the work is
`quote`: **a verbatim span from the document**, which is what makes the extraction
checkable at all. Everything else a model produces about a measure is a summary and could
be a plausible invention; the quote either appears in the PDF or it does not.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The sectors this project can say anything about. `other` is not a catch-all to be
# quietly discarded — it is most of the document, and the coverage report counts it.
Sector = Literal["transport", "urban_greening", "industry", "waste", "agriculture", "other"]

_NOT_WORD = re.compile(r"[^a-z0-9]+")


def despace(text: str) -> str:
    """Strip everything but letters and digits, lowercased.

    This is the whole trick that makes Phase D's guard work. The Punjab Clean Air Action
    Plan's font encoding shreds words on extraction —

        "The P unja b Clea n A ir Act ion P la n En vir on me nt Protecti o n Depart m ent"

    — so a quote the model read correctly from the *rendered* page will never match the
    *extracted* text character for character. Removing every separator from both sides
    makes them comparable again, and it costs nothing that matters: two spans that agree on
    every letter and digit in order are the same span.
    """
    return _NOT_WORD.sub("", text.lower())


class PolicyMeasure(BaseModel):
    """One quantified commitment out of a policy document."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=3, max_length=200)
    sector: Sector
    target: str = Field(
        max_length=200,
        description=(
            "The quantified target as stated, e.g. '35% reduction in PM2.5'. Empty when "
            "the measure names no figure, which is most of them"
        ),
    )
    target_year: int | None = Field(default=None, ge=2000, le=2100)
    source_page: int | None = Field(default=None, ge=1)
    quote: str = Field(
        min_length=20,
        max_length=600,
        description=(
            "Verbatim from the document. **Checked against the document's own de-spaced "
            "text** — a measure whose quote does not match is dropped, because a summary "
            "nobody can trace back is indistinguishable from a fabrication."
        ),
    )
    document: str = Field(description="Which document this came from")
    document_sha256: str = Field(
        description="Of the exact bytes extracted from, so a re-extraction is comparable"
    )

    def is_grounded_in(self, despaced_document: str) -> bool:
        """Whether this measure's quote actually appears in the document."""
        needle = despace(self.quote)
        return len(needle) >= 20 and needle in despaced_document


class Coverage(BaseModel):
    """How much of a document this project's two levers can express.

    **The miss rate is the finding, not the failure.** A canopy fraction and an emission
    fraction cannot say anything about fuel sulfur limits, catalytic converter mandates or
    CNG policy, and reporting only the measures that happened to fit would describe a
    document this tool understands far better than it does.
    """

    model_config = ConfigDict(frozen=True)

    document: str
    extracted: int = Field(ge=0, description="Measures the model returned")
    grounded: int = Field(ge=0, description="…whose quote was found in the document")
    expressible: int = Field(ge=0, description="…that map onto one of the two levers")
    dropped_quotes: tuple[str, ...] = Field(
        default=(),
        description=(
            "Quotes that did not resolve. Kept so a fabrication is visible rather than "
            "merely absent — the same reason `evidence` reports its rejected citations"
        ),
    )

    @property
    def summary(self) -> str:
        return (
            f"{self.document}: {self.extracted} measures extracted, {self.grounded} with a "
            f"quote found in the document, {self.expressible} expressible on this tile. "
            f"The remaining {self.grounded - self.expressible} are real commitments this "
            "model has no lever for — fuel standards, vehicle technology, enforcement — "
            "not measures that failed to extract."
        )
