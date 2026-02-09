# gitpilot/headless.py
"""Headless execution mode for CI/CD pipelines.

Runs GitPilot non-interactively from the command line, GitHub Actions,
or GitLab CI, returning structured JSON output.

Usage examples::

    gitpilot run --headless -r owner/repo -m "fix the login bug"
    gitpilot run --headless -r owner/repo --from-pr 42
    echo "add tests for auth module" | gitpilot run --headless -r owner/repo
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .agent_tools import set_repo_context
from .agentic import dispatch_request

logger = logging.getLogger(__name__)


@dataclass
class HeadlessResult:
    """Result of a headless execution."""

    success: bool
    output: str
    session_id: Optional[str] = None
    pr_url: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "success": self.success,
                "output": self.output,
                "session_id": self.session_id,
                "pr_url": self.pr_url,
                "error": self.error,
            },
            indent=2,
        )


async def run_headless(
    repo_full_name: str,
    message: str,
    token: str,
    branch: Optional[str] = None,
    auto_pr: bool = False,
    from_pr: Optional[int] = None,
) -> HeadlessResult:
    """Execute a request non-interactively."""
    owner, repo = repo_full_name.split("/", 1)
    set_repo_context(owner, repo, token=token, branch=branch or "main")

    # If from_pr, fetch PR context
    if from_pr:
        try:
            from .github_pulls import get_pull_request

            pr = await get_pull_request(owner, repo, from_pr, token=token)
            message = (
                f"PR #{from_pr}: {pr.get('title', '')}\n"
                f"{pr.get('body', '')}\n\n"
                f"User request: {message}"
            )
        except Exception as e:
            logger.warning("Could not fetch PR #%s: %s", from_pr, e)

    try:
        result = await dispatch_request(
            user_request=message,
            repo_full_name=repo_full_name,
            token=token,
            branch_name=branch,
        )

        output = result.get("result", "") if isinstance(result, dict) else str(result)

        return HeadlessResult(
            success=True,
            output=output,
        )
    except Exception as e:
        logger.exception("Headless execution failed")
        return HeadlessResult(
            success=False,
            output="",
            error=str(e),
        )
