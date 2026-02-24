"""Tests for gitpilot.context_pack module."""
from __future__ import annotations

import json

import pytest

from gitpilot.context_pack import build_context_pack


# ---------------------------------------------------------------------------
# Empty workspace (no context)
# ---------------------------------------------------------------------------

class TestEmptyWorkspace:
    def test_returns_empty_string(self, tmp_path):
        result = build_context_pack(tmp_path, query="anything")
        assert result == ""

    def test_no_query(self, tmp_path):
        result = build_context_pack(tmp_path)
        assert result == ""


# ---------------------------------------------------------------------------
# Conventions only
# ---------------------------------------------------------------------------

class TestConventions:
    def test_includes_conventions(self, tmp_path):
        gp = tmp_path / ".gitpilot"
        gp.mkdir()
        (gp / "GITPILOT.md").write_text("Use pytest for all tests")

        result = build_context_pack(tmp_path, query="test")
        assert "Project Context Pack" in result
        assert "Conventions" in result
        assert "pytest" in result

    def test_disabled_flag(self, tmp_path):
        gp = tmp_path / ".gitpilot"
        gp.mkdir()
        (gp / "GITPILOT.md").write_text("Use pytest for all tests")

        result = build_context_pack(tmp_path, query="test", include_conventions=False)
        assert "Conventions" not in result


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

class TestUseCasePack:
    def test_includes_active_use_case(self, tmp_path):
        from gitpilot.use_case import UseCaseManager

        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Auth Feature")
        mgr.chat(uc.use_case_id, "Summary: Build authentication")
        mgr.finalize(uc.use_case_id)

        result = build_context_pack(tmp_path, query="auth")
        assert "Active Use Case" in result
        assert "Auth Feature" in result

    def test_no_active_use_case(self, tmp_path):
        from gitpilot.use_case import UseCaseManager

        mgr = UseCaseManager(tmp_path)
        mgr.create_use_case(title="Not Finalized")

        result = build_context_pack(tmp_path, query="test")
        # No active use case, so no use case section
        assert "Active Use Case" not in result

    def test_disabled_flag(self, tmp_path):
        from gitpilot.use_case import UseCaseManager

        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Auth Feature")
        mgr.finalize(uc.use_case_id)

        result = build_context_pack(tmp_path, query="auth", include_use_case=False)
        assert "Active Use Case" not in result


# ---------------------------------------------------------------------------
# Asset chunks
# ---------------------------------------------------------------------------

class TestAssetChunks:
    def test_includes_relevant_chunks(self, tmp_path):
        from gitpilot.context_vault import ContextVault

        vault = ContextVault(tmp_path)
        vault.upload_asset(
            "meeting.txt",
            b"We discussed the authentication flow and database schema.",
            mime="text/plain",
        )

        result = build_context_pack(tmp_path, query="authentication database")
        assert "Relevant References" in result
        assert "authentication" in result.lower()

    def test_no_query_no_assets_section(self, tmp_path):
        from gitpilot.context_vault import ContextVault

        vault = ContextVault(tmp_path)
        vault.upload_asset("notes.txt", b"some content", mime="text/plain")

        # No query means no asset search
        result = build_context_pack(tmp_path, query="")
        assert "Relevant References" not in result

    def test_disabled_flag(self, tmp_path):
        from gitpilot.context_vault import ContextVault

        vault = ContextVault(tmp_path)
        vault.upload_asset("notes.txt", b"auth details", mime="text/plain")

        result = build_context_pack(tmp_path, query="auth", include_assets=False)
        assert "Relevant References" not in result


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------

class TestCombined:
    def test_full_context_pack(self, tmp_path):
        from gitpilot.context_vault import ContextVault
        from gitpilot.use_case import UseCaseManager

        # Set up conventions
        gp = tmp_path / ".gitpilot"
        gp.mkdir()
        (gp / "GITPILOT.md").write_text("Use type hints everywhere")

        # Set up use case
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Login Feature")
        mgr.chat(uc.use_case_id, "Summary: Build a login page")
        mgr.finalize(uc.use_case_id)

        # Set up assets
        vault = ContextVault(tmp_path)
        vault.upload_asset(
            "design.md",
            b"The login page should have email and password fields.",
            mime="text/markdown",
        )

        result = build_context_pack(tmp_path, query="login page")
        assert "Project Context Pack" in result
        assert "Conventions" in result
        assert "Active Use Case" in result
        assert "Relevant References" in result

    def test_max_total_chars_respected(self, tmp_path):
        gp = tmp_path / ".gitpilot"
        gp.mkdir()
        (gp / "GITPILOT.md").write_text("x" * 5000)

        result = build_context_pack(tmp_path, query="test", max_total_chars=100)
        # Should be bounded
        assert len(result) <= 200  # some overhead for the header
