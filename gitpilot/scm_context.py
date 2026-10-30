# gitpilot/scm_context.py
"""Per-request source-control provider context.

GitPilot talks to two hosting providers, GitHub and GitLab. The provider of the
repository a request works on is carried in a context variable next to that
provider's credential, so the helpers in :mod:`gitpilot.github_api` and
:mod:`gitpilot.gitlab_api` can each refuse to run with the other provider's
credential. A GitLab token never reaches api.github.com and a GitHub token never
reaches the GitLab API.

This module has no GitPilot imports so both API modules can depend on it.
"""
from __future__ import annotations

import contextvars
import re
from typing import Literal, Optional

Provider = Literal["github", "gitlab"]
PROVIDERS: tuple[str, ...] = ("github", "gitlab")
DEFAULT_PROVIDER: Provider = "github"

_request_provider: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_provider", default=None
)
# Stable provider repository id (GitLab numeric project id) for the request's
# repository, paired with its full path so a call against another repository
# never borrows it.
_request_repo: contextvars.ContextVar[Optional[tuple[str, str]]] = contextvars.ContextVar(
    "request_repo", default=None
)


def normalize_provider(value: Optional[str], *, default: Provider = DEFAULT_PROVIDER) -> Provider:
    """Return a known provider name, or raise ``ValueError`` for an unknown one."""
    if value is None or str(value).strip() == "":
        return default
    name = str(value).strip().lower()
    if name not in PROVIDERS:
        raise ValueError(f"Unknown git provider: {value!r}")
    return name  # type: ignore[return-value]


def current_provider() -> Optional[str]:
    """The provider bound to the current execution scope, if any."""
    return _request_provider.get()


def current_repo_id(full_name: str) -> Optional[str]:
    """The bound stable repository id, only when it belongs to ``full_name``."""
    bound = _request_repo.get()
    if bound and bound[0].lower() == full_name.lower():
        return bound[1]
    return None


# Well-known token prefixes. They let each API helper refuse a credential that
# visibly belongs to the other provider (defence in depth on top of routing).
_GITHUB_TOKEN_RE = re.compile(r"^(gh[pousr]_|github_pat_)")
_GITLAB_TOKEN_RE = re.compile(r"^(glpat-|gloas-|gldt-|glrt-|glcbt-|glptt-|glft-|glimt-|glagent-|glsoat-)")


def token_belongs_to_other_provider(token: str, provider: str) -> bool:
    """True when ``token`` has the visible prefix of the provider that is *not* ``provider``."""
    if not token:
        return False
    if provider == "github":
        return bool(_GITLAB_TOKEN_RE.match(token))
    if provider == "gitlab":
        return bool(_GITHUB_TOKEN_RE.match(token))
    return False
