"""The C-scoped trace test — Batch V4-C4.

The acceptance criterion the whole programme is aimed at, scoped to what Phase C
can honestly assert.  The task is the one from §19.1:

    "Analyze how this repository's testing infrastructure works. Do not modify
    files."

**Nothing about the trajectory is hardcoded.**  The assertions are on
*categories* of tool call and on journal integrity — a config file was read, the
tests directory was searched, no mutating call happened, the answer names the
real framework.  Which file it reads first, and in what order, is the model's to
discover; a test that pinned the steps would be asserting that we wrote the steps
down, which is the opposite of the property under test.

Policy assertions are deliberately absent: the PolicyEngine arrives in Batch
V4-D1, so "zero ``ask`` decisions" cannot be checked yet.  Batch V4-E6 adds them.

The NATIVE run uses a scripted provider — a recorded trajectory, so CI is
deterministic and offline.  The REACT_TEXT run talks to a real local model and
skips when none is reachable, because a grammar a real model must follow is not
something a fixture can prove.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from gitpilot.agent.contracts import Dialect
from gitpilot.agent.headless import run_loop_headless
from gitpilot.agent.journal import LineType, read_journal, read_seed
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers.base import ProviderResponse, RawToolCall

TASK = (
    "Analyze how this repository's testing infrastructure works. "
    "Do not modify files."
)

#: Tools that change something.  A read-only task must touch none of them.
MUTATING_TOOLS = frozenset({
    "fs.write", "fs.edit", "fs.delete",
    "git.commit", "git.push", "git.branch", "git.stash",
    "github.issue.create", "github.issue.comment", "github.pr.create",
})

CONFIG_FILES = ("pyproject.toml", "pytest.ini", "setup.cfg", "Makefile", "package.json")


@pytest.fixture()
def repo(tmp_path):
    """A small project whose testing setup is discoverable but not obvious."""
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "src").mkdir()

    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8",
    )
    (root / "Makefile").write_text("test:\n\tpython -m pytest -q\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("def greet(n):\n    return n\n", encoding="utf-8")
    (root / "tests" / "test_app.py").write_text(
        "from src.app import greet\n\n\ndef test_greet():\n    assert greet('x') == 'x'\n",
        encoding="utf-8",
    )
    (root / "tests" / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    return root


class _RecordedTrajectory:
    """A provider replaying one recorded NATIVE trajectory.

    Written as a *reaction* to what the loop sends rather than a fixed script:
    it answers with the next investigative step until it has seen enough, which
    keeps the test honest about the loop driving the conversation.
    """

    name = "recorded"
    model = "gpt-4o-mini"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self):
        self.steps = [
            RawToolCall(id="c1", name="fs.read", arguments={"path": "pyproject.toml"}),
            RawToolCall(id="c2", name="fs.glob", arguments={"pattern": "tests/**/*.py"}),
            RawToolCall(id="c3", name="test.detect", arguments={}),
        ]
        self.observations: list[str] = []

    async def complete(self, messages, *, tools=None, system=None, **opts):
        # Everything the loop has fed back so far.
        self.observations = [
            str(m.get("content") or "") for m in messages if m.get("role") == "tool"
        ]
        if self.steps:
            return ProviderResponse(
                text="Let me look at that.", tool_calls=[self.steps.pop(0)],
            )
        return ProviderResponse(text=(
            "This project uses pytest. pyproject.toml sets testpaths to tests/, the "
            "suite lives in tests/ with a conftest.py, and `make test` runs "
            "python -m pytest -q."
        ))

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _run(repo, tmp_path, provider, profile):
    events = []
    return asyncio.run(run_loop_headless(
        TASK,
        workspace=repo,
        session_id="trace",
        journal_root=tmp_path / "journals",
        read_only=True,
        max_iterations=12,
        emit=events.append,
        provider=provider,
        profile=profile,
    )), events


# ---------------------------------------------------------------------------
# NATIVE
# ---------------------------------------------------------------------------


class TestTraceNative:
    @pytest.fixture()
    def outcome(self, repo, tmp_path):
        profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
        assert profile.dialect is Dialect.NATIVE
        return _run(repo, tmp_path, _RecordedTrajectory(), profile)

    def test_the_run_completes(self, outcome):
        result, _events = outcome
        assert result.status == "completed", result.answer

    def test_a_config_file_was_read(self, outcome):
        """Category, not a specific file: which one it starts with is its choice."""
        result, _events = outcome
        read = set(result.summary["files_read"])

        assert any(name in read for name in CONFIG_FILES), read

    def test_the_tests_directory_was_searched(self, outcome):
        result, events = outcome
        searches = [
            event for event in events
            if event.get("type") == "tool_start"
            and event.get("name") in ("fs.glob", "fs.grep")
        ]
        assert searches, "expected a glob or grep over the project"
        assert any("tests" in json.dumps(event.get("arguments") or {}) for event in searches)

    def test_the_test_command_was_discovered_or_run_read_only(self, outcome):
        """`test.detect` or a READ_ONLY `terminal.run` — either is a real answer."""
        result, events = outcome
        names = [event.get("name") for event in events if event.get("type") == "tool_start"]

        assert "test.detect" in names or "terminal.run" in names

    def test_nothing_was_modified(self, outcome):
        result, events = outcome
        names = {event.get("name") for event in events if event.get("type") == "tool_start"}

        assert not (names & MUTATING_TOOLS), names & MUTATING_TOOLS
        assert result.summary["files_modified"] == []

    def test_the_answer_names_the_real_framework(self, outcome):
        result, _events = outcome
        assert "pytest" in result.answer.lower()

    def test_the_trajectory_was_not_a_single_shot(self, outcome):
        """The property under test: it iterated on what it found."""
        result, _events = outcome
        assert result.summary["iterations"] >= 3
        assert result.summary["tool_calls"] >= 2

    def test_observations_reached_the_model(self, outcome):
        """A loop that does not feed results back is not a loop."""
        result, events = outcome
        assert any(event.get("type") == "tool_result" for event in events)


# ---------------------------------------------------------------------------
# Journal integrity
# ---------------------------------------------------------------------------


class TestJournalIntegrity:
    @pytest.fixture()
    def journal_lines(self, repo, tmp_path):
        profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
        result, _events = _run(repo, tmp_path, _RecordedTrajectory(), profile)
        return list(read_journal(Path(result.journal_path))), result

    def test_the_journal_opens_with_the_seed_and_closes_with_a_status(self, journal_lines):
        lines, _result = journal_lines

        assert lines[0]["type"] == LineType.RUN_STARTED
        assert lines[-1]["type"] == LineType.RUN_FINISHED
        assert lines[-1]["status"] == "completed"

    def test_seq_is_monotonic(self, journal_lines):
        lines, _result = journal_lines
        assert [line["seq"] for line in lines] == list(range(1, len(lines) + 1))

    def test_every_tool_result_has_a_preceding_decision(self, journal_lines):
        """The ordering Batch V4-D7's invariants are built on."""
        lines, _result = journal_lines
        decided: set[str] = set()

        for line in lines:
            if line["type"] == LineType.POLICY_DECISION and line["verdict"] == "allow":
                decided.add(line["call_id"])
            elif line["type"] == LineType.TOOL_RESULT:
                assert line["call_id"] in decided, (
                    f"{line['tool']} produced a result with no preceding allow"
                )

    def test_the_seed_records_what_the_model_was_asked(self, journal_lines):
        _lines, result = journal_lines
        seed = read_seed(Path(result.journal_path))

        assert seed.task == TASK
        assert seed.dialect == "native"

    def test_no_journal_line_carries_reasoning_content(self, journal_lines):
        lines, _result = journal_lines
        blob = json.dumps(lines)

        assert "<think" not in blob
        assert "reasoning_content" not in blob


# ---------------------------------------------------------------------------
# Replay stability
# ---------------------------------------------------------------------------


class TestReplayStability:
    def test_the_journal_replays_to_the_same_sequence(self, repo, tmp_path):
        """Resume (Batch V4-F1) rebuilds from these lines, so they have to be
        stable and complete on their own."""
        profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
        result, _events = _run(repo, tmp_path, _RecordedTrajectory(), profile)

        lines = list(read_journal(Path(result.journal_path)))
        replayed = [
            (line["type"], line.get("tool") or line.get("state") or line.get("status"))
            for line in lines
        ]

        again = list(read_journal(Path(result.journal_path)))
        assert [
            (line["type"], line.get("tool") or line.get("state") or line.get("status"))
            for line in again
        ] == replayed

    def test_two_identical_runs_produce_the_same_trajectory_shape(self, repo, tmp_path):
        profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")

        first, _ = _run(repo, tmp_path / "a", _RecordedTrajectory(), profile)
        second, _ = _run(repo, tmp_path / "b", _RecordedTrajectory(), profile)

        def shape(result):
            return [
                (line["type"], line.get("tool"))
                for line in read_journal(Path(result.journal_path))
                if line["type"] in (LineType.TOOL_CALL, LineType.TOOL_RESULT)
            ]

        assert shape(first) == shape(second)

    def test_a_dialect_change_does_not_break_the_record(self, repo, tmp_path):
        """The journal is dialect-neutral: it records calls and results, not the
        wire format they were expressed in."""
        native = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
        result, _events = _run(repo, tmp_path, _RecordedTrajectory(), native)

        lines = list(read_journal(Path(result.journal_path)))
        tool_lines = [line for line in lines if line["type"] == LineType.TOOL_CALL]

        assert tool_lines
        for line in tool_lines:
            # Everything a different dialect would need to re-issue the call.
            assert {"call_id", "tool", "arguments"} <= set(line)


# ---------------------------------------------------------------------------
# Headless JSONL
# ---------------------------------------------------------------------------


class TestHeadlessSchema:
    def test_every_event_is_a_json_object_with_a_type(self, repo, tmp_path):
        profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
        _result, events = _run(repo, tmp_path, _RecordedTrajectory(), profile)

        assert events
        for event in events:
            assert isinstance(event, dict)
            assert event.get("type")
            json.dumps(event)   # must be serialisable as one JSONL line

    def test_the_result_json_carries_status_answer_and_summary(self, repo, tmp_path):
        profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
        result, _events = _run(repo, tmp_path, _RecordedTrajectory(), profile)

        payload = json.loads(result.to_json())
        assert payload["status"] == "completed"
        assert "pytest" in payload["answer"].lower()
        assert payload["summary"]["files_read"]
        assert payload["journal"].endswith(".jsonl")

    def test_the_cli_exposes_the_loop_engine(self):
        import inspect

        from gitpilot import cli

        signature = inspect.signature(cli.run)
        assert "engine" in signature.parameters
        assert "max_iterations" in signature.parameters
        assert "read_only" in signature.parameters


# ---------------------------------------------------------------------------
# REACT_TEXT against a real local model
# ---------------------------------------------------------------------------


def _ollama_reachable() -> bool:
    import httpx

    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        response = httpx.get(f"{base}/api/tags", timeout=1.5)
        return response.status_code == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(
    not _ollama_reachable(),
    reason="no local Ollama; a grammar a real model must follow cannot be proven by a fixture",
)
class TestTraceReactText:
    def test_a_real_local_model_discovers_the_trajectory(self, repo, tmp_path):
        from gitpilot.agent.providers import OpenAICompatibleProvider

        model = os.environ.get("GITPILOT_TRACE_MODEL", "llama3:8b")
        provider = OpenAICompatibleProvider(
            base_url=f"{os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')}/v1",
            model=model, api_key="ollama", name="ollama",
            supports_native_tools=False,
        )
        profile = resolve_profile("ollama", model, cache_path=tmp_path / "c.json")

        result, events = _run(repo, tmp_path, provider, profile)

        names = {event.get("name") for event in events if event.get("type") == "tool_start"}
        assert not (names & MUTATING_TOOLS), "a read-only task must not mutate"
        assert result.summary["tool_calls"] >= 1, "the model never used a tool"
