"""GitPilot repair flow — the GENERIC patch-generation pipeline.

GitPilot is the only component that *generates* patches.  This package is
provider-neutral: it calls OllaBridge (or any OpenAI-compatible endpoint)
and an optional sandbox (MatrixLab) and is consumable by SelfRepair,
Agent-Matrix, CI, or a developer.

Public/dry-run behaviour is always SAFE: no real PRs, no network required.
"""

from .schema import (
    DEFAULT_FORBIDDEN_PATHS,
    CoderSpec,
    Issue,
    RepairRequest,
    RepairResponse,
    SandboxSpec,
)

__all__ = [
    "RepairRequest",
    "RepairResponse",
    "Issue",
    "CoderSpec",
    "SandboxSpec",
    "DEFAULT_FORBIDDEN_PATHS",
]
