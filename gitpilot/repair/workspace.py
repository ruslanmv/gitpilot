"""Preparing the throwaway checkout a run works in.

A coding agent can only build what it can read. The clone is therefore a real
step with a real failure mode — not a best-effort nicety — and it needs
credentials for anything that isn't a public repository.

Credentials never reach the command line (visible in the process table). A
token is handed to git through a short-lived askpass helper instead, and the
temporary files are removed as soon as the clone finishes. Nothing here is
written into the checkout's ``.git/config``, so the credential cannot leak into
a later push or a diff.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass

DEFAULT_TIMEOUT = 300
DEFAULT_DEPTH = 1

# Checked in order; the first one set wins. GITPILOT_GIT_TOKEN lets an operator
# give GitPilot its own credential rather than reusing an ambient one.
_TOKEN_ENV = ("GITPILOT_GIT_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "GIT_TOKEN")

_ASKPASS = """#!/bin/sh
case "$1" in
  Username*) printf '%s' "$GIT_ASKPASS_USERNAME" ;;
  *)         printf '%s' "$GIT_ASKPASS_PASSWORD" ;;
esac
"""


class WorkspaceError(RuntimeError):
    """The checkout could not be prepared, with the reason git gave."""


@dataclass
class Workspace:
    path: str
    branch: str = ""
    #: True when the requested base branch did not exist and the repository's
    #: default branch was used instead — the caller should say so out loud.
    fell_back_to_default: bool = False


def token_for(repo_url: str) -> str:
    """The token to authenticate this URL with, if one is configured.

    SSH remotes authenticate with the agent's key, and local paths need nothing,
    so a token is only relevant for http(s).
    """
    if not repo_url.lower().startswith(("http://", "https://")):
        return ""
    for name in _TOKEN_ENV:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def redact(text: str, token: str = "") -> str:
    """Strip anything credential-shaped out of git's output before it is logged."""
    if token:
        text = text.replace(token, "***")
    # https://user:secret@host — keep the host, drop the secret.
    return re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1***@", text)


def _git_env(token: str, askpass_path: str) -> dict[str, str]:
    env = dict(os.environ)
    # Never block a server run waiting for a password on a terminal nobody reads.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    if token:
        env["GIT_ASKPASS"] = askpass_path
        env["GIT_ASKPASS_USERNAME"] = os.getenv("GITPILOT_GIT_USERNAME", "x-access-token")
        env["GIT_ASKPASS_PASSWORD"] = token
    return env


def _write_askpass(directory: str) -> str:
    path = os.path.join(directory, "askpass.sh")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_ASKPASS)
    os.chmod(path, stat.S_IRWXU)  # owner-only
    return path


def _run(argv: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, timeout=timeout, env=env, check=False)


def clone(
    repo_url: str,
    branch: str = "",
    depth: int = DEFAULT_DEPTH,
    timeout: int = DEFAULT_TIMEOUT,
    dest: str | None = None,
) -> Workspace:
    """Clone *repo_url* into a temporary directory and return the workspace.

    Raises :class:`WorkspaceError` when the repository cannot be cloned — a run
    with no code to read must fail loudly rather than produce an invented patch.
    """
    if not (repo_url or "").strip():
        raise WorkspaceError("no repository URL was given for this run")

    workspace = dest or tempfile.mkdtemp(prefix="gitpilot-repair-")
    token = token_for(repo_url)
    helper_dir = tempfile.mkdtemp(prefix="gitpilot-cred-")
    fell_back = False
    try:
        env = _git_env(token, _write_askpass(helper_dir) if token else "")
        base = ["git", "clone", "--depth", str(depth)]

        proc = None
        if branch:
            proc = _run([*base, "--branch", branch, "--single-branch", repo_url, workspace],
                        env, timeout)
            if proc.returncode != 0:
                # The base branch may simply not exist (a repo on "master", or a
                # brand-new repo). Retry on the default branch and say so.
                shutil.rmtree(workspace, ignore_errors=True)
                os.makedirs(workspace, exist_ok=True)
                fell_back = True
                proc = None
        if proc is None:
            proc = _run([*base, repo_url, workspace], env, timeout)

        if proc.returncode != 0:
            detail = redact((proc.stderr or b"").decode(errors="replace").strip(), token)
            hint = "" if token else " (no credential configured: set GITPILOT_GIT_TOKEN for private repositories)"
            raise WorkspaceError(f"could not clone {redact(repo_url)}: {detail[:400]}{hint}")
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"cloning {redact(repo_url)} timed out after {timeout}s") from exc
    finally:
        shutil.rmtree(helper_dir, ignore_errors=True)

    return Workspace(path=workspace, fell_back_to_default=fell_back)


def create_branch(workspace: str, branch: str, timeout: int = 60) -> None:
    """Start the run's work branch. Never pushed from here — the platform decides."""
    if not branch:
        return
    proc = _run(["git", "-C", workspace, "checkout", "-b", branch], _git_env("", ""), timeout)
    if proc.returncode != 0:
        raise WorkspaceError(
            "could not create the work branch: "
            + redact((proc.stderr or b"").decode(errors="replace").strip())[:200]
        )
