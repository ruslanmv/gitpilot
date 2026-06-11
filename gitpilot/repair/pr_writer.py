"""Draft-PR writer abstraction.

FIRST WAVE: this is a SAFE no-op / dry-run stub.  It NEVER opens a real PR.
``GITPILOT_DRAFT_PR_ENABLED`` defaults to ``false``; even when set, the first
wave returns a simulated result and does not call any Git host.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def draft_pr_enabled() -> bool:
    return _truthy(os.getenv("GITPILOT_DRAFT_PR_ENABLED"))


@dataclass
class PRResult:
    created: bool
    url: str | None
    message: str


class DraftPRWriter:
    """Stubbed draft-PR writer.

    In the first wave this never performs network operations.  It exists so
    the service can call a stable interface that later waves can implement.
    """

    def create_draft_pr(
        self,
        repo_url: str,
        branch: str,
        title: str,
        body: str,
        base: str = "main",
    ) -> PRResult:
        if not draft_pr_enabled():
            return PRResult(
                created=False,
                url=None,
                message=(
                    "Draft PR disabled (GITPILOT_DRAFT_PR_ENABLED=false). "
                    "No PR created."
                ),
            )
        # First wave: even when "enabled", do NOT open a real PR.
        return PRResult(
            created=False,
            url=None,
            message=(
                "Draft PR is a no-op stub in the first wave. "
                f"Would open PR '{title}' on {repo_url} ({branch} -> {base})."
            ),
        )
