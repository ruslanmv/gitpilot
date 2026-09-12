"""Production ToolRegistry with durable replay protection for mutations.

The base :class:`ToolRegistry` intentionally stays a small, predictable library
primitive.  This subclass adds the runtime-only contract GitPilot needs once a
tool call belongs to a real run/session:

* safe/read-only tools keep the base registry's zero-ledger fast path;
* mutating tools use the stable provider/tool-call id as the idempotency key;
* that key is bound to the canonical tool id, exact arguments, run and target;
* successful retries replay the original ToolResult without repeating effects;
* ambiguous failures fail closed instead of automatically duplicating a remote
  action whose response may simply have been lost.

Standalone toolkit calls without a run/session id deliberately bypass the ledger.
They have no durable approval identity to resume and are frequently used by SDK
consumers, tests and parity harnesses with short synthetic call ids.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Dict, Mapping

from ..idempotency import IdempotencyError, get_idempotency_store
from .registry import (
    Effect,
    ToolCall,
    ToolError,
    ToolExecutionContext,
    ToolHandler,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)

_MUTATING_EFFECTS = frozenset({
    Effect.WRITES_FS,
    Effect.GIT_LOCAL,
    Effect.GIT_REMOTE,
    Effect.FORGE_WRITE,
    Effect.EXTERNAL_WRITE,
})


def _mutating(spec: ToolSpec) -> bool:
    return bool(spec.effects & _MUTATING_EFFECTS)


def _scope(ctx: ToolExecutionContext, spec: ToolSpec) -> str | None:
    """Return a durable scope, or None for direct/ephemeral library calls."""
    run = ctx.run_id or ctx.session_id
    if not run:
        return None
    if ctx.workspace is not None:
        target = f"workspace:{ctx.workspace.root}"
    elif ctx.repo is not None:
        target = f"repo:{ctx.repo.full_name}:{ctx.repo.branch or 'HEAD'}"
    else:
        target = "unbound"
    return f"runtime:{run}:{target}:{spec.id}"


def _payload(result: ToolResult) -> Dict[str, Any]:
    return {
        "ok": result.ok,
        "content": result.content,
        "data": result.data,
        "error": result.error,
        "denied": result.denied,
    }


def _result_from_payload(call: ToolCall, payload: Mapping[str, Any]) -> ToolResult:
    return ToolResult(
        call_id=call.id,
        tool=call.tool,
        ok=bool(payload.get("ok")),
        content=str(payload.get("content") or ""),
        data=dict(payload.get("data") or {}) or None,
        error=payload.get("error"),
        denied=bool(payload.get("denied")),
    )


async def _invoke(handler: ToolHandler, call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """Match ToolRegistry's async/sync dispatch without blocking the event loop."""
    if inspect.iscoroutinefunction(handler):
        raw: Any = handler(call, ctx)
    else:
        raw = await asyncio.to_thread(handler, call, ctx)

    if inspect.isawaitable(raw):
        raw = await raw
    if not isinstance(raw, ToolResult):
        # This is a programming error, not a model/tool error.  Preserve the base
        # registry contract by surfacing ToolError to its execute() wrapper.
        raise ToolError(
            f"tool {call.tool!r} returned {type(raw).__name__}, expected ToolResult"
        )
    return raw


class RuntimeToolRegistry(ToolRegistry):
    """ToolRegistry whose mutating handlers become replay-safe in real runs."""

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if not _mutating(spec):
            super().register(spec, handler)
            return

        async def protected(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
            scope = _scope(ctx, spec)
            if scope is None:
                # Direct library/tests have no approval/run identity.  Keep their
                # semantics identical to the base registry and avoid disk I/O.
                return await _invoke(handler, call, ctx)

            store = ctx.extras.get("idempotency_store") or get_idempotency_store()

            async def operation() -> Dict[str, Any]:
                result = await _invoke(handler, call, ctx)
                return _payload(result)

            try:
                stored = await store.run_once_async(
                    scope=scope,
                    idempotency_key=call.id,
                    arguments={"tool": spec.id, "arguments": call.arguments or {}},
                    operation=operation,
                )
            except IdempotencyError as exc:
                return ToolResult.failure(
                    call,
                    f"{spec.id} was not executed: {exc}",
                    error="idempotency_guard",
                    data={"retry_safe": False, "requires_reconciliation": True},
                )
            return _result_from_payload(call, stored)

        super().register(spec, protected)
