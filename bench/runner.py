# bench/runner.py
"""Running one benchmark cell — Batch V4-G4.

A cell is one ``(task, tier, engine)``. Running it means: build the fixture in a
throwaway directory, drive the engine, measure, then ask the task's own checker
whether it worked.

The metrics come from the run's own record rather than from instrumentation added
here. Tokens and approval counts are read out of the journal, which the loop writes
anyway — so the numbers in the report are the same numbers a user could audit after
the fact. The one thing measured from outside is wall time, because that is the one
thing the journal cannot know about itself.

Two engines, one protocol. :class:`LoopEngine` drives
:class:`~gitpilot.agent.runner.AgentRunner`; :class:`LegacyEngine` drives the CrewAI
dispatch. Both return the same :class:`CellMetrics`, which is what makes the
comparison in :mod:`bench.report` an apples-to-apples one rather than two different
measurements presented side by side.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple

from .matrix import ModelTier
from .tasks import BenchTask, Outcome

logger = logging.getLogger(__name__)

#: A cell that has not finished in this long is scored as a failure. A benchmark
#: that waits forever for one hung cell reports nothing at all.
DEFAULT_TIMEOUT_S = 300.0


@dataclass
class CellMetrics:
    """What one engine run cost."""

    wall_time_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    approvals: int = 0
    denials: int = 0
    iterations: int = 0
    tool_calls: int = 0
    answer: str = ""
    status: str = ""
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wall_time_s": round(self.wall_time_s, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "approvals": self.approvals,
            "denials": self.denials,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "status": self.status,
            "error": self.error,
        }


class BenchEngine(Protocol):
    """What the harness needs from an engine.

    A protocol rather than a base class so a test can supply a scripted engine
    without importing the real one — which is what lets the harness itself be
    tested offline while the matrix needs models.
    """

    name: str

    def available(self, tier: ModelTier) -> bool:
        ...

    def run(self, task: BenchTask, tier: ModelTier, workspace: Path) -> CellMetrics:
        ...


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@dataclass
class LoopEngine:
    """The agentic engine, driven exactly as an endpoint drives it."""

    name: str = "loop"
    topology_id: str = "autonomous_engineer"
    read_only_topology_id: str = "code_inspector"
    timeout_s: float = DEFAULT_TIMEOUT_S
    journal_root: Optional[Path] = None

    def available(self, tier: ModelTier) -> bool:
        from .matrix import tier_is_available

        return tier_is_available(tier)

    #: Flags the measured engine needs, forced on for the duration of a cell.
    #:
    #: Without this the harness measures a loop with **no policy engine**: the
    #: ``agent_policy`` flag is off by default, so ``AgentRunner`` falls back to
    #: Phase C's permissive stub and a read-only task's write is simply applied.
    #: The benchmark exists to decide whether to flip the default, so it has to
    #: measure the engine as it will be *after* the flip, not as today's flags
    #: happen to configure it.
    required_flags: Tuple[str, ...] = (
        "agent_loop", "agent_policy", "agent_loop_default", "tool_registry",
        "model_profiles", "agent_resume",
    )

    def run(self, task: BenchTask, tier: ModelTier, workspace: Path) -> CellMetrics:
        from gitpilot import flags

        previous = {name: flags.is_on(name) for name in self.required_flags}
        for name in self.required_flags:
            flags.set_override(name, True)
        try:
            return self._run(task, tier, workspace)
        finally:
            for name, was_on in previous.items():
                if was_on:
                    flags.set_override(name, True)
                else:
                    flags.clear_override(name)

    def _run(self, task: BenchTask, tier: ModelTier, workspace: Path) -> CellMetrics:
        from gitpilot.agent.model_profile import resolve_profile
        from gitpilot.agent.providers import build_provider
        from gitpilot.agent.runner import AgentRunner, RunRequest, apply_topology_policy

        journal_root = self.journal_root or (workspace.parent / "journals")
        profile = resolve_profile(
            tier.family, tier.model, cache_path=journal_root / "probe.json",
        )
        if profile.dialect.value != tier.expected_dialect:
            # Not a failure of the run — a failure of the matrix's premise. Say so
            # loudly rather than reporting a number for a dialect nobody tested.
            logger.warning(
                "%s resolved to the %s dialect, not %s; this cell no longer tests "
                "what the matrix claims",
                tier.model, profile.dialect.value, tier.expected_dialect,
            )

        request = apply_topology_policy(RunRequest(
            task=task.prompt,
            session_id=f"bench-{task.id}-{tier.label}",
            topology_id=self.read_only_topology_id if task.read_only else self.topology_id,
            workspace_root=workspace,
            journal_root=journal_root,
            session_mode="auto" if not task.read_only else "normal",
            # Nobody is watching a benchmark, so a call that would prompt is denied.
            # A cell that hangs on an approval measures the harness, not the engine.
            approvals="none",
        ), workspace=workspace)

        runner = AgentRunner()
        provider = build_provider(model=tier.model)

        started = time.monotonic()
        try:
            result = asyncio.run(_with_timeout(
                runner.run(request, provider=provider, profile=profile), self.timeout_s,
            ))
        except TimeoutError:
            return CellMetrics(
                wall_time_s=time.monotonic() - started,
                status="timeout",
                error=f"no answer within {self.timeout_s:.0f}s",
            )
        except Exception as exc:  # noqa: BLE001 - one bad cell must not end the matrix
            logger.exception("cell %s/%s/loop crashed", task.id, tier.label)
            return CellMetrics(
                wall_time_s=time.monotonic() - started, status="error", error=repr(exc),
            )
        elapsed = time.monotonic() - started

        metrics = CellMetrics(
            wall_time_s=elapsed,
            answer=result.answer or "",
            status=result.status,
        )
        # ``AgentResult`` carries its context, which carries the journal. Reading
        # the counters back out of the journal rather than off the context is
        # deliberate: the journal is what a user could audit afterwards, so the
        # report's numbers and theirs are the same numbers.
        _read_journal_into(metrics, getattr(result.context.journal, "path", None))
        if not metrics.total_tokens:
            budgets = result.context.budgets
            metrics.prompt_tokens = budgets.prompt_tokens
            metrics.completion_tokens = budgets.completion_tokens
        metrics.iterations = metrics.iterations or result.context.iteration
        return metrics


async def _with_timeout(coroutine: Any, timeout_s: float) -> Any:
    return await asyncio.wait_for(coroutine, timeout=timeout_s)


def _read_journal_into(metrics: CellMetrics, journal_path: Optional[Any]) -> None:
    """Fill the counters from the run's own record.

    Reading the journal rather than counting events as they fly past means the
    report's numbers are the ones a user could reconstruct afterwards.
    """
    if not journal_path:
        return
    try:
        from gitpilot.agent.journal import LineType, read_journal

        for line in read_journal(Path(str(journal_path))):
            kind = line.get("type")
            if kind == LineType.POLICY_DECISION:
                if line.get("verdict") == "ask":
                    metrics.approvals += 1
                elif line.get("verdict") == "deny":
                    metrics.denials += 1
            elif kind == LineType.TOOL_CALL:
                metrics.tool_calls += 1
            elif kind == LineType.RUN_FINISHED:
                usage = line.get("usage") or {}
                metrics.prompt_tokens = int(usage.get("prompt_tokens") or 0)
                metrics.completion_tokens = int(usage.get("completion_tokens") or 0)
                metrics.iterations = int(line.get("iteration") or metrics.iterations)
    except Exception as exc:  # noqa: BLE001 - missing metrics beat a failed cell
        logger.warning("could not read the journal for a cell: %s", exc)


# ---------------------------------------------------------------------------
# The legacy path
# ---------------------------------------------------------------------------


@dataclass
class LegacyEngine:
    """The CrewAI dispatch, for the side of the comparison being replaced.

    It needs a GitHub-shaped repository binding and the ``crewai`` extra, which is
    exactly the constraint that motivated this programme — a folder-only session
    could not use it at all. Where it cannot run, the cell is recorded as
    ``unavailable`` rather than as a failure: "legacy scored zero because it does
    not work here" would flatter the loop for the wrong reason.
    """

    name: str = "legacy"
    topology_id: str = "default"
    timeout_s: float = DEFAULT_TIMEOUT_S
    repo_full_name: str = ""
    token: str = ""

    def available(self, tier: ModelTier) -> bool:
        from .matrix import tier_is_available

        if not tier_is_available(tier):
            return False
        if not self.repo_full_name:
            return False
        try:
            import crewai  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    def run(self, task: BenchTask, tier: ModelTier, workspace: Path) -> CellMetrics:
        from gitpilot.agentic import dispatch_request

        started = time.monotonic()
        try:
            payload = asyncio.run(_with_timeout(
                dispatch_request(
                    task.prompt,
                    self.repo_full_name,
                    token=self.token,
                    topology_id=self.topology_id,
                ),
                self.timeout_s,
            ))
        except TimeoutError:
            return CellMetrics(
                wall_time_s=time.monotonic() - started,
                status="timeout",
                error=f"no answer within {self.timeout_s:.0f}s",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("cell %s/%s/legacy crashed", task.id, tier.label)
            return CellMetrics(
                wall_time_s=time.monotonic() - started, status="error", error=repr(exc),
            )

        answer = ""
        if isinstance(payload, dict):
            answer = str(payload.get("result") or payload.get("message") or "")
        return CellMetrics(
            wall_time_s=time.monotonic() - started,
            answer=answer,
            status="completed",
            # The legacy path keeps no journal, so tokens and approvals are not
            # observable from here. Reported as zero and flagged, rather than
            # estimated — an invented denominator is worse than a missing one.
            error="",
        )


# ---------------------------------------------------------------------------
# Driving cells
# ---------------------------------------------------------------------------


@dataclass
class CellRun:
    """One cell's inputs, metrics and verdict."""

    task_id: str
    tier: str
    engine: str
    outcome: Outcome
    metrics: CellMetrics
    skipped: bool = False
    unavailable: bool = False
    category: str = ""
    tags: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome.ok and not self.skipped and not self.unavailable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task_id,
            "tier": self.tier,
            "engine": self.engine,
            "ok": self.ok,
            "detail": self.outcome.detail,
            "skipped": self.skipped,
            "unavailable": self.unavailable,
            "category": self.category,
            "tags": list(self.tags),
            **self.metrics.to_dict(),
        }


def run_cell(
    task: BenchTask,
    tier: ModelTier,
    engine: BenchEngine,
    *,
    workspace_root: Optional[Path] = None,
    keep_workspace: bool = False,
) -> CellRun:
    """Build the fixture, run the engine, check the result."""
    if not engine.available(tier):
        return CellRun(
            task_id=task.id, tier=tier.label, engine=engine.name,
            outcome=Outcome.failed(f"{engine.name} is not available for {tier.label}"),
            metrics=CellMetrics(status="unavailable"),
            unavailable=True, category=task.category, tags=list(task.tags),
        )

    created = workspace_root is None
    root = Path(workspace_root) if workspace_root else Path(tempfile.mkdtemp(prefix="bench-"))
    workspace = root / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        task.build(workspace)
        metrics = engine.run(task, tier, workspace)
        if metrics.status in ("timeout", "error"):
            outcome = Outcome.failed(metrics.error or metrics.status)
        else:
            outcome = task.check(workspace, metrics.answer)
        return CellRun(
            task_id=task.id, tier=tier.label, engine=engine.name,
            outcome=outcome, metrics=metrics,
            category=task.category, tags=list(task.tags),
        )
    finally:
        if created and not keep_workspace:
            shutil.rmtree(root, ignore_errors=True)


def run_matrix(
    engines: Mapping[str, BenchEngine],
    *,
    tiers: Optional[List[ModelTier]] = None,
    task_ids: Optional[List[str]] = None,
    on_cell: Optional[Any] = None,
) -> List[CellRun]:
    """Every cell, in tier-major order."""
    from .matrix import cells
    from .tasks import task_for

    results: List[CellRun] = []
    for tier, engine_name, task_id in cells(
        tiers=tiers, engines=tuple(engines), task_ids=task_ids,
    ):
        task = task_for(task_id)
        engine = engines.get(engine_name)
        if task is None or engine is None:
            continue
        result = run_cell(task, tier, engine)
        results.append(result)
        if on_cell is not None:
            on_cell(result)
    return results
