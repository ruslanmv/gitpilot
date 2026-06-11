"""SandboxProvider abstract base class.

A sandbox provider validates a patch / runs commands in an isolated
workspace.  Concrete providers (e.g. MatrixLab) implement this ABC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SandboxResult:
    """Normalized result of a sandbox run / patch validation."""

    run_id: str = ""
    status: str = "error"  # "passed" | "failed" | "error"
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int | None = None
    artifacts: list[dict[str, str]] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "artifacts": list(self.artifacts),
            "skipped": self.skipped,
            "reason": self.reason,
        }

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> SandboxResult:
        """Build from a MatrixLab-shaped JSON response."""
        return cls(
            run_id=str(data.get("run_id", "")),
            status=str(data.get("status", "error")),
            exit_code=data.get("exit_code"),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            duration_ms=data.get("duration_ms"),
            artifacts=list(data.get("artifacts", []) or []),
        )

    @classmethod
    def skipped_result(cls, reason: str) -> SandboxResult:
        return cls(status="error", skipped=True, reason=reason)


class SandboxProvider(ABC):
    """Abstract sandbox provider."""

    name: str = "sandbox"

    @abstractmethod
    def health(self) -> bool:
        """Return True iff the sandbox is reachable/healthy."""
        raise NotImplementedError

    @abstractmethod
    def run(self, **payload: Any) -> SandboxResult:
        """Run arbitrary commands in the sandbox."""
        raise NotImplementedError

    @abstractmethod
    def validate_patch(self, **payload: Any) -> SandboxResult:
        """Validate a patch (apply + run profile) in the sandbox."""
        raise NotImplementedError
