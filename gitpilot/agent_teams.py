# gitpilot/agent_teams.py
"""Parallel multi-agent execution on git worktrees.

Coordinates multiple agents working on independent subtasks simultaneously.
Each agent operates on its own git worktree to avoid conflicts, and a lead
agent reviews and merges the results.

Architecture inspired by the MapReduce pattern and the *divide-and-conquer*
approach from distributed systems research (Dean & Ghemawat, 2004).

Workflow::

    User: "Add authentication to the API"
    Lead agent splits → 4 subtasks
    ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
    │ Agent A:    │  │ Agent B:    │  │ Agent C:    │  │ Agent D:    │
    │ User model  │  │ Middleware  │  │ Endpoints   │  │ Tests       │
    │ worktree/a  │  │ worktree/b  │  │ worktree/c  │  │ worktree/d  │
    └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
          └───────────┴───────────┴───────────┘
                          │
                    Lead reviews & merges
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SubTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SubTask:
    """A single subtask to be executed by one agent."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    description: str = ""
    assigned_agent: str = ""
    files: List[str] = field(default_factory=list)
    status: SubTaskStatus = SubTaskStatus.PENDING
    result: str = ""
    error: Optional[str] = None
    worktree_path: Optional[Path] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "assigned_agent": self.assigned_agent,
            "files": self.files,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class TeamResult:
    """Aggregated result from parallel agent execution."""

    task: str
    subtasks: List[SubTask] = field(default_factory=list)
    merge_status: str = "pending"  # pending | merged | conflict | failed
    conflicts: List[str] = field(default_factory=list)
    summary: str = ""

    @property
    def all_completed(self) -> bool:
        return all(s.status == SubTaskStatus.COMPLETED for s in self.subtasks)

    @property
    def any_failed(self) -> bool:
        return any(s.status == SubTaskStatus.FAILED for s in self.subtasks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "merge_status": self.merge_status,
            "conflicts": self.conflicts,
            "summary": self.summary,
            "all_completed": self.all_completed,
            "any_failed": self.any_failed,
        }


class AgentTeam:
    """Coordinate multiple agents working in parallel.

    Usage::

        team = AgentTeam(workspace_path=Path("/repo"))
        subtasks = team.plan_and_split("Add auth system", num_agents=4)
        result = await team.execute_parallel(subtasks, executor_fn=my_agent_fn)
        merge = await team.merge_results(result)
    """

    def __init__(self, workspace_path: Optional[Path] = None) -> None:
        self.workspace_path = workspace_path
        self._worktrees: List[Path] = []

    def plan_and_split(
        self,
        task: str,
        num_agents: int = 4,
        subtask_descriptions: Optional[List[Dict[str, str]]] = None,
    ) -> List[SubTask]:
        """Split a task into independent subtasks.

        If ``subtask_descriptions`` is provided, use those directly.
        Otherwise, create generic subtasks from the task description.
        """
        subtasks = []

        if subtask_descriptions:
            for i, desc in enumerate(subtask_descriptions):
                subtasks.append(SubTask(
                    title=desc.get("title", f"Subtask {i + 1}"),
                    description=desc.get("description", ""),
                    assigned_agent=desc.get("agent", f"agent_{i}"),
                    files=desc.get("files", []),
                ))
        else:
            # Generic split — the LLM would normally do this
            for i in range(min(num_agents, 8)):
                subtasks.append(SubTask(
                    title=f"Part {i + 1} of {task}",
                    description=f"Handle part {i + 1} of the task: {task}",
                    assigned_agent=f"agent_{i}",
                ))

        return subtasks

    async def execute_parallel(
        self,
        subtasks: List[SubTask],
        executor_fn: Optional[Any] = None,
    ) -> TeamResult:
        """Execute subtasks in parallel.

        ``executor_fn`` is an async callable(SubTask) -> str that runs the
        agent logic for each subtask.  If not provided, subtasks are marked
        as completed with a placeholder result.
        """
        result = TeamResult(task="parallel_execution", subtasks=subtasks)

        async def _run_subtask(subtask: SubTask) -> None:
            subtask.status = SubTaskStatus.RUNNING
            subtask.started_at = datetime.now(timezone.utc).isoformat()
            try:
                if executor_fn:
                    subtask.result = await executor_fn(subtask)
                else:
                    subtask.result = f"Completed: {subtask.title}"
                subtask.status = SubTaskStatus.COMPLETED
            except Exception as e:
                subtask.status = SubTaskStatus.FAILED
                subtask.error = str(e)
                logger.error("Subtask %s failed: %s", subtask.id, e)
            finally:
                subtask.completed_at = datetime.now(timezone.utc).isoformat()

        # Run all subtasks concurrently
        await asyncio.gather(*[_run_subtask(st) for st in subtasks])

        return result

    async def merge_results(self, team_result: TeamResult) -> TeamResult:
        """Merge results from parallel execution.

        In a full implementation, this would:
        1. Check for file conflicts between subtask outputs
        2. Use git merge-tree for conflict detection
        3. Have a lead agent resolve conflicts

        For now, it aggregates results and detects file overlaps.
        """
        if team_result.any_failed:
            team_result.merge_status = "failed"
            failed = [s for s in team_result.subtasks if s.status == SubTaskStatus.FAILED]
            team_result.summary = (
                f"{len(failed)} subtask(s) failed: "
                + ", ".join(f"{s.title} ({s.error})" for s in failed)
            )
            return team_result

        # Detect file conflicts (same file modified by multiple agents)
        file_owners: Dict[str, List[str]] = {}
        for st in team_result.subtasks:
            for f in st.files:
                file_owners.setdefault(f, []).append(st.assigned_agent)

        conflicts = [f for f, owners in file_owners.items() if len(owners) > 1]
        team_result.conflicts = conflicts

        if conflicts:
            team_result.merge_status = "conflict"
            team_result.summary = (
                f"File conflicts detected in: {', '.join(conflicts)}. "
                "Manual review required."
            )
        else:
            team_result.merge_status = "merged"
            completed = [s for s in team_result.subtasks if s.status == SubTaskStatus.COMPLETED]
            team_result.summary = (
                f"All {len(completed)} subtasks completed successfully. "
                "No file conflicts detected."
            )

        return team_result

    async def setup_worktrees(self, subtasks: List[SubTask], base_branch: str = "main") -> None:
        """Create git worktrees for each subtask (requires workspace_path)."""
        if not self.workspace_path:
            return
        for st in subtasks:
            worktree_name = f"worktree-{st.id}"
            worktree_path = self.workspace_path / ".worktrees" / worktree_name
            branch_name = f"team/{st.id}"

            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add", "-b", branch_name,
                str(worktree_path), base_branch,
                cwd=str(self.workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            st.worktree_path = worktree_path
            self._worktrees.append(worktree_path)

    async def cleanup_worktrees(self) -> None:
        """Remove all worktrees created by this team."""
        if not self.workspace_path:
            return
        for wt in self._worktrees:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "remove", "--force", str(wt),
                cwd=str(self.workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        self._worktrees.clear()
