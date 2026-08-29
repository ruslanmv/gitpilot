"""MCP servers as canonical tools — Batch V4-H1.

The point of this batch is that a schema which was already being captured finally
reaches the model. So the load-bearing tests are the round-trips: a server's
``inputSchema`` in, a NATIVE function schema and a REACT_TEXT manual entry out —
because that is the thing two MCP code paths were each half of.

The rest guard the operational layer the bespoke bridge had and the JSON-RPC client
did not: a disabled server's tools are *absent* rather than denied, and a mutating
tool asks.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from gitpilot.agent.policy import CapabilityMask, PolicyEngine, ResolvedPolicy
from gitpilot.permissions import PermissionMode
from gitpilot.toolkit import ToolCall, ToolRegistry
from gitpilot.toolkit.mcp import (
    MAX_RESULT_CHARS,
    NAMESPACE,
    build_binding,
    canonical_id,
    effects_for,
    is_mutation,
    normalise_schema,
    prune_for_profile,
    register_client,
    register_from_store,
    risk_for,
    sanitise_segment,
    server_capability,
    store_overrides,
    tool_is_enabled,
)
from gitpilot.toolkit.registry import Effect, Risk

QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "The statement to run."},
        "timeout_ms": {"type": "integer"},
    },
    "required": ["sql"],
}


class FakeTool(SimpleNamespace):
    pass


def _tool(name: str, *, description: str = "", schema=None) -> FakeTool:
    return FakeTool(name=name, description=description, input_schema=schema)


class FakeClient:
    """An ``MCPClient`` with the transport replaced and nothing else."""

    def __init__(self, servers: dict, *, unreachable=()) -> None:
        self._servers = servers
        self._unreachable = set(unreachable)
        self.calls: list[tuple[str, str, dict]] = []
        self.connects: list[str] = []

    def list_servers(self):
        return list(self._servers)

    def add_server(self, config):
        """``MCPClient.add_server``. Present on the fake because the store path calls
        it, and a fake missing it fails as a caught AttributeError rather than loudly."""
        self._servers.setdefault(config.name, [])

    async def connect(self, name: str):
        self.connects.append(name)
        if name in self._unreachable:
            raise ConnectionError(f"{name} is not listening")
        return SimpleNamespace(tools=self._servers[name])

    async def call_tool(self, conn, tool_name: str, params):
        self.calls.append((getattr(conn, "name", ""), tool_name, dict(params or {})))
        return f"result of {tool_name}({params})"


def _registry(client, **kwargs) -> tuple[ToolRegistry, object]:
    registry = ToolRegistry()
    result = asyncio.run(register_client(registry, client, **kwargs))
    return registry, result


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


class TestNaming:
    @pytest.mark.parametrize(("server", "tool", "expected"), [
        ("postgres", "query", "mcp.postgres.query"),
        ("Postgres", "Query", "mcp.postgres.query"),
        ("github-mcp", "search-code", "mcp.github_mcp.search_code"),
        ("db", "list.tables", "mcp.db.list_tables"),
        ("db", "  spaced  name ", "mcp.db.spaced_name"),
    ])
    def test_a_wire_name_becomes_a_tool_id(self, server, tool, expected):
        """The registry's ids are a namespace GitPilot has to keep parseable, and
        MCP names come from someone else's server."""
        assert canonical_id(server, tool) == expected

    @pytest.mark.parametrize(("server", "tool"), [
        ("", "query"), ("postgres", ""), ("!!!", "query"), ("postgres", "###"),
        ("123", "query"),
    ])
    def test_a_name_with_nothing_usable_in_it_has_no_id(self, server, tool):
        assert canonical_id(server, tool) == ""

    def test_the_id_the_registry_accepts_is_the_id_we_build(self):
        binding = build_binding("github-mcp", "search-code")

        assert binding is not None
        assert binding.spec.id == "mcp.github_mcp.search_code"
        assert binding.wire_name == "search-code", "the wire name is what we call"

    def test_a_server_shares_one_capability(self):
        """So a mask can grant one server without naming its every tool."""
        assert server_capability("postgres") == "mcp.postgres"
        assert build_binding("postgres", "query").spec.capability == "mcp.postgres"

    def test_sanitising_leaves_nothing_that_is_not_snake_case(self):
        assert sanitise_segment("Foo-Bar.Baz") == "foo_bar_baz"


# ---------------------------------------------------------------------------
# The schema round-trip
# ---------------------------------------------------------------------------


class TestSchemaReachesTheModel:
    def test_the_servers_schema_survives_into_the_spec(self):
        """The whole batch in one assertion: the schema was always captured, and
        nothing consumed it."""
        binding = build_binding("postgres", "query", input_schema=QUERY_SCHEMA)

        assert binding.spec.params_schema["properties"]["sql"]["type"] == "string"
        assert binding.spec.params_schema["required"] == ["sql"]

    def test_it_round_trips_through_the_native_dialect(self, tmp_path):
        from gitpilot.agent.model_profile import resolve_profile
        from gitpilot.toolkit.registry import SchemaProfile

        registry, _result = _registry(FakeClient({
            "postgres": [_tool("query", description="Run SQL", schema=QUERY_SCHEMA)],
        }))
        profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "p.json")

        render = registry.render(profile=SchemaProfile(
            max_tool_schemas=profile.max_tool_schemas, schema_verbosity="full",
        ))
        by_name = {schema["name"]: schema for schema in render.schemas}

        assert "mcp.postgres.query" in by_name
        schema = by_name["mcp.postgres.query"]["parameters"]
        assert "sql" in schema["properties"]
        assert schema["required"] == ["sql"]

    def test_it_round_trips_through_the_compact_rendering(self):
        """REACT_TEXT renders the same specs at ``compact`` verbosity, so the
        argument names have to survive that too — a manual entry naming no
        arguments is a tool the model calls with none."""
        from gitpilot.toolkit.registry import SchemaProfile

        registry, _result = _registry(FakeClient({
            "postgres": [_tool("query", description="Run SQL", schema=QUERY_SCHEMA)],
        }))

        render = registry.render(profile=SchemaProfile(
            max_tool_schemas=16, schema_verbosity="compact",
        ))
        rendered = str(render.schemas)

        assert "mcp.postgres.query" in rendered
        assert "sql" in rendered

    def test_a_missing_schema_is_an_empty_object_not_a_refusal(self):
        """A tool with an unknown signature is still callable; refusing to register
        it would hide a working tool over a metadata quibble."""
        binding = build_binding("postgres", "ping", input_schema=None)

        assert binding.spec.params_schema == {"type": "object", "properties": {}}

    @pytest.mark.parametrize("schema", [
        None, [], "an object", 42, {"properties": "nope"}, {"type": "string"},
    ])
    def test_a_malformed_schema_is_repaired_rather_than_raising(self, schema):
        normalised = normalise_schema(schema)

        assert normalised["type"] == "object"
        assert isinstance(normalised["properties"], dict)

    def test_a_non_list_required_is_dropped(self):
        assert "required" not in normalise_schema(
            {"type": "object", "properties": {}, "required": "sql"},
        )

    def test_the_servers_description_is_preferred_over_a_guess(self):
        """The bridge guessed descriptions from names, and a guess in a tool
        description is a guess the model will act on."""
        with_text = build_binding("postgres", "query", description="Run SQL, carefully")
        without = build_binding("postgres", "query")

        assert with_text.spec.description == "Run SQL, carefully"
        assert "No description provided" in without.spec.description
        assert "postgres" in without.spec.description


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


class TestRiskMapping:
    @pytest.mark.parametrize(("name", "expected"), [
        ("query", Risk.SAFE),
        ("list_tables", Risk.SAFE),
        ("update_row", Risk.APPROVAL),
        ("create_issue", Risk.APPROVAL),
        ("delete_table", Risk.HIGH_RISK),
        ("drop_database", Risk.HIGH_RISK),
    ])
    def test_classify_risk_becomes_tool_risk(self, name, expected):
        """One classifier for the badge the admin UI shows and the approval the
        engine demands, rather than two opinions."""
        assert risk_for(name) is expected

    def test_a_mutating_tool_declares_an_external_write(self):
        """"Wrote a file" and "ran git" are both wrong about a Postgres UPDATE, and
        calling it read-only would let a read-only run mutate production data."""
        assert Effect.EXTERNAL_WRITE in effects_for("update_row")
        assert Effect.EXTERNAL_WRITE not in effects_for("query")

    def test_every_mcp_tool_touches_the_network(self):
        assert Effect.NETWORK in effects_for("query")

    def test_a_mutation_asks_before_it_runs(self):
        registry, _result = _registry(FakeClient({
            "db": [_tool("query"), _tool("update_row")],
        }))
        engine = PolicyEngine(ResolvedPolicy(
            mode=PermissionMode.NORMAL, capabilities=CapabilityMask.permissive(),
        ))

        allowed = asyncio.run(engine.authorize(
            ToolCall(id="1", tool="mcp.db.query", arguments={}),
            _ctx(registry),
        ))
        asked = asyncio.run(engine.authorize(
            ToolCall(id="2", tool="mcp.db.update_row", arguments={}),
            _ctx(registry),
        ))

        assert allowed.verdict == "allow"
        assert asked.verdict == "ask"

    def test_a_read_only_run_refuses_a_mutating_mcp_tool(self):
        registry, _result = _registry(FakeClient({"db": [_tool("delete_table")]}))
        engine = PolicyEngine(ResolvedPolicy(
            mode=PermissionMode.PLAN, capabilities=CapabilityMask.permissive(),
        ))

        decision = asyncio.run(engine.authorize(
            ToolCall(id="1", tool="mcp.db.delete_table", arguments={}), _ctx(registry),
        ))

        assert decision.verdict == "deny"
        assert "read-only" in decision.reason

    def test_a_read_only_run_still_permits_a_query(self):
        registry, _result = _registry(FakeClient({"db": [_tool("query")]}))
        engine = PolicyEngine(ResolvedPolicy(
            mode=PermissionMode.PLAN, capabilities=CapabilityMask.permissive(),
        ))

        decision = asyncio.run(engine.authorize(
            ToolCall(id="1", tool="mcp.db.query", arguments={}), _ctx(registry),
        ))

        assert decision.verdict == "allow"


def _ctx(registry):
    """The shape both the registry and the policy engine want.

    ``registry.execute`` takes a :class:`ToolExecutionContext`; ``PolicyEngine`` looks
    for one at ``ctx.tool_context`` because it is handed the *run* context. This
    object is both, which is what the real ``AgentContext`` amounts to here.
    """
    from gitpilot.toolkit import ToolExecutionContext

    tool_context = ToolExecutionContext(
        session_id="s", run_id="r", extras={"registry": registry},
    )
    tool_context.tool_context = tool_context   # type: ignore[attr-defined]
    return tool_context


# ---------------------------------------------------------------------------
# Toggles
# ---------------------------------------------------------------------------


class TestToggles:
    def test_a_disabled_tool_is_absent_from_the_registry(self):
        """A tool withheld from ``schemas()`` costs nothing; a tool offered and then
        refused costs a turn."""
        registry, result = _registry(
            FakeClient({"db": [_tool("query"), _tool("drop_table")]}),
            overrides={"db": {"drop_table": False}},
        )

        assert "mcp.db.query" in registry
        assert "mcp.db.drop_table" not in registry
        assert result.skipped["db.drop_table"] == "disabled by tool_overrides"

    def test_an_absent_override_means_enabled(self):
        """A server the user installed and enabled is a server whose tools they
        wanted, including ones discovered rather than catalogued."""
        assert tool_is_enabled({}, "query") is True
        assert tool_is_enabled({"other": False}, "query") is True
        assert tool_is_enabled({"query": False}, "query") is False
        assert tool_is_enabled({"query": True}, "query") is True

    def test_mutations_can_be_withheld_wholesale(self):
        registry, result = _registry(
            FakeClient({"db": [_tool("query"), _tool("update_row")]}),
            include_mutation=False,
        )

        assert "mcp.db.query" in registry
        assert "mcp.db.update_row" not in registry
        assert result.skipped["db.update_row"] == "mutation withheld"

    def test_an_unreachable_server_is_recorded_not_raised(self):
        """One unreachable MCP server must not take out the run, and pretending its
        tools exist would produce a model calling something that cannot answer."""
        registry, result = _registry(
            FakeClient({"up": [_tool("query")], "down": [_tool("query")]},
                       unreachable=["down"]),
        )

        assert "mcp.up.query" in registry
        assert "down" in result.unreachable
        assert result.servers == ["up"]

    def test_a_name_that_cannot_be_an_id_is_skipped_loudly(self, caplog):
        with caplog.at_level("WARNING"):
            registry, result = _registry(FakeClient({"db": [_tool("###")]}))

        assert len(registry) == 0
        assert result.skipped["db.###"] == "name cannot be a tool id"
        assert "no usable id" in caplog.text

    def test_two_servers_may_expose_the_same_tool_name(self):
        registry, _result = _registry(FakeClient({
            "a": [_tool("query")], "b": [_tool("query")],
        }))

        assert "mcp.a.query" in registry
        assert "mcp.b.query" in registry

    def test_registering_the_same_server_twice_warns_rather_than_crashing(self, caplog):
        client = FakeClient({"db": [_tool("query")]})
        registry = ToolRegistry()

        asyncio.run(register_client(registry, client))
        with caplog.at_level("WARNING"):
            second = asyncio.run(register_client(registry, client))

        assert second.skipped["db.query"] == "already registered"
        assert len(registry) == 1

    def test_only_the_named_servers_are_visited(self):
        client = FakeClient({"a": [_tool("query")], "b": [_tool("query")]})

        _registry_, _result = ToolRegistry(), asyncio.run(
            register_client(ToolRegistry(), client, servers=["a"]),
        )

        assert client.connects == ["a"]


# ---------------------------------------------------------------------------
# Calling
# ---------------------------------------------------------------------------


class TestCalling:
    def test_a_call_reaches_the_right_tool(self):
        """F3's late-binding trap, in the new layer: every wrapper carried the right
        name and called whichever tool the loop visited last."""
        client = FakeClient({"db": [_tool("query"), _tool("explain"), _tool("ping")]})
        registry, _result = _registry(client)

        for tool_id in ("mcp.db.query", "mcp.db.explain", "mcp.db.ping"):
            asyncio.run(registry.execute(
                ToolCall(id="1", tool=tool_id, arguments={"x": 1}), _ctx(registry),
            ))

        assert [name for _server, name, _args in client.calls] == ["query", "explain", "ping"]

    def test_the_wire_name_is_sent_not_the_sanitised_one(self):
        client = FakeClient({"db": [_tool("search-code")]})
        registry, _result = _registry(client)

        asyncio.run(registry.execute(
            ToolCall(id="1", tool="mcp.db.search_code", arguments={}), _ctx(registry),
        ))

        assert client.calls[0][1] == "search-code"

    def test_arguments_are_passed_through(self):
        client = FakeClient({"db": [_tool("query")]})
        registry, _result = _registry(client)

        asyncio.run(registry.execute(
            ToolCall(id="1", tool="mcp.db.query", arguments={"sql": "SELECT 1"}),
            _ctx(registry),
        ))

        assert client.calls[0][2] == {"sql": "SELECT 1"}

    def test_a_server_error_is_a_failed_tool_call_not_a_crash(self):
        class Exploding(FakeClient):
            async def call_tool(self, conn, tool_name, params):
                raise RuntimeError("the server said no")

        registry, _result = _registry(Exploding({"db": [_tool("query")]}))

        result = asyncio.run(registry.execute(
            ToolCall(id="1", tool="mcp.db.query", arguments={}), _ctx(registry),
        ))

        assert result.ok is False
        assert "the server said no" in (result.error or "")

    def test_a_huge_result_is_truncated_in_the_observation(self):
        class Chatty(FakeClient):
            async def call_tool(self, conn, tool_name, params):
                return "x" * (MAX_RESULT_CHARS + 5_000)

        registry, _result = _registry(Chatty({"db": [_tool("query")]}))

        result = asyncio.run(registry.execute(
            ToolCall(id="1", tool="mcp.db.query", arguments={}), _ctx(registry),
        ))

        assert len(result.content) <= MAX_RESULT_CHARS + 40
        assert "truncated" in result.content

    def test_the_result_records_which_server_answered(self):
        registry, _result = _registry(FakeClient({"db": [_tool("query")]}))

        result = asyncio.run(registry.execute(
            ToolCall(id="1", tool="mcp.db.query", arguments={}), _ctx(registry),
        ))

        assert result.data["server"] == "db"
        assert result.data["tool"] == "query"


# ---------------------------------------------------------------------------
# The admin store, and the schema budget
# ---------------------------------------------------------------------------


class FakeStore:
    def __init__(self, servers: dict) -> None:
        self._servers = servers

    def snapshot(self):
        return SimpleNamespace(servers=self._servers)


def _server(**kwargs):
    return SimpleNamespace(**{
        "installed": True, "enabled": True, "orphan": False, "tool_overrides": {},
        **kwargs,
    })


class TestTheStoreDecidesWhat:
    def test_only_installed_enabled_non_orphan_servers_are_targets(self):
        overrides, enabled = store_overrides(FakeStore({
            "yes": _server(),
            "not_installed": _server(installed=False),
            "off": _server(enabled=False),
            "orphaned": _server(orphan=True),
        }))

        assert enabled == ["yes"]
        assert set(overrides) == {"yes"}

    def test_tool_overrides_come_from_the_store(self):
        overrides, _enabled = store_overrides(FakeStore({
            "db": _server(tool_overrides={"drop_table": False}),
        }))

        assert overrides["db"] == {"drop_table": False}

    def test_the_store_and_the_transport_are_intersected(self):
        """A server enabled in the store with no transport config cannot be
        connected to, and saying so beats a silent absence."""
        registry = ToolRegistry()
        client = FakeClient({"db": [_tool("query")]})

        result = asyncio.run(register_from_store(
            registry, client=client,
            store=FakeStore({"db": _server(), "ghost": _server()}),
        ))

        assert "mcp.db.query" in registry
        assert "not configured for a transport" in result.skipped["ghost"]

    def test_a_store_that_enables_nothing_falls_back_to_the_configured_servers(self):
        """An install with no admin store at all still gets its ``mcp.json``."""
        registry = ToolRegistry()

        asyncio.run(register_from_store(
            registry, client=FakeClient({"db": [_tool("query")]}), store=FakeStore({}),
        ))

        assert "mcp.db.query" in registry

    def test_a_disabled_server_contributes_nothing(self):
        registry = ToolRegistry()

        asyncio.run(register_from_store(
            registry,
            client=FakeClient({"db": [_tool("query")]}),
            store=FakeStore({"db": _server(enabled=False)}),
        ))

        assert len(registry) == 0


class TestSchemaBudget:
    def _registry_with(self, count: int) -> ToolRegistry:
        return _registry(FakeClient({
            "db": [_tool(f"tool_{index}") for index in range(count)],
        }))[0]

    def test_mcp_tools_are_dropped_before_the_projects_own(self):
        """A 16-schema model offered thirty MCP tools spends its window on a
        catalogue."""
        from gitpilot.toolkit.builtins import build_default_registry

        registry = build_default_registry(include_git=False, include_forge=False)
        asyncio.run(register_client(registry, FakeClient({
            "db": [_tool(f"tool_{index}") for index in range(20)],
        })))

        withheld, report = prune_for_profile(
            registry, SimpleNamespace(max_tool_schemas=len(registry) - 5),
        )

        assert all(tool_id.startswith(f"{NAMESPACE}.") for tool_id in withheld)
        assert report["dropped"] > 0

    def test_a_generous_budget_withholds_nothing(self):
        registry = self._registry_with(3)

        withheld, report = prune_for_profile(
            registry, SimpleNamespace(max_tool_schemas=64),
        )

        assert withheld == []
        assert report["dropped"] == 0

    def test_the_report_names_the_budget_as_the_reason(self):
        registry = self._registry_with(10)

        _withheld, report = prune_for_profile(
            registry, SimpleNamespace(max_tool_schemas=4),
        )

        assert report["reasons"].get("schema-budget")

    def test_a_registry_with_no_mcp_tools_is_a_no_op(self):
        from gitpilot.toolkit.builtins import build_default_registry

        withheld, report = prune_for_profile(
            build_default_registry(include_git=False, include_forge=False),
            SimpleNamespace(max_tool_schemas=2),
        )

        assert withheld == []
        assert report == {"kept": 0, "dropped": 0, "reasons": {}}

    def test_no_profile_budget_means_no_budget_pruning(self):
        registry = self._registry_with(10)

        withheld, _report = prune_for_profile(
            registry, SimpleNamespace(max_tool_schemas=0),
        )

        assert withheld == []

    def test_mcp_tools_are_withheld_from_a_lite_run(self):
        """A third-party server's arguments are the least likely to survive a
        names-only rendering intact."""
        registry = self._registry_with(2)

        render = registry.render(lite_only=True)

        assert render.schemas == []
        assert {tool.id for tool in render.dropped} == {"mcp.db.tool_0", "mcp.db.tool_1"}


# ---------------------------------------------------------------------------
# Through the runner, and the bridge's retirement
# ---------------------------------------------------------------------------


class TestThroughTheRunner:
    def test_the_flag_is_off_so_a_run_registers_no_mcp_tools(self, tmp_path):
        """An MCP server is a third party, and a run that starts calling one because
        a config file exists is not a run the user asked for."""
        from gitpilot.agent.runner import FLAG_MCP_TOOLS, AgentRunner, RunRequest
        from gitpilot import flags

        flags.clear_all_overrides()
        assert flags.is_on(FLAG_MCP_TOOLS, default=False) is False

        runner = AgentRunner()
        registry = asyncio.run(runner._registry_for(   # noqa: SLF001
            RunRequest(task="t", workspace_root=tmp_path),
        ))

        assert [spec.id for spec in registry.specs() if spec.namespace == NAMESPACE] == []

    def test_with_the_flag_on_the_workspace_servers_are_registered(self, tmp_path, monkeypatch):
        from gitpilot.agent.runner import FLAG_MCP_TOOLS, AgentRunner, RunRequest
        from gitpilot import flags
        from gitpilot.toolkit import mcp as mcp_mod

        async def fake_register(registry, **kwargs):
            binding = build_binding("probe", "query", input_schema=QUERY_SCHEMA)
            return mcp_mod.register_bindings(
                registry, [binding], lambda *a, **k: None,
            )

        monkeypatch.setattr(mcp_mod, "register_from_store", fake_register)
        flags.set_override(FLAG_MCP_TOOLS, True)
        try:
            registry = asyncio.run(AgentRunner()._registry_for(   # noqa: SLF001
                RunRequest(task="t", workspace_root=tmp_path),
            ))
        finally:
            flags.clear_all_overrides()

        assert "mcp.probe.query" in registry

    def test_a_failing_registration_does_not_stop_the_run(self, tmp_path, monkeypatch, caplog):
        """An unreachable server, or a missing admin store, must not stop a run that
        was never about MCP."""
        from gitpilot.agent.runner import FLAG_MCP_TOOLS, AgentRunner, RunRequest
        from gitpilot import flags
        from gitpilot.toolkit import mcp as mcp_mod

        async def explode(registry, **kwargs):
            raise RuntimeError("no store, no servers, no luck")

        monkeypatch.setattr(mcp_mod, "register_from_store", explode)
        flags.set_override(FLAG_MCP_TOOLS, True)
        try:
            with caplog.at_level("WARNING"):
                registry = asyncio.run(AgentRunner()._registry_for(   # noqa: SLF001
                    RunRequest(task="t", workspace_root=tmp_path),
                ))
        finally:
            flags.clear_all_overrides()

        assert "fs.read" in registry, "the rest of the registry survived"
        assert "could not register MCP tools" in caplog.text


class TestTheBridgeTransportRetires:
    def test_the_transport_warns(self):
        from gitpilot import mcp_tools_bridge as bridge

        bridge._WARNED.clear()   # noqa: SLF001
        with pytest.warns(DeprecationWarning, match="V4-H1"):
            bridge.build_mcp_agent_tools(store=_FakeLoadStore({}))

    def test_it_warns_once_rather_than_per_call(self):
        """A run that calls one in a loop should not fill the log with the same
        sentence."""
        import warnings as warnings_mod

        from gitpilot import mcp_tools_bridge as bridge

        bridge._WARNED.clear()   # noqa: SLF001
        with warnings_mod.catch_warnings(record=True) as seen:
            warnings_mod.simplefilter("always")
            bridge.build_mcp_agent_tools(store=_FakeLoadStore({}))
            bridge.build_mcp_agent_tools(store=_FakeLoadStore({}))

        assert len([w for w in seen if w.category is DeprecationWarning]) == 1

    def test_the_surviving_layer_does_not_warn(self):
        """`describe_available_tools`, `_select_enabled_tools` and `_classify` are
        what the new module calls into — only the transport retires."""
        import warnings as warnings_mod

        from gitpilot import mcp_tools_bridge as bridge

        bridge._WARNED.clear()   # noqa: SLF001
        with warnings_mod.catch_warnings(record=True) as seen:
            warnings_mod.simplefilter("always")
            bridge.describe_available_tools(store=_FakeLoadStore({}))

        assert [w for w in seen if w.category is DeprecationWarning] == []

    def test_the_new_module_reuses_the_bridges_risk_classifier(self):
        """One function behind the badge the admin UI shows and the approval the
        engine demands."""
        from gitpilot.mcp_admin_api import classify_risk

        for name in ("query", "update_row", "delete_table"):
            assert (classify_risk(name) == "low") == (risk_for(name) is Risk.SAFE)
            assert is_mutation(name) is (classify_risk(name) != "low")


class _FakeLoadStore:
    def __init__(self, servers: dict) -> None:
        self._servers = servers

    def load(self):
        return SimpleNamespace(servers=self._servers)

    def snapshot(self):
        return SimpleNamespace(servers=self._servers)


# ---------------------------------------------------------------------------
# The scenario the feature exists for
# ---------------------------------------------------------------------------


class TestAddAServerInTheUiAndTheAgentCanUseIt:
    """"I add a Postgres MCP server, and the AI can query Postgres."

    This is the whole point of the MCP surface, and until Batch V4-H5 it did not
    work: the settings panel writes into the **admin store** (endpoint, auth env var,
    tool toggles) and ``MCPClient`` reads its transports from ``.gitpilot/mcp.json``,
    which the UI never touches. The server was enabled, listed in the panel, and
    unreachable — ``register_from_store`` skipped it as "enabled in the store but not
    configured for a transport".
    """

    def _store(self, **overrides):
        return _FakeLoadStore({
            "postgres": _server(
                endpoint="https://mcp.internal/postgres",
                auth_token_env="PG_MCP_TOKEN",
                **overrides,
            ),
        })

    def test_a_store_endpoint_becomes_a_transport(self):
        from gitpilot.toolkit.mcp import server_config_from_state

        config = server_config_from_state(
            "postgres", _server(endpoint="https://mcp.internal/postgres"),
        )

        assert config["name"] == "postgres"
        assert config["url"] == "https://mcp.internal/postgres"
        assert config["transport"] == "http"

    def test_an_sse_endpoint_is_recognised(self):
        """A gateway that terminates MCP over SSE says so in the path."""
        from gitpilot.toolkit.mcp import server_config_from_state

        config = server_config_from_state(
            "forge", _server(endpoint="http://127.0.0.1:4444/servers/1/sse"),
        )

        assert config["transport"] == "sse"

    def test_the_auth_env_var_from_the_ui_becomes_a_bearer_token(self, monkeypatch):
        from gitpilot.toolkit.mcp import server_config_from_state

        monkeypatch.setenv("PG_MCP_TOKEN", "s3cret")
        config = server_config_from_state(
            "postgres",
            _server(endpoint="https://mcp.internal/pg", auth_token_env="PG_MCP_TOKEN"),
        )

        assert config["auth_token"] == "s3cret"
        assert config["headers"]["Authorization"] == "Bearer s3cret"

    def test_an_unauthenticated_gateway_is_fine(self):
        from gitpilot.toolkit.mcp import server_config_from_state

        config = server_config_from_state(
            "local", _server(endpoint="http://127.0.0.1:8765"),
        )

        assert "auth_token" not in config

    def test_a_server_with_no_endpoint_has_nothing_to_connect_to(self):
        from gitpilot.toolkit.mcp import server_config_from_state

        assert server_config_from_state("empty", _server(endpoint="")) is None
        assert server_config_from_state("missing", None) is None

    def test_attaching_gives_the_client_a_server_it_did_not_have(self):
        from gitpilot.toolkit.mcp import attach_store_servers

        client = FakeClient({})
        added = attach_store_servers(client, self._store())

        assert added == ["postgres"]

    def test_a_server_already_in_mcp_json_is_left_alone(self):
        """An explicit file beats a derived endpoint: someone wrote the file."""
        from gitpilot.toolkit.mcp import attach_store_servers

        client = FakeClient({"postgres": [_tool("query")]})
        added = attach_store_servers(client, self._store())

        assert added == []

    def test_a_disabled_server_is_not_attached(self):
        from gitpilot.toolkit.mcp import attach_store_servers

        client = FakeClient({})

        assert attach_store_servers(client, self._store(enabled=False)) == []

    def test_the_whole_path_end_to_end(self):
        """Add it in the UI, and the model is offered ``mcp.postgres.query`` with the
        server's own schema."""
        from gitpilot.toolkit.registry import SchemaProfile

        class StoreBackedClient(FakeClient):
            """A client with no ``mcp.json`` that learns the server from the store."""

            def __init__(self):
                super().__init__({})
                self.configured: list[str] = []

            def add_server(self, config):
                self.configured.append(config.name)
                # Once configured, the server answers with its real tool list.
                self._servers[config.name] = [
                    _tool("query", description="Run SQL", schema=QUERY_SCHEMA),
                    _tool("drop_table", description="Drop a table"),
                ]

        registry = ToolRegistry()
        client = StoreBackedClient()

        result = asyncio.run(register_from_store(
            registry, client=client, store=self._store(),
        ))

        assert client.configured == ["postgres"], "the store's endpoint was never used"
        assert "mcp.postgres.query" in registry
        assert result.skipped == {}, result.skipped

        # The schema reached the model, which is the difference from the old bridge.
        render = registry.render(profile=SchemaProfile(
            max_tool_schemas=64, schema_verbosity="full",
        ))
        query = next(s for s in render.schemas if s["name"] == "mcp.postgres.query")
        assert "sql" in query["parameters"]["properties"]

        # And a destructive tool from the same server still asks.
        assert registry.spec("mcp.postgres.drop_table").risk is Risk.HIGH_RISK

    def test_a_tool_the_user_switched_off_in_the_ui_never_appears(self):
        registry = ToolRegistry()

        class StoreBackedClient(FakeClient):
            def __init__(self):
                super().__init__({})

            def add_server(self, config):
                self._servers[config.name] = [_tool("query"), _tool("drop_table")]

        asyncio.run(register_from_store(
            registry, client=StoreBackedClient(),
            store=self._store(tool_overrides={"drop_table": False}),
        ))

        assert "mcp.postgres.query" in registry
        assert "mcp.postgres.drop_table" not in registry
