"""The hand-mirrored TypeScript client, checked against the schema it mirrors (A18).

`web/src/api/client.ts` restates `api/schemas/` by hand, and the plan named that seam as
the one most likely to drift. It has drifted once, measurably: `SimulateResponse` carried
no `air` field for the whole of Phase 9, so a client could not have read the air result
even if a panel had existed for it.

**`tsc -b` cannot catch this and CI used to claim it could.** TypeScript type-checks
TypeScript against TypeScript; it never sees a Pydantic model, so a field added to a
response schema and forgotten in `client.ts` compiles perfectly and is simply invisible at
runtime. This test is the thing that actually looks at both sides.

Deliberately a field-name diff rather than generated types. Codegen would mean a new
dependency, a build step and a generated file to keep in the tree, to replace a hand-written
client whose comments carry the reasoning for half the fields. The failure mode worth
catching is a *missing or misnamed field*, and that is what this compares.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from terrarium.api.main import create_app

CLIENT_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api" / "client.ts"

# TS interfaces whose name differs from the schema they mirror. The client drops the
# `Response` suffix where the type is a nested object rather than a whole response body.
ALIASES = {
    "DeltaStats": "DeltaStatsResponse",
    "Equity": "EquityResponse",
    "Air": "AirResponse",
    "DecileShare": "DecileShareResponse",
    "PlantTreesAction": "PlantTrees",
    "RestrictVehiclesAction": "RestrictVehicles",
}

# Declared inline in FastAPI's schema (a bare dict) rather than as a named model, so there
# is nothing on the Python side to diff against.
NOT_IN_OPENAPI = {"GeoJsonPolygon"}

# FastAPI's own error shapes. The client has no reason to mirror them.
IGNORED_SCHEMAS = {"HTTPValidationError", "ValidationError"}


def _openapi_schemas() -> dict[str, Any]:
    with TestClient(create_app()) as client:
        spec = client.get("/openapi.json").json()
    schemas: dict[str, Any] = spec["components"]["schemas"]
    return schemas


def _ts_interfaces() -> dict[str, set[str]]:
    """Field names per `export interface` in client.ts.

    Flat two-space fields only, which is how the file is written throughout. A nested
    object literal would be missed, so if one ever appears this parser must grow with it —
    hence the emptiness check in `test_parser_still_finds_interfaces`.
    """
    source = CLIENT_TS.read_text(encoding="utf-8")
    return {
        match.group(1): set(re.findall(r"^ {2}(\w+)\??:", match.group(2), re.M))
        for match in re.finditer(r"export interface (\w+) \{(.*?)\n\}", source, re.S)
    }


pytestmark = pytest.mark.skipif(
    not CLIENT_TS.exists(), reason="web/ is not present (installed wheel, not a checkout)"
)


def test_parser_still_finds_interfaces() -> None:
    """Guard the guard: a parser that silently matches nothing would pass everything."""
    interfaces = _ts_interfaces()
    assert len(interfaces) > 20, f"only parsed {len(interfaces)} interfaces from client.ts"
    assert interfaces["SimulateResponse"], "SimulateResponse parsed with no fields"


def test_client_types_match_the_schemas_they_mirror() -> None:
    """Every mirrored interface carries exactly the fields its schema declares.

    Both directions matter. A field the server sends and the client lacks is data the UI
    cannot reach (the Phase 9 `air` bug); a field the client expects and the server never
    sends is a `undefined` that reads as a legitimate zero.
    """
    schemas = _openapi_schemas()
    problems: list[str] = []

    for ts_name, ts_fields in sorted(_ts_interfaces().items()):
        if ts_name in NOT_IN_OPENAPI:
            continue
        schema_name = ALIASES.get(ts_name, ts_name)
        if schema_name not in schemas:
            problems.append(f"{ts_name}: no schema named {schema_name!r} — renamed or removed")
            continue

        declared = set(schemas[schema_name].get("properties", {}))
        if missing := declared - ts_fields:
            problems.append(
                f"{ts_name} <- {schema_name}: server sends {sorted(missing)}, "
                "client.ts does not declare it"
            )
        if extra := ts_fields - declared:
            problems.append(
                f"{ts_name} <- {schema_name}: client.ts declares {sorted(extra)}, "
                "server never sends it"
            )

    assert not problems, "web/src/api/client.ts has drifted from api/schemas/:\n  " + "\n  ".join(
        problems
    )


def test_every_response_schema_is_mirrored() -> None:
    """A whole new response model must reach the client, not just a new field on an old one.

    This is the half a field-diff cannot see: a schema the client has never heard of has no
    interface to diff, so it would pass the test above by being absent entirely.
    """
    mirrored = {ALIASES.get(name, name) for name in _ts_interfaces()}
    unmirrored = sorted(
        name
        for name in _openapi_schemas()
        if name not in mirrored and name not in IGNORED_SCHEMAS and name.endswith("Response")
    )
    assert not unmirrored, (
        f"response schemas with no interface in client.ts: {unmirrored}. "
        "Mirror them, or add them to IGNORED_SCHEMAS with the reason."
    )
