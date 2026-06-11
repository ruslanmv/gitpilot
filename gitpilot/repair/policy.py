"""Path policy + safety checks for the GitPilot repair flow.

Everything here is **fail-closed**: when in doubt, deny.

A change is BLOCKED when any of these hold:

* ``allowed_paths`` is empty;
* a changed file is outside ``allowed_paths``;
* a changed file matches ``forbidden_paths``;
* the patch touches ``.env``;
* the patch touches anything under ``secrets`` / matching ``*secret*`` / ``*token*``;
* ``sandbox.required`` is true and MatrixLab is unavailable;
* sandbox validation fails;
* ``risk_level`` is high and there is no human-approval flag.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid import cycle at runtime
    from .schema import RepairRequest

from .schema import DEFAULT_FORBIDDEN_PATHS


@dataclass
class PolicyResult:
    """Outcome of a policy evaluation."""

    allowed: bool
    violations: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # truthy == allowed
        return self.allowed


class PolicyViolation(Exception):
    """Raised when a fail-closed policy check is violated."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


def _normalize(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _matches_any(path: str, globs: list[str]) -> bool:
    norm = _normalize(path)
    base = norm.rsplit("/", 1)[-1]
    for g in globs:
        gg = _normalize(g)
        # Match full path and basename; support **.
        if fnmatch.fnmatch(norm, gg) or fnmatch.fnmatch(base, gg):
            return True
        # `secrets/**` should also match `secrets/foo`
        if gg.endswith("/**") and (norm == gg[:-3] or norm.startswith(gg[:-2])):
            return True
        # `**/*token*` style: also match when token/secret appears anywhere.
        if "**" in gg:
            tail = gg.split("**", 1)[-1].strip("/")
            if tail and fnmatch.fnmatch(norm, f"*{tail}") or (tail and fnmatch.fnmatch(base, tail)):
                return True
    return False


# Hard, always-on denylist (independent of request-supplied forbidden_paths).
_HARD_DENY = [".env", ".env.*", "secrets/**", "**/*secret*", "**/*token*"]


def _touches_secret_or_env(path: str) -> bool:
    norm = _normalize(path).lower()
    base = norm.rsplit("/", 1)[-1]
    if base == ".env" or base.startswith(".env."):
        return True
    if "secret" in norm or "token" in norm:
        return True
    if norm == "secrets" or norm.startswith("secrets/"):
        return True
    return False


def is_path_allowed(
    path: str,
    allowed: list[str],
    forbidden: list[str] | None = None,
) -> bool:
    """Return True iff *path* is inside *allowed* and not in *forbidden*.

    Fail-closed: empty *allowed* => not allowed; secrets/env => not allowed.
    """
    forbidden = list(forbidden or []) + DEFAULT_FORBIDDEN_PATHS + _HARD_DENY
    if not allowed:
        return False
    if _touches_secret_or_env(path):
        return False
    if _matches_any(path, forbidden):
        return False
    return _matches_any(path, allowed)


def validate_changed_files(
    files: list[str],
    request: RepairRequest,
) -> PolicyResult:
    """Evaluate the set of changed files against the request policy.

    Returns a :class:`PolicyResult` (does not raise) so callers can decide
    whether to raise or record a blocked response.
    """
    violations: list[str] = []
    allowed = list(request.allowed_paths or [])
    forbidden = list(request.forbidden_paths or [])

    if not allowed:
        violations.append("allowed_paths is empty (fail-closed): no changes permitted")
        return PolicyResult(allowed=False, violations=violations)

    if not files:
        # No changes is acceptable (nothing to block), but caller may warn.
        return PolicyResult(allowed=True, violations=[])

    for f in files:
        norm = _normalize(f)
        if _touches_secret_or_env(f):
            violations.append(f"file touches secret/.env material: {norm}")
            continue
        if _matches_any(f, forbidden + DEFAULT_FORBIDDEN_PATHS + _HARD_DENY):
            violations.append(f"file matches forbidden_paths: {norm}")
            continue
        if not _matches_any(f, allowed):
            violations.append(f"file outside allowed_paths: {norm}")

    return PolicyResult(allowed=not violations, violations=violations)


def check_sandbox_policy(
    request: RepairRequest,
    sandbox_healthy: bool,
) -> PolicyResult:
    """Fail-closed when sandbox is required but unavailable."""
    if request.sandbox.required and not sandbox_healthy:
        return PolicyResult(
            allowed=False,
            violations=["sandbox.required=true but MatrixLab is unavailable"],
        )
    return PolicyResult(allowed=True)


def check_risk_policy(
    risk_level: str,
    human_approval: bool,
) -> PolicyResult:
    """Fail-closed when risk is high without human approval."""
    if str(risk_level).lower() == "high" and not human_approval:
        return PolicyResult(
            allowed=False,
            violations=["risk_level=high requires human approval"],
        )
    return PolicyResult(allowed=True)


def assert_allowed(result: PolicyResult) -> None:
    """Raise :class:`PolicyViolation` if the result is not allowed."""
    if not result.allowed:
        raise PolicyViolation(result.violations)
