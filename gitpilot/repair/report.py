"""Build a human-readable repair report and the repair-response.json structure."""

from __future__ import annotations

from typing import Any

from .schema import RepairResponse


def build_report(response: RepairResponse) -> str:
    """Return a concise, human-readable summary of a repair response."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("GitPilot Repair Report")
    lines.append("=" * 60)
    lines.append(f"task_id    : {response.task_id}")
    lines.append(f"mode       : {response.mode.value}")
    lines.append(f"status     : {response.status}")
    lines.append(f"risk_level : {response.risk_level.value}")

    if response.changed_files:
        lines.append("")
        lines.append("Changed files:")
        for cf in response.changed_files:
            lines.append(f"  [{cf.change_type}] {cf.path}")
    else:
        lines.append("")
        lines.append("Changed files: (none)")

    if response.review:
        lines.append("")
        lines.append("Review:")
        lines.append(f"  {response.review}")

    if response.sandbox_result is not None:
        sr = response.sandbox_result
        lines.append("")
        if sr.get("skipped"):
            lines.append(f"Sandbox    : skipped ({sr.get('reason', '')})")
        else:
            lines.append(
                f"Sandbox    : {sr.get('status', 'n/a')} "
                f"(exit_code={sr.get('exit_code')})"
            )

    if response.pr_url:
        lines.append("")
        lines.append(f"PR         : {response.pr_url}")

    if response.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in response.warnings:
            lines.append(f"  - {w}")

    if response.messages:
        lines.append("")
        lines.append("Messages:")
        for m in response.messages:
            lines.append(f"  - {m}")

    lines.append("")
    lines.append("Patch preview:")
    lines.append("-" * 60)
    lines.append(response.patch_preview or "(empty)")
    lines.append("-" * 60)
    return "\n".join(lines)


def build_response_json(response: RepairResponse) -> dict[str, Any]:
    """Return the JSON-serializable repair-response dict."""
    return response.to_json()
