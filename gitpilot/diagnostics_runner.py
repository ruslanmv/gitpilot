# gitpilot/diagnostics_runner.py
"""
Run linters and type-checkers server-side.

For web/HF Spaces where VS Code language services aren't available,
this module detects and runs the appropriate linter, then parses
the output into structured diagnostic entries.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from .terminal import TerminalExecutor, TerminalSession, CommandResult

logger = logging.getLogger(__name__)

# (marker file, linter name, command)
LINTER_MARKERS = [
    ("tsconfig.json", "tsc", "npx tsc --noEmit 2>&1"),
    ("eslint.config.js", "eslint", "npx eslint . --format compact 2>&1"),
    ("eslint.config.mjs", "eslint", "npx eslint . --format compact 2>&1"),
    (".eslintrc.json", "eslint", "npx eslint . --format compact 2>&1"),
    (".eslintrc.js", "eslint", "npx eslint . --format compact 2>&1"),
    (".eslintrc.yml", "eslint", "npx eslint . --format compact 2>&1"),
    ("biome.json", "biome", "npx biome check . 2>&1"),
    ("pyproject.toml", "ruff", "ruff check . --output-format text 2>&1"),
    ("setup.cfg", "flake8", "flake8 . 2>&1"),
    (".flake8", "flake8", "flake8 . 2>&1"),
    ("Cargo.toml", "cargo", "cargo check --message-format short 2>&1"),
    ("go.mod", "go", "go vet ./... 2>&1"),
    ("clippy.toml", "clippy", "cargo clippy 2>&1"),
]


async def detect_linter(workspace_path: Path) -> Optional[tuple[str, str]]:
    """Detect linter from project files. Returns (name, command) or None."""
    for marker, name, command in LINTER_MARKERS:
        if (workspace_path / marker).exists():
            logger.info("Detected linter: %s (via %s)", name, marker)
            return name, command
    return None


async def run_linter(
    workspace_path: Path,
    executor: Optional[TerminalExecutor] = None,
    timeout: int = 60,
) -> Optional[CommandResult]:
    """Detect the linter and run it. Returns CommandResult or None."""
    detection = await detect_linter(workspace_path)
    if not detection:
        return None

    name, command = detection
    executor = executor or TerminalExecutor()
    session = TerminalSession(workspace_path=workspace_path)

    logger.info("Running linter %s: %s", name, command)
    return await executor.execute(session, command, timeout=timeout)


def parse_diagnostics(output: str) -> List[dict]:
    """
    Parse linter output into structured entries (best-effort).

    Handles common formats:
      - file:line:col: severity: message (gcc, tsc, eslint compact)
      - file:line: severity: message
      - file(line,col): error TS1234: message (tsc)
    """
    entries: List[dict] = []
    for line in output.splitlines()[:200]:
        line = line.strip()
        if not line:
            continue

        # Try tsc format: file(line,col): error TSxxxx: message
        if "): error " in line or "): warning " in line:
            try:
                paren_idx = line.index("(")
                close_idx = line.index(")")
                file_path = line[:paren_idx].strip()
                pos = line[paren_idx + 1 : close_idx]
                rest = line[close_idx + 2 :].strip()
                parts = pos.split(",")
                line_num = int(parts[0]) if parts else 0
                severity = "error" if rest.startswith("error") else "warning"
                message = rest.split(":", 1)[-1].strip() if ":" in rest else rest
                entries.append({
                    "file": file_path,
                    "line": line_num,
                    "severity": severity,
                    "message": message,
                })
                continue
            except (ValueError, IndexError):
                pass

        # Try standard format: file:line:col: severity: message
        parts = line.split(":", 4)
        if len(parts) >= 4:
            try:
                file_path = parts[0].strip()
                line_num = int(parts[1].strip())
                rest = ":".join(parts[2:]).strip()
                severity = "error" if "error" in rest.lower() else "warning"
                message = rest
                entries.append({
                    "file": file_path,
                    "line": line_num,
                    "severity": severity,
                    "message": message,
                })
                continue
            except (ValueError, IndexError):
                pass

        # Fallback: treat as info
        if any(kw in line.lower() for kw in ("error", "warning", "fail")):
            entries.append({
                "file": "",
                "line": 0,
                "severity": "error" if "error" in line.lower() else "warning",
                "message": line,
            })

    return entries
