"""`/agent/*` over the synthetic runtime.

**The search requires a model now**, so these tests configure a scripted one through
`with_model` — the same `resolve_adapter` seam `require_model` and the nodes both read, so
a test that passes here is a test the route would actually take. No network, ever.

The first assertion in the file is the one the change is about: with no model, the route
refuses with a status code instead of running a different, worse procedure and returning
it under the same field names.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from terrarium.api.conftest import ScriptedAdapter

Configure = Callable[[Sequence[str]], ScriptedAdapter]

BUDGET = {"max_simulations": 3, "max_llm_calls": 4, "wall_clock_s": 120.0}


def _events(response: Response) -> list[dict[str, Any]]:
    """Parse an SSE body into its data payloads."""
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _goal() -> str:
    return json.dumps({"metric": "person_degrees"})


def _propose(region_id: str, fraction: float = 0.3) -> str:
    return json.dumps(
        {
            "region_ids": [region_id],
            "plan": {
                "name": "Scripted planting",
                "actions": [{"kind": "plant_trees", "canopy_fraction_added": fraction}],
            },
        }
    )


def _script(client: TestClient) -> list[str]:
    """A goal and a proposal for a region that really exists in this cube's lattice."""
    region = client.get("/agent/candidates").json()["candidates"][61]["region_id"]
    return [_goal(), _propose(region)]


# --- the lattice needs no model ------------------------------------------------------


def test_candidates_tile_the_grid(client: TestClient) -> None:
    """Deterministic, and deliberately *not* behind `require_model`: the lattice is the
    grid layer's own output and the UI draws it before anything is searched."""
    response = client.get("/agent/candidates")
    assert response.status_code == 200

    body = response.json()
    assert body["block_cells"] == 20
    assert body["window"].endswith("summer")
    assert len(body["candidates"]) == 11 * 11  # 201 x 202 cells at 20 per block

    first = body["candidates"][0]
    assert first["geometry"]["type"] == "Polygon"
    assert first["region_id"] == "r00c00"


def test_an_unknown_window_is_404(client: TestClient) -> None:
    response = client.get("/agent/candidates", params={"window": "1998-autumn"})
    assert response.status_code == 404
    assert "have" in response.json()["detail"]


# --- the search requires one ---------------------------------------------------------


def test_a_search_without_a_model_is_503_before_the_stream_opens(client: TestClient) -> None:
    """The change this file is about.

    A status code, not a 200 whose body is a lattice sweep dressed as a search. And
    refused *before* the stream opens, so the client gets a code it can branch on rather
    than an error event inside a successful response.
    """
    response = client.post("/agent/search", json={"goal": "cool this tile"})

    assert response.status_code == 503
    assert "TERRARIUM_GROQ_API_KEY" in response.json()["detail"]
    assert not response.headers["content-type"].startswith("text/event-stream")


def test_a_search_streams_events_and_ends_with_a_result(
    client: TestClient, with_model: Configure
) -> None:
    with_model(_script(client))

    response = client.post("/agent/search", json={"goal": "cool this tile", "budget": BUDGET})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _events(response)
    assert events[0]["node"] == "parse_goal"
    assert events[-1]["node"] == "done"

    result = events[-1]["result"]
    assert result["best"] is not None
    assert result["baseline"] is not None
    # Every proposal came from the model or from the greedy control. There is no third
    # producer any more.
    assert {a["proposer"] for a in result["tried"]} <= {"model", "greedy"}


def test_a_finished_search_is_readable_by_id(
    client: TestClient, with_model: Configure
) -> None:
    with_model(_script(client))

    response = client.post("/agent/search", json={"goal": "cool this tile", "budget": BUDGET})
    search_id = _events(response)[-1]["result"]["search_id"]

    read_back = client.get(f"/agent/search/{search_id}")
    assert read_back.status_code == 200
    assert read_back.json()["result"]["search_id"] == search_id


def test_an_unknown_search_id_is_404(client: TestClient) -> None:
    response = client.get("/agent/search/nope")
    assert response.status_code == 404
    assert "do not survive a restart" in response.json()["detail"]


def test_a_search_carries_no_geometry_in_its_request(
    client: TestClient, with_model: Configure
) -> None:
    """D26 as an HTTP contract: there is nothing for a caller to draw, so a `geometry`
    key is a mistake and is rejected rather than ignored."""
    with_model(_script(client))

    response = client.post(
        "/agent/search",
        json={"goal": "cool this tile", "geometry": {"type": "Polygon", "coordinates": []}},
    )
    assert response.status_code == 422


def test_the_winning_plan_can_be_run_through_simulate(
    client: TestClient, with_model: Configure
) -> None:
    """The apply path. The winner's region geometry has to be a body `/simulate` accepts,
    or the 'Apply this plan' button hands the user a polygon that does not reproduce."""
    with_model(_script(client))

    search = client.post("/agent/search", json={"goal": "cool this tile", "budget": BUDGET})
    result = _events(search)[-1]["result"]
    best = result["best"]

    candidates = {c["region_id"]: c for c in client.get("/agent/candidates").json()["candidates"]}
    geometry = candidates[best["region_ids"][0]]["geometry"]

    replay = client.post(
        "/simulate",
        json={
            "geometry": geometry,
            "canopy_fraction_added": 0.30,
            "window": result["window"],
        },
    )
    assert replay.status_code == 200
    # The same cells, so the same delta the search scored.
    assert replay.json()["stats"]["mean_delta_inside"] < 0


def test_a_provider_that_dies_mid_search_reports_it_in_the_stream(
    client: TestClient, with_model: Configure
) -> None:
    """The stream is already open and half-rendered, so this cannot be a status code.

    A truncated event stream with no body is the one failure a browser cannot report
    usefully, which is why the generator turns it into an `error` event and ends cleanly.
    """
    with_model(["not json at all"])

    response = client.post("/agent/search", json={"goal": "cool this tile", "budget": BUDGET})
    assert response.status_code == 200

    events = _events(response)
    assert events[-1]["node"] == "error"
    assert events[-1]["result"] is None
