"""Request and response contracts for the citizen-observation routes.

Every response here repeats, in one form or another, that these are *reports* rather than
measurements. That is not decoration: the same map draws them next to a modelled ΔLST, and
a client that cannot tell the two apart will quote them the same way.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from terrarium.api.schemas.cube import LayerResponse
from terrarium.dsl.observe import Observation

# A phone photo is a few hundred kB; base64 inflates it by a third. 8 MB of encoded data is
# generous for one image and small enough that a request cannot exhaust the process.
MAX_IMAGE_BASE64_CHARS = 8_000_000

SUPPORTED_MIME_TYPES = ("image/jpeg", "image/png", "image/webp")


class ObservationRequest(BaseModel):
    """One photo and where it was taken."""

    model_config = ConfigDict(frozen=True)

    image_base64: str = Field(
        min_length=1,
        max_length=MAX_IMAGE_BASE64_CHARS,
        description="The photo, base64-encoded, without a data: URI prefix",
    )
    mime_type: str = Field(
        default="image/jpeg", description=f"One of {', '.join(SUPPORTED_MIME_TYPES)}"
    )
    lon: float = Field(ge=-180.0, le=180.0)
    lat: float = Field(
        ge=-90.0,
        le=90.0,
        description=(
            "Where the photo was taken. Supplied by the client, never inferred by the "
            "model — it is shown the pixels, not the location."
        ),
    )


class ObservationResponse(BaseModel):
    """One stored observation, on the grid."""

    model_config = ConfigDict(frozen=True)

    id: int
    observation: Observation
    lon: float
    lat: float
    row: int = Field(description="Grid row the photo falls in, assigned by the API")
    col: int


class ObservationListResponse(BaseModel):
    """Everything reported since the process started."""

    model_config = ConfigDict(frozen=True)

    observations: tuple[ObservationResponse, ...]
    reader: str = Field(
        description=(
            "The vision model that read these photos, or a sentence saying none is "
            "configured. Unlike the planner, this path has no offline fallback: no rule "
            "parser can read a photograph."
        )
    )
    persisted: bool = Field(
        default=False,
        description=(
            "Always false. Observations live in the process and vanish on restart — the "
            "demo needs the mechanism, not a database of user-submitted content."
        ),
    )


class ObservationLayerResponse(BaseModel):
    """The reports as a raster, for drawing beside the modelled layers."""

    model_config = ConfigDict(frozen=True)

    layer: LayerResponse
    count: int = Field(description="How many observations this raster is built from")
    measured: bool = Field(
        default=False,
        description=(
            "Always false, and the reason this is a separate endpoint from /cube/layer. "
            "Every variable behind that one is an instrument reading; this is a language "
            "model's opinion of a phone photo, rendered on the same grid so it can be "
            "compared, and kept out of the cube so the cube stays answerable."
        ),
    )
