"""The composition root's own middleware: body size, CORS. No network."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from terrarium.config import Settings


def test_a_declared_body_over_the_limit_is_rejected_before_parsing(client: TestClient) -> None:
    """F21: `Content-Length` alone is enough to refuse a 100 MB body without reading it."""
    response = client.post(
        "/simulate",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": str(2_000_000)},
    )
    assert response.status_code == 413


def test_a_normal_request_is_unaffected_by_the_body_limit(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_a_wildcard_cors_origin_fails_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """F23: the natural reach for a fresh deployment (`'["*"]'`) is refused with a message
    that says what to do instead, rather than silently accepted and made dangerous by
    `allow_credentials`."""
    with pytest.raises(ValueError, match="real origin"):
        Settings(cors_origins=["*"])


def test_a_configured_origin_still_gets_the_preflight_header(client: TestClient) -> None:
    response = client.options(
        "/simulate",
        headers={
            "origin": "http://localhost:5173",
            "access-control-request-method": "POST",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_credentials_are_not_advertised_to_a_cross_origin_caller(client: TestClient) -> None:
    """F23: nothing here uses cookies or HTTP auth, and a wildcard-friendly credentialed
    CORS config is the dangerous combination - `allow_credentials` must stay off."""
    response = client.options(
        "/simulate",
        headers={
            "origin": "http://localhost:5173",
            "access-control-request-method": "POST",
        },
    )
    assert "access-control-allow-credentials" not in response.headers


def test_unimplemented_methods_are_not_advertised(client: TestClient) -> None:
    response = client.options(
        "/simulate",
        headers={
            "origin": "http://localhost:5173",
            "access-control-request-method": "POST",
        },
    )
    allowed = response.headers.get("access-control-allow-methods", "")
    assert "DELETE" not in allowed
    assert "PUT" not in allowed
    assert "PATCH" not in allowed
