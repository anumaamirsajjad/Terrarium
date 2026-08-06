"""A citizen photo, read into a typed observation.

Pure, like the rest of `dsl/` except `llm.py`: the adapter arrives as an argument, the
image arrives already base64-encoded, and this module only builds a prompt and validates
what comes back. That is what lets the whole path be tested without a key and without a
network.

**An observation is not a measurement, and it never becomes one.** It lands on the
canonical grid because that is the only way to show it next to the model's output, but it
is kept in its own advisory layer and is *never* written into the cube's variables. The
reason is the cube's whole contract: every variable in it is a satellite or reanalysis
measurement on a known instrument with known error, and a language model's reading of a
phone photo is neither. Mixing them would make "what the cube says" unanswerable.

Unlike the planner, this path has **no offline fallback**. No rule parser can read a
photograph, so with no key configured the endpoint says so and stops rather than inventing
a category — the same posture `scripts/validate_air.py` takes about OpenAQ (D16).
"""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from terrarium.dsl.llm import LLMUnavailable, VisionAdapter


# What a phone photo can plausibly evidence *about the things this project models*. There
# is deliberately no flood or standing-water category: flood risk has no core (see
# CLAUDE.md > Scope), and offering the category would collect reports the product cannot
# answer, which is worse than not asking.
class ObservationCategory(StrEnum):
    SHADE_DEFICIT = "shade_deficit"  # exposed pavement, no canopy over a used space
    CANOPY = "canopy"  # existing or newly planted trees
    AIR_SOURCE = "air_source"  # visible smoke: burning waste, a kiln, heavy traffic
    OTHER = "other"  # legible, but not about heat or air


MAX_DESCRIPTION_CHARS = 240


class ObservationError(ValueError):
    """The photo could not be read into an observation. Carries a user-facing reason."""


class Observation(BaseModel):
    """One citizen report, as the model read it.

    `confidence` is the model's own, and it is kept rather than thresholded here: the API
    stores it and the UI shows it, so a reader can discount a shaky reading instead of
    having it silently dropped by a number chosen in this file.
    """

    model_config = ConfigDict(frozen=True)

    category: ObservationCategory
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_CHARS)
    severity: int = Field(
        ge=1,
        le=5,
        description="1 = worth noting, 5 = severe. The model's judgement, not a measurement",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="The model's own confidence. Stored and shown, never used as a filter here",
    )


SYSTEM_PROMPT = f"""\
You read a street-level photograph from Lahore and return one JSON object. JSON only.

Schema:
{{
  "category": one of {[c.value for c in ObservationCategory]},
  "description": string, max {MAX_DESCRIPTION_CHARS} characters, what is visible,
  "severity": integer 1-5, where 1 is worth noting and 5 is severe,
  "confidence": number 0-1, how sure you are of the category
}}

Rules:
- Describe only what is visible in the image. Do not infer a cause, a history, or a fix.
- "shade_deficit" is exposed ground people are using with no canopy over it.
- "canopy" is existing or newly planted trees.
- "air_source" is visible smoke or dust: burning waste, a kiln, heavy diesel traffic.
- Anything legible but unrelated to heat or air is "other" with a low severity.
- Never identify a person, a vehicle plate, or an address.
- If the image is unreadable, return "other" with confidence 0.
"""

USER_PROMPT = "Read this photograph and return the JSON object."


def observation_from_photo(
    *, image_base64: str, mime_type: str, adapter: VisionAdapter
) -> Observation:
    """Ask the model what the photo shows, and refuse anything that is not an `Observation`.

    Raises `ObservationError` on an unreachable model or an unusable answer — the same
    treatment either way, because from the caller's side there is nothing to distinguish:
    no observation was produced, and there is no second parser to fall back to.
    """
    try:
        raw = adapter.complete_json_with_image(
            system=SYSTEM_PROMPT,
            user=USER_PROMPT,
            image_base64=image_base64,
            mime_type=mime_type,
        )
    except LLMUnavailable as exc:
        raise ObservationError(str(exc)) from exc

    try:
        return Observation.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        # Same rule as the planner's: the model's output is a validated model or it is
        # nothing. Nothing here is ever half-accepted into the store.
        raise ObservationError(
            f"{adapter.name} returned something that is not an observation "
            f"({type(exc).__name__})"
        ) from exc
