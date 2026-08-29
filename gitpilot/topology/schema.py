# gitpilot/topology/schema.py
"""Topology schema v2 — Batch V4-G1.

A v1 topology is three hand-written things: a name, a routing rule, and a
ReactFlow graph drawn by hand. That last part is why T9's graph names a
"tool-def pruner" and an "approval batcher" as if they were agents — the picture
was the deliverable, and nothing executed it.

A v2 topology is a **policy document**. It says what engine runs, which dialect,
which capabilities are granted, who approves what, whether the run must prove
itself, and where its limits are. The picture is *derived* from the document, so
it cannot drift from the behaviour: add ``git.commit: ask`` and a Git node appears
in the Agent Workflow view because the capability is really there.

Two rules in here are safety rules rather than schema rules, and they are the
reason parsing is a function with tests rather than a ``yaml.safe_load``:

**A topology may only tighten approvals.** ``approval.mode`` runs through
:func:`gitpilot.agent.policy.restrict_mode` against the session's mode — the same
asymmetry the request body already obeys (§8.1, F11). A document asking for
``auto`` in a ``normal`` session gets ``normal``.

**A capability grant is a grant, not a suggestion.** The raw mapping is handed to
:class:`~gitpilot.agent.policy.CapabilityMask`, which closes by default the
moment a document names anything: listing capabilities is how a topology says
"only these". A misspelled capability therefore *withholds* a tool rather than
silently granting it, which is the direction a policy file must fail in.

The document shape is §15.1 of ``docs/upgrade-plan-v4-agentic-runtime.md``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..agent.policy import CapabilityMask
    from ..permissions import PermissionMode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# v1 vocabulary (kept — the picker, the API and the VS Code client speak it)
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
    #: The v4 engine.  Distinct from ``react_loop``, which was only ever a
    #: promise in a flow graph: this one names a topology whose document is
    #: executed by :class:`gitpilot.agent.loop.AgentLoop`.
    agentic_loop = "agentic_loop"


class RoutingStrategy(str, Enum):
    """How a request is mapped to agents within a topology."""

    classify_and_dispatch = "classify_and_dispatch"   # Regex/LLM picks one agent
    always_main_agent = "always_main_agent"           # Everything goes to one agent
    fixed_sequence = "fixed_sequence"                 # Ordered chain of agents


# ---------------------------------------------------------------------------
# v2 vocabulary
# ---------------------------------------------------------------------------


class Engine(str, Enum):
    """What actually runs the topology."""

    agentic_loop = "agentic_loop"
    sequential_pipeline = "sequential_pipeline"
    single_task = "single_task"

    @property
    def execution_style(self) -> ExecutionStyle:
        """The v1 style this engine reports to clients."""
        if self is Engine.agentic_loop:
            return ExecutionStyle.agentic_loop
        if self is Engine.sequential_pipeline:
            return ExecutionStyle.crew_pipeline
        return ExecutionStyle.single_task


class DialectChoice(str, Enum):
    """Which rendering of the loop to speak.

    ``auto`` is the normal answer: the :class:`~gitpilot.agent.model_profile.
    ModelProfile` knows what the model can do, and a topology guessing over it
    is how a 1.5B model ends up being sent tool schemas it cannot use.
    """

    auto = "auto"
    native = "native"
    react_text = "react_text"
    lite = "lite"


class ApprovalMode(str, Enum):
    """Who answers when a call needs permission."""

    session_default = "session_default"
    plan = "plan"
    normal = "normal"
    auto = "auto"
    #: Headless: never prompt.  A call that would ask is denied instead of
    #: hanging on a prompt nobody will see.
    none = "none"


class VerificationMode(str, Enum):
    """Whether a run must prove its changes work (§10, Batch V4-E3)."""

    off = "off"
    auto = "auto"
    required = "required"


#: Subagent templates a document may name.  Deliberately a constant here rather
#: than an import of :mod:`gitpilot.agent.delegation`: parsing a topology must not
#: drag the whole agent package into an API import.  ``tests/topology`` pins this
#: tuple to the templates that actually exist, so the two cannot drift.
SUBAGENT_TEMPLATES: Tuple[str, ...] = ("explorer", "reviewer", "researcher", "test_analyst")

#: Roles with a prompt profile in :mod:`gitpilot.agent.prompts`.  Same reason for
#: being a constant, and pinned by the same kind of test.  A document may name a
#: role outside this set — it just gets generic instructions.
ROLE_PROFILES: Tuple[str, ...] = (
    "software_engineer", "feature_builder", "bug_hunter", "code_reviewer",
    "architect", "quick_fixer", "lite_agent",
)

#: Capability namespaces with a human label for the generated flow graph.
#: Anything not listed still gets a node, titled from its own namespace — an
#: unknown namespace should look unfamiliar, not disappear.
NAMESPACE_LABELS: Dict[str, Tuple[str, str]] = {
    "fs": ("File Tools", "read, write, edit, glob, grep"),
    "terminal": ("Shell", "commands, sandboxed to the workspace"),
    "test": ("Tests", "detect the framework, run the suite"),
    "git": ("Git", "status, diff, log, commit, push"),
    "github": ("Forge", "issues, pull requests, code search"),
    "web": ("Web", "search and fetch"),
    "todo": ("Plan", "the run's visible task list"),
    "mcp": ("MCP Servers", "tools from connected MCP servers"),
    "docker": ("Containers", "container lifecycle"),
}

#: ``agent.*`` is drawn as subagent nodes, not as one namespace node: a box
#: labelled "Agent" next to the subagents it spawns says nothing twice.
_DELEGATION_NAMESPACE = "agent"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_*]+)*$|^[a-z][a-z0-9_]*\.\*$")

#: Top-level keys the parser understands.  An unrecognised key is a warning, not
#: an error: a document written for a newer GitPilot should still load.
_KNOWN_KEYS = frozenset({
    "id", "name", "icon", "category", "description", "aliases", "hidden",
    "execution", "agent", "capabilities", "approval", "verification", "limits",
    "visualization", "flow_graph", "classifier_hints", "sequence",
    #: Emitted by :meth:`TopologyPolicy.to_dict` and ignored on the way back in —
    #: where a document came from is something the loader knows, not something a
    #: document may assert about itself.
    "source",
})


class SchemaError(ValueError):
    """A topology document that cannot be trusted to mean what it says."""


# ---------------------------------------------------------------------------
# Document sections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionSpec:
    engine: Engine = Engine.agentic_loop
    dialect: DialectChoice = DialectChoice.auto

    def to_dict(self) -> Dict[str, Any]:
        return {"engine": self.engine.value, "dialect": self.dialect.value}


@dataclass(frozen=True)
class AgentSpec:
    role: str = "software_engineer"
    subagents: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "subagents": list(self.subagents)}


@dataclass(frozen=True)
class ApprovalSpec:
    """Who answers approvals, and how much a document is allowed to change that."""

    mode: ApprovalMode = ApprovalMode.session_default

    @property
    def headless(self) -> bool:
        """``True`` when the topology says "never prompt"."""
        return self.mode is ApprovalMode.none

    def requested_mode(self) -> Optional[str]:
        """The value to hand :func:`~gitpilot.agent.policy.restrict_mode`.

        ``session_default`` and ``none`` are not permission modes: the first
        means "don't ask for one", the second is about *who answers*, not about
        what is permitted.  Both leave the session's mode alone.
        """
        if self.mode in (ApprovalMode.session_default, ApprovalMode.none):
            return None
        return str(self.mode.value)

    def effective_mode(self, session_mode: Any) -> "PermissionMode":
        """The mode this topology produces in a session running *session_mode*.

        Tighten-only, through the same function a request body goes through: a
        document asking for ``auto`` inside a ``normal`` session gets ``normal``.
        A topology is configuration, and configuration is not an authenticated
        escalation channel any more than a chat body is.
        """
        from ..agent.policy import restrict_mode

        return restrict_mode(session_mode, self.requested_mode())

    def approval_timeout_s(self, default: float = 120.0) -> float:
        """``0`` for a headless topology — a denial beats a prompt nobody sees."""
        return 0.0 if self.headless else default

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode.value}


@dataclass(frozen=True)
class VerificationSpec:
    tests: VerificationMode = VerificationMode.auto
    max_fix_cycles: int = 3

    def to_dict(self) -> Dict[str, Any]:
        return {"tests": self.tests.value, "max_fix_cycles": self.max_fix_cycles}


@dataclass(frozen=True)
class LimitsSpec:
    max_iterations: int = 40
    max_tool_calls: int = 120
    max_runtime_seconds: int = 1_800

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_iterations": self.max_iterations,
            "max_tool_calls": self.max_tool_calls,
            "max_runtime_seconds": self.max_runtime_seconds,
        }


@dataclass(frozen=True)
class TopologyPolicy:
    """The executable part of a v2 topology."""

    execution: ExecutionSpec = field(default_factory=ExecutionSpec)
    agent: AgentSpec = field(default_factory=AgentSpec)
    #: The capability mapping exactly as declared.  Kept raw because
    #: ``RunRequest.topology_capabilities`` wants the mapping, and because
    #: round-tripping through a mask would lose qualifiers the mask does not model.
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    approval: ApprovalSpec = field(default_factory=ApprovalSpec)
    verification: VerificationSpec = field(default_factory=VerificationSpec)
    limits: LimitsSpec = field(default_factory=LimitsSpec)
    visualization: str = "auto"
    #: ``builtin`` | ``user`` | ``project``.  A project document is only ever
    #: loaded from a trusted workspace (see :mod:`gitpilot.topology.registry`).
    source: str = "builtin"

    def capability_mask(self) -> "CapabilityMask":
        """The mask this document grants.

        Closed by default whenever the document named anything: the mask's own
        rule, and the reason a typo withholds a tool instead of granting one.
        """
        from ..agent.policy import CapabilityMask

        if not self.capabilities:
            return CapabilityMask.permissive()
        return CapabilityMask.from_mapping(dict(self.capabilities))

    def namespaces(self) -> List[str]:
        """Capability namespaces this topology grants, in declaration order."""
        seen: List[str] = []
        for key, value in self.capabilities.items():
            if value is False or value is None:
                continue
            namespace = key.split(".", 1)[0].rstrip("*").rstrip(".")
            if namespace and namespace not in seen:
                seen.append(namespace)
        return seen

    @property
    def read_only(self) -> bool:
        """Whether the document grants nothing that can change the workspace."""
        mutating = ("fs.write", "fs.edit", "fs.delete", "git.commit", "git.push")
        mask = self.capability_mask()
        return not any(mask.allows(capability) for capability in mutating)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution": self.execution.to_dict(),
            "agent": self.agent.to_dict(),
            "capabilities": dict(self.capabilities),
            "approval": self.approval.to_dict(),
            "verification": self.verification.to_dict(),
            "limits": self.limits.to_dict(),
            "visualization": self.visualization,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# The topology itself
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
    #: The v2 policy document, when this topology came from one.  ``None`` for
    #: the hand-written v1 entries, which is how callers tell them apart.
    policy: Optional[TopologyPolicy] = None
    #: Older ids that resolve here — a saved preference must survive a rename.
    aliases: Tuple[str, ...] = ()
    #: Resolvable but not offered in the picker (a superseded id kept as an alias).
    hidden: bool = False

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
            "policy": self.policy.to_dict() if self.policy else None,
            "aliases": list(self.aliases),
            "hidden": self.hidden,
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_document(data: Any, *, source: str = "builtin") -> Topology:
    """Build a :class:`Topology` from a §15.1 document.

    Raises :class:`SchemaError` for anything that would otherwise be honoured as
    a *weaker* policy than the author wrote.
    """
    if not isinstance(data, Mapping):
        raise SchemaError(f"a topology document must be a mapping, got {type(data).__name__}")

    for key in data:
        if str(key) not in _KNOWN_KEYS:
            logger.warning(
                "topology %r: ignoring unknown key %r", data.get("id", "?"), key,
            )

    topology_id = str(data.get("id") or "").strip()
    if not topology_id:
        raise SchemaError("a topology document needs an 'id'")
    if not _ID_RE.match(topology_id):
        raise SchemaError(
            f"topology id {topology_id!r} must be lower_snake_case "
            "(it appears in URLs and saved preferences)"
        )

    policy = TopologyPolicy(
        execution=_execution(data.get("execution"), topology_id),
        agent=_agent(data.get("agent"), topology_id),
        capabilities=_capabilities(data.get("capabilities"), topology_id),
        approval=_approval(data.get("approval"), topology_id),
        verification=_verification(data.get("verification"), topology_id),
        limits=_limits(data.get("limits"), topology_id),
        visualization="embedded" if _embedded_graph(data) else "auto",
        source=source,
    )

    name = str(data.get("name") or topology_id.replace("_", " ").title())
    description = str(data.get("description") or "").strip() or _describe(policy, name)
    category = _category(data.get("category"), policy)
    hints = [str(hint) for hint in _as_list(data.get("classifier_hints"))]

    graph = _embedded_graph(data) or build_flow_graph(
        policy, name=name, topology_id=topology_id,
    )

    return Topology(
        id=topology_id,
        name=name,
        description=description,
        category=category,
        icon=str(data.get("icon") or "\U0001f9e0"),   # brain
        agents_used=[policy.agent.role, *policy.agent.subagents],
        execution_style=policy.execution.engine.execution_style,
        routing_policy=_routing(policy, data, hints),
        flow_graph=graph,
        policy=policy,
        aliases=tuple(str(alias) for alias in _as_list(data.get("aliases"))),
        hidden=bool(data.get("hidden", False)),
    )


def _execution(raw: Any, topology_id: str) -> ExecutionSpec:
    section = _section(raw, "execution", topology_id)
    return ExecutionSpec(
        engine=_enum(Engine, section.get("engine"), Engine.agentic_loop,
                     field_name="execution.engine", topology_id=topology_id),
        dialect=_enum(DialectChoice, section.get("dialect"), DialectChoice.auto,
                      field_name="execution.dialect", topology_id=topology_id),
    )


def _agent(raw: Any, topology_id: str) -> AgentSpec:
    section = _section(raw, "agent", topology_id)
    role = str(section.get("role") or "software_engineer").strip()
    if not _ID_RE.match(role):
        raise SchemaError(f"topology {topology_id!r}: agent.role {role!r} must be lower_snake_case")
    if role not in ROLE_PROFILES:
        # A warning rather than an error: a user is allowed to invent a role, and
        # an unknown one still produces a working run — just with generic prose
        # instead of a persona. Refusing would make the built-in table a closed set.
        logger.warning(
            "topology %r: role %r has no prompt profile; the run gets generic "
            "instructions. Known roles: %s",
            topology_id, role, ", ".join(sorted(ROLE_PROFILES)),
        )

    subagents: List[str] = []
    for entry in _as_list(section.get("subagents")):
        name = str(entry).strip()
        if name not in SUBAGENT_TEMPLATES:
            raise SchemaError(
                f"topology {topology_id!r}: unknown subagent {name!r}; "
                f"available: {', '.join(SUBAGENT_TEMPLATES)}"
            )
        if name not in subagents:
            subagents.append(name)
    return AgentSpec(role=role, subagents=tuple(subagents))


def _capabilities(raw: Any, topology_id: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise SchemaError(
            f"topology {topology_id!r}: 'capabilities' must be a mapping of "
            "capability → true | false | ask | {qualifier}"
        )
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        capability = str(key).strip()
        if not _CAPABILITY_RE.match(capability):
            raise SchemaError(
                f"topology {topology_id!r}: {capability!r} is not a capability id "
                "(expected e.g. 'fs.read', 'github.pr.create' or 'fs.*')"
            )
        out[capability] = _capability_value(capability, value, topology_id)
    return out


def _capability_value(capability: str, value: Any, topology_id: str) -> Any:
    """Validate one grant without deciding what it means.

    Interpretation belongs to :class:`~gitpilot.agent.policy.Qualifier`; this only
    refuses shapes it would silently discard.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("ask", "true", "yes", "false", "no", "forbidden"):
            if text in ("false", "no", "forbidden"):
                return False
            return True if text in ("true", "yes") else "ask"
        raise SchemaError(
            f"topology {topology_id!r}: capability {capability!r} = {value!r}; "
            "expected true, false or 'ask'"
        )
    if isinstance(value, Mapping):
        unknown = set(map(str, value)) - {
            "paths", "exclude", "network", "max_depth", "verdict", "classes",
        }
        if unknown:
            logger.warning(
                "topology %r: capability %s has unrecognised qualifier keys %s",
                topology_id, capability, ", ".join(sorted(unknown)),
            )
        return dict(value)
    raise SchemaError(
        f"topology {topology_id!r}: capability {capability!r} has an unusable value "
        f"of type {type(value).__name__}"
    )


def _approval(raw: Any, topology_id: str) -> ApprovalSpec:
    section = _section(raw, "approval", topology_id)
    return ApprovalSpec(
        mode=_enum(ApprovalMode, section.get("mode"), ApprovalMode.session_default,
                   field_name="approval.mode", topology_id=topology_id),
    )


def _verification(raw: Any, topology_id: str) -> VerificationSpec:
    section = _section(raw, "verification", topology_id)

    # YAML 1.1 reads a bare ``off`` as the boolean False, so ``tests: off`` — the
    # spelling §15.1 uses — arrives here as False under PyYAML and as the string
    # "off" under the in-tree fallback.  Accepting both is what makes one file mean
    # one thing regardless of which parser read it.
    tests = section.get("tests")
    if tests is False:
        tests = VerificationMode.off.value
    elif tests is True:
        raise SchemaError(
            f"topology {topology_id!r}: verification.tests must be one of "
            f"{', '.join(m.value for m in VerificationMode)} — YAML read 'on'/'yes' "
            "as a boolean, which does not name one of them"
        )

    cycles = section.get("max_fix_cycles", 3)
    if not isinstance(cycles, int) or isinstance(cycles, bool) or cycles < 0:
        raise SchemaError(
            f"topology {topology_id!r}: verification.max_fix_cycles must be a "
            f"non-negative integer, got {cycles!r}"
        )
    return VerificationSpec(
        tests=_enum(VerificationMode, tests, VerificationMode.auto,
                    field_name="verification.tests", topology_id=topology_id),
        max_fix_cycles=cycles,
    )


def _limits(raw: Any, topology_id: str) -> LimitsSpec:
    section = _section(raw, "limits", topology_id)
    defaults = LimitsSpec()
    values: Dict[str, int] = {}
    for name in ("max_iterations", "max_tool_calls", "max_runtime_seconds"):
        value = section.get(name, getattr(defaults, name))
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise SchemaError(
                f"topology {topology_id!r}: limits.{name} must be a positive "
                f"integer, got {value!r}"
            )
        values[name] = value
    return LimitsSpec(**values)


def _category(raw: Any, policy: TopologyPolicy) -> TopologyCategory:
    if raw is None:
        # A pipeline document is one that runs a fixed sequence; everything else
        # is a system topology the user picks deliberately.
        return (
            TopologyCategory.pipeline
            if policy.execution.engine is Engine.sequential_pipeline
            else TopologyCategory.system
        )
    try:
        return TopologyCategory(str(raw).strip().lower())
    except ValueError as exc:
        raise SchemaError(
            f"unknown category {raw!r}; expected one of "
            f"{', '.join(c.value for c in TopologyCategory)}"
        ) from exc


def _routing(policy: TopologyPolicy, data: Mapping[str, Any], hints: List[str]) -> RoutingPolicy:
    """The v1 routing policy a document implies.

    A ``sequence`` wins over the engine, and that is a compatibility bridge
    rather than an oversight. The legacy CrewAI dispatcher in
    :mod:`gitpilot.agentic` selects a pipeline by ``strategy is fixed_sequence``,
    so a migrated pipeline that dropped its sequence would stop running *the
    moment its document landed* — with the ``agent_loop_default`` flag still off.
    Keeping the sequence means one document drives both paths: the legacy
    dispatcher until the flip, the engine after it (§15.3, Batch V4-G3).
    """
    engine = policy.execution.engine
    sequence = [str(step) for step in _as_list(data.get("sequence"))]
    if sequence:
        return RoutingPolicy(
            strategy=RoutingStrategy.fixed_sequence,
            sequence=sequence,
            classifier_hints=hints,
        )
    if engine is Engine.sequential_pipeline:
        return RoutingPolicy(
            strategy=RoutingStrategy.fixed_sequence,
            sequence=[policy.agent.role],
            classifier_hints=hints,
        )
    if engine is Engine.single_task:
        return RoutingPolicy(
            strategy=RoutingStrategy.classify_and_dispatch, classifier_hints=hints,
        )
    return RoutingPolicy(
        strategy=RoutingStrategy.always_main_agent,
        primary_agent=policy.agent.role,
        classifier_hints=hints,
    )


def _describe(policy: TopologyPolicy, name: str) -> str:
    """A description derived from the document, so every topology has one."""
    parts = [f"{name}: {policy.execution.engine.value.replace('_', ' ')}"]
    if policy.read_only:
        parts.append("read-only")
    if policy.agent.subagents:
        parts.append(f"subagents: {', '.join(policy.agent.subagents)}")
    parts.append(f"up to {policy.limits.max_iterations} iterations")
    return "; ".join(parts) + "."


def _section(raw: Any, name: str, topology_id: str) -> Mapping[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise SchemaError(f"topology {topology_id!r}: {name!r} must be a mapping")
    return raw


def _enum(enum_cls: Any, raw: Any, default: Any, *, field_name: str, topology_id: str) -> Any:
    if raw is None or raw == "":
        return default
    try:
        return enum_cls(str(raw).strip().lower())
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise SchemaError(
            f"topology {topology_id!r}: unknown {field_name} {raw!r}; expected one of {allowed}"
        ) from exc


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _embedded_graph(data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """An explicitly drawn graph, when the document ships one."""
    for key in ("flow_graph", "visualization"):
        candidate = data.get(key)
        if isinstance(candidate, Mapping) and "nodes" in candidate:
            return {"nodes": list(candidate["nodes"]), "edges": list(candidate.get("edges") or [])}
    return None


# ---------------------------------------------------------------------------
# The generated flow graph
# ---------------------------------------------------------------------------

_ROW_USER = 0
_ROW_AGENT = 150
_ROW_TOOLS = 330
_ROW_SUBAGENTS = 490
_ROW_OUTPUT = 640
_COLUMN_GAP = 200
_CENTRE_X = 400


def build_flow_graph(
    policy: TopologyPolicy, *, name: str, topology_id: str = "",
) -> Dict[str, Any]:
    """Derive the ReactFlow graph from the policy document.

    The picture is generated so it cannot lie: every tool node exists because a
    capability was granted, and every subagent node exists because the document
    named a template. A hand-drawn graph can claim an approval batcher that was
    never built — this one can only claim what the policy says.
    """
    nodes: List[Dict[str, Any]] = [{
        "id": "user_request",
        "type": "user",
        "data": {"label": "User Request", "description": "Task, question or follow-up"},
        "position": {"x": _CENTRE_X, "y": _ROW_USER},
    }]
    edges: List[Dict[str, Any]] = []

    mask = policy.capability_mask()
    granted = sorted(
        key for key in policy.capabilities
        if policy.capabilities[key] is not False and policy.capabilities[key] is not None
    )
    nodes.append({
        "id": "main_agent",
        "type": "agent",
        "data": {
            "label": _role_label(policy.agent.role),
            "mode": "read-only" if policy.read_only else "read-write",
            "dialect": policy.execution.dialect.value,
            "tools": granted or ["ALL"],
            "description": (
                f"{name} running in a while(tool_use) loop: reason, act, observe, repeat. "
                f"Up to {policy.limits.max_iterations} iterations and "
                f"{policy.limits.max_tool_calls} tool calls."
            ),
        },
        "position": {"x": _CENTRE_X, "y": _ROW_AGENT},
    })
    edges.append({
        "id": "e-user-main", "source": "user_request", "target": "main_agent",
        "animated": True,
    })

    namespaces = [ns for ns in policy.namespaces() if ns != _DELEGATION_NAMESPACE]
    for index, namespace in enumerate(namespaces):
        label, blurb = NAMESPACE_LABELS.get(
            namespace, (namespace.replace("_", " ").title(), f"{namespace}.* capabilities"),
        )
        node_id = f"tools_{namespace}"
        nodes.append({
            "id": node_id,
            "type": "tool",
            "data": {
                "label": label,
                "description": blurb,
                "tools": sorted(
                    key for key in policy.capabilities
                    if key.split(".", 1)[0] == namespace and mask.allows(key)
                ),
            },
            "position": {"x": _spread(index, len(namespaces)), "y": _ROW_TOOLS},
        })
        edges.append({
            "id": f"e-main-{namespace}", "source": "main_agent", "target": node_id,
            "type": "bidirectional",
        })

    subagents = _subagent_nodes(policy)
    for index, subagent in enumerate(subagents):
        node_id = f"subagent_{subagent}"
        nodes.append({
            "id": node_id,
            "type": "agent",
            "data": {
                "label": f"{subagent.replace('_', ' ').title()} (subagent)",
                "mode": "read-only",
                "description": (
                    "Spawned by agent.delegate with the parent's capabilities "
                    "intersected with the template's; returns a summary, not a transcript."
                ),
            },
            "position": {"x": _spread(index, len(subagents)), "y": _ROW_SUBAGENTS},
        })
        edges.append({
            "id": f"e-main-{subagent}", "source": "main_agent", "target": node_id,
            "label": f"Task({subagent})",
        })
        edges.append({
            "id": f"e-{subagent}-main", "source": node_id, "target": "main_agent",
            "label": "summary", "animated": True,
        })

    nodes.append({
        "id": "output",
        "type": "output",
        "data": {
            "label": "Answer / Diff",
            "description": (
                "Streamed over SSE. Verification: "
                f"{policy.verification.tests.value}."
            ),
        },
        "position": {"x": _CENTRE_X, "y": _ROW_OUTPUT},
    })
    edges.append({
        "id": "e-main-output", "source": "main_agent", "target": "output",
        "label": "no tool calls = done",
    })

    return {"nodes": nodes, "edges": edges}


def _subagent_nodes(policy: TopologyPolicy) -> List[str]:
    """Which subagent boxes to draw.

    A topology that grants ``agent.delegate`` without naming templates still
    delegates, so it gets one generic box rather than an empty row that reads as
    "delegation is off".
    """
    if policy.agent.subagents:
        return list(policy.agent.subagents)
    if policy.capability_mask().allows("agent.delegate") and any(
        key.startswith(_DELEGATION_NAMESPACE) for key in policy.capabilities
    ):
        return ["task"]
    return []


def _spread(index: int, count: int) -> int:
    """Lay *count* nodes out in a row centred on the main agent."""
    if count <= 1:
        return _CENTRE_X
    offset = (index - (count - 1) / 2) * _COLUMN_GAP
    return int(round(_CENTRE_X + offset))


def _role_label(role: str) -> str:
    return role.replace("_", " ").title()
