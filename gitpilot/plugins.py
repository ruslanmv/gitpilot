# gitpilot/plugins.py
"""Plugin system for GitPilot.

Plugins extend GitPilot with additional skills, hooks, MCP configs,
and custom agent types.  Plugins are installed from git URLs or local
directories into ``~/.gitpilot/plugins/``.

A plugin is a directory containing a ``plugin.json`` manifest:

.. code-block:: json

    {
        "name": "my-plugin",
        "version": "1.0.0",
        "description": "Does amazing things",
        "skills": ["skills/*.md"],
        "hooks": ["hooks.json"],
        "mcp": ["mcp.json"]
    }
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path.home() / ".gitpilot" / "plugins"
MANIFEST_FILE = "plugin.json"


@dataclass
class PluginInfo:
    """Metadata about an installed plugin."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    source: str = ""  # git URL or local path
    skills: List[str] = field(default_factory=list)
    hooks: List[str] = field(default_factory=list)
    mcp_configs: List[str] = field(default_factory=list)
    path: Optional[Path] = None

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> "PluginInfo":
        data = json.loads(manifest_path.read_text())
        return cls(
            name=data["name"],
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            source=data.get("source", ""),
            skills=data.get("skills", []),
            hooks=data.get("hooks", []),
            mcp_configs=data.get("mcp", []),
            path=manifest_path.parent,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "source": self.source,
            "skills": self.skills,
            "hooks": self.hooks,
            "mcp_configs": self.mcp_configs,
            "path": str(self.path) if self.path else None,
        }


class PluginManager:
    """Discover, install, and manage GitPilot plugins."""

    def __init__(self, plugins_dir: Optional[Path] = None) -> None:
        self.plugins_dir = plugins_dir or PLUGINS_DIR
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, PluginInfo] = {}

    # ------------------------------------------------------------------
    # Install / Uninstall
    # ------------------------------------------------------------------

    def install(self, source: str) -> PluginInfo:
        """Install a plugin from a git URL or local directory path.

        Returns the installed PluginInfo.
        """
        source_path = Path(source)

        if source_path.is_dir():
            return self._install_from_local(source_path)

        if source.startswith(("http://", "https://", "git@")):
            return self._install_from_git(source)

        raise ValueError(f"Invalid plugin source: {source}. Provide a git URL or local path.")

    def _install_from_git(self, url: str) -> PluginInfo:
        # Derive plugin name from URL
        name = url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        dest = self.plugins_dir / name

        if dest.exists():
            # Update existing
            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(dest),
                capture_output=True,
                check=True,
            )
        else:
            subprocess.run(
                ["git", "clone", "--depth=1", url, str(dest)],
                capture_output=True,
                check=True,
            )

        return self._load_plugin(dest)

    def _install_from_local(self, source: Path) -> PluginInfo:
        manifest = source / MANIFEST_FILE
        if not manifest.exists():
            raise FileNotFoundError(f"No {MANIFEST_FILE} found in {source}")
        info = PluginInfo.from_manifest(manifest)
        dest = self.plugins_dir / info.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        return self._load_plugin(dest)

    def uninstall(self, plugin_name: str) -> bool:
        """Uninstall a plugin by name.  Returns True if removed."""
        dest = self.plugins_dir / plugin_name
        if not dest.exists():
            return False
        shutil.rmtree(dest)
        self._cache.pop(plugin_name, None)
        logger.info("Uninstalled plugin: %s", plugin_name)
        return True

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_installed(self) -> List[PluginInfo]:
        """List all installed plugins."""
        plugins = []
        if not self.plugins_dir.exists():
            return plugins
        for child in sorted(self.plugins_dir.iterdir()):
            if child.is_dir() and (child / MANIFEST_FILE).exists():
                try:
                    plugins.append(self._load_plugin(child))
                except Exception as e:
                    logger.warning("Bad plugin at %s: %s", child, e)
        return plugins

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        """Get a specific plugin by name."""
        if name in self._cache:
            return self._cache[name]
        path = self.plugins_dir / name
        if path.exists() and (path / MANIFEST_FILE).exists():
            return self._load_plugin(path)
        return None

    def _load_plugin(self, path: Path) -> PluginInfo:
        info = PluginInfo.from_manifest(path / MANIFEST_FILE)
        info.path = path
        self._cache[info.name] = info
        return info

    # ------------------------------------------------------------------
    # Skill loading
    # ------------------------------------------------------------------

    def load_all_skills(self) -> List[Dict[str, Any]]:
        """Load skill definitions from all installed plugins.

        Returns raw skill dicts (name, description, prompt_template, etc.)
        that can be passed to the SkillManager.
        """
        from .skills import Skill

        skills: List[Dict[str, Any]] = []
        for plugin in self.list_installed():
            if not plugin.path:
                continue
            for pattern in plugin.skills:
                for skill_file in plugin.path.glob(pattern):
                    try:
                        skill = Skill.from_file(skill_file)
                        skills.append({
                            "skill": skill,
                            "plugin": plugin.name,
                        })
                    except Exception as e:
                        logger.warning("Failed to load skill %s: %s", skill_file, e)
        return skills

    # ------------------------------------------------------------------
    # Hook loading
    # ------------------------------------------------------------------

    def load_all_hooks(self) -> List[Dict[str, Any]]:
        """Load hook definitions from all installed plugins."""
        all_hooks: List[Dict[str, Any]] = []
        for plugin in self.list_installed():
            if not plugin.path:
                continue
            for hook_file_pattern in plugin.hooks:
                for hook_file in plugin.path.glob(hook_file_pattern):
                    try:
                        hooks = json.loads(hook_file.read_text())
                        for h in hooks:
                            h["plugin"] = plugin.name
                            all_hooks.append(h)
                    except Exception as e:
                        logger.warning("Failed to load hooks from %s: %s", hook_file, e)
        return all_hooks

    # ------------------------------------------------------------------
    # MCP config loading
    # ------------------------------------------------------------------

    def load_all_mcp_configs(self) -> List[Dict[str, Any]]:
        """Load MCP server configs from all installed plugins."""
        configs: List[Dict[str, Any]] = []
        for plugin in self.list_installed():
            if not plugin.path:
                continue
            for mcp_pattern in plugin.mcp_configs:
                for mcp_file in plugin.path.glob(mcp_pattern):
                    try:
                        data = json.loads(mcp_file.read_text())
                        servers = data if isinstance(data, list) else data.get("servers", [])
                        for s in servers:
                            s["plugin"] = plugin.name
                            configs.append(s)
                    except Exception as e:
                        logger.warning("Failed to load MCP config from %s: %s", mcp_file, e)
        return configs
