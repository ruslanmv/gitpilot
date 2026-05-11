"""Tests for the error envelope (Batch P1-D)."""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot import flags
from gitpilot.errors import (
    FLAG_ERROR_ENVELOPE,
    GitPilotError,
    NotFoundError,
    UpstreamError,
    ValidationError,
    error_envelope,
    error_envelope_response,
    wrap_errors_envelope,
)


@pytest.fixture(autouse=True)
def _isolate_flags() -> Iterator[None]:
    flags.clear_all_overrides()
    yield
    flags.clear_all_overrides()


# ----------------------------------------------------------------------
# Pure-function shape
# ----------------------------------------------------------------------

def test_envelope_for_gitpilot_error_has_full_payload() -> None:
    err = GitPilotError(
        code="sandbox.unreachable",
        message="MatrixLab is down",
        hint="start the runner",
        doc_url="https://docs.gitpilot.dev/errors/sandbox-unreachable",
        status_code=503,
    )
    payload = error_envelope(err)
    assert payload["error"]["code"] == "sandbox.unreachable"
    assert payload["error"]["message"] == "MatrixLab is down"
    assert payload["error"]["hint"] == "start the runner"
    assert payload["error"]["doc_url"].endswith("sandbox-unreachable")
    assert isinstance(payload["trace_id"], str) and payload["trace_id"]


def test_envelope_for_unknown_error_uses_fallback_code() -> None:
    payload = error_envelope(RuntimeError("kaboom"))
    assert payload["error"]["code"] == "internal.unexpected"
    assert "kaboom" in payload["error"]["message"]
    assert "GITPILOT_DEBUG" in payload["error"]["hint"]


def test_subclasses_carry_canonical_codes() -> None:
    assert ValidationError("bad").code == "request.invalid"
    assert ValidationError("bad").status_code == 400
    assert NotFoundError("missing").code == "resource.not_found"
    assert NotFoundError("missing").status_code == 404
    assert UpstreamError("upstream broke").status_code == 502


def test_trace_id_can_be_supplied() -> None:
    payload = error_envelope(RuntimeError("x"), trace_id="abc123")
    assert payload["trace_id"] == "abc123"


# ----------------------------------------------------------------------
# Response helper
# ----------------------------------------------------------------------

def test_response_uses_status_code_from_error() -> None:
    err = NotFoundError("nope")
    resp = error_envelope_response(err)
    assert resp.status_code == 404


def test_response_defaults_to_500_for_unknown_errors() -> None:
    resp = error_envelope_response(RuntimeError("boom"))
    assert resp.status_code == 500


# ----------------------------------------------------------------------
# Decorator behaviour — the heart of the batch
# ----------------------------------------------------------------------

def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/raises-known")
    @wrap_errors_envelope
    async def raises_known() -> dict:
        raise NotFoundError("nope")

    @app.get("/raises-generic")
    @wrap_errors_envelope
    async def raises_generic() -> dict:
        raise RuntimeError("kaboom")

    @app.get("/happy")
    @wrap_errors_envelope
    async def happy() -> dict:
        return {"ok": True}

    return app


def test_decorator_passes_through_when_flag_off() -> None:
    flags.set_override(FLAG_ERROR_ENVELOPE, False)
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # Legacy FastAPI behaviour for a bare RuntimeError is a 500 with body
    # "Internal Server Error" (plain text, not JSON).  That's the legacy
    # shape we promise to leave untouched when the flag is off.
    resp = client.get("/raises-generic")
    assert resp.status_code == 500
    assert "Internal Server Error" in resp.text


def test_decorator_emits_envelope_when_flag_on() -> None:
    flags.set_override(FLAG_ERROR_ENVELOPE, True)
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/raises-generic")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal.unexpected"
    assert "kaboom" in body["error"]["message"]
    assert "trace_id" in body


def test_decorator_propagates_status_code() -> None:
    flags.set_override(FLAG_ERROR_ENVELOPE, True)
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/raises-known")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "resource.not_found"


def test_decorator_does_not_touch_happy_path() -> None:
    flags.set_override(FLAG_ERROR_ENVELOPE, True)
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/happy")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_decorator_is_idempotent_when_already_wrapped() -> None:
    flags.set_override(FLAG_ERROR_ENVELOPE, True)

    @wrap_errors_envelope
    @wrap_errors_envelope
    async def doubly() -> dict:
        raise ValidationError("nope")

    # Second wrap must not change the contract.
    app = FastAPI()
    app.get("/doubly")(doubly)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/doubly")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "request.invalid"
