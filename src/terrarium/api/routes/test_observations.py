"""The citizen-observation routes. No key configured, and none of these needs one.

The interesting case is the *refusal*: with no vision model this endpoint must say so
rather than store an empty observation, because a stored blank would render on the map as
a report that a citizen never made.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from terrarium.api import main
from terrarium.api.observations import ObservationStore, RateLimiter
from terrarium.api.routes import observations
from terrarium.api.routes.test_cube import decode
from terrarium.api.runtime import Runtime
from terrarium.config import Settings

PHOTO = base64.b64encode(b"not really a jpeg").decode()

# Central Lahore, well inside the tile.
INSIDE = {"lon": 74.35, "lat": 31.52}


def _body(**overrides: Any) -> dict[str, Any]:
    return {"image_base64": PHOTO, "mime_type": "image/jpeg", **INSIDE, **overrides}


class _StubVision:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "stub:vision"

    def complete_json_with_image(
        self, *, system: str, user: str, image_base64: str, mime_type: str
    ) -> str:
        self.calls += 1
        return json.dumps(
            {
                "category": "air_source",
                "description": "waste burning at the kerb",
                "severity": 5,
                "confidence": 0.9,
            }
        )


@pytest.fixture
def keyed_client(
    synthetic_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """A client whose settings carry a key, with the provider stubbed at the adapter seam.

    The stub replaces `adapter_from_key` in the route module — the one function that
    decides whether a model exists — so the test exercises the real route, the real store
    and the real validation, and still never opens a socket.
    """
    monkeypatch.setattr(observations, "adapter_from_key", lambda *_, **__: _StubVision())
    app = main.create_app(
        Settings(env="test", gemini_api_key="test-key"), runtime=synthetic_runtime
    )
    yield TestClient(app)


# ------------------------------------------------------------- without a key ---


def test_submitting_without_a_model_is_a_503_that_says_why(client: TestClient) -> None:
    response = client.post("/observations", json=_body())
    assert response.status_code == 503

    detail = response.json()["detail"]
    assert "TERRARIUM_GEMINI_API_KEY" in detail
    # And it says *why* there is no fallback, unlike every other path in this API.
    assert "no rule parser can read a photograph" in detail


def test_the_list_endpoint_works_without_a_model(client: TestClient) -> None:
    body = client.get("/observations").json()
    assert body["observations"] == []
    assert "no vision model configured" in body["reader"]
    # Never true. Observations live in the process and vanish on restart.
    assert body["persisted"] is False


def test_the_layer_is_all_nan_before_anybody_reports_anything(client: TestClient) -> None:
    body = client.get("/observations/layer").json()
    raster = decode(body["layer"])

    assert body["count"] == 0
    assert body["measured"] is False
    assert raster.shape == (201, 202)
    assert body["layer"]["valid_fraction"] == 0.0


def test_a_bad_mime_type_is_refused_before_the_model_is_consulted(client: TestClient) -> None:
    # 422 rather than the 503: the request itself is wrong, and finding that out should not
    # depend on whether a key happens to be set.
    response = client.post("/observations", json=_body(mime_type="image/gif"))
    assert response.status_code == 422


def test_data_that_is_not_base64_is_refused_here_rather_than_by_the_provider(
    client: TestClient,
) -> None:
    response = client.post("/observations", json=_body(image_base64="!!!not base64!!!"))
    assert response.status_code == 422
    assert "not valid base64" in response.json()["detail"]


def test_a_photo_from_outside_the_tile_is_refused(client: TestClient) -> None:
    # Karachi. Clamping to the nearest edge cell would be a wrong answer wearing a
    # plausible one's clothes.
    response = client.post("/observations", json=_body(lon=67.0, lat=24.86))
    assert response.status_code == 422
    assert "outside the tile" in response.json()["detail"]


# ---------------------------------------------------------------- with a key ---


def test_a_photo_becomes_an_observation_on_the_grid(keyed_client: TestClient) -> None:
    body = keyed_client.post("/observations", json=_body()).json()

    assert body["observation"]["category"] == "air_source"
    assert body["observation"]["severity"] == 5
    # The API assigns the cell from the submitted coordinates: the model is shown the
    # pixels, not the location, and has no business inventing one.
    assert 0 <= body["row"] < 201
    assert 0 <= body["col"] < 202


def test_reports_accumulate_and_render_onto_the_canonical_grid(keyed_client: TestClient) -> None:
    keyed_client.post("/observations", json=_body())
    keyed_client.post("/observations", json=_body(lon=74.30, lat=31.47))

    listed = keyed_client.get("/observations").json()
    assert len(listed["observations"]) == 2
    assert listed["reader"] == "stub:vision"

    layer = keyed_client.get("/observations/layer").json()
    raster = decode(layer["layer"])
    assert layer["count"] == 2
    assert int((raster == 5).sum()) == 2


def test_the_observation_layer_is_not_a_cube_variable(keyed_client: TestClient) -> None:
    # The separation this whole feature rests on: a language model's reading of a phone
    # photo is not a measurement, so it never becomes one of the cube's variables.
    assert keyed_client.get("/cube/layer/citizen_severity").status_code == 404


def test_each_app_gets_its_own_store(client: TestClient, keyed_client: TestClient) -> None:
    # The store hangs off `app.state`, not a module global, so one deployment's reports
    # cannot appear in another's — and a test that submits cannot leak into one that
    # asserts an empty list.
    keyed_client.post("/observations", json=_body())

    assert len(keyed_client.get("/observations").json()["observations"]) == 1
    assert client.get("/observations").json()["observations"] == []



# ------------------------------------------------------------- the rate limit ---
#
# A21: the ceiling on free-tier spend. Checked *after* the 503, so a deployment with no key
# refuses for the honest reason rather than rationing calls it never makes — and *before*
# the model call, which is the only thing on this route that costs anything.


@pytest.fixture
def limited(
    synthetic_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[int], tuple[TestClient, _StubVision]]]:
    """Build a keyed client whose ceiling is low enough to reach inside a test.

    A factory rather than a fixed fixture because the limit is what each test varies, and
    it hands back the stub so a test can assert on what the model was *not* asked to do.
    """
    clients: list[TestClient] = []

    def build(limit: int) -> tuple[TestClient, _StubVision]:
        stub = _StubVision()
        monkeypatch.setattr(observations, "adapter_from_key", lambda *_, **__: stub)
        app = main.create_app(
            Settings(env="test", gemini_api_key="test-key"), runtime=synthetic_runtime
        )
        store = ObservationStore()
        store.rate_limiter = RateLimiter(limit=limit, window_s=3600.0)
        app.state.terrarium_observations = store
        client = TestClient(app)
        clients.append(client)
        return client, stub

    yield build
    for client in clients:
        client.close()


def test_the_ceiling_refuses_with_429_once_it_is_reached(
    limited: Callable[[int], tuple[TestClient, _StubVision]],
) -> None:
    client, _ = limited(2)

    assert client.post("/observations", json=_body()).status_code == 200
    assert client.post("/observations", json=_body()).status_code == 200

    refused = client.post("/observations", json=_body())
    assert refused.status_code == 429
    assert "rate limit" in refused.json()["detail"]


def test_a_refused_call_never_reaches_the_model(
    limited: Callable[[int], tuple[TestClient, _StubVision]],
) -> None:
    """The whole point: the 429 must land before the thing that spends quota."""
    client, stub = limited(1)

    assert client.post("/observations", json=_body()).status_code == 200
    assert stub.calls == 1

    for _ in range(5):
        assert client.post("/observations", json=_body()).status_code == 429
    assert stub.calls == 1, "a refused request still spent a model call"


def test_a_malformed_request_does_not_spend_a_slot(
    limited: Callable[[int], tuple[TestClient, _StubVision]],
) -> None:
    """Validation is free. Only the model call is rationed, so a 422 must not cost a slot."""
    client, _ = limited(1)

    bad = client.post("/observations", json=_body(image_base64="!!!not base64!!!"))
    assert bad.status_code == 422
    assert client.post("/observations", json=_body()).status_code == 200


def test_reads_are_not_rationed(
    limited: Callable[[int], tuple[TestClient, _StubVision]],
) -> None:
    """GET costs nothing upstream, so it must not be caught by a ceiling on spend."""
    client, _ = limited(1)

    assert client.post("/observations", json=_body()).status_code == 200
    for _ in range(10):
        assert client.get("/observations").status_code == 200
        assert client.get("/observations/layer").status_code == 200
