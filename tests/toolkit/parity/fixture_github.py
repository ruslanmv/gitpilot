"""A fake GitHub repository for parity cases — Batch V4-A2.

The GitHub-mode tools reach the network through four functions in
:mod:`gitpilot.github_api` (``get_repo_tree``, ``get_file``, ``put_file``,
``delete_file``).  This patches those four, in place, with an in-memory
repository built from the same file set as the local fixture — so a parity case
can run the CrewAI tool and the registry tool against identical content and
compare, offline, in CI.

Patching at the ``github_api`` boundary rather than at ``httpx`` is deliberate:
the tools' own logic (tree filtering, the fetch cap, read-modify-commit) still
executes, which is what the parity gate is about.  Faking HTTP would test our
model of GitHub's wire format, which is not the thing at risk here.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional
from unittest.mock import patch

from .fixture_repo import FILES

#: Deterministic sha so recorded corpora do not churn.
COMMIT_SHA = "fixturesha0123456789abcdef0123456789abcd"
BRANCH = "main"
OWNER = "fixture"
REPO = "repo"


class FakeGitHubRepo:
    """An in-memory repository with GitHub's call signatures."""

    def __init__(self, files: Optional[Dict[str, str]] = None) -> None:
        self.files: Dict[str, str] = dict(files if files is not None else FILES)
        self.commits: List[dict] = []

    async def get_repo_tree(self, owner, repo, *, token=None, ref=None):
        return [{"path": path, "type": "blob"} for path in sorted(self.files)]

    async def get_file(self, owner, repo, path, *, token=None, ref=None):
        if path not in self.files:
            # The real client raises on a missing path; the tools depend on that
            # to distinguish "absent" from "empty".
            raise FileNotFoundError(f"404 Not Found: {path}")
        return self.files[path]

    async def put_file(self, owner, repo, path, content, message, *, token=None, branch=None):
        self.files[path] = content
        self.commits.append({"path": path, "message": message, "op": "put"})
        return {"commit_sha": COMMIT_SHA, "path": path}

    async def delete_file(self, owner, repo, path, message, *, token=None, branch=None):
        self.files.pop(path, None)
        self.commits.append({"path": path, "message": message, "op": "delete"})
        return {"commit_sha": COMMIT_SHA, "path": path}


@contextmanager
def fake_github(files: Optional[Dict[str, str]] = None) -> Iterator[FakeGitHubRepo]:
    """Patch the four ``github_api`` entry points the fs tools use.

    Both the ``github_api`` module attributes and the names ``agent_tools``
    imported at module load are patched, because the legacy tool resolves
    ``get_file``/``get_repo_tree`` through its own namespace.
    """
    repo = FakeGitHubRepo(files)
    with (
        patch("gitpilot.github_api.get_repo_tree", repo.get_repo_tree),
        patch("gitpilot.github_api.get_file", repo.get_file),
        patch("gitpilot.github_api.put_file", repo.put_file),
        patch("gitpilot.github_api.delete_file", repo.delete_file),
        patch("gitpilot.agent_tools.get_repo_tree", repo.get_repo_tree),
        patch("gitpilot.agent_tools.get_file", repo.get_file),
    ):
        yield repo
