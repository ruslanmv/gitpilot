# gitpilot/topology_registry.py
"""Topology Registry — switchable agent workflow presets for GitPilot.

A topology controls three things simultaneously:
  1. **Visualization** — the ReactFlow node/edge graph shown in Agent Workflow view.
  2. **Routing** — which agent(s) are selected for a given user request.
  3. **Execution** — the runtime pattern (single_task, crew_pipeline, or react_loop).

There are 7 built-in topologies:
  - T1 ``default``       — Fan-out CrewAI routing (the original architecture)
  - T2 ``gitpilot_code``   — Hub-and-spoke ReAct loop with on-demand subagents
  - T3 ``feature_builder`` — 5-agent pipeline: explore > plan > implement > review > PR
  - T4 ``bug_hunter``    — 4-agent pipeline: explore > fix > verify > PR
  - T5 ``code_inspector`` — 2-agent read-only: explore > review
  - T6 ``architect_mode`` — 2-agent read-only: explore > plan
  - T7 ``quick_fix``     — 2-agent fast path: implement > git

This module is **purely additive** — it does not modify any existing routing,
agent, or execution logic.  The existing ``get_flow_definition()`` and
``dispatch_request()`` continue to work unchanged.  New code paths can opt-in
to topology-aware behaviour by importing from this module.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TopologyCategory(str, Enum):
    """Whether a topology is a system-level architecture or a task pipeline."""
    system = "system"
    pipeline = "pipeline"


class ExecutionStyle(str, Enum):
    """How the topology's agents are orchestrated at runtime."""
    single_task = "single_task"        # One agent, one task (T1 default)
    react_loop = "react_loop"          # while(tool_use) main agent loop (T2)
    crew_pipeline = "crew_pipeline"    # Sequential multi-agent CrewAI crew (T3-T7)


class RoutingStrategy(str, Enum):
    """How a request is mapped to agents within a topology."""
    classify_and_dispatch = "classify_and_dispatch"   # Regex/LLM picks one agent
    always_main_agent = "always_main_agent"           # Everything goes to one agent
    fixed_sequence = "fixed_sequence"                 # Ordered chain of agents


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RoutingPolicy:
    """Defines how a topology selects agents for a request."""
    strategy: RoutingStrategy
    primary_agent: Optional[str] = None
    sequence: Optional[List[str]] = None
    classifier_hints: List[str] = field(default_factory=list)


@dataclass
class TopologyMeta:
    """Lightweight summary of a topology (no graph data)."""
    id: str
    name: str
    description: str
    category: TopologyCategory
    icon: str
    agents_used: List[str]
    execution_style: ExecutionStyle


@dataclass
class Topology:
    """Complete topology definition including the flow graph."""
    id: str
    name: str
    description: str
    category: TopologyCategory
    icon: str
    agents_used: List[str]
    execution_style: ExecutionStyle
    routing_policy: RoutingPolicy
    flow_graph: Dict[str, Any]       # {"nodes": [...], "edges": [...]}

    def to_meta(self) -> TopologyMeta:
        return TopologyMeta(
            id=self.id,
            name=self.name,
            description=self.description,
            category=self.category,
            icon=self.icon,
            agents_used=self.agents_used,
            execution_style=self.execution_style,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "icon": self.icon,
            "agents_used": self.agents_used,
            "execution_style": self.execution_style.value,
            "routing_policy": {
                "strategy": self.routing_policy.strategy.value,
                "primary_agent": self.routing_policy.primary_agent,
                "sequence": self.routing_policy.sequence,
                "classifier_hints": self.routing_policy.classifier_hints,
            },
            "flow_graph": self.flow_graph,
        }


# ---------------------------------------------------------------------------
# T1 — Default CrewAI Routing
# ---------------------------------------------------------------------------

_T1_FLOW_GRAPH: Dict[str, Any] = {
    "nodes": [
        {
            "id": "user_request",
            "type": "user",
            "data": {
                "label": "User Request",
                "description": "Incoming task from user",
            },
            "position": {"x": 400, "y": 0},
        },
        {
            "id": "router",
            "type": "router",
            "data": {
                "label": "Task Router",
                "description": "Classifies request type and dispatches to the best agent",
                "model": "regex + heuristics",
            },
            "position": {"x": 400, "y": 100},
        },
        {
            "id": "repo_explorer",
            "type": "agent",
            "data": {
                "label": "Repo Explorer",
                "model": "Haiku 4.5",
                "mode": "read-only",
                "tools": ["Glob", "Grep", "Read", "LS", "Bash(ro)"],
                "description": "Searches and maps codebase structure",
            },
            "position": {"x": 0, "y": 250},
        },
        {
            "id": "planner",
            "type": "agent",
            "data": {
                "label": "Planner",
                "model": "Sonnet 4.5",
                "mode": "read-only",
                "tools": ["Glob", "Grep", "Read", "Bash(ro)"],
                "description": "Designs implementation plans and strategies",
            },
            "position": {"x": 160, "y": 250},
        },
        {
            "id": "code_writer",
            "type": "agent",
            "data": {
                "label": "Code Writer",
                "model": "Sonnet 4.5",
                "mode": "read-write",
                "tools": ["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep"],
                "description": "Implements code changes, creates files, runs tests",
            },
            "position": {"x": 320, "y": 250},
        },
        {
            "id": "reviewer",
            "type": "agent",
            "data": {
                "label": "Reviewer",
                "model": "Sonnet 4.5",
                "mode": "read-only",
                "tools": ["Read", "Grep", "Glob", "Bash(git diff)"],
                "description": "Reviews code for quality, security, and best practices",
            },
            "position": {"x": 480, "y": 250},
        },
        {
            "id": "issue_manager",
            "type": "agent",
            "data": {
                "label": "Issue Manager",
                "model": "Sonnet 4.5",
                "mode": "read-write",
                "tools": ["GitHub API", "Read"],
                "description": "Creates, updates, and manages GitHub issues",
            },
            "position": {"x": 640, "y": 250},
        },
        {
            "id": "pr_manager",
            "type": "agent",
            "data": {
                "label": "PR Manager",
                "model": "Sonnet 4.5",
                "mode": "git-ops",
                "tools": ["Bash(git)", "Bash(gh)", "Read"],
                "description": "Creates branches, commits, pushes, opens PRs",
            },
            "position": {"x": 800, "y": 250},
        },
        {
            "id": "search_agent",
            "type": "agent",
            "data": {
                "label": "Search Agent",
                "model": "Sonnet 4.5",
                "mode": "read-only",
                "tools": ["WebSearch", "WebFetch", "Read"],
                "description": "Researches external documentation and APIs",
            },
            "position": {"x": 160, "y": 400},
        },
        {
            "id": "learning_agent",
            "type": "agent",
            "data": {
                "label": "Learning Agent",
                "model": "Sonnet 4.5",
                "mode": "read-only",
                "tools": ["WebSearch", "WebFetch", "Read"],
                "description": "Explains concepts, generates tutorials",
            },
            "position": {"x": 320, "y": 400},
        },
        {
            "id": "local_editor",
            "type": "agent",
            "data": {
                "label": "Local Editor",
                "model": "Sonnet 4.5",
                "mode": "read-write",
                "tools": ["Read", "Write", "Edit", "Glob"],
                "description": "Edits local files without git operations",
            },
            "position": {"x": 480, "y": 400},
        },
        {
            "id": "terminal_agent",
            "type": "agent",
            "data": {
                "label": "Terminal Agent",
                "model": "Sonnet 4.5",
                "mode": "read-write",
                "tools": ["Bash"],
                "description": "Runs shell commands, manages environment",
            },
            "position": {"x": 640, "y": 400},
        },
        {
            "id": "github_tools",
            "type": "tool_group",
            "data": {
                "label": "GitHub Tools",
                "tools": ["GitHub API", "Bash(gh)"],
                "description": "GitHub REST/GraphQL API and CLI",
            },
            "position": {"x": 0, "y": 400},
        },
        {
            "id": "local_tools",
            "type": "tool_group",
            "data": {
                "label": "Local Tools",
                "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                "description": "Filesystem and shell tools",
            },
            "position": {"x": 800, "y": 400},
        },
        {
            "id": "output",
            "type": "output",
            "data": {
                "label": "Result",
                "description": "Response returned to user",
            },
            "position": {"x": 400, "y": 550},
        },
    ],
    "edges": [
        {"id": "e-user-router",       "source": "user_request",   "target": "router",         "animated": True},
        {"id": "e-router-explorer",   "source": "router",         "target": "repo_explorer",  "label": "explore"},
        {"id": "e-router-planner",    "source": "router",         "target": "planner",        "label": "plan"},
        {"id": "e-router-codewriter", "source": "router",         "target": "code_writer",    "label": "implement"},
        {"id": "e-router-reviewer",   "source": "router",         "target": "reviewer",       "label": "review"},
        {"id": "e-router-issue",      "source": "router",         "target": "issue_manager",  "label": "issue"},
        {"id": "e-router-pr",         "source": "router",         "target": "pr_manager",     "label": "pr"},
        {"id": "e-router-search",     "source": "router",         "target": "search_agent",   "label": "search"},
        {"id": "e-router-learning",   "source": "router",         "target": "learning_agent", "label": "learn"},
        {"id": "e-router-editor",     "source": "router",         "target": "local_editor",   "label": "edit"},
        {"id": "e-router-terminal",   "source": "router",         "target": "terminal_agent", "label": "terminal"},
        {"id": "e-explorer-output",   "source": "repo_explorer",  "target": "output"},
        {"id": "e-planner-output",    "source": "planner",        "target": "output"},
        {"id": "e-codewriter-output", "source": "code_writer",    "target": "output"},
        {"id": "e-reviewer-output",   "source": "reviewer",       "target": "output"},
        {"id": "e-issue-output",      "source": "issue_manager",  "target": "output"},
        {"id": "e-pr-output",         "source": "pr_manager",     "target": "output"},
        {"id": "e-search-output",     "source": "search_agent",   "target": "output"},
        {"id": "e-learning-output",   "source": "learning_agent", "target": "output"},
        {"id": "e-editor-output",     "source": "local_editor",   "target": "output"},
        {"id": "e-terminal-output",   "source": "terminal_agent", "target": "output"},
        {"id": "e-explorer-github",   "source": "repo_explorer",  "target": "github_tools",   "type": "bidirectional", "animated": False},
        {"id": "e-pr-github",         "source": "pr_manager",     "target": "github_tools",   "type": "bidirectional", "animated": False},
        {"id": "e-codewriter-local",  "source": "code_writer",    "target": "local_tools",    "type": "bidirectional", "animated": False},
        {"id": "e-terminal-local",    "source": "terminal_agent", "target": "local_tools",    "type": "bidirectional", "animated": False},
    ],
}

T1_DEFAULT = Topology(
    id="default",
    name="Default (CrewAI Routing)",
    description="Router dispatches to specialized agents based on task type",
    category=TopologyCategory.system,
    icon="\U0001f500",   # shuffle arrows
    agents_used=[
        "repo_explorer", "planner", "code_writer", "reviewer",
        "issue_manager", "pr_manager", "search_agent",
        "learning_agent", "local_editor", "terminal_agent",
    ],
    execution_style=ExecutionStyle.single_task,
    routing_policy=RoutingPolicy(
        strategy=RoutingStrategy.classify_and_dispatch,
        classifier_hints=[],
    ),
    flow_graph=_T1_FLOW_GRAPH,
)

# ---------------------------------------------------------------------------
# T2 — GitPilot Code (ReAct Loop + Subagents)
# ---------------------------------------------------------------------------

_T2_FLOW_GRAPH: Dict[str, Any] = {
    "nodes": [
        {
            "id": "user_request",
            "type": "user",
            "data": {"label": "User Request", "description": "Incoming task or feedback"},
            "position": {"x": 400, "y": 0},
        },
        {
            "id": "main_react_agent",
            "type": "agent",
            "data": {
                "label": "Main ReAct Agent",
                "model": "Opus 4.6",
                "mode": "read-write",
                "tools": ["ALL"],
                "description": "Central agent running in a while(tool_use) loop. Reasons, acts, observes, repeats. Delegates complex subtasks to subagents.",
            },
            "position": {"x": 400, "y": 150},
        },
        {
            "id": "todo_write",
            "type": "tool",
            "data": {
                "label": "TodoWrite",
                "tools": ["TodoWrite"],
                "description": "Creates and tracks step-by-step TODO lists for complex tasks",
            },
            "position": {"x": 150, "y": 150},
        },
        {
            "id": "fs_tools",
            "type": "tool_group",
            "data": {
                "label": "File Tools",
                "tools": ["Read", "Write", "Edit", "MultiEdit"],
                "description": "Read, create, and edit files in the repository",
            },
            "position": {"x": 650, "y": 80},
        },
        {
            "id": "search_tools",
            "type": "tool_group",
            "data": {
                "label": "Search Tools",
                "tools": ["Glob", "Grep", "LS"],
                "description": "Find files by pattern, search contents, list directories",
            },
            "position": {"x": 650, "y": 160},
        },
        {
            "id": "bash_tool",
            "type": "tool",
            "data": {
                "label": "Bash",
                "tools": ["Bash"],
                "description": "Execute shell commands (git, npm, pytest, etc.)",
            },
            "position": {"x": 650, "y": 240},
        },
        {
            "id": "web_tools",
            "type": "tool_group",
            "data": {
                "label": "Web Tools",
                "tools": ["WebSearch", "WebFetch"],
                "description": "Search the web and fetch page contents",
            },
            "position": {"x": 150, "y": 240},
        },
        {
            "id": "subagent_explore",
            "type": "agent",
            "data": {
                "label": "Explore Subagent",
                "model": "Haiku 4.5",
                "mode": "read-only",
                "tools": ["Glob", "Grep", "Read", "LS", "Bash(ro)"],
                "description": "Fast, cheap codebase exploration. Returns concise summary without polluting main context.",
            },
            "position": {"x": 100, "y": 400},
        },
        {
            "id": "subagent_plan",
            "type": "agent",
            "data": {
                "label": "Plan Subagent",
                "model": "Sonnet 4.5",
                "mode": "read-only",
                "tools": ["Glob", "Grep", "Read", "Bash(ro)"],
                "description": "Researches codebase and designs implementation plans before execution.",
            },
            "position": {"x": 270, "y": 400},
        },
        {
            "id": "subagent_review",
            "type": "agent",
            "data": {
                "label": "Review Subagent",
                "model": "Sonnet 4.5",
                "mode": "read-only",
                "tools": ["Read", "Grep", "Glob", "Bash(git diff)"],
                "description": "Reviews code changes for security, quality, and best practices.",
            },
            "position": {"x": 440, "y": 400},
        },
        {
            "id": "subagent_research",
            "type": "agent",
            "data": {
                "label": "Research Subagent",
                "model": "Sonnet 4.5",
                "mode": "read-only",
                "tools": ["WebSearch", "WebFetch", "Read"],
                "description": "Gathers external knowledge from documentation, APIs, and examples.",
            },
            "position": {"x": 610, "y": 400},
        },
        {
            "id": "subagent_gitops",
            "type": "agent",
            "data": {
                "label": "GitOps Subagent",
                "model": "Sonnet 4.5",
                "mode": "git-ops",
                "tools": ["Bash(git)", "Bash(gh)", "Read"],
                "description": "Handles git operations: commit, push, create PR.",
            },
            "position": {"x": 780, "y": 400},
        },
        {
            "id": "output",
            "type": "output",
            "data": {"label": "Result", "description": "Response returned to user (when loop ends)"},
            "position": {"x": 400, "y": 550},
        },
    ],
    "edges": [
        {"id": "e-user-main",          "source": "user_request",      "target": "main_react_agent", "animated": True},
        {"id": "e-main-todo",          "source": "main_react_agent",  "target": "todo_write",       "type": "bidirectional"},
        {"id": "e-main-fs",            "source": "main_react_agent",  "target": "fs_tools",         "type": "bidirectional"},
        {"id": "e-main-search",        "source": "main_react_agent",  "target": "search_tools",     "type": "bidirectional"},
        {"id": "e-main-bash",          "source": "main_react_agent",  "target": "bash_tool",        "type": "bidirectional"},
        {"id": "e-main-web",           "source": "main_react_agent",  "target": "web_tools",        "type": "bidirectional"},
        {"id": "e-main-explore",       "source": "main_react_agent",  "target": "subagent_explore", "label": "Task(explore)"},
        {"id": "e-explore-main",       "source": "subagent_explore",  "target": "main_react_agent", "label": "summary",       "animated": True},
        {"id": "e-main-plan",          "source": "main_react_agent",  "target": "subagent_plan",    "label": "Task(plan)"},
        {"id": "e-plan-main",          "source": "subagent_plan",     "target": "main_react_agent", "label": "plan",          "animated": True},
        {"id": "e-main-review",        "source": "main_react_agent",  "target": "subagent_review",  "label": "Task(review)"},
        {"id": "e-review-main",        "source": "subagent_review",   "target": "main_react_agent", "label": "findings",      "animated": True},
        {"id": "e-main-research",      "source": "main_react_agent",  "target": "subagent_research","label": "Task(research)"},
        {"id": "e-research-main",      "source": "subagent_research", "target": "main_react_agent", "label": "info",          "animated": True},
        {"id": "e-main-gitops",        "source": "main_react_agent",  "target": "subagent_gitops",  "label": "Task(gitops)"},
        {"id": "e-gitops-main",        "source": "subagent_gitops",   "target": "main_react_agent", "label": "PR URL",        "animated": True},
        {"id": "e-main-output",        "source": "main_react_agent",  "target": "output",           "label": "no tool calls = done"},
    ],
}

T2_CLAUDE_CODE = Topology(
    id="gitpilot_code",
    name="GitPilot Code (ReAct + Subagents)",
    description="Single main agent in a ReAct loop with on-demand subagents",
    category=TopologyCategory.system,
    icon="\U0001f9e0",   # brain
    agents_used=[
        "main_react_agent", "subagent_explore", "subagent_plan",
        "subagent_review", "subagent_research", "subagent_gitops",
    ],
    execution_style=ExecutionStyle.react_loop,
    routing_policy=RoutingPolicy(
        strategy=RoutingStrategy.always_main_agent,
        primary_agent="main_react_agent",
        classifier_hints=[],
    ),
    flow_graph=_T2_FLOW_GRAPH,
)

# ---------------------------------------------------------------------------
# T3 — Feature Builder (5-agent pipeline)
# ---------------------------------------------------------------------------

_T3_FLOW_GRAPH: Dict[str, Any] = {
    "nodes": [
        {"id": "user_request", "type": "user",   "data": {"label": "User Request",  "description": "New feature or enhancement request"}, "position": {"x": 400, "y": 0}},
        {"id": "explorer",     "type": "agent",  "data": {"label": "Explorer",  "model": "Haiku 4.5",  "mode": "read-only",  "tools": ["Glob","Grep","Read","LS","Bash(ro)"],                              "description": "Maps codebase structure and discovers relevant files"},          "position": {"x": 100, "y": 150}},
        {"id": "planner",      "type": "agent",  "data": {"label": "Planner",   "model": "Sonnet 4.5", "mode": "read-only",  "tools": ["Glob","Grep","Read","Bash(ro)"],                                   "description": "Designs step-by-step implementation plan"},                       "position": {"x": 250, "y": 150}},
        {"id": "developer",    "type": "agent",  "data": {"label": "Developer", "model": "Sonnet 4.5", "mode": "read-write", "tools": ["Read","Write","Edit","MultiEdit","Bash","Glob","Grep"],             "description": "Implements code changes and runs tests"},                          "position": {"x": 400, "y": 150}},
        {"id": "reviewer",     "type": "agent",  "data": {"label": "Reviewer",  "model": "Sonnet 4.5", "mode": "read-only",  "tools": ["Read","Grep","Glob","Bash(git diff)"],                              "description": "Reviews code for quality, security, and best practices"},          "position": {"x": 550, "y": 150}},
        {"id": "git_agent",    "type": "agent",  "data": {"label": "Git Agent", "model": "Sonnet 4.5", "mode": "git-ops",    "tools": ["Bash(git)","Bash(gh)","Read"],                                     "description": "Creates branch, commits, pushes, opens PR"},                      "position": {"x": 700, "y": 150}},
        {"id": "output",       "type": "output", "data": {"label": "PR Created", "description": "Feature implemented and PR opened"},                                                                       "position": {"x": 700, "y": 300}},
    ],
    "edges": [
        {"id": "e-user-exp",    "source": "user_request", "target": "explorer",  "animated": True},
        {"id": "e-exp-plan",    "source": "explorer",     "target": "planner",   "label": "analysis",  "animated": True},
        {"id": "e-plan-dev",    "source": "planner",      "target": "developer", "label": "plan",      "animated": True},
        {"id": "e-dev-rev",     "source": "developer",    "target": "reviewer",  "label": "changes",   "animated": True},
        {"id": "e-rev-git",     "source": "reviewer",     "target": "git_agent", "label": "approved",  "animated": True},
        {"id": "e-git-output",  "source": "git_agent",    "target": "output",    "label": "PR URL",    "animated": True},
    ],
}

T3_FEATURE_BUILDER = Topology(
    id="feature_builder",
    name="Feature Builder",
    description="Full pipeline: explore > plan > implement > review > PR",
    category=TopologyCategory.pipeline,
    icon="\U0001f680",   # rocket
    agents_used=["explorer", "planner", "developer", "reviewer", "git_agent"],
    execution_style=ExecutionStyle.crew_pipeline,
    routing_policy=RoutingPolicy(
        strategy=RoutingStrategy.fixed_sequence,
        sequence=["explorer", "planner", "developer", "reviewer", "git_agent"],
        classifier_hints=[
            "add", "create", "implement", "build", "new feature",
            "endpoint", "component", "module", "integrate", "migration",
            "refactor", "rewrite", "enhance", "upgrade",
        ],
    ),
    flow_graph=_T3_FLOW_GRAPH,
)

# ---------------------------------------------------------------------------
# T4 — Bug Hunter (4-agent pipeline)
# ---------------------------------------------------------------------------

_T4_FLOW_GRAPH: Dict[str, Any] = {
    "nodes": [
        {"id": "user_request", "type": "user",   "data": {"label": "Bug Report",     "description": "Bug description or error report"}, "position": {"x": 400, "y": 0}},
        {"id": "explorer",     "type": "agent",  "data": {"label": "Explorer",  "model": "Haiku 4.5",  "mode": "read-only",  "tools": ["Glob","Grep","Read","LS","Bash(ro)"],                              "description": "Traces error patterns and locates root cause"},   "position": {"x": 175, "y": 150}},
        {"id": "developer",    "type": "agent",  "data": {"label": "Developer", "model": "Sonnet 4.5", "mode": "read-write", "tools": ["Read","Write","Edit","MultiEdit","Bash","Glob","Grep"],             "description": "Applies targeted fix and runs tests"},             "position": {"x": 350, "y": 150}},
        {"id": "reviewer",     "type": "agent",  "data": {"label": "Reviewer",  "model": "Sonnet 4.5", "mode": "read-only",  "tools": ["Read","Grep","Glob","Bash(git diff)"],                              "description": "Verifies fix and checks for regressions"},         "position": {"x": 525, "y": 150}},
        {"id": "git_agent",    "type": "agent",  "data": {"label": "Git Agent", "model": "Sonnet 4.5", "mode": "git-ops",    "tools": ["Bash(git)","Bash(gh)","Read"],                                     "description": "Commits fix, pushes, opens hotfix PR"},            "position": {"x": 700, "y": 150}},
        {"id": "output",       "type": "output", "data": {"label": "Hotfix PR", "description": "Bug fixed and hotfix PR opened"},                                                                           "position": {"x": 700, "y": 300}},
    ],
    "edges": [
        {"id": "e-user-exp",   "source": "user_request", "target": "explorer",  "animated": True},
        {"id": "e-exp-dev",    "source": "explorer",     "target": "developer", "label": "root cause", "animated": True},
        {"id": "e-dev-rev",    "source": "developer",    "target": "reviewer",  "label": "fix applied","animated": True},
        {"id": "e-rev-git",    "source": "reviewer",     "target": "git_agent", "label": "verified",   "animated": True},
        {"id": "e-git-output", "source": "git_agent",    "target": "output",    "label": "PR URL",     "animated": True},
    ],
}

T4_BUG_HUNTER = Topology(
    id="bug_hunter",
    name="Bug Hunter",
    description="Diagnose > fix > verify > ship hotfix",
    category=TopologyCategory.pipeline,
    icon="\U0001f41b",   # bug
    agents_used=["explorer", "developer", "reviewer", "git_agent"],
    execution_style=ExecutionStyle.crew_pipeline,
    routing_policy=RoutingPolicy(
        strategy=RoutingStrategy.fixed_sequence,
        sequence=["explorer", "developer", "reviewer", "git_agent"],
        classifier_hints=[
            "fix", "bug", "error", "broken", "failing", "crash", "exception",
            "debug", "traceback", "500", "403", "404", "timeout", "leak",
            "regression", "hotfix", "patch", "not working", "tests failing",
        ],
    ),
    flow_graph=_T4_FLOW_GRAPH,
)

# ---------------------------------------------------------------------------
# T5 — Code Inspector (2-agent read-only)
# ---------------------------------------------------------------------------

_T5_FLOW_GRAPH: Dict[str, Any] = {
    "nodes": [
        {"id": "user_request", "type": "user",   "data": {"label": "Review Request",   "description": "Code review or audit request"}, "position": {"x": 300, "y": 0}},
        {"id": "explorer",     "type": "agent",  "data": {"label": "Explorer",  "model": "Haiku 4.5",  "mode": "read-only",  "tools": ["Glob","Grep","Read","LS","Bash(ro)"],         "description": "Discovers modified files and gathers context"},    "position": {"x": 200, "y": 150}},
        {"id": "reviewer",     "type": "agent",  "data": {"label": "Reviewer",  "model": "Sonnet 4.5", "mode": "read-only",  "tools": ["Read","Grep","Glob","Bash(git diff)"],        "description": "Deep analysis: security, quality, performance"},   "position": {"x": 400, "y": 150}},
        {"id": "output",       "type": "output", "data": {"label": "Review Report", "description": "Structured review with severity levels"},                                          "position": {"x": 400, "y": 300}},
    ],
    "edges": [
        {"id": "e-user-exp",    "source": "user_request", "target": "explorer", "animated": True},
        {"id": "e-exp-rev",     "source": "explorer",     "target": "reviewer", "label": "scope + context", "animated": True},
        {"id": "e-rev-output",  "source": "reviewer",     "target": "output",   "label": "report",         "animated": True},
    ],
}

T5_CODE_INSPECTOR = Topology(
    id="code_inspector",
    name="Code Inspector",
    description="Read-only analysis: explore changes > review for issues",
    category=TopologyCategory.pipeline,
    icon="\U0001f50d",   # magnifying glass
    agents_used=["explorer", "reviewer"],
    execution_style=ExecutionStyle.crew_pipeline,
    routing_policy=RoutingPolicy(
        strategy=RoutingStrategy.fixed_sequence,
        sequence=["explorer", "reviewer"],
        classifier_hints=[
            "review", "audit", "security", "inspect", "analyze code",
            "vulnerabilities", "quality", "what changed", "diff",
            "pre-merge", "check quality", "code smell", "coverage",
        ],
    ),
    flow_graph=_T5_FLOW_GRAPH,
)

# ---------------------------------------------------------------------------
# T6 — Architect Mode (2-agent read-only)
# ---------------------------------------------------------------------------

_T6_FLOW_GRAPH: Dict[str, Any] = {
    "nodes": [
        {"id": "user_request", "type": "user",   "data": {"label": "Architecture Question", "description": "Design or strategy question"}, "position": {"x": 300, "y": 0}},
        {"id": "explorer",     "type": "agent",  "data": {"label": "Explorer", "model": "Haiku 4.5",  "mode": "read-only",  "tools": ["Glob","Grep","Read","LS","Bash(ro)"],  "description": "Deep codebase research: structure, deps, patterns"}, "position": {"x": 200, "y": 150}},
        {"id": "planner",      "type": "agent",  "data": {"label": "Planner",  "model": "Sonnet 4.5", "mode": "read-only",  "tools": ["Glob","Grep","Read","Bash(ro)"],       "description": "Synthesizes findings into actionable plan"},          "position": {"x": 400, "y": 150}},
        {"id": "output",       "type": "output", "data": {"label": "Implementation Plan", "description": "Plan awaiting user approval before execution"},                       "position": {"x": 400, "y": 300}},
    ],
    "edges": [
        {"id": "e-user-exp",    "source": "user_request", "target": "explorer", "animated": True},
        {"id": "e-exp-plan",    "source": "explorer",     "target": "planner",  "label": "deep analysis", "animated": True},
        {"id": "e-plan-output", "source": "planner",      "target": "output",   "label": "plan + approval", "animated": True},
    ],
}

T6_ARCHITECT_MODE = Topology(
    id="architect_mode",
    name="Architect Mode",
    description="Research codebase > design plan (no code changes)",
    category=TopologyCategory.pipeline,
    icon="\U0001f4d0",   # triangular ruler
    agents_used=["explorer", "planner"],
    execution_style=ExecutionStyle.crew_pipeline,
    routing_policy=RoutingPolicy(
        strategy=RoutingStrategy.fixed_sequence,
        sequence=["explorer", "planner"],
        classifier_hints=[
            "plan", "design", "architect", "strategy", "how should",
            "approach", "migration", "refactor plan", "proposal",
            "trade-offs", "options", "recommend", "evaluate",
        ],
    ),
    flow_graph=_T6_FLOW_GRAPH,
)

# ---------------------------------------------------------------------------
# T7 — Quick Fix (2-agent fast path)
# ---------------------------------------------------------------------------

_T7_FLOW_GRAPH: Dict[str, Any] = {
    "nodes": [
        {"id": "user_request", "type": "user",   "data": {"label": "Quick Edit",      "description": "Trivial change request"}, "position": {"x": 300, "y": 0}},
        {"id": "developer",    "type": "agent",  "data": {"label": "Developer", "model": "Sonnet 4.5", "mode": "read-write", "tools": ["Read","Write","Edit","MultiEdit","Bash","Glob","Grep"], "description": "Makes targeted change, verifies with quick test"},  "position": {"x": 200, "y": 150}},
        {"id": "git_agent",    "type": "agent",  "data": {"label": "Git Agent", "model": "Sonnet 4.5", "mode": "git-ops",    "tools": ["Bash(git)","Bash(gh)","Read"],                          "description": "Commits and pushes the change"},                    "position": {"x": 400, "y": 150}},
        {"id": "output",       "type": "output", "data": {"label": "Committed & Pushed", "description": "Change committed and pushed"},                                                          "position": {"x": 400, "y": 300}},
    ],
    "edges": [
        {"id": "e-user-dev",    "source": "user_request", "target": "developer", "animated": True},
        {"id": "e-dev-git",     "source": "developer",    "target": "git_agent", "label": "changes ready", "animated": True},
        {"id": "e-git-output",  "source": "git_agent",    "target": "output",    "label": "pushed",        "animated": True},
    ],
}

T7_QUICK_FIX = Topology(
    id="quick_fix",
    name="Quick Fix",
    description="Minimal pipeline: edit > commit > done",
    category=TopologyCategory.pipeline,
    icon="\u26a1",   # lightning bolt
    agents_used=["developer", "git_agent"],
    execution_style=ExecutionStyle.crew_pipeline,
    routing_policy=RoutingPolicy(
        strategy=RoutingStrategy.fixed_sequence,
        sequence=["developer", "git_agent"],
        classifier_hints=[
            "typo", "rename", "update readme", "config", "small change",
            "one-liner", "documentation", "comment", "formatting",
            "version bump", "update dependency", "quick",
        ],
    ),
    flow_graph=_T7_FLOW_GRAPH,
)

# ---------------------------------------------------------------------------
# T8 — Lite Mode (single-agent, optimized for small LLMs < 7B)
# ---------------------------------------------------------------------------

_T8_FLOW_GRAPH: Dict[str, Any] = {
    "nodes": [
        {
            "id": "user_request",
            "type": "user",
            "data": {
                "label": "User Request",
                "description": "Incoming task or question",
            },
            "position": {"x": 300, "y": 0},
        },
        {
            "id": "intent_classifier",
            "type": "router",
            "data": {
                "label": "Intent Classifier",
                "description": "Regex-based instant classification: QUESTION vs ACTION (no LLM call)",
                "model": "regex",
            },
            "position": {"x": 300, "y": 100},
        },
        {
            "id": "pre_fetch",
            "type": "tool_group",
            "data": {
                "label": "Pre-Fetch Context",
                "tools": ["GitHub API"],
                "description": "Fetches file list, README content, and directory structure via API",
            },
            "position": {"x": 100, "y": 200},
        },
        {
            "id": "lite_agent",
            "type": "agent",
            "data": {
                "label": "GitPilot Lite",
                "model": "Any (1.5B+)",
                "mode": "read-write",
                "tools": [],
                "description": "Single LLM call with pre-injected context. Prompt adapts to intent type.",
            },
            "position": {"x": 300, "y": 200},
        },
        {
            "id": "validator",
            "type": "tool",
            "data": {
                "label": "File Validator",
                "tools": ["regex"],
                "description": "Validates MODIFY/DELETE targets exist in repo, strips hallucinated paths",
            },
            "position": {"x": 500, "y": 200},
        },
        {
            "id": "output",
            "type": "output",
            "data": {
                "label": "Result",
                "description": "Answer (question) or validated plan (action)",
            },
            "position": {"x": 300, "y": 320},
        },
    ],
    "edges": [
        {"id": "e-user-classify",      "source": "user_request",      "target": "intent_classifier", "animated": True},
        {"id": "e-classify-prefetch",   "source": "intent_classifier", "target": "pre_fetch",         "label": "always",   "animated": True},
        {"id": "e-prefetch-lite",       "source": "pre_fetch",         "target": "lite_agent",        "label": "context",  "animated": True},
        {"id": "e-lite-validator",      "source": "lite_agent",        "target": "validator",         "label": "action only", "animated": True},
        {"id": "e-lite-output-q",       "source": "lite_agent",        "target": "output",            "label": "question → answer"},
        {"id": "e-validator-output",    "source": "validator",         "target": "output",            "label": "validated plan", "animated": True},
    ],
}

T8_LITE_MODE = Topology(
    id="lite_mode",
    name="Lite Mode (Small LLMs)",
    description="Smart intent detection + single agent + file validation — optimized for models under 7B",
    category=TopologyCategory.system,
    icon="\U0001f4a1",   # light bulb
    agents_used=["lite_agent"],
    execution_style=ExecutionStyle.single_task,
    routing_policy=RoutingPolicy(
        strategy=RoutingStrategy.always_main_agent,
        primary_agent="lite_agent",
        classifier_hints=[],
    ),
    flow_graph=_T8_FLOW_GRAPH,
)


# ---------------------------------------------------------------------------
# T9 — Tool-Augmented ReAct (experimental, opt-in)
# Wires the Phase 1–4 primitives — mode-bound MCP servers, lazy MCP tool
# pruning, sandboxed exec, Anthropic prompt cache, mode tool policies —
# into a Claude-Code-style ReAct loop.  Surfaced in the UI as an
# "experimental" card so users can try it without affecting existing
# topologies.  Disabled by default for the routing layer; users pick it
# explicitly.
# ---------------------------------------------------------------------------

_T9_FLOW_GRAPH: Dict[str, Any] = {
    "nodes": [
        {"id": "user_request", "type": "user",   "data": {"label": "User Request", "description": "Task, refactor, or question"},
         "position": {"x": 400, "y": 0}},
        {"id": "prompt_cache", "type": "system", "data": {"label": "Prompt Cache (Anthropic)", "icon": "🚀",
                                                          "description": "AGENTS.md + rules + tool defs cached as a stable prefix; ~90% input-token savings on multi-turn",
                                                          "feature_flag": "prompt_cache"},
         "position": {"x": 700, "y": 80}},
        {"id": "mode",         "type": "system", "data": {"label": "Active Mode (YAML)", "icon": "🎛️",
                                                          "description": ".gitpilot/modes.yaml — declarative persona + tool policy + bound MCP servers"},
         "position": {"x": 100, "y": 80}},
        {"id": "react_main",   "type": "agent",  "data": {"label": "Main Agent (ReAct)",
                                                          "model": "Sonnet 4.6", "mode": "policy-bound",
                                                          "tools": ["Read","Grep","Glob","mode-allowed MCP tools","Sandboxed Bash"],
                                                          "description": "Single main agent in a Thought/Action/Observation loop, scoped to the active mode's tool policy"},
         "position": {"x": 400, "y": 180}},
        {"id": "tool_pruner",  "type": "system", "data": {"label": "Tool-Def Pruner", "icon": "✂️",
                                                          "description": "Lazy MCP tool defs — only descriptors the active mode allows are emitted to the model",
                                                          "feature_flag": "lazy_tool_defs"},
         "position": {"x": 200, "y": 300}},
        {"id": "mcp_servers",  "type": "agent",  "data": {"label": "Mode-bound MCP Servers", "icon": "🧩",
                                                          "tools": ["postgres.*","github.search_code","milvus.query","custom servers"],
                                                          "description": "MCP servers declared inline in the mode; start/stop with the mode"},
         "position": {"x": 400, "y": 320}},
        {"id": "sandbox",      "type": "system", "data": {"label": "Sandbox (subprocess / matrixlab)", "icon": "🛡️",
                                                          "description": "Shell execution jailed to the workspace, secrets stripped; switch to containerised matrixlab via env var"},
         "position": {"x": 600, "y": 300}},
        {"id": "context_cache","type": "system", "data": {"label": "Context-Pack LRU", "icon": "🗂️",
                                                          "description": "Memoised by workspace + mode + file mtimes; instant hits across turns",
                                                          "feature_flag": "context_cache"},
         "position": {"x": 700, "y": 220}},
        {"id": "approval",     "type": "system", "data": {"label": "Approval Batcher", "icon": "✅",
                                                          "description": "Batches consecutive read-only tool calls into a single user prompt"},
         "position": {"x": 400, "y": 460}},
        {"id": "output",       "type": "output", "data": {"label": "Answer / Diff", "description": "Streamed via SSE (/chat/stream) when stream_v2=1"},
         "position": {"x": 400, "y": 580}},
    ],
    "edges": [
        {"id": "e-user-main",    "source": "user_request", "target": "react_main",   "animated": True},
        {"id": "e-mode-pruner",  "source": "mode",         "target": "tool_pruner",  "label": "tool policy", "animated": True},
        {"id": "e-pruner-main",  "source": "tool_pruner",  "target": "react_main",   "label": "pruned defs",  "animated": True},
        {"id": "e-cache-main",   "source": "prompt_cache", "target": "react_main",   "label": "stable prefix", "animated": True},
        {"id": "e-ctx-main",     "source": "context_cache","target": "react_main",   "label": "context pack",  "animated": True},
        {"id": "e-main-mcp",     "source": "react_main",   "target": "mcp_servers",  "label": "tool call",   "animated": True},
        {"id": "e-main-sandbox", "source": "react_main",   "target": "sandbox",      "label": "shell",       "animated": True},
        {"id": "e-mcp-main",     "source": "mcp_servers",  "target": "react_main",   "label": "observation", "animated": True},
        {"id": "e-sandbox-main", "source": "sandbox",      "target": "react_main",   "label": "stdout",      "animated": True},
        {"id": "e-main-approval","source": "react_main",   "target": "approval",     "label": "edit/exec",   "animated": True},
        {"id": "e-approval-out", "source": "approval",     "target": "output",       "label": "approved",    "animated": True},
    ],
}

T9_TOOL_AUGMENTED_REACT = Topology(
    id="tool_augmented_react",
    name="Tool-Augmented ReAct (experimental)",
    description=(
        "ReAct loop wired through the Phase 1–4 primitives: mode-bound MCP "
        "servers, lazy MCP tool pruning, prompt cache, context LRU, "
        "sandboxed shell, approval batcher."
    ),
    category=TopologyCategory.system,
    icon="\U0001f9ea",   # test tube — flags it as experimental
    agents_used=[
        "main_react_agent",
        "mode_resolver",
        "tool_def_pruner",
        "mcp_servers",
        "sandbox_runner",
        "approval_batcher",
    ],
    execution_style=ExecutionStyle.react_loop,
    routing_policy=RoutingPolicy(
        strategy=RoutingStrategy.always_main_agent,
        primary_agent="main_react_agent",
        classifier_hints=[],
    ),
    flow_graph=_T9_FLOW_GRAPH,
)


# ===========================================================================
# Registry singleton
# ===========================================================================

TOPOLOGY_REGISTRY: Dict[str, Topology] = {
    t.id: t
    for t in [
        T1_DEFAULT,
        T2_CLAUDE_CODE,
        T3_FEATURE_BUILDER,
        T4_BUG_HUNTER,
        T5_CODE_INSPECTOR,
        T6_ARCHITECT_MODE,
        T7_QUICK_FIX,
        T8_LITE_MODE,
        T9_TOOL_AUGMENTED_REACT,
    ]
}

DEFAULT_TOPOLOGY_ID = "default"


def list_topologies() -> List[Dict[str, Any]]:
    """Return lightweight summaries of all registered topologies."""
    result = []
    for t in TOPOLOGY_REGISTRY.values():
        meta = t.to_meta()
        result.append({
            "id": meta.id,
            "name": meta.name,
            "description": meta.description,
            "category": meta.category.value,
            "icon": meta.icon,
            "agents_used": meta.agents_used,
            "execution_style": meta.execution_style.value,
        })
    return result


def get_topology(topology_id: str) -> Optional[Topology]:
    """Look up a topology by ID.  Returns None if not found."""
    return TOPOLOGY_REGISTRY.get(topology_id)


def get_topology_graph(topology_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the flow graph for a given topology.

    If *topology_id* is ``None`` or unrecognised, falls back to ``"default"``.
    The returned dict is the same shape as the legacy ``get_flow_definition()``
    output so the frontend ``FlowViewer`` can consume it without changes.
    """
    tid = topology_id or DEFAULT_TOPOLOGY_ID
    topo = TOPOLOGY_REGISTRY.get(tid)
    if topo is None:
        topo = TOPOLOGY_REGISTRY[DEFAULT_TOPOLOGY_ID]

    # Build the response — include topology metadata alongside the graph
    # so the frontend can display the name/description even without a
    # separate metadata call.
    graph = topo.flow_graph.copy()

    # For backward compat with the legacy FlowViewer which expects flat
    # ``nodes`` with ``label``/``type``/``description`` keys, we normalise
    # the new richer node format.  New FlowViewer uses the ``data`` sub-key
    # directly but old code reads top-level keys.
    legacy_nodes = []
    for n in graph.get("nodes", []):
        ln = dict(n)  # shallow copy
        # Hoist data.label / data.description to top level for legacy compat
        d = n.get("data", {})
        ln.setdefault("label", d.get("label", n["id"]))
        ln.setdefault("description", d.get("description", ""))
        legacy_nodes.append(ln)

    return {
        "topology_id": topo.id,
        "topology_name": topo.name,
        "topology_icon": topo.icon,
        "topology_description": topo.description,
        "execution_style": topo.execution_style.value,
        "nodes": legacy_nodes,
        "edges": graph.get("edges", []),
    }


# ===========================================================================
# Topology classifier — keyword-based auto-detection
# ===========================================================================

@dataclass
class ClassificationResult:
    """Result of classifying a user message against topology hints."""
    recommended: str
    confidence: float
    alternatives: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recommended_topology": self.recommended,
            "confidence": round(self.confidence, 3),
            "alternatives": self.alternatives,
        }


def classify_message(message: str) -> ClassificationResult:
    """Classify a user message and recommend the best topology.

    Uses a two-pass approach:
      1. Keyword hit scoring against each topology's ``classifier_hints``.
      2. Tie-breaking heuristics (message length, question marks, etc.).

    System topologies (T1, T2) act as fallbacks — they are only recommended
    when no pipeline topology scores above the confidence threshold.
    """
    msg_lower = message.lower().strip()
    scores: List[Tuple[str, float]] = []

    for tid, topo in TOPOLOGY_REGISTRY.items():
        hints = topo.routing_policy.classifier_hints
        if not hints:
            continue  # system topologies have no hints

        hit_count = 0
        for hint in hints:
            # Match whole-word (ish) — allow the hint to appear as a substring
            # but prefer word boundaries.
            pattern = r"(?:^|\W)" + re.escape(hint) + r"(?:\W|$)"
            if re.search(pattern, msg_lower):
                hit_count += 1

        if hit_count > 0:
            # Normalise: score = hits / total_hints, capped at 1.0
            raw_score = min(hit_count / max(len(hints), 1), 1.0)
            # Boost by number of distinct hits so more-specific matches win
            boosted = raw_score * (1 + 0.1 * min(hit_count, 5))
            scores.append((tid, min(boosted, 1.0)))

    # Sort descending by score
    scores.sort(key=lambda x: x[1], reverse=True)

    if not scores or scores[0][1] < 0.05:
        # Nothing matched — fall back to default
        return ClassificationResult(
            recommended="default",
            confidence=0.5,
            alternatives=[
                {"id": "gitpilot_code", "confidence": 0.45},
            ],
        )

    best_id, best_score = scores[0]
    alternatives = [
        {"id": tid, "confidence": round(sc, 3)}
        for tid, sc in scores[1:4]
    ]

    # Always include the two system topologies as alternatives if not already present
    present_ids = {best_id} | {a["id"] for a in alternatives}
    for sys_id, sys_conf in [("default", 0.3), ("gitpilot_code", 0.35)]:
        if sys_id not in present_ids:
            alternatives.append({"id": sys_id, "confidence": sys_conf})

    return ClassificationResult(
        recommended=best_id,
        confidence=round(best_score, 3),
        alternatives=alternatives,
    )


# ===========================================================================
# User preference persistence
# ===========================================================================

_TOPOLOGY_PREF_KEY = "active_topology"


def get_saved_topology_preference() -> Optional[str]:
    """Read the user's saved topology preference from settings file."""
    import json
    from .settings import CONFIG_DIR

    pref_file = CONFIG_DIR / "topology_pref.json"
    if pref_file.exists():
        try:
            data = json.loads(pref_file.read_text("utf-8"))
            value = data.get(_TOPOLOGY_PREF_KEY)
            if value in TOPOLOGY_REGISTRY:
                return value
        except Exception:
            pass
    return None

def save_topology_preference(topology_id: str) -> None:
    """Persist the user's selected topology preference."""
    import json
    from .settings import CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    pref_file = CONFIG_DIR / "topology_pref.json"
    pref_file.write_text(
        json.dumps({_TOPOLOGY_PREF_KEY: topology_id}, indent=2),
        "utf-8",
    )
