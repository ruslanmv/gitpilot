# GitPilot model benchmarks

Small, repeatable quality checks for the models GitPilot can drive, served
through [OllaBridge](../../ollabridge) or any OpenAI-compatible endpoint. One
command runs them, keeps the history, renders `report.html`, and suggests how
GitPilot should drive each model.

```bash
python -m benchmarks.run --list                                 # what the gateway serves
python -m benchmarks.run --models qwen2.5:1.5b,llama3:8b        # benchmark some models
python -m benchmarks.run --models-file benchmarks/models.txt    # or a list kept in a file
python -m benchmarks.run --new --owned-by <provider> --limit 3  # only models not benchmarked yet
python -m benchmarks.report                                     # re-render from saved results
```

No dependencies beyond the Python 3.11 standard library, so it runs from any
interpreter on the machine. `--suites code,tools,instructions,reasoning,agentic`
adds GitPilot's own agent tasks; they need GitPilot's dependencies and take
minutes per task.

## What it measures

| Suite | Tasks | Graded by | Industry analogue |
|---|---|---|---|
| Coding | 10 functions and classes | hidden unit tests, executed (pass@1) | HumanEval, MBPP |
| Tool calling | 9 cases over one fixed set of 5 tools | tool name and arguments; 3 cases must call nothing | BFCL |
| Instruction following | 8 prompts with checkable constraints | every constraint must hold (strict) | IFEval |
| Reasoning | 8 multi-step problems, one integer answer | exact match | GSM8K |
| GitPilot agent tasks (optional) | the 13 `bench/` fixture tasks | the project left on disk | SWE-bench-style |

The practices that make the numbers comparable:

- **Fresh tasks.** Written for GitPilot rather than copied from public sets, so a
  model cannot pass on memorised answers. Each task ships a reference answer, and
  `tests/test_quality_benchmarks.py` proves that reference passes and a wrong
  answer fails. A grader nobody has seen fail is not a grader.
- **No model judges another.** Every verdict is a unit test, a structural match or
  a string rule, so the same reply always gets the same score.
- **Controlled decoding.** Temperature 0, top_p 1, a fixed seed and the same
  prompts for every model. `--repeats N` measures consistency.
- **Honest uncertainty.** Pass rates come with 95% Wilson intervals; with a few
  dozen tasks, two models whose intervals overlap are not ranked.
- **Serving errors are not quality results.** Timeouts, 5xx, credential and
  "model not found" errors are retried where sensible, excluded from scores and
  counted separately. A model whose first three requests all fail that way is
  stopped and marked unavailable instead of being scored at 0%.
- **Contained execution.** Model-written code passes a static check that refuses
  file, process and network access, then runs in an isolated interpreter
  (`python -I -S`) in a temporary directory with a scrubbed environment and a
  timeout. That is a mitigation for a developer laptop, not a container: for
  untrusted models at scale, run the harness inside a disposable VM or container.
- **Reproducible runs.** Each result file records the GitPilot commit, a hash of
  the whole task set, every decoding parameter, the gateway and the platform.
  Every reply is kept in `<run>.transcripts.jsonl` for audit.

## How it fits GitPilot as models keep arriving

```
new model in OllaBridge
   → python -m benchmarks.run --new          only the newcomers; same task set, comparable history
   → report.html                             leaderboard, per-suite scores, every task, latency
   → model_profiles.suggested.yaml           NATIVE / REACT_TEXT / LITE, from tool-calling and
                                             instruction-following scores
   → review, copy into .gitpilot/models.yaml GitPilot then drives the model in that dialect
   → --suites ...,agentic on the shortlist   the real agent loop on real fixtures
   → python -m bench (the v4 flip gate)      before any default changes
```

Results accumulate in `results/`. The report merges every run that used the
current task set and shows the newest result per model, so adding one model
never means re-running the others. Changing the task set changes its hash;
older runs are then left out of the report, and the report says so. That is
deliberate: scores from different task sets are not comparable.

The suggested profile uses GitPilot's own key for a model (lowercase, provider
prefix dropped) and the file parses with GitPilot's `models.yaml` loader, which
the tests check. It is never written into `.gitpilot/` automatically.

To add a suite, add a module under `suites/` that exposes `TASKS` (each with a
grader and a reference answer) and register it in `suites/__init__.py`.

## Reading the report

`report.html` is self-contained and works offline. Hover any bar, cell or mark
for counts, the interval and the reason a task failed.

It is written to be shareable without revealing the deployment behind it:

- **Model names are generic.** A provider namespace and deployment prefixes such
  as `internal-` or `ext-` are removed, so `provider/internal-acme-coder-30b`
  shows as `acme-coder-30b`. An opaque alias that names no model family (a tier
  name like `vendor-lite`) is replaced by the model's own answer to "which model
  are you?", marked *self-reported*, or failing that by its vendor and tier. A
  name given in the models file (`id = Name`) always wins.
- **Names link to the public model.** `model_links.txt` maps a display name to its
  public page (Hugging Face), so a reader can look the model up. Only https links
  are rendered; the served build may be quantized or fine-tuned, and the report
  says so. Check a new link against the model hub before adding it.
- **The environment is a label.** The report shows `--environment-label`
  (default "Anonymized test environment") and nothing about the host, the
  gateway address, the platform or the products in the serving path. Routes
  appear only as "local runtime" or "API gateway".
- **Real ids stay local.** The result files keep the real model ids and where the
  run happened, for reference. They, the transcripts, the report and the
  suggestions are git-ignored, and so is `models.local.txt` for private model
  lists.

Pick an environment label that describes the hardware truthfully if latency
numbers will be compared: the response times depend on it.

## OllaBridge behaviours the harness works around

Found by reading `ollabridge/src/ollabridge/api/main.py` and its Open WebUI
adapter (OllaBridge 0.1.9). Each is worth fixing in OllaBridge itself:

| Behaviour | Effect on benchmarks | Workaround here | Fix in OllaBridge |
|---|---|---|---|
| Local Ollama gets only `num_predict` (`_ollama_options`); temperature, top_p and seed are dropped | local models sample at Ollama's default, so runs are not repeatable | Ollama-owned models go straight to Ollama's `/v1`, which honours them (`--route gateway` to disable) | forward `temperature`, `top_p` and `seed` in `_ollama_options` |
| The reply is rebuilt without `usage` or `model` | no token counts; no proof of which model answered | tokens estimated at 4 chars/token and flagged | pass through the upstream `usage` and `model` |
| `stream` is not part of the request schema | no time-to-first-token | end-to-end latency only | add streaming to `/v1/chat/completions` |
| The catalog can list models the upstream no longer serves (HTTP 400 "Model not found") | would read as a failed answer | classified as a serving error | prune stale catalog entries on sync |
| Open WebUI chats time out at 120 s (`ProviderTimeout`, HTTP 504) | slow large models look broken | client timeout 180 s; 504 counted as a serving error | make the adapter timeout configurable per source |

One client-side trap, not an OllaBridge bug: on Windows, a Python client that
reaches a WSL-hosted gateway through `localhost` waits about 21 seconds per
connection, because `localhost` resolves to `::1` first and that attempt hangs
for the Windows connect retry before IPv4 is tried (curl races both and hides
it). The harness defaults to `127.0.0.1` for that reason; GitPilot's own default
OllaBridge URL (`http://localhost:11435`) has the same problem on that setup.

## Files

| Path | Role |
|---|---|
| `run.py` | command line: model selection, routing, running, result files |
| `client.py` | OpenAI-compatible client, retries, error classification |
| `suites/` | the task sets and their graders |
| `sandbox.py` | static check and isolated execution for coding tasks |
| `stats.py` | Wilson intervals, medians, percentiles |
| `report.py` | merges runs and renders `report.html` |
| `regrade.py` | re-scores saved replies after a grader fix, without asking any model again |
| `naming.py` | generic, publishable model names |
| `profiles.py` | GitPilot dialect suggestions |
| `models.txt` | the public model list; private ids go in the git-ignored `models.local.txt` |
| `model_links.txt` | display name → public model page, linked from the report |
