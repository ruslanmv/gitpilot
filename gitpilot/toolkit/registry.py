# gitpilot/toolkit/registry.py
"""Tool contracts and the registry that owns them — Batch V4-A1.

Three things live here:

* **The contract.**  :class:`ToolSpec` describes one tool: a namespaced id, a
  JSON Schema for its arguments, a capability key, a risk class, and the side
  effects it can have.  :class:`ToolCall` and :class:`ToolResult` are what
  flows in and out.
* **The registry.**  :class:`ToolRegistry` maps ids to handlers, renders model
  facing schemas at three verbosity levels within a tool-count budget, and
  executes one call at a time.
* **The execution context.**  :class:`ToolExecutionContext` carries the
  workspace or repository binding, replacing the module-global
  ``_current_repo_context`` / ``_current_workspace`` that the CrewAI tools
  mutate.  Two concurrent sessions cannot corrupt each other's target.

Deliberate omissions, and where they land:

* **Authorization.**  ``execute`` does not decide whether a call is allowed.
  The loop asks the policy engine first (Batch V4-D1).  Until then the
  permissive :data:`ALL_CAPABILITIES` stands in.
* **Model profiles.**  :class:`SchemaProfile` is the small structural subset of
  Batch V4-B2's ``ModelProfile`` that schema rendering needs — verbosity and a
  tool budget.  ``ModelProfile`` will satisfy it without inheritance, which is
  what lets A1 ship before B2.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class Risk(str, Enum):
    """How much trouble a tool can cause if the model is wrong.

    The policy engine may *raise* a call's risk (an ``rm -rf`` inside an
    otherwise ordinary ``terminal.run``) but never lower it below the tool's
    declared class.
    """

    SAFE = "safe"                # read-only; runs without asking
    APPROVAL = "approval"        # mutates local state; asks in `normal` mode
    HIGH_RISK = "high_risk"      # leaves the machine or is hard to undo


class Effect(str, Enum):
    """What a tool touches.  Coarse on purpose — this drives policy, not docs."""

    READS_FS = "reads_fs"
    WRITES_FS = "writes_fs"
    EXECUTES = "executes"
    NETWORK = "network"
    GIT_LOCAL = "git_local"
    GIT_REMOTE = "git_remote"
    FORGE_WRITE = "forge_write"
    #: Changed state on a system GitPilot does not own — a database row, a ticket,
    #: a deploy (Batch V4-H1).  MCP tools need this: "wrote a file" and "ran git"
    #: are both wrong about a Postgres UPDATE, and calling it read-only would let a
    #: read-only run mutate someone's production data.
    EXTERNAL_WRITE = "external_write"


#: A tool id: dot-separated snake_case segments, at least two
#: (``fs.read``, ``github.pr.create``, ``mcp.postgres.query``).
_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class ToolError(Exception):
    """Raised for registry misuse (bad id, duplicate registration, unknown tool)."""


def permits(capabilities: Any, spec: "ToolSpec") -> bool:
    """Whether *capabilities* grants *spec* — Batch V4-H5.

    A grant may name either the capability **group** a tool belongs to
    (``git.read``, ``github.pr.write``) or the **tool id** itself
    (``git.status``, ``github.pr.create``). Both are checked, and the id is checked
    first because it is the more specific statement.

    Twelve of the built-in specs declare a grouped capability that is not their id,
    and every shipped topology document names tool ids — §15.1's own example writes
    ``github.pr.create: ask``. Checking only the group meant those documents granted
    nothing: an agent under the *default* topology could not run ``git status`` or
    read an issue, silently, because the tools were withheld from the schema list
    rather than refused at call time. Requiring a document author to know the
    internal grouping is a trap that caught all eight of them.
    """
    if spec.id != spec.capability and capabilities.allows(spec.id):
        return True
    return bool(capabilities.allows(spec.capability))


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """Everything the system knows about one tool, apart from its behaviour."""

    id: str
    title: str
    description: str
    params_schema: Dict[str, Any]
    capability: str = ""                 # defaults to ``id``
    risk: Risk = Risk.SAFE
    effects: frozenset[Effect] = frozenset()
    timeout_s: Optional[int] = None
    lite_compatible: bool = True
    #: Lower survives schema-budget pruning first.  Topologies set this in
    #: Batch V4-G1; the default keeps registration order meaningful.
    priority: int = 50

    def __post_init__(self) -> None:
        if not _TOOL_ID_RE.match(self.id):
            raise ToolError(
                f"invalid tool id {self.id!r}: expected namespaced snake_case "
                "like 'fs.read'"
            )
        if not isinstance(self.params_schema, Mapping):
            raise ToolError(f"{self.id}: params_schema must be a JSON Schema object")
        if self.params_schema.get("type") != "object":
            raise ToolError(f"{self.id}: params_schema must be of type 'object'")
        if self.timeout_s is not None and self.timeout_s <= 0:
            # Rejected rather than interpreted: a falsy 0 reads as "no timeout"
            # to a truthiness check and as "expire immediately" to a human.
            raise ToolError(f"{self.id}: timeout_s must be positive or None")
        if not self.capability:
            object.__setattr__(self, "capability", self.id)

    @property
    def namespace(self) -> str:
        return self.id.split(".", 1)[0]

    def signature(self) -> str:
        """A one-line call signature, e.g. ``fs.read(path, offset?)``.

        Used by the small-model schema verbosity, where a full JSON Schema per
        tool would consume the context window the task needs.
        """
        props: Mapping[str, Any] = self.params_schema.get("properties", {}) or {}
        required = set(self.params_schema.get("required", []) or [])
        parts = [name if name in required else f"{name}?" for name in props]
        return f"{self.id}({', '.join(parts)})"


@dataclass
class ToolCall:
    """One requested invocation.

    ``id`` is the provider's call id where there is one, and a synthesized
    ``call_<n>`` in the text dialects (Batches V4-B4/B5) — the loop needs a
    stable handle either way to correlate results and events.
    """

    id: str
    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """One outcome.

    ``content`` is what the model sees.  ``data`` is the structured payload for
    events, the journal, and the UI — a diff, an exit code, the paths written —
    so no consumer has to re-parse prose.  ``error`` is a machine-readable
    class, not a message: the message belongs in ``content``, where the model
    can act on it.
    """

    call_id: str
    tool: str
    ok: bool
    content: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    denied: bool = False

    @classmethod
    def success(
        cls,
        call: ToolCall,
        content: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        return cls(call_id=call.id, tool=call.tool, ok=True, content=content, data=data)

    @classmethod
    def failure(
        cls,
        call: ToolCall,
        content: str,
        *,
        error: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        return cls(
            call_id=call.id, tool=call.tool, ok=False,
            content=content, data=data, error=error,
        )

    @classmethod
    def denied_for(cls, call: ToolCall, reason: str) -> "ToolResult":
        """A policy refusal, which is not the same as a failure.

        The model is told plainly so it can choose another route instead of
        retrying the same call.
        """
        return cls(
            call_id=call.id, tool=call.tool, ok=False,
            content=f"Denied: {reason}", error="denied", denied=True,
        )


# ---------------------------------------------------------------------------
# Execution context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalWorkspace:
    """A checkout on disk.  Tools resolve paths under ``root`` and never above it."""

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))


@dataclass(frozen=True)
class RepoBinding:
    """A GitHub repository reached over the API, with no local checkout."""

    owner: str
    repo: str
    token: Optional[str] = None
    branch: Optional[str] = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


EventEmitter = Callable[[str, Dict[str, Any]], Awaitable[None]]


@dataclass
class ToolExecutionContext:
    """Per-run target and services for tool handlers.

    A tool takes no repository or workspace argument from the model — those are
    facts about the session, and letting a model pass them would let it aim a
    write at somewhere it was never granted.
    """

    workspace: Optional[LocalWorkspace] = None
    repo: Optional[RepoBinding] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    emit: Optional[EventEmitter] = None
    #: Sandbox override.  Normally ``None``: :mod:`gitpilot.sandbox_routing`
    #: resolves the backend from settings per call, which is what makes a
    #: mid-session change in Settings → Sandbox take effect.  GitPilot has no
    #: ``SandboxManager`` type to hold here — ``get_sandbox()`` plus the three
    #: backend classes are the whole surface — so this is typed loosely and
    #: exists for tests and for a future per-run pin.
    sandbox: Optional[Any] = None
    #: Free-form slot for services later batches attach (the policy decision in
    #: D1, the journal in C1).
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        """Whether handlers should use the on-disk backend.

        A local checkout wins when both bindings are present: it is the cheaper
        and more capable of the two, and it is what the user is looking at.
        """
        return self.workspace is not None

    def require_workspace(self) -> LocalWorkspace:
        if self.workspace is None:
            raise ToolError("no local workspace bound to this run")
        return self.workspace

    def require_repo(self) -> RepoBinding:
        if self.repo is None:
            raise ToolError("no repository bound to this run")
        return self.repo


# ---------------------------------------------------------------------------
# Capabilities (placeholder until Batch V4-D1)
# ---------------------------------------------------------------------------


class CapabilitySet(Protocol):
    """Whatever can answer "may this run?" for a capability key.

    Batch V4-D1's ``CapabilityMask`` implements this structurally; A1 only
    needs the question answered.
    """

    def allows(self, capability: str) -> bool: ...


class _AllowAll:
    def allows(self, capability: str) -> bool:  # noqa: D102 - trivial
        return True

    def __repr__(self) -> str:
        return "ALL_CAPABILITIES"


#: Stand-in for "no restrictions", used until the policy engine lands.  Its
#: presence in a call site is a marker for work Batch V4-D1 must revisit.
ALL_CAPABILITIES: CapabilitySet = _AllowAll()


def capabilities_from(granted: Iterable[str]) -> CapabilitySet:
    """A concrete :class:`CapabilitySet` over an explicit allow-list.

    Supports namespace wildcards (``fs.*``) because topologies grant whole
    families far more often than single tools.
    """
    allowed = set(granted)

    class _Explicit:
        def allows(self, capability: str) -> bool:
            if capability in allowed:
                return True
            head = capability.split(".", 1)[0]
            return f"{head}.*" in allowed or "*" in allowed

    return _Explicit()


# ---------------------------------------------------------------------------
# Schema rendering
# ---------------------------------------------------------------------------

VERBOSITY_FULL = "full"
VERBOSITY_COMPACT = "compact"
VERBOSITY_NAMES_ONLY = "names_only"

_VERBOSITIES = (VERBOSITY_FULL, VERBOSITY_COMPACT, VERBOSITY_NAMES_ONLY)


@dataclass(frozen=True)
class SchemaProfile:
    """The slice of a model's profile that schema rendering depends on."""

    max_tool_schemas: int = 64
    schema_verbosity: str = VERBOSITY_FULL

    def __post_init__(self) -> None:
        if self.schema_verbosity not in _VERBOSITIES:
            raise ToolError(
                f"unknown schema_verbosity {self.schema_verbosity!r}; "
                f"expected one of {_VERBOSITIES}"
            )


DEFAULT_SCHEMA_PROFILE = SchemaProfile()


@dataclass(frozen=True)
class PrunedTool:
    """One tool left out of a render, and why."""

    id: str
    reason: str


@dataclass(frozen=True)
class SchemaRender:
    """Rendered tool definitions plus an account of what was left out.

    The dropped list exists so the loop can emit a ``tools_pruned`` event.  A
    silent cap reads to everyone downstream as "the model had every tool and
    still failed", which is the wrong diagnosis.
    """

    schemas: List[Dict[str, Any]]
    dropped: List[PrunedTool] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return bool(self.dropped)


def _first_sentence(text: str, limit: int = 160) -> str:
    collapsed = " ".join(text.split())
    match = re.search(r"(?<=[.!?])\s", collapsed)
    if match:
        collapsed = collapsed[: match.start()]
    return collapsed[:limit].rstrip()


def _compact_schema(schema: Mapping[str, Any]) -> Dict[str, Any]:
    """Types and required-ness only — the shape a small model can still follow."""
    props = schema.get("properties", {}) or {}
    compact_props: Dict[str, Any] = {}
    for name, prop in props.items():
        entry: Dict[str, Any] = {"type": prop.get("type", "string")}
        if "enum" in prop:
            entry["enum"] = prop["enum"]
        compact_props[name] = entry
    out: Dict[str, Any] = {"type": "object", "properties": compact_props}
    if schema.get("required"):
        out["required"] = list(schema["required"])
    return out


def render_spec(spec: ToolSpec, verbosity: str) -> Dict[str, Any]:
    """One tool definition at the requested verbosity."""
    if verbosity == VERBOSITY_NAMES_ONLY:
        return {
            "name": spec.id,
            "signature": spec.signature(),
            "description": _first_sentence(spec.description, 100),
        }
    if verbosity == VERBOSITY_COMPACT:
        return {
            "name": spec.id,
            "description": _first_sentence(spec.description),
            "parameters": _compact_schema(spec.params_schema),
        }
    return {
        "name": spec.id,
        "description": spec.description.strip(),
        "parameters": dict(spec.params_schema),
    }


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

_JSON_TYPES: Dict[str, Tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def validate_arguments(
    schema: Mapping[str, Any], arguments: Mapping[str, Any],
) -> List[str]:
    """Check *arguments* against the subset of JSON Schema tools actually use.

    Returns a list of human-readable problems; empty means valid.  The point is
    not conformance — it is giving a model a correction it can act on, which is
    why messages name the argument and what was expected.  Hand-rolled rather
    than pulling in ``jsonschema``: the vocabulary here is small and fixed, and
    a new runtime dependency for it would not pay for itself.
    """
    problems: List[str] = []
    props: Mapping[str, Any] = schema.get("properties", {}) or {}
    required: Sequence[str] = schema.get("required", []) or []

    for name in required:
        if name not in arguments or arguments[name] is None:
            problems.append(f"missing required argument {name!r}")

    if schema.get("additionalProperties") is False:
        for name in arguments:
            if name not in props:
                problems.append(
                    f"unknown argument {name!r} (expected one of "
                    f"{', '.join(sorted(props)) or 'none'})"
                )

    for name, value in arguments.items():
        prop = props.get(name)
        if not isinstance(prop, Mapping) or value is None:
            continue

        expected = prop.get("type")
        if isinstance(expected, str) and expected in _JSON_TYPES:
            allowed = _JSON_TYPES[expected]
            # bool is an int subclass; a flag passed where a number belongs is
            # a real mistake and worth reporting rather than coercing.
            if isinstance(value, bool) and expected in ("integer", "number"):
                problems.append(f"{name!r} must be {expected}, got boolean")
            elif not isinstance(value, allowed):
                problems.append(
                    f"{name!r} must be {expected}, got {type(value).__name__}"
                )
                continue

        if "enum" in prop and value not in prop["enum"]:
            problems.append(
                f"{name!r} must be one of {prop['enum']}, got {value!r}"
            )
        minimum, maximum = prop.get("minimum"), prop.get("maximum")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if minimum is not None and value < minimum:
                problems.append(f"{name!r} must be >= {minimum}")
            if maximum is not None and value > maximum:
                problems.append(f"{name!r} must be <= {maximum}")

    return problems


# ---------------------------------------------------------------------------
# Legacy names
# ---------------------------------------------------------------------------

#: Legacy CrewAI display names and VS Code tool ids → canonical ids.  Saved
#: sessions, recorded events, the extension's approval cards and
#: ``approval_protocol.DANGEROUS_TOOLS`` all speak these; resolving them keeps
#: every one of those working while the migration runs.  Keys are matched
#: case-insensitively after whitespace normalisation.
LEGACY_ALIASES: Dict[str, str] = {
    # filesystem — GitHub-mode CrewAI tools
    "read file content": "fs.read",
    "read": "fs.read",
    "read file content window": "fs.read",
    "write or update a file in the repository": "fs.write",
    "edit a section of a file (exact string replacement)": "fs.edit",
    "delete a file from the repository": "fs.delete",
    "list all files in repository": "fs.list",
    "list": "fs.list",
    "find files matching a pattern": "fs.glob",
    "search file contents": "fs.grep",
    "find code by semantic search": "fs.semantic_search",
    "apply a unified diff to a file": "fs.apply_patch",
    "get directory structure": "fs.tree",
    "get repository summary": "fs.repo_summary",
    # filesystem — local-mode CrewAI tools
    "read local file": "fs.read",
    "write local file": "fs.write",
    "delete local file": "fs.delete",
    "list local files": "fs.list",
    "search in files": "fs.grep",
    # VS Code tool ids
    "read_file": "fs.read",
    "write_file": "fs.write",
    "edit_file": "fs.edit",
    "delete_file": "fs.delete",
    "list_files": "fs.list",
    "glob": "fs.glob",
    "grep": "fs.grep",
    # terminal
    "run shell command": "terminal.run",
    "run_command": "terminal.run",
    "run code in sandbox": "terminal.run_snippet",
    "run_in_sandbox": "terminal.run_snippet",
    # git
    "git status": "git.status",
    "git_status": "git.status",
    "git diff": "git.diff",
    "git_diff": "git.diff",
    "git log": "git.log",
    "git_log": "git.log",
    "git commit": "git.commit",
    "git_commit": "git.commit",
    "create a new branch in the repository": "git.branch",
    # not-yet-real tools that already appear in topology flow graphs
    "todowrite": "todo.write",
}


def _normalise_name(name: str) -> str:
    return " ".join(str(name).strip().lower().replace("-", "_").split())


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ToolHandler = Callable[..., Any]     # (call, ctx) -> ToolResult | Awaitable[ToolResult]


class ToolRegistry:
    """Holds tool contracts and runs one call at a time.

    Explicitly *not* a global: a registry is built per run from the topology's
    capability set, so two sessions with different permissions cannot see each
    other's tools.  :func:`gitpilot.toolkit.builtins.build_default_registry`
    assembles the standard set.
    """

    def __init__(self) -> None:
        self._specs: Dict[str, ToolSpec] = {}
        self._handlers: Dict[str, ToolHandler] = {}
        self._order: List[str] = []

    # -- registration ----------------------------------------------------

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.id in self._specs:
            raise ToolError(f"tool {spec.id!r} is already registered")
        if not callable(handler):
            raise ToolError(f"tool {spec.id!r}: handler is not callable")
        self._specs[spec.id] = spec
        self._handlers[spec.id] = handler
        self._order.append(spec.id)

    def __contains__(self, tool_id: object) -> bool:
        return self.resolve(str(tool_id)) is not None

    def __len__(self) -> int:
        return len(self._specs)

    # -- lookup ----------------------------------------------------------

    def resolve(self, name: str) -> Optional[str]:
        """Map anything a caller might say to a registered id, or ``None``.

        Tries the id, then the legacy alias table, then a normalised form.
        Local models mangle names in predictable ways (``fs_read``, ``Fs.Read``,
        ``fs-read``) and a corrective observation costs an entire model turn, so
        the cheap normalisation is worth it.
        """
        if name in self._specs:
            return name
        normalised = _normalise_name(name)
        alias = LEGACY_ALIASES.get(normalised)
        if alias and alias in self._specs:
            return alias
        underscored = normalised.replace(" ", "_")
        alias = LEGACY_ALIASES.get(underscored)
        if alias and alias in self._specs:
            return alias
        for candidate in (normalised, underscored, underscored.replace("_", ".", 1)):
            if candidate in self._specs:
                return candidate
        return None

    def spec(self, name: str) -> ToolSpec:
        resolved = self.resolve(name)
        if resolved is None:
            raise ToolError(f"unknown tool {name!r}")
        return self._specs[resolved]

    def specs(self, capabilities: Optional[CapabilitySet] = None) -> List[ToolSpec]:
        caps = capabilities or ALL_CAPABILITIES
        return [
            self._specs[tid] for tid in self._order
            if permits(caps, self._specs[tid])
        ]

    def ids(self) -> List[str]:
        return list(self._order)

    # -- schema rendering ------------------------------------------------

    def render(
        self,
        capabilities: Optional[CapabilitySet] = None,
        profile: Optional[SchemaProfile] = None,
        *,
        lite_only: bool = False,
    ) -> SchemaRender:
        """Model-facing definitions for the tools *capabilities* permits.

        *lite_only* withholds tools marked ``lite_compatible=False``.  The flag
        had no consumer until Batch V4-E1 added a tool that genuinely needs it:
        ``todo.write`` asks a model to maintain structured state alongside its
        actual work, which a 1.5B model does badly at both. Set by the LITE
        adapter, so the decision lives with the dialect that knows.
        """
        prof = profile or DEFAULT_SCHEMA_PROFILE
        caps = capabilities or ALL_CAPABILITIES

        dropped: List[PrunedTool] = [
            PrunedTool(self._specs[tid].id, "capability-not-granted")
            for tid in self._order
            if not permits(caps, self._specs[tid])
        ]
        permitted = self.specs(caps)

        if lite_only:
            dropped.extend(
                PrunedTool(spec.id, "not-lite-compatible")
                for spec in permitted if not spec.lite_compatible
            )
            permitted = [spec for spec in permitted if spec.lite_compatible]

        budget = max(int(prof.max_tool_schemas), 0)
        if len(permitted) > budget:
            ranked = sorted(
                enumerate(permitted), key=lambda pair: (pair[1].priority, pair[0]),
            )
            keep_ids = {spec.id for _, spec in ranked[:budget]}
            dropped.extend(
                PrunedTool(spec.id, "schema-budget")
                for spec in permitted if spec.id not in keep_ids
            )
            permitted = [spec for spec in permitted if spec.id in keep_ids]

        return SchemaRender(
            schemas=[render_spec(spec, prof.schema_verbosity) for spec in permitted],
            dropped=dropped,
        )

    def schemas(
        self,
        capabilities: Optional[CapabilitySet] = None,
        profile: Optional[SchemaProfile] = None,
    ) -> List[Dict[str, Any]]:
        """Convenience wrapper when the caller does not need the prune report."""
        return self.render(capabilities, profile).schemas

    # -- execution -------------------------------------------------------

    async def execute(
        self, call: ToolCall, ctx: ToolExecutionContext,
    ) -> ToolResult:
        """Run one call.

        Never raises for anything the model did: an unknown tool, bad
        arguments, a handler blowing up, or a timeout all come back as a
        ``ToolResult`` the model can read and correct.  Only registry misuse by
        *our own* code (a non-callable handler, say) is allowed to propagate.
        """
        resolved = self.resolve(call.tool)
        if resolved is None:
            return ToolResult.failure(
                call,
                f"Unknown tool {call.tool!r}. Available: {', '.join(self._order)}",
                error="unknown_tool",
            )
        if resolved != call.tool:
            # Keep the canonical id on the result so events and the journal
            # never record two names for one tool.
            call = replace(call, tool=resolved)

        spec = self._specs[resolved]
        problems = validate_arguments(spec.params_schema, call.arguments or {})
        if problems:
            return ToolResult.failure(
                call,
                "Invalid arguments for {}: {}. Expected: {}".format(
                    spec.id, "; ".join(problems), spec.signature(),
                ),
                error="invalid_arguments",
                data={"problems": problems},
            )

        handler = self._handlers[resolved]
        try:
            if inspect.iscoroutinefunction(handler):
                outcome: Any = handler(call, ctx)
            else:
                # A synchronous handler could block for as long as it likes —
                # a git subprocess, a file read on a slow disk — and the loop
                # is single-threaded, so running it inline would stall event
                # emission and cancellation for every other task.
                outcome = asyncio.to_thread(handler, call, ctx)

            if inspect.isawaitable(outcome):
                if spec.timeout_s is not None:
                    outcome = await asyncio.wait_for(outcome, spec.timeout_s)
                else:
                    outcome = await outcome
        except asyncio.TimeoutError:
            return ToolResult.failure(
                call,
                f"{spec.id} timed out after {spec.timeout_s}s",
                error="timeout",
            )
        except PermissionError as exc:
            # Path jails and sandbox policy raise this; it is a refusal, and
            # the model should route around it rather than retry.
            return ToolResult.failure(
                call, f"Permission denied: {exc}", error="permission_denied",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the loop must survive any tool
            return ToolResult.failure(
                call, f"{spec.id} failed: {exc}", error=type(exc).__name__,
            )

        if not isinstance(outcome, ToolResult):
            raise ToolError(
                f"tool {spec.id!r} returned {type(outcome).__name__}, expected ToolResult"
            )
        return outcome
