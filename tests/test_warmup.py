"""Tests for the model warmup hook — Batch P2-E."""
from __future__ import annotations

import asyncio
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot import flags
from gitpilot.warmup import (
    FLAG_MODEL_WARMUP,
    WarmupResult,
    register_warmup,
    reset_registry_for_tests,
    run_warmup_async,
    run_warmup_now,
)


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    flags.clear_all_overrides()
    reset_registry_for_tests()
    yield
    flags.clear_all_overrides()
    reset_registry_for_tests()


# ----------------------------------------------------------------------
# Flag-off short-circuit
# ----------------------------------------------------------------------

def test_flag_off_skips() -> None:
    flags.set_override(FLAG_MODEL_WARMUP, False)
    result = run_warmup_now()
    assert result.skipped is True
    assert result.ok is True
    assert result.duration_ms == 0


# ----------------------------------------------------------------------
# Successful warmup
# ----------------------------------------------------------------------

def test_flag_on_runs_warm_fn() -> None:
    flags.set_override(FLAG_MODEL_WARMUP, True)
    called = asyncio.Event()

    async def warm() -> None:
        called.set()

    result = run_warmup_now(warm_fn=warm, provider_hint="ollama", model_hint="llama3")
    assert result.skipped is False
    assert result.ok is True
    assert called.is_set()
    assert result.provider == "ollama"
    assert result.model == "llama3"


def test_to_dict_round_trips() -> None:
    flags.set_override(FLAG_MODEL_WARMUP, True)

    async def warm() -> None:
        return None

    result = run_warmup_now(warm_fn=warm)
    import json
    payload = json.dumps(result.to_dict())
    assert "duration_ms" in payload


# ----------------------------------------------------------------------
# Failure modes
# ----------------------------------------------------------------------

def test_timeout_is_caught_and_warned(caplog: pytest.LogCaptureFixture) -> None:
    flags.set_override(FLAG_MODEL_WARMUP, True)

    async def slow_warm() -> None:
        await asyncio.sleep(2)

    with caplog.at_level("WARNING", logger="gitpilot.warmup"):
        result = run_warmup_now(warm_fn=slow_warm, timeout=0.1)
    assert result.ok is False
    assert result.error == "timeout"
    assert any("timed out" in r.message for r in caplog.records)


def test_exception_is_caught_and_warned(caplog: pytest.LogCaptureFixture) -> None:
    flags.set_override(FLAG_MODEL_WARMUP, True)

    async def broken_warm() -> None:
        raise RuntimeError("nope")

    with caplog.at_level("WARNING", logger="gitpilot.warmup"):
        result = run_warmup_now(warm_fn=broken_warm)
    assert result.ok is False
    assert "nope" in (result.error or "")


# ----------------------------------------------------------------------
# FastAPI registration
# ----------------------------------------------------------------------

def test_register_returns_false_when_flag_off() -> None:
    flags.set_override(FLAG_MODEL_WARMUP, False)
    app = FastAPI()
    assert register_warmup(app) is False


def test_register_idempotent_when_called_twice() -> None:
    flags.set_override(FLAG_MODEL_WARMUP, True)

    async def warm() -> None:
        return None

    app = FastAPI()
    assert register_warmup(app, warm_fn=warm) is True
    assert register_warmup(app, warm_fn=warm) is False


def test_register_warmup_runs_on_startup(caplog: pytest.LogCaptureFixture) -> None:
    flags.set_override(FLAG_MODEL_WARMUP, True)
    ran = asyncio.Event()

    async def warm() -> None:
        ran.set()

    app = FastAPI()
    assert register_warmup(app, warm_fn=warm, timeout=1.0) is True

    with caplog.at_level("INFO", logger="gitpilot.warmup"):
        with TestClient(app):
            # Entering the context fires the startup event.
            pass

    assert ran.is_set()
    assert any("warmup ok" in r.message for r in caplog.records)
    # Result visible on app.state for /health-style endpoints.
    assert isinstance(app.state.warmup, dict)
    assert app.state.warmup["ok"] is True


def test_warmup_completes_within_three_seconds() -> None:
    """The DoD requires startup to be unblocked within the timeout."""
    flags.set_override(FLAG_MODEL_WARMUP, True)

    async def warm() -> None:
        return None

    import time
    start = time.monotonic()
    result = run_warmup_now(warm_fn=warm, timeout=3.0)
    elapsed = time.monotonic() - start
    assert result.ok is True
    assert elapsed < 3.0
