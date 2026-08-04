"""Resolve a run's ``CoderSpec`` to a concrete coder.

One seam, three kinds of coder, chosen per run:

* ``ollabridge`` (default) — the built-in single-shot model that returns a
  unified diff. Unchanged behaviour; this is what every existing run gets.
* ``claude_code`` / ``codex`` — headless coding agents run inside the prepared
  workspace (see :mod:`gitpilot.inference.agent_coder`).
* any other name — a generic agent defined by ``GITPILOT_AGENT_CMD``.

An unknown provider falls back to the built-in coder rather than failing the
run, so a typo degrades to the safe default instead of dropping work.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from gitpilot.inference.agent_coder import AGENT_PRESETS, AgentCoder, resolve_agent_spec
from gitpilot.inference.ollabridge_client import OllaBridgeClient

log = logging.getLogger(__name__)

DEFAULT_PROVIDER = "ollabridge"

_LABELS = {
    "ollabridge": "Built-in coder",
    "claude_code": "Claude Code",
    "codex": "Codex",
}


def default_provider() -> str:
    """Deployment-wide default, so an operator can switch the whole install to
    an agent without every caller having to ask for it."""
    return (os.getenv("GITPILOT_CODER_PROVIDER", DEFAULT_PROVIDER) or DEFAULT_PROVIDER).strip()


def build_coder(spec: Any = None, demo_mode: bool = False) -> Any:
    """Build the coder for a run.

    ``spec`` is the request's :class:`~gitpilot.repair.schema.CoderSpec` (or
    None). Selection order: the run's provider, else the deployment default.
    """
    provider = (getattr(spec, "provider", "") or "").strip() or default_provider()
    key = provider.lower().replace("-", "_")

    if key in {DEFAULT_PROVIDER, "", "default"}:
        return OllaBridgeClient(demo_mode=demo_mode)

    agent_spec = resolve_agent_spec(key)
    if agent_spec is not None:
        log.info("Run uses the %s coding agent", agent_spec.name)
        return AgentCoder(agent_spec, demo_mode=demo_mode)

    log.warning("Unknown coder provider %r — falling back to %s", provider, DEFAULT_PROVIDER)
    return OllaBridgeClient(demo_mode=demo_mode)


def _agent_availability(spec) -> tuple[bool, str]:
    """Whether this agent could actually run here, and why not if it couldn't.

    A picker that offers a coder this deployment cannot run is worse than no
    picker, so availability is probed rather than assumed: the executable must
    exist, and any credential the agent needs must be set.
    """
    executable = spec.command[0] if spec.command else ""
    if not shutil.which(executable):
        return False, f"{executable!r} is not installed on the GitPilot host"
    if spec.credential_env and not os.getenv(spec.credential_env):
        return False, f"{spec.credential_env} is not set"
    return True, ""


def available_coders() -> list[dict[str, Any]]:
    """The coders this deployment can offer, with honest availability.

    The built-in coder is always available (it degrades to demo output offline);
    agents are listed only as available when their CLI and credential are present.
    """
    default = default_provider().lower().replace("-", "_")
    coders: list[dict[str, Any]] = [
        {"id": DEFAULT_PROVIDER, "label": _LABELS[DEFAULT_PROVIDER], "kind": "model",
         "available": True, "reason": "", "default": default in {DEFAULT_PROVIDER, "", "default"}}
    ]
    for name, spec in AGENT_PRESETS.items():
        available, reason = _agent_availability(spec)
        coders.append({
            "id": name, "label": _LABELS.get(name, name), "kind": "agent",
            "available": available, "reason": reason, "default": default == name,
        })

    custom = resolve_agent_spec("agent") if os.getenv("GITPILOT_AGENT_CMD") else None
    if custom is not None:
        available, reason = _agent_availability(custom)
        coders.append({
            "id": "agent", "label": custom.command[0] if custom.command else "Custom agent",
            "kind": "agent", "available": available, "reason": reason,
            "default": default not in {c["id"] for c in coders},
        })
    return coders
