# gitpilot/test_detection.py
"""
Auto-detect test framework and build the test command.

Works for all platforms (server-side detection for web/HF Spaces,
VS Code can call this via the API or detect locally).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# (marker file/dir, framework name, command)
FRAMEWORK_MARKERS = [
    ("jest.config.js", "jest", "npx jest --ci --no-coverage 2>&1"),
    ("jest.config.ts", "jest", "npx jest --ci --no-coverage 2>&1"),
    ("jest.config.mjs", "jest", "npx jest --ci --no-coverage 2>&1"),
    ("vitest.config.ts", "vitest", "npx vitest run 2>&1"),
    ("vitest.config.js", "vitest", "npx vitest run 2>&1"),
    ("vitest.config.mjs", "vitest", "npx vitest run 2>&1"),
    (".mocharc.yml", "mocha", "npx mocha 2>&1"),
    (".mocharc.json", "mocha", "npx mocha 2>&1"),
    ("pytest.ini", "pytest", "python -m pytest --tb=short -q 2>&1"),
    ("setup.cfg", "pytest", "python -m pytest --tb=short -q 2>&1"),
    ("conftest.py", "pytest", "python -m pytest --tb=short -q 2>&1"),
    ("Cargo.toml", "cargo", "cargo test 2>&1"),
    ("go.mod", "go", "go test ./... 2>&1"),
    ("mix.exs", "elixir", "mix test 2>&1"),
    ("Gemfile", "ruby", "bundle exec rake test 2>&1"),
]


async def detect_test_command(workspace_path: Path) -> Optional[str]:
    """Detect the test runner from project manifest files."""
    for marker, name, command in FRAMEWORK_MARKERS:
        if (workspace_path / marker).exists():
            logger.info("Detected test framework: %s (via %s)", name, marker)
            return command

    # Check pyproject.toml for [tool.pytest]
    pyproject = workspace_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(errors="replace")
            if "[tool.pytest" in content:
                return "python -m pytest --tb=short -q 2>&1"
        except OSError:
            pass

    # Check package.json scripts.test
    pkg = workspace_path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            test_script = data.get("scripts", {}).get("test", "")
            if test_script and "no test specified" not in test_script:
                return "npm test -- --ci 2>&1"
        except (json.JSONDecodeError, OSError):
            pass

    return None


async def detect_framework_name(workspace_path: Path) -> Optional[str]:
    """Return just the framework name (for UI display)."""
    for marker, name, _ in FRAMEWORK_MARKERS:
        if (workspace_path / marker).exists():
            return name

    pyproject = workspace_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(errors="replace")
            if "[tool.pytest" in content:
                return "pytest"
        except OSError:
            pass

    pkg = workspace_path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            if data.get("scripts", {}).get("test"):
                return "npm"
        except (json.JSONDecodeError, OSError):
            pass

    return None
