"""Tests for gitpilot.use_case module."""
from __future__ import annotations

import json

import pytest

from gitpilot.use_case import (
    UseCaseManager,
    UseCase,
    UseCaseSpec,
    INITIAL_ASSISTANT_MESSAGE,
)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestUseCaseCRUD:
    def test_list_empty(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        assert mgr.list_use_cases() == []

    def test_create_and_list(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Auth Feature")
        assert uc.title == "Auth Feature"
        assert uc.use_case_id
        assert not uc.is_active
        assert not uc.is_finalized
        assert len(uc.messages) == 1  # initial assistant message
        assert uc.messages[0].role == "assistant"
        assert INITIAL_ASSISTANT_MESSAGE in uc.messages[0].content

        lst = mgr.list_use_cases()
        assert len(lst) == 1
        assert lst[0]["title"] == "Auth Feature"

    def test_get_use_case(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Test")
        loaded = mgr.get_use_case(uc.use_case_id)
        assert loaded is not None
        assert loaded.title == "Test"
        assert loaded.use_case_id == uc.use_case_id

    def test_get_nonexistent(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        assert mgr.get_use_case("nonexistent") is None


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class TestUseCaseChat:
    def test_chat_adds_messages(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Chat Test")

        result = mgr.chat(uc.use_case_id, "Summary: Build a login page")
        assert result is not None
        # Should have: initial assistant + user + response
        assert len(result.messages) == 3
        assert result.messages[1].role == "user"
        assert result.messages[1].content == "Summary: Build a login page"
        assert result.messages[2].role == "assistant"

    def test_chat_updates_spec(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Spec Test")

        result = mgr.chat(uc.use_case_id, "Summary: User authentication system")
        assert result.spec.summary == "User authentication system"

    def test_chat_with_requirements(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Req Test")

        result = mgr.chat(uc.use_case_id, "- Login with email\n- Password reset flow")
        assert len(result.spec.requirements) == 2
        assert "Login with email" in result.spec.requirements

    def test_chat_nonexistent_use_case(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        assert mgr.chat("nonexistent", "hello") is None


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

class TestFinalize:
    def test_finalize_marks_active(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="To Finalize")

        result = mgr.finalize(uc.use_case_id)
        assert result is not None
        assert result.is_active is True
        assert result.is_finalized is True

    def test_finalize_exports_markdown(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Export Test")
        mgr.chat(uc.use_case_id, "Summary: A test feature")
        mgr.finalize(uc.use_case_id)

        md_path = mgr.use_cases_dir / f"{uc.use_case_id}.md"
        assert md_path.exists()
        content = md_path.read_text()
        assert "Export Test" in content

    def test_finalize_deactivates_others(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc1 = mgr.create_use_case(title="First")
        uc2 = mgr.create_use_case(title="Second")

        mgr.finalize(uc1.use_case_id)
        mgr.finalize(uc2.use_case_id)

        loaded1 = mgr.get_use_case(uc1.use_case_id)
        loaded2 = mgr.get_use_case(uc2.use_case_id)
        assert loaded1.is_active is False
        assert loaded2.is_active is True

    def test_finalize_nonexistent(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        assert mgr.finalize("nonexistent") is None


# ---------------------------------------------------------------------------
# Active use case
# ---------------------------------------------------------------------------

class TestActiveUseCase:
    def test_no_active(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        assert mgr.get_active_use_case() is None

    def test_get_active(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Active")
        mgr.finalize(uc.use_case_id)

        active = mgr.get_active_use_case()
        assert active is not None
        assert active.use_case_id == uc.use_case_id
        assert active.is_active is True


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------

class TestSpecParsing:
    def test_labeled_fields(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Parse Test")

        msg = (
            "Summary: Build a dashboard\n"
            "Problem: Users cannot see metrics\n"
            "Users: Product managers\n"
            "Constraints: Must use React\n"
        )
        result = mgr.chat(uc.use_case_id, msg)
        assert result.spec.summary == "Build a dashboard"
        assert result.spec.problem == "Users cannot see metrics"
        assert result.spec.users == "Product managers"
        assert "Must use React" in result.spec.constraints

    def test_acceptance_criteria_detection(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="AC Test")

        msg = "- User should be able to login\n- Must verify email"
        result = mgr.chat(uc.use_case_id, msg)
        # These contain "should" and "must" so should be classified as AC
        assert len(result.spec.acceptance_criteria) == 2

    def test_to_dict_roundtrip(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Roundtrip")
        mgr.chat(uc.use_case_id, "Summary: Test roundtrip")

        d = uc.to_dict()
        assert isinstance(d, dict)
        assert d["title"] == "Roundtrip"
        assert "spec" in d
        assert "messages" in d

    def test_to_summary(self, tmp_path):
        mgr = UseCaseManager(tmp_path)
        uc = mgr.create_use_case(title="Summary Test")
        summary = uc.to_summary()
        assert summary["title"] == "Summary Test"
        assert "use_case_id" in summary
        assert "messages" not in summary  # summary excludes messages
