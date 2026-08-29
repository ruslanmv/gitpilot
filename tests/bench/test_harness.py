"""The benchmark harness — Batch V4-G4.

Two things are being tested, and the first is easy to overlook.

**Are the tasks discriminating?** A benchmark whose checkers pass on an untouched
workspace measures nothing, and a benchmark whose checkers cannot be made to pass
measures nothing either. So every edit task is checked twice: against the fixture
as built (must fail) and against a reference solution applied to that fixture (must
pass). That pair is what makes a task worth reporting.

**Is the gate honest?** A gate that green-lights an incomplete matrix is worse than
no gate, because it produces a number someone will cite. The cases below cover
every way a report can fail to be evidence: a tier missing, an engine that never
ran, a metric never observed.

Everything runs offline. The harness's engine seam is a Protocol precisely so these
tests can supply a scripted one — the real matrix needs Ollama and an API key, and
a test that needed those would not run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench import matrix as matrix_mod
from bench.matrix import ENGINES, TIERS, cells, tier_for
from bench.report import (
    FAIL,
    INCOMPLETE,
    PASS,
    REGRESSION_BUDGET,
    meets_gate,
    render_table,
    summarise,
)
from bench.runner import CellMetrics, CellRun, run_cell, run_matrix
from bench.tasks import TASKS, Outcome, task_for, tasks_for_tier


# ---------------------------------------------------------------------------
# A scripted engine
# ---------------------------------------------------------------------------


class ScriptedEngine:
    """An engine that applies a canned solution and reports canned metrics."""

    def __init__(
        self,
        name: str = "loop",
        *,
        solutions: dict | None = None,
        answers: dict | None = None,
        wall_time_s: float = 1.0,
        tokens: int = 1_000,
        approvals: int = 0,
        available_everywhere: bool = True,
        status: str = "completed",
    ) -> None:
        self.name = name
        self.solutions = solutions or {}
        self.answers = answers or {}
        self.wall_time_s = wall_time_s
        self.tokens = tokens
        self.approvals = approvals
        self._available = available_everywhere
        self.status = status
        self.seen: list[tuple[str, str]] = []

    def available(self, tier) -> bool:
        return self._available

    def run(self, task, tier, workspace: Path) -> CellMetrics:
        self.seen.append((task.id, tier.label))
        solution = self.solutions.get(task.id)
        if solution is not None:
            solution(workspace)
        return CellMetrics(
            wall_time_s=self.wall_time_s,
            prompt_tokens=self.tokens,
            completion_tokens=0,
            approvals=self.approvals,
            answer=self.answers.get(task.id, ""),
            status=self.status,
        )


# ---------------------------------------------------------------------------
# Reference solutions — what a correct run leaves behind
# ---------------------------------------------------------------------------


def _edit(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def _solve_fix_failing_test(root: Path) -> None:
    _edit(root, "src/app.py", 'f"Hello {name}"', 'f"Hello, {name}"')


def _solve_add_function(root: Path) -> None:
    path = root / "src/app.py"
    path.write_text(
        path.read_text(encoding="utf-8") + '\n\ndef farewell(name):\n    return f"Bye, {name}"\n',
        encoding="utf-8",
    )


def _solve_rename_symbol(root: Path) -> None:
    _edit(root, "src/app.py", "def greet(", "def hello(")
    _edit(root, "src/util.py", "from src.app import greet", "from src.app import hello")
    _edit(root, "src/util.py", "[greet(n)", "[hello(n)")


def _solve_add_test(root: Path) -> None:
    path = root / "tests/test_app.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n\nfrom src.util import greet_all\n\n\ndef test_greet_all():\n"
          '    assert greet_all(["a"]) == ["Hello, a"]\n',
        encoding="utf-8",
    )


def _solve_readme_typo(root: Path) -> None:
    _edit(root, "README.md", "smal project", "small project")


def _solve_bump_version(root: Path) -> None:
    _edit(root, "pyproject.toml", 'version = "0.3.1"', 'version = "0.4.0"')


def _solve_remove_dead_code(root: Path) -> None:
    path = root / "src/app.py"
    path.write_text(
        'def greet(name):\n    return f"Hello, {name}"\n', encoding="utf-8",
    )


SOLUTIONS = {
    "fix_failing_test": _solve_fix_failing_test,
    "add_function": _solve_add_function,
    "rename_symbol": _solve_rename_symbol,
    "add_test": _solve_add_test,
    "fix_readme_typo": _solve_readme_typo,
    "bump_version": _solve_bump_version,
    "remove_dead_code": _solve_remove_dead_code,
}

ANSWERS = {
    "read_framework": "It uses pytest; run `make test`.",
    "find_symbol": "greet is defined in src/app.py.",
    "count_tests": "There are 3 test functions.",
    "explain_dataflow": "src/util.py calls greet from src/app.py for each name.",
    "missing_file": "There is no src/parser.py in this project.",
    "refuse_write": "This run is read-only, so I cannot modify src/app.py.",
}


# ---------------------------------------------------------------------------
# The task set
# ---------------------------------------------------------------------------


class TestTaskSet:
    def test_the_set_is_the_documented_size(self):
        """§18 asks for 10–15 tasks."""
        assert 10 <= len(TASKS) <= 15

    def test_ids_are_unique(self):
        ids = [task.id for task in TASKS]
        assert len(ids) == len(set(ids))

    def test_every_task_has_a_prompt_and_a_checker(self):
        for task in TASKS:
            assert task.prompt.strip(), task.id
            assert callable(task.check), task.id
            assert callable(task.build), task.id

    def test_every_category_is_represented(self):
        categories = {task.category for task in TASKS}
        assert {"read", "edit", "fix", "negative"} <= categories

    def test_there_are_negative_tasks(self):
        """The ones no capability improvement can fake: scored on *not* doing
        something."""
        negatives = [task for task in TASKS if task.category == "negative"]
        assert len(negatives) >= 2
        assert all(task.read_only for task in negatives)

    def test_task_for_finds_and_misses(self):
        assert task_for("fix_failing_test") is not None
        assert task_for("no_such_task") is None

    @pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
    def test_the_fixture_builds(self, task, tmp_path):
        task.build(tmp_path)
        assert list(tmp_path.rglob("*")), f"{task.id} built nothing"


class TestTasksAreDiscriminating:
    """The property that makes the benchmark worth running."""

    @pytest.mark.parametrize("task_id", sorted(SOLUTIONS), ids=str)
    def test_an_untouched_workspace_fails(self, task_id, tmp_path):
        """A task that passes without the agent doing anything is free marks."""
        task = task_for(task_id)
        task.build(tmp_path)

        outcome = task.check(tmp_path, "")

        assert outcome.ok is False, f"{task_id} passes on an untouched fixture"
        assert outcome.detail, "a failure has to say why"

    @pytest.mark.parametrize("task_id", sorted(SOLUTIONS), ids=str)
    def test_the_reference_solution_passes(self, task_id, tmp_path):
        """A task nobody can pass measures nothing either."""
        task = task_for(task_id)
        task.build(tmp_path)
        SOLUTIONS[task_id](tmp_path)

        outcome = task.check(tmp_path, "")

        assert outcome.ok is True, f"{task_id}: {outcome.detail}"

    @pytest.mark.parametrize("task_id", sorted(ANSWERS), ids=str)
    def test_a_correct_answer_passes_and_an_empty_one_does_not(self, task_id, tmp_path):
        task = task_for(task_id)
        task.build(tmp_path)

        assert task.check(tmp_path, ANSWERS[task_id]).ok is True
        assert task.check(tmp_path, "").ok is False

    def test_a_syntactically_broken_edit_scores_zero(self, tmp_path):
        """String matching would give full marks to a plausible-looking function
        that does not parse, so the checkers execute the code."""
        task = task_for("add_function")
        task.build(tmp_path)
        (tmp_path / "src/app.py").write_text(
            'def greet(name):\n    return f"Hello, {name}"\n\n'
            "def farewell(name)\n    return 'Bye, ' + name\n",   # missing colon
            encoding="utf-8",
        )

        assert task.check(tmp_path, "").ok is False

    def test_a_function_that_returns_the_wrong_thing_scores_zero(self, tmp_path):
        task = task_for("add_function")
        task.build(tmp_path)
        (tmp_path / "src/app.py").write_text(
            'def greet(name):\n    return f"Hello, {name}"\n\n\n'
            'def farewell(name):\n    return f"Goodbye, {name}"\n',
            encoding="utf-8",
        )

        outcome = task.check(tmp_path, "")

        assert outcome.ok is False
        assert "Goodbye" in outcome.detail

    def test_breaking_the_neighbour_scores_zero(self, tmp_path):
        """Removing dead code by deleting the file would otherwise pass."""
        task = task_for("remove_dead_code")
        task.build(tmp_path)
        (tmp_path / "src/app.py").write_text("", encoding="utf-8")

        assert task.check(tmp_path, "").ok is False

    def test_the_hallucination_task_fails_a_created_file(self, tmp_path):
        task = task_for("missing_file")
        task.build(tmp_path)
        (tmp_path / "src" / "parser.py").write_text("def parse(): pass\n", encoding="utf-8")

        outcome = task.check(tmp_path, "src/parser.py does not exist")

        assert outcome.ok is False
        assert "created" in outcome.detail

    def test_the_read_only_task_fails_a_write(self, tmp_path):
        task = task_for("refuse_write")
        task.build(tmp_path)
        (tmp_path / "src" / "app.py").write_text(
            'def greet(name):\n    return f"HELLO, {name}".upper()\n', encoding="utf-8",
        )

        outcome = task.check(tmp_path, "This run is read-only.")

        assert outcome.ok is False
        assert "modified" in outcome.detail

    def test_the_read_only_task_also_wants_an_explanation(self, tmp_path):
        """Silently doing nothing is not the same as declining."""
        task = task_for("refuse_write")
        task.build(tmp_path)

        assert task.check(tmp_path, "Done!").ok is False
        assert task.check(tmp_path, "I cannot modify files in a read-only run.").ok is True


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


class TestMatrix:
    def test_there_is_one_tier_per_dialect(self):
        """The claim under test is that one engine drives all three, so a matrix
        with two frontier models in it would test nothing."""
        assert {tier.expected_dialect for tier in TIERS} == {"native", "react_text", "lite"}

    @pytest.mark.parametrize("tier", TIERS, ids=lambda t: t.label)
    def test_each_tiers_model_really_resolves_to_its_dialect(self, tier, tmp_path):
        from gitpilot.agent.model_profile import resolve_profile

        profile = resolve_profile(
            tier.family, tier.model, cache_path=tmp_path / f"{tier.label}.json",
        )

        assert profile.dialect.value == tier.expected_dialect

    def test_the_engines_are_the_two_under_comparison(self):
        assert ENGINES == ("legacy", "loop")

    def test_tier_for_finds_and_misses(self):
        assert tier_for("local_small") is not None
        assert tier_for("gpt2") is None

    def test_cells_are_tier_major(self):
        """A partial run should leave complete tiers, because the gate is
        per-tier — a matrix that covered every tier's first two tasks would
        support no comparison at all."""
        order = [tier.label for tier, _engine, _task in cells()]

        assert order == sorted(order, key=lambda label: order.index(label))

    def test_a_small_tier_is_not_asked_the_multi_file_tasks(self):
        """A tier's success rate should measure what it got wrong, not what it was
        never asked."""
        lite = {task.id for task in tasks_for_tier("lite")}
        native = {task.id for task in tasks_for_tier("native")}

        assert "rename_symbol" in native
        assert "rename_symbol" not in lite
        assert lite < native

    def test_cells_never_offer_a_tier_a_task_above_it(self):
        for tier, _engine, task_id in cells():
            allowed = {task.id for task in tasks_for_tier(tier.expected_dialect)}
            assert task_id in allowed

    def test_availability_is_probed_not_assumed(self, monkeypatch):
        monkeypatch.setattr(matrix_mod, "_ollama_reachable", lambda: False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        assert matrix_mod.available_tiers() == []
        assert len(matrix_mod.missing_tiers()) == len(TIERS)

    def test_an_unreachable_ollama_is_not_an_exception(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")

        assert matrix_mod._ollama_reachable() is False


# ---------------------------------------------------------------------------
# Running cells
# ---------------------------------------------------------------------------


class TestRunCell:
    def test_a_solved_task_is_a_pass(self, tmp_path):
        engine = ScriptedEngine(solutions=SOLUTIONS)

        result = run_cell(
            task_for("bump_version"), TIERS[0], engine, workspace_root=tmp_path,
        )

        assert result.ok is True
        assert result.metrics.wall_time_s == 1.0

    def test_an_unsolved_task_is_a_fail_with_a_reason(self, tmp_path):
        result = run_cell(
            task_for("bump_version"), TIERS[0], ScriptedEngine(), workspace_root=tmp_path,
        )

        assert result.ok is False
        assert result.outcome.detail

    def test_an_unavailable_engine_is_not_a_failure(self, tmp_path):
        """"Legacy scored zero on a machine where it cannot run" is not evidence."""
        result = run_cell(
            task_for("bump_version"), TIERS[0],
            ScriptedEngine(name="legacy", available_everywhere=False),
            workspace_root=tmp_path,
        )

        assert result.unavailable is True
        assert result.ok is False
        assert result.metrics.status == "unavailable"

    def test_a_timeout_is_a_failure_carrying_its_reason(self, tmp_path):
        engine = ScriptedEngine(status="timeout")
        engine.run = lambda task, tier, workspace: CellMetrics(  # noqa: ARG005
            wall_time_s=300.0, status="timeout", error="no answer within 300s",
        )

        result = run_cell(
            task_for("bump_version"), TIERS[0], engine, workspace_root=tmp_path,
        )

        assert result.ok is False
        assert "300s" in result.outcome.detail

    def test_the_fixture_is_built_before_the_engine_sees_it(self, tmp_path):
        seen = {}

        engine = ScriptedEngine()
        engine.run = lambda task, tier, workspace: (   # noqa: ARG005
            seen.update({"version": (workspace / "pyproject.toml").read_text()}),
            CellMetrics(status="completed"),
        )[1]

        run_cell(task_for("bump_version"), TIERS[0], engine, workspace_root=tmp_path)

        assert '0.3.1' in seen["version"]

    def test_each_cell_gets_its_own_workspace(self, tmp_path):
        engine = ScriptedEngine(solutions=SOLUTIONS)

        first = run_cell(task_for("bump_version"), TIERS[0], engine, workspace_root=tmp_path / "a")
        second = run_cell(task_for("bump_version"), TIERS[0], engine, workspace_root=tmp_path / "b")

        assert first.ok and second.ok

    def test_a_temporary_workspace_is_cleaned_up(self):
        engine = ScriptedEngine(solutions=SOLUTIONS)
        recorded: list[Path] = []

        original = engine.run

        def spy(task, tier, workspace):
            recorded.append(workspace)
            return original(task, tier, workspace)

        engine.run = spy
        run_cell(task_for("bump_version"), TIERS[0], engine)

        assert recorded and not recorded[0].exists()

    def test_run_matrix_covers_every_cell(self, tmp_path):
        engines = {"loop": ScriptedEngine(solutions=SOLUTIONS, answers=ANSWERS)}

        results = run_matrix(engines, tiers=[TIERS[0]])

        assert len(results) == len(tasks_for_tier(TIERS[0].expected_dialect))
        assert {result.engine for result in results} == {"loop"}

    def test_run_matrix_reports_progress(self):
        seen: list[CellRun] = []
        engines = {"loop": ScriptedEngine(solutions=SOLUTIONS, answers=ANSWERS)}

        results = run_matrix(
            engines, tiers=[TIERS[2]], task_ids=["bump_version"], on_cell=seen.append,
        )

        assert len(seen) == len(results) == 1


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _cell(tier: str, engine: str, *, ok: bool, wall: float = 1.0, tokens: int = 100) -> CellRun:
    return CellRun(
        task_id=f"t{int(ok)}", tier=tier, engine=engine,
        outcome=Outcome.passed() if ok else Outcome.failed("nope"),
        metrics=CellMetrics(wall_time_s=wall, prompt_tokens=tokens),
        category="edit",
    )


def _matched(tier: str, *, loop_ok: int, legacy_ok: int, total: int = 4, **kwargs) -> list[CellRun]:
    out = []
    for index in range(total):
        out.append(_cell(tier, "legacy", ok=index < legacy_ok))
        out.append(_cell(tier, "loop", ok=index < loop_ok, **kwargs))
    return out


class TestGate:
    def test_matching_success_within_budget_passes(self):
        report = summarise(_matched("frontier", loop_ok=3, legacy_ok=3, wall=1.4, tokens=140))

        assert report.tiers["frontier"].verdict() == PASS
        assert meets_gate(report) is True

    def test_beating_legacy_passes(self):
        report = summarise(_matched("frontier", loop_ok=4, legacy_ok=2))

        assert meets_gate(report) is True

    def test_losing_on_success_rate_fails(self):
        report = summarise(_matched("frontier", loop_ok=2, legacy_ok=3))

        assert report.tiers["frontier"].verdict() == FAIL
        assert "success rate" in " ".join(report.tiers["frontier"].reasons())

    def test_being_too_slow_fails_even_with_a_better_success_rate(self):
        report = summarise(_matched("frontier", loop_ok=4, legacy_ok=2, wall=2.0))

        assert report.tiers["frontier"].verdict() == FAIL
        assert "wall time" in " ".join(report.tiers["frontier"].reasons())

    def test_being_too_expensive_fails(self):
        report = summarise(_matched("frontier", loop_ok=4, legacy_ok=4, tokens=200))

        assert report.tiers["frontier"].verdict() == FAIL
        assert "tokens" in " ".join(report.tiers["frontier"].reasons())

    def test_dominance_is_explicitly_not_required(self):
        """A change that trades some latency for a lot of capability should pass —
        that is the trade this programme is making."""
        report = summarise(_matched("frontier", loop_ok=4, legacy_ok=1, wall=1.49, tokens=149))

        assert meets_gate(report) is True

    def test_a_missing_tier_makes_the_report_incomplete(self):
        report = summarise(
            _matched("frontier", loop_ok=4, legacy_ok=4), missing=["local_small"],
        )

        assert report.verdict() == INCOMPLETE
        assert meets_gate(report) is False

    def test_a_tier_with_no_loop_cells_is_incomplete(self):
        report = summarise([_cell("frontier", "legacy", ok=True)])

        assert report.tiers["frontier"].verdict() == INCOMPLETE
        assert meets_gate(report) is False
        assert "loop produced no cells" in " ".join(report.tiers["frontier"].reasons())

    def test_a_tier_where_legacy_was_unavailable_is_incomplete(self):
        """Not a win. The loop cannot beat an engine that never ran."""
        cells_ = _matched("frontier", loop_ok=4, legacy_ok=0)
        for cell in cells_:
            if cell.engine == "legacy":
                cell.unavailable = True

        report = summarise(cells_)

        assert report.tiers["frontier"].verdict() == INCOMPLETE
        assert meets_gate(report) is False

    def test_one_failing_tier_fails_the_whole_gate(self):
        report = summarise(
            _matched("frontier", loop_ok=4, legacy_ok=4)
            + _matched("local_small", loop_ok=1, legacy_ok=3),
        )

        assert report.tiers["frontier"].verdict() == PASS
        assert report.tiers["local_small"].verdict() == FAIL
        assert meets_gate(report) is False

    def test_an_empty_report_is_not_a_pass(self):
        assert meets_gate(summarise([])) is False

    def test_an_unobserved_baseline_does_not_constrain(self):
        """The legacy path keeps no journal, so its token count is unknown rather
        than zero — and unknown must not fail every comparison on a technicality."""
        cells_ = _matched("frontier", loop_ok=4, legacy_ok=4, tokens=5_000)
        for cell in cells_:
            if cell.engine == "legacy":
                cell.metrics.prompt_tokens = 0

        assert meets_gate(summarise(cells_)) is True

    def test_the_budget_is_the_documented_one(self):
        assert REGRESSION_BUDGET == 1.5

    def test_a_tighter_budget_can_be_asked_for(self):
        report = summarise(_matched("frontier", loop_ok=4, legacy_ok=4, wall=1.4))

        assert meets_gate(report, budget=1.5) is True
        assert meets_gate(report, budget=1.2) is False


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestReporting:
    def test_the_table_states_the_verdict(self):
        report = summarise(_matched("frontier", loop_ok=4, legacy_ok=4))

        rendered = render_table(report)

        assert "frontier" in rendered
        assert "legacy" in rendered and "loop" in rendered
        assert f"gate: {PASS}" in rendered

    def test_the_table_names_the_reason_for_a_failure(self):
        report = summarise(_matched("frontier", loop_ok=1, legacy_ok=4))

        assert "success rate" in render_table(report)

    def test_the_table_shows_a_tier_that_did_not_run(self):
        cells_ = _matched("frontier", loop_ok=4, legacy_ok=0)
        for cell in cells_:
            if cell.engine == "legacy":
                cell.unavailable = True

        rendered = render_table(summarise(cells_))

        assert "unavailable" in rendered

    def test_categories_are_reported_separately(self):
        """An average over thirteen tasks can hide "every edit got worse" behind
        "the reads got better"."""
        cells_ = _matched("frontier", loop_ok=4, legacy_ok=4)
        for cell in cells_[:4]:
            cell.category = "read"

        by_category = summarise(cells_).by_category()

        assert set(by_category) == {"read", "edit"}
        assert "by category" in render_table(summarise(cells_))

    def test_the_report_round_trips_through_json(self, tmp_path):
        from bench.report import load

        report = summarise(_matched("frontier", loop_ok=3, legacy_ok=3))
        path = report.write(tmp_path / "out" / "results.json")

        data = load(path)

        assert data["verdict"] == report.verdict()
        assert data["budget"] == REGRESSION_BUDGET
        assert len(data["cells"]) == 8

    def test_load_refuses_something_that_is_not_a_report(self, tmp_path):
        from bench.report import load

        path = tmp_path / "bad.json"
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        with pytest.raises(ValueError, match="does not contain a benchmark report"):
            load(path)

    def test_every_cell_is_serialisable(self):
        report = summarise(_matched("frontier", loop_ok=2, legacy_ok=2))

        json.dumps(report.to_dict())   # must not raise


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_list_shows_the_tasks_and_the_tiers(self, capsys):
        from bench.__main__ import main

        assert main(["--list"]) == 0
        out = capsys.readouterr().out
        assert "fix_failing_test" in out
        assert "local_small" in out

    def test_an_unknown_tier_is_an_error(self, capsys):
        from bench.__main__ import main

        assert main(["--tiers", "gpt2"]) == 2
        assert "unknown tier" in capsys.readouterr().err

    def test_no_available_tier_says_what_is_missing(self, capsys, monkeypatch):
        from bench.__main__ import main

        monkeypatch.setattr(matrix_mod, "_ollama_reachable", lambda: False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        assert main([]) == 2
        assert "no model tier is available" in capsys.readouterr().err

    def test_an_unknown_engine_is_an_error(self, capsys, monkeypatch):
        from bench.__main__ import main

        monkeypatch.setattr(matrix_mod, "_ollama_reachable", lambda: True)

        assert main(["--engines", "telepathy"]) == 2
        assert "no known engine" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The real loop engine, offline
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """A model that works the task through the real registry.

    Reacts to what it is offered rather than replaying a fixed script, so the run
    exercises the loop's dispatch, the policy engine and the journal — everything
    ``LoopEngine`` reads its metrics out of.
    """

    name = "scripted"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self, model: str, steps, answer: str) -> None:
        self.model = model
        self.steps = list(steps)
        self.answer = answer
        self.calls = 0

    async def complete(self, messages, *, tools=None, system=None, **opts):
        from gitpilot.agent.providers.base import ProviderResponse

        self.calls += 1
        if self.steps:
            return ProviderResponse(text="working", tool_calls=[self.steps.pop(0)])
        return ProviderResponse(text=self.answer)

    def stream(self, *args, **kwargs):  # pragma: no cover - never used
        raise NotImplementedError


@pytest.fixture()
def offline_frontier(monkeypatch):
    """The frontier tier, available, with the provider replaced."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    holder: dict = {}

    def fake_build_provider(*, model=None, **kwargs):
        return holder["provider"]

    import gitpilot.agent.providers as providers_mod

    monkeypatch.setattr(providers_mod, "build_provider", fake_build_provider)
    return holder


class TestLoopEngineDrivesTheRealLoop:
    def _call(self, call_id, name, **arguments):
        from gitpilot.agent.providers.base import RawToolCall

        return RawToolCall(id=call_id, name=name, arguments=arguments)

    def test_an_edit_task_passes_through_the_real_engine(self, offline_frontier, tmp_path):
        """The seam that would silently break: the harness has to actually drive
        ``AgentRunner`` and score what the run left on disk."""
        from bench.runner import LoopEngine

        task = task_for("bump_version")
        workspace = tmp_path / "project"
        workspace.mkdir()
        task.build(workspace)

        offline_frontier["provider"] = _ScriptedProvider(
            "claude-sonnet-4-5",
            steps=[
                self._call("c1", "fs.read", path="pyproject.toml"),
                self._call(
                    "c2", "fs.edit", path="pyproject.toml",
                    old_string='version = "0.3.1"', new_string='version = "0.4.0"',
                ),
            ],
            answer="Bumped the version to 0.4.0.",
        )

        metrics = LoopEngine(journal_root=tmp_path / "journals").run(
            task, tier_for("frontier"), workspace,
        )

        assert metrics.status in ("completed", "degraded"), metrics.error
        assert task.check(workspace, metrics.answer).ok is True

    def test_the_metrics_come_from_the_runs_own_journal(self, offline_frontier, tmp_path):
        """So the report's numbers are the ones a user could audit afterwards."""
        from bench.runner import LoopEngine

        task = task_for("bump_version")
        workspace = tmp_path / "project"
        workspace.mkdir()
        task.build(workspace)

        offline_frontier["provider"] = _ScriptedProvider(
            "claude-sonnet-4-5",
            steps=[self._call("c1", "fs.read", path="pyproject.toml")],
            answer="Read it.",
        )

        metrics = LoopEngine(journal_root=tmp_path / "journals").run(
            task, tier_for("frontier"), workspace,
        )

        assert metrics.tool_calls >= 1
        assert metrics.wall_time_s > 0
        assert metrics.iterations >= 1

    def test_a_read_only_task_runs_under_the_read_only_topology(self, offline_frontier, tmp_path):
        """``refuse_write`` is scored on refusing, so the harness has to put the run
        in a topology that actually refuses."""
        from bench.runner import LoopEngine

        task = task_for("refuse_write")
        workspace = tmp_path / "project"
        workspace.mkdir()
        task.build(workspace)

        offline_frontier["provider"] = _ScriptedProvider(
            "claude-sonnet-4-5",
            steps=[
                self._call("c1", "fs.read", path="src/app.py"),
                self._call(
                    "c2", "fs.write", path="src/app.py",
                    content='def greet(name):\n    return f"HELLO, {name}"\n',
                ),
            ],
            answer="I cannot modify files: this run is read-only.",
        )

        metrics = LoopEngine(journal_root=tmp_path / "journals").run(
            task, tier_for("frontier"), workspace,
        )

        assert task.check(workspace, metrics.answer).ok is True, "the write was applied"
        assert metrics.denials >= 1, "nothing was refused"

    def test_a_crash_inside_the_engine_is_one_failed_cell(self, offline_frontier, tmp_path):
        """One bad cell must not end the matrix."""
        from bench.runner import LoopEngine

        class _Exploding(_ScriptedProvider):
            async def complete(self, messages, **kwargs):
                raise RuntimeError("the provider fell over")

        task = task_for("bump_version")
        workspace = tmp_path / "project"
        workspace.mkdir()
        task.build(workspace)
        offline_frontier["provider"] = _Exploding("claude-sonnet-4-5", [], "")

        metrics = LoopEngine(journal_root=tmp_path / "journals").run(
            task, tier_for("frontier"), workspace,
        )

        assert metrics.status in ("error", "failed", "degraded")
        assert metrics.wall_time_s >= 0

    def test_a_timeout_is_reported_rather_than_waited_out(self, offline_frontier, tmp_path):
        from bench.runner import LoopEngine

        class _Slow(_ScriptedProvider):
            async def complete(self, messages, **kwargs):
                import asyncio

                await asyncio.sleep(5)
                raise AssertionError("should have timed out")

        task = task_for("bump_version")
        workspace = tmp_path / "project"
        workspace.mkdir()
        task.build(workspace)
        offline_frontier["provider"] = _Slow("claude-sonnet-4-5", [], "")

        metrics = LoopEngine(journal_root=tmp_path / "journals", timeout_s=0.2).run(
            task, tier_for("frontier"), workspace,
        )

        assert metrics.status == "timeout"
        assert "0s" in metrics.error or "within" in metrics.error

    def test_a_dialect_that_does_not_match_the_tier_is_flagged(
        self, offline_frontier, tmp_path, caplog,
    ):
        """A cell whose model no longer resolves to the tier's dialect is no longer
        testing what the matrix claims, and the report should not pretend it is."""
        from bench.runner import LoopEngine
        from gitpilot.agent import model_profile as profile_mod
        from gitpilot.agent.contracts import Dialect

        task = task_for("read_framework")
        workspace = tmp_path / "project"
        workspace.mkdir()
        task.build(workspace)
        offline_frontier["provider"] = _ScriptedProvider(
            "claude-sonnet-4-5", steps=[], answer="pytest",
        )

        real = profile_mod.resolve_profile

        def downgraded(family, model, **kwargs):
            return real(family, model, **kwargs).forced(Dialect.LITE)

        monkey = pytest.MonkeyPatch()
        monkey.setattr(profile_mod, "resolve_profile", downgraded)
        try:
            with caplog.at_level("WARNING"):
                LoopEngine(journal_root=tmp_path / "journals").run(
                    task, tier_for("frontier"), workspace,
                )
        finally:
            monkey.undo()

        assert "no longer tests what the matrix claims" in caplog.text

    def test_the_legacy_engine_is_unavailable_without_a_repo(self):
        from bench.runner import LegacyEngine

        assert LegacyEngine().available(tier_for("frontier")) is False

    def test_the_engine_forces_on_the_flags_it_measures(self, offline_frontier, tmp_path):
        """Found by writing the read-only cell: with ``agent_policy`` off — its
        default — ``AgentRunner`` falls back to Phase C's permissive stub, and the
        harness happily scored a write that the shipped policy forbids. A benchmark
        deciding whether to flip the default has to measure the engine as it will be
        after the flip."""
        from bench.runner import LoopEngine

        assert "agent_policy" in LoopEngine.required_flags
        assert "agent_loop" in LoopEngine.required_flags

    def test_the_flags_are_restored_afterwards(self, offline_frontier, tmp_path):
        """A benchmark that leaves the process's flags on would change the
        behaviour of whatever ran next."""
        from bench.runner import LoopEngine
        from gitpilot import flags

        flags.clear_all_overrides()
        before = {name: flags.is_on(name) for name in LoopEngine.required_flags}

        task = task_for("read_framework")
        workspace = tmp_path / "project"
        workspace.mkdir()
        task.build(workspace)
        offline_frontier["provider"] = _ScriptedProvider(
            "claude-sonnet-4-5", steps=[], answer="pytest",
        )

        LoopEngine(journal_root=tmp_path / "journals").run(
            task, tier_for("frontier"), workspace,
        )

        assert {name: flags.is_on(name) for name in LoopEngine.required_flags} == before
