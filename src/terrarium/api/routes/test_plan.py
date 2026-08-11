"""POST /plan and GET /plan/presets. In-memory cube, no model key, no network.

The route is translation, so these tests check the translation: that the polygon is
measured against the tile rather than assumed, that a refusal arrives *before* any physics
runs, and that what comes back can be posted straight to `/simulate` and produce the same
numbers. The DSL's own rules are tested in `dsl/`.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from terrarium.api.routes.test_simulate import PLANTABLE

# The synthetic river runs down the western edge of the fixture cube: nothing there can be
# planted, which is what makes it the honest test of a measured refusal.
RIVER = {
    "type": "Polygon",
    "coordinates": [
        [
            [74.2540, 31.44],
            [74.2570, 31.44],
            [74.2570, 31.47],
            [74.2540, 31.47],
            [74.2540, 31.44],
        ]
    ],
}


def build_plan(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/plan", json={"geometry": PLANTABLE, **body})
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


# ------------------------------------------------------------------- presets ---


def test_presets_are_listed_with_their_caveats(client: TestClient) -> None:
    body = client.get("/plan/presets").json()

    assert {entry["slug"] for entry in body["presets"]} >= {
        "street-trees",
        "low-emission-zone",
    }
    assert all(entry["caveat"] for entry in body["presets"])


def test_the_planner_names_itself_and_admits_to_having_no_model(client: TestClient) -> None:
    # The test settings carry no Gemini key, which is the deployment this has to work in.
    assert client.get("/plan/presets").json()["planner"] == "rules (no model configured)"


# ---------------------------------------------------------------- the sources ---


def test_a_preset_becomes_a_simulate_body(client: TestClient) -> None:
    body = build_plan(client, preset="street-trees")

    assert body["source"] == "preset"
    assert body["canopy_fraction_added"] == 0.15
    assert body["simulate_request"]["canopy_fraction_added"] == 0.15
    assert body["simulate_request"]["window"] == body["window"]


def test_free_text_is_parsed_by_the_rules_when_there_is_no_model(client: TestClient) -> None:
    body = build_plan(client, text="plant 4,000 trees here")

    assert body["source"] == "rules"
    assert body["tree_count"] == 4_000
    assert body["canopy_fraction_added"] > 0


def test_an_explicit_plan_is_validated_like_any_other(client: TestClient) -> None:
    body = build_plan(
        client,
        plan={
            "name": "Hand-built",
            "actions": [{"kind": "plant_trees", "canopy_fraction_added": 0.2}],
        },
    )
    assert body["source"] == "explicit"
    assert body["canopy_fraction_added"] == 0.2


def test_exactly_one_source_is_required(client: TestClient) -> None:
    both = client.post(
        "/plan", json={"geometry": PLANTABLE, "text": "plant 10 trees", "preset": "street-trees"}
    )
    assert both.status_code == 422

    neither = client.post("/plan", json={"geometry": PLANTABLE})
    assert neither.status_code == 422


def test_an_unknown_preset_is_a_404_naming_the_real_ones(client: TestClient) -> None:
    response = client.post("/plan", json={"geometry": PLANTABLE, "preset": "plant-everything"})
    assert response.status_code == 404
    assert "street-trees" in response.json()["detail"]


def test_text_with_no_intervention_in_it_is_refused_usefully(client: TestClient) -> None:
    response = client.post("/plan", json={"geometry": PLANTABLE, "text": "hello"})
    assert response.status_code == 422
    assert "5,000 trees" in response.json()["detail"]


def test_nan_in_an_explicit_plan_is_a_422_not_a_500(client: TestClient) -> None:
    """F22, on the second route the finding named. Same mechanism as /simulate: `NaN` has
    no JSON spelling, so an error response that echoes it used to fail to serialise."""
    body = json.dumps(
        {
            "geometry": PLANTABLE,
            "plan": {
                "name": "test",
                "actions": [{"kind": "plant_trees", "canopy_fraction_added": float("nan")}],
            },
        }
    )
    response = client.post("/plan", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422


def test_a_tree_count_long_enough_to_overflow_is_a_422_not_a_500(client: TestClient) -> None:
    # F14: `float("9"*309)` is `inf`, and `round(inf)` used to raise a bare `OverflowError`
    # straight out of the rule parser - a well-formed request 500ing.
    response = client.post(
        "/plan", json={"geometry": PLANTABLE, "text": "plant " + "9" * 309 + " trees"}
    )
    assert response.status_code == 422


# ------------------------------------------------------ measured against the tile ---


def test_the_polygon_is_measured_rather_than_assumed(client: TestClient) -> None:
    body = build_plan(client, preset="street-trees")

    # max_trees comes from the cube's own canopy headroom, so it must be a real number
    # bounded by the polygon's area at one crown per 25 m2.
    assert 0 < body["max_trees"] <= body["cells"] * 10_000 / 25
    assert body["area_km2"] == pytest.approx(body["cells"] * 0.01)


def test_a_planting_that_cannot_fit_is_refused_with_the_arithmetic(client: TestClient) -> None:
    # The refusal the phase exists for. It has to arrive from /plan, before /simulate runs
    # a core and returns a small delta that looks like a plan that merely worked badly.
    response = client.post("/plan", json={"geometry": PLANTABLE, "text": "plant 90,000,000 trees"})
    assert response.status_code == 422

    detail = response.json()["detail"]
    assert "90,000,000 trees" in detail
    assert "still plantable" in detail


def test_a_polygon_over_water_refuses_a_planting(client: TestClient) -> None:
    response = client.post("/plan", json={"geometry": RIVER, "preset": "street-trees"})
    assert response.status_code == 422
    assert "nothing in this polygon can be planted" in response.json()["detail"]


def test_a_polygon_over_water_still_accepts_a_restriction(client: TestClient) -> None:
    # There is no reason a low-emission zone needs plantable ground, and refusing it
    # because trees would not grow there would be the DSL over-reaching.
    body = build_plan(client, geometry=RIVER, preset="low-emission-zone")
    assert body["emission_fraction_removed"] == 1.0


def test_an_over_ambitious_fraction_warns_instead_of_refusing(client: TestClient) -> None:
    body = build_plan(client, preset="dense-canopy")
    assert body["canopy_fraction_added"] == 0.40
    if body["canopy_utilisation"] > 1.0:
        assert any("more than this polygon can take" in note for note in body["notes"])


# ------------------------------------------------------------------- the window ---


def test_the_window_defaults_to_the_latest_summer(client: TestClient) -> None:
    body = build_plan(client, preset="street-trees")
    assert body["window"] == "2024-summer"
    assert body["season"] == "summer"


def test_a_season_without_a_year_still_picks_the_right_season(client: TestClient) -> None:
    # "in winter" has to mean winter: the same restriction buys several times more under
    # the inversion, so answering it with the summer default would be off by that factor.
    body = build_plan(client, text="ban cars here in winter")
    assert body["window"] == "2024-winter"
    assert body["season"] == "winter"


def test_the_preset_that_carries_a_season_uses_it(client: TestClient) -> None:
    assert build_plan(client, preset="winter-inversion")["window"] == "2024-winter"


def test_an_explicit_window_overrides_the_plan(client: TestClient) -> None:
    body = build_plan(client, preset="winter-inversion", window="2024-summer")
    assert body["window"] == "2024-summer"


def test_an_unknown_window_is_a_404_listing_what_exists(client: TestClient) -> None:
    response = client.post(
        "/plan", json={"geometry": PLANTABLE, "preset": "street-trees", "window": "1998-summer"}
    )
    assert response.status_code == 404
    assert "2024-summer" in response.json()["detail"]


# ------------------------------------------------------------------ end to end ---


def test_the_returned_body_runs_and_the_trees_survive_the_round_trip(
    client: TestClient,
) -> None:
    plan = build_plan(client, text="plant 4,000 trees and remove 50% of traffic")

    assert plan["tree_count"] > 0

    response = client.post("/simulate", json=plan["simulate_request"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["window"] == plan["window"]
    assert body["stats"]["mean_delta_inside"] < 0
    # The plan asked for traffic too, so the air block has to be there rather than null.
    assert body["air"] is not None
