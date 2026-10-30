"""Run the quality benchmarks against models served by OllaBridge.

    python -m benchmarks.run --list
    python -m benchmarks.run --models qwen2.5:1.5b,llama3:8b
    python -m benchmarks.run --models-file benchmarks/models.txt --repeats 3
    python -m benchmarks.run --models provider/some-model --suites code,tools,agentic

Writes ``benchmarks/results/<run-id>.json`` (scores and manifest),
``<run-id>.transcripts.jsonl`` (every reply, for audit) and re-renders
``benchmarks/report.html`` from every compatible result file.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import urlopen

from . import HARNESS_VERSION
from .client import ChatClient, ChatResult
from .naming import IDENTITY_PROMPT, display_name, needs_identity
from .suites import CHAT_SUITES, SUITE_TITLES, Task, load_tasks, task_set_digest

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
# 127.0.0.1, not "localhost": on Windows, Python resolves localhost to ::1 first,
# and a port that WSL's mirrored networking forwards from Linux leaves that IPv6
# attempt hanging for Windows' 21-second connect retry before IPv4 is tried.
# Every request would carry 21 s of dead time (curl races both and hides it).
DEFAULT_GATEWAY = os.environ.get("BENCH_BASE_URL", "http://127.0.0.1:11435/v1")
DEFAULT_OLLAMA = os.environ.get("BENCH_OLLAMA_URL", "http://127.0.0.1:11434/v1")

#: Stop a model after this many infrastructure failures in a row with no success:
#: a gateway that is down must not be scored as a model that is wrong.
CIRCUIT_BREAKER = 3


def short_name(model_id: str) -> str:
    """A generic display name from the id alone (see ``naming``)."""
    return display_name(model_id)[0]


def _get_json(url: str, timeout: float = 10.0) -> Any:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - operator-supplied local URL
        return json.loads(response.read())


def gateway_models(base_url: str) -> Dict[str, Dict[str, Any]]:
    """``{model id: catalog entry}`` as the gateway lists them."""
    try:
        data = _get_json(f"{base_url.rstrip('/')}/models")
    except Exception as exc:  # noqa: BLE001 - reported, not fatal
        print(f"⚠️  Could not list models at {base_url}: {exc}", file=sys.stderr)
        return {}
    items = data.get("data", data) if isinstance(data, dict) else data
    return {str(item.get("id")): item for item in items or [] if isinstance(item, dict) and item.get("id")}


def gateway_health(base_url: str) -> Dict[str, Any]:
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        data = _get_json(f"{root}/health", timeout=5)
        return {k: data.get(k) for k in ("status", "mode", "default_model", "auth_mode") if k in data}
    except Exception as exc:  # noqa: BLE001
        return {"status": f"unreachable ({exc.__class__.__name__})"}


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE.parent,  # noqa: S603,S607
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def read_models_file(path: Path) -> Dict[str, str]:
    """``{model id: display name or ""}``; a line is ``id`` or ``id = Display Name``."""
    models: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        model, _, name = line.partition("=")
        models[model.strip()] = name.strip()
    return models


def identify(client: ChatClient, model: str) -> str:
    """Ask the model what it is (one short request); empty when it will not say."""
    reply = client.chat(model, [{"role": "user", "content": IDENTITY_PROMPT}], max_tokens=512)
    return reply.text if reply.ok else ""


def route_for(model: str, owned_by: str, *, route: str, gateway: str, ollama: str) -> tuple[str, str]:
    """Where to send one model, and the route's name for the report.

    OllaBridge forwards only the output cap to local Ollama (not temperature or
    seed), so for a fair, repeatable comparison local models go straight to
    Ollama's own OpenAI endpoint unless ``--route gateway`` is given.
    """
    if route == "auto" and owned_by == "ollama":
        return ollama, "ollama-direct"
    return gateway, "gateway"


def run_model(client: ChatClient, model: str, tasks: List[Task], *, repeats: int,
              transcripts, seed: Optional[int]) -> tuple[List[Dict[str, Any]], str]:
    rows: List[Dict[str, Any]] = []
    infra_streak = 0
    any_success = False
    for index, task in enumerate(tasks, 1):
        for repeat in range(1, repeats + 1):
            if infra_streak >= CIRCUIT_BREAKER and not any_success:
                rows.append(_skipped(task, repeat, "skipped: model unavailable"))
                continue
            reply = client.chat(model, task.messages, tools=task.tools,
                                max_tokens=task.max_tokens, seed=seed)
            row = _grade(task, repeat, reply)
            rows.append(row)
            transcripts.write(json.dumps({
                "model": model, "task": task.id, "repeat": repeat, "text": reply.text,
                "tool_calls": reply.tool_calls, "error_kind": reply.error_kind,
                "error_detail": reply.error_detail, "latency_s": round(reply.latency_s, 3),
            }, ensure_ascii=False) + "\n")
            transcripts.flush()
            if reply.is_rate_limited:
                pass  # the server is throttling, not down: neither evidence of an outage nor of health
            elif row["infra_error"]:
                infra_streak += 1
            else:
                infra_streak, any_success = 0, True
            mark = "ok " if row["passed"] else ("ERR" if row["infra_error"] else "FAIL")
            print(f"  {mark} [{index:>2}/{len(tasks)}] {task.id:<34} {reply.latency_s:6.1f}s  "
                  f"{row['detail'][:70]}", flush=True)
    status = "unavailable" if (infra_streak >= CIRCUIT_BREAKER and not any_success) else "ok"
    return rows, status


def _grade(task: Task, repeat: int, reply: ChatResult) -> Dict[str, Any]:
    tokens, estimated = reply.output_tokens()
    row: Dict[str, Any] = {
        "task": task.id, "suite": task.suite, "category": task.category, "repeat": repeat,
        "latency_s": round(reply.latency_s, 3), "attempts": reply.attempts,
        "error_kind": reply.error_kind, "served_model": reply.served_model,
        "output_tokens": tokens if reply.ok else None, "tokens_estimated": estimated,
        "tool_calls_made": len(reply.tool_calls),
    }
    if not reply.ok:
        infra = reply.is_infra_error
        row.update(passed=None if infra else False, infra_error=infra,
                   detail=f"{reply.error_kind}: {reply.error_detail[:160]}")
        return row
    try:
        verdict = task.grade(reply)
    except Exception as exc:  # noqa: BLE001 - a grader bug must not end the run
        row.update(passed=False, infra_error=False, detail=f"grader error: {exc!r}")
        return row
    row.update(passed=verdict.passed, infra_error=False, detail=verdict.detail)
    return row


def _skipped(task: Task, repeat: int, detail: str) -> Dict[str, Any]:
    return {"task": task.id, "suite": task.suite, "category": task.category, "repeat": repeat,
            "passed": None, "infra_error": True, "detail": detail, "latency_s": 0.0,
            "attempts": 0, "error_kind": "skipped", "output_tokens": None, "tokens_estimated": False}


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default="", help="comma-separated model ids, as the gateway lists them")
    parser.add_argument("--models-file", type=Path, help="one model id per line; '#' starts a comment")
    parser.add_argument("--suites", default=",".join(CHAT_SUITES),
                        help=f"comma-separated: {', '.join(SUITE_TITLES)} (agentic is slow, off by default)")
    parser.add_argument("--repeats", type=int, default=1, help="runs per task; >1 measures consistency")
    parser.add_argument("--base-url", default=DEFAULT_GATEWAY, help="OllaBridge (or any OpenAI-compatible) /v1 URL")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA, help="Ollama's own /v1 URL for local models")
    parser.add_argument("--route", choices=("auto", "gateway"), default="auto",
                        help="auto: local Ollama models go direct so temperature and seed apply; "
                             "gateway: everything through --base-url")
    parser.add_argument("--api-key", default=os.environ.get("BENCH_API_KEY", ""),
                        help="bearer key for the gateway (env BENCH_API_KEY); not needed in local-trust mode")
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds per request")
    parser.add_argument("--retries", type=int, default=1, help="retries for timeouts, 429 and 5xx only")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--agentic-timeout", type=float, default=600.0)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--report", type=Path, default=HERE / "report.html")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--environment-label",
                        default=os.environ.get("BENCH_ENVIRONMENT", "Anonymized test environment"),
                        help="how the report describes where the run happened; nothing else about "
                             "the host, gateway or platform is shown")
    parser.add_argument("--list", action="store_true", help="list the gateway's models and exit")
    parser.add_argument("--new", action="store_true",
                        help="add the gateway's models that have no result for this task set yet")
    parser.add_argument("--match", default="", help="with --new: only model ids containing this text")
    parser.add_argument("--owned-by", default="", help="with --new: only models this provider owns (e.g. ollama, openwebui)")
    parser.add_argument("--limit", type=int, default=3, help="with --new: at most this many models per run")
    return parser.parse_args(argv)


def untested_models(listed: Dict[str, str], results_dir: Path, digest: str, *,
                    match: str = "", owned_by: str = "", limit: int = 3) -> tuple[List[str], List[str]]:
    """Gateway models with no successful result for ``digest``: (to run now, left for later).

    This is the "a new model appeared in OllaBridge" path: re-running the same
    task set on just the newcomers keeps every score comparable with the history.
    """
    tested = set()
    for path in results_dir.glob("*.json"):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if run.get("manifest", {}).get("task_set_digest") != digest:
            continue
        tested |= {m["id"] for m in run.get("models", []) if m.get("status") == "ok"}
    candidates = [model for model, owner in sorted(listed.items())
                  if model not in tested
                  and (not match or match.lower() in model.lower())
                  and (not owned_by or owner == owned_by)]
    return candidates[: max(0, limit)], candidates[max(0, limit):]


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    listed = gateway_models(args.base_url)

    if args.list:
        health = gateway_health(args.base_url)
        print(f"Gateway {args.base_url}: {health.get('status')}")
        for model_id, meta in sorted(listed.items(), key=lambda kv: (str(kv[1].get("owned_by")), kv[0])):
            owner = str(meta.get("owned_by") or "")
            hint = "  (asks the model to identify itself)" if needs_identity(model_id) else ""
            print(f"  {owner:<10} {model_id:<48} → {short_name(model_id)}{hint}")
        return 0

    suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    unknown = [s for s in suites if s not in SUITE_TITLES]
    if unknown:
        print(f"Unknown suite(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    tasks = load_tasks([s for s in suites if s in CHAT_SUITES])

    overrides: Dict[str, str] = {m.strip(): "" for m in args.models.split(",") if m.strip()}
    if args.models_file:
        overrides.update(read_models_file(args.models_file))
    if args.new:
        owners = {model: str(meta.get("owned_by") or "") for model, meta in listed.items()}
        fresh, later = untested_models(owners, args.out_dir, task_set_digest(tasks), match=args.match,
                                       owned_by=args.owned_by, limit=args.limit)
        print(f"New on the gateway and not yet benchmarked: {len(fresh) + len(later)}; running {len(fresh)}.")
        if later:
            print(f"   Left for a later run (raise --limit): {', '.join(short_name(m) for m in later)}")
        overrides.update({model: overrides.get(model, "") for model in fresh})
    models = list(overrides)
    if not models:
        print("No models to run. Use --models, --models-file or --new (see --list).", file=sys.stderr)
        return 2

    # The pid keeps ids unique when several runs start in the same second (one
    # process per remote model is the fast way to benchmark a batch).
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    result_path = args.out_dir / f"{run_id}.json"
    transcript_path = args.out_dir / f"{run_id}.transcripts.jsonl"

    manifest = {
        "harness_version": HARNESS_VERSION,
        "gitpilot_commit": git_commit(),
        "task_set_digest": task_set_digest(tasks),
        "suites": suites,
        "task_count": len(tasks),
        "params": {"temperature": 0, "top_p": 1, "seed": args.seed, "repeats": args.repeats,
                   "timeout_s": args.timeout, "retries": args.retries,
                   "max_tokens": {s: max((t.max_tokens for t in tasks if t.suite == s), default=None)
                                  for s in suites if s in CHAT_SUITES}},
        "environment": args.environment_label,
        "gateway_status": gateway_health(args.base_url).get("status"),
        "python": platform.python_version(),
        # Where the run happened: kept in the local result file for reference,
        # never rendered into the report.
        "private": {"base_url": args.base_url, "ollama_url": args.ollama_url,
                    "platform": platform.platform()},
    }
    model_rows: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with transcript_path.open("w", encoding="utf-8") as transcripts:
        for model in models:
            meta = listed.get(model, {})
            owned_by = str(meta.get("owned_by") or "")
            if listed and model not in listed:
                print(f"⚠️  {model} is not in the gateway's model list; trying it anyway.")
            endpoint, route = route_for(model, owned_by, route=args.route,
                                        gateway=args.base_url, ollama=args.ollama_url)
            client = ChatClient(endpoint, api_key=args.api_key if route == "gateway" else "",
                                timeout_s=args.timeout, retries=args.retries)
            claimed = identify(client, model) if not overrides.get(model) and needs_identity(model) else ""
            name, name_source = display_name(model, override=overrides.get(model), claimed=claimed,
                                             vendor=str(meta.get("upstream_owned_by") or ""))
            print(f"\n▶ {name}  ({route}, {len(tasks)} tasks × {args.repeats})", flush=True)
            began = time.monotonic()
            rows, status = run_model(client, model, tasks, repeats=args.repeats,
                                     transcripts=transcripts, seed=args.seed)
            if "agentic" in suites and status == "ok":
                from .suites.agentic import run_agentic

                print("  … GitPilot agent tasks (slow)", flush=True)
                gateway_root = args.base_url.rstrip("/").removesuffix("/v1")
                agentic = run_agentic(model, gateway_root=gateway_root, timeout_s=args.agentic_timeout)
                for row in agentic:
                    row["repeat"] = 1
                rows += agentic
            for row in rows:
                row["model"] = model
            all_rows += rows
            model_rows.append({"id": model, "name": name, "name_source": name_source, "route": route,
                               "owned_by": owned_by, "status": status,
                               "elapsed_s": round(time.monotonic() - began, 1)})

    payload = {"run_id": run_id, "started_at": started,
               "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "manifest": manifest, "models": model_rows, "results": all_rows}
    result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults:     {result_path}\nTranscripts: {transcript_path}")

    if not args.no_report:
        from .report import render_reports

        report = render_reports(sorted(args.out_dir.glob("*.json")), args.report)
        print(f"Report:      {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
