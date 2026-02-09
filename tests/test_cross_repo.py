"""Tests for gitpilot.cross_repo module."""
from __future__ import annotations

import json

import pytest

from gitpilot.cross_repo import (
    CrossRepoAnalyzer,
    Dependency,
    DependencyGraph,
    ImpactReport,
    MigrationPlan,
)


# ---------------------------------------------------------------------------
# Dependency dataclass
# ---------------------------------------------------------------------------

class TestDependency:
    def test_default_fields(self):
        dep = Dependency(source="a/repo", target="lodash")
        assert dep.source == "a/repo"
        assert dep.target == "lodash"
        assert dep.dep_type == "runtime"
        assert dep.version == ""
        assert dep.ecosystem == ""

    def test_to_dict(self):
        dep = Dependency(
            source="a/repo", target="flask",
            dep_type="dev", version=">=3.0", ecosystem="pip",
        )
        d = dep.to_dict()
        assert d["source"] == "a/repo"
        assert d["target"] == "flask"
        assert d["dep_type"] == "dev"
        assert d["version"] == ">=3.0"
        assert d["ecosystem"] == "pip"


# ---------------------------------------------------------------------------
# DependencyGraph
# ---------------------------------------------------------------------------

class TestDependencyGraph:
    def test_empty_graph(self):
        g = DependencyGraph()
        assert g.node_count == 0
        assert g.edge_count == 0

    def test_node_and_edge_count(self):
        g = DependencyGraph(dependencies=[
            Dependency(source="A", target="B"),
            Dependency(source="A", target="C"),
            Dependency(source="B", target="C"),
        ])
        assert g.node_count == 3  # A, B, C
        assert g.edge_count == 3

    def test_to_dict(self):
        g = DependencyGraph(
            repos=["r1"], ecosystems=["npm"],
            dependencies=[Dependency(source="r1", target="lodash")],
        )
        d = g.to_dict()
        assert d["repos"] == ["r1"]
        assert d["ecosystems"] == ["npm"]
        assert d["node_count"] == 2
        assert d["edge_count"] == 1
        assert len(d["dependencies"]) == 1


# ---------------------------------------------------------------------------
# ImpactReport / MigrationPlan
# ---------------------------------------------------------------------------

class TestImpactReport:
    def test_to_dict(self):
        report = ImpactReport(
            source_repo="lib",
            change_description="Breaking change",
            affected_repos=["svc1", "svc2"],
            risk_level="high",
            details=["svc1 depends on lib"],
        )
        d = report.to_dict()
        assert d["source_repo"] == "lib"
        assert d["risk_level"] == "high"
        assert len(d["affected_repos"]) == 2


class TestMigrationPlan:
    def test_to_dict(self):
        plan = MigrationPlan(
            target_pattern="ESLint 9",
            repos=["r1", "r2"],
            steps=[{"order": "1", "repo": "r1", "action": "migrate"}],
            estimated_effort="low",
        )
        d = plan.to_dict()
        assert d["target_pattern"] == "ESLint 9"
        assert d["estimated_effort"] == "low"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

class TestParseNpm:
    def test_runtime_and_dev_deps(self):
        analyzer = CrossRepoAnalyzer()
        content = json.dumps({
            "dependencies": {"express": "^4.18.0", "lodash": "^4.17"},
            "devDependencies": {"jest": "^29.0"},
        })
        deps = analyzer._parse_npm(content)
        assert len(deps) == 3
        names = {d.target for d in deps}
        assert names == {"express", "lodash", "jest"}
        runtime = [d for d in deps if d.dep_type == "runtime"]
        dev = [d for d in deps if d.dep_type == "dev"]
        assert len(runtime) == 2
        assert len(dev) == 1

    def test_peer_deps(self):
        analyzer = CrossRepoAnalyzer()
        content = json.dumps({"peerDependencies": {"react": "^18"}})
        deps = analyzer._parse_npm(content)
        assert len(deps) == 1
        assert deps[0].dep_type == "peer"

    def test_invalid_json(self):
        analyzer = CrossRepoAnalyzer()
        deps = analyzer._parse_npm("{broken")
        assert deps == []


class TestParsePip:
    def test_basic_requirements(self):
        analyzer = CrossRepoAnalyzer()
        content = "requests>=2.28\nflask>=3.0\n"
        deps = analyzer._parse_pip(content)
        assert len(deps) == 2
        names = {d.target for d in deps}
        assert "requests" in names
        assert "flask" in names

    def test_ignores_comments_and_flags(self):
        analyzer = CrossRepoAnalyzer()
        content = "# comment\n-r base.txt\ndjango>=4.0\n"
        deps = analyzer._parse_pip(content)
        assert len(deps) == 1
        assert deps[0].target == "django"

    def test_package_without_version(self):
        analyzer = CrossRepoAnalyzer()
        content = "numpy\n"
        deps = analyzer._parse_pip(content)
        assert len(deps) == 1
        assert deps[0].target == "numpy"
        assert deps[0].version == ""


class TestParsePyproject:
    def test_basic_pyproject(self):
        analyzer = CrossRepoAnalyzer()
        content = """
[project]
dependencies = [
    "flask>=3.0",
    "sqlalchemy>=2.0",
]
"""
        deps = analyzer._parse_pyproject(content)
        assert len(deps) == 2
        names = {d.target for d in deps}
        assert "flask" in names
        assert "sqlalchemy" in names

    def test_empty_pyproject(self):
        analyzer = CrossRepoAnalyzer()
        content = "[project]\nname = 'myproject'\n"
        deps = analyzer._parse_pyproject(content)
        assert deps == []


class TestParseGomod:
    def test_basic_gomod(self):
        analyzer = CrossRepoAnalyzer()
        content = """module github.com/myorg/myapp

go 1.21

require (
\tgithub.com/gin-gonic/gin v1.9.1
\tgithub.com/lib/pq v1.10.9
)
"""
        deps = analyzer._parse_gomod(content)
        assert len(deps) == 2
        targets = {d.target for d in deps}
        assert "github.com/gin-gonic/gin" in targets

    def test_ignores_comments(self):
        analyzer = CrossRepoAnalyzer()
        content = "\t// indirect dep\n\tgithub.com/pkg/errors v0.9.1\n"
        deps = analyzer._parse_gomod(content)
        assert len(deps) == 1


# ---------------------------------------------------------------------------
# CrossRepoAnalyzer.analyze_dependencies_from_files
# ---------------------------------------------------------------------------

class TestAnalyzeDependencies:
    def test_multi_repo_graph(self):
        analyzer = CrossRepoAnalyzer()
        repo_files = {
            "org/frontend": {"package.json": json.dumps({"dependencies": {"react": "^18"}})},
            "org/backend": {"requirements.txt": "flask>=3.0\nredis>=4.0"},
        }
        graph = analyzer.analyze_dependencies_from_files(repo_files)
        assert set(graph.repos) == {"org/frontend", "org/backend"}
        assert "npm" in graph.ecosystems
        assert "pip" in graph.ecosystems
        assert graph.edge_count == 3  # react + flask + redis

    def test_unknown_file_ignored(self):
        analyzer = CrossRepoAnalyzer()
        graph = analyzer.analyze_dependencies_from_files({
            "repo": {"unknown.lock": "some content"},
        })
        assert graph.edge_count == 0

    def test_source_set_on_deps(self):
        analyzer = CrossRepoAnalyzer()
        graph = analyzer.analyze_dependencies_from_files({
            "myrepo": {"requirements.txt": "click>=8.0"},
        })
        assert graph.dependencies[0].source == "myrepo"


# ---------------------------------------------------------------------------
# CrossRepoAnalyzer.impact_analysis
# ---------------------------------------------------------------------------

class TestImpactAnalysis:
    def _build_graph(self):
        """Build a test graph: svc1->lib, svc2->lib, svc3->svc1."""
        return DependencyGraph(
            repos=["lib", "svc1", "svc2", "svc3"],
            dependencies=[
                Dependency(source="svc1", target="lib"),
                Dependency(source="svc2", target="lib"),
                Dependency(source="svc3", target="svc1"),
            ],
        )

    def test_direct_dependents(self):
        analyzer = CrossRepoAnalyzer()
        graph = self._build_graph()
        report = analyzer.impact_analysis(graph, "lib", "API v2 change")
        assert "svc1" in report.affected_repos
        assert "svc2" in report.affected_repos

    def test_transitive_dependents(self):
        analyzer = CrossRepoAnalyzer()
        graph = self._build_graph()
        report = analyzer.impact_analysis(graph, "lib", "Breaking")
        # svc3 depends on svc1, which depends on lib
        assert "svc3" in report.affected_repos

    def test_risk_level_low(self):
        analyzer = CrossRepoAnalyzer()
        graph = DependencyGraph(dependencies=[])
        report = analyzer.impact_analysis(graph, "isolated", "change")
        assert report.risk_level == "low"

    def test_risk_level_medium(self):
        analyzer = CrossRepoAnalyzer()
        graph = DependencyGraph(dependencies=[
            Dependency(source="a", target="lib"),
            Dependency(source="b", target="lib"),
        ])
        report = analyzer.impact_analysis(graph, "lib", "change")
        assert report.risk_level == "medium"

    def test_risk_level_high(self):
        analyzer = CrossRepoAnalyzer()
        deps = [Dependency(source=f"svc{i}", target="lib") for i in range(5)]
        graph = DependencyGraph(dependencies=deps)
        report = analyzer.impact_analysis(graph, "lib", "change")
        assert report.risk_level == "high"

    def test_risk_level_critical(self):
        analyzer = CrossRepoAnalyzer()
        deps = [Dependency(source=f"svc{i}", target="lib") for i in range(15)]
        graph = DependencyGraph(dependencies=deps)
        report = analyzer.impact_analysis(graph, "lib", "change")
        assert report.risk_level == "critical"


# ---------------------------------------------------------------------------
# CrossRepoAnalyzer.detect_shared_conventions
# ---------------------------------------------------------------------------

class TestDetectSharedConventions:
    def test_shared_configs(self):
        analyzer = CrossRepoAnalyzer()
        repo_files = {
            "repo1": {".eslintrc.json": "{}", "tsconfig.json": "{}"},
            "repo2": {".eslintrc.json": "{}", "Makefile": "all:"},
            "repo3": {"Makefile": "all:", "Dockerfile": "FROM node"},
        }
        conventions = analyzer.detect_shared_conventions(repo_files)
        assert "repo1" in conventions.get(".eslintrc", []) or "repo1" in conventions.get(".eslintrc.json", [])
        assert "Makefile" in conventions
        assert len(conventions["Makefile"]) == 2

    def test_no_shared(self):
        analyzer = CrossRepoAnalyzer()
        repo_files = {
            "repo1": {"random.txt": "data"},
        }
        conventions = analyzer.detect_shared_conventions(repo_files)
        assert conventions == {}


# ---------------------------------------------------------------------------
# CrossRepoAnalyzer.suggest_migration
# ---------------------------------------------------------------------------

class TestSuggestMigration:
    def test_basic_plan(self):
        analyzer = CrossRepoAnalyzer()
        plan = analyzer.suggest_migration(["r1", "r2"], "ESLint 9")
        assert plan.target_pattern == "ESLint 9"
        assert plan.repos == ["r1", "r2"]
        assert len(plan.steps) == 2
        assert plan.steps[0]["order"] == "1"
        assert plan.estimated_effort == "low"

    def test_medium_effort(self):
        analyzer = CrossRepoAnalyzer()
        repos = [f"repo{i}" for i in range(7)]
        plan = analyzer.suggest_migration(repos, "pattern")
        assert plan.estimated_effort == "medium"

    def test_high_effort(self):
        analyzer = CrossRepoAnalyzer()
        repos = [f"repo{i}" for i in range(15)]
        plan = analyzer.suggest_migration(repos, "pattern")
        assert plan.estimated_effort == "high"
