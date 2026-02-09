# gitpilot/cross_repo.py
"""Cross-repository intelligence — dependency graphs and impact analysis.

Analyses patterns across multiple repositories to provide:
- Dependency graphs (repo A depends on repo B)
- Impact analysis (change in lib affects services)
- Shared convention detection
- Migration planning across repos

Draws on the concept of *software ecosystems analysis* from research
on large-scale dependency management (Decan et al., 2019).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Common dependency file patterns
_DEP_FILES = {
    "package.json": "npm",
    "requirements.txt": "pip",
    "Pipfile": "pipenv",
    "pyproject.toml": "pyproject",
    "Cargo.toml": "cargo",
    "go.mod": "go",
    "Gemfile": "bundler",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "composer.json": "composer",
}


@dataclass
class Dependency:
    """A dependency relationship between two entities."""

    source: str  # e.g., "owner/repo-a"
    target: str  # e.g., "owner/repo-b" or "package-name"
    dep_type: str = "runtime"  # runtime | dev | peer | optional
    version: str = ""
    ecosystem: str = ""  # npm, pip, cargo, etc.

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "dep_type": self.dep_type,
            "version": self.version,
            "ecosystem": self.ecosystem,
        }


@dataclass
class DependencyGraph:
    """A graph of dependencies across repositories."""

    repos: List[str] = field(default_factory=list)
    dependencies: List[Dependency] = field(default_factory=list)
    ecosystems: List[str] = field(default_factory=list)

    @property
    def node_count(self) -> int:
        nodes: Set[str] = set()
        for d in self.dependencies:
            nodes.add(d.source)
            nodes.add(d.target)
        return len(nodes)

    @property
    def edge_count(self) -> int:
        return len(self.dependencies)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repos": self.repos,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "ecosystems": self.ecosystems,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
        }


@dataclass
class ImpactReport:
    """Impact analysis report for a change in a repository."""

    source_repo: str
    change_description: str
    affected_repos: List[str] = field(default_factory=list)
    risk_level: str = "low"  # low | medium | high | critical
    details: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_repo": self.source_repo,
            "change_description": self.change_description,
            "affected_repos": self.affected_repos,
            "risk_level": self.risk_level,
            "details": self.details,
        }


@dataclass
class MigrationPlan:
    """Plan for migrating a pattern across repositories."""

    target_pattern: str
    repos: List[str] = field(default_factory=list)
    steps: List[Dict[str, str]] = field(default_factory=list)
    estimated_effort: str = "unknown"  # low | medium | high

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_pattern": self.target_pattern,
            "repos": self.repos,
            "steps": self.steps,
            "estimated_effort": self.estimated_effort,
        }


class CrossRepoAnalyzer:
    """Analyze patterns and dependencies across multiple repositories.

    Usage::

        analyzer = CrossRepoAnalyzer()
        graph = analyzer.analyze_dependencies_from_files({
            "owner/repo-a": {"package.json": '{"dependencies": {"lodash": "^4"}}'},
            "owner/repo-b": {"requirements.txt": "requests>=2.28\\nflask>=3.0"},
        })
        impact = analyzer.impact_analysis(graph, "owner/repo-a", "Breaking change in API v2")
    """

    def analyze_dependencies_from_files(
        self,
        repo_files: Dict[str, Dict[str, str]],
    ) -> DependencyGraph:
        """Build a dependency graph from dependency files.

        Args:
            repo_files: Mapping of repo name → {filename: content}.
        """
        graph = DependencyGraph(repos=list(repo_files.keys()))
        ecosystems: Set[str] = set()

        for repo, files in repo_files.items():
            for filename, content in files.items():
                ecosystem = _DEP_FILES.get(filename)
                if not ecosystem:
                    continue
                ecosystems.add(ecosystem)
                deps = self._parse_dependencies(filename, content, ecosystem)
                for dep in deps:
                    dep.source = repo
                    graph.dependencies.append(dep)

        graph.ecosystems = sorted(ecosystems)
        return graph

    def impact_analysis(
        self,
        graph: DependencyGraph,
        source_repo: str,
        change_description: str,
    ) -> ImpactReport:
        """Analyze the impact of a change in one repo on others.

        Walks the dependency graph to find repos that depend (directly
        or transitively) on the source repo.
        """
        # Build reverse adjacency: target → [sources]
        dependents: Dict[str, List[str]] = {}
        for dep in graph.dependencies:
            dependents.setdefault(dep.target, []).append(dep.source)

        # BFS from source_repo
        affected: Set[str] = set()
        queue = [source_repo]
        visited: Set[str] = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for dependent in dependents.get(current, []):
                if dependent != source_repo:
                    affected.add(dependent)
                    queue.append(dependent)

        # Risk assessment
        if len(affected) == 0:
            risk = "low"
        elif len(affected) <= 3:
            risk = "medium"
        elif len(affected) <= 10:
            risk = "high"
        else:
            risk = "critical"

        details = []
        for repo in sorted(affected):
            deps_on_source = [
                d for d in graph.dependencies
                if d.source == repo and d.target == source_repo
            ]
            for d in deps_on_source:
                details.append(f"{repo} depends on {source_repo} ({d.dep_type}, {d.version})")

        return ImpactReport(
            source_repo=source_repo,
            change_description=change_description,
            affected_repos=sorted(affected),
            risk_level=risk,
            details=details,
        )

    def detect_shared_conventions(
        self,
        repo_files: Dict[str, Dict[str, str]],
    ) -> Dict[str, List[str]]:
        """Detect shared conventions across repos.

        Looks for common config files, linters, formatters, CI configs, etc.
        """
        conventions: Dict[str, List[str]] = {}

        convention_files = [
            ".eslintrc", ".eslintrc.json", ".prettierrc",
            "ruff.toml", "pyproject.toml", ".flake8",
            ".github/workflows", "Makefile", "Dockerfile",
            "tsconfig.json", "jest.config",
        ]

        for repo, files in repo_files.items():
            for cf in convention_files:
                for filename in files:
                    if cf in filename:
                        conventions.setdefault(cf, []).append(repo)

        return conventions

    def suggest_migration(
        self,
        repos: List[str],
        target_pattern: str,
    ) -> MigrationPlan:
        """Suggest a migration plan for applying a pattern across repos."""
        steps = []
        for i, repo in enumerate(repos):
            steps.append({
                "order": str(i + 1),
                "repo": repo,
                "action": f"Apply {target_pattern} to {repo}",
                "status": "pending",
            })

        effort = "low" if len(repos) <= 3 else ("medium" if len(repos) <= 10 else "high")

        return MigrationPlan(
            target_pattern=target_pattern,
            repos=repos,
            steps=steps,
            estimated_effort=effort,
        )

    # ------------------------------------------------------------------
    # Dependency parsers
    # ------------------------------------------------------------------

    def _parse_dependencies(
        self, filename: str, content: str, ecosystem: str,
    ) -> List[Dependency]:
        if ecosystem == "npm":
            return self._parse_npm(content)
        if ecosystem in ("pip", "pipenv"):
            return self._parse_pip(content)
        if ecosystem == "pyproject":
            return self._parse_pyproject(content)
        if ecosystem == "go":
            return self._parse_gomod(content)
        return []

    def _parse_npm(self, content: str) -> List[Dependency]:
        deps = []
        try:
            data = json.loads(content)
            for section, dep_type in [
                ("dependencies", "runtime"),
                ("devDependencies", "dev"),
                ("peerDependencies", "peer"),
            ]:
                for name, version in data.get(section, {}).items():
                    deps.append(Dependency(
                        source="", target=name,
                        dep_type=dep_type, version=version, ecosystem="npm",
                    ))
        except json.JSONDecodeError:
            pass
        return deps

    def _parse_pip(self, content: str) -> List[Dependency]:
        deps = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            m = re.match(r"([a-zA-Z0-9_-]+)\s*([><=!~]+.+)?", line)
            if m:
                deps.append(Dependency(
                    source="", target=m.group(1),
                    dep_type="runtime", version=m.group(2) or "", ecosystem="pip",
                ))
        return deps

    def _parse_pyproject(self, content: str) -> List[Dependency]:
        deps = []
        in_deps = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("dependencies"):
                in_deps = True
                continue
            if in_deps:
                if stripped.startswith("]"):
                    in_deps = False
                    continue
                m = re.match(r'"([a-zA-Z0-9_-]+)', stripped)
                if m:
                    deps.append(Dependency(
                        source="", target=m.group(1),
                        dep_type="runtime", ecosystem="pyproject",
                    ))
        return deps

    def _parse_gomod(self, content: str) -> List[Dependency]:
        deps = []
        for line in content.split("\n"):
            m = re.match(r"\s+(\S+)\s+(\S+)", line)
            if m and not line.strip().startswith("//"):
                deps.append(Dependency(
                    source="", target=m.group(1),
                    dep_type="runtime", version=m.group(2), ecosystem="go",
                ))
        return deps
