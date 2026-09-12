"""Production ToolRegistry with durable replay protection for mutations.

The base :class:`ToolRegistry` remains a small, predictable library primitive.
This subclass adds the runtime-only contract GitPilot needs once a tool call
belongs to a real run/session:

* safe/read-only tools keep the base registry's zero-ledger fast path;
* direct SDK/toolkit calls without a run/session keep byte-for-byte base
  execution semantics;
* mutating runtime calls use the stable provider/tool-call id as the
  idempotency key;
* the key is bound to the canonical tool id, exact arguments, run, and target;
* successful retries replay the original :class:`ToolResult` without repeating
  side effects;
* ambiguous handler failures fail closed instead of automatically duplicating a
  remote action whose response may simply have been lost.

The important layering rule is that replay protection lives at the production
execution boundary, not inside every tool handler and not in the generic
registry. That keeps tests, SDK use, and read-heavy workloads inexpensive while
making the real agent path durable by construction.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from ..idempotency import IdempotencyError, get_idempotency_store
from .registry import (
    Effect,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    validate_arguments,
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
    """Return a durable scope, or ``None`` for direct/ephemeral calls."""
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
    """JSON-friendly result persisted for exact replay after a restart."""
    return {
        "tool": result.tool,
        "ok": result.ok,
        "content": result.content,
        "data": result.data,
        "error": result.error,
        "denied": result.denied,
    }


def _result_from_payload(call: ToolCall, payload: Mapping[str, Any]) -> ToolResult:
    return ToolResult(
        call_id=call.id,
        tool=str(payload.get("tool") or call.tool),
        ok=bool(payload.get("ok")),
        content=str(payload.get("content") or ""),
        data=dict(payload.get("data") or {}) or None,
        error=payload.get("error"),
        denied=bool(payload.get("denied")),
    )


class _MutationOutcomeUncertain(RuntimeError):
    """Carry the first failure while making the ledger fail closed on retry."""

    def __init__(self, result: ToolResult) -> None:
        super().__init__(result.content)
        self.result = result


class RuntimeToolRegistry(ToolRegistry):
    """ToolRegistry whose mutating calls become replay-safe in real runs."""

    async def execute(
        self,
        call: ToolCall,
        ctx: ToolExecutionContext,
    ) -> ToolResult:
        # Unknown tools and invalid arguments are deterministic input failures,
        # not attempted mutations. Let the base registry return its normal,
        # corrective ToolResult without creating a ledger record.
        resolved = self.resolve(call.tool)
        if resolved is None:
            return await super().execute(call, ctx)

        spec = self.spec(resolved)
        if validate_arguments(spec.params_schema, call.arguments or {}):
            return await super().execute(call, ctx)

        scope = _scope(ctx, spec)
        if not _mutating(spec) or scope is None:
            # This branch is deliberately the exact base execution path. In
            # particular, standalone toolkit calls must not gain persistence,
            # altered timeout semantics, or hidden disk I/O merely because the
            # default registry happens to be runtime-capable.
            return await super().execute(call, ctx)

        store = ctx.extras.get("idempotency_store") or get_idempotency_store()
        identity = {"tool": resolved, "arguments": call.arguments or {}}

        async def operation() -> Dict[str, Any]:
            result = await super(RuntimeToolRegistry, self).execute(call, ctx)
            if not result.ok:
                # A downstream failure can be ambiguous: the service may have
                # committed the mutation and only lost the response. Raising
                # here lets IdempotencyStore mark the key indeterminate. We then
                # return the original ToolResult for this first attempt; only a
                # retry is blocked pending reconciliation/new approval.
                raise _MutationOutcomeUncertain(result)
            return _payload(result)

        try:
            stored = await store.run_once_async(
                scope=scope,
                idempotency_key=call.id,
                arguments=identity,
                operation=operation,
            )
        except _MutationOutcomeUncertain as exc:
            return exc.result
        except IdempotencyError as exc:
            return ToolResult.failure(
                call,
                f"{resolved} was not executed: {exc}",
                error="idempotency_guard",
                data={"retry_safe": False, "requires_reconciliation": True},
            )

        return _result_from_payload(call, stored)
