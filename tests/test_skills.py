"""Tests for gitpilot.skills module."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitpilot.skills import Skill, SkillManager, _parse_yaml_simple


# ---------------------------------------------------------------------------
# _parse_yaml_simple
# ---------------------------------------------------------------------------


class TestParseYamlSimple:
    def test_key_value_string(self):
        result = _parse_yaml_simple("name: review\ndescription: Review code")
        assert result["name"] == "review"
        assert result["description"] == "Review code"

    def test_boolean_true(self):
        result = _parse_yaml_simple("auto_trigger: true")
        assert result["auto_trigger"] is True

    def test_boolean_false(self):
        result = _parse_yaml_simple("auto_trigger: false")
        assert result["auto_trigger"] is False

    def test_integer_value(self):
        result = _parse_yaml_simple("count: 42")
        assert result["count"] == 42

    def test_list_values(self):
        text = "required_tools:\n  - git_diff\n  - read_local_file"
        result = _parse_yaml_simple(text)
        assert result["required_tools"] == ["git_diff", "read_local_file"]

    def test_empty_string(self):
        result = _parse_yaml_simple("")
        assert result == {}

    def test_comments_ignored(self):
        text = "# this is a comment\nname: test"
        result = _parse_yaml_simple(text)
        assert result["name"] == "test"
        assert "#" not in str(result.keys())

    def test_mixed_keys(self):
        text = (
            "name: deploy\n"
            "auto_trigger: true\n"
            "required_tools:\n"
            "  - ssh\n"
            "  - docker\n"
            "version: 3"
        )
        result = _parse_yaml_simple(text)
        assert result["name"] == "deploy"
        assert result["auto_trigger"] is True
        assert result["required_tools"] == ["ssh", "docker"]
        assert result["version"] == 3

    def test_empty_list_key(self):
        text = "items:\n"
        result = _parse_yaml_simple(text)
        assert result["items"] == []

    def test_value_with_colon(self):
        result = _parse_yaml_simple("description: Run tests: unit and integration")
        assert result["description"] == "Run tests: unit and integration"


# ---------------------------------------------------------------------------
# Skill.from_file
# ---------------------------------------------------------------------------


class TestSkillFromFile:
    def test_full_front_matter(self, tmp_path):
        md = (
            "---\n"
            "name: review\n"
            "description: Review code quality\n"
            "auto_trigger: false\n"
            "required_tools:\n"
            "  - git_diff\n"
            "  - read_local_file\n"
            "---\n"
            "\n"
            "Review the code changes on the current branch.\n"
            "Focus on security issues."
        )
        path = tmp_path / "review.md"
        path.write_text(md)

        skill = Skill.from_file(path)
        assert skill.name == "review"
        assert skill.description == "Review code quality"
        assert skill.auto_trigger is False
        assert skill.required_tools == ["git_diff", "read_local_file"]
        assert "Review the code changes" in skill.prompt_template
        assert skill.source_file == path

    def test_no_front_matter_uses_filename(self, tmp_path):
        path = tmp_path / "quick-fix.md"
        path.write_text("Just fix the bug.\n")

        skill = Skill.from_file(path)
        assert skill.name == "quick-fix"
        assert skill.description == ""
        assert skill.prompt_template == "Just fix the bug."

    def test_front_matter_name_overrides_filename(self, tmp_path):
        md = "---\nname: custom-name\n---\nDo something."
        path = tmp_path / "filename.md"
        path.write_text(md)

        skill = Skill.from_file(path)
        assert skill.name == "custom-name"

    def test_auto_trigger_true(self, tmp_path):
        md = "---\nname: auto\nauto_trigger: true\n---\nAuto skill."
        path = tmp_path / "auto.md"
        path.write_text(md)

        skill = Skill.from_file(path)
        assert skill.auto_trigger is True


# ---------------------------------------------------------------------------
# Skill.render
# ---------------------------------------------------------------------------


class TestSkillRender:
    def test_render_with_variables(self):
        skill = Skill(
            name="greet",
            prompt_template="Hello {{name}}, welcome to {{project}}!",
        )
        result = skill.render({"name": "Alice", "project": "GitPilot"})
        assert result == "Hello Alice, welcome to GitPilot!"

    def test_render_no_context(self):
        skill = Skill(name="plain", prompt_template="No vars here.")
        result = skill.render()
        assert result == "No vars here."

    def test_render_partial_context(self):
        skill = Skill(name="partial", prompt_template="{{a}} and {{b}}")
        result = skill.render({"a": "first"})
        assert result == "first and {{b}}"

    def test_render_empty_context(self):
        skill = Skill(name="e", prompt_template="{{x}}")
        result = skill.render({})
        assert result == "{{x}}"


# ---------------------------------------------------------------------------
# Skill.to_dict
# ---------------------------------------------------------------------------


class TestSkillToDict:
    def test_to_dict_all_fields(self, tmp_path):
        path = tmp_path / "skill.md"
        skill = Skill(
            name="deploy",
            description="Deploy the app",
            auto_trigger=True,
            required_tools=["ssh", "docker"],
            source_file=path,
        )
        d = skill.to_dict()
        assert d["name"] == "deploy"
        assert d["description"] == "Deploy the app"
        assert d["auto_trigger"] is True
        assert d["required_tools"] == ["ssh", "docker"]
        assert d["source"] == str(path)

    def test_to_dict_none_source(self):
        skill = Skill(name="nosrc")
        d = skill.to_dict()
        assert d["source"] is None


# ---------------------------------------------------------------------------
# SkillManager — load_all
# ---------------------------------------------------------------------------


class TestSkillManagerLoadAll:
    def test_load_from_project_and_user_dirs(self, tmp_path):
        workspace = tmp_path / "workspace"
        project_skills = workspace / ".gitpilot" / "skills"
        project_skills.mkdir(parents=True)
        (project_skills / "proj.md").write_text("---\nname: proj-skill\n---\nProject prompt.")

        user_dir = tmp_path / "user"
        user_skills = user_dir / "skills"
        user_skills.mkdir(parents=True)
        (user_skills / "usr.md").write_text("---\nname: user-skill\n---\nUser prompt.")

        mgr = SkillManager(workspace_path=workspace, user_dir=user_dir)
        count = mgr.load_all()
        assert count == 2

    def test_load_all_no_dirs(self, tmp_path):
        mgr = SkillManager(workspace_path=tmp_path / "nope", user_dir=tmp_path / "also-nope")
        count = mgr.load_all()
        assert count == 0

    def test_load_all_skips_bad_files(self, tmp_path):
        user_dir = tmp_path / "user"
        skills_dir = user_dir / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "good.md").write_text("---\nname: good\n---\nGood.")
        # Create a file that will fail to parse (directory disguised as .md)
        bad_dir = skills_dir / "bad.md"
        bad_dir.mkdir()

        mgr = SkillManager(user_dir=user_dir)
        count = mgr.load_all()
        # The good one should load, the bad one should be skipped
        assert count >= 1
        assert mgr.get("good") is not None


# ---------------------------------------------------------------------------
# SkillManager — register / get / list / invoke
# ---------------------------------------------------------------------------


class TestSkillManagerOperations:
    def test_register_and_get(self):
        mgr = SkillManager()
        skill = Skill(name="manual", description="Manually registered")
        mgr.register(skill)
        assert mgr.get("manual") is skill

    def test_get_nonexistent_returns_none(self):
        mgr = SkillManager()
        assert mgr.get("ghost") is None

    def test_list_skills(self):
        mgr = SkillManager()
        mgr.register(Skill(name="a", description="Skill A"))
        mgr.register(Skill(name="b", description="Skill B"))
        listed = mgr.list_skills()
        assert len(listed) == 2
        names = {s["name"] for s in listed}
        assert names == {"a", "b"}

    def test_invoke_existing_skill(self):
        mgr = SkillManager()
        mgr.register(Skill(
            name="greet",
            prompt_template="Hello {{user}}!",
        ))
        result = mgr.invoke("greet", {"user": "Bob"})
        assert result == "Hello Bob!"

    def test_invoke_nonexistent_returns_none(self):
        mgr = SkillManager()
        assert mgr.invoke("missing") is None


# ---------------------------------------------------------------------------
# SkillManager — find_auto_triggers
# ---------------------------------------------------------------------------


class TestFindAutoTriggers:
    def test_matches_by_name(self):
        mgr = SkillManager()
        mgr.register(Skill(name="lint", description="Run linter", auto_trigger=True))
        mgr.register(Skill(name="deploy", description="Deploy app", auto_trigger=True))
        mgr.register(Skill(name="review", description="Code review", auto_trigger=False))

        matches = mgr.find_auto_triggers("Please lint my code")
        names = [s.name for s in matches]
        assert "lint" in names
        assert "review" not in names

    def test_matches_by_description_words(self):
        mgr = SkillManager()
        mgr.register(Skill(
            name="sec-audit", description="security vulnerability scan",
            auto_trigger=True,
        ))
        matches = mgr.find_auto_triggers("check for security issues")
        assert len(matches) == 1
        assert matches[0].name == "sec-audit"

    def test_no_match_when_auto_trigger_false(self):
        mgr = SkillManager()
        mgr.register(Skill(name="deploy", description="Deploy", auto_trigger=False))
        matches = mgr.find_auto_triggers("deploy the app")
        assert matches == []

    def test_no_matches_empty(self):
        mgr = SkillManager()
        matches = mgr.find_auto_triggers("something random")
        assert matches == []

    def test_case_insensitive_match(self):
        mgr = SkillManager()
        mgr.register(Skill(name="LINT", description="Linter", auto_trigger=True))
        matches = mgr.find_auto_triggers("run lint please")
        assert len(matches) == 1
