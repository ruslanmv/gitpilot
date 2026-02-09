"""Tests for gitpilot.agent_router module."""
from __future__ import annotations

from gitpilot.agent_router import (
    AgentType,
    RequestCategory,
    route,
)


# ---------------------------------------------------------------------------
# Issue routing
# ---------------------------------------------------------------------------

class TestIssueRouting:
    def test_create_issue(self):
        plan = route("Create an issue about the login bug")
        assert plan.category == RequestCategory.ISSUE_MANAGEMENT
        assert AgentType.ISSUE_MANAGER in plan.agents
        assert plan.metadata.get("action") == "create"

    def test_open_new_issue(self):
        plan = route("open a new issue for dark mode feature")
        assert plan.category == RequestCategory.ISSUE_MANAGEMENT

    def test_update_issue(self):
        plan = route("update issue #42 title to 'Fixed'")
        assert plan.category == RequestCategory.ISSUE_MANAGEMENT
        assert plan.metadata.get("action") == "update"
        assert plan.entity_number == 42

    def test_close_issue(self):
        plan = route("close issue #10")
        assert plan.category == RequestCategory.ISSUE_MANAGEMENT

    def test_list_issues(self):
        plan = route("show me all open issues")
        assert plan.category == RequestCategory.ISSUE_MANAGEMENT
        assert plan.metadata.get("action") == "list"

    def test_comment_on_issue(self):
        plan = route("comment on issue #5 with status update")
        assert plan.category == RequestCategory.ISSUE_MANAGEMENT
        assert plan.metadata.get("action") == "comment"
        assert plan.entity_number == 5

    def test_label_issue(self):
        plan = route("label issue #3 as bug")
        assert plan.category == RequestCategory.ISSUE_MANAGEMENT

    def test_assign_issue(self):
        plan = route("assign issue #7 to alice")
        assert plan.category == RequestCategory.ISSUE_MANAGEMENT


# ---------------------------------------------------------------------------
# PR routing
# ---------------------------------------------------------------------------

class TestPRRouting:
    def test_create_pr(self):
        plan = route("create a pull request from feature to main")
        assert plan.category == RequestCategory.PR_MANAGEMENT
        assert AgentType.PR_MANAGER in plan.agents
        assert plan.metadata.get("action") == "create"

    def test_merge_pr(self):
        plan = route("merge pull request #15")
        assert plan.category == RequestCategory.PR_MANAGEMENT
        assert plan.metadata.get("action") == "merge"

    def test_list_prs(self):
        plan = route("show me all pull requests")
        assert plan.category == RequestCategory.PR_MANAGEMENT
        assert plan.metadata.get("action") == "list"

    def test_review_pr(self):
        plan = route("review pull request #8")
        assert plan.category == RequestCategory.PR_MANAGEMENT
        assert AgentType.CODE_REVIEWER in plan.agents
        assert plan.metadata.get("action") == "review"


# ---------------------------------------------------------------------------
# Search routing
# ---------------------------------------------------------------------------

class TestSearchRouting:
    def test_search_code(self):
        plan = route("search for the function parse_config in code")
        assert plan.category == RequestCategory.CODE_SEARCH
        assert AgentType.SEARCH in plan.agents

    def test_find_file(self):
        plan = route("find the file containing the main entry point")
        assert plan.category == RequestCategory.CODE_SEARCH

    def test_search_users(self):
        plan = route("search for user alice")
        assert plan.category == RequestCategory.CODE_SEARCH
        assert plan.metadata.get("search_type") == "users"
        assert plan.requires_repo_context is False

    def test_search_repos(self):
        plan = route("find a repository for machine learning")
        assert plan.category == RequestCategory.CODE_SEARCH
        assert plan.metadata.get("search_type") == "repositories"


# ---------------------------------------------------------------------------
# Code review routing
# ---------------------------------------------------------------------------

class TestCodeReviewRouting:
    def test_review_code_quality(self):
        plan = route("review code quality of the project")
        assert plan.category == RequestCategory.CODE_REVIEW
        assert AgentType.CODE_REVIEWER in plan.agents

    def test_analyze_security(self):
        plan = route("analyze code for security vulnerabilities")
        assert plan.category == RequestCategory.CODE_REVIEW

    def test_check_performance(self):
        plan = route("inspect code for performance issues")
        assert plan.category == RequestCategory.CODE_REVIEW


# ---------------------------------------------------------------------------
# Learning routing
# ---------------------------------------------------------------------------

class TestLearningRouting:
    def test_how_to_question(self):
        plan = route("how do I set up GitHub Actions?")
        assert plan.category == RequestCategory.LEARNING
        assert AgentType.LEARNING in plan.agents
        assert plan.requires_repo_context is False

    def test_explain_question(self):
        plan = route("explain how authentication works")
        assert plan.category == RequestCategory.LEARNING

    def test_best_practice(self):
        plan = route("what is the best practice for CI/CD?")
        assert plan.category == RequestCategory.LEARNING

    def test_github_topic_actions(self):
        plan = route("tell me about GitHub Actions")
        assert plan.category == RequestCategory.LEARNING


# ---------------------------------------------------------------------------
# Default plan+execute routing
# ---------------------------------------------------------------------------

class TestDefaultRouting:
    def test_refactor_request(self):
        plan = route("refactor the login module to use async patterns")
        assert plan.category == RequestCategory.PLAN_EXECUTE
        assert AgentType.EXPLORER in plan.agents
        assert AgentType.PLANNER in plan.agents
        assert AgentType.CODE_WRITER in plan.agents

    def test_add_feature(self):
        plan = route("add a dark mode toggle to the settings panel")
        assert plan.category == RequestCategory.PLAN_EXECUTE

    def test_generic_request(self):
        plan = route("improve the README")
        assert plan.category == RequestCategory.PLAN_EXECUTE


# ---------------------------------------------------------------------------
# Entity number extraction
# ---------------------------------------------------------------------------

class TestEntityExtraction:
    def test_hash_number(self):
        plan = route("update issue #42")
        assert plan.entity_number == 42

    def test_issue_word_number(self):
        plan = route("close issue 99")
        assert plan.entity_number == 99

    def test_no_entity(self):
        plan = route("list all issues")
        assert plan.entity_number is None
