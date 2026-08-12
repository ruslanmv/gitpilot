# gitpilot/toolkit/mcp.py
"""MCP servers as canonical tools — Batch V4-H1.

GitPilot had two MCP paths that never met.

:mod:`gitpilot.mcp_client` is a real JSON-RPC client — stdio, HTTP and SSE
transports, ``initialize``/``tools/list``/``tools/call``, and it captures each
tool's ``inputSchema``. It then threw that schema away: its only consumer was
``to_crewai_tools``, which wrapped every tool as a single ``params: str`` CrewAI
callable. The model was handed a JSON blob parameter and told to guess.

:mod:`gitpilot.mcp_tools_bridge` is a bespoke HTTP POST of ``{method:
'tools/call'}`` at a configured endpoint. It has no schema at all — it *synthesises*
descriptions from tool names — but it does have the layer that matters
operationally: the admin store, per-server enable/disable, ``tool_overrides``, and
``classify_risk``.

This module keeps the second layer and gives it the first transport. Each enabled
tool becomes a registry entry ``mcp.<server>.<tool>`` carrying the server's real
``inputSchema``, so every dialect gets it for free: NATIVE sends it as a function
schema, REACT_TEXT renders it in the tool manual, LITE reduces it to a signature.
The schema was always there; nothing consumed it.

Three things it is careful about.

**A mutation asks.** ``classify_risk`` already sorts names into low/medium/high;
medium and high become :attr:`Risk.APPROVAL` and :attr:`Risk.HIGH_RISK`, so the
policy engine prompts before an MCP tool changes anything. Mutating tools also
declare :attr:`Effect.EXTERNAL_WRITE`, which is what makes a read-only run refuse
them — "wrote a file" and "ran git" are both wrong about a Postgres UPDATE.

**A disabled server is absent, not denied.** A tool withheld from ``schemas()``
costs nothing; a tool offered and then refused costs a turn. ``tool_overrides``,
``enabled``, ``installed`` and ``orphan`` are all read before registration.

**Names are sanitised, and a name that cannot be sanitised is skipped loudly.** MCP
tool names come from someone else's server, and the registry's ids are a namespace
GitPilot has to keep parseable. A tool called ``search-code`` registers as
``mcp.<server>.search_code``; the wire name is kept for the call.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..tool_def_pruner import prune_descriptors
from .registry import Effect, Risk, ToolCall, ToolExecutionContext, ToolRegistry, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

FLAG_MCP_TOOLS = "mcp_tools"

#: The namespace every MCP tool lands in, so a capability mask can grant or
#: withhold the whole family (``mcp.*``) or one server (``mcp.postgres.*``).
NAMESPACE = "mcp"

#: MCP tools are the last thing to survive a schema budget: a project's own files
#: and shell matter more than a third-party server's twentieth helper.
DEFAULT_PRIORITY = 90

#: Tool output longer than this is truncated in the observation.  The full text is
#: still in the journal; a 400 KB query result would otherwise evict the task.
MAX_RESULT_CHARS = 20_000

_SEGMENT_RE = re.compile(r"[^a-z0-9_]+")
_LEADING_RE = re.compile(r"^[^a-z]+")


@dataclass
class MCPRegistration:
    """What one registration pass did."""

    registered: List[str] = field(default_factory=list)
    #: Qualified wire name → why it was not registered.
    skipped: Dict[str, str] = field(default_factory=dict)
    servers: List[str] = field(default_factory=list)
    #: Servers that could not be reached at all.
    unreachable: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "registered": list(self.registered),
            "skipped": dict(self.skipped),
            "servers": list(self.servers),
            "unreachable": dict(self.unreachable),
        }


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def sanitise_segment(name: str) -> str:
    """One id segment from an arbitrary MCP name, or ``""`` if nothing survives."""
    lowered = _SEGMENT_RE.sub("_", str(name).strip().lower()).strip("_")
    lowered = _LEADING_RE.sub("", lowered)
    return lowered


def canonical_id(server: str, tool: str) -> str:
    """``mcp.<server>.<tool>``, or ``""`` when either name cannot be sanitised."""
    server_segment, tool_segment = sanitise_segment(server), sanitise_segment(tool)
    if not server_segment or not tool_segment:
        return ""
    return f"{NAMESPACE}.{server_segment}.{tool_segment}"


def server_capability(server: str) -> str:
    """The capability a server's tools share, so a mask can grant one server."""
    segment = sanitise_segment(server)
    return f"{NAMESPACE}.{segment}" if segment else NAMESPACE


# ---------------------------------------------------------------------------
# Risk and effects
# ---------------------------------------------------------------------------


def risk_for(tool_name: str) -> Risk:
    """The registry risk for an MCP tool name.

    ``classify_risk`` is the existing keyword classifier the admin UI already
    shows users; reusing it means the risk badge in the UI and the approval the
    engine demands come from one function rather than two opinions.
    """
    try:
        from ..mcp_admin_api import RISK_HIGH, RISK_MEDIUM, classify_risk
    except Exception:  # noqa: BLE001 - without the classifier, assume the worst
        logger.debug("mcp_admin_api unavailable; treating %s as approval", tool_name)
        return Risk.APPROVAL

    risk = classify_risk(tool_name)
    if risk == RISK_HIGH:
        return Risk.HIGH_RISK
    if risk == RISK_MEDIUM:
        return Risk.APPROVAL
    return Risk.SAFE


def effects_for(tool_name: str) -> frozenset[Effect]:
    """What an MCP tool touches.

    Always ``NETWORK`` — a call leaves the process even over stdio, in the sense
    that matters to a policy: GitPilot does not control what happens next. A
    mutating name additionally declares ``EXTERNAL_WRITE``, which is what makes a
    read-only run refuse it.
    """
    effects = {Effect.NETWORK}
    if risk_for(tool_name) is not Risk.SAFE:
        effects.add(Effect.EXTERNAL_WRITE)
    return frozenset(effects)


def is_mutation(tool_name: str) -> bool:
    return risk_for(tool_name) is not Risk.SAFE


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def normalise_schema(schema: Any, *, tool_id: str = "") -> Dict[str, Any]:
    """A ``params_schema`` the registry will accept.

    An MCP server is entitled to send a schema GitPilot did not expect — no
    ``type``, a bare list of names, or nothing at all. ``ToolSpec`` requires an
    object schema, so a missing or malformed one becomes an empty object rather
    than an exception: a tool with an unknown signature is still callable, and
    refusing to register it would hide a working tool over a metadata quibble.
    """
    if not isinstance(schema, Mapping):
        if schema is not None:
            logger.debug("%s: inputSchema is %s, not an object", tool_id, type(schema).__name__)
        return {"type": "object", "properties": {}}

    normalised = dict(schema)
    if normalised.get("type") != "object":
        normalised["type"] = "object"
    properties = normalised.get("properties")
    if not isinstance(properties, Mapping):
        normalised["properties"] = {}
    required = normalised.get("required")
    if required is not None and not isinstance(required, (list, tuple)):
        normalised.pop("required")
    return normalised


def describe(tool_name: str, description: str, server: str) -> str:
    """The description the model sees.

    Prefers the server's own text. Where there is none, says which server the tool
    came from rather than inventing behaviour from the name — the bridge's
    ``_describe`` guessed, and a guess in a tool description is a guess the model
    will act on.
    """
    text = (description or "").strip()
    if text:
        return text
    return f"Tool {tool_name!r} from the {server!r} MCP server. No description provided."


# ---------------------------------------------------------------------------
# Building specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCPToolBinding:
    """One MCP tool, resolved to a registry spec plus how to call it."""

    spec: ToolSpec
    server: str
    #: The name to send on the wire, which may differ from the sanitised id.
    wire_name: str

    @property
    def qualified(self) -> str:
        return f"{self.server}.{self.wire_name}"


def build_binding(
    server: str,
    tool_name: str,
    *,
    description: str = "",
    input_schema: Any = None,
    priority: int = DEFAULT_PRIORITY,
) -> Optional[MCPToolBinding]:
    """A binding for one tool, or ``None`` when its name cannot be a tool id."""
    tool_id = canonical_id(server, tool_name)
    if not tool_id:
        return None
    return MCPToolBinding(
        spec=ToolSpec(
            id=tool_id,
            title=f"{server}: {tool_name}",
            description=describe(tool_name, description, server),
            params_schema=normalise_schema(input_schema, tool_id=tool_id),
            capability=server_capability(server),
            risk=risk_for(tool_name),
            effects=effects_for(tool_name),
            # A third-party server's schemas are the first thing to drop from a
            # small model's budget, and its arguments are the least likely to
            # survive a names-only rendering intact.
            lite_compatible=False,
            priority=priority,
        ),
        server=server,
        wire_name=tool_name,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def tool_is_enabled(overrides: Mapping[str, Any], tool_name: str) -> bool:
    """Whether ``tool_overrides`` permits this tool.

    An override that is explicitly ``False`` withholds it. An *absent* override
    means the admin store has never been asked about this tool, which happens for
    every tool discovered from a server rather than listed in the catalogue — those
    default to enabled, because a server the user installed and enabled is a server
    whose tools they wanted.
    """
    override = overrides.get(tool_name)
    return override is not False


def register_bindings(
    registry: ToolRegistry,
    bindings: Sequence[MCPToolBinding],
    caller: Any,
) -> MCPRegistration:
    """Register *bindings*, skipping duplicates rather than raising.

    Two servers may legitimately expose a ``query`` tool; the ids differ because
    the server name is in them. A genuine duplicate means the same server was
    registered twice, which is a caller bug worth a warning and not worth a crash
    mid-session.
    """
    result = MCPRegistration()
    for binding in bindings:
        if binding.spec.id in registry:
            result.skipped[binding.qualified] = "already registered"
            logger.warning("MCP tool %s is already registered; skipping", binding.spec.id)
            continue
        registry.register(binding.spec, _handler_for(binding, caller))
        result.registered.append(binding.spec.id)
        if binding.server not in result.servers:
            result.servers.append(binding.server)
    return result


def _handler_for(binding: MCPToolBinding, caller: Any) -> Any:
    """The registry handler for one binding.

    Bound through a factory's parameters rather than an inline closure — the same
    late-binding trap F3 fixed in ``to_crewai_tools``, where every generated wrapper
    called whichever tool the loop had visited last.
    """

    async def handle(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
        try:
            payload = await caller(binding.server, binding.wire_name, dict(call.arguments))
        except Exception as exc:  # noqa: BLE001 - a server's failure is a tool failure
            logger.info("MCP call %s failed: %s", binding.spec.id, exc)
            return ToolResult.failure(
                call,
                f"{binding.spec.id} failed: {exc}",
                error=f"{binding.spec.id} failed: {exc}",
            )

        text = payload if isinstance(payload, str) else str(payload)
        truncated = len(text) > MAX_RESULT_CHARS
        return ToolResult.success(
            call,
            text[:MAX_RESULT_CHARS] + ("\n… (truncated)" if truncated else ""),
            data={"server": binding.server, "tool": binding.wire_name},
        )

    return handle


async def register_client(
    registry: ToolRegistry,
    client: Any,
    *,
    servers: Optional[Sequence[str]] = None,
    overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
    include_mutation: bool = True,
) -> MCPRegistration:
    """Connect each server through :class:`~gitpilot.mcp_client.MCPClient` and register.

    A server that will not connect is recorded and skipped: one unreachable MCP
    server must not take out the run, and pretending its tools exist would produce
    a model calling something that cannot answer.
    """
    result = MCPRegistration()
    names = list(servers) if servers is not None else list(client.list_servers())
    per_server_overrides = overrides or {}

    async def call(server: str, tool: str, arguments: Dict[str, Any]) -> Any:
        conn = await client.connect(server)
        return await client.call_tool(conn, tool, arguments)

    for server in names:
        try:
            conn = await client.connect(server)
        except Exception as exc:  # noqa: BLE001 - an unreachable server is data
            logger.warning("could not connect to MCP server %r: %s", server, exc)
            result.unreachable[server] = str(exc)
            continue

        server_overrides = per_server_overrides.get(server) or {}
        bindings: List[MCPToolBinding] = []
        for tool in getattr(conn, "tools", []) or []:
            wire_name = getattr(tool, "name", "")
            qualified = f"{server}.{wire_name}"

            if not tool_is_enabled(server_overrides, wire_name):
                result.skipped[qualified] = "disabled by tool_overrides"
                continue
            if not include_mutation and is_mutation(wire_name):
                result.skipped[qualified] = "mutation withheld"
                continue

            binding = build_binding(
                server, wire_name,
                description=getattr(tool, "description", "") or "",
                input_schema=getattr(tool, "input_schema", None),
            )
            if binding is None:
                result.skipped[qualified] = "name cannot be a tool id"
                logger.warning(
                    "MCP tool %r on %r has no usable id; skipping", wire_name, server,
                )
                continue
            bindings.append(binding)

        registered = register_bindings(registry, bindings, call)
        result.registered.extend(registered.registered)
        result.skipped.update(registered.skipped)
        for name in registered.servers:
            if name not in result.servers:
                result.servers.append(name)

    return result


def store_overrides(
    store: Any = None,
) -> Tuple[Dict[str, Dict[str, Any]], Optional[List[str]]]:
    """``(overrides, enabled server ids)`` from the admin store.

    This is the layer the bespoke bridge had and the JSON-RPC client did not:
    which servers the user installed and enabled, and which of their tools they
    switched off. Only the *transport* retires.

    The second element is ``None`` when the store has **no opinion** — it is absent,
    or it knows about no servers at all — and a list otherwise, *including the empty
    list*. The distinction is load-bearing: "the user disabled every server" and
    "there is no admin store on this install" must not produce the same behaviour,
    and conflating them registered a disabled server's tools anyway.
    """
    try:
        from ..mcp_admin_api import MCPStore
    except Exception:  # noqa: BLE001 - no store ⇒ no opinion
        logger.debug("mcp_admin_api unavailable; no server toggles applied")
        return {}, None

    source: Any = store or MCPStore()
    snapshot: Any = source.snapshot()
    servers = getattr(snapshot, "servers", None) or {}
    if not servers:
        return {}, None

    overrides: Dict[str, Dict[str, Any]] = {}
    enabled: List[str] = []
    for server_id, server in servers.items():
        if not getattr(server, "installed", False) or not getattr(server, "enabled", False):
            continue
        if getattr(server, "orphan", False):
            # Kept in the store, but its tools are not advertised until the user
            # re-attaches it — the bridge's rule, preserved.
            continue
        enabled.append(server_id)
        overrides[server_id] = dict(getattr(server, "tool_overrides", None) or {})
    return overrides, enabled


#: How a store endpoint's URL maps to an MCP transport.  A gateway that terminates
#: MCP over SSE advertises it in the path; everything else is streamable HTTP.
_SSE_MARKERS = ("/sse", "/sse/", "/events")


def server_config_from_state(server_id: str, state: Any) -> Optional[Dict[str, Any]]:
    """An :class:`~gitpilot.mcp_client.MCPServerConfig` dict from a store entry.

    This is the step that was missing until Batch V4-H5, and the gap it left was the
    whole point of the feature: a user adds a Postgres MCP server in the VS Code
    settings panel, GitPilot writes it into the **admin store** — endpoint, auth env
    var, tool toggles — and :class:`~gitpilot.mcp_client.MCPClient` takes its
    transports from ``.gitpilot/mcp.json``, which the UI never touches. So the server
    was enabled, listed, and unreachable: ``register_from_store`` skipped it as
    "enabled in the store but not configured for a transport".

    Deriving the transport from the endpoint closes the loop. Adding a server in the
    UI is now enough for its tools to reach the model as ``mcp.<server>.<tool>`` with
    the server's own ``inputSchema`` — including MCP Context Forge, which is one
    entry here and federates everything behind it.
    """
    endpoint = str(getattr(state, "endpoint", "") or "").strip()
    if not endpoint:
        return None

    lowered = endpoint.lower()
    transport = "sse" if any(marker in lowered for marker in _SSE_MARKERS) else "http"

    token = ""
    try:
        from ..mcp_tools_bridge import _resolve_token

        # The bridge's token resolution — per-server env var, then MCP_AUTH_TOKEN —
        # is part of the layer H1 kept. Reusing it means the UI's "auth env var"
        # field means the same thing on both paths.
        token = _resolve_token(state)
    except Exception as exc:  # noqa: BLE001 - an unauthenticated gateway is legitimate
        logger.debug("could not resolve a token for %s (%s)", server_id, exc)

    config: Dict[str, Any] = {
        "name": server_id,
        "transport": transport,
        "url": endpoint,
    }
    if token:
        config["auth_token"] = token
        config["headers"] = {"Authorization": f"Bearer {token}"}
    return config


def attach_store_servers(client: Any, store: Any = None) -> List[str]:
    """Give *client* a transport for every enabled store server it lacks one for.

    Returns the server ids it added. A server already configured in ``mcp.json`` is
    left alone: an explicit file beats a derived endpoint, because someone wrote the
    file on purpose.
    """
    _overrides, enabled = store_overrides(store)
    if not enabled:
        return []

    try:
        from ..mcp_admin_api import MCPStore
    except Exception:  # noqa: BLE001
        return []

    source: Any = store or MCPStore()
    snapshot: Any = source.snapshot()
    states = getattr(snapshot, "servers", None) or {}
    known = set(client.list_servers())

    added: List[str] = []
    for server_id in enabled:
        if server_id in known:
            continue
        config = server_config_from_state(server_id, states.get(server_id))
        if config is None:
            logger.info(
                "MCP server %r is enabled but has no endpoint; nothing to connect to",
                server_id,
            )
            continue
        try:
            from ..mcp_client import MCPServerConfig

            client.add_server(MCPServerConfig.from_dict(config))
            added.append(server_id)
        except Exception as exc:  # noqa: BLE001 - one unusable entry is not the run
            logger.warning("could not configure MCP server %r: %s", server_id, exc)
    return added


async def register_from_store(
    registry: ToolRegistry,
    *,
    client: Any = None,
    store: Any = None,
    include_mutation: bool = True,
    workspace: Optional[Any] = None,
) -> MCPRegistration:
    """The whole path: admin store for *what*, ``MCPClient`` for *how*."""
    overrides, enabled = store_overrides(store)

    if client is None:
        from ..mcp_client import MCPClient

        client = MCPClient()
        if workspace is not None:
            from pathlib import Path

            client.load_config(Path(workspace) / ".gitpilot")

    # A server the user added in the settings panel lives in the store, not in
    # `mcp.json`. Give it a transport before asking what the client knows about.
    attach_store_servers(client, store)

    known = set(client.list_servers())
    if enabled is None:
        # No admin store on this install: every configured server is a server the
        # user wrote into `mcp.json`, which is consent enough.
        targets = list(known)
    else:
        # Intersect: a server in the store that has no transport config cannot be
        # connected to, and a configured server the user disabled must stay off.
        targets = [name for name in enabled if name in known]

    result = await register_client(
        registry, client,
        servers=targets,
        overrides=overrides,
        include_mutation=include_mutation,
    )
    for name in (enabled or []):
        if name not in known:
            result.skipped[name] = "enabled in the store but not configured for a transport"
    return result


# ---------------------------------------------------------------------------
# The schema budget
# ---------------------------------------------------------------------------


def prune_for_profile(
    registry: ToolRegistry,
    profile: Any,
    *,
    policy: Any = None,
    enabled: Optional[bool] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Drop MCP tools that do not fit the model's schema budget.

    ``tool_def_pruner`` has existed since Phase 2 with one caller — the legacy
    bridge — and it prunes on a *mode's* tool policy. What it never knew is how
    many schemas the model can actually hold: :class:`ModelProfile` knows, and a
    16-schema model offered thirty MCP tools spends its window on a catalogue.

    Returns the tool ids to withhold and a report. Applied by the caller rather
    than mutating the registry, because "which tools this run may see" is a
    per-request question and the registry outlives the request.
    """
    mcp_specs = [
        spec for spec in registry.specs()
        if spec.namespace == NAMESPACE
    ]
    if not mcp_specs:
        return [], {"kept": 0, "dropped": 0, "reasons": {}}

    descriptors = [_PruneView.of(spec) for spec in mcp_specs]
    by_key = {(view.server_id, view.name): view.tool_id for view in descriptors}
    kept, report = prune_descriptors(descriptors, policy=policy, enabled=enabled)
    kept_ids = {
        by_key[(view.server_id, view.name)]
        for view in kept
        if (view.server_id, view.name) in by_key
    }

    budget = int(getattr(profile, "max_tool_schemas", 0) or 0)
    over_budget: List[str] = []
    if budget:
        # The non-MCP tools have first claim: a project's own files and shell
        # matter more than a third-party server's twentieth helper.
        others = len([spec for spec in registry.specs() if spec.namespace != NAMESPACE])
        room = max(budget - others, 0)
        surviving = [spec for spec in mcp_specs if spec.id in kept_ids]
        surviving.sort(key=lambda spec: (spec.priority, spec.id))
        over_budget = [spec.id for spec in surviving[room:]]

    withheld = [spec.id for spec in mcp_specs if spec.id not in kept_ids] + over_budget
    return withheld, {
        "kept": len(kept_ids) - len(over_budget),
        "dropped": len(withheld),
        "reasons": {
            **dict(report.reason_counts or {}),
            **({"schema-budget": len(over_budget)} if over_budget else {}),
        },
    }


@dataclass
class _PruneView:
    """A :class:`ToolSpec` in the shape ``prune_descriptors`` expects.

    The pruner predates the registry and speaks ``(server_id, name)``; adapting
    here keeps one pruning implementation rather than a second one that agrees with
    it until it does not.
    """

    name: str
    server_id: str
    tool_id: str

    @classmethod
    def of(cls, spec: ToolSpec) -> "_PruneView":
        parts = spec.id.split(".", 2)
        return cls(
            name=parts[2] if len(parts) > 2 else spec.id,
            server_id=parts[1] if len(parts) > 1 else "",
            tool_id=spec.id,
        )
