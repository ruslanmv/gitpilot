# gitpilot/agent/headless.py
"""Running the loop with no transport — Batch V4-C4.

``gitpilot run --engine loop`` drives a run against a local checkout and prints
one JSON object per event to stdout.  That gives the trace tests a way to assert
on a real run without a server, and gives CI a way to use the engine without
inventing a client.

JSONL rather than a final blob on purpose: the point of the engine is the
trajectory, and a single summary object at the end tells you nothing about how it
got there.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .runner import AgentRunner, RunRequest


@dataclass
class HeadlessResult:
    """The outcome, plus every event the run emitted."""

    status: str
    answer: str
    events: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    journal_path: str = ""

    @property
    def success(self) -> bool:
        return self.status in ("completed", "degraded")

    def to_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "answer": self.answer,
                "summary": self.summary,
                "journal": self.journal_path,
            },
            indent=2,
            sort_keys=True,
        )


async def run_loop_headless(
    task: str,
    *,
    workspace: Path,
    session_id: str = "headless",
    max_iterations: Optional[int] = None,
    read_only: bool = False,
    model: Optional[str] = None,
    journal_root: Optional[Path] = None,
    emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    provider: Any = None,
    profile: Any = None,
) -> HeadlessResult:
    """Drive one run, streaming events to *emit* (stdout by default)."""
    events: List[Dict[str, Any]] = []
    sink = emit if emit is not None else _print_line

    class _Bus:
        """The minimum an ``AgentEventBus`` consumer needs, without a transport."""

        async def emit(self, event: Any) -> None:
            payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            events.append(payload)
            sink(payload)

    runner = AgentRunner()
    request = RunRequest(
        task=task,
        session_id=session_id,
        workspace_root=Path(workspace),
        max_iterations=max_iterations,
        read_only=read_only,
        model=model,
        journal_root=journal_root,
    )

    kwargs: Dict[str, Any] = {}
    if provider is not None:
        kwargs["provider"] = provider
    if profile is not None:
        kwargs["profile"] = profile

    result = await runner.run(request, bus=_Bus(), **kwargs)

    return HeadlessResult(
        status=result.status,
        answer=result.answer,
        events=events,
        summary=result.context.summary(),
        journal_path=str(result.context.journal.path),
    )


def _print_line(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()
