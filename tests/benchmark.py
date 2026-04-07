#!/usr/bin/env python3
"""
GitPilot Code Generation Benchmark
===================================

Graduated stress test for the `gitpilot generate` CLI command.
Measures extraction quality, generation speed, and content validity
across tiers of increasing complexity — from a single hello.py
up to full project scaffolds.

Usage:
    # Run all tiers with the default model
    python tests/benchmark.py

    # Run specific tiers
    python tests/benchmark.py --tiers 1 2 3

    # Use a specific model
    python tests/benchmark.py --model llama3

    # Save JSON results
    python tests/benchmark.py --output results.json

    # Generate HTML dashboard
    python tests/benchmark.py --dashboard report.html

    # Quick smoke test (tier 1 only)
    python tests/benchmark.py --quick
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ── Benchmark tier definitions ──────────────────────────────────────

@dataclass
class BenchmarkTier:
    """A single benchmark test case."""
    id: int
    name: str
    prompt: str
    expected_files: int
    required_patterns: list[str] = field(default_factory=list)
    description: str = ""

    def __str__(self) -> str:
        return f"Tier {self.id}: {self.name} ({self.expected_files} files)"


TIERS: list[BenchmarkTier] = [
    BenchmarkTier(
        id=1,
        name="hello",
        prompt="Create a Python file called hello.py that prints 'Hello, World!'",
        expected_files=1,
        required_patterns=["hello.py"],
        description="Single file — trivial generation",
    ),
    BenchmarkTier(
        id=2,
        name="flask-app",
        prompt=(
            "Create a Flask hello world web application with these files: "
            "app.py (Flask app with / route), requirements.txt (Flask dependency), "
            "README.md (project description with install and run instructions)"
        ),
        expected_files=3,
        required_patterns=["app.py", "requirements.txt", "README.md"],
        description="3 files — small web app",
    ),
    BenchmarkTier(
        id=3,
        name="rest-api",
        prompt=(
            "Create a FastAPI REST API project with these files: "
            "main.py (FastAPI app with CRUD endpoints for items), "
            "models.py (Pydantic models for Item), "
            "database.py (in-memory database), "
            "requirements.txt (fastapi, uvicorn), "
            "README.md (API docs with endpoints), "
            "tests/test_api.py (pytest tests for each endpoint)"
        ),
        expected_files=6,
        required_patterns=["main.py", "models.py", "requirements.txt"],
        description="6 files — REST API with tests",
    ),
    BenchmarkTier(
        id=4,
        name="cli-tool",
        prompt=(
            "Create a Python CLI tool project with these files: "
            "cli.py (click-based CLI with add, list, remove commands), "
            "storage.py (JSON file storage), "
            "models.py (dataclass models), "
            "utils.py (helper functions), "
            "requirements.txt (click dependency), "
            "setup.py (package setup), "
            "README.md (usage docs), "
            "tests/test_cli.py (CLI tests), "
            "tests/test_storage.py (storage tests), "
            ".gitignore (Python gitignore)"
        ),
        expected_files=10,
        required_patterns=["cli.py", "storage.py", "requirements.txt"],
        description="10 files — full CLI package",
    ),
    BenchmarkTier(
        id=5,
        name="full-project",
        prompt=(
            "Create a complete Python microservice project with ALL of these files: "
            "app/__init__.py, app/main.py (FastAPI app), app/config.py (settings), "
            "app/models.py (SQLAlchemy models), app/schemas.py (Pydantic schemas), "
            "app/database.py (DB session), app/routers/__init__.py, "
            "app/routers/items.py (CRUD router), app/routers/health.py (healthcheck), "
            "tests/__init__.py, tests/test_items.py, tests/test_health.py, "
            "tests/conftest.py (fixtures), "
            "Dockerfile, docker-compose.yml, requirements.txt, "
            "README.md, .gitignore, .env.example, Makefile"
        ),
        expected_files=20,
        required_patterns=["app/main.py", "requirements.txt", "Dockerfile"],
        description="20 files — microservice scaffold",
    ),
]


# ── Benchmark result ────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    """Metrics from one benchmark run."""
    tier_id: int
    tier_name: str
    model: str
    time_seconds: float = 0.0
    files_expected: int = 0
    files_generated: int = 0
    extraction_rate: float = 0.0
    total_bytes: int = 0
    avg_file_bytes: float = 0.0
    content_valid: bool = False
    files_list: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.files_generated > 0 and self.error is None

    @property
    def grade(self) -> str:
        if self.error:
            return "FAIL"
        rate = self.extraction_rate
        if rate >= 0.8:
            return "A"
        if rate >= 0.6:
            return "B"
        if rate >= 0.4:
            return "C"
        if rate > 0:
            return "D"
        return "FAIL"


# ── Runner ──────────────────────────────────────────────────────────

class BenchmarkRunner:
    """Executes benchmark tiers via the gitpilot generate CLI."""

    def __init__(self, model: Optional[str] = None, timeout: int = 300):
        self.model = model
        self.timeout = timeout
        self.project_root = Path(__file__).resolve().parent.parent

    def run_tier(self, tier: BenchmarkTier) -> BenchmarkResult:
        """Run a single tier and return metrics."""
        result = BenchmarkResult(
            tier_id=tier.id,
            tier_name=tier.name,
            model=self.model or "default",
            files_expected=tier.expected_files,
        )

        work_dir = tempfile.mkdtemp(prefix=f"gp-bench-t{tier.id}-")

        try:
            # Build command
            cmd = [
                sys.executable, "-m", "gitpilot", "generate",
                "-m", tier.prompt,
                "-o", work_dir,
            ]

            env = os.environ.copy()
            env["PYTHONPATH"] = str(self.project_root)

            # Run with timing
            start = time.monotonic()
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.project_root),
                env=env,
            )
            elapsed = time.monotonic() - start
            result.time_seconds = round(elapsed, 2)

            if proc.returncode != 0:
                result.error = (proc.stderr or proc.stdout or "Unknown error").strip()[:500]
                return result

            # Collect generated files
            generated = []
            total_size = 0
            for root, _dirs, files in os.walk(work_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    rel = os.path.relpath(fp, work_dir)
                    size = os.path.getsize(fp)
                    generated.append(rel)
                    total_size += size

            result.files_generated = len(generated)
            result.files_list = sorted(generated)
            result.total_bytes = total_size
            result.avg_file_bytes = round(total_size / max(len(generated), 1), 1)
            result.extraction_rate = round(
                min(len(generated) / max(tier.expected_files, 1), 1.0), 3
            )

            # Content validation: check required patterns
            valid = True
            for pattern in tier.required_patterns:
                # Check if any generated file matches the pattern
                matched = any(
                    pattern in g or g.endswith(pattern.split("/")[-1])
                    for g in generated
                )
                if not matched:
                    valid = False
                    break
            result.content_valid = valid

        except subprocess.TimeoutExpired:
            result.error = f"Timed out after {self.timeout}s"
            result.time_seconds = float(self.timeout)
        except Exception as e:
            result.error = str(e)[:500]
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        return result


# ── Reporter ────────────────────────────────────────────────────────

class BenchmarkReporter:
    """Renders benchmark results as table, JSON, and HTML dashboard."""

    def __init__(self, results: list[BenchmarkResult]):
        self.results = results

    def print_table(self) -> None:
        """Print a rich-formatted table to stdout."""
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.panel import Panel

            console = Console()

            table = Table(
                title="GitPilot Code Generation Benchmark",
                show_lines=True,
                title_style="bold white",
            )
            table.add_column("Tier", style="cyan", width=6, justify="center")
            table.add_column("Name", style="white", width=14)
            table.add_column("Expected", justify="center", width=8)
            table.add_column("Generated", justify="center", width=9)
            table.add_column("Rate", justify="center", width=6)
            table.add_column("Time", justify="right", width=8)
            table.add_column("Size", justify="right", width=8)
            table.add_column("Valid", justify="center", width=5)
            table.add_column("Grade", justify="center", width=5)

            for r in self.results:
                rate_color = "green" if r.extraction_rate >= 0.8 else "yellow" if r.extraction_rate > 0 else "red"
                grade_color = {"A": "green", "B": "green", "C": "yellow", "D": "red", "FAIL": "red"}.get(r.grade, "white")

                table.add_row(
                    str(r.tier_id),
                    r.tier_name,
                    str(r.files_expected),
                    str(r.files_generated),
                    f"[{rate_color}]{r.extraction_rate:.0%}[/{rate_color}]",
                    f"{r.time_seconds:.1f}s",
                    self._fmt_bytes(r.total_bytes),
                    "[green]Yes[/green]" if r.content_valid else "[red]No[/red]",
                    f"[{grade_color}]{r.grade}[/{grade_color}]",
                )

            console.print()
            console.print(table)

            # Summary
            passed = sum(1 for r in self.results if r.passed)
            total = len(self.results)
            avg_rate = sum(r.extraction_rate for r in self.results) / max(total, 1)
            avg_time = sum(r.time_seconds for r in self.results) / max(total, 1)
            model = self.results[0].model if self.results else "unknown"

            console.print()
            console.print(Panel.fit(
                f"Model: [bold]{model}[/bold]\n"
                f"Passed: [{'green' if passed == total else 'yellow'}]{passed}/{total}[/{'green' if passed == total else 'yellow'}]\n"
                f"Avg extraction rate: {avg_rate:.0%}\n"
                f"Avg generation time: {avg_time:.1f}s",
                title="Summary",
                border_style="cyan",
            ))
            console.print()

        except ImportError:
            # Fallback: plain text
            self._print_plain()

    def _print_plain(self) -> None:
        """Plain-text fallback when Rich is not available."""
        header = f"{'Tier':>4} {'Name':<14} {'Exp':>4} {'Gen':>4} {'Rate':>6} {'Time':>7} {'Grade':>5}"
        print("\n" + header)
        print("-" * len(header))
        for r in self.results:
            print(
                f"{r.tier_id:>4} {r.tier_name:<14} {r.files_expected:>4} "
                f"{r.files_generated:>4} {r.extraction_rate:>5.0%} "
                f"{r.time_seconds:>6.1f}s {r.grade:>5}"
            )
        print()

    def save_json(self, path: str) -> None:
        """Save results as JSON."""
        data = {
            "benchmark": "gitpilot-codegen",
            "version": self._get_version(),
            "model": self.results[0].model if self.results else "unknown",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "results": [asdict(r) for r in self.results],
            "summary": {
                "total_tiers": len(self.results),
                "passed": sum(1 for r in self.results if r.passed),
                "avg_extraction_rate": round(
                    sum(r.extraction_rate for r in self.results) / max(len(self.results), 1), 3
                ),
                "avg_time_seconds": round(
                    sum(r.time_seconds for r in self.results) / max(len(self.results), 1), 2
                ),
                "total_files_generated": sum(r.files_generated for r in self.results),
                "total_bytes": sum(r.total_bytes for r in self.results),
            },
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Results saved to {path}")

    def save_dashboard(self, path: str) -> None:
        """Generate a self-contained HTML dashboard with charts."""
        model = self.results[0].model if self.results else "unknown"
        results_json = json.dumps([asdict(r) for r in self.results])

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitPilot Benchmark — {model}</title>
<style>
  :root {{ --bg: #0B0B0D; --surface: #1C1C1F; --border: #27272A; --fg: #EDEDED; --muted: #A1A1AA; --accent: #D95C3D; --green: #4ade80; --yellow: #facc15; --red: #f87171; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--fg); padding: 32px; }}
  h1 {{ font-size: 28px; font-weight: 800; margin-bottom: 8px; }}
  .subtitle {{ color: var(--muted); font-size: 14px; margin-bottom: 32px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 32px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
  .card h3 {{ font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }}
  .card .value {{ font-size: 32px; font-weight: 800; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }}
  th {{ background: rgba(255,255,255,0.03); text-align: left; padding: 12px 16px; font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }}
  td {{ padding: 12px 16px; border-top: 1px solid var(--border); font-size: 14px; }}
  .grade-A {{ color: var(--green); font-weight: 700; }} .grade-B {{ color: var(--green); }} .grade-C {{ color: var(--yellow); }} .grade-D {{ color: var(--red); }} .grade-FAIL {{ color: var(--red); font-weight: 700; }}
  .bar-container {{ display: flex; align-items: center; gap: 8px; }}
  .bar {{ height: 8px; border-radius: 4px; background: var(--accent); min-width: 2px; }}
  .bar-label {{ font-size: 12px; color: var(--muted); white-space: nowrap; }}
  canvas {{ max-width: 100%; margin: 24px 0; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }}
  .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 24px; }}
  @media (max-width: 768px) {{ .charts {{ grid-template-columns: 1fr; }} }}
  footer {{ margin-top: 48px; color: var(--muted); font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<h1>GitPilot Code Generation Benchmark</h1>
<p class="subtitle">Model: <strong>{model}</strong> · {time.strftime("%Y-%m-%d %H:%M UTC")}</p>

<div class="grid" id="summary-cards"></div>

<table id="results-table">
  <thead>
    <tr><th>Tier</th><th>Name</th><th>Expected</th><th>Generated</th><th>Rate</th><th>Time</th><th>Size</th><th>Valid</th><th>Grade</th></tr>
  </thead>
  <tbody id="results-body"></tbody>
</table>

<div class="charts">
  <div><canvas id="chart-rate" width="500" height="300"></canvas></div>
  <div><canvas id="chart-time" width="500" height="300"></canvas></div>
</div>

<footer>Generated by GitPilot v0.2.6 · <a href="https://github.com/ruslanmv/gitpilot" style="color: var(--accent);">github.com/ruslanmv/gitpilot</a></footer>

<script>
const results = {results_json};

// Summary cards
const cards = document.getElementById('summary-cards');
const passed = results.filter(r => r.files_generated > 0 && !r.error).length;
const avgRate = results.reduce((s, r) => s + r.extraction_rate, 0) / results.length;
const avgTime = results.reduce((s, r) => s + r.time_seconds, 0) / results.length;
const totalFiles = results.reduce((s, r) => s + r.files_generated, 0);

[
  ['Tiers passed', passed + '/' + results.length, passed === results.length ? 'var(--green)' : 'var(--yellow)'],
  ['Avg extraction rate', (avgRate * 100).toFixed(0) + '%', avgRate >= 0.8 ? 'var(--green)' : 'var(--yellow)'],
  ['Avg generation time', avgTime.toFixed(1) + 's', 'var(--fg)'],
  ['Total files generated', totalFiles, 'var(--accent)'],
].forEach(([title, value, color]) => {{
  cards.innerHTML += '<div class="card"><h3>' + title + '</h3><div class="value" style="color:' + color + '">' + value + '</div></div>';
}});

// Table
const tbody = document.getElementById('results-body');
results.forEach(r => {{
  const rate = (r.extraction_rate * 100).toFixed(0);
  const barWidth = Math.max(rate * 2, 4);
  tbody.innerHTML += '<tr>' +
    '<td>' + r.tier_id + '</td>' +
    '<td><strong>' + r.tier_name + '</strong></td>' +
    '<td>' + r.files_expected + '</td>' +
    '<td>' + r.files_generated + '</td>' +
    '<td><div class="bar-container"><div class="bar" style="width:' + barWidth + 'px"></div><span class="bar-label">' + rate + '%</span></div></td>' +
    '<td>' + r.time_seconds.toFixed(1) + 's</td>' +
    '<td>' + (r.total_bytes / 1024).toFixed(1) + ' KB</td>' +
    '<td>' + (r.content_valid ? '✓' : '✗') + '</td>' +
    '<td class="grade-' + r.grade + '">' + r.grade + '</td>' +
    '</tr>';
}});

// Charts (simple canvas bar charts)
function drawBarChart(canvasId, data, label, color) {{
  const canvas = document.getElementById(canvasId);
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const pad = 50, barW = Math.min(60, (w - pad * 2) / data.length - 10);
  const maxVal = Math.max(...data.map(d => d.value), 1);

  ctx.fillStyle = '#1C1C1F'; ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = '#A1A1AA'; ctx.font = '13px sans-serif';
  ctx.fillText(label, pad, 24);

  data.forEach((d, i) => {{
    const x = pad + i * (barW + 10);
    const barH = (d.value / maxVal) * (h - pad - 60);
    const y = h - pad - barH;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(x, y, barW, barH, 4);
    ctx.fill();
    ctx.fillStyle = '#EDEDED'; ctx.font = '11px sans-serif';
    ctx.fillText(d.label, x, h - pad + 16);
    ctx.fillStyle = '#A1A1AA'; ctx.font = '11px sans-serif';
    ctx.fillText(d.valueLabel, x, y - 6);
  }});
}}

drawBarChart('chart-rate',
  results.map(r => ({{ label: 'T' + r.tier_id, value: r.extraction_rate * 100, valueLabel: (r.extraction_rate * 100).toFixed(0) + '%' }})),
  'Extraction Rate by Tier', '#D95C3D');

drawBarChart('chart-time',
  results.map(r => ({{ label: 'T' + r.tier_id, value: r.time_seconds, valueLabel: r.time_seconds.toFixed(1) + 's' }})),
  'Generation Time by Tier (seconds)', '#ff7a3c');
</script>
</body>
</html>"""

        with open(path, "w") as f:
            f.write(html)
        print(f"Dashboard saved to {path}")

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / (1024 * 1024):.1f} MB"

    @staticmethod
    def _get_version() -> str:
        try:
            from gitpilot.version import __version__
            return __version__
        except Exception:
            return "unknown"


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GitPilot Code Generation Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tests/benchmark.py                    # all tiers, default model\n"
            "  python tests/benchmark.py --quick             # tier 1 only (smoke test)\n"
            "  python tests/benchmark.py --tiers 1 2 3       # specific tiers\n"
            "  python tests/benchmark.py --model llama3      # specific model\n"
            "  python tests/benchmark.py --dashboard out.html # HTML report\n"
        ),
    )
    parser.add_argument("--tiers", nargs="+", type=int, help="Tier IDs to run (default: all)")
    parser.add_argument("--model", type=str, default=None, help="LLM model name")
    parser.add_argument("--output", type=str, default=None, help="Save JSON results to file")
    parser.add_argument("--dashboard", type=str, default=None, help="Generate HTML dashboard")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per tier in seconds")
    parser.add_argument("--quick", action="store_true", help="Run tier 1 only (smoke test)")

    args = parser.parse_args()

    # Select tiers
    if args.quick:
        selected = [t for t in TIERS if t.id == 1]
    elif args.tiers:
        selected = [t for t in TIERS if t.id in args.tiers]
    else:
        selected = TIERS

    if not selected:
        print("No tiers selected. Available: 1-5")
        sys.exit(1)

    # Configure model if specified
    if args.model:
        settings_path = Path.home() / ".gitpilot" / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            settings["ollama_model"] = args.model
            settings_path.write_text(json.dumps(settings, indent=2))
            print(f"Model set to: {args.model}")

    model_name = args.model or "default"

    # Run
    runner = BenchmarkRunner(model=model_name, timeout=args.timeout)
    results: list[BenchmarkResult] = []

    print(f"\nRunning {len(selected)} tier(s) with model '{model_name}'...\n")

    for tier in selected:
        print(f"  [{tier.id}/{len(selected)}] {tier.name} ({tier.expected_files} files)...", end="", flush=True)
        result = runner.run_tier(tier)
        results.append(result)

        if result.error:
            print(f" FAIL ({result.time_seconds:.1f}s)")
        else:
            print(f" {result.files_generated} files, {result.extraction_rate:.0%} rate ({result.time_seconds:.1f}s)")

    # Report
    reporter = BenchmarkReporter(results)
    reporter.print_table()

    if args.output:
        reporter.save_json(args.output)

    if args.dashboard:
        reporter.save_dashboard(args.dashboard)

    # Exit code
    all_passed = all(r.passed for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
