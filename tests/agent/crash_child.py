# tests/agent/crash_child.py
"""A loop run in its own process, so the parent can actually ``kill -9`` it.

Batch V4-F4's DoD says `kill -9`, and it means it. An in-process
``asyncio.CancelledError`` is a *graceful* cancel in this system — the loop
catches it, writes ``finish("cancelled")`` and the journal is terminal — which is
correct behaviour and the opposite of what a crash leaves. Only a real signal
produces the state resume exists for: a journal with no finish line, whatever had
been flushed, and nothing else.

Run as::

    python -m tests.agent.crash_child <workspace> <journal_root> <session> <turns>

Prints the journal path on the first line and then a line per completed
iteration, both flushed, so the parent can watch progress and choose its moment.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


async def main() -> int:
    workspace = Path(sys.argv[1])
    journal_root = Path(sys.argv[2])
    session = sys.argv[3]
    turns = int(sys.argv[4])

    from gitpilot.agent.model_profile import resolve_profile
    from gitpilot.agent.providers.base import ProviderResponse, RawToolCall
    from gitpilot.agent.runner import AgentRunner, RunRequest

    class _Slow:
        """Answers a step at a time, slowly enough to be interrupted."""

        name = "fake"
        model = "gpt-4o-mini"
        supports_native_tools = True
        supports_parallel_tool_calls = False
        supports_streaming = False

        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, messages, *, tools=None, system=None, **opts):
            self.calls += 1
            print(f"iteration {self.calls}", flush=True)
            await asyncio.sleep(0.05)
            if self.calls > turns:
                return ProviderResponse(text="finished the work")
            return ProviderResponse(
                text=f"step {self.calls}",
                tool_calls=[RawToolCall(
                    id=f"c{self.calls}", name="fs.read",
                    arguments={"path": "README.md"},
                )],
            )

        def stream(self, *args, **kwargs):
            raise NotImplementedError

    runner = AgentRunner()
    request = RunRequest(
        task="analyse and improve the parser",
        session_id=session,
        workspace_root=workspace,
        journal_root=journal_root,
        max_iterations=turns + 5,
    )
    profile = resolve_profile(
        "openai", "gpt-4o-mini", cache_path=journal_root / "probe.json",
    )
    loop, ctx = runner.build(request, provider=_Slow(), profile=profile)

    # The parent needs this before anything else happens.
    print(f"journal {ctx.journal.path}", flush=True)

    await loop.run(ctx)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
