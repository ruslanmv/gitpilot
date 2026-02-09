"""Shared fixtures for GitPilot tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture()
def mock_github_request():
    """Patch github_request in all modules that import it."""
    mock = AsyncMock()
    with (
        patch("gitpilot.github_api.github_request", mock),
        patch("gitpilot.github_issues.github_request", mock),
        patch("gitpilot.github_pulls.github_request", mock),
        patch("gitpilot.github_search.github_request", mock),
    ):
        yield mock


@pytest.fixture()
def repo_context():
    """Set a dummy repo context for agent tools."""
    from gitpilot.agent_tools import set_repo_context

    set_repo_context("test-owner", "test-repo", token="ghp_fake", branch="main")
    yield {"owner": "test-owner", "repo": "test-repo", "token": "ghp_fake", "branch": "main"}
