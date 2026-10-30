# gitpilot/workspace.py
"""Local workspace manager — clone, sync, and operate on repositories locally.

Manages a workspace directory (~/.gitpilot/workspaces/{owner}/{repo}) where
repositories are cloned and kept in sync.  All local file operations go through
this module to ensure path-traversal safety and consistency.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gitpilot.models import WorkspaceSummary

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path.home() / ".gitpilot" / "workspaces"


@dataclass
class WorkspaceInfo:
    """Metadata about an active workspace."""

    owner: str
    repo: str
    path: Path
    branch: str
    remote_url: str
    is_dirty: bool = False
    last_sync: str | None = None
    provider: str = "github"
    # Git config passed through the environment for fetch/push: carries the
    # credential as an HTTP header for the provider host only. Never persisted
    # to .git/config and never printed in an error (it is not on the argv).
    auth_env: dict[str, str] = field(default_factory=dict, repr=False)


def _remote_url(owner: str, repo: str, provider: str) -> str:
    """Credential-free HTTPS clone URL of a hosted repository."""
    if provider == "gitlab":
        from gitpilot.gitlab_api import gitlab_base_url

        return f"{gitlab_base_url()}/{owner}/{repo}.git"
    return f"https://github.com/{owner}/{repo}.git"


def _active_key(owner: str, repo: str, provider: str) -> str:
    # GitHub keeps its historical "owner/repo" key.
    return f"gitlab:{owner}/{repo}" if provider == "gitlab" else f"{owner}/{repo}"


def _with_config(env: dict[str, str], key: str, value: str) -> dict[str, str]:
    """Append one ``GIT_CONFIG_*`` entry to an environment built by :func:`_auth_env`."""
    out = dict(env)
    n = int(out.get("GIT_CONFIG_COUNT", "0"))
    out[f"GIT_CONFIG_KEY_{n}"] = key
    out[f"GIT_CONFIG_VALUE_{n}"] = value
    out["GIT_CONFIG_COUNT"] = str(n + 1)
    return out


def _auth_env(remote_url: str, token: str | None, provider: str) -> dict[str, str]:
    """``GIT_CONFIG_*`` variables adding an Authorization header for one host."""
    if not token:
        return {}
    user = "oauth2" if provider == "gitlab" else "x-access-token"
    basic = base64.b64encode(f"{user}:{token}".encode()).decode("ascii")
    scheme, _, rest = remote_url.partition("://")
    origin = f"{scheme}://{rest.split('/', 1)[0]}/"
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{origin}.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        "GIT_TERMINAL_PROMPT": "0",
    }


class WorkspaceManager:
    """Manages local git clones for repository operations.

    Responsibilities:
    - Clone repositories on first access (shallow for speed)
    - Checkout and track branches
    - Provide safe file read / write / delete / search
    - Sync with remote (pull / push)
    - Create feature branches, commit, and push
    """

    def __init__(self, root: Path | None = None):
        self.root = root or WORKSPACE_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self._active: dict[str, WorkspaceInfo] = {}

    def workspace_path(self, owner: str, repo: str, provider: str = "github") -> Path:
        # GitLab clones live under "_gitlab/" so the same path on both
        # providers never shares a checkout ("_" cannot start a GitHub login).
        if provider == "gitlab":
            return self.root.joinpath("_gitlab", *owner.split("/"), repo)
        return self.root / owner / repo

    # ------------------------------------------------------------------
    # Workspace lifecycle
    # ------------------------------------------------------------------

    async def ensure_workspace(
        self,
        owner: str,
        repo: str,
        token: str | None,
        branch: str | None = None,
        provider: str = "github",
    ) -> WorkspaceInfo:
        """Clone if absent, fetch if present, checkout *branch*.

        The credential travels as an HTTP header in the git process
        environment, scoped to the provider host; the stored remote URL has no
        credential (an older clone that embedded one is rewritten).
        """
        ws_path = self.workspace_path(owner, repo, provider)
        remote_url = _remote_url(owner, repo, provider)
        auth = _auth_env(remote_url, token, provider)

        if not (ws_path / ".git").exists():
            ws_path.mkdir(parents=True, exist_ok=True)
            await self._run_git(
                ["git", "clone", "--depth=1", remote_url, str(ws_path)],
                cwd=ws_path.parent,
                env=auth,
            )
        else:
            # Fetch from the credential-free URL (a command-scoped override, so
            # a token an older clone embedded in .git/config is not used), then
            # rewrite the stored remote so that token is gone for good.
            await self._run_git(
                ["git", "fetch", "origin", "--prune"],
                cwd=ws_path,
                env={"GIT_TERMINAL_PROMPT": "0", **_with_config(auth, "remote.origin.url", remote_url)},
            )
            await self._run_git(
                ["git", "remote", "set-url", "origin", remote_url],
                cwd=ws_path, check=False,
            )

        target_branch = branch or await self._default_branch(ws_path)
        await self._checkout(ws_path, target_branch)

        info = WorkspaceInfo(
            owner=owner,
            repo=repo,
            path=ws_path,
            branch=target_branch,
            remote_url=remote_url,
            provider=provider,
            auth_env=auth,
        )
        self._active[_active_key(owner, repo, provider)] = info
        return info

    async def cleanup(self, owner: str, repo: str, provider: str = "github") -> bool:
        ws_path = self.workspace_path(owner, repo, provider)
        if ws_path.exists():
            shutil.rmtree(ws_path)
            self._active.pop(_active_key(owner, repo, provider), None)
            return True
        return False

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _safe_resolve(self, ws: WorkspaceInfo, file_path: str) -> Path:
        full = (ws.path / file_path).resolve()
        if not str(full).startswith(str(ws.path.resolve())):
            raise PermissionError(f"Path traversal blocked: {file_path}")
        return full

    async def read_file(self, ws: WorkspaceInfo, file_path: str) -> str:
        full = self._safe_resolve(ws, file_path)
        return full.read_text(encoding="utf-8", errors="replace")

    async def write_file(
        self, ws: WorkspaceInfo, file_path: str, content: str
    ) -> dict[str, Any]:
        full = self._safe_resolve(ws, file_path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return {"path": file_path, "size": len(content)}

    async def delete_file(self, ws: WorkspaceInfo, file_path: str) -> bool:
        full = self._safe_resolve(ws, file_path)
        if full.exists():
            full.unlink()
            return True
        return False

    async def list_files(
        self, ws: WorkspaceInfo, directory: str = "."
    ) -> list[str]:
        result = await self._run_git(
            ["git", "ls-files", "--cached", "--others",
             "--exclude-standard", directory],
            cwd=ws.path,
        )
        return [f for f in result.stdout.strip().split("\n") if f]

    async def search_files(
        self, ws: WorkspaceInfo, pattern: str, path: str = "."
    ) -> list[dict[str, Any]]:
        try:
            result = await self._run_git(
                ["git", "grep", "-n", "--no-color", "-I", pattern, "--", path],
                cwd=ws.path, check=False,
            )
            matches = []
            for line in result.stdout.strip().split("\n"):
                if ":" in line and line:
                    parts = line.split(":", 2)
                    if len(parts) >= 3:
                        matches.append({
                            "file": parts[0],
                            "line": int(parts[1]) if parts[1].isdigit() else 0,
                            "content": parts[2],
                        })
            return matches
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Git operations
    # ------------------------------------------------------------------

    async def create_branch(
        self, ws: WorkspaceInfo, branch_name: str
    ) -> str:
        await self._run_git(
            ["git", "checkout", "-b", branch_name], cwd=ws.path,
        )
        ws.branch = branch_name
        return branch_name

    async def commit(
        self, ws: WorkspaceInfo, message: str, files: list[str] | None = None,
    ) -> dict[str, str]:
        if files:
            await self._run_git(["git", "add", "--"] + files, cwd=ws.path)
        else:
            await self._run_git(["git", "add", "-A"], cwd=ws.path)

        await self._run_git(["git", "commit", "-m", message], cwd=ws.path)
        sha_result = await self._run_git(
            ["git", "rev-parse", "HEAD"], cwd=ws.path,
        )
        return {"sha": sha_result.stdout.strip(), "message": message}

    async def push(
        self, ws: WorkspaceInfo, force: bool = False,
    ) -> dict[str, str]:
        cmd = ["git", "push", "-u", "origin", ws.branch]
        if force:
            cmd.insert(2, "--force-with-lease")
        await self._run_git(cmd, cwd=ws.path, env=ws.auth_env or None)
        return {"branch": ws.branch, "status": "pushed"}

    async def diff(self, ws: WorkspaceInfo, staged: bool = False) -> str:
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")
        result = await self._run_git(cmd, cwd=ws.path)
        return result.stdout

    async def status(self, ws: WorkspaceInfo) -> dict[str, Any]:
        result = await self._run_git(
            ["git", "status", "--porcelain=v2", "--branch"], cwd=ws.path,
        )
        return self._parse_status(result.stdout)

    async def log(
        self, ws: WorkspaceInfo, count: int = 10,
    ) -> list[dict[str, str]]:
        result = await self._run_git(
            ["git", "log", f"-{count}", "--format=%H|%an|%ae|%s|%aI"],
            cwd=ws.path,
        )
        commits: list[dict[str, str]] = []
        for line in result.stdout.strip().split("\n"):
            if "|" in line:
                parts = line.split("|", 4)
                commits.append({
                    "sha": parts[0],
                    "author": parts[1],
                    "email": parts[2],
                    "message": parts[3],
                    "date": parts[4] if len(parts) > 4 else "",
                })
        return commits

    async def stash(self, ws: WorkspaceInfo, pop: bool = False) -> str:
        cmd = ["git", "stash", "pop" if pop else "push"]
        result = await self._run_git(cmd, cwd=ws.path)
        return result.stdout.strip()

    async def merge(
        self, ws: WorkspaceInfo, branch: str,
    ) -> dict[str, Any]:
        result = await self._run_git(
            ["git", "merge", branch], cwd=ws.path, check=False,
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "conflicts": result.returncode != 0,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_git(self, cmd, cwd=None, env=None, check=True):
        full_env = {**os.environ, **(env or {})}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
        )
        stdout, stderr = await proc.communicate()

        class _Result:
            pass

        r = _Result()
        r.stdout = stdout.decode("utf-8", errors="replace")
        r.stderr = stderr.decode("utf-8", errors="replace")
        r.returncode = proc.returncode
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Git command failed ({proc.returncode}): {' '.join(cmd)}\n{r.stderr}"
            )
        return r

    async def _default_branch(self, ws_path: Path) -> str:
        result = await self._run_git(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=ws_path, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("/")[-1]
        return "main"

    async def _checkout(self, ws_path: Path, branch: str):
        result = await self._run_git(
            ["git", "checkout", branch], cwd=ws_path, check=False,
        )
        if result.returncode != 0:
            await self._run_git(
                ["git", "checkout", "-b", branch, f"origin/{branch}"],
                cwd=ws_path, check=False,
            )

    @staticmethod
    def _parse_status(raw: str) -> dict[str, Any]:
        modified, added, deleted, untracked = [], [], [], []
        branch_name = "unknown"
        for line in raw.split("\n"):
            if line.startswith("# branch.head"):
                branch_name = line.split()[-1]
            elif line.startswith("1 "):
                parts = line.split()
                xy = parts[1] if len(parts) > 1 else ""
                path = parts[-1] if parts else ""
                if "M" in xy:
                    modified.append(path)
                elif "A" in xy:
                    added.append(path)
                elif "D" in xy:
                    deleted.append(path)
            elif line.startswith("? "):
                untracked.append(line[2:])
        return {
            "branch": branch_name,
            "modified": modified,
            "added": added,
            "deleted": deleted,
            "untracked": untracked,
            "clean": not any([modified, added, deleted, untracked]),
        }


async def summarize_workspace(folder_path: str) -> WorkspaceSummary:
    """Summarize workspace state for the redesigned UI status endpoint."""
    folder_path = os.path.abspath(folder_path)
    folder_name = os.path.basename(folder_path) if folder_path else None
    folder_open = os.path.isdir(folder_path) if folder_path else False

    summary = WorkspaceSummary(
        folder_open=folder_open,
        folder_path=folder_path,
        folder_name=folder_name,
    )

    if not folder_open:
        return summary

    # Check for git repo
    git_dir = os.path.join(folder_path, ".git")
    if not os.path.exists(git_dir):
        return summary

    summary.git_detected = True
    summary.repo_root = folder_path

    # Get repo name from folder
    summary.repo_name = folder_name

    # Get branch and remotes via git CLI
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--abbrev-ref", "HEAD",
            cwd=folder_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            summary.branch = stdout.decode().strip()
    except Exception:
        logger.debug("Branch detection failed", exc_info=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "remote", "-v",
            cwd=folder_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            remotes = set()
            for line in stdout.decode().strip().splitlines():
                parts = line.split()
                if parts:
                    remotes.add(parts[0])
            summary.remotes = sorted(remotes)
    except Exception:
        logger.debug("Remote detection failed", exc_info=True)

    return summary
