"""Read back what `scripts/extract_policy.py` wrote to the shared DuckDB catalogue.

Read-only and pure I/O — no model, no PDF. This is what lets `GET /policy/measures` serve
the extracted library without the Gemini key `policy/extract.py` needs: the key paid for
the extraction, once, as a build step; reading the result back costs nothing.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from terrarium.policy.schema import PolicyMeasure

_TABLE = "policy_measures"


def read_measures(duckdb_path: Path) -> tuple[PolicyMeasure, ...]:
    """Every measure `extract_policy.py` has grounded, or `()` before it has ever run.

    Only grounded measures reach the table at all — `extract()` drops a measure whose
    quote does not match the document before the script inserts anything — so every row
    read back here is safe to hand `policy.to_plan.to_plan` without re-checking the quote.
    """
    if not duckdb_path.exists():
        return ()

    conn = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        if _TABLE not in tables:
            return ()
        rows = conn.execute(
            f"SELECT document, document_sha256, title, sector, target, target_year, "
            f"source_page, quote FROM {_TABLE} ORDER BY document, sector, title"
        ).fetchall()
    finally:
        conn.close()

    return tuple(
        PolicyMeasure(
            document=row[0],
            document_sha256=row[1],
            title=row[2],
            sector=row[3],
            target=row[4] or "",
            target_year=row[5],
            source_page=row[6],
            quote=row[7],
        )
        for row in rows
    )
