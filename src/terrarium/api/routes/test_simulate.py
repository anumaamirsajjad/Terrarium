"""POST /simulate. In-memory cube, real booster, no network.

These are contract tests for the interface between the physics track and the product
track. The physics itself is tested in `cores/thermal/test_simulate.py`; what is checked
here is that the API hands the core the right window and the right mask, and reports back
what it actually did rather than what was asked for.
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np
from fastapi.testclient import TestClient

from terrarium.api.routes.test_cube import decode

# A box over the built-up middle of the tile, well clear of the synthetic river along the
# western edge and the tree band across the north.
PLANTABLE = {
    "type": "Polygon",
    "coordinates": [
        [
            [74.34, 31.49],
            [74.38, 31.49],
            [74.38, 31.52],
            [74.34, 31.52],
            [74.34, 31.49],
        ]
    ],
}

# The synthetic river down the western edge. Nothing plantable in it, which makes it the
# way to ask for a planting that *achieves* nothing without asking for nothing.
RIVER = {
    "type": "Polygon",
    "coordinates": [
        [
            [74.2540, 31.44],
            [74.2570, 31.44],
            [74.2570, 31.60],
            [74.2540, 31.60],
            [74.2540, 31.44],
        ]
    ],
}


def simulate(client: TestClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"geometry": PLANTABLE, "canopy_fraction_added": 0.30, **overrides}
    response = client.post("/simulate", json=body)
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


def test_planting_cools_the_planted_area(client: TestClient) -> None:
    """The first sanity check. Positive here is a sign error, not a finding."""
    body = simulate(client)

    assert body["stats"]["mean_delta_inside"] < 0
    assert body["units"] == "degC"
    assert body["variable"] == "lst_c"


def test_the_delta_covers_the_whole_tile_not_just_the_polygon(client: TestClient) -> None:
    """Cooling genuinely extends past the drawn edge - that spillover is real physics."""
    body = simulate(client)
    delta = decode(body["delta"])

    assert delta.shape == (201, 202)
    assert body["stats"]["spillover_cells"] > 0
    assert body["stats"]["mean_delta_spillover"] < 0


def test_untouched_ground_is_exactly_zero(client: TestClient) -> None:
    """Proof the delta is prediction-minus-prediction, not prediction-minus-observed.

    Outside the feature neighbourhood both feature rows are identical, so the two
    predictions are bit-identical. Had we differenced against observed LST the whole tile
    would carry residual noise and the map would be unreadable.
    """
    delta = decode(simulate(client)["delta"])

    assert (delta == 0.0).sum() > delta.size // 2


def test_more_canopy_cools_more(client: TestClient) -> None:
    light = simulate(client, canopy_fraction_added=0.10)
    heavy = simulate(client, canopy_fraction_added=0.50)

    assert heavy["stats"]["mean_delta_inside"] < light["stats"]["mean_delta_inside"]


def test_zero_canopy_changes_nothing(client: TestClient) -> None:
    body = simulate(client, canopy_fraction_added=0.0)
    delta = decode(body["delta"])

    assert np.all(delta[np.isfinite(delta)] == 0.0)


def test_a_request_that_asks_for_nothing_is_not_headlined_as_a_planting(
    client: TestClient,
) -> None:
    """Both levers at zero used to fall through to "Planting".

    The brief then read *"Planting over N km2 changes nothing measurable"*, which states a
    modelled result for a planting nobody requested. Only reachable by posting here
    directly - `/plan` refuses an empty plan at the schema - so it is cosmetic, but the
    sentence is the product's own summary of what it did.
    """
    body = simulate(client, canopy_fraction_added=0.0, emission_fraction_removed=0.0)
    headline = body["brief"]["headline"]

    assert "Planting" not in headline
    assert headline.startswith("No intervention")


def test_a_planting_with_no_headroom_is_still_called_a_planting(client: TestClient) -> None:
    """The distinction A19 draws is *requested*, not *achieved*.

    Planting over water adds no canopy and cools nothing, but the plan did ask to plant -
    "changes nothing measurable" is then the right answer to the right question.
    """
    body = simulate(client, canopy_fraction_added=0.4, geometry=RIVER)

    assert body["brief"]["headline"].startswith("Planting")


# ------------------------------------------------------------------- the window ---


def test_the_window_is_echoed_and_defaults_to_summer(client: TestClient) -> None:
    """Never assume the default. The same planting differs ~4x between seasons."""
    body = simulate(client)

    assert body["window"] == "2024-summer"
    assert body["season"] == "summer"


def test_winter_and_summer_give_different_answers(client: TestClient) -> None:
    summer = simulate(client, window="2024-summer")
    winter = simulate(client, window="2024-winter")

    assert summer["window"] == "2024-summer"
    assert winter["window"] == "2024-winter"
    assert summer["stats"]["mean_delta_inside"] != winter["stats"]["mean_delta_inside"]
    # Winter's own contrast is smaller, so its ceiling is lower too.
    assert winter["context"]["tree_built_contrast_c"] < summer["context"]["tree_built_contrast_c"]


def test_an_unknown_window_is_404(client: TestClient) -> None:
    response = client.post(
        "/simulate", json={"geometry": PLANTABLE, "window": "1999-summer"}
    )

    assert response.status_code == 404
    assert "1999-summer" in response.json()["detail"]


# ---------------------------------------------------------------- plausibility ---


def test_cooling_never_exceeds_the_tile_s_own_contrast(client: TestClient) -> None:
    """The ceiling: you cannot buy more cooling than the data contains."""
    body = simulate(client, canopy_fraction_added=1.0)
    context = body["context"]

    assert context["tree_built_contrast_c"] > 0
    assert body["stats"]["mean_delta_inside"] >= -context["tree_built_contrast_c"]


def test_the_response_reports_canopy_actually_added_not_requested(client: TestClient) -> None:
    """Requested is a ceiling, not a promise - cells with canopy already have less room."""
    body = simulate(client, canopy_fraction_added=1.0)

    assert 0 < body["context"]["mean_canopy_added"] <= 1.0
    assert body["context"]["mean_canopy_added"] < 1.0


def test_the_linear_expectation_is_shipped_beside_the_delta(client: TestClient) -> None:
    """A naked ΔLST invites comparison to a literature range this tile does not have."""
    context = simulate(client)["context"]

    assert context["linear_expectation_c"] < 0
    assert context["ratio_to_linear"] is not None
    assert context["ratio_to_linear"] > 0


# -------------------------------------------------------------------- geometry ---


def test_a_polygon_outside_the_tile_is_422_not_a_zero_delta(client: TestClient) -> None:
    """The dangerous case: an empty mask simulates cleanly and looks like 'no effect'."""
    response = client.post(
        "/simulate",
        json={
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01], [0, 0]]],
            }
        },
    )

    assert response.status_code == 422
    assert "no grid cells" in response.json()["detail"]


def test_a_point_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/simulate",
        json={"geometry": {"type": "Point", "coordinates": [74.36, 31.51]}},
    )

    assert response.status_code == 422


def test_a_canopy_fraction_above_one_is_rejected_by_the_schema(client: TestClient) -> None:
    response = client.post(
        "/simulate", json={"geometry": PLANTABLE, "canopy_fraction_added": 1.5}
    )

    assert response.status_code == 422


def test_planting_on_water_adds_nothing(client: TestClient) -> None:
    """The synthetic river runs down the western edge; you cannot plant a river."""
    response = client.post("/simulate", json={"geometry": RIVER})

    # Either the mask holds no plantable cell at all (n_cells_changed == 0), or the few
    # non-water cells it caught cool slightly. What must never happen is the river itself
    # being reported as planted.
    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["mean_delta_inside"] <= 0


# --------------------------------------------------------------------------------
# The air block (Phase 9)
# --------------------------------------------------------------------------------


def test_no_air_block_unless_the_plan_touches_emissions(client: TestClient) -> None:
    """A tree-planting request asks no air question, and must not be answered as if it did."""
    assert simulate(client)["air"] is None


def test_a_low_emission_zone_cleans_the_air(client: TestClient) -> None:
    body = simulate(client, emission_fraction_removed=1.0)
    air = body["air"]

    assert air is not None
    assert air["variable"] == "pm25_ugm3"
    assert air["units"] == "ug m-3"
    assert air["stats"]["mean_delta_inside"] < 0
    # Downwind spillover is the point of a dispersion core: the streets behind the zone
    # benefit too, over a far wider ring than cooling ever reaches.
    assert air["stats"]["mean_delta_spillover"] < 0
    assert air["stats"]["spillover_cells"] > body["stats"]["spillover_cells"]
    assert decode(air["delta"]).shape == (201, 202)


def test_the_air_answer_names_its_window_and_its_inversion(client: TestClient) -> None:
    """Winter is not a scale factor on summer, so the parameters travel with the answer."""
    summer = simulate(client, emission_fraction_removed=0.5, window="2024-summer")["air"]
    winter = simulate(client, emission_fraction_removed=0.5, window="2024-winter")["air"]

    assert winter["mixing_height_m"] < summer["mixing_height_m"]
    assert winter["wind_direction_deg"] != summer["wind_direction_deg"]
    # Same emissions removed, much larger effect under the inversion.
    assert abs(winter["stats"]["min_delta"]) > 3 * abs(summer["stats"]["min_delta"])


def test_a_removal_fraction_above_one_is_rejected_by_the_schema(client: TestClient) -> None:
    response = client.post(
        "/simulate", json={"geometry": PLANTABLE, "emission_fraction_removed": 1.4}
    )

    assert response.status_code == 422
