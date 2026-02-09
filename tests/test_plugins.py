"""Tests for gitpilot.plugins module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gitpilot.plugins import MANIFEST_FILE, PluginInfo, PluginManager


# ---------------------------------------------------------------------------
# Helper to create a plugin directory on disk
# ---------------------------------------------------------------------------


def _make_plugin(base: Path, name: str, **extras) -> Path:
    """Create a minimal plugin directory with a manifest. Returns the path."""
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": extras.get("version", "1.0.0"),
        "description": extras.get("description", f"{name} plugin"),
        "author": extras.get("author", "tester"),
        "skills": extras.get("skills", []),
        "hooks": extras.get("hooks", []),
        "mcp": extras.get("mcp", []),
    }
    (plugin_dir / MANIFEST_FILE).write_text(json.dumps(manifest))
    return plugin_dir


# ---------------------------------------------------------------------------
# PluginInfo.from_manifest / to_dict
# ---------------------------------------------------------------------------


class TestPluginInfo:
    def test_from_manifest_all_fields(self, tmp_path):
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        manifest = {
            "name": "my-plugin",
            "version": "2.1.0",
            "description": "A great plugin",
            "author": "dev",
            "source": "https://github.com/org/my-plugin",
            "skills": ["skills/*.md"],
            "hooks": ["hooks.json"],
            "mcp": ["mcp.json"],
        }
        manifest_path = plugin_dir / MANIFEST_FILE
        manifest_path.write_text(json.dumps(manifest))

        info = PluginInfo.from_manifest(manifest_path)
        assert info.name == "my-plugin"
        assert info.version == "2.1.0"
        assert info.description == "A great plugin"
        assert info.author == "dev"
        assert info.skills == ["skills/*.md"]
        assert info.hooks == ["hooks.json"]
        assert info.mcp_configs == ["mcp.json"]
        assert info.path == plugin_dir

    def test_from_manifest_defaults(self, tmp_path):
        plugin_dir = tmp_path / "minimal"
        plugin_dir.mkdir()
        (plugin_dir / MANIFEST_FILE).write_text(json.dumps({"name": "minimal"}))

        info = PluginInfo.from_manifest(plugin_dir / MANIFEST_FILE)
        assert info.name == "minimal"
        assert info.version == "0.0.0"
        assert info.description == ""
        assert info.skills == []

    def test_to_dict(self, tmp_path):
        info = PluginInfo(
            name="p1", version="1.0.0", description="desc",
            author="auth", source="local", skills=["s/*.md"],
            hooks=["h.json"], mcp_configs=["m.json"], path=tmp_path,
        )
        d = info.to_dict()
        assert d["name"] == "p1"
        assert d["version"] == "1.0.0"
        assert d["path"] == str(tmp_path)

    def test_to_dict_none_path(self):
        info = PluginInfo(name="p2")
        d = info.to_dict()
        assert d["path"] is None


# ---------------------------------------------------------------------------
# PluginManager — install from local
# ---------------------------------------------------------------------------


class TestInstallLocal:
    def test_install_from_local_dir(self, tmp_path):
        source = _make_plugin(tmp_path / "source", "local-plug")
        mgr = PluginManager(plugins_dir=tmp_path / "plugins")

        info = mgr.install(str(source))
        assert info.name == "local-plug"
        assert (tmp_path / "plugins" / "local-plug" / MANIFEST_FILE).exists()

    def test_install_local_overwrites_existing(self, tmp_path):
        source = _make_plugin(tmp_path / "source", "overwrite-plug", version="1.0.0")
        mgr = PluginManager(plugins_dir=tmp_path / "plugins")
        mgr.install(str(source))

        # Update source version and re-install
        manifest = json.loads((source / MANIFEST_FILE).read_text())
        manifest["version"] = "2.0.0"
        (source / MANIFEST_FILE).write_text(json.dumps(manifest))
        info = mgr.install(str(source))
        assert info.version == "2.0.0"

    def test_install_local_missing_manifest_raises(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        mgr = PluginManager(plugins_dir=tmp_path / "plugins")

        with pytest.raises(FileNotFoundError, match=MANIFEST_FILE):
            mgr.install(str(empty_dir))

    def test_install_invalid_source_raises(self, tmp_path):
        mgr = PluginManager(plugins_dir=tmp_path / "plugins")
        with pytest.raises(ValueError, match="Invalid plugin source"):
            mgr.install("not-a-url-or-path-that-exists")


# ---------------------------------------------------------------------------
# PluginManager — install from git
# ---------------------------------------------------------------------------


class TestInstallGit:
    def test_install_from_git_clones(self, tmp_path):
        mgr = PluginManager(plugins_dir=tmp_path / "plugins")
        dest = tmp_path / "plugins" / "my-plugin"

        def fake_clone(cmd, **kwargs):
            # Simulate git clone by creating the directory with a manifest
            dest.mkdir(parents=True, exist_ok=True)
            (dest / MANIFEST_FILE).write_text(json.dumps({
                "name": "my-plugin", "version": "1.0.0",
            }))
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_clone) as mock_run:
            info = mgr.install("https://github.com/org/my-plugin.git")

        assert info.name == "my-plugin"
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "clone" in args
        assert "--depth=1" in args

    def test_install_from_git_pulls_existing(self, tmp_path):
        mgr = PluginManager(plugins_dir=tmp_path / "plugins")
        dest = tmp_path / "plugins" / "existing"
        _make_plugin(dest.parent, "existing", version="1.0.0")

        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            info = mgr.install("https://github.com/org/existing")

        assert info.name == "existing"
        args = mock_run.call_args[0][0]
        assert "pull" in args


# ---------------------------------------------------------------------------
# PluginManager — uninstall
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_removes_directory(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        _make_plugin(plugins_dir, "removable")
        mgr = PluginManager(plugins_dir=plugins_dir)

        result = mgr.uninstall("removable")
        assert result is True
        assert not (plugins_dir / "removable").exists()

    def test_uninstall_nonexistent_returns_false(self, tmp_path):
        mgr = PluginManager(plugins_dir=tmp_path / "plugins")
        result = mgr.uninstall("ghost")
        assert result is False

    def test_uninstall_clears_cache(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        _make_plugin(plugins_dir, "cached")
        mgr = PluginManager(plugins_dir=plugins_dir)
        mgr.get_plugin("cached")  # populate cache
        assert "cached" in mgr._cache

        mgr.uninstall("cached")
        assert "cached" not in mgr._cache


# ---------------------------------------------------------------------------
# PluginManager — list / get
# ---------------------------------------------------------------------------


class TestListAndGet:
    def test_list_installed_returns_all(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        _make_plugin(plugins_dir, "alpha")
        _make_plugin(plugins_dir, "beta")
        mgr = PluginManager(plugins_dir=plugins_dir)

        plugins = mgr.list_installed()
        names = {p.name for p in plugins}
        assert names == {"alpha", "beta"}

    def test_list_installed_empty_dir(self, tmp_path):
        mgr = PluginManager(plugins_dir=tmp_path / "plugins")
        assert mgr.list_installed() == []

    def test_list_installed_skips_bad_plugins(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        _make_plugin(plugins_dir, "good")
        bad = plugins_dir / "bad"
        bad.mkdir()
        (bad / MANIFEST_FILE).write_text("not json{{{")

        mgr = PluginManager(plugins_dir=plugins_dir)
        plugins = mgr.list_installed()
        assert len(plugins) == 1
        assert plugins[0].name == "good"

    def test_get_plugin_found(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        _make_plugin(plugins_dir, "findme")
        mgr = PluginManager(plugins_dir=plugins_dir)

        info = mgr.get_plugin("findme")
        assert info is not None
        assert info.name == "findme"

    def test_get_plugin_not_found(self, tmp_path):
        mgr = PluginManager(plugins_dir=tmp_path / "plugins")
        assert mgr.get_plugin("nope") is None

    def test_get_plugin_from_cache(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        _make_plugin(plugins_dir, "cached-plug")
        mgr = PluginManager(plugins_dir=plugins_dir)

        # First call loads from disk
        info1 = mgr.get_plugin("cached-plug")
        # Second call should come from cache
        info2 = mgr.get_plugin("cached-plug")
        assert info1 is info2


# ---------------------------------------------------------------------------
# PluginManager — load_all_skills
# ---------------------------------------------------------------------------


class TestLoadAllSkills:
    def test_loads_skill_files(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugin_dir = _make_plugin(plugins_dir, "skill-plug", skills=["skills/*.md"])

        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        (skills_dir / "review.md").write_text(
            "---\nname: review\ndescription: Code review\n---\nReview the code."
        )

        mgr = PluginManager(plugins_dir=plugins_dir)
        skills = mgr.load_all_skills()
        assert len(skills) == 1
        assert skills[0]["plugin"] == "skill-plug"
        assert skills[0]["skill"].name == "review"

    def test_no_skills_returns_empty(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        _make_plugin(plugins_dir, "no-skills")
        mgr = PluginManager(plugins_dir=plugins_dir)
        assert mgr.load_all_skills() == []


# ---------------------------------------------------------------------------
# PluginManager — load_all_hooks
# ---------------------------------------------------------------------------


class TestLoadAllHooks:
    def test_loads_hook_files(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugin_dir = _make_plugin(plugins_dir, "hook-plug", hooks=["hooks.json"])

        hooks_data = [
            {"event": "post_edit", "name": "lint", "command": "ruff ."},
        ]
        (plugin_dir / "hooks.json").write_text(json.dumps(hooks_data))

        mgr = PluginManager(plugins_dir=plugins_dir)
        hooks = mgr.load_all_hooks()
        assert len(hooks) == 1
        assert hooks[0]["plugin"] == "hook-plug"
        assert hooks[0]["name"] == "lint"


# ---------------------------------------------------------------------------
# PluginManager — load_all_mcp_configs
# ---------------------------------------------------------------------------


class TestLoadAllMCPConfigs:
    def test_loads_mcp_configs(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugin_dir = _make_plugin(plugins_dir, "mcp-plug", mcp=["mcp.json"])

        mcp_data = {"servers": [
            {"name": "pg", "type": "stdio", "command": "pg-mcp"},
        ]}
        (plugin_dir / "mcp.json").write_text(json.dumps(mcp_data))

        mgr = PluginManager(plugins_dir=plugins_dir)
        configs = mgr.load_all_mcp_configs()
        assert len(configs) == 1
        assert configs[0]["plugin"] == "mcp-plug"
        assert configs[0]["name"] == "pg"

    def test_loads_mcp_array_format(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugin_dir = _make_plugin(plugins_dir, "mcp-arr", mcp=["mcp.json"])

        mcp_data = [{"name": "s1", "type": "http", "url": "http://x"}]
        (plugin_dir / "mcp.json").write_text(json.dumps(mcp_data))

        mgr = PluginManager(plugins_dir=plugins_dir)
        configs = mgr.load_all_mcp_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "s1"
