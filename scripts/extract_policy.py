"""Extract measures from a downloaded policy document and print the coverage table.

    uv run python scripts/ingest_policy.py
    uv run python scripts/extract_policy.py

**Needs `TERRARIUM_GEMINI_API_KEY`**, and unlike every other path in this project there is
no offline fallback — a rule parser cannot read a policy document, and the naive text
extraction this file's guard relies on is far too mangled to parse meaning from. So this
refuses rather than half-extracting, exactly as `scripts/validate_air.py` does about its
OpenAQ key.

That is not a hole in the zero-budget claim: this is a **build step**, run once by a
maintainer, and its output is data. No route needs it and no deployment needs a key to
serve what it produced.

**The coverage table is the output that matters**, not the measure list. Most of what a
clean-air plan commits to — fuel sulfur limits, catalytic converters, CNG policy,
inspection regimes — cannot be said with a canopy fraction and an emission fraction, and
printing only the measures that happened to fit would describe a document this tool
understands far better than it does.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from terrarium.config import Settings, get_settings
from terrarium.policy.extract import ExtractionUnavailable, extract
from terrarium.policy.to_plan import to_plan

logger = logging.getLogger("extract_policy")

MEASURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_measures (
    document        TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    title           TEXT NOT NULL,
    sector          TEXT NOT NULL,
    target          TEXT,
    target_year     INTEGER,
    source_page     INTEGER,
    quote           TEXT NOT NULL,
    expressible     BOOLEAN NOT NULL,
    canopy_fraction DOUBLE,
    emission_fraction DOUBLE,
    extracted_at    TIMESTAMP NOT NULL
);
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, help="One document instead of every downloaded one")
    parser.add_argument("--json", type=Path, help="Also write the measures here")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the table without writing to DuckDB",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings: Settings = get_settings()

    documents = [args.pdf] if args.pdf else sorted(settings.policy_dir.glob("*.pdf"))
    if not documents:
        logger.error(
            "no PDFs in %s. Run scripts/ingest_policy.py first.", settings.policy_dir
        )
        return 1

    conn = None if args.dry_run else duckdb.connect(str(settings.duckdb_path))
    if conn is not None:
        conn.execute(MEASURES_SCHEMA)

    exported: list[dict[str, object]] = []
    try:
        for path in documents:
            try:
                measures, coverage = extract(
                    path.read_bytes(), document=path.stem, settings=settings
                )
            except ExtractionUnavailable as exc:
                logger.error("%s", exc)
                return 2

            print()
            print(coverage.summary)
            if coverage.dropped_quotes:
                # Reported rather than merely absent, for the same reason `evidence`
                # reports its rejected citations: a fabrication that is silently filtered
                # is a fabrication nobody knows the model produced.
                print(
                    f"  {len(coverage.dropped_quotes)} quote(s) were not found in the "
                    "document and their measures were dropped:"
                )
                for quote in coverage.dropped_quotes:
                    print(f"    - {quote!r}")
            print()
            print(f"  {'sector':<16} {'lever':<26} measure")
            print(f"  {'-' * 16} {'-' * 26} {'-' * 40}")

            for measure in measures:
                mapped = to_plan(measure)
                if mapped is None:
                    lever = "— no lever for this —"
                    canopy = emission = None
                else:
                    planting = mapped.plan.planting
                    restriction = mapped.plan.restriction
                    canopy = planting.canopy_fraction_added if planting else None
                    emission = restriction.emission_fraction_removed if restriction else None
                    lever = (
                        f"canopy +{canopy:.0%}{' (assumed)' if mapped.assumed else ''}"
                        if canopy is not None
                        else f"emissions -{emission:.0%}"
                        if emission is not None
                        else "—"
                    )

                print(f"  {measure.sector:<16} {lever:<26} {measure.title[:52]}")

                exported.append(
                    {
                        "document": measure.document,
                        "sector": measure.sector,
                        "title": measure.title,
                        "target": measure.target,
                        "target_year": measure.target_year,
                        "source_page": measure.source_page,
                        "quote": measure.quote,
                        "expressible": mapped is not None,
                        "canopy_fraction": canopy,
                        "emission_fraction": emission,
                        "basis": mapped.basis if mapped else None,
                    }
                )

                if conn is not None:
                    conn.execute(
                        "INSERT INTO policy_measures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            measure.document,
                            measure.document_sha256,
                            measure.title,
                            measure.sector,
                            measure.target,
                            measure.target_year,
                            measure.source_page,
                            measure.quote,
                            mapped is not None,
                            canopy,
                            emission,
                            datetime.now(UTC),
                        ],
                    )
    finally:
        if conn is not None:
            conn.close()

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(exported, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("wrote %d measures to %s", len(exported), args.json)

    print()
    print(
        "Read the miss rate as a fact about the two levers, not about the extractor. A "
        "canopy fraction and an emission fraction cannot express a fuel standard."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
