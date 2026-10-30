"""The quality benchmarks in ``benchmarks/``: graders, sandbox, runner and report.

A benchmark is only as trustworthy as its graders, so every task is checked
twice: its reference answer must pass, and a plausible wrong answer must fail.
Everything here runs offline against a scripted client.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from benchmarks import client as client_mod
from benchmarks import naming
from benchmarks import run as run_mod
from benchmarks.client import ChatResult, parse_completion, strip_reasoning
from benchmarks.profiles import profile_key, suggest_dialect, write_suggestions
from benchmarks.report import merge_runs, render_html, summarise
from benchmarks.sandbox import extract_code, run_tests, static_check
from benchmarks.stats import intervals_overlap, wilson_interval
from benchmarks.suites import load_tasks, task_set_digest
from benchmarks.suites.reasoning import extract_answer

ALL_TASKS = load_tasks(["code", "tools", "instructions", "reasoning"])


# ---------------------------------------------------------------------------
# Every task: the reference passes, a wrong answer fails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task", ALL_TASKS, ids=lambda t: t.id)
def test_the_reference_answer_passes(task):
    verdict = task.grade(task.reference)
    assert verdict.passed, f"{task.id}: {verdict.detail}"


WRONG_ANSWERS = {
    "code": ChatResult(text="```python\ndef unrelated():\n    return None\n```"),
    "tools": ChatResult(text="I would call a tool here, but I will just guess."),
    "instructions": ChatResult(text="Sure! Here is a long, rambling, Capitalised answer, with commas, "
                                    "that ignores the requested format entirely and goes on and on."),
    "reasoning": ChatResult(text="Answer: 999999"),
}


@pytest.mark.parametrize("task", [t for t in ALL_TASKS if not t.id.startswith("tools/relevance")
                                  and t.id != "tools/use_tool_result"], ids=lambda t: t.id)
def test_a_wrong_answer_fails(task):
    assert not task.grade(WRONG_ANSWERS[task.suite]).passed


@pytest.mark.parametrize("task_id", ["tools/relevance_arithmetic", "tools/relevance_concept",
                                     "tools/use_tool_result"])
def test_calling_a_tool_when_none_was_needed_fails(task_id):
    task = next(t for t in ALL_TASKS if t.id == task_id)
    verdict = task.grade(ChatResult(tool_calls=[{"name": "get_weather", "arguments": {"city": "Oslo"}}]))
    assert not verdict.passed
    assert "no tool was needed" in verdict.detail


def test_task_ids_are_unique_and_the_digest_is_stable():
    ids = [t.id for t in ALL_TASKS]
    assert len(ids) == len(set(ids))
    assert task_set_digest(ALL_TASKS) == task_set_digest(load_tasks(["code", "tools", "instructions", "reasoning"]))
    assert task_set_digest(ALL_TASKS) != task_set_digest(ALL_TASKS[:-1])


# ---------------------------------------------------------------------------
# Tool-call grading details
# ---------------------------------------------------------------------------


def _tool_task(task_id):
    return next(t for t in ALL_TASKS if t.id == f"tools/{task_id}")


def test_tool_arguments_are_compared_loosely_where_it_is_harmless():
    task = _tool_task("currency_eur_usd")
    reply = ChatResult(tool_calls=[{"name": "convert_currency",
                                    "arguments": {"amount": "250.0", "from_currency": "eur", "to_currency": "USD"}}])
    assert task.grade(reply).passed


def test_the_wrong_tool_or_a_wrong_argument_fails_with_the_reason():
    task = _tool_task("weather_unit")
    wrong_tool = task.grade(ChatResult(tool_calls=[{"name": "search_code", "arguments": {"pattern": "x"}}]))
    wrong_unit = task.grade(ChatResult(tool_calls=[{"name": "get_weather",
                                                    "arguments": {"city": "Copenhagen", "unit": "celsius"}}]))
    assert "instead of get_weather" in wrong_tool.detail
    assert "expected fahrenheit" in wrong_unit.detail


def test_unparseable_tool_arguments_fail():
    completion = {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "run_tests", "arguments": "{not json"}}]}}]}
    reply = parse_completion(json.dumps(completion))
    assert not _tool_task("tests_verbose").grade(reply).passed


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code, reason", [
    ("import os\nos.system('echo hi')", "imports os"),
    ("import subprocess", "imports subprocess"),
    ("from socket import socket", "imports socket"),
    ("open('x', 'w').write('y')", "uses open"),
    ("eval('1+1')", "uses eval"),
    ("().__class__.__bases__[0].__subclasses__()", "touches"),
    ("__import__('os')", "uses __import__"),
])
def test_the_static_check_refuses_escapes(code, reason):
    assert reason in static_check(code)


def test_allowed_standard_library_imports_run():
    result = run_tests("import re\nfrom collections import Counter\ndef f(s):\n    return Counter(re.findall('a', s))['a']",
                       "assert f('banana') == 3")
    assert result.passed, result.detail


def test_a_failing_test_reports_the_assertion_and_an_infinite_loop_times_out():
    failing = run_tests("def f():\n    return 1", "assert f() == 2, 'wrong value'")
    looping = run_tests("def f():\n    while True:\n        pass", "f()", timeout_s=2)
    assert not failing.passed and "wrong value" in failing.detail
    assert not looping.passed and "timed out" in looping.detail


def test_an_unclosed_fence_still_yields_the_code():
    """llama3 opens a ```python fence and stops; the code is still the answer."""
    assert extract_code("```python\ndef f():\n    return 1") == "def f():\n    return 1"


def test_code_is_taken_from_the_longest_fenced_block():
    text = "Here:\n```python\nx = 1\n```\nand the real one\n```python\ndef f():\n    return 42\n```"
    assert extract_code(text).startswith("def f()")
    assert extract_code("def g(): pass") == "def g(): pass"


# ---------------------------------------------------------------------------
# Client parsing and error accounting
# ---------------------------------------------------------------------------


def test_reasoning_blocks_are_stripped_before_grading():
    assert strip_reasoning("<think>let me see</think>\nAnswer: 5") == "Answer: 5"
    assert strip_reasoning("<think>ran out of tokens mid-thought") == ""


def test_a_completion_without_usage_or_model_is_still_read():
    """OllaBridge's gateway drops both; the harness estimates tokens and says so."""
    reply = parse_completion(json.dumps({"choices": [{"message": {"content": "pong"}}]}), 1.5)
    tokens, estimated = reply.output_tokens()
    assert reply.ok and reply.text == "pong" and reply.served_model == ""
    assert estimated and tokens >= 1


@pytest.mark.parametrize("kind, detail, infra, retryable", [
    ("timeout", "", True, True),
    ("connection", "refused", True, True),
    ("http_504", "provider_timeout", True, True),
    ("http_429", "rate limited", True, True),
    ("http_401", "bad key", True, False),
    ("http_400", '{"detail":"Model not found"}', True, False),
    ("http_400", "tools_unsupported: model does not support tools", False, False),
    ("http_400", "invalid request", False, False),
])
def test_errors_are_split_into_serving_failures_and_model_failures(kind, detail, infra, retryable):
    reply = ChatResult(error_kind=kind, error_detail=detail)
    assert reply.is_infra_error is infra
    assert reply.is_retryable is retryable


def test_rate_limits_wait_for_retry_after_and_have_their_own_budget(monkeypatch):
    replies = [ChatResult(error_kind="http_429", error_detail="slow down", retry_after_s=7.0),
               ChatResult(error_kind="http_429", error_detail="slow down"),
               ChatResult(text="ok")]
    monkeypatch.setattr(client_mod.ChatClient, "_once", lambda self, payload: replies.pop(0))
    waits = []
    client = client_mod.ChatClient("http://x/v1", retries=0, backoff_s=1, rate_limit_retries=4)
    client.sleep = waits.append

    result = client.chat("m", [{"role": "user", "content": "hi"}])

    assert result.ok and result.attempts == 3
    assert waits == [7.0, 4.0], "Retry-After honoured, then exponential back-off"


def test_rate_limiting_does_not_trip_the_unavailable_breaker():
    class Throttled(_ScriptedClient):
        def chat(self, model, messages, **kwargs):
            self.calls += 1
            if self.calls <= 5:
                return ChatResult(error_kind="http_429", error_detail="slow down")
            return super().chat(model, messages, **kwargs)

    rows, status = run_mod.run_model(Throttled(), "m", ALL_TASKS, repeats=1,
                                     transcripts=io.StringIO(), seed=7)
    assert status == "ok"
    assert sum(1 for r in rows if r["passed"]) == len(ALL_TASKS) - 5


def test_a_permanent_refusal_is_not_retried(monkeypatch):
    calls = []

    def fake_once(self, payload):
        calls.append(payload)
        return ChatResult(error_kind="http_400", error_detail='{"detail":"Model not found"}')

    monkeypatch.setattr(client_mod.ChatClient, "_once", fake_once)
    client = client_mod.ChatClient("http://x/v1", retries=3, backoff_s=0)
    assert client.chat("m", [{"role": "user", "content": "hi"}]).error_kind == "http_400"
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Reasoning extraction and statistics
# ---------------------------------------------------------------------------


def test_the_marked_answer_wins_over_other_numbers():
    assert extract_answer("12 * 0.75 = 9, minus 4\nAnswer: 5") == ("5", True)
    assert extract_answer("so it is 1,000 requests") == ("1000", False)
    assert extract_answer("no idea") == ("", False)


def test_wilson_interval_behaves_at_the_edges():
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0.25 < high < 0.35
    low, high = wilson_interval(10, 10)
    assert high == 1.0 and 0.65 < low < 0.75
    assert intervals_overlap((0.2, 0.6), (0.5, 0.9)) and not intervals_overlap((0.1, 0.2), (0.3, 0.4))


# ---------------------------------------------------------------------------
# Runner, end to end with a scripted client
# ---------------------------------------------------------------------------


class _ScriptedClient:
    """Answers every task with its reference, or fails every request."""

    def __init__(self, *, failing: bool = False):
        self.failing = failing
        self.calls = 0

    def chat(self, model, messages, *, tools=None, max_tokens=1024, seed=None):
        self.calls += 1
        if self.failing:
            return ChatResult(error_kind="http_504", error_detail="provider_timeout", latency_s=1.0)
        task = next(t for t in ALL_TASKS if t.messages == messages)
        reply = ChatResult(text=task.reference.text, tool_calls=task.reference.tool_calls, latency_s=0.5)
        return reply


def test_a_model_that_answers_everything_correctly_scores_every_suite_full():
    rows, status = run_mod.run_model(_ScriptedClient(), "m", ALL_TASKS, repeats=1,
                                     transcripts=io.StringIO(), seed=7)
    assert status == "ok"
    assert all(row["passed"] for row in rows)


def test_a_dead_route_is_stopped_early_and_never_scored():
    client = _ScriptedClient(failing=True)
    rows, status = run_mod.run_model(client, "m", ALL_TASKS, repeats=1, transcripts=io.StringIO(), seed=7)
    assert status == "unavailable"
    assert client.calls == run_mod.CIRCUIT_BREAKER
    assert all(row["passed"] is None and row["infra_error"] for row in rows)


def test_local_models_go_direct_so_sampling_is_controlled():
    assert run_mod.route_for("llama3:8b", "ollama", route="auto", gateway="G", ollama="O") == ("O", "ollama-direct")
    assert run_mod.route_for("openwebui/x", "openwebui", route="auto", gateway="G", ollama="O") == ("G", "gateway")
    assert run_mod.route_for("llama3:8b", "ollama", route="gateway", gateway="G", ollama="O") == ("G", "gateway")


def test_short_names_are_generic():
    assert run_mod.short_name("provider/internal-qwen9-coder-30b") == "qwen9-coder-30b"
    assert run_mod.short_name("deepseek-r1:latest") == "deepseek-r1"
    assert run_mod.short_name("qwen2.5:1.5b") == "qwen2.5:1.5b"


@pytest.mark.parametrize("model_id, expected", [
    ("provider/internal-llama9-70b", "llama9-70b"),
    ("provider/ext-mistral-large", "mistral-large"),
    ("provider/internal---gemma9-31b", "gemma9-31b"),
    ("provider/tuned-local-qwen9-7b", "qwen9-7b"),
])
def test_deployment_prefixes_never_reach_the_report(model_id, expected):
    assert naming.display_name(model_id) == (expected, "derived")


@pytest.mark.parametrize("model_id", ["provider/ext-openai-lite", "provider/internal---flash",
                                      "provider/internal-acme-glimmer-30b", "provider/vendor-standard"])
def test_opaque_aliases_need_the_model_to_identify_itself(model_id):
    assert naming.needs_identity(model_id)


def test_an_alias_is_named_by_its_own_answer_or_by_vendor_and_tier():
    assert naming.display_name("provider/ext-openai-lite", claimed="I am GPT-9 mini.") == (
        "GPT-9 mini (self-reported)", "self-reported")
    assert naming.display_name("provider/ext-openai-lite", claimed="", vendor="openai") == (
        "openai (lite)", "vendor")
    rambling = "As an AI developed to help you, I cannot share details about my architecture or version."
    assert naming.display_name("provider/ext-openai-lite", claimed=rambling, vendor="openai")[1] == "vendor"


def test_an_explicit_name_always_wins():
    assert naming.display_name("provider/internal-x", override="Coder A") == ("Coder A", "override")


def test_the_models_file_takes_display_names(tmp_path):
    listing = tmp_path / "models.txt"
    listing.write_text("# comment\nqwen2.5:1.5b\nprovider/internal-x = Coder A  # private\n", encoding="utf-8")
    assert run_mod.read_models_file(listing) == {"qwen2.5:1.5b": "", "provider/internal-x": "Coder A"}


def test_new_models_are_found_against_this_task_sets_history(tmp_path):
    digest = task_set_digest(ALL_TASKS)
    (tmp_path / "old.json").write_text(json.dumps({
        "manifest": {"task_set_digest": digest},
        "models": [{"id": "llama3:8b", "status": "ok"}, {"id": "openwebui/down", "status": "unavailable"}],
        "results": []}), encoding="utf-8")
    listed = {"llama3:8b": "ollama", "openwebui/down": "openwebui", "openwebui/new-a": "openwebui",
              "openwebui/new-b": "openwebui", "qwen2.5:1.5b": "ollama"}

    fresh, later = run_mod.untested_models(listed, tmp_path, digest, owned_by="openwebui", limit=2)

    assert fresh == ["openwebui/down", "openwebui/new-a"]  # an unavailable model is retried
    assert later == ["openwebui/new-b"]


# ---------------------------------------------------------------------------
# Report and profile suggestions
# ---------------------------------------------------------------------------


def _run(run_id, digest, model_id, rows, status="ok"):
    for row in rows:
        row.setdefault("model", model_id)
    return {"run_id": run_id, "manifest": {"task_set_digest": digest, "params": {}, "gateway": {}},
            "models": [{"id": model_id, "name": run_mod.short_name(model_id), "route": "gateway",
                        "status": status}],
            "results": rows}


def _row(task, suite, passed, **extra):
    return {"task": task, "suite": suite, "category": "x", "repeat": 1, "passed": passed,
            "infra_error": passed is None, "detail": "d", "latency_s": 2.0, "output_tokens": 40,
            "tokens_estimated": True, "tool_calls_made": extra.get("calls", 0)}


def test_the_report_never_shows_the_raw_id_or_where_the_run_happened():
    runs = [_run("r1", "D", "provider/internal-qwen9-coder-9b",
                 [_row("code/a", "code", True), _row("tools/b", "tools", False)])]
    runs[0]["manifest"].update(environment="Lab A", gateway_status="ok", python="3.11.9",
                               private={"base_url": "http://gateway.corp.example:11435/v1",
                                        "platform": "Windows-11-10.0.26200-SP0"})
    runs[0]["models"][0]["route"] = "ollama-direct"
    merged, notes = merge_runs(runs)
    page = render_html(merged, notes)
    assert "qwen9-coder-9b" in page
    for leak in ("provider/", "internal-", "gateway.corp.example", "11435", "Windows-11", "ollama"):
        assert leak not in page.lower(), leak
    assert "Lab A" in page and "local runtime" in page


@pytest.mark.parametrize("detail, expected", [
    ('http_400: {"error":{"message":"registry.example.ai/library/m:8b does not support tools"}}',
     "refused: the model does not support tool calls"),
    ("http_504: provider/secret-model's provider did not answer in time", "serving error (HTTP 504)"),
    ("timeout: no answer within 180s", "serving error (timeout)"),
    ("answered 7, expected 5", "answered 7, expected 5"),
    ("see https://host.example.com/x and acme/coder-7b", "see <url> and <model>"),
])
def test_failure_reasons_in_the_report_name_no_infrastructure(detail, expected):
    assert naming.scrub_detail(detail) == expected


def test_model_names_link_to_their_public_page_and_only_over_https(tmp_path):
    from benchmarks.report import load_links

    links_file = tmp_path / "links.txt"
    links_file.write_text("# c\ncoder-a = https://huggingface.co/acme/Coder-A\n"
                          "bad = javascript:alert(1)\nplain = http://example.com/x\n", encoding="utf-8")
    links = load_links(links_file)
    assert links == {"coder-a": "https://huggingface.co/acme/Coder-A"}

    run = _run("r1", "D", "provider/internal-x", [_row("code/a", "code", True)])
    run["models"][0].update(name="coder-a", name_source="override")
    page = render_html(*merge_runs([run]), links=links)
    assert "href='https://huggingface.co/acme/Coder-A'" in page
    assert "rel='noopener noreferrer'" in page


def test_the_shipped_links_file_holds_only_https_hugging_face_pages():
    from benchmarks.report import LINKS_FILE, load_links

    raw = [line for line in LINKS_FILE.read_text(encoding="utf-8").splitlines()
           if line.strip() and not line.lstrip().startswith("#")]
    links = load_links()
    assert len(links) == len(raw)
    assert all(url.startswith("https://huggingface.co/") for url in links.values())
    assert not any("internal" in name or "ext-" in name for name in links)


def test_an_old_result_file_with_a_raw_name_is_scrubbed_on_render():
    run = _run("r1", "D", "provider/internal-gemma9-2b", [_row("code/a", "code", True)])
    run["models"][0]["name"] = "internal-gemma9-2b"  # written by an older harness
    page = render_html(*merge_runs([run]))
    assert "gemma9-2b" in page and "internal-gemma9-2b" not in page


def test_runs_with_another_task_set_are_left_out_and_the_report_says_so():
    runs = [_run("r1", "OLD", "a", [_row("code/a", "code", True)]),
            _run("r2", "NEW", "b", [_row("code/a", "code", False)])]
    merged, notes = merge_runs(runs)
    assert [m["id"] for m in merged["models"]] == ["b"]
    assert notes and "r1" in notes[0]


def test_serving_errors_do_not_count_against_a_model():
    runs = [_run("r1", "D", "m", [_row("code/a", "code", True), _row("code/b", "code", None)])]
    model = summarise(merge_runs(runs)[0])["models"][0]
    assert model["suites"]["code"]["rate"] == 1.0
    assert model["errors"] == 1


@pytest.mark.parametrize("tools, instructions, dialect", [
    (0.9, 0.5, "native"), (0.6, 0.4, "react_text"), (0.2, 0.7, "react_text"), (0.1, 0.3, "lite"),
])
def test_dialect_suggestions_follow_the_scores(tools, instructions, dialect):
    suites = {"tools": {"rate": tools}, "instructions": {"rate": instructions}}
    assert suggest_dialect(suites)["dialect"] == dialect


def test_suggestions_use_gitpilots_model_key_and_parse_with_its_loader(tmp_path):
    from gitpilot.agent.model_profile import load_yaml_overrides

    models = [{"name": "coder-x", "profile_key": profile_key("provider/Coder-X"),
               "suggestion": {"dialect": "native", "why": "tool calling 90%"}},
              {"name": "qwen2.5:1.5b", "profile_key": profile_key("qwen2.5:1.5b"),
               "suggestion": {"dialect": "lite", "why": "tool calling 10%"}}]
    target = tmp_path / ".gitpilot" / "models.yaml"
    target.parent.mkdir()
    write_suggestions(models, target, digest="D")

    loaded = load_yaml_overrides(tmp_path)

    assert loaded["coder-x"]["dialect"] == "native"
    assert loaded["qwen2.5:1.5b"]["dialect"] == "lite"


def test_regrading_applies_a_grader_fix_to_saved_replies(tmp_path):
    """The stored transcript is the evidence: a grader fix re-scores it without the model."""
    from benchmarks.regrade import regrade_run

    task = next(t for t in ALL_TASKS if t.id == "code/normalize_whitespace")
    tasks = load_tasks(["code"])
    run = {"run_id": "R", "manifest": {"task_set_digest": task_set_digest(tasks), "suites": ["code"]},
           "models": [{"id": "m", "name": "m", "status": "ok"}],
           "results": [{"model": "m", "task": task.id, "suite": "code", "category": "strings", "repeat": 1,
                        "passed": False, "infra_error": False, "detail": "rejected before running: syntax error"}]}
    (tmp_path / "R.json").write_text(json.dumps(run), encoding="utf-8")
    (tmp_path / "R.transcripts.jsonl").write_text(json.dumps({
        "model": "m", "task": task.id, "repeat": 1, "error_kind": None, "tool_calls": [],
        "text": "```python\ndef normalize_whitespace(s):\n    return ' '.join(s.split())"}) + "\n",
        encoding="utf-8")

    assert regrade_run(tmp_path / "R.json") == 1
    regraded = json.loads((tmp_path / "R.json").read_text(encoding="utf-8"))
    assert regraded["results"][0]["passed"] is True and "regraded_at" in regraded


def test_report_page_renders_for_an_empty_history(tmp_path):
    merged, notes = merge_runs([])
    page = render_html(merged, notes)
    assert "<html" in page and "No model produced" in page
    out = tmp_path / "report.html"
    from benchmarks.report import render_reports

    render_reports([], out)
    assert out.exists() and (tmp_path / "model_profiles.suggested.yaml").read_text().startswith("#")


def test_the_package_needs_nothing_outside_the_standard_library():
    """It has to run from any Python 3.11 on the box, with or without GitPilot's venv."""
    import ast

    stdlib = set(__import__("sys").stdlib_module_names)
    for path in Path("benchmarks").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name in stdlib or name == "benchmarks", f"{path}: imports {name}"
