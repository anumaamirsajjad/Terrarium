"""PDF bytes in, `PolicyMeasure`s out, every quote checked against the document.

One of the four modules that may reach a model (D25). Its post-check is
`PolicyMeasure.is_grounded_in`: **a measure whose verbatim quote cannot be found in the
document's own de-spaced text is dropped**, whatever else the model said about it.

The design decision this file rests on:

> **Do not build a pypdf → text → LLM pipeline.**

Naive text extraction produces garbage on the document this phase targets. The font
encoding shreds words —

    "The P unja b Clea n A ir Act ion P la n En vir on me nt Protecti o n Depart m ent"

— so a model fed that text is being asked to read something no human could, and will
hallucinate to fill the gaps. Handing the raw PDF bytes to a model with native PDF
understanding deletes the extraction step entirely. That is the single strongest argument
for Gemini anywhere in this project.

`pypdf` is still here, and only for the guard. It never parses meaning; it produces the
haystack the quote is searched in, and it produces it from the *same* mangled text — which
is why both sides are de-spaced before they are compared.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from collections.abc import Sequence

from pydantic import ValidationError

from terrarium.dsl.llm import GeminiAdapter, LLMUnavailable, resolve_adapter
from terrarium.policy.schema import Coverage, PolicyMeasure, despace

logger = logging.getLogger(__name__)


class ExtractionUnavailable(RuntimeError):
    """No model capable of reading a PDF was reachable.

    Unlike everything else in this layer, Phase D has **no offline fallback**, and that is
    stated rather than papered over: a rule parser cannot read a policy document, and a
    despaced-text keyword search would produce measures nobody could defend. So the script
    refuses rather than half-extracting — the same choice `scripts/validate_air.py` makes
    about its OpenAQ key.

    This does not weaken the zero-budget claim: extraction is a **build step**, run once by
    a maintainer, and its output is committed as data. No route needs it and no deployment
    needs a key to serve what it produced.
    """


EXTRACT_SYSTEM = """You read a government policy document and list the measures it commits \
to. Output JSON only.

Schema:
{"measures": [
  {
    "title": string, the measure in a few words,
    "sector": "transport" | "urban_greening" | "industry" | "waste" | "agriculture" | "other",
    "target": string, the quantified target exactly as stated, or "" if it states none,
    "target_year": integer, or null,
    "source_page": integer page number, or null,
    "quote": string, VERBATIM from the document, at least 20 characters
  }
]}

Rules:
1. "quote" must be copied EXACTLY from the document, word for word. It will be searched \
for in the document's own text, and a measure whose quote cannot be found is discarded. \
Do not paraphrase it, do not tidy its spacing, do not join two sentences that are not \
adjacent.
2. List measures the document actually commits to. Do not list background, definitions, \
or the problem statement.
3. Include measures of EVERY sector, not only ones about traffic or trees. A document \
whose measures are mostly fuel standards and enforcement should come back mostly fuel \
standards and enforcement.
4. "target" is only for a figure the document states. Never compute one, never estimate \
one, and never carry a figure from one measure onto another.
5. Prefer the document's own wording for "title".
"""


def document_text(pdf: bytes) -> str:
    """The document's text, de-spaced, as the haystack for the quote guard.

    Deliberately not used for anything else. On the target document this string reads as
    nonsense; what survives the mangling is the *sequence of letters and digits*, and that
    is exactly and only what `despace` compares.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    return despace("\n".join(page.extract_text() or "" for page in reader.pages))


def extract(
    pdf: bytes, *, document: str, settings: object
) -> tuple[tuple[PolicyMeasure, ...], Coverage]:
    """Extract measures from `pdf`, keeping only those whose quote is in the document.

    Raises `ExtractionUnavailable` when no PDF-capable model is configured. Everything else
    in this project degrades to a deterministic answer; this cannot, and says so rather
    than producing measures nobody could defend.
    """
    adapter = resolve_adapter(settings, task="policy")
    reader = _pdf_reader(adapter)
    if reader is None:
        raise ExtractionUnavailable(
            "policy extraction needs a model that reads PDFs natively, which today means "
            "TERRARIUM_GEMINI_API_KEY. Free and no card, but not optional here: text "
            "extracted from this document is shredded by its font encoding, so there is "
            "nothing honest to fall back to. This is a build step, not a route — no "
            "deployment needs the key to serve what it produces."
        )

    digest = hashlib.sha256(pdf).hexdigest()
    try:
        raw = reader.complete_json_with_pdf(
            system=EXTRACT_SYSTEM,
            user=f"List every measure this document commits to. Document: {document}",
            pdf=pdf,
        )
        payload = json.loads(raw)["measures"]
    except (LLMUnavailable, ValueError, KeyError, TypeError) as exc:
        raise ExtractionUnavailable(f"{reader.name} could not read the document: {exc}") from exc

    haystack = document_text(pdf)
    kept: list[PolicyMeasure] = []
    dropped: list[str] = []

    for entry in payload:
        try:
            measure = PolicyMeasure.model_validate(
                {**entry, "document": document, "document_sha256": digest}
            )
        except (ValidationError, TypeError) as exc:
            logger.info("dropping a malformed measure: %s", type(exc).__name__)
            continue

        # The guard. A quote that is not in the document means the measure was summarised
        # from somewhere the document is not, and there is no way to tell that apart from
        # invention — so it goes, however plausible it reads.
        if measure.is_grounded_in(haystack):
            kept.append(measure)
        else:
            dropped.append(measure.quote[:120])
            logger.warning("quote not found in %s, dropping: %r", document, measure.quote[:80])

    from terrarium.policy.to_plan import expressible

    return tuple(kept), Coverage(
        document=document,
        extracted=len(payload),
        grounded=len(kept),
        expressible=sum(1 for measure in kept if expressible(measure)),
        dropped_quotes=tuple(dropped),
    )


def _pdf_reader(adapter: object) -> GeminiAdapter | None:
    """The first adapter in the chain that can be handed a PDF.

    Only Gemini can today. Groq stays configured and is used everywhere else; this walks
    past it rather than failing, so a deployment with both keys extracts fine.
    """
    if isinstance(adapter, GeminiAdapter):
        return adapter
    candidates: Sequence[object] = getattr(adapter, "adapters", ())
    for candidate in candidates:
        if isinstance(candidate, GeminiAdapter):
            return candidate
    return None
