"""Render the benchmark results as one self-contained HTML report.

    python -m benchmarks.report                      # every run in benchmarks/results/
    python -m benchmarks.report results/A.json results/B.json -o report.html

Runs are merged per model (the newest run of a model wins) as long as they used
the same task set; runs with a different task-set digest are left out and the
report says so. Only short model names appear in the report.
"""
from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .naming import scrub, scrub_detail
from .profiles import profile_key, suggest_dialect, write_suggestions
from .stats import intervals_overlap, median, percentile, wilson_interval
from .suites import SUITE_TITLES

HERE = Path(__file__).resolve().parent
SUITE_ORDER = ["code", "tools", "instructions", "reasoning", "agentic"]
#: Route labels as the report shows them: what kind of path, not which product.
ROUTE_LABELS = {"ollama-direct": "local runtime", "gateway": "API gateway"}
LINKS_FILE = HERE / "model_links.txt"


def load_links(path: Path = LINKS_FILE) -> Dict[str, str]:
    """``{display name: public model page}``; only https URLs are kept."""
    links: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return links
    for line in lines:
        line = line.split("#", 1)[0].strip()
        name, sep, url = line.partition("=")
        if sep and url.strip().lower().startswith("https://"):
            links[name.strip()] = url.strip()
    return links


def name_cell(name: str, links: Dict[str, str]) -> str:
    """The model's name, linked to its public page when one is known."""
    url = links.get(name) or links.get(name.removesuffix(" (self-reported)"))
    if not url:
        return esc(name)
    return (f"<a href='{esc(url)}' target='_blank' rel='noopener noreferrer' "
            f"title='Public model page'>{esc(name)}</a>")


def public_name(model: Dict[str, Any]) -> str:
    """The name the report may show. An explicit or self-reported name is kept as
    chosen; anything else is scrubbed of deployment prefixes again, so a result
    file written by an older harness cannot leak an internal id."""
    if model.get("name_source") in ("override", "self-reported", "vendor"):
        return str(model.get("name") or "model")
    return scrub(str(model.get("name") or model.get("id") or "model"))

# Sequential blue ramp (light → dark), from the data-viz reference palette.
RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
        "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def pct(value: Optional[float]) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


# ---------------------------------------------------------------------------
# Loading and merging
# ---------------------------------------------------------------------------


def load_runs(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    runs = []
    for path in paths:
        if path.name.endswith(".transcripts.jsonl"):
            continue
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "results" in data and "manifest" in data:
            runs.append(data)
    return sorted(runs, key=lambda run: run.get("run_id", ""))


def merge_runs(runs: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """One view over many runs: newest digest wins, newest run per model wins."""
    if not runs:
        return {"models": [], "results": [], "manifest": {}, "runs": []}, []
    digest = runs[-1]["manifest"].get("task_set_digest")
    kept = [run for run in runs if run["manifest"].get("task_set_digest") == digest]
    dropped = [run["run_id"] for run in runs if run not in kept]

    latest: Dict[str, Dict[str, Any]] = {}
    for run in kept:
        for model in run.get("models", []):
            latest[model["id"]] = {"run": run, "model": model}
    models, results = [], []
    for model_id, entry in latest.items():
        models.append(entry["model"])
        results += [row for row in entry["run"]["results"] if row.get("model") == model_id]
    notes = []
    if dropped:
        notes.append(f"{len(dropped)} older run(s) used a different task set and are not shown: "
                     f"{', '.join(dropped)}.")
    merged = {"models": models, "results": results, "manifest": kept[-1]["manifest"],
              "runs": [run["run_id"] for run in kept]}
    return merged, notes


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def summarise(merged: Dict[str, Any]) -> Dict[str, Any]:
    by_model: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in merged["results"]:
        by_model[row["model"]].append(row)

    suites_present = [s for s in SUITE_ORDER if any(r["suite"] == s for r in merged["results"])]
    summary = {"suites": suites_present, "models": []}
    for model in merged["models"]:
        rows = by_model.get(model["id"], [])
        suite_scores = {}
        for suite in suites_present:
            graded = [r for r in rows if r["suite"] == suite and r.get("passed") is not None]
            errors = sum(1 for r in rows if r["suite"] == suite and r.get("infra_error"))
            passed = sum(1 for r in graded if r["passed"])
            n = len(graded)
            suite_scores[suite] = {
                "rate": passed / n if n else None, "passed": passed, "n": n, "errors": errors,
                "ci": wilson_interval(passed, n) if n else None,
            }
        rates = [s["rate"] for s in suite_scores.values() if s["rate"] is not None]
        chat_ok = [r for r in rows if r["suite"] != "agentic" and r.get("passed") is not None]
        latencies = [r["latency_s"] for r in chat_ok if r.get("latency_s")]
        speeds = [r["output_tokens"] / r["latency_s"] for r in chat_ok
                  if r.get("output_tokens") and r.get("latency_s")]
        pooled_n = len([r for r in rows if r.get("passed") is not None])
        pooled_pass = sum(1 for r in rows if r.get("passed"))
        consistency = _consistency(rows)
        summary["models"].append({
            "name": public_name(model), "name_source": model.get("name_source", "derived"),
            "route": ROUTE_LABELS.get(model.get("route", ""), "API gateway"),
            "status": model.get("status", "ok"),
            "profile_key": profile_key(model["id"]),
            "suggestion": suggest_dialect(suite_scores) if model.get("status", "ok") == "ok" else None,
            "suites": suite_scores,
            "overall": sum(rates) / len(rates) if rates else None,
            "overall_ci": wilson_interval(pooled_pass, pooled_n) if pooled_n else None,
            "graded": pooled_n,
            "errors": sum(1 for r in rows if r.get("infra_error")),
            "latency_median": median(latencies), "latency_p90": percentile(latencies, 90),
            "tokens_per_s": median(speeds),
            "tokens_estimated": any(r.get("tokens_estimated") for r in chat_ok),
            "consistency": consistency,
            "rows": rows,
        })
    summary["models"].sort(key=lambda m: (m["overall"] is None, -(m["overall"] or 0), m["name"]))
    return summary


def _consistency(rows: List[Dict[str, Any]]) -> Optional[float]:
    """Share of tasks whose repeats all agreed; None with a single repeat."""
    outcomes: Dict[str, set] = defaultdict(set)
    for row in rows:
        if row.get("passed") is not None:
            outcomes[row["task"]].add(bool(row["passed"]))
    repeats = {row.get("repeat", 1) for row in rows}
    if len(repeats) < 2 or not outcomes:
        return None
    return sum(1 for values in outcomes.values() if len(values) == 1) / len(outcomes)


def findings(summary: Dict[str, Any]) -> List[str]:
    models = [m for m in summary["models"] if m["overall"] is not None]
    out: List[str] = []
    unavailable = [m["name"] for m in summary["models"] if m["status"] != "ok" or m["overall"] is None]
    if not models:
        out.append("No model produced a gradable answer; see the errors column.")
    if models:
        best = models[0]
        line = f"<b>{esc(best['name'])}</b> scores highest overall at {pct(best['overall'])}"
        if len(models) > 1:
            runner = models[1]
            if best["overall_ci"] and runner["overall_ci"] and intervals_overlap(best["overall_ci"], runner["overall_ci"]):
                line += (f", but its confidence interval overlaps <b>{esc(runner['name'])}</b> "
                         f"({pct(runner['overall'])}), so this sample cannot separate them")
            else:
                line += f", ahead of <b>{esc(runner['name'])}</b> at {pct(runner['overall'])}"
        out.append(line + ".")
        for suite in summary["suites"]:
            scored = [m for m in models if m["suites"][suite]["rate"] is not None]
            if len(scored) < 2:
                continue
            top = max(scored, key=lambda m: m["suites"][suite]["rate"])
            low = min(scored, key=lambda m: m["suites"][suite]["rate"])
            if top["suites"][suite]["rate"] != low["suites"][suite]["rate"]:
                out.append(f"{esc(SUITE_TITLES[suite])}: <b>{esc(top['name'])}</b> leads with "
                           f"{pct(top['suites'][suite]['rate'])}; <b>{esc(low['name'])}</b> trails at "
                           f"{pct(low['suites'][suite]['rate'])}.")
        no_tools = [m["name"] for m in models if "tools" in m["suites"]
                    and m["suites"]["tools"]["n"] and _tool_calls_made(m) == 0]
        if no_tools:
            subject = "it needs" if len(no_tools) == 1 else "they need"
            out.append(f"{esc(', '.join(no_tools))} never emitted a native tool call, so in GitPilot "
                       f"{subject} the REACT_TEXT or LITE dialect rather than NATIVE.")
        timed = [m for m in models if m["latency_median"] is not None]
        if len(timed) > 1:
            fastest = min(timed, key=lambda m: m["latency_median"])
            out.append(f"Fastest median response: <b>{esc(fastest['name'])}</b> at "
                       f"{fastest['latency_median']:.1f}s per request (end to end).")
    if unavailable:
        out.append(f"Not scored because the serving path failed: {esc(', '.join(unavailable))}. "
                   "These are availability problems, not quality results.")
    errored = [m for m in models if m["errors"]]
    if errored:
        out.append("Some requests failed in the serving path and were excluded from scores: "
                   + esc(", ".join(f"{m['name']} ({m['errors']})" for m in errored)) + ".")
    return out


def _tool_calls_made(model: Dict[str, Any]) -> int:
    """Native tool calls the model emitted across the tool-calling suite."""
    return sum(int(r.get("tool_calls_made") or 0) for r in model["rows"]
               if r["suite"] == "tools" and r.get("passed") is not None)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = """
.viz-root{color-scheme:light;--surface-0:#f4f3f0;--surface-1:#fcfcfb;--border:#e3e1dc;
--text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#77756f;--grid:#e8e6e1;
--series-1:#2a78d6;--good:#0ca30c;--critical:#d03b3b;--warning:#b77800;--neutral:#9a9893;
--h0:#cde2fb;--h1:#b7d3f6;--h2:#9ec5f4;--h3:#86b6ef;--h4:#6da7ec;--h5:#5598e7;--h6:#3987e5;
--h7:#2a78d6;--h8:#256abf;--h9:#1c5cab;--h10:#184f95;--h11:#104281;--h12:#0d366b;--hnone:#f0efec}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
--surface-0:#121211;--surface-1:#1a1a19;--border:#33332f;--text-primary:#f0efec;--text-secondary:#c3c2b7;
--text-muted:#9a9890;--grid:#2c2c29;--series-1:#3987e5;--good:#0ca30c;--critical:#e66767;--warning:#fab219;
--neutral:#6f6e69;--h0:#0d366b;--h1:#104281;--h2:#184f95;--h3:#1c5cab;--h4:#256abf;--h5:#2a78d6;
--h6:#3987e5;--h7:#5598e7;--h8:#6da7ec;--h9:#86b6ef;--h10:#9ec5f4;--h11:#b7d3f6;--h12:#cde2fb;--hnone:#383835}}
.viz-root a{color:var(--series-1);text-decoration:none}.viz-root a:hover{text-decoration:underline}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-0:#121211;--surface-1:#1a1a19;--border:#33332f;
--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#9a9890;--grid:#2c2c29;--series-1:#3987e5;
--good:#0ca30c;--critical:#e66767;--warning:#fab219;--neutral:#6f6e69;--h0:#0d366b;--h1:#104281;
--h2:#184f95;--h3:#1c5cab;--h4:#256abf;--h5:#2a78d6;--h6:#3987e5;--h7:#5598e7;--h8:#6da7ec;--h9:#86b6ef;
--h10:#9ec5f4;--h11:#b7d3f6;--h12:#cde2fb;--hnone:#383835}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0)}
.viz-root{font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--text-primary);
background:var(--surface-0);min-height:100vh;padding:32px 16px 64px}
.page{max-width:1080px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:17px;margin:36px 0 10px}
.sub{color:var(--text-secondary);margin:0 0 20px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin:12px 0}
.findings{margin:0;padding-left:20px}.findings li{margin:6px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile .k{color:var(--text-secondary);font-size:13px}.tile .v{font-size:24px;font-weight:600;margin-top:2px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
th{color:var(--text-secondary);font-weight:600;font-size:13px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.ci{color:var(--text-muted);font-size:12px}
.muted{color:var(--text-muted)}
.heat td.cell{text-align:center;font-variant-numeric:tabular-nums;font-weight:600;border:2px solid var(--surface-1)}
.grid td.st{text-align:center;font-weight:700}
.st.pass{color:var(--good)}.st.fail{color:var(--critical)}.st.err{color:var(--warning)}.st.none{color:var(--neutral)}
.legend{display:flex;gap:18px;flex-wrap:wrap;color:var(--text-secondary);font-size:13px;margin:8px 0 0}
.note{color:var(--text-secondary);font-size:13px;margin:8px 0 0;white-space:normal;max-width:760px}
.tile .v.text{font-size:17px;line-height:1.3;overflow-wrap:anywhere}
svg text{fill:var(--text-primary);font:13px system-ui,-apple-system,"Segoe UI",sans-serif}
svg .axis{stroke:var(--grid)} svg .label{fill:var(--text-secondary)}
.bar{fill:var(--series-1)} .whisker{stroke:var(--text-secondary);stroke-width:1.5}
.bar:hover{opacity:.85}
code{font-size:13px}
details summary{cursor:pointer;color:var(--text-secondary)}
"""


def _heat_var(rate: Optional[float]) -> Tuple[str, str]:
    if rate is None:
        return "var(--hnone)", "var(--text-muted)"
    step = round(rate * (len(RAMP) - 1))
    # Light cells take dark ink and dark cells light ink, in both themes.
    ink = "#0b0b0b" if step <= 6 else "#ffffff"
    return f"var(--h{step})", f"light-dark({ink}, {'#ffffff' if step <= 5 else '#0b0b0b'})"


def _bar_chart(items: List[Tuple[str, float, Optional[Tuple[float, float]], str]],
               *, max_value: float, unit: str, title: str) -> str:
    """Horizontal bars, one series: name, value, optional interval, tooltip."""
    if not items:
        return '<p class="muted">Nothing to chart yet.</p>'
    label_w, width, row_h, pad = 190, 760, 34, 8
    plot_w = width - label_w - 90
    height = row_h * len(items) + 30
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{esc(title)}">']
    for tick in range(0, 5):
        x = label_w + plot_w * tick / 4
        value = max_value * tick / 4
        parts.append(f'<line class="axis" x1="{x:.1f}" y1="4" x2="{x:.1f}" y2="{height - 24}"/>')
        label = f"{value * 100:.0f}%" if unit == "%" else f"{value:.0f}{unit}"
        parts.append(f'<text class="label" x="{x:.1f}" y="{height - 8}" text-anchor="middle">{label}</text>')
    for i, (name, value, interval, tip) in enumerate(items):
        y = 6 + i * row_h
        w = max(4.0, plot_w * min(value, max_value) / max_value) if max_value else 4.0
        parts.append(f'<text x="{label_w - 10}" y="{y + row_h / 2 + 1}" text-anchor="end" '
                     f'dominant-baseline="middle">{esc(name)}</text>')
        parts.append(f'<rect class="bar" x="{label_w}" y="{y + pad}" width="{w:.1f}" height="{row_h - 2 * pad}" '
                     f'rx="4"><title>{esc(tip)}</title></rect>')
        if interval:
            x1 = label_w + plot_w * interval[0] / max_value
            x2 = label_w + plot_w * interval[1] / max_value
            yc = y + row_h / 2
            parts.append(f'<line class="whisker" x1="{x1:.1f}" y1="{yc:.1f}" x2="{x2:.1f}" y2="{yc:.1f}"/>'
                         f'<line class="whisker" x1="{x1:.1f}" y1="{yc - 5:.1f}" x2="{x1:.1f}" y2="{yc + 5:.1f}"/>'
                         f'<line class="whisker" x1="{x2:.1f}" y1="{yc - 5:.1f}" x2="{x2:.1f}" y2="{yc + 5:.1f}"/>')
        shown = f"{value * 100:.0f}%" if unit == "%" else f"{value:.1f}{unit}"
        end = label_w + max(w, plot_w * (interval[1] if interval else 0) / max_value if max_value else 0)
        parts.append(f'<text x="{end + 8:.1f}" y="{y + row_h / 2 + 1}" dominant-baseline="middle">{shown}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_html(merged: Dict[str, Any], notes: List[str], links: Optional[Dict[str, str]] = None) -> str:
    summary = summarise(merged)
    links = load_links() if links is None else links
    manifest = merged.get("manifest", {})
    suites = summary["suites"]
    models = summary["models"]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tasks = sorted({r["task"] for r in merged["results"]},
                   key=lambda t: (SUITE_ORDER.index(t.split("/")[0]) if t.split("/")[0] in SUITE_ORDER else 9, t))

    best = next((m for m in models if m["overall"] is not None), None)
    tiles = [
        ("Models tested", str(len(models))),
        ("Tasks per model", str(len(tasks))),
        ("Top overall", f"{best['name']} · {pct(best['overall'])}" if best else "–"),  # text tile
        ("Graded answers", str(sum(m["graded"] for m in models))),
    ]

    out: List[str] = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>GitPilot Model Benchmark</title>",
        f"<style>{CSS}</style></head><body><div class='viz-root'><main class='page'>",
        "<h1>GitPilot model benchmark</h1>",
        f"<p class='sub'>{esc(manifest.get('environment') or 'Anonymized test environment')} · "
        f"generated {esc(generated)} · task set <code>{esc(manifest.get('task_set_digest', '?'))}</code>"
        f" · harness {esc(manifest.get('harness_version', '?'))} · GitPilot <code>{esc(manifest.get('gitpilot_commit') or '?')}</code></p>",
        "<div class='tiles'>" + "".join(
            f"<div class='card tile'><div class='k'>{esc(k)}</div>"
            f"<div class='v{'' if v.isdigit() else ' text'}'>{esc(v)}</div></div>" for k, v in tiles
        ) + "</div>",
        "<h2>Findings</h2><div class='card'><ul class='findings'>",
        *[f"<li>{line}</li>" for line in findings(summary)],
        *[f"<li class='muted'>{esc(note)}</li>" for note in notes],
        "</ul></div>",
    ]

    # Overall chart
    chart_items = [(m["name"], m["overall"], m["overall_ci"],
                    f"{m['name']}: {pct(m['overall'])} overall (macro average of suites); "
                    f"95% CI of pooled pass rate {pct(m['overall_ci'][0])}–{pct(m['overall_ci'][1])}")
                   for m in models if m["overall"] is not None]
    out += ["<h2>Overall score</h2><div class='card'>",
            _bar_chart(chart_items, max_value=1.0, unit="%", title="Overall score by model"),
            "<p class='note'>Bar: average of the suite scores. Whisker: 95% Wilson interval of the pooled "
            "pass rate, so overlapping whiskers mean the sample cannot rank those models.</p></div>"]

    # Heat table (doubles as the table view)
    out += ["<h2>Scores by suite</h2><div class='card scroll'><table class='heat'><thead><tr><th>Model</th>"]
    out += [f"<th class='num'>{esc(SUITE_TITLES[s])}</th>" for s in suites]
    out += ["<th class='num'>Overall</th><th class='num'>Median latency</th><th class='num'>≈ tokens/s</th>"
            "<th class='num'>Serving errors</th><th>GitPilot dialect</th><th>Route</th></tr></thead><tbody>"]
    for m in models:
        out.append(f"<tr><td>{name_cell(m['name'], links)}</td>")
        for s in suites:
            score = m["suites"][s]
            bg, ink = _heat_var(score["rate"])
            ci = (f"95% CI {pct(score['ci'][0])}–{pct(score['ci'][1])}" if score["ci"] else "not graded")
            tip = f"{m['name']} · {SUITE_TITLES[s]}: {score['passed']}/{score['n']} passed; {ci}; {score['errors']} serving errors"
            out.append(f"<td class='cell num' style='background:{bg};color:{ink}' title='{esc(tip)}'>"
                       f"{pct(score['rate'])}<div class='ci' style='color:inherit;opacity:.8'>{score['passed']}/{score['n']}</div></td>")
        latency = "–" if m["latency_median"] is None else f"{m['latency_median']:.1f}s"
        p90 = "" if m["latency_p90"] is None else f"<div class='ci'>p90 {m['latency_p90']:.1f}s</div>"
        speed = "–" if m["tokens_per_s"] is None else f"{m['tokens_per_s']:.0f}"
        suggestion = m.get("suggestion")
        dialect = (f"<span title='{esc(suggestion['why'])}'>{esc(suggestion['dialect'].upper())}</span>"
                   if suggestion else "<span class='muted'>–</span>")
        out.append(f"<td class='num'><b>{pct(m['overall'])}</b></td><td class='num'>{latency}{p90}</td>"
                   f"<td class='num'>{speed}</td><td class='num'>{m['errors']}</td><td>{dialect}</td>"
                   f"<td class='muted'>{esc(m['route'])}</td></tr>")
    out.append("</tbody></table><p class='note'>Cell shade: darker is a higher pass rate. "
               "Hover a cell for counts and its 95% interval. Latency is end to end, per request. "
               "GitPilot dialect: how GitPilot should drive the model, from its tool-calling and "
               "instruction-following scores (also written to <code>model_profiles.suggested.yaml</code>). "
               "A linked name opens the public model page on Hugging Face; the build that was served may be "
               "quantized or tuned.</p></div>")

    # Latency chart
    timed = [m for m in models if m["latency_median"] is not None]
    if timed:
        slowest = max(m["latency_p90"] or m["latency_median"] for m in timed)
        lat_items = [(m["name"], m["latency_median"], None,
                      f"{m['name']}: median {m['latency_median']:.1f}s, p90 {(m['latency_p90'] or 0):.1f}s per request")
                     for m in sorted(timed, key=lambda m: m["latency_median"])]
        out += ["<h2>Response time</h2><div class='card'>",
                _bar_chart(lat_items, max_value=max(1.0, slowest), unit="s", title="Median latency by model"),
                "<p class='note'>Median seconds per request, end to end, on this machine and route. "
                "Shorter is faster.</p></div>"]

    # Per-task grid
    out += ["<h2>Every task</h2><div class='card scroll'><table class='grid'><thead><tr><th>Task</th>"]
    out += [f"<th class='num'>{name_cell(m['name'], links)}</th>" for m in models]
    out.append("</tr></thead><tbody>")
    for task in tasks:
        suite = task.split("/")[0]
        out.append(f"<tr><td><span class='muted'>{esc(SUITE_TITLES.get(suite, suite))}</span> · {esc(task.split('/', 1)[1])}</td>")
        for m in models:
            rows = [r for r in m["rows"] if r["task"] == task]
            out.append(_task_cell(m["name"], task, rows))
        out.append("</tr>")
    out.append("</tbody></table><p class='legend'><span class='st pass'>✓ passed</span>"
               "<span class='st fail'>✗ failed</span><span class='st err'>! serving error, not scored</span>"
               "<span class='st none'>– not run</span> · hover a mark for the reason</p></div>")

    out += _methodology(manifest, merged, summary)
    out.append("</main></div></body></html>")
    return "\n".join(out)


def _task_cell(model: str, task: str, rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<td class='st none' title='not run'>–</td>"
    graded = [r for r in rows if r.get("passed") is not None]
    details = "; ".join(sorted({scrub_detail(r.get("detail") or "")[:140] for r in rows}))
    if not graded:
        return f"<td class='st err' title='{esc(model)} · {esc(task)}: serving error: {esc(details)}'>!</td>"
    passed = sum(1 for r in graded if r["passed"])
    if passed == len(graded):
        cls, mark = "pass", "✓"
    elif passed == 0:
        cls, mark = "fail", "✗"
    else:
        cls, mark = "fail", f"{passed}/{len(graded)}"
    return f"<td class='st {cls}' title='{esc(model)} · {esc(task)}: {esc(details)}'>{mark}</td>"


def _methodology(manifest: Dict[str, Any], merged: Dict[str, Any], summary: Dict[str, Any]) -> List[str]:
    params = manifest.get("params", {})
    estimated = any(m["tokens_estimated"] for m in summary["models"])
    consistency = [f"{m['name']} {pct(m['consistency'])}" for m in summary["models"] if m["consistency"] is not None]
    lines = [
        "<h2>How this was measured</h2><div class='card'><ul class='findings'>",
        "<li><b>Coding</b>: write a function or class; graded by running hidden unit tests (pass@1). Code runs in an "
        "isolated interpreter after a static check that refuses file, process and network access.</li>",
        "<li><b>Tool calling</b>: native function calls against one fixed set of five tools, graded on the tool "
        "chosen and its arguments, plus cases where the right answer is to call no tool (BFCL-style).</li>",
        "<li><b>Instruction following</b>: prompts with constraints a program can check, scored strictly: every "
        "constraint must hold (IFEval-style).</li>",
        "<li><b>Reasoning</b>: short multi-step problems with one integer answer, exact match.</li>",
        "<li><b>GitPilot agent tasks</b> (when run): GitPilot's own agent loop edits and tests a throwaway project; "
        "the result on disk is checked.</li>",
        "<li>All tasks are new rather than copied from public benchmarks, to limit training-data contamination. "
        "No answer is judged by another model.</li>",
        f"<li>Decoding: temperature {esc(params.get('temperature', 0))}, top_p {esc(params.get('top_p', 1))}, "
        f"seed {esc(params.get('seed', '–'))}, {esc(params.get('repeats', 1))} repeat(s) per task. Models on the "
        "local runtime are called directly, because the API gateway in this setup does not forward temperature "
        "or seed to it.</li>",
        "<li>Timeouts, 5xx, credential and missing-model errors are excluded from scores and counted as serving "
        "errors. A model whose first requests all fail this way is stopped and marked unavailable.</li>",
        "<li>Scores are pass rates over a few dozen tasks. Read the 95% intervals before ranking close models.</li>",
    ]
    if estimated:
        lines.append("<li>Token counts are estimated at four characters per token: the gateway does not return "
                     "usage.</li>")
    if any(m.get("name_source") == "self-reported" for m in summary["models"]):
        lines.append("<li>Names marked <i>self-reported</i> are what a model said when asked which model it is; "
                     "models can be wrong about themselves. Other names are generic forms of the served id.</li>")
    if consistency:
        lines.append(f"<li>Consistency across repeats: {esc(', '.join(consistency))}.</li>")
    lines += [
        "</ul><details><summary>Run manifest</summary><table>",
        f"<tr><th>Runs merged</th><td>{esc(', '.join(merged.get('runs', [])))}</td></tr>",
        f"<tr><th>Environment</th><td>{esc(manifest.get('environment') or 'Anonymized test environment')}</td></tr>",
        f"<tr><th>Suites</th><td>{esc(', '.join(manifest.get('suites', [])))}</td></tr>",
        f"<tr><th>Timeout / retries</th><td>{esc(params.get('timeout_s', '?'))}s / {esc(params.get('retries', '?'))}</td></tr>",
        f"<tr><th>Harness Python</th><td>{esc(manifest.get('python', '?'))}</td></tr>",
        "</table></details></div>",
    ]
    return lines


def render_reports(paths: Iterable[Path], output: Path, *, profiles: Optional[Path] = None) -> Path:
    """Write the HTML report and, beside it, the suggested GitPilot model profiles."""
    merged, notes = merge_runs(load_runs(paths))
    output.write_text(render_html(merged, notes), encoding="utf-8")
    summary = summarise(merged)
    write_suggestions(summary["models"], profiles or output.with_name("model_profiles.suggested.yaml"),
                      digest=str(merged.get("manifest", {}).get("task_set_digest", "?")))
    return output


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.report")
    parser.add_argument("results", nargs="*", type=Path, help="result JSON files (default: benchmarks/results/*.json)")
    parser.add_argument("-o", "--output", type=Path, default=HERE / "report.html")
    args = parser.parse_args(argv)
    paths = args.results or sorted((HERE / "results").glob("*.json"))
    print(render_reports(paths, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
