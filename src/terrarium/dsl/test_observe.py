"""Reading a photo into an observation, with the model stubbed.

The whole point of the adapter arriving as an argument: this path is exercised offline,
against replies a real model might plausibly return, including the bad ones.
"""

from __future__ import annotations

import json

import pytest

from terrarium.dsl.llm import LLMUnavailable
from terrarium.dsl.observe import (
    Observation,
    ObservationCategory,
    ObservationError,
    observation_from_photo,
)


class _StubVision:
    """A vision model that returns whatever the test says, without leaving the process."""

    def __init__(self, reply: str | Exception) -> None:
        self._reply = reply
        self.calls: list[dict[str, str]] = []

    @property
    def name(self) -> str:
        return "stub:vision"

    def complete_json_with_image(
        self, *, system: str, user: str, image_base64: str, mime_type: str
    ) -> str:
        self.calls.append({"system": system, "image": image_base64, "mime": mime_type})
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


GOOD = json.dumps(
    {
        "category": "shade_deficit",
        "description": "A bus stop on bare concrete with no tree within sight.",
        "severity": 4,
        "confidence": 0.8,
    }
)


def _read(reply: str | Exception) -> Observation:
    return observation_from_photo(
        image_base64="Zm9v", mime_type="image/jpeg", adapter=_StubVision(reply)
    )


def test_a_valid_reply_becomes_a_typed_observation() -> None:
    observation = _read(GOOD)
    assert observation.category is ObservationCategory.SHADE_DEFICIT
    assert observation.severity == 4


def test_the_image_reaches_the_adapter_with_its_mime_type() -> None:
    adapter = _StubVision(GOOD)
    observation_from_photo(image_base64="Zm9v", mime_type="image/png", adapter=adapter)
    assert adapter.calls[0]["image"] == "Zm9v"
    assert adapter.calls[0]["mime"] == "image/png"


def test_the_prompt_forbids_identifying_people() -> None:
    # A citizen photo of a street contains people and number plates. The instruction is the
    # only thing standing between this feature and a surveillance tool, so it is asserted
    # rather than trusted to survive an edit.
    adapter = _StubVision(GOOD)
    observation_from_photo(image_base64="Zm9v", mime_type="image/jpeg", adapter=adapter)
    assert "Never identify a person" in adapter.calls[0]["system"]


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        json.dumps({"category": "flood", "description": "x", "severity": 3, "confidence": 0.5}),
        json.dumps({"category": "canopy", "description": "x", "severity": 9, "confidence": 0.5}),
        json.dumps({"category": "canopy", "severity": 3, "confidence": 0.5}),
        json.dumps({"category": "canopy", "description": "", "severity": 3, "confidence": 0.5}),
    ],
)
def test_anything_that_is_not_an_observation_is_refused(reply: str) -> None:
    # Including "flood": there is no flood core, so offering the category would collect
    # reports the product cannot answer.
    with pytest.raises(ObservationError):
        _read(reply)


def test_an_unreachable_model_is_an_observation_error_not_a_crash() -> None:
    # And there is deliberately no fallback: no rule parser can read a photograph, so the
    # caller has to be told, not handed a default observation.
    with pytest.raises(ObservationError, match="no network"):
        _read(LLMUnavailable("no network"))


def test_an_observation_is_frozen() -> None:
    observation = _read(GOOD)
    with pytest.raises(ValueError):
        observation.severity = 1
