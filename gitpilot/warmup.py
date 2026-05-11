# gitpilot/warmup.py
"""Model warmup — fire a tiny call at startup so the first user prompt
doesn't sit on a cold model.

Batch P2-E — additive, flag-gated.  Most local-model backends (Ollama,
LM Studio, vLLM) load weights lazily on the first request.  Even hosted
providers can suffer a one-off TCP / TLS / quota check that the user
feels as a noticeable hesitation on the *first* turn.  A 1-token
warmup before the user types anything moves that cost off the critical
path.

Behaviour matrix
----------------
* ``model_warmup`` flag off (default) → :func:`run_warmup` returns a
  no-op result.  No network call.
* Flag on → a 1-token completion is sent to the active provider.
  Success is logged at INFO.  Timeout or error is logged at WARNING
  and surfaced in the returned :class:`WarmupResult` but never raised
  to the caller.

Wiring
------
The module exposes :func:`run_warmup_async` (a coroutine) and a sync
shim :func:`run_warmup_now` that callers can attach to a FastAPI
``startup`` hook or a CLI ``serve`` command.  Registration is done
once via :func:`register_warmup`, idempotent across reloads.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from . import flags

logger = logging.getLogger(__name__)

FLAG_MODEL_WARMUP = "model_warmup"
DEFAULT_TIMEOUT_SEC = 3.0
WARMUP_PROMPT = "ok"


@dataclass(frozen=True)
class WarmupResult:
    """Outcome of a single warmup attempt."""

    skipped: bool
    ok: bool
    duration_ms: int
    provider: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skipped": self.skipped,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "provider": self.provider,
            "model": self.model,
            "error": self.error,
        }


# Pluggable warmup callable so tests can avoid hitting the real LLM.
WarmFn = Callable[[], Awaitable[None]]


# ----------------------------------------------------------------------
# Default warmup implementation
# ----------------------------------------------------------------------

async def _default_warm() -> None:
    """Send a 1-token completion to whichever provider is active.

    Imports :mod:`gitpilot.llm_provider` lazily so importing the warmup
    module is cheap and side-effect-free.  If anything in the LLM
    chain raises (missing key, unreachable host, unsupported provider)
    the exception bubbles up; :func:`run_warmup_async` translates it
    into a :class:`WarmupResult` with ``ok=False``.
    """
    from .llm_provider import build_llm  # local import — heavy

    llm = build_llm()
    call = getattr(llm, "call", None) or getattr(llm, "invoke", None)
    if call is None:
        raise RuntimeError("active LLM has no .call/.invoke interface")
    result = call(WARMUP_PROMPT)
    # CrewAI .call is sync; wrap if it returned a coroutine.
    if asyncio.iscoroutine(result):
        await result


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

async def run_warmup_async(
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    warm_fn: Optional[WarmFn] = None,
    enabled: Optional[bool] = None,
    provider_hint: Optional[str] = None,
    model_hint: Optional[str] = None,
) -> WarmupResult:
    """Run one warmup attempt and return the outcome.

    Never raises.  When the flag is off or ``enabled=False`` the call
    short-circuits with ``skipped=True``.
    """
    flag_on = enabled if enabled is not None else flags.is_on(FLAG_MODEL_WARMUP)
    if not flag_on:
        return WarmupResult(
            skipped=True, ok=True, duration_ms=0,
            provider=provider_hint, model=model_hint,
        )

    fn: WarmFn = warm_fn or _default_warm
    start = time.monotonic()
    try:
        # ``asyncio.wait_for`` guarantees the warmup never blocks
        # startup for longer than the timeout — important for serverless
        # / Kubernetes liveness probes.
        await asyncio.wait_for(fn(), timeout=timeout)
    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning(
            "model warmup timed out after %sms (provider=%s)",
            duration_ms, provider_hint or "?",
        )
        return WarmupResult(
            skipped=False, ok=False, duration_ms=duration_ms,
            provider=provider_hint, model=model_hint, error="timeout",
        )
    except Exception as exc:  # noqa: BLE001 — boundary
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning(
            "model warmup failed (provider=%s): %s",
            provider_hint or "?", exc,
        )
        return WarmupResult(
            skipped=False, ok=False, duration_ms=duration_ms,
            provider=provider_hint, model=model_hint, error=str(exc)[:240],
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "model warmup ok in %sms (provider=%s, model=%s)",
        duration_ms, provider_hint or "?", model_hint or "?",
    )
    return WarmupResult(
        skipped=False, ok=True, duration_ms=duration_ms,
        provider=provider_hint, model=model_hint,
    )


def run_warmup_now(**kwargs: Any) -> WarmupResult:
    """Synchronous shim — useful for CLI / non-async startup paths."""
    return asyncio.run(run_warmup_async(**kwargs))


# ----------------------------------------------------------------------
# FastAPI registration
# ----------------------------------------------------------------------

_REGISTERED_APPS: set[int] = set()


def register_warmup(
    app: Any,
    *,
    warm_fn: Optional[WarmFn] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> bool:
    """Attach a startup hook that runs :func:`run_warmup_async` once.

    Returns ``True`` if the hook was registered, ``False`` when the
    app already has one (idempotent across reloads) or when the flag is
    off at registration time.
    """
    if not flags.is_on(FLAG_MODEL_WARMUP):
        return False

    app_id = id(app)
    if app_id in _REGISTERED_APPS:
        return False
    _REGISTERED_APPS.add(app_id)

    # FastAPI is migrating away from ``on_event`` toward lifespan
    # handlers.  We use ``on_event`` here for compatibility with the
    # existing legacy FastAPI app which uses lifespans for other
    # init; adding a second lifespan would conflict.  The deprecation
    # is benign — a wholesale lifespan migration is tracked separately.
    @app.on_event("startup")  # type: ignore[untyped-decorator]
    async def _on_startup() -> None:
        provider = None
        model = None
        try:
            from .settings import get_settings  # local
            settings = get_settings()
            provider = getattr(settings.provider, "value", str(settings.provider))
            model = getattr(getattr(settings, provider, None), "model", None)
        except Exception:
            logger.debug("could not resolve provider/model for warmup", exc_info=True)
        result = await run_warmup_async(
            timeout=timeout, warm_fn=warm_fn,
            provider_hint=provider, model_hint=model,
        )
        # Make the result visible via app.state for /health-style endpoints.
        try:
            app.state.warmup = result.to_dict()
        except Exception:
            pass

    return True


def reset_registry_for_tests() -> None:
    """Clear the idempotency guard.  Test-only."""
    _REGISTERED_APPS.clear()
