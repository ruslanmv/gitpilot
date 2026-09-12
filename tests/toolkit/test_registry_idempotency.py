"""Idempotency invariants at the canonical ToolRegistry boundary."""
from __future__ import annotations

import asyncio

from gitpilot.idempotency import IdempotencyStore
from gitpilot.toolkit import (
    Effect,
    LocalWorkspace,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def _spec(tool_id: str, *, mutating: bool) -> ToolSpec:
    return ToolSpec(
        id=tool_id,
        title=tool_id,
        description="test tool",
        params_schema=SCHEMA,
        risk=Risk.APPROVAL if mutating else Risk.SAFE,
        effects=(
            frozenset({Effect.WRITES_FS})
            if mutating
            else frozenset({Effect.READS_FS})
        ),
    )


def _ctx(tmp_path, store: IdempotencyStore) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace=LocalWorkspace(root=tmp_path),
        session_id="s1",
        run_id="r1",
        extras={"idempotency_store": store},
    )


def test_mutating_retry_replays_result_without_reexecuting(tmp_path):
    registry = ToolRegistry()
    calls: list[dict] = []

    async def handler(call, ctx):
        calls.append(dict(call.arguments))
        return ToolResult.success(
            call,
            "written",
            data={"path": call.arguments["path"], "paths_written": [call.arguments["path"]]},
        )

    registry.register(_spec("fs.write", mutating=True), handler)
    store = IdempotencyStore(tmp_path / "idem.sqlite3")
    ctx = _ctx(tmp_path, store)
    call = ToolCall(
        id="approval-123",
        tool="fs.write",
        arguments={"path": "a.txt", "content": "hello"},
    )

    first = asyncio.run(registry.execute(call, ctx))
    second = asyncio.run(registry.execute(call, ctx))

    assert first.ok and second.ok
    assert first.content == second.content == "written"
    assert first.data == second.data
    assert calls == [{"path": "a.txt", "content": "hello"}]


def test_approval_id_is_bound_to_exact_arguments(tmp_path):
    registry = ToolRegistry()
    calls = 0

    async def handler(call, ctx):
        nonlocal calls
        calls += 1
        return ToolResult.success(call, "written")

    registry.register(_spec("fs.write", mutating=True), handler)
    ctx = _ctx(tmp_path, IdempotencyStore(tmp_path / "idem.sqlite3"))

    first = asyncio.run(
        registry.execute(
            ToolCall(
                id="approval-1",
                tool="fs.write",
                arguments={"path": "a.txt", "content": "approved"},
            ),
            ctx,
        )
    )
    changed = asyncio.run(
        registry.execute(
            ToolCall(
                id="approval-1",
                tool="fs.write",
                arguments={"path": "a.txt", "content": "different"},
            ),
            ctx,
        )
    )

    assert first.ok
    assert not changed.ok
    assert changed.error == "idempotency_guard"
    assert changed.data == {"retry_safe": False, "requires_reconciliation": True}
    assert calls == 1


def test_distinct_approval_ids_are_distinct_mutations(tmp_path):
    registry = ToolRegistry()
    calls = 0

    async def handler(call, ctx):
        nonlocal calls
        calls += 1
        return ToolResult.success(call, f"write-{calls}")

    registry.register(_spec("fs.write", mutating=True), handler)
    ctx = _ctx(tmp_path, IdempotencyStore(tmp_path / "idem.sqlite3"))

    one = asyncio.run(
        registry.execute(
            ToolCall(id="approval-1", tool="fs.write", arguments={"path": "a.txt"}),
            ctx,
        )
    )
    two = asyncio.run(
        registry.execute(
            ToolCall(id="approval-2", tool="fs.write", arguments={"path": "a.txt"}),
            ctx,
        )
    )

    assert one.content == "write-1"
    assert two.content == "write-2"
    assert calls == 2


def test_safe_reads_stay_on_zero_ledger_fast_path(tmp_path):
    registry = ToolRegistry()
    calls = 0

    async def handler(call, ctx):
        nonlocal calls
        calls += 1
        return ToolResult.success(call, f"read-{calls}")

    registry.register(_spec("fs.read", mutating=False), handler)

    class ExplodingStore:
        async def run_once_async(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("safe reads must not touch idempotency storage")

    ctx = ToolExecutionContext(
        workspace=LocalWorkspace(root=tmp_path),
        session_id="s1",
        run_id="r1",
        extras={"idempotency_store": ExplodingStore()},
    )
    call = ToolCall(id="read-1", tool="fs.read", arguments={"path": "a.txt"})

    first = asyncio.run(registry.execute(call, ctx))
    second = asyncio.run(registry.execute(call, ctx))

    assert first.content == "read-1"
    assert second.content == "read-2"
    assert calls == 2


def test_failed_mutation_becomes_indeterminate_and_is_not_retried(tmp_path):
    registry = ToolRegistry()
    calls = 0

    async def handler(call, ctx):
        nonlocal calls
        calls += 1
        raise TimeoutError("downstream response lost")

    registry.register(_spec("fs.write", mutating=True), handler)
    ctx = _ctx(tmp_path, IdempotencyStore(tmp_path / "idem.sqlite3"))
    call = ToolCall(id="approval-timeout", tool="fs.write", arguments={"path": "a.txt"})

    first = asyncio.run(registry.execute(call, ctx))
    second = asyncio.run(registry.execute(call, ctx))

    assert not first.ok and first.error == "TimeoutError"
    assert not second.ok and second.error == "idempotency_guard"
    assert second.data == {"retry_safe": False, "requires_reconciliation": True}
    assert calls == 1
