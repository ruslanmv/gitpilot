"""The live system prefix — Batch V4-H2.

Two pieces of tested, working code had never been sent to a model:
``prompt_cache.build_system_blocks`` composed the whole cacheable payload and no
caller used it, and ``modes.activate_mode`` had no caller at all — a user could
write a mode, see it listed, select it, and have it change nothing.

So the tests that carry this batch are the ones that prove the join is real: the
mode's persona reaches the prompt, its tool policy reaches the *mask*, and the
tool-def digest changes when the mask does. The last one is what stops a cached
prefix describing tools the run cannot use.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.policy import CapabilityMask
from gitpilot.agent.runner import AgentRunner, RunRequest
from gitpilot.agent.system_payload import (
    FLAG_AGENT_SYSTEM_PAYLOAD,
    MAX_TAIL_CHARS,
    ActiveMode,
    build_payload,
    build_system_text,
    mask_for_mode,
    payload_enabled,
    register_mode_servers,
    resolve_mode,
)

MODES_YAML = """\
customModes:
  - slug: db-pilot
    name: "DB Pilot"
    description: "Queries against staging"
    roleDefinition: |
      You are a senior DBA. Always EXPLAIN before mutating.
    customInstructions: |
      Refuse DROP without explicit confirmation.
    groups:
      - read
    mcpServers:
      postgres:
        command: uvx
        args: [mcp-postgres-server]
        alwaysAllow: [postgres.explain]
"""


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "project"
    (root / ".gitpilot").mkdir(parents=True)
    (root / ".gitpilot" / "modes.yaml").write_text(MODES_YAML, encoding="utf-8")
    (root / "AGENTS.md").write_text(
        "# Project\n\nUse tabs. Run `make check` before committing.\n", encoding="utf-8",
    )
    return root


TOOL_DEFS = [
    {"name": "fs.read", "description": "Read a file", "parameters": {"type": "object"}},
    {"name": "fs.write", "description": "Write a file", "parameters": {"type": "object"}},
]


# ---------------------------------------------------------------------------
# activate_mode finally has a caller
# ---------------------------------------------------------------------------


class TestResolveMode:
    def test_a_mode_in_the_workspace_resolves(self, workspace):
        mode = resolve_mode("db-pilot", workspace)

        assert mode is not None
        assert mode.slug == "db-pilot"
        assert mode.name == "DB Pilot"

    def test_its_persona_becomes_a_prompt_block(self, workspace):
        """The thing a user writes a mode *for*."""
        mode = resolve_mode("db-pilot", workspace)

        assert "senior DBA" in mode.prompt_block
        assert "Refuse DROP" in mode.prompt_block

    def test_its_tool_policy_comes_through(self, workspace):
        mode = resolve_mode("db-pilot", workspace)

        assert mode.tool_policy is not None
        assert mode.restricts_tools is True, "groups: [read] should restrict"

    def test_its_mcp_servers_come_through(self, workspace):
        mode = resolve_mode("db-pilot", workspace)

        assert [config["name"] for config in mode.mcp_server_configs] == ["postgres"]
        assert mode.mcp_toggles

    def test_an_unknown_slug_leaves_the_run_unconfigured(self, workspace):
        """A session naming a mode it does not have proceeds as it did before modes
        existed, rather than failing."""
        assert resolve_mode("no-such-mode", workspace) is None

    def test_no_slug_is_no_mode(self, workspace):
        assert resolve_mode("", workspace) is None

    def test_a_broken_modes_file_does_not_fail_a_run(self, tmp_path, caplog):
        root = tmp_path / "broken"
        (root / ".gitpilot").mkdir(parents=True)
        (root / ".gitpilot" / "modes.yaml").write_text("customModes: not-a-list\n", "utf-8")

        with caplog.at_level("INFO"):
            assert resolve_mode("db-pilot", root) is None

    def test_a_mode_narrows_the_mask_and_cannot_widen_it(self, workspace):
        """A mode is configuration a user wrote, and configuration may narrow what a
        topology granted but never widen it."""
        mode = resolve_mode("db-pilot", workspace)
        granted = CapabilityMask.from_mapping({"fs.read": True, "fs.write": True})

        narrowed = mask_for_mode(mode, granted)

        assert narrowed.allows("fs.read")
        assert not narrowed.allows("fs.write"), "groups: [read] permits no writes"

    def test_no_mode_leaves_the_mask_exactly_as_it_was(self):
        mask = CapabilityMask.from_mapping({"fs.read": True})

        assert mask_for_mode(None, mask) is mask

    def test_a_modes_servers_are_added_to_the_client(self, workspace):
        class FakeClient:
            def __init__(self):
                self.added = []

            def add_server(self, config):
                self.added.append(config.name)

        client = FakeClient()
        added = register_mode_servers(resolve_mode("db-pilot", workspace), client)

        assert added == ["postgres"]
        assert client.added == ["postgres"]

    def test_an_unusable_server_config_is_skipped_not_raised(self, caplog):
        class Refusing:
            def add_server(self, config):
                raise ValueError("nope")

        mode = ActiveMode(slug="m", mcp_server_configs=[{"name": "bad"}])

        with caplog.at_level("WARNING"):
            assert register_mode_servers(mode, Refusing()) == []
        assert "unusable MCP server" in caplog.text


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------


class TestPayload:
    def test_it_carries_the_project_and_the_base_prompt(self, workspace):
        text = build_system_text(
            base_system="You are GitPilot.",
            workspace=workspace,
            tool_defs=TOOL_DEFS,
            enabled=True,
        )

        assert "You are GitPilot." in text
        assert "Use tabs" in text, "AGENTS.md never reached the model"

    def test_a_modes_persona_is_stable_prefix_not_volatile_tail(self, workspace):
        """A persona holds for the whole session; the tail is re-sent uncached every
        turn, so putting it there would be paying for it on every request."""
        payload = build_payload(
            base_system="You are GitPilot.",
            workspace=workspace,
            mode=resolve_mode("db-pilot", workspace),
            tool_defs=TOOL_DEFS,
            enabled=True,
        )
        cacheable = "\n".join(block.text for block in payload.blocks if block.cacheable)

        assert "senior DBA" in cacheable

    def test_the_ordering_is_the_caches(self, workspace):
        """Anything volatile placed before something stable invalidates the stable
        part on every request."""
        payload = build_payload(
            base_system="BASE",
            workspace=workspace,
            tool_defs=TOOL_DEFS,
            session_tail="the user just renamed a file",
            enabled=True,
        )
        labels = [block.label for block in payload.blocks]

        assert labels.index("base") < labels.index("agents_md")
        assert labels[-1] == "session"
        assert payload.blocks[-1].cacheable is False

    def test_a_long_tail_keeps_its_end(self, workspace):
        """What changed most recently is what the next turn needs."""
        tail = "\n".join(f"line {index}" for index in range(2_000))

        payload = build_payload(
            base_system="BASE", workspace=workspace, session_tail=tail, enabled=True,
        )
        rendered = "\n".join(block.text for block in payload.blocks)

        assert "line 1999" in rendered
        assert "line 0\n" not in rendered
        assert len(rendered) < len(tail)
        assert MAX_TAIL_CHARS == 2_000

    def test_no_tail_adds_no_block(self, workspace):
        payload = build_payload(base_system="BASE", workspace=workspace, enabled=True)

        assert "session" not in [block.label for block in payload.blocks]


class TestTheDigestTracksTheMask:
    def _digest(self, tool_defs):
        return build_payload(
            base_system="BASE", tool_defs=tool_defs, enabled=True,
        ).cache_prefix_digest

    def test_the_same_tools_give_the_same_digest(self):
        assert self._digest(TOOL_DEFS) == self._digest(list(TOOL_DEFS))

    def test_a_narrower_mask_gives_a_different_digest(self):
        """Otherwise a cached prefix would describe tools this run cannot use — the
        model would be told about `fs.write` and then refused it."""
        assert self._digest(TOOL_DEFS) != self._digest(TOOL_DEFS[:1])

    def test_the_digest_comes_from_the_masked_render(self, tmp_path):
        from gitpilot.toolkit.builtins import build_default_registry

        registry = build_default_registry(include_git=False, include_forge=False)
        wide = registry.render(CapabilityMask.permissive()).schemas
        narrow = registry.render(
            CapabilityMask.from_mapping({"fs.read": True}),
        ).schemas

        assert self._digest(wide) != self._digest(narrow)
        assert len(narrow) < len(wide)


# ---------------------------------------------------------------------------
# Through the runner
# ---------------------------------------------------------------------------


class _FakeProvider:
    name = "openai"
    model = "gpt-4o-mini"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    async def complete(self, messages, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _build(tmp_path, **request_kwargs):
    profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "p.json")
    request = RunRequest(
        task="do the thing", journal_root=tmp_path / "journals", **request_kwargs,
    )
    return AgentRunner().build(request, provider=_FakeProvider(), profile=profile)


class TestThroughTheRunner:
    def test_with_the_flag_off_the_prompt_is_byte_identical_to_phase_c(
        self, tmp_path, workspace,
    ):
        """Batch rule 2. The off-branch has to be what shipped, not something new."""
        from gitpilot import flags
        from gitpilot.agent.prompts import build_system_prompt

        flags.clear_all_overrides()
        _loop, ctx = _build(tmp_path, workspace_root=workspace, mode_slug="db-pilot")
        expected = build_system_prompt(
            ctx.model_profile, read_only=False, require_verification=False,
            # The default registry offers `todo.write` under a permissive mask, so
            # Phase C's prompt includes the TODO block. Naming it here rather than
            # guessing is the difference between this test pinning the off-branch
            # and this test pinning a shorter prompt nobody ships.
            with_todo=True, role="",
        )

        assert ctx.system_payload == expected
        assert "senior DBA" not in ctx.system_payload
        assert "Use tabs" not in ctx.system_payload

    def test_with_the_flag_on_the_project_reaches_the_prompt(self, tmp_path, workspace):
        from gitpilot import flags

        flags.set_override(FLAG_AGENT_SYSTEM_PAYLOAD, True)
        try:
            _loop, ctx = _build(tmp_path, workspace_root=workspace, mode_slug="db-pilot")
        finally:
            flags.clear_all_overrides()

        assert "senior DBA" in ctx.system_payload, "the mode changed nothing"
        assert "Use tabs" in ctx.system_payload, "AGENTS.md changed nothing"

    def test_the_mode_narrows_the_runs_capabilities(self, tmp_path, workspace):
        """The other half of a mode doing something: not just prose, a smaller mask."""
        from gitpilot import flags

        flags.set_override("agent_policy", True)
        try:
            _loop, ctx = _build(tmp_path, workspace_root=workspace, mode_slug="db-pilot")
        finally:
            flags.clear_all_overrides()

        assert ctx.capabilities.allows("fs.read")
        assert not ctx.capabilities.allows("fs.write")

    def test_no_mode_leaves_the_capabilities_alone(self, tmp_path, workspace):
        from gitpilot import flags

        flags.set_override("agent_policy", True)
        try:
            _loop, ctx = _build(tmp_path, workspace_root=workspace)
        finally:
            flags.clear_all_overrides()

        assert ctx.capabilities.allows("fs.write")

    def test_a_payload_that_will_not_compose_falls_back_to_the_base_prompt(
        self, tmp_path, workspace, monkeypatch, caplog,
    ):
        """A prompt that will not compose is not a run-ender."""
        from gitpilot import flags
        from gitpilot.agent import system_payload as payload_mod

        def explode(**kwargs):
            raise RuntimeError("no prompt for you")

        monkeypatch.setattr(payload_mod, "build_system_text", explode)
        flags.set_override(FLAG_AGENT_SYSTEM_PAYLOAD, True)
        try:
            with caplog.at_level("WARNING"):
                _loop, ctx = _build(tmp_path, workspace_root=workspace)
        finally:
            flags.clear_all_overrides()

        assert "GitPilot" in ctx.system_payload
        assert "could not compose the system payload" in caplog.text

    def test_the_flag_defaults_off(self):
        from gitpilot import flags

        flags.clear_all_overrides()
        assert payload_enabled() is False
        assert payload_enabled(True) is True
