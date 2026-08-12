"""Tool registry core — Batch V4-A1."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gitpilot.toolkit import (
    ALL_CAPABILITIES,
    Effect,
    LocalWorkspace,
    RepoBinding,
    Risk,
    SchemaProfile,
    ToolCall,
    ToolError,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    validate_arguments,
)
from gitpilot.toolkit.registry import capabilities_from

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path relative to the root"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _spec(tool_id="fs.read", **kwargs) -> ToolSpec:
    defaults = dict(
        id=tool_id,
        title="Read file",
        description="Read a file from the workspace. Returns its text content.",
        params_schema=READ_SCHEMA,
        risk=Risk.SAFE,
        effects=frozenset({Effect.READS_FS}),
    )
    defaults.update(kwargs)
    return ToolSpec(**defaults)


async def _echo_handler(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult.success(call, f"read {call.arguments['path']}", data={"path": call.arguments["path"]})


class TestToolSpec:
    @pytest.mark.parametrize("bad_id", [
        "read",             # no namespace
        "fs..read",         # empty segment
        "FS.read",          # not snake_case
        "fs.Read",
        "fs-read",
        "1fs.read",
    ])
    def test_rejects_non_canonical_ids(self, bad_id):
        with pytest.raises(ToolError, match="invalid tool id"):
            _spec(bad_id)

    @pytest.mark.parametrize("good_id", ["fs.read", "github.pr.create", "mcp.postgres.query"])
    def test_accepts_namespaced_ids(self, good_id):
        assert _spec(good_id).id == good_id

    def test_capability_defaults_to_id(self):
        assert _spec().capability == "fs.read"
        assert _spec(capability="fs.write").capability == "fs.write"

    def test_schema_must_be_an_object(self):
        with pytest.raises(ToolError, match="type 'object'"):
            _spec(params_schema={"type": "string"})

    def test_signature_marks_optional_arguments(self):
        assert _spec().signature() == "fs.read(path, limit?)"

    def test_namespace(self):
        assert _spec("github.pr.create").namespace == "github"


class TestRegistration:
    def test_duplicate_registration_rejected(self):
        reg = ToolRegistry()
        reg.register(_spec(), _echo_handler)
        with pytest.raises(ToolError, match="already registered"):
            reg.register(_spec(), _echo_handler)

    def test_non_callable_handler_rejected(self):
        reg = ToolRegistry()
        with pytest.raises(ToolError, match="not callable"):
            reg.register(_spec(), "not a function")


class TestResolution:
    @pytest.fixture()
    def reg(self):
        reg = ToolRegistry()
        reg.register(_spec(), _echo_handler)
        reg.register(_spec("terminal.run"), _echo_handler)
        return reg

    def test_exact_id(self, reg):
        assert reg.resolve("fs.read") == "fs.read"

    @pytest.mark.parametrize("legacy", [
        "Read file content",          # CrewAI display name, GitHub mode
        "Read local file",            # CrewAI display name, local mode
        "read_file",                  # VS Code tool id
        "READ",                       # the alias tool in agent_tools
    ])
    def test_legacy_names_resolve(self, reg, legacy):
        assert reg.resolve(legacy) == "fs.read"

    @pytest.mark.parametrize("mangled", ["FS.READ", "fs-read", " fs.read "])
    def test_mangled_forms_resolve(self, reg, mangled):
        """A corrective observation costs a whole model turn; normalise instead."""
        assert reg.resolve(mangled) == "fs.read"

    def test_run_command_alias(self, reg):
        assert reg.resolve("Run shell command") == "terminal.run"
        assert reg.resolve("run_command") == "terminal.run"

    def test_unknown_returns_none(self, reg):
        assert reg.resolve("fs.telepathy") is None
        assert "fs.telepathy" not in reg

    def test_alias_for_unregistered_tool_is_not_resolved(self):
        """``todo.write`` has an alias but no implementation until Batch V4-E1."""
        reg = ToolRegistry()
        reg.register(_spec(), _echo_handler)
        assert reg.resolve("TodoWrite") is None


class TestSchemaRendering:
    @pytest.fixture()
    def reg(self):
        reg = ToolRegistry()
        reg.register(_spec(), _echo_handler)
        reg.register(_spec("fs.write", risk=Risk.APPROVAL), _echo_handler)
        return reg

    def test_full_verbosity_carries_the_whole_schema(self, reg):
        rendered = reg.schemas(profile=SchemaProfile(schema_verbosity="full"))
        assert rendered[0]["parameters"] == READ_SCHEMA
        assert "Returns its text content" in rendered[0]["description"]

    def test_compact_verbosity_sheds_prose_and_keeps_types(self, reg):
        rendered = reg.schemas(profile=SchemaProfile(schema_verbosity="compact"))
        params = rendered[0]["parameters"]
        assert params["properties"] == {"path": {"type": "string"}, "limit": {"type": "integer"}}
        assert params["required"] == ["path"]
        assert rendered[0]["description"] == "Read a file from the workspace."

    def test_names_only_verbosity_is_a_signature_line(self, reg):
        rendered = reg.schemas(profile=SchemaProfile(schema_verbosity="names_only"))
        assert rendered[0] == {
            "name": "fs.read",
            "signature": "fs.read(path, limit?)",
            "description": "Read a file from the workspace.",
        }

    def test_unknown_verbosity_rejected(self):
        with pytest.raises(ToolError, match="schema_verbosity"):
            SchemaProfile(schema_verbosity="terse")

    def test_capabilities_filter_what_the_model_can_see(self, reg):
        render = reg.render(capabilities=capabilities_from({"fs.read"}))
        assert [s["name"] for s in render.schemas] == ["fs.read"]
        assert [d.id for d in render.dropped] == ["fs.write"]
        assert render.dropped[0].reason == "capability-not-granted"

    def test_namespace_wildcard_grants_a_family(self, reg):
        render = reg.render(capabilities=capabilities_from({"fs.*"}))
        assert {s["name"] for s in render.schemas} == {"fs.read", "fs.write"}

    def test_budget_pruning_is_reported_not_silent(self, reg):
        """A silent cap reads downstream as 'the model had every tool and failed'."""
        render = reg.render(profile=SchemaProfile(max_tool_schemas=1))
        assert len(render.schemas) == 1
        assert render.truncated
        assert render.dropped[0].reason == "schema-budget"

    def test_priority_decides_what_survives_the_budget(self):
        reg = ToolRegistry()
        reg.register(_spec("fs.read", priority=90), _echo_handler)
        reg.register(_spec("fs.write", priority=10), _echo_handler)
        render = reg.render(profile=SchemaProfile(max_tool_schemas=1))
        assert [s["name"] for s in render.schemas] == ["fs.write"]


class TestArgumentValidation:
    def test_valid_arguments_produce_no_problems(self):
        assert validate_arguments(READ_SCHEMA, {"path": "a.py", "limit": 10}) == []

    def test_missing_required(self):
        assert "missing required argument 'path'" in validate_arguments(READ_SCHEMA, {})

    def test_wrong_type_names_the_argument_and_expectation(self):
        problems = validate_arguments(READ_SCHEMA, {"path": 42})
        assert problems == ["'path' must be string, got int"]

    def test_boolean_is_not_an_integer(self):
        """bool subclasses int in Python; a flag passed as a count is a real mistake."""
        assert validate_arguments(READ_SCHEMA, {"path": "a", "limit": True}) == [
            "'limit' must be integer, got boolean",
        ]

    def test_bounds_enforced(self):
        assert validate_arguments(READ_SCHEMA, {"path": "a", "limit": 0}) == ["'limit' must be >= 1"]
        assert validate_arguments(READ_SCHEMA, {"path": "a", "limit": 9000}) == ["'limit' must be <= 500"]

    def test_unknown_argument_rejected_when_additional_properties_false(self):
        problems = validate_arguments(READ_SCHEMA, {"path": "a", "mode": "x"})
        assert "unknown argument 'mode'" in problems[0]

    def test_enum_violation(self):
        schema = {"type": "object", "properties": {"how": {"type": "string", "enum": ["a", "b"]}}}
        assert validate_arguments(schema, {"how": "c"}) == ["'how' must be one of ['a', 'b'], got 'c'"]


class TestExecution:
    @pytest.fixture()
    def reg(self):
        reg = ToolRegistry()
        reg.register(_spec(), _echo_handler)
        return reg

    def test_happy_path(self, reg):
        call = ToolCall(id="c1", tool="fs.read", arguments={"path": "a.py"})
        result = asyncio.run(reg.execute(call, ToolExecutionContext()))
        assert result.ok and result.content == "read a.py"
        assert result.data == {"path": "a.py"}
        assert result.call_id == "c1"

    def test_legacy_name_executes_and_records_the_canonical_id(self, reg):
        call = ToolCall(id="c2", tool="Read local file", arguments={"path": "a.py"})
        result = asyncio.run(reg.execute(call, ToolExecutionContext()))
        assert result.ok
        assert result.tool == "fs.read", "events and journal must record one name per tool"

    def test_unknown_tool_is_a_result_not_an_exception(self, reg):
        call = ToolCall(id="c3", tool="fs.telepathy", arguments={})
        result = asyncio.run(reg.execute(call, ToolExecutionContext()))
        assert not result.ok and result.error == "unknown_tool"
        assert "fs.read" in result.content, "list valid ids so the model can retry"

    def test_invalid_arguments_teach_the_correction(self, reg):
        call = ToolCall(id="c4", tool="fs.read", arguments={"limit": 5})
        result = asyncio.run(reg.execute(call, ToolExecutionContext()))
        assert result.error == "invalid_arguments"
        assert "missing required argument 'path'" in result.content
        assert "fs.read(path, limit?)" in result.content

    def test_handler_exception_becomes_a_result(self):
        reg = ToolRegistry()

        async def explode(call, ctx):
            raise ValueError("backend on fire")

        reg.register(_spec(), explode)
        result = asyncio.run(
            reg.execute(ToolCall(id="c5", tool="fs.read", arguments={"path": "a"}), ToolExecutionContext()),
        )
        assert not result.ok and result.error == "ValueError"
        assert "backend on fire" in result.content

    def test_permission_error_is_labelled_as_a_refusal(self):
        reg = ToolRegistry()

        async def jailed(call, ctx):
            raise PermissionError("Path traversal blocked: ../etc/passwd")

        reg.register(_spec(), jailed)
        result = asyncio.run(
            reg.execute(ToolCall(id="c6", tool="fs.read", arguments={"path": "../etc/passwd"}), ToolExecutionContext()),
        )
        assert result.error == "permission_denied"

    def test_timeout_is_enforced_per_spec(self, monkeypatch):
        reg = ToolRegistry()
        slept: list[float] = []

        async def slow(call, ctx):
            await asyncio.sleep(30)

        reg.register(_spec(timeout_s=1), slow)

        # Assert the timeout wiring, not the passage of a real second.
        real_wait_for = asyncio.wait_for

        async def instant_wait_for(awaitable, timeout):
            slept.append(timeout)
            return await real_wait_for(awaitable, 0.01)

        monkeypatch.setattr("gitpilot.toolkit.registry.asyncio.wait_for", instant_wait_for)

        result = asyncio.run(
            reg.execute(ToolCall(id="c7", tool="fs.read", arguments={"path": "a"}), ToolExecutionContext()),
        )
        assert result.error == "timeout"
        assert "1s" in result.content
        assert slept == [1], "the spec's timeout must be the one applied"

    def test_zero_timeout_is_rejected_at_construction(self):
        """0 reads as 'no timeout' to a truthiness check and 'now' to a human."""
        with pytest.raises(ToolError, match="timeout_s must be positive"):
            _spec(timeout_s=0)

    def test_sync_handler_runs_off_the_event_loop(self):
        """A blocking handler must not stall event emission or cancellation.

        The loop is single-threaded, so a synchronous git subprocess run inline
        would freeze every other task for its duration.
        """
        import threading

        reg = ToolRegistry()
        thread_names: list[str] = []

        def sync_handler(call, ctx):
            thread_names.append(threading.current_thread().name)
            return ToolResult.success(call, "sync ok")

        reg.register(_spec(), sync_handler)

        async def _run():
            main_thread = threading.current_thread().name
            result = await reg.execute(
                ToolCall(id="c8", tool="fs.read", arguments={"path": "a"}), ToolExecutionContext(),
            )
            return result, main_thread

        result, main_thread = asyncio.run(_run())

        assert result.content == "sync ok"
        assert thread_names and thread_names[0] != main_thread, (
            "sync handlers must be dispatched with asyncio.to_thread"
        )

    def test_async_handler_stays_on_the_loop(self):
        """No needless thread hop for the common case."""
        import threading

        reg = ToolRegistry()
        seen: list[str] = []

        async def async_handler(call, ctx):
            seen.append(threading.current_thread().name)
            return ToolResult.success(call, "async ok")

        reg.register(_spec(), async_handler)

        async def _run():
            main_thread = threading.current_thread().name
            await reg.execute(
                ToolCall(id="c8b", tool="fs.read", arguments={"path": "a"}), ToolExecutionContext(),
            )
            return main_thread

        main_thread = asyncio.run(_run())
        assert seen == [main_thread]

    def test_handler_returning_the_wrong_type_is_our_bug_not_the_models(self):
        reg = ToolRegistry()

        async def bad(call, ctx):
            return "just a string"

        reg.register(_spec(), bad)
        with pytest.raises(ToolError, match="expected ToolResult"):
            asyncio.run(
                reg.execute(ToolCall(id="c9", tool="fs.read", arguments={"path": "a"}), ToolExecutionContext()),
            )


class TestExecutionContext:
    def test_local_workspace_wins_when_both_bindings_present(self, tmp_path):
        ctx = ToolExecutionContext(
            workspace=LocalWorkspace(root=tmp_path),
            repo=RepoBinding(owner="o", repo="r"),
        )
        assert ctx.is_local is True

    def test_repo_only_context_is_not_local(self):
        ctx = ToolExecutionContext(repo=RepoBinding(owner="o", repo="r", branch="main"))
        assert ctx.is_local is False
        assert ctx.require_repo().full_name == "o/r"

    def test_missing_binding_raises(self):
        ctx = ToolExecutionContext()
        with pytest.raises(ToolError, match="no local workspace"):
            ctx.require_workspace()
        with pytest.raises(ToolError, match="no repository"):
            ctx.require_repo()

    def test_workspace_root_is_coerced_to_path(self):
        ws = LocalWorkspace(root="/tmp/x")
        assert isinstance(ws.root, Path)


class TestDeniedResult:
    def test_denial_is_distinct_from_failure(self):
        call = ToolCall(id="c10", tool="git.push", arguments={})
        result = ToolResult.denied_for(call, "git.push not granted by this topology")
        assert result.denied and not result.ok
        assert result.error == "denied"
        assert "not granted" in result.content


def test_all_capabilities_is_permissive_and_marked():
    assert ALL_CAPABILITIES.allows("anything.at.all")
    assert repr(ALL_CAPABILITIES) == "ALL_CAPABILITIES"
