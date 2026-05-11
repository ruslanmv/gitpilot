# gitpilot/errors.py
"""Structured error envelope — Batch P1-D.

Lets every backend endpoint return a uniform error shape that the UI can
render as a friendly block::

    {
      "error": {
        "code":    "sandbox.unreachable",
        "message": "MatrixLab runner did not respond",
        "hint":    "Set GITPILOT_MATRIXLAB_URL or start the runner.",
        "doc_url": "https://docs.gitpilot.dev/errors/sandbox-unreachable"
      },
      "trace_id": "…"
    }

The envelope is opt-in via the ``error_envelope`` feature flag and the
:func:`wrap_errors_envelope` decorator.  When the flag is off (the
legacy default) the decorator is a passthrough — uncaught exceptions
bubble up to FastAPI exactly as before so existing clients see no
change.
"""
from __future__ import annotations

import functools
import logging
import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar, cast

from . import flags

logger = logging.getLogger(__name__)

FLAG_ERROR_ENVELOPE = "error_envelope"
DEFAULT_DOC_BASE = "https://docs.gitpilot.dev/errors"

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


# ----------------------------------------------------------------------
# Public exception type
# ----------------------------------------------------------------------

@dataclass
class GitPilotError(Exception):
    """Base error carrying structured fields for the envelope.

    ``code`` should be a dotted, stable identifier (``sandbox.unreachable``)
    that the UI can branch on; ``message`` is human-readable; ``hint``
    suggests a remedy; ``doc_url`` deep-links to documentation.
    """

    code: str
    message: str
    hint: Optional[str] = None
    doc_url: Optional[str] = None
    status_code: int = 500

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_envelope(self, trace_id: Optional[str] = None) -> Dict[str, Any]:
        return error_envelope(self, trace_id=trace_id)


# Convenience subclasses for common categories.

class ValidationError(GitPilotError):
    """Raised when a request fails input validation (HTTP 400)."""

    def __init__(self, message: str, *, hint: Optional[str] = None) -> None:
        super().__init__(
            code="request.invalid",
            message=message,
            hint=hint,
            doc_url=f"{DEFAULT_DOC_BASE}/request-invalid",
            status_code=400,
        )


class NotFoundError(GitPilotError):
    """Raised when a requested resource is missing (HTTP 404)."""

    def __init__(self, message: str, *, hint: Optional[str] = None) -> None:
        super().__init__(
            code="resource.not_found",
            message=message,
            hint=hint,
            doc_url=f"{DEFAULT_DOC_BASE}/resource-not-found",
            status_code=404,
        )


class UpstreamError(GitPilotError):
    """Raised when an upstream provider (LLM, MCP, GitHub) returns an
    unrecoverable error (HTTP 502)."""

    def __init__(self, message: str, *, hint: Optional[str] = None, code: str = "upstream.failure") -> None:
        super().__init__(
            code=code,
            message=message,
            hint=hint,
            doc_url=f"{DEFAULT_DOC_BASE}/upstream-failure",
            status_code=502,
        )


# ----------------------------------------------------------------------
# Envelope construction
# ----------------------------------------------------------------------

def error_envelope(
    err: BaseException,
    *,
    trace_id: Optional[str] = None,
    fallback_code: str = "internal.unexpected",
) -> Dict[str, Any]:
    """Render an exception as the canonical error payload."""
    if isinstance(err, GitPilotError):
        body: Dict[str, Any] = {
            "code": err.code,
            "message": err.message,
        }
        if err.hint:
            body["hint"] = err.hint
        if err.doc_url:
            body["doc_url"] = err.doc_url
    else:
        body = {
            "code": fallback_code,
            "message": str(err) or err.__class__.__name__,
            "hint": "Re-run with GITPILOT_DEBUG=1 for a traceback in the server log.",
            "doc_url": f"{DEFAULT_DOC_BASE}/internal-unexpected",
        }
    return {
        "error": body,
        "trace_id": trace_id or _new_trace_id(),
    }


def error_envelope_response(err: BaseException, *, trace_id: Optional[str] = None) -> Any:
    """Return a FastAPI ``JSONResponse`` carrying the envelope.

    Imports the FastAPI types lazily so the module remains importable in
    contexts where FastAPI isn't installed (CLI, tests).
    """
    from fastapi.responses import JSONResponse

    status = err.status_code if isinstance(err, GitPilotError) else 500
    return JSONResponse(status_code=status, content=error_envelope(err, trace_id=trace_id))


# ----------------------------------------------------------------------
# Endpoint decorator
# ----------------------------------------------------------------------

def wrap_errors_envelope(func: F) -> F:
    """Decorate an async FastAPI handler to emit the envelope.

    When the ``error_envelope`` flag is **off** the wrapper re-raises so
    the legacy FastAPI behaviour (default ``{detail: …}`` body or
    framework traceback) applies.  When the flag is **on** every
    uncaught exception is translated into the structured payload.

    The decorator is a no-op for handlers that return normally.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except GitPilotError as err:
            if flags.is_on(FLAG_ERROR_ENVELOPE):
                trace_id = _new_trace_id()
                logger.warning(
                    "GitPilotError code=%s trace_id=%s msg=%s",
                    err.code, trace_id, err.message,
                )
                return error_envelope_response(err, trace_id=trace_id)
            raise
        except Exception as err:  # noqa: BLE001 — top-of-stack adapter
            if flags.is_on(FLAG_ERROR_ENVELOPE):
                trace_id = _new_trace_id()
                logger.exception(
                    "unhandled exception in %s (trace_id=%s)", func.__name__, trace_id,
                )
                return error_envelope_response(err, trace_id=trace_id)
            raise

    return cast(F, wrapper)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def render_traceback_for_log(err: BaseException) -> str:
    """Return a short traceback suitable for structured logs."""
    return "".join(traceback.format_exception(type(err), err, err.__traceback__)).strip()
