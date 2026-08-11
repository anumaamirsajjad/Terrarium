"""`/explain/spatial` over the real cores, with no model configured.

The keyless answer is the table, and the table is the answer — the prose is a reading of
it. That is why `source: "table"` is not a degraded response.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [
            [74.30, 31.47],
            [74.36, 31.47],
            [74.36, 31.53],
            [74.30, 31.53],
            [74.30, 31.47],
        ]
    ],
}


def test_it_segments_the_pattern_without_a_model(client: TestClient) -> None:
    response = client.post(
        "/explain/spatial", json={"geometry": POLYGON, "canopy_fraction_added": 0.4}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["source"] == "table"
    assert body["summary"] is None
    assert body["regions"], "a 0.4 canopy planting should move something"
    assert body["window"].endswith("summer")


def test_regions_carry_the_attributes_that_explain_them(client: TestClient) -> None:
    body = client.post(
        "/explain/spatial", json={"geometry": POLYGON, "canopy_fraction_added": 0.4}
    ).json()
    region = body["regions"][0]

    assert region["expected_cooling_c"] > 0
    assert region["headroom_km2"] >= 0
    assert 0.0 <= region["tree_cover_fraction"] <= 1.0
    assert isinstance(region["inside_polygon"], bool)
    assert isinstance(region["spillover"], bool)


def test_regions_are_ordered_strongest_first(client: TestClient) -> None:
    body = client.post(
        "/explain/spatial", json={"geometry": POLYGON, "canopy_fraction_added": 0.4}
    ).json()
    cooling = [region["expected_cooling_c"] for region in body["regions"]]

    assert cooling == sorted(cooling, reverse=True)


def test_a_plan_that_changes_nothing_returns_an_empty_table(client: TestClient) -> None:
    """Nothing is the honest answer, and there is nothing for a model to describe —
    asking anyway is an invitation to describe a pattern that is not there."""
    body = client.post(
        "/explain/spatial", json={"geometry": POLYGON, "canopy_fraction_added": 0.0}
    ).json()

    assert body["regions"] == []
    assert body["summary"] is None


def test_a_bad_polygon_is_422(client: TestClient) -> None:
    response = client.post(
        "/explain/spatial",
        json={
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
            "canopy_fraction_added": 0.3,
        },
    )
    assert response.status_code == 422


def test_an_unknown_window_is_404(client: TestClient) -> None:
    response = client.post(
        "/explain/spatial",
        json={"geometry": POLYGON, "canopy_fraction_added": 0.3, "window": "1998-autumn"},
    )
    assert response.status_code == 404


def test_the_explained_pattern_matches_what_simulate_returned(client: TestClient) -> None:
    """The two must describe the same run. If they diverge, the panel explains a map the
    user is not looking at — which is the failure recomputing rather than trusting a
    client-supplied delta field is meant to prevent."""
    body = {"geometry": POLYGON, "canopy_fraction_added": 0.4}

    simulated = client.post("/simulate", json=body).json()
    explained = client.post("/explain/spatial", json=body).json()

    assert explained["window"] == simulated["window"]
    # The strongest region cannot have cooled more than the tile's best cell, corrected.
    best = abs(simulated["stats"]["min_delta"]) / 2.5
    assert explained["regions"][0]["expected_cooling_c"] <= best + 1e-6
