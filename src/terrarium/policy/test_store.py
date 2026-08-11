from __future__ import annotations

from pathlib import Path

import duckdb

from terrarium.policy.store import read_measures
from terrarium.policy.test_to_plan import measure


def test_no_database_yet_is_an_empty_tuple(tmp_path: Path) -> None:
    assert read_measures(tmp_path / "does-not-exist.duckdb") == ()


def test_a_database_with_no_extraction_yet_is_an_empty_tuple(tmp_path: Path) -> None:
    """`extract_policy.py` creates the table on its first run. Before that, the shared
    catalogue exists (other builds wrote to it) but has nothing for this route to read."""
    path = tmp_path / "terrarium.duckdb"
    duckdb.connect(str(path)).execute("CREATE TABLE builds (build_id VARCHAR)").close()

    assert read_measures(path) == ()


def test_reads_back_what_extract_policy_wrote(tmp_path: Path) -> None:
    path = tmp_path / "terrarium.duckdb"
    written = measure()
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

    measures = read_measures(path)
    assert measures == (written,)
