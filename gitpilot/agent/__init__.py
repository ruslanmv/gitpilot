# gitpilot/agent/__init__.py
"""The agentic runtime — GitPilot v4.

Built batch by batch (see ``docs/upgrade-v4-batches.md``):

* Batch V4-B1 — ``providers/``: direct provider clients, no CrewAI, no litellm.
* Batch V4-B2 — ``model_profile``: what each model can do, and which dialect it
  is therefore spoken to.
* Batches V4-B3/B4/B5 — ``dialects/``: three renderings of one loop, so the same
  engine drives a frontier API and a 1.5B model running locally.
* Batches V4-C1/C2 — ``journal``, ``context``, ``loop``: the engine itself.
* Batches V4-C3/C4 — ``runner``, ``headless``: what an endpoint or the CLI calls.

Importing this package is cheap and side-effect free — the loop's own modules are
imported from the runner, not from here, so the light contracts stay light. The
engine is reached only when the ``agent_loop`` flag is on *and* the request's
topology asked for it (see :func:`gitpilot.agent.runner.should_use_loop`).
"""
from __future__ import annotations

from .contracts import AgentTurn, Dialect, Finality, TokenUsage

__all__ = ["AgentTurn", "Dialect", "Finality", "TokenUsage"]
