"""GET /policy/measures. No cube, no model, no network — a read of a DuckDB table."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from terrarium.api.main import create_app
from terrarium.config import Settings
from terrarium.policy.test_to_plan import measure


def _app(duckdb_path: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                env="test",
                serve_zarr_store=Path("does/not/exist"),
                duckdb_path=duckdb_path,
            )
        )
    )


def test_no_extraction_yet_is_an_empty_list_not_an_error(tmp_path: Path) -> None:
    response = _app(tmp_path / "terrarium.duckdb").get("/policy/measures")

    assert response.status_code == 200
    assert response.json() == {"measures": [], "expressible": 0}


def test_an_expressible_measure_carries_its_plan_and_citation(tmp_path: Path) -> None:
    path = tmp_path / "terrarium.duckdb"
    written = measure()  # a 36% traffic measure, from src/terrarium/policy/test_to_plan.py
    conn = duckdb.connect(str(path))
    conn.execute(
        "CREATE TABLE policy_measures (document VARCHAR, document_sha256 VARCHAR, "
        "title VARCHAR, sector VARCHAR, target VARCHAR, target_year INTEGER, "
        "source_page INTEGER, quote VARCHAR, expressible BOOLEAN, canopy_fraction DOUBLE, "
        "emission_fraction DOUBLE, extracted_at TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO policy_measures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            written.document,
            written.document_sha256,
            written.title,
            written.sector,
            written.target,
            written.target_year,
            written.source_page,
            written.quote,
            True,
            None,
            0.36,
            "2026-08-11 00:00:00",
        ],
    )
    conn.close()

    body = _app(path).get("/policy/measures").json()

    assert body["expressible"] == 1
    item = body["measures"][0]
    assert item["measure"]["quote"] == written.quote
    assert item["mapped"]["plan"]["actions"][0]["kind"] == "restrict_vehicles"
    assert item["mapped"]["plan"]["actions"][0]["emission_fraction_removed"] == pytest.approx(0.36)
    assert written.document in item["mapped"]["basis"]
