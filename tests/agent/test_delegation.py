"""Subagents — Batch V4-E2."""
from __future__ import annotations

import asyncio
import json

import pytest

from gitpilot.agent.delegation import (
    DEFAULT_MAX_DEPTH,
    MAX_RETURN_CHARS,
    TEMPLATES,
    SubagentResult,
    child_capabilities,
    child_journal_path,
    parse_return,
    remaining_depth,
    template_for,
    template_names,
)
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.policy import CapabilityMask, PolicyEngine, Qualifier, ResolvedPolicy
from gitpilot.agent.providers.base import ProviderResponse, RawToolCall
from gitpilot.agent.runner import AgentRunner, RunRequest

from .parity_fixture import fixture_repo  # noqa: F401 — pytest fixture


class _TwoLevelProvider:
    """Answers the parent one way and a child another.

    Told apart by whether ``agent.delegate`` is on offer: only a parent has it,
    which is the depth limit expressed as a fact about the tool list.
    """

    name = "fake"
    model = "gpt-4o-mini"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self, parent, child):
        self.parent = list(parent)
        self.child = list(child)
        self.child_tools: list[set] = []

    async def complete(self, messages, *, tools=None, system=None, **opts):
        names = {getattr(tool, "name", "") for tool in (tools or [])}
        if "agent.delegate" in names:
            return self.parent.pop(0) if self.parent else ProviderResponse(text="parent done")
        self.child_tools.append(names)
        return self.child.pop(0) if self.child else ProviderResponse(text="child done")

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _delegate_turn(agent="explorer", task="map the repo", **extra):
    return ProviderResponse(tool_calls=[
        RawToolCall(
            id="d1", name="agent.delegate",
            arguments={"agent": agent, "task": task, **extra},
        ),
    ])


def _run(tmp_path, fixture_repo, provider, **request_kwargs):
    profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
    return asyncio.run(AgentRunner().run(
        RunRequest(
            task="explore this repo", session_id="s",
            workspace_root=fixture_repo.path, journal_root=tmp_path / "j",
            **request_kwargs,
        ),
        provider=provider, profile=profile,
    ))


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class TestTemplates:
    def test_the_four_templates_t2_drew_all_exist(self):
        """T2's flow graph has drawn Task() edges to these for months, spawning
        nothing."""
        assert set(template_names()) == {
            "explorer", "reviewer", "researcher", "test_analyst",
        }

    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    def test_every_template_is_read_only_by_default(self, name):
        """A subagent that can write is a second agent, not a delegation."""
        assert TEMPLATES[name].read_only is True

    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    def test_no_template_grants_a_write_capability(self, name):
        granted = TEMPLATES[name].capabilities
        assert not {"fs.write", "fs.edit", "fs.delete", "git.commit", "git.push"} & set(granted)

    def test_an_unknown_name_resolves_to_nothing(self):
        assert template_for("wibble") is None
        assert template_for("") is None

    def test_the_brief_is_self_contained(self):
        """A subagent cannot see the parent's conversation."""
        brief = TEMPLATES["reviewer"].brief("review app.py", "the security findings")
        assert "review app.py" in brief
        assert "the security findings" in brief
        assert "structured fields" in brief


# ---------------------------------------------------------------------------
# The mask — the safety property
# ---------------------------------------------------------------------------


class TestMaskIntersection:
    def test_a_child_cannot_exceed_its_parent(self):
        parent = CapabilityMask.from_mapping({"fs.read": True})
        child = child_capabilities(parent, TEMPLATES["explorer"])

        assert child.allows("fs.read") is True
        assert child.allows("fs.glob") is False, (
            "the template grants fs.glob; the parent does not, so the child must not"
        )

    def test_a_template_is_a_ceiling_not_a_grant(self):
        """`test_analyst` asks for terminal.run; a parent without it stays without."""
        parent = CapabilityMask.from_mapping({"fs.read": True, "test.run": True})
        child = child_capabilities(parent, TEMPLATES["test_analyst"])

        assert child.allows("test.run") is True
        assert child.allows("terminal.run") is False

    def test_a_child_cannot_write_when_its_parent_cannot(self):
        parent = CapabilityMask.from_mapping({"fs.read": True, "fs.write": False})
        child = child_capabilities(parent, TEMPLATES["explorer"])

        assert child.allows("fs.write") is False

    def test_an_unknown_parent_mask_cannot_widen_the_ceiling(self):
        """A permissive stand-in must not become a way to grant more."""
        class _Restrictive:
            def allows(self, capability):
                return capability == "fs.read"

        child = child_capabilities(_Restrictive(), TEMPLATES["explorer"])

        assert child.allows("fs.read") is True
        assert child.allows("fs.grep") is False

    def test_no_parent_yields_the_template_alone(self):
        child = child_capabilities(None, TEMPLATES["researcher"])
        assert child.allows("fs.read") is True
        assert child.allows("fs.write") is False


class TestDepth:
    def test_the_default_is_one_level(self):
        assert remaining_depth(ResolvedPolicy(), 0) == DEFAULT_MAX_DEPTH
        assert remaining_depth(ResolvedPolicy(), 1) == 0

    def test_the_qualifier_can_raise_it(self):
        policy = ResolvedPolicy(
            capabilities=CapabilityMask(
                granted={"agent.delegate": Qualifier(max_depth=3)},
            ),
        )
        assert remaining_depth(policy, 0) == 3
        assert remaining_depth(policy, 3) == 0

    def test_a_child_is_not_offered_the_tool(self, tmp_path, fixture_repo):
        """The depth limit expressed as an absent tool rather than a refused call:
        offering something whose every use fails teaches the model nothing."""
        provider = _TwoLevelProvider(
            parent=[_delegate_turn(), ProviderResponse(text="parent done")],
            child=[ProviderResponse(text=json.dumps({"summary": "one module", "status": "completed"}))],
        )

        _run(tmp_path, fixture_repo, provider)

        assert provider.child_tools, "the child never ran"
        for offered in provider.child_tools:
            assert "agent.delegate" not in offered


# ---------------------------------------------------------------------------
# The structured return
# ---------------------------------------------------------------------------


class TestStructuredReturn:
    def test_a_json_answer_is_read_into_the_shape(self):
        result = parse_return(json.dumps({
            "summary": "two modules",
            "files": ["a.py", "b.py"],
            "findings": ["b.py has no tests"],
            "status": "completed",
        }))

        assert result.summary == "two modules"
        assert result.files == ["a.py", "b.py"]
        assert result.status == "completed"

    def test_json_embedded_in_prose_is_still_found(self):
        result = parse_return('Here you go:\n{"summary": "ok", "status": "completed"}\nHope that helps.')
        assert result.summary == "ok"

    def test_prose_becomes_a_partial_result(self):
        """Refusing it outright would make a formatting failure look like a
        delegation that found nothing."""
        result = parse_return("I looked at app.py and it defines one class.")

        assert result.status == "partial"
        assert "app.py" in result.summary

    def test_an_empty_answer_is_a_failure(self):
        result = parse_return("")
        assert result.status == "failed"
        assert "returned nothing" in result.note

    def test_an_unknown_status_becomes_partial(self):
        assert parse_return('{"summary": "x", "status": "brilliant"}').status == "partial"

    def test_a_scalar_field_is_accepted_as_a_list(self):
        result = parse_return('{"summary": "x", "files": "a.py", "status": "completed"}')
        assert result.files == ["a.py"]

    def test_the_render_reads_as_an_observation(self):
        result = SubagentResult(
            summary="two modules", files=["a.py"],
            findings=["no tests"], recommendations=["add a test"],
        )
        text = result.render()

        assert "two modules" in text
        assert "- no tests" in text
        assert "- add a test" in text
        assert "Files: a.py" in text

    def test_a_non_completed_status_is_visible_to_the_parent(self):
        text = SubagentResult(
            summary="got partway", status="partial", note="ran out of steps",
        ).render()
        assert "status: partial" in text
        assert "ran out of steps" in text


class TestCompression:
    def test_an_oversized_return_is_compressed_not_dropped(self):
        result = SubagentResult(
            summary="x " * 20_000, files=["src/app.py", "src/db.py"],
        )
        text = result.render(budget=2_000)

        assert len(text) <= 2_000 + 200   # the file list is re-appended

    def test_compression_preserves_the_paths(self):
        """The paths are what the parent needs to act; the prose is what it does
        not."""
        result = SubagentResult(
            summary="prose. " * 5_000,
            files=["src/app.py", "tests/test_app.py", "docs/readme.md"],
        )
        text = result.render(budget=1_500)

        for path in result.files:
            assert path in text, f"{path} was lost in compression"


# ---------------------------------------------------------------------------
# Running a delegation
# ---------------------------------------------------------------------------


class TestRunningADelegation:
    def test_the_parent_receives_a_structured_answer(self, tmp_path, fixture_repo):
        provider = _TwoLevelProvider(
            parent=[_delegate_turn(), ProviderResponse(text="the explorer mapped it")],
            child=[ProviderResponse(text=json.dumps({
                "summary": "one module and a readme",
                "files": ["a.py"], "status": "completed",
            }))],
        )

        result = _run(tmp_path, fixture_repo, provider)

        assert result.status == "completed"
        entry = result.context.ledger.entries[0]
        assert entry.call.tool == "agent.delegate"
        assert entry.result.ok is True
        assert entry.result.data["summary"] == "one module and a readme"
        assert "one module and a readme" in entry.result.content

    def test_the_child_journal_is_separate(self, tmp_path, fixture_repo):
        """So the parent's journal stays readable and the safety invariants can be
        asserted per run rather than over an interleaved log."""
        provider = _TwoLevelProvider(
            parent=[_delegate_turn(), ProviderResponse(text="done")],
            child=[ProviderResponse(text='{"summary": "ok", "status": "completed"}')],
        )

        result = _run(tmp_path, fixture_repo, provider)

        parent_path = result.context.journal.path
        children = list(parent_path.parent.rglob("sub/*.jsonl"))
        assert len(children) == 1
        assert children[0].parent.parent.name == parent_path.stem

    def test_the_files_the_child_actually_read_are_reported(self, tmp_path, fixture_repo):
        """From the ledger rather than the model's own list: a path it never
        opened is not a finding."""
        provider = _TwoLevelProvider(
            parent=[_delegate_turn(), ProviderResponse(text="done")],
            child=[
                ProviderResponse(tool_calls=[
                    RawToolCall(id="c1", name="fs.read", arguments={"path": "README.md"}),
                ]),
                ProviderResponse(text='{"summary": "read it", "status": "completed"}'),
            ],
        )

        result = _run(tmp_path, fixture_repo, provider)

        assert "README.md" in result.context.ledger.entries[0].result.data["files"]

    def test_an_unknown_agent_is_a_correctable_error(self, tmp_path, fixture_repo):
        provider = _TwoLevelProvider(
            parent=[_delegate_turn(agent="wizard"), ProviderResponse(text="ok, myself then")],
            child=[],
        )

        result = _run(tmp_path, fixture_repo, provider)

        entry = result.context.ledger.entries[0]
        assert entry.result.ok is False
        assert "explorer" in entry.result.content   # it lists what is available
        assert result.status == "completed"

    def test_an_empty_task_is_refused(self, tmp_path, fixture_repo):
        provider = _TwoLevelProvider(
            parent=[_delegate_turn(task=""), ProviderResponse(text="fine")],
            child=[],
        )

        result = _run(tmp_path, fixture_repo, provider)
        assert result.context.ledger.entries[0].result.ok is False

    def test_a_child_failure_does_not_kill_the_parent(self, tmp_path, fixture_repo):
        class _Exploding(_TwoLevelProvider):
            async def complete(self, messages, *, tools=None, system=None, **opts):
                names = {getattr(tool, "name", "") for tool in (tools or [])}
                if "agent.delegate" not in names:
                    raise RuntimeError("the child's provider fell over")
                return await super().complete(messages, tools=tools, system=system, **opts)

        provider = _Exploding(
            parent=[_delegate_turn(), ProviderResponse(text="I carried on alone")],
            child=[],
        )

        result = _run(tmp_path, fixture_repo, provider)

        assert result.status == "completed"
        assert result.answer == "I carried on alone"
        entry = result.context.ledger.entries[0]
        assert entry.result.ok is False
        assert "do the work yourself" in entry.result.content

    def test_a_read_only_parent_yields_a_read_only_child(self, tmp_path, fixture_repo):
        provider = _TwoLevelProvider(
            parent=[_delegate_turn(), ProviderResponse(text="done")],
            child=[
                ProviderResponse(tool_calls=[
                    RawToolCall(
                        id="c1", name="fs.write",
                        arguments={"path": "a.py", "content": "x"},
                    ),
                ]),
                ProviderResponse(text='{"summary": "could not write", "status": "partial"}'),
            ],
        )

        _run(tmp_path, fixture_repo, provider, read_only=True)

        assert not (fixture_repo.path / "a.py").exists() or (
            (fixture_repo.path / "a.py").read_text() != "x"
        )

    def test_the_childs_iterations_are_capped(self, tmp_path, fixture_repo):
        """A subagent with an open-ended budget is a second run."""
        provider = _TwoLevelProvider(
            parent=[_delegate_turn(max_iterations=2), ProviderResponse(text="done")],
            child=[
                ProviderResponse(tool_calls=[
                    RawToolCall(id=f"c{n}", name="fs.read", arguments={"path": "README.md"}),
                ])
                for n in range(8)
            ],
        )

        result = _run(tmp_path, fixture_repo, provider)

        entry = result.context.ledger.entries[0]
        assert entry.result.data["status"] in ("partial", "failed")

    def test_the_events_are_tagged_for_nesting(self, tmp_path, fixture_repo):
        from gitpilot.agent_events import AgentEventBus

        bus = AgentEventBus()
        seen = []

        async def drive():
            _sub, queue = bus.subscribe()
            provider = _TwoLevelProvider(
                parent=[_delegate_turn(), ProviderResponse(text="done")],
                child=[
                    ProviderResponse(tool_calls=[
                        RawToolCall(id="c1", name="fs.read", arguments={"path": "README.md"}),
                    ]),
                    ProviderResponse(text='{"summary": "ok", "status": "completed"}'),
                ],
            )
            profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
            await AgentRunner().run(
                RunRequest(
                    task="x", session_id="s", workspace_root=fixture_repo.path,
                    journal_root=tmp_path / "j",
                ),
                bus=bus, provider=provider, profile=profile,
            )
            while not queue.empty():
                seen.append(queue.get_nowait())

        asyncio.run(drive())

        nested = [
            event for event in seen
            if event.data.get("parent_call_id") == "d1"
        ]
        assert nested, "no event was tagged with the parent call"
        assert {event.data.get("subagent") for event in nested} == {"explorer"}


class TestJournalPaths:
    def test_a_child_sits_under_its_parents_run(self, tmp_path):
        from gitpilot.agent.journal import RunJournal

        parent = RunJournal(run_id="run_abc", session_id="s", root=tmp_path)
        path = child_journal_path(parent, 2)

        assert path.parts[-3:] == ("run_abc", "sub", "2.jsonl")
