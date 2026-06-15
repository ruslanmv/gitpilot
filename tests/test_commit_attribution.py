"""GitPilot commit attribution (Co-authored-by trailer, Claude-Code style)."""

from __future__ import annotations

import pytest

from gitpilot.commit_attribution import (
    bot_email,
    bot_name,
    co_authored_by,
    with_attribution,
)


def test_co_authored_by_uses_gitpilot_identity():
    assert bot_name() == "GitPilot"
    assert co_authored_by() == f"Co-authored-by: GitPilot <{bot_email()}>"


def test_with_attribution_appends_signature_and_trailer():
    out = with_attribution("Add health endpoint")
    assert out.startswith("Add health endpoint")
    assert "Generated with GitPilot" in out
    assert "Co-authored-by: GitPilot <" in out


def test_with_attribution_is_idempotent():
    once = with_attribution("Fix bug")
    twice = with_attribution(once)
    assert once == twice
    assert once.count("Co-authored-by: GitPilot") == 1


def test_attribution_can_be_disabled(monkeypatch):
    monkeypatch.setenv("GITPILOT_COMMIT_ATTRIBUTION", "false")
    assert with_attribution("Plain commit") == "Plain commit"


def test_bot_email_overridable(monkeypatch):
    monkeypatch.setenv("GITPILOT_BOT_EMAIL", "bot@gitpilot.ruslanmv.com")
    assert bot_email() == "bot@gitpilot.ruslanmv.com"
    assert "bot@gitpilot.ruslanmv.com" in co_authored_by()


def test_bot_email_defaults_to_app_bot(monkeypatch):
    monkeypatch.delenv("GITPILOT_BOT_EMAIL", raising=False)
    monkeypatch.setenv("GITHUB_APP_SLUG", "gitpilota")
    assert bot_email() == "gitpilota[bot]@users.noreply.github.com"
