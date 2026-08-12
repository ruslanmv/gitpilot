"""The harness must be trustworthy before the tools lean on it — Batch V4-A2."""
from __future__ import annotations

import json

import pytest

from gitpilot.toolkit import (
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    LocalWorkspace,
)

from .fixture_repo import FILES, build_fixture_repo
from .harness import (
    ParityCase,
    ParityMismatch,
    assert_parity,
    record_corpus,
    run_case,
)

ECHO_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _registry(reply):
    reg = ToolRegistry()

    async def handler(call, ctx):
        return ToolResult.success(call, reply(call.arguments["text"]))

    reg.register(
        ToolSpec(
            id="demo.echo",
            title="Echo",
            description="Echo the argument back.",
            params_schema=ECHO_SCHEMA,
            risk=Risk.SAFE,
        ),
        handler,
    )
    return reg


class TestFixtureRepo:
    def test_builds_a_real_git_repo_offline(self, tmp_path):
        ws = build_fixture_repo(tmp_path / "repo")

        assert (ws.path / ".git").is_dir()
        assert ws.branch == "main"
        for rel in FILES:
            assert (ws.path / rel).is_file()

    def test_is_deterministic_across_builds(self, tmp_path):
        """Same content and metadata, so a parity diff is never fixture noise."""
        import subprocess

        shas = []
        for name in ("a", "b"):
            ws = build_fixture_repo(tmp_path / name)
            out = subprocess.run(
                ["git", "log", "-1", "--format=%T"],
                cwd=ws.path, capture_output=True, text=True, check=True,
            )
            shas.append(out.stdout.strip())

        assert shas[0] == shas[1], "fixture tree hash must not vary between builds"


class TestComparator:
    def test_identical_outputs_match(self, tmp_path):
        ws = build_fixture_repo(tmp_path / "repo")
        case = ParityCase(
            name="echo-match",
            tool="demo.echo",
            arguments={"text": "hi"},
            legacy=lambda _ws: "hi!",
        )
        outcome = run_case(
            case, ws, _registry(lambda t: f"{t}!"),
            ToolExecutionContext(workspace=LocalWorkspace(root=ws.path)),
        )
        assert outcome.matched
        assert_parity(outcome)

    def test_one_byte_divergence_is_caught(self, tmp_path):
        """The property that makes the gate worth anything."""
        ws = build_fixture_repo(tmp_path / "repo")
        case = ParityCase(
            name="echo-drift",
            tool="demo.echo",
            arguments={"text": "hi"},
            legacy=lambda _ws: "hi!",
        )
        outcome = run_case(
            case, ws, _registry(lambda t: f"{t}!."),
            ToolExecutionContext(workspace=LocalWorkspace(root=ws.path)),
        )
        assert not outcome.matched
        with pytest.raises(ParityMismatch) as exc:
            assert_parity(outcome)
        assert "legacy" in str(exc.value) and "registry" in str(exc.value)

    def test_normalisation_applies_to_both_sides(self, tmp_path):
        """It may hide run-specific noise, never a difference in shape."""
        ws = build_fixture_repo(tmp_path / "repo")
        case = ParityCase(
            name="echo-normalised",
            tool="demo.echo",
            arguments={"text": "hi"},
            legacy=lambda _ws: "hi took 12 ms",
            normalise=lambda text: text.replace("12", "N").replace("34", "N"),
        )
        outcome = run_case(
            case, ws, _registry(lambda t: f"{t} took 34 ms"),
            ToolExecutionContext(workspace=LocalWorkspace(root=ws.path)),
        )
        assert outcome.matched
        assert outcome.legacy_output != outcome.registry_output, "raw outputs are kept for review"


class TestRecorder:
    def test_writes_a_reviewable_snapshot(self, tmp_path):
        ws = build_fixture_repo(tmp_path / "repo")
        case = ParityCase(
            name="echo-record",
            tool="demo.echo",
            arguments={"text": "hi"},
            legacy=lambda _ws: "hi!",
        )
        outcome = run_case(
            case, ws, _registry(lambda t: f"{t}!"),
            ToolExecutionContext(workspace=LocalWorkspace(root=ws.path)),
        )

        destination = record_corpus([outcome], tmp_path / "out" / "corpus.json")
        payload = json.loads(destination.read_text())

        assert payload == [{
            "name": "echo-record",
            "tool": "demo.echo",
            "matched": True,
            "legacy": "hi!",
            "registry": "hi!",
        }]

    def test_recording_is_stable_for_the_same_outcomes(self, tmp_path):
        ws = build_fixture_repo(tmp_path / "repo")
        case = ParityCase(
            name="echo-stable", tool="demo.echo", arguments={"text": "hi"},
            legacy=lambda _ws: "hi!",
        )
        reg = _registry(lambda t: f"{t}!")
        ctx = ToolExecutionContext(workspace=LocalWorkspace(root=ws.path))

        first = record_corpus([run_case(case, ws, reg, ctx)], tmp_path / "one.json").read_text()
        second = record_corpus([run_case(case, ws, reg, ctx)], tmp_path / "two.json").read_text()

        assert first == second
