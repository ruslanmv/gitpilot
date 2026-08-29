"""A deterministic git repository for parity cases — Batch V4-A2.

Offline by construction: a real ``git init`` with pinned author, message and
content, so ``git ls-files``, ``git grep``, ``git status`` and ``git log``
behave exactly as they do against a user's checkout.  Mocking git here would
mean the parity gate proved the mocks matched, not the tools.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from gitpilot.workspace import WorkspaceInfo

#: Content is fixed so parity failures point at the tools, not the fixture.
FILES: dict[str, str] = {
    "README.md": "# Fixture\n\nA repository for parity cases.\n",
    "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    "src/app.py": (
        "import os\n"
        "\n"
        "\n"
        "def greet(name):\n"
        '    """Say hello."""\n'
        '    return f"hello {name}"\n'
        "\n"
        "\n"
        "def farewell(name):\n"
        '    return f"bye {name}"\n'
    ),
    "src/util.py": "def helper():\n    return 1\n",
    "tests/test_app.py": (
        "from src.app import greet\n"
        "\n"
        "\n"
        "def test_greet():\n"
        '    assert greet("world") == "hello world"\n'
    ),
    "docs/guide.md": "Read the guide.\nIt mentions greet twice: greet.\n",
}

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Parity Fixture",
    "GIT_AUTHOR_EMAIL": "parity@example.invalid",
    "GIT_COMMITTER_NAME": "Parity Fixture",
    "GIT_COMMITTER_EMAIL": "parity@example.invalid",
    "GIT_AUTHOR_DATE": "2024-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2024-01-01T00:00:00+00:00",
    "GIT_CONFIG_GLOBAL": "/dev/null",   # ignore the developer's ~/.gitconfig
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(args: list[str], cwd: Path) -> None:
    import os

    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={**os.environ, **_GIT_ENV},
    )


def build_fixture_repo(root: Path) -> WorkspaceInfo:
    """Create the fixture checkout at *root* and return its WorkspaceInfo.

    Idempotent: called twice on the same path it returns the existing checkout
    rather than re-initialising, since a second ``git commit`` with nothing
    staged fails.  Callers that need a pristine tree pass a fresh path.
    """
    if (root / ".git").is_dir():
        return WorkspaceInfo(
            owner="fixture",
            repo="repo",
            path=root,
            branch="main",
            remote_url="https://example.invalid/fixture/repo.git",
        )

    root.mkdir(parents=True, exist_ok=True)
    for rel, text in FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    _git(["init", "--initial-branch=main"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-m", "Fixture commit"], root)

    return WorkspaceInfo(
        owner="fixture",
        repo="repo",
        path=root,
        branch="main",
        remote_url="https://example.invalid/fixture/repo.git",
    )
