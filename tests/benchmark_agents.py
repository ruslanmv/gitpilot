#!/usr/bin/env python3
"""Benchmark AI code generation from hello.py to a 100-file project.

This script can run in two modes:
1) simulate: generate realistic synthetic benchmark data.
2) command: run your real CLI/agent command against progressively harder prompts.

Outputs:
- Console table report
- CSV + JSON raw data
- Markdown summary
- HTML dashboard with plots and analysis insights

Example usage:
    python tests/benchmark_agents.py --simulate
    python tests/benchmark_agents.py --command-template "gitpilot \"{prompt}\"" --repeats 3
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import math
import random
import statistics
import subprocess
import sys
import textwrap
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Scenario:
    """A generation benchmark scenario with gradual complexity growth."""

    level: int
    name: str
    target_files: int
    prompt: str


@dataclass(slots=True)
class RunResult:
    """Single benchmark execution result."""

    timestamp: str
    mode: str
    topology_id: str
    scenario_level: int
    scenario_name: str
    target_files: int
    repeat: int
    duration_s: float
    success: bool
    exit_code: int
    stdout_chars: int
    stderr_chars: int
    estimated_context_tokens: int
    quality_score: float
    throughput_files_per_min: float


def progressive_targets(max_files: int) -> list[int]:
    """Build progressive test sizes from tiny to max_files."""
    seeds = [1, 2, 3, 5, 8, 13, 21, 34, 55, 80, 100]
    values = sorted({v for v in seeds if v <= max_files})
    if max_files not in values:
        values.append(max_files)
    return sorted(set(values))


def build_scenarios(max_files: int) -> list[Scenario]:
    """Create complexity ladder starting from hello.py up to large project generation."""
    targets = progressive_targets(max_files)
    scenarios: list[Scenario] = []

    for idx, files in enumerate(targets, start=1):
        if files == 1:
            name = "hello.py"
            prompt = "Create hello.py that prints 'Hello, World!' and include a short docstring."
        elif files <= 5:
            name = "small-script-pack"
            prompt = (
                "Generate a tiny Python package with CLI entrypoint, tests, README, and simple"
                " utility modules."
            )
        elif files <= 20:
            name = "service-starter"
            prompt = (
                "Generate a production-minded Python service starter with API, settings,"
                " logging, tests, and lint config."
            )
        elif files <= 55:
            name = "multi-module-app"
            prompt = (
                "Generate a medium-size project with architecture layers, reusable components,"
                " test suite, CI workflow, and docs."
            )
        else:
            name = "full-github-project"
            prompt = (
                "Generate a complete GitHub-ready project with docs, CI/CD, packaging,"
                " observability, tests, examples, and modular source code."
            )

        stress_hint = (
            f" Target approx {files} files. Keep quality high while handling larger context "
            f"and cross-file consistency (level {idx})."
        )
        scenarios.append(
            Scenario(
                level=idx,
                name=name,
                target_files=files,
                prompt=prompt + stress_hint,
            )
        )

    return scenarios


def estimate_context_tokens(target_files: int, scenario_level: int) -> int:
    """Rough context-size proxy for stress analysis."""
    base = 350
    file_factor = target_files * 95
    complexity_factor = int((scenario_level**1.4) * 120)
    return base + file_factor + complexity_factor


def simulated_run(
    scenario: Scenario,
    repeat: int,
    topology_id: str,
    rng: random.Random,
) -> RunResult:
    """Generate realistic synthetic benchmark output for planning and dashboards."""
    context_tokens = estimate_context_tokens(scenario.target_files, scenario.level)

    # Latency grows non-linearly with context + some jitter.
    core = 0.4 + (scenario.target_files**1.18) * 0.08
    jitter = rng.uniform(0.85, 1.25)
    duration_s = max(0.2, core * jitter)

    # Success probability gently decreases with complexity.
    fail_risk = min(0.45, scenario.target_files / 260)
    success = rng.random() > fail_risk
    exit_code = 0 if success else 1

    stdout_chars = int(500 + scenario.target_files * rng.uniform(120, 340))
    stderr_chars = 0 if success else int(rng.uniform(60, 420))

    # Heuristic quality score (0-100).
    score_base = 92 - scenario.target_files * 0.19 - scenario.level * 0.9
    quality_score = max(5.0, min(99.0, score_base + rng.uniform(-6.5, 5.0)))
    if not success:
        quality_score *= 0.55

    throughput = (scenario.target_files / max(duration_s, 0.001)) * 60

    return RunResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        mode="simulate",
        topology_id=topology_id,
        scenario_level=scenario.level,
        scenario_name=scenario.name,
        target_files=scenario.target_files,
        repeat=repeat,
        duration_s=round(duration_s, 4),
        success=success,
        exit_code=exit_code,
        stdout_chars=stdout_chars,
        stderr_chars=stderr_chars,
        estimated_context_tokens=context_tokens,
        quality_score=round(quality_score, 2),
        throughput_files_per_min=round(throughput, 2),
    )


def command_run(
    scenario: Scenario,
    repeat: int,
    topology_id: str,
    command_template: str,
    timeout_s: int,
) -> RunResult:
    """Execute a real CLI command against the scenario prompt."""
    context_tokens = estimate_context_tokens(scenario.target_files, scenario.level)

    command = command_template.format(
        prompt=scenario.prompt,
        prompt_json=json.dumps(scenario.prompt),
        files=scenario.target_files,
        level=scenario.level,
        name=scenario.name,
        topology=topology_id,
    )

    started = time.perf_counter()
    try:
        completed = subprocess.run(  # noqa: S602
            command,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        duration_s = time.perf_counter() - started
        success = completed.returncode == 0
        exit_code = completed.returncode
        stdout_chars = len(completed.stdout)
        stderr_chars = len(completed.stderr)
    except subprocess.TimeoutExpired as exc:
        duration_s = time.perf_counter() - started
        success = False
        exit_code = 124
        stdout_chars = len(exc.stdout or "")
        stderr_chars = len(exc.stderr or "") + 20

    # Quality proxy when running real command:
    # balanced score based on success + signal volume + time penalty.
    response_signal = min(1.0, (stdout_chars + stderr_chars) / 12000)
    time_penalty = min(0.75, duration_s / 180)
    quality_score = (80 if success else 30) + response_signal * 18 - time_penalty * 25
    quality_score = max(0.0, min(100.0, quality_score))

    throughput = (scenario.target_files / max(duration_s, 0.001)) * 60

    return RunResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        mode="command",
        topology_id=topology_id,
        scenario_level=scenario.level,
        scenario_name=scenario.name,
        target_files=scenario.target_files,
        repeat=repeat,
        duration_s=round(duration_s, 4),
        success=success,
        exit_code=exit_code,
        stdout_chars=stdout_chars,
        stderr_chars=stderr_chars,
        estimated_context_tokens=context_tokens,
        quality_score=round(quality_score, 2),
        throughput_files_per_min=round(throughput, 2),
    )


def group_by_scenario(results: list[RunResult]) -> dict[tuple[int, int], list[RunResult]]:
    buckets: dict[tuple[int, int, str], list[RunResult]] = {}
    for row in results:
        key = (row.scenario_level, row.target_files, row.topology_id)
        buckets.setdefault(key, []).append(row)
    return buckets


def summarize(results: list[RunResult]) -> list[dict[str, Any]]:
    """Aggregate repeats into scenario-level metrics."""
    rows: list[dict[str, Any]] = []
    for (_level, _files, _topology), items in sorted(group_by_scenario(results).items()):
        durations = [x.duration_s for x in items]
        scores = [x.quality_score for x in items]
        throughputs = [x.throughput_files_per_min for x in items]
        success_rate = sum(1 for x in items if x.success) / len(items)

        sample = items[0]
        rows.append(
            {
                "level": sample.scenario_level,
                "scenario": sample.scenario_name,
                "files": sample.target_files,
                "topology": sample.topology_id,
                "runs": len(items),
                "success_rate": round(success_rate * 100, 1),
                "avg_duration_s": round(statistics.mean(durations), 3),
                "p95_duration_s": round(percentile(durations, 95), 3),
                "avg_quality": round(statistics.mean(scores), 2),
                "avg_throughput_fpm": round(statistics.mean(throughputs), 2),
                "avg_context_tokens": int(statistics.mean([x.estimated_context_tokens for x in items])),
            }
        )
    return rows


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100)
    low = math.floor(k)
    high = math.ceil(k)
    if low == high:
        return ordered[int(k)]
    return ordered[low] * (high - k) + ordered[high] * (k - low)


def format_table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "Lvl",
        "Scenario",
        "Topology",
        "Files",
        "Success%",
        "AvgSec",
        "P95Sec",
        "Quality",
        "Files/Min",
        "CtxTok",
    ]
    data = [
        [
            str(r["level"]),
            str(r["scenario"]),
            str(r["topology"]),
            str(r["files"]),
            f"{r['success_rate']:.1f}",
            f"{r['avg_duration_s']:.2f}",
            f"{r['p95_duration_s']:.2f}",
            f"{r['avg_quality']:.1f}",
            f"{r['avg_throughput_fpm']:.1f}",
            str(r["avg_context_tokens"]),
        ]
        for r in rows
    ]

    widths = [len(h) for h in headers]
    for row in data:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    def render_line(cols: list[str]) -> str:
        return " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(cols))

    sep = "-+-".join("-" * w for w in widths)
    lines = [render_line(headers), sep]
    lines.extend(render_line(row) for row in data)
    return "\n".join(lines)


def save_csv(results: list[RunResult], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        for row in results:
            writer.writerow(asdict(row))


def save_json(results: list[RunResult], summary_rows: list[dict[str, Any]], path: Path) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_runs": len(results),
        "summary": summary_rows,
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def insight_lines(summary_rows: list[dict[str, Any]]) -> list[str]:
    if not summary_rows:
        return ["No data available."]

    hardest = max(summary_rows, key=lambda r: r["files"])
    fastest = min(summary_rows, key=lambda r: r["avg_duration_s"])
    best_quality = max(summary_rows, key=lambda r: r["avg_quality"])

    return [
        (
            f"Fastest scenario: {fastest['scenario']} ({fastest['files']} files) at "
            f"{fastest['avg_duration_s']}s average."
        ),
        (
            f"Most difficult scenario observed: {hardest['scenario']} ({hardest['files']} files) "
            f"with {hardest['success_rate']}% success."
        ),
        (
            f"Best average quality: {best_quality['scenario']} = {best_quality['avg_quality']}/100."
        ),
        "Use these trends to tune prompt size, chunking strategy, and model-routing thresholds.",
    ]


def resolve_topologies(topologies_arg: str) -> list[str]:
    """Resolve requested topology IDs.

    Accepts comma-separated IDs or 'all'. Falls back to ['default'] if the
    topology registry cannot be imported in this environment.
    """
    raw = (topologies_arg or "default").strip()
    if not raw:
        return ["default"]

    try:
        from gitpilot.topology_registry import list_topologies
    except Exception:
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        try:
            from gitpilot.topology_registry import list_topologies  # type: ignore[redefined-outer-name]
        except Exception:
            if raw.lower() == "all":
                return ["default"]
            return [part.strip() for part in raw.split(",") if part.strip()]

    raw_available = list_topologies()
    available: list[str] = []
    for item in raw_available:
        if hasattr(item, "id"):
            available.append(item.id)
        elif isinstance(item, dict) and "id" in item:
            available.append(str(item["id"]))
    if raw.lower() == "all":
        return available

    requested = [part.strip() for part in raw.split(",") if part.strip()]
    filtered = [tid for tid in requested if tid in available]
    return filtered or ["default"]


def markdown_summary(summary_rows: list[dict[str, Any]], insights: list[str]) -> str:
    lines = ["# Benchmark Summary", "", "## Scenario Table", ""]
    lines.append(format_table(summary_rows))
    lines.extend(["", "## Insights", ""])
    lines.extend(f"- {line}" for line in insights)
    return "\n".join(lines)


def render_plot_base64(summary_rows: list[dict[str, Any]]) -> str:
    """Render chart image as base64 PNG; return empty string if matplotlib unavailable."""
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return ""

    files = [r["files"] for r in summary_rows]
    durations = [r["avg_duration_s"] for r in summary_rows]
    success = [r["success_rate"] for r in summary_rows]
    quality = [r["avg_quality"] for r in summary_rows]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4), dpi=120)

    axes[0].plot(files, durations, marker="o", color="#2563eb")
    axes[0].set_title("Latency vs Files")
    axes[0].set_xlabel("Target files")
    axes[0].set_ylabel("Avg duration (s)")
    axes[0].grid(alpha=0.2)

    axes[1].plot(files, success, marker="o", color="#16a34a")
    axes[1].set_title("Success Rate")
    axes[1].set_xlabel("Target files")
    axes[1].set_ylabel("Success %")
    axes[1].set_ylim(0, 100)
    axes[1].grid(alpha=0.2)

    axes[2].plot(files, quality, marker="o", color="#7c3aed")
    axes[2].set_title("Quality Score")
    axes[2].set_xlabel("Target files")
    axes[2].set_ylabel("Quality / 100")
    axes[2].set_ylim(0, 100)
    axes[2].grid(alpha=0.2)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def render_dashboard_html(
    summary_rows: list[dict[str, Any]],
    insights: list[str],
    table_text: str,
    image_b64: str,
    output_path: Path,
) -> None:
    img_section = (
        f'<img alt="benchmark plots" src="data:image/png;base64,{image_b64}"/>'
        if image_b64
        else '<p class="warn">matplotlib not installed: plots were skipped.</p>'
    )

    rows_html = "\n".join(
        "<tr>"
        + "".join(
            f"<td>{row[k]}</td>"
            for k in [
                "level",
                "scenario",
                "topology",
                "files",
                "success_rate",
                "avg_duration_s",
                "p95_duration_s",
                "avg_quality",
                "avg_throughput_fpm",
                "avg_context_tokens",
            ]
        )
        + "</tr>"
        for row in summary_rows
    )

    html = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>AI Code Generation Benchmark Dashboard</title>
<style>
body {{ font-family: Inter, Arial, sans-serif; margin: 24px; background: #f8fafc; color: #0f172a; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ margin-bottom: 4px; }}
.subtitle {{ color: #475569; margin-top: 0; }}
.panel {{ background: white; border-radius: 12px; padding: 16px 18px; margin: 14px 0; box-shadow: 0 1px 3px rgba(2,6,23,.08); }}
pre {{ background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px; overflow: auto; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ border-bottom: 1px solid #e2e8f0; padding: 8px; text-align: left; font-size: 14px; }}
th {{ background: #f1f5f9; }}
ul {{ margin-top: 8px; }}
.warn {{ color: #b45309; font-weight: 600; }}
img {{ width: 100%; border-radius: 10px; border: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class=\"container\">
  <h1>AI Generation Benchmark Dashboard</h1>
  <p class=\"subtitle\">Generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>

  <div class=\"panel\">
    <h2>Quick Table</h2>
    <pre>{table_text}</pre>
  </div>

  <div class=\"panel\">
    <h2>Plots</h2>
    {img_section}
  </div>

  <div class=\"panel\">
    <h2>Summary Table</h2>
    <table>
      <thead>
        <tr>
          <th>Level</th><th>Scenario</th><th>Topology</th><th>Files</th><th>Success %</th>
          <th>Avg Sec</th><th>P95 Sec</th><th>Quality</th><th>Files/Min</th><th>Context Tokens</th>
        </tr>
      </thead>
      <tbody>
      {rows_html}
      </tbody>
    </table>
  </div>

  <div class=\"panel\">
    <h2>Insights</h2>
    <ul>
      {''.join(f'<li>{item}</li>' for item in insights)}
    </ul>
  </div>
</div>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark AI code generation complexity from 1 to 100 files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Command template placeholders:
              {prompt}       raw prompt string
              {prompt_json}  JSON-escaped prompt string
              {files}        target file count
              {level}        scenario level
              {name}         scenario name
              {topology}     topology id

            Example:
              python tests/benchmark_agents.py --command-template "gitpilot {prompt_json}" --repeats 3
            """
        ),
    )
    parser.add_argument("--simulate", action="store_true", help="Use synthetic benchmark data.")
    parser.add_argument(
        "--command-template",
        type=str,
        default="",
        help="Shell command template to execute real generation benchmarks.",
    )
    parser.add_argument("--repeats", type=int, default=1, help="Runs per scenario.")
    parser.add_argument("--timeout", type=int, default=600, help="Command timeout in seconds.")
    parser.add_argument("--max-files", type=int, default=100, help="Maximum files to stress-test.")
    parser.add_argument(
        "--topologies",
        type=str,
        default="default",
        help="Comma-separated topology IDs, or 'all' to benchmark every topology.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_results"),
        help="Directory to write reports and dashboard assets.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for simulate mode.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    mode = "simulate" if args.simulate or not args.command_template else "command"

    scenarios = build_scenarios(max_files=max(1, args.max_files))
    rng = random.Random(args.seed)
    topologies = resolve_topologies(args.topologies)

    results: list[RunResult] = []
    for topology_id in topologies:
        for scenario in scenarios:
            for repeat in range(1, args.repeats + 1):
                if mode == "simulate":
                    row = simulated_run(
                        scenario=scenario,
                        repeat=repeat,
                        topology_id=topology_id,
                        rng=rng,
                    )
                else:
                    row = command_run(
                        scenario=scenario,
                        repeat=repeat,
                        topology_id=topology_id,
                        command_template=args.command_template,
                        timeout_s=args.timeout,
                    )
                results.append(row)

    if not results:
        print("No benchmark data generated.", file=sys.stderr)
        return 1

    summary_rows = summarize(results)
    table_text = format_table(summary_rows)
    insights = insight_lines(summary_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "benchmark_raw.csv"
    json_path = args.output_dir / "benchmark_report.json"
    md_path = args.output_dir / "benchmark_summary.md"
    html_path = args.output_dir / "benchmark_dashboard.html"

    save_csv(results, csv_path)
    save_json(results, summary_rows, json_path)
    md_path.write_text(markdown_summary(summary_rows, insights), encoding="utf-8")

    chart_b64 = render_plot_base64(summary_rows)
    render_dashboard_html(summary_rows, insights, table_text, chart_b64, html_path)

    print("\n=== Benchmark Report ===")
    print(table_text)
    print("\nInsights:")
    for line in insights:
        print(f"- {line}")
    print("\nArtifacts:")
    print(f"- CSV:  {csv_path}")
    print(f"- JSON: {json_path}")
    print(f"- MD:   {md_path}")
    print(f"- HTML: {html_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
