"""Turn benchmark scores into GitPilot model-profile suggestions.

GitPilot drives every model through one of three dialects (see
``gitpilot/agent/model_profile.py``):

* NATIVE      — the provider's own tool-calling API; needs reliable tool calls.
* REACT_TEXT  — tools described in text and parsed from the reply; needs a model
                that follows a format strictly.
* LITE        — a constrained read-then-act loop for small models.

Unknown models are probed at runtime, but a probe is one request. A benchmark
row is dozens, so it is the better evidence. This module writes the suggestion
in the exact shape GitPilot reads from ``.gitpilot/models.yaml``, keyed by the
same normalised name GitPilot uses (provider prefix dropped, lowercase). It
never edits that file: adopting a suggestion is a reviewed change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

#: A model has to get at least this share of tool-calling tasks right to be
#: trusted with NATIVE tool calls inside an unattended agent loop.
NATIVE_TOOLS_THRESHOLD = 0.75
#: Text dialects parse the model's own output, so it has to follow formats.
REACT_INSTRUCTIONS_THRESHOLD = 0.6
REACT_TOOLS_THRESHOLD = 0.5


def suggest_dialect(suites: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """``{"dialect": ..., "why": ...}``, or None when there is too little evidence."""
    tools = (suites.get("tools") or {}).get("rate")
    instructions = (suites.get("instructions") or {}).get("rate")
    if tools is None and instructions is None:
        return None
    if tools is not None and tools >= NATIVE_TOOLS_THRESHOLD:
        return {"dialect": "native", "why": f"tool calling {tools:.0%} ≥ {NATIVE_TOOLS_THRESHOLD:.0%}"}
    if (instructions or 0) >= REACT_INSTRUCTIONS_THRESHOLD or (tools or 0) >= REACT_TOOLS_THRESHOLD:
        return {"dialect": "react_text",
                "why": f"tool calling {tools or 0:.0%}, instruction following {instructions or 0:.0%}"}
    return {"dialect": "lite",
            "why": f"tool calling {tools or 0:.0%}, instruction following {instructions or 0:.0%}"}


def profile_key(model_id: str) -> str:
    """The key GitPilot's ``models.yaml`` loader matches on."""
    name = model_id.strip().lower()
    return name.split("/", 1)[-1] if "/" in name else name


def write_suggestions(summary_models: List[Dict[str, Any]], path: Path, *, digest: str) -> Path:
    lines = [
        "# Suggested GitPilot model profiles, from benchmarks/ (generated; do not edit by hand).",
        f"# Task set {digest}. Review, then copy the entries you agree with into .gitpilot/models.yaml.",
        "# These come from single-turn tasks. Before adopting NATIVE for a small model, confirm it",
        "# holds up over a whole agent run: python -m benchmarks.run --models <id> --suites tools,agentic",
        "models:",
    ]
    for model in summary_models:
        suggestion = model.get("suggestion")
        if not suggestion:
            continue
        # The reason sits on its own line: GitPilot's YAML-subset loader is not
        # guaranteed to strip a trailing comment from a value.
        lines.append(f"  # {model['name']}: {suggestion['why']}")
        lines.append(f"  {_yaml_key(model['profile_key'])}:")
        lines.append(f"    dialect: {suggestion['dialect']}")
    if lines[-1] == "models:":
        lines[-1] = "models: {}"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _yaml_key(key: str) -> str:
    return f'"{key}"' if any(ch in key for ch in ':#{}[],&*!|>%@`') else key
