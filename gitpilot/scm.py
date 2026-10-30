# gitpilot/scm.py
"""Provider-neutral source-control operations for GitHub and GitLab.

Agents, CrewAI tools, toolkit tools and HTTP routes call these functions instead
of the GitHub helpers directly. Each one dispatches to :mod:`gitpilot.github_api`
(and its ``github_pulls`` / ``github_issues`` / ``github_search`` siblings) or to
:mod:`gitpilot.gitlab_api`, which returns the same payload shapes, so one agent
implementation serves both providers.

The provider comes from an explicit ``provider=`` argument when given, else from
the execution scope bound with :func:`execution_context`. Without either it is
GitHub, which keeps every pre-existing caller unchanged.

Binding a scope binds the provider *and* that provider's credential together:
inside a GitLab scope the GitHub helpers refuse to run at all, so a GitLab token
can never be sent to api.github.com, and a GitLab helper never sees a GitHub
token.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, cast

from fastapi import HTTPException

from . import github_api, gitlab_api
from .scm_context import (
    DEFAULT_PROVIDER,
    Provider,
    current_provider,
    normalize_provider,
)

__all__ = [
    "Provider", "current_provider", "execution_context", "provider_for", "split_full_name",
    "web_url", "branch_url", "workspace_dir", "get_repo", "create_branch", "get_repo_tree", "get_file",
    "put_file", "delete_file", "list_branches", "list_commits", "compare_refs",
    "list_pull_requests", "get_pull_request", "create_pull_request", "update_pull_request",
    "merge_pull_request", "list_pr_files", "list_pr_reviews", "create_pr_review",
    "add_pr_comment", "list_issues", "get_issue", "create_issue", "update_issue",
    "list_issue_comments", "add_issue_comment", "add_labels", "remove_label",
    "set_assignees", "search_code", "search_issues", "change_request_label",
]


def provider_for(provider: Optional[str] = None) -> Provider:
    """Resolve the provider: explicit argument, then bound scope, then GitHub."""
    if provider:
        try:
            return normalize_provider(provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    bound = current_provider()
    return bound if bound in ("github", "gitlab") else DEFAULT_PROVIDER  # type: ignore[return-value]


@contextmanager
def execution_context(
    provider: Optional[str],
    token: Optional[str],
    ref: Optional[str] = None,
    *,
    repo_full_name: Optional[str] = None,
    repo_id: Optional[Any] = None,
) -> Iterator[None]:
    """Bind provider, credential, ref and repository for one request or agent run."""
    name = provider_for(provider or DEFAULT_PROVIDER)
    if name == "gitlab":
        bound_repo = (repo_full_name, str(repo_id)) if repo_full_name and repo_id not in (None, "") else None
        # Clear any GitHub credential of an enclosing scope while GitLab is bound.
        with github_api.execution_context(None, None), gitlab_api.execution_context(token, ref, bound_repo):
            yield
    else:
        with gitlab_api.execution_context(None, None), github_api.execution_context(token, ref):
            yield


def split_full_name(full_name: str, provider: Optional[str] = None) -> Tuple[str, str]:
    """Split ``owner/repo``; GitLab owners may be nested group paths.

    GitHub names must have exactly two segments. GitLab names need at least two:
    everything before the last ``/`` is the (sub)group namespace.
    """
    name = provider_for(provider)
    parts = [p for p in str(full_name or "").strip().strip("/").split("/")]
    if any(p in ("", ".", "..") for p in parts) or len(parts) < 2:
        raise ValueError(f"Invalid repository name: {full_name!r}")
    if name == "github" and len(parts) != 2:
        raise ValueError(f"Invalid GitHub repository name (expected owner/repo): {full_name!r}")
    return "/".join(parts[:-1]), parts[-1]


def web_url(full_name: str, provider: Optional[str] = None) -> str:
    """Browser URL of a repository."""
    if provider_for(provider) == "gitlab":
        return f"{gitlab_api.gitlab_base_url()}/{full_name}"
    return f"https://github.com/{full_name}"


def branch_url(full_name: str, branch: str, provider: Optional[str] = None) -> str:
    """Browser URL of a branch."""
    if provider_for(provider) == "gitlab":
        return f"{web_url(full_name, 'gitlab')}/-/tree/{branch}"
    return f"https://github.com/{full_name}/tree/{branch}"


def workspace_dir(full_name: str, provider: Optional[str] = None) -> Path:
    """Local data directory of a hosted repository (clone, context pack, memory).

    GitLab repositories live under ``_gitlab/`` (an underscore cannot start a
    GitHub account name), so ``acme/app`` on both providers never share a
    directory.
    """
    owner, repo = split_full_name(full_name, provider)
    root = Path.home() / ".gitpilot" / "workspaces"
    if provider_for(provider) == "gitlab":
        return root.joinpath("_gitlab", *owner.split("/"), repo)
    return root / owner / repo


def change_request_label(provider: Optional[str] = None) -> str:
    """'merge request' on GitLab, 'pull request' on GitHub (for user-facing text)."""
    return "merge request" if provider_for(provider) == "gitlab" else "pull request"


def _gl(provider: Optional[str]) -> bool:
    return provider_for(provider) == "gitlab"


# ---------------------------------------------------------------------------
# Repository, branches, tree, files, history
# ---------------------------------------------------------------------------

async def get_repo(owner: str, repo: str, token: Optional[str] = None, *,
                   provider: Optional[str] = None) -> Dict[str, Any]:
    """Repository metadata (``default_branch``, ``permissions``...)."""
    if _gl(provider):
        return await gitlab_api.get_repo(owner, repo, token=token)
    return await github_api.get_repo(owner, repo, token=token)


async def create_branch(owner: str, repo: str, new_branch: str, from_ref: str = "HEAD",
                        token: Optional[str] = None, *, provider: Optional[str] = None) -> str:
    """Create a branch; returns ``refs/heads/<name>``."""
    if _gl(provider):
        return await gitlab_api.create_branch(owner, repo, new_branch, from_ref, token=token)
    return await github_api.create_branch(owner, repo, new_branch, from_ref, token=token)


async def get_repo_tree(owner: str, repo: str, token: Optional[str] = None, ref: str = "HEAD", *,
                        provider: Optional[str] = None) -> List[Dict[str, str]]:
    """Recursive list of files (blobs)."""
    if _gl(provider):
        return await gitlab_api.get_repo_tree(owner, repo, token=token, ref=ref)
    return cast(List[Dict[str, str]], await github_api.get_repo_tree(owner, repo, token=token, ref=ref))


async def get_file(owner: str, repo: str, path: str, token: Optional[str] = None,
                   ref: Optional[str] = None, *, provider: Optional[str] = None) -> str:
    """Decoded text content of one file."""
    if _gl(provider):
        return await gitlab_api.get_file(owner, repo, path, token=token, ref=ref)
    return await github_api.get_file(owner, repo, path, token=token, ref=ref)


async def put_file(owner: str, repo: str, path: str, content: str, message: str,
                   token: Optional[str] = None, branch: Optional[str] = None, *,
                   provider: Optional[str] = None) -> Dict[str, Any]:
    """Create or update a file as one commit on ``branch``."""
    if _gl(provider):
        return await gitlab_api.put_file(owner, repo, path, content, message, token=token, branch=branch)
    return await github_api.put_file(owner, repo, path, content, message, token=token, branch=branch)


async def delete_file(owner: str, repo: str, path: str, message: str,
                      token: Optional[str] = None, branch: Optional[str] = None, *,
                      provider: Optional[str] = None) -> Dict[str, Any]:
    """Delete a file as one commit on ``branch``."""
    if _gl(provider):
        return await gitlab_api.delete_file(owner, repo, path, message, token=token, branch=branch)
    return await github_api.delete_file(owner, repo, path, message, token=token, branch=branch)


async def list_branches(owner: str, repo: str, *, query: Optional[str] = None, page: int = 1,
                        per_page: int = 100, token: Optional[str] = None,
                        provider: Optional[str] = None) -> Dict[str, Any]:
    """Branches in the branch-picker shape ``{branches, has_more, ...}``."""
    if _gl(provider):
        return await gitlab_api.list_branches(owner, repo, query=query, page=page,
                                              per_page=per_page, token=token)
    return await github_api.list_branches(owner, repo, query=query, page=page,
                                          per_page=per_page, token=token)


async def list_commits(owner: str, repo: str, *, ref: Optional[str] = None, path: Optional[str] = None,
                       page: int = 1, per_page: int = 30, token: Optional[str] = None,
                       provider: Optional[str] = None) -> List[Dict[str, Any]]:
    """Commit history summaries."""
    if _gl(provider):
        return await gitlab_api.list_commits(owner, repo, ref=ref, path=path, page=page,
                                             per_page=per_page, token=token)
    return await github_api.list_commits(owner, repo, ref=ref, path=path, page=page,
                                         per_page=per_page, token=token)


async def compare_refs(owner: str, repo: str, base: str, head: str, token: Optional[str] = None, *,
                       provider: Optional[str] = None) -> Dict[str, Any]:
    """Diff of ``head`` against ``base``: ``{commits, files[{filename,status,patch}]}``."""
    if _gl(provider):
        return await gitlab_api.compare_refs(owner, repo, base, head, token=token)
    return await github_api.compare_refs(owner, repo, base, head, token=token)


# ---------------------------------------------------------------------------
# Pull requests (GitHub) / merge requests (GitLab)
# ---------------------------------------------------------------------------

def _pulls(provider: Optional[str]) -> Any:
    if _gl(provider):
        return gitlab_api
    from . import github_pulls
    return github_pulls


def _issues(provider: Optional[str]) -> Any:
    if _gl(provider):
        return gitlab_api
    from . import github_issues
    return github_issues


def _search(provider: Optional[str]) -> Any:
    if _gl(provider):
        return gitlab_api
    from . import github_search
    return github_search


async def list_pull_requests(owner: str, repo: str, *args: Any, provider: Optional[str] = None,
                             **kwargs: Any) -> List[Dict[str, Any]]:
    """Pull/merge requests in the GitHub pull-request shape."""
    return cast(List[Dict[str, Any]], await _pulls(provider).list_pull_requests(owner, repo, *args, **kwargs))


async def get_pull_request(owner: str, repo: str, pull_number: int, token: Optional[str] = None, *,
                           provider: Optional[str] = None) -> Dict[str, Any]:
    """One pull/merge request."""
    return cast(Dict[str, Any], await _pulls(provider).get_pull_request(owner, repo, pull_number, token=token))


async def create_pull_request(owner: str, repo: str, *args: Any, provider: Optional[str] = None,
                              **kwargs: Any) -> Dict[str, Any]:
    """Open a pull request (GitHub) or merge request (GitLab)."""
    return cast(Dict[str, Any], await _pulls(provider).create_pull_request(owner, repo, *args, **kwargs))


async def update_pull_request(owner: str, repo: str, pull_number: int, *args: Any,
                              provider: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    """Edit a pull/merge request."""
    return cast(Dict[str, Any], await _pulls(provider).update_pull_request(owner, repo, pull_number, *args, **kwargs))


async def merge_pull_request(owner: str, repo: str, pull_number: int, *args: Any,
                             provider: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    """Merge a pull/merge request."""
    return cast(Dict[str, Any], await _pulls(provider).merge_pull_request(owner, repo, pull_number, *args, **kwargs))


async def list_pr_files(owner: str, repo: str, pull_number: int, *args: Any,
                        provider: Optional[str] = None, **kwargs: Any) -> List[Dict[str, Any]]:
    """Changed files of a pull/merge request."""
    return cast(List[Dict[str, Any]], await _pulls(provider).list_pr_files(owner, repo, pull_number, *args, **kwargs))


async def list_pr_reviews(owner: str, repo: str, pull_number: int, token: Optional[str] = None, *,
                          provider: Optional[str] = None) -> List[Dict[str, Any]]:
    """Reviews (GitHub) or approvals (GitLab)."""
    return cast(List[Dict[str, Any]], await _pulls(provider).list_pr_reviews(owner, repo, pull_number, token=token))


async def create_pr_review(owner: str, repo: str, pull_number: int, *args: Any,
                           provider: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    """Approve / request changes / comment."""
    return cast(Dict[str, Any], await _pulls(provider).create_pr_review(owner, repo, pull_number, *args, **kwargs))


async def add_pr_comment(owner: str, repo: str, pull_number: int, body: str,
                         token: Optional[str] = None, *, provider: Optional[str] = None) -> Dict[str, Any]:
    """Comment on a pull/merge request."""
    return cast(Dict[str, Any], await _pulls(provider).add_pr_comment(owner, repo, pull_number, body, token=token))


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

async def list_issues(owner: str, repo: str, *args: Any, provider: Optional[str] = None,
                      **kwargs: Any) -> List[Dict[str, Any]]:
    """Issues in the GitHub issue shape."""
    return cast(List[Dict[str, Any]], await _issues(provider).list_issues(owner, repo, *args, **kwargs))


async def get_issue(owner: str, repo: str, issue_number: int, token: Optional[str] = None, *,
                    provider: Optional[str] = None) -> Dict[str, Any]:
    """One issue."""
    return cast(Dict[str, Any], await _issues(provider).get_issue(owner, repo, issue_number, token=token))


async def create_issue(owner: str, repo: str, *args: Any, provider: Optional[str] = None,
                       **kwargs: Any) -> Dict[str, Any]:
    """Open an issue."""
    return cast(Dict[str, Any], await _issues(provider).create_issue(owner, repo, *args, **kwargs))


async def update_issue(owner: str, repo: str, issue_number: int, *args: Any,
                       provider: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    """Edit an issue."""
    return cast(Dict[str, Any], await _issues(provider).update_issue(owner, repo, issue_number, *args, **kwargs))


async def list_issue_comments(owner: str, repo: str, issue_number: int, *args: Any,
                              provider: Optional[str] = None, **kwargs: Any) -> List[Dict[str, Any]]:
    """Comments on an issue."""
    return cast(List[Dict[str, Any]], await _issues(provider).list_issue_comments(owner, repo, issue_number, *args, **kwargs))


async def add_issue_comment(owner: str, repo: str, issue_number: int, body: str,
                            token: Optional[str] = None, *, provider: Optional[str] = None) -> Dict[str, Any]:
    """Comment on an issue."""
    return cast(Dict[str, Any], await _issues(provider).add_issue_comment(owner, repo, issue_number, body, token=token))


async def add_labels(owner: str, repo: str, issue_number: int, labels: List[str],
                     token: Optional[str] = None, *, provider: Optional[str] = None) -> List[Dict[str, Any]]:
    """Add labels to an issue."""
    return cast(List[Dict[str, Any]], await _issues(provider).add_labels(owner, repo, issue_number, labels, token=token))


async def remove_label(owner: str, repo: str, issue_number: int, label: str,
                       token: Optional[str] = None, *, provider: Optional[str] = None) -> Any:
    """Remove one label from an issue."""
    return await _issues(provider).remove_label(owner, repo, issue_number, label, token=token)


async def set_assignees(owner: str, repo: str, issue_number: int, assignees: List[str],
                        token: Optional[str] = None, *, provider: Optional[str] = None) -> Dict[str, Any]:
    """Replace an issue's assignees."""
    return cast(Dict[str, Any], await _issues(provider).set_assignees(owner, repo, issue_number, assignees, token=token))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_code(query: str, *args: Any, provider: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    """Code search (GitLab: within the given project)."""
    return cast(Dict[str, Any], await _search(provider).search_code(query, *args, **kwargs))


async def search_issues(query: str, *args: Any, provider: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    """Issue / pull-request search (GitLab: within the given project)."""
    return cast(Dict[str, Any], await _search(provider).search_issues(query, *args, **kwargs))
