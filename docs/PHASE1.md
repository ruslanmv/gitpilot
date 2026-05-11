# Phase 1 — Foundations

Every batch below is additive, flag-gated where applicable, and removable
in a single revert.  Phase 1 ships no user-visible behaviour change; it
puts the rails in place so Phases 2–4 can land safely.

## Status

| Batch | Done | Notes |
|---|---|---|
| P1-A · Feature-flag service | ✅ | `gitpilot/flags.py`, 16 tests, RLock-safe |
| P1-B · Coverage gate ≥ 80 % | ✅ | gated allowlist in `pyproject.toml`; CI workflow at `.github/workflows/coverage.yml` |
| P1-C · `mypy --strict` foothold | ✅ | 15 modules + `gitpilot/public_api/__init__.py` |
| P1-D · Error envelope | ✅ | `wrap_errors_envelope` decorator; flag: `error_envelope` |
| P1-E · `gitpilot doctor` CLI | ✅ | 9 checks, runs in ≤ 100 ms offline; JSON via `--json` |

Full test count: **1 109 passing** (1 035 prior + 74 new).
Gated coverage: **88.05 %**.
Strict mypy: **15 source files clean**.

## Quick reference

### Feature flags
```bash
# Enable a flag for one process
GITPILOT_FLAGS="error_envelope=1,prompt_cache=0" gitpilot serve

# Or persist for the workspace
echo '{"error_envelope": true}' > .gitpilot/flags.json
```

### Coverage
```bash
make coverage          # gated modules, enforces >= 80 %
make coverage-full     # informational, full tree
make coverage-html     # writes htmlcov/index.html
```

### Types
```bash
make typecheck         # mypy --strict on gated modules
```

### Error envelope
```python
from gitpilot.public_api import wrap_errors_envelope, NotFoundError

@app.get("/widgets/{wid}")
@wrap_errors_envelope
async def get_widget(wid: str) -> dict:
    if not exists(wid):
        raise NotFoundError(f"widget {wid} not found",
                            hint="Check the widget ID with /widgets/list")
    return load_widget(wid)
```
With flag `error_envelope=1` the response on a 404 becomes:
```json
{
  "error": {
    "code":    "resource.not_found",
    "message": "widget abc not found",
    "hint":    "Check the widget ID with /widgets/list",
    "doc_url": "https://docs.gitpilot.dev/errors/resource-not-found"
  },
  "trace_id": "8f3c…"
}
```
With the flag off (legacy default) FastAPI's original 500/HTTPException
behaviour is preserved.

### Doctor
```bash
gitpilot doctor                       # rich table, exit 0/1
gitpilot doctor --offline             # skip every network probe (~100 ms)
gitpilot doctor --json                # machine-readable, for CI
python -m gitpilot.doctor --json      # zero-Typer fallback
```

Checks run today:
1. Python ≥ 3.11
2. node on PATH
3. uv on PATH
4. Workspace files (`AGENTS.md`, `.gitpilot/modes.yaml`)
5. `modes.yaml` parses
6. Sandbox backend reachable (subprocess / matrixlab / off)
7. MCP config parses
8. Model API credential present for the configured provider
9. Frontend bundle packaged

### Public API surface

```python
from gitpilot.public_api import (
    # flags
    is_on, set_override,
    # context + tools
    ToolPolicy, ContextBudgetManager, AgentsLoader, MentionParser,
    # sandbox + trust
    get_sandbox, SandboxPolicy, TrustStore,
    # error envelope
    wrap_errors_envelope, GitPilotError, NotFoundError,
    # doctor
    doctor_run_checks, doctor_render_json,
)
```
Anything outside this list is internal and may change.  Older modules
(legacy `gitpilot.api`, agents, GitHub clients, …) are unchanged and
remain importable as before.
