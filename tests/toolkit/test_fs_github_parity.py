"""GitHub-mode parity for the filesystem tools — Batches V4-A2 / V4-A3.

The local cases in ``test_fs.py`` cover the on-disk backend.  These cover the
other one, where the tools' own logic — tree listing, glob pre-filtering, the
fetch cap, read-modify-commit — is what has to keep behaving identically.
"""
from __future__ import annotations

import pytest

import gitpilot.agent_tools as at
from gitpilot.toolkit import RepoBinding, ToolExecutionContext, ToolRegistry
from gitpilot.toolkit import fs as fs_tools

from .parity.fixture_github import BRANCH, OWNER, REPO, fake_github
from .parity.fixture_repo import build_fixture_repo
from .parity.harness import ParityCase, assert_parity, run_case


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    fs_tools.register(reg)
    return reg


@pytest.fixture()
def repo_ctx() -> ToolExecutionContext:
    return ToolExecutionContext(
        repo=RepoBinding(owner=OWNER, repo=REPO, token="fixture-token", branch=BRANCH),
    )


PARITY_CASES = [
    ParityCase(
        name="fs.read/github",
        tool="fs.read",
        arguments={"path": "src/app.py"},
        legacy=lambda ws: at.read_file.func("src/app.py"),
    ),
    ParityCase(
        name="fs.read/github-missing",
        tool="fs.read",
        arguments={"path": "absent.py"},
        legacy=lambda ws: at.read_file.func("absent.py"),
    ),
    ParityCase(
        name="fs.list/github",
        tool="fs.list",
        arguments={},
        legacy=lambda ws: at.list_repository_files.func(),
    ),
    ParityCase(
        name="fs.glob/github",
        tool="fs.glob",
        arguments={"pattern": "**/*.py"},
        legacy=lambda ws: at.list_repository_files_glob.func("**/*.py"),
    ),
    ParityCase(
        name="fs.glob/github-no-match",
        tool="fs.glob",
        arguments={"pattern": "**/*.rs"},
        legacy=lambda ws: at.list_repository_files_glob.func("**/*.rs"),
    ),
    ParityCase(
        name="fs.grep/github",
        tool="fs.grep",
        arguments={"pattern": "def greet", "path_pattern": "**/*.py"},
        legacy=lambda ws: at.grep_repository.func("def greet", "**/*.py"),
    ),
    ParityCase(
        name="fs.grep/github-path-miss",
        tool="fs.grep",
        arguments={"pattern": "greet", "path_pattern": "**/*.rs"},
        legacy=lambda ws: at.grep_repository.func("greet", "**/*.rs"),
    ),
    ParityCase(
        name="fs.write/github",
        tool="fs.write",
        arguments={"path": "added.py", "content": "x = 1\n", "commit_message": "Add added.py"},
        legacy=lambda ws: at.write_file.func("added.py", "x = 1\n", "Add added.py"),
    ),
    ParityCase(
        name="fs.edit/github",
        tool="fs.edit",
        arguments={
            "path": "src/util.py",
            "old_string": "return 1",
            "new_string": "return 2",
            "commit_message": "Bump",
        },
        legacy=lambda ws: at.edit_file.func("src/util.py", "return 1", "return 2", "Bump"),
    ),
    ParityCase(
        name="fs.edit/github-conflict",
        tool="fs.edit",
        arguments={
            "path": "docs/guide.md",
            "old_string": "greet",
            "new_string": "hello",
            "commit_message": "Rename",
        },
        legacy=lambda ws: at.edit_file.func("docs/guide.md", "greet", "hello", "Rename"),
    ),
    ParityCase(
        name="fs.delete/github",
        tool="fs.delete",
        arguments={"path": "docs/guide.md", "commit_message": "Drop guide"},
        legacy=lambda ws: at.delete_repo_file.func("docs/guide.md", "Drop guide"),
    ),
]


@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda c: c.name)
def test_github_mode_matches_the_crewai_tool(case, registry, repo_ctx, tmp_path):
    """Each side gets its own fake repository so mutations cannot cross over."""
    ws = build_fixture_repo(tmp_path / "shape-only")

    with fake_github():
        at.set_repo_context(OWNER, REPO, token="fixture-token", branch=BRANCH)
        legacy_output = case.legacy(ws)

    with fake_github():
        outcome = run_case(
            ParityCase(
                name=case.name,
                tool=case.tool,
                arguments=case.arguments,
                legacy=lambda _ws, captured=legacy_output: captured,
                normalise=case.normalise,
            ),
            ws,
            registry,
            repo_ctx,
        )

    assert_parity(outcome)


def test_grep_respects_the_file_fetch_cap(registry, repo_ctx):
    """200 paths at ~50 KB each is already 10 MB; the cap is part of the contract."""
    many = {f"f{i}.py": "needle\n" for i in range(fs_tools.FILE_FETCH_CAP + 25)}

    with fake_github(many) as repo:
        fetched: list[str] = []
        original = repo.get_file

        async def counting_get_file(owner, name, path, *, token=None, ref=None):
            fetched.append(path)
            return await original(owner, name, path, token=token, ref=ref)

        repo.get_file = counting_get_file  # type: ignore[method-assign]
        from unittest.mock import patch

        with patch("gitpilot.github_api.get_file", counting_get_file):
            import asyncio

            from gitpilot.toolkit import ToolCall

            asyncio.run(
                registry.execute(
                    ToolCall(id="cap", tool="fs.grep", arguments={"pattern": "needle"}), repo_ctx,
                )
            )

    assert len(fetched) == fs_tools.FILE_FETCH_CAP
