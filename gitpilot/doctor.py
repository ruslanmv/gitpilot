# gitpilot/doctor.py
"""``gitpilot doctor`` — install + environment health check.

Reports a green / amber / red status for each prerequisite GitPilot needs.
Built to halve install-time support load: a single command tells the user
what's missing and how to fix it.

The implementation is pure-stdlib + optional ``rich`` for pretty output.
``--offline`` skips every network probe so the command stays under the
2-second budget on a healthy machine.  ``--json`` emits a machine-readable
payload for CI use.

This module is invoked through :mod:`gitpilot.cli` but works standalone::

    python -m gitpilot.doctor --json
"""
from __future__ import annotations

import dataclasses
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence


# ----------------------------------------------------------------------
# Status model
# ----------------------------------------------------------------------

LEVELS = ("green", "amber", "red")


@dataclass
class CheckResult:
    """Outcome of a single health check."""

    name: str
    level: str  # "green" | "amber" | "red"
    summary: str
    hint: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class DoctorReport:
    """Aggregate report for one run."""

    results: List[CheckResult] = field(default_factory=list)
    duration_ms: int = 0
    offline: bool = False

    @property
    def worst_level(self) -> str:
        ranking = {"green": 0, "amber": 1, "red": 2}
        return max((r.level for r in self.results), key=lambda lvl: ranking.get(lvl, 0), default="green")

    @property
    def exit_code(self) -> int:
        return {"green": 0, "amber": 0, "red": 1}.get(self.worst_level, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "duration_ms": self.duration_ms,
            "offline": self.offline,
            "worst_level": self.worst_level,
            "exit_code": self.exit_code,
        }


# ----------------------------------------------------------------------
# Individual checks  (each returns a CheckResult)
# ----------------------------------------------------------------------

def check_python() -> CheckResult:
    major, minor = sys.version_info.major, sys.version_info.minor
    if major == 3 and minor >= 11:
        return CheckResult("python", "green", f"Python {major}.{minor} ({platform.python_implementation()})")
    return CheckResult(
        "python", "red",
        f"Python {major}.{minor} is too old",
        hint="GitPilot requires Python >= 3.11.  Install via uv: `uv python install 3.11`.",
    )


def check_node() -> CheckResult:
    path = shutil.which("node")
    if not path:
        return CheckResult(
            "node", "amber",
            "node not found on PATH",
            hint="Optional for the frontend.  Install via nvm or your package manager.",
        )
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=2, check=False)
        version = out.stdout.strip() or "unknown"
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult("node", "amber", f"node failed to run: {exc}")
    return CheckResult("node", "green", f"node {version}")


def check_uv() -> CheckResult:
    path = shutil.which("uv")
    if not path:
        return CheckResult(
            "uv", "amber",
            "uv not found on PATH",
            hint="Optional but recommended.  Install via `pip install uv` or the official installer.",
        )
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=2, check=False)
        version = out.stdout.strip() or "unknown"
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult("uv", "amber", f"uv failed to run: {exc}")
    return CheckResult("uv", "green", version)


def check_workspace_files(workspace: Path) -> CheckResult:
    workspace = workspace.resolve()
    agents_md = workspace / "AGENTS.md"
    modes = workspace / ".gitpilot" / "modes.yaml"
    bits: List[str] = []
    level = "green"
    hint: Optional[str] = None
    if agents_md.exists():
        bits.append("AGENTS.md ✓")
    else:
        bits.append("AGENTS.md missing")
        level = "amber"
        hint = "Run `gitpilot init` to generate a starter AGENTS.md."
    if modes.exists():
        bits.append(".gitpilot/modes.yaml ✓")
    else:
        bits.append(".gitpilot/modes.yaml missing")
    return CheckResult("workspace", level, ", ".join(bits), hint=hint)


def check_modes_parses(workspace: Path) -> CheckResult:
    path = workspace / ".gitpilot" / "modes.yaml"
    if not path.exists():
        return CheckResult("modes.yaml", "amber", "no modes.yaml in this workspace")
    try:
        from gitpilot.modes import ModeRegistry  # local import to keep doctor light
        registry = ModeRegistry()
        count = registry.load(workspace_path=workspace)
        return CheckResult("modes.yaml", "green", f"parsed {count} mode(s)")
    except Exception as exc:
        return CheckResult(
            "modes.yaml", "red",
            "modes.yaml did not parse",
            hint="Open the file and check for YAML syntax errors.",
            detail=str(exc),
        )


def check_sandbox_reachable(*, offline: bool) -> CheckResult:
    from gitpilot.sandbox import (  # local import
        BACKEND_MATRIXLAB,
        BACKEND_OFF,
        BACKEND_SUBPROCESS,
        get_sandbox,
    )
    sb = get_sandbox()
    backend = sb.backend
    if backend == BACKEND_OFF:
        return CheckResult(
            "sandbox", "amber",
            "sandbox disabled (BACKEND_OFF)",
            hint="Set GITPILOT_SANDBOX=subprocess (default) or matrixlab.",
        )
    if backend == BACKEND_SUBPROCESS:
        return CheckResult("sandbox", "green", "subprocess backend ready")
    if backend == BACKEND_MATRIXLAB:
        if offline:
            return CheckResult("sandbox", "amber", "matrixlab backend (skipped probe — offline)")
        import asyncio
        import contextlib
        try:
            health = asyncio.run(asyncio.wait_for(sb.health(), timeout=2))
        except Exception as exc:
            return CheckResult(
                "sandbox", "red",
                "matrixlab backend not reachable",
                hint="Start the runner or set GITPILOT_MATRIXLAB_URL.",
                detail=str(exc),
            )
        finally:
            close = getattr(sb, "aclose", None)
            if callable(close):  # pragma: no branch
                with contextlib.suppress(Exception):
                    asyncio.run(close())
        if health.get("ok"):
            return CheckResult("sandbox", "green", "matrixlab runner reachable")
        return CheckResult(
            "sandbox", "red",
            "matrixlab runner unhealthy",
            detail=str(health.get("error", "")),
        )
    return CheckResult("sandbox", "amber", f"unknown backend: {backend}")


def check_mcp_config(workspace: Path) -> CheckResult:
    project = workspace / ".gitpilot" / "mcp.json"
    user = Path.home() / ".gitpilot" / "mcp.json"
    files = [p for p in (project, user) if p.exists()]
    if not files:
        return CheckResult("mcp", "amber", "no mcp.json found (project or user)")
    try:
        servers: List[str] = []
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data.get("servers", []) if isinstance(data, dict) else []:
                if isinstance(entry, dict) and entry.get("name"):
                    servers.append(str(entry["name"]))
        return CheckResult("mcp", "green", f"{len(servers)} MCP server(s) configured: {', '.join(sorted(set(servers))) or '(none)'}")
    except Exception as exc:
        return CheckResult("mcp", "red", "mcp.json did not parse", detail=str(exc))


_API_KEY_HINTS = {
    "openai":    "Set OPENAI_API_KEY",
    "anthropic": "Set ANTHROPIC_API_KEY",
    "watsonx":   "Set WATSONX_API_KEY (and WATSONX_PROJECT_ID)",
    "ollama":    "Run `ollama serve` locally; no key needed",
}

_API_KEY_ENVS = {
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "watsonx":   "WATSONX_API_KEY",
}


def check_model_credentials() -> CheckResult:
    provider = (os.environ.get("GITPILOT_LLM_PROVIDER") or "").lower()
    if not provider:
        # Best-effort: check whether any known env var is set.
        present = [name for name, env in _API_KEY_ENVS.items() if os.environ.get(env)]
        if present:
            return CheckResult("model", "green", f"credential(s) present: {', '.join(present)}")
        return CheckResult(
            "model", "amber",
            "no GITPILOT_LLM_PROVIDER set and no provider API key in env",
            hint="Set GITPILOT_LLM_PROVIDER and the matching API key, or use ollama locally.",
        )
    if provider == "ollama":
        return CheckResult("model", "green", "provider=ollama (no API key needed)")
    env = _API_KEY_ENVS.get(provider)
    if env and os.environ.get(env):
        return CheckResult("model", "green", f"provider={provider} ({env} set)")
    return CheckResult(
        "model", "red",
        f"provider={provider} but credential is missing",
        hint=_API_KEY_HINTS.get(provider, f"Set the API key env var for {provider}"),
    )


def check_frontend_bundle() -> CheckResult:
    bundle_dir = Path(__file__).parent / "web"
    index = bundle_dir / "index.html"
    if not bundle_dir.exists():
        return CheckResult(
            "frontend", "amber",
            "frontend bundle not packaged",
            hint="Run `make frontend-build` to produce the static bundle.",
        )
    if not index.exists():
        return CheckResult(
            "frontend", "amber",
            "frontend bundle present but index.html missing",
        )
    return CheckResult("frontend", "green", f"bundle at {bundle_dir}")


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------

CheckFn = Callable[[], CheckResult]


def _build_checks(workspace: Path, *, offline: bool) -> Sequence[CheckFn]:
    return (
        check_python,
        check_node,
        check_uv,
        lambda: check_workspace_files(workspace),
        lambda: check_modes_parses(workspace),
        lambda: check_sandbox_reachable(offline=offline),
        lambda: check_mcp_config(workspace),
        check_model_credentials,
        check_frontend_bundle,
    )


def run_checks(
    workspace: Optional[Path] = None,
    *,
    offline: bool = False,
) -> DoctorReport:
    """Execute every check and return a :class:`DoctorReport`."""
    workspace = (workspace or Path.cwd()).resolve()
    report = DoctorReport(offline=offline)
    start = time.monotonic()
    for fn in _build_checks(workspace, offline=offline):
        try:
            report.results.append(fn())
        except Exception as exc:  # pragma: no cover - defensive
            report.results.append(
                CheckResult(getattr(fn, "__name__", "check"), "red", "check failed", detail=str(exc))
            )
    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report


# ----------------------------------------------------------------------
# Renderers
# ----------------------------------------------------------------------

_LEVEL_GLYPHS = {"green": "✅", "amber": "⚠️ ", "red": "❌"}


def render_text(report: DoctorReport) -> str:
    """Render a plain-text table.  Used by both Typer and ``python -m``."""
    width = max((len(r.name) for r in report.results), default=8)
    lines = ["gitpilot doctor"]
    lines.append("-" * 60)
    for r in report.results:
        glyph = _LEVEL_GLYPHS.get(r.level, "?")
        lines.append(f"{glyph}  {r.name.ljust(width)}  {r.summary}")
        if r.hint:
            lines.append(f"     ↳ {r.hint}")
    lines.append("-" * 60)
    lines.append(f"worst:  {report.worst_level}    duration:  {report.duration_ms} ms")
    return "\n".join(lines)


def render_json(report: DoctorReport) -> str:
    """Render a :class:`DoctorReport` as indented JSON for CI consumption."""
    return json.dumps(report.to_dict(), indent=2)


# ----------------------------------------------------------------------
# Module-level CLI ``python -m gitpilot.doctor``
# ----------------------------------------------------------------------

def _module_main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gitpilot.doctor")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run_checks(args.workspace, offline=args.offline)
    print(render_json(report) if args.json else render_text(report))
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - manual entry
    raise SystemExit(_module_main())
