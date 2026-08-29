# Sandbox Runtime

GitPilot executes code in a configurable sandbox so the chat **▶ Run** button,
the agent's autonomous build/test loop, and the HTTP API all share one
runtime contract. Three backends ship in the box.

## Backends

| Backend          | Isolation                    | Use it when                                            |
| ---------------- | ---------------------------- | ------------------------------------------------------ |
| `subprocess`     | host process, cwd jail       | **Default.** Tries simple snippets locally.            |
| `matrixlab`      | Docker container per snippet | Enterprise — untrusted code, multi-tenant, audit-able. |
| `off`            | none (pass-through)          | Local dev only. No jail; equivalent to host shell.     |

`subprocess` is the safe default so a fresh install runs hello-world without
any setup. Operators pick `matrixlab` from **Settings → Sandbox runtime** for
isolated, ephemeral, resource-limited execution.

## Precedence

Resolution order at every sandbox call:

```
explicit  >  GITPILOT_SANDBOX env  >  ~/.gitpilot/settings.json  >  "subprocess"
```

When an env var shadows the persisted choice, `GET /api/sandbox/status`
returns `env_override: "GITPILOT_SANDBOX"` and the Settings panel renders an
**env override** badge so the user understands why their UI selection isn't
taking effect.

## How the three surfaces share one path

```
┌─────────────────────┐      ┌──────────────────────┐
│ Chat ▶ Run button   │      │ Agent run_in_sandbox │
│ Chat run_command    │      │ Agent run_command    │
└──────────┬──────────┘      └──────────┬───────────┘
           │                            │
           └──────────┬─────────────────┘
                      ▼
           ┌──────────────────────┐
           │ POST /api/sandbox/run│      same backend, same policy,
           │   {language, code}   │      same error envelope
           └──────────┬───────────┘
                      │
        ┌─────────────┼──────────────┐
        ▼             ▼              ▼
   SubprocessSandbox  NullSandbox  MatrixLabSandbox ──► POST /code/run
       (default)       (off)                              on the Runner
```

- The **frontend ▶ Run button** in chat (`frontend/components/RunnableCodeBlock.jsx`)
  POSTs the fenced snippet to `/api/sandbox/run`.
- The **agent's `run_in_sandbox` tool** is the same HTTP call wrapped as a
  CrewAI tool, so a single binding governs both human and autonomous runs.
- The **agent's `run_command` tool** routes through the same endpoint:
  `bash` → `language=bash, code=<command>` against the configured backend.

On the Runner, snippets go to `POST /code/run` and workspace commands
(`MatrixLabSandbox.run`) to `POST /run`, which takes the workspace as a
zip. Neither is `POST /repo/run` — that endpoint clones a git repository
and requires a `repo_url`.

## Configuration

### From the UI

`Settings → Sandbox runtime` shows a radio (Local / MatrixLab / Pass-through)
plus a MatrixLab card with URL, bearer token (write-only — saved tokens
display as bullets), default image, network egress toggle, timeout, and a
**Test connection** button.

### From the environment

| Var                                       | Effect                                                            |
| ----------------------------------------- | ----------------------------------------------------------------- |
| `GITPILOT_SANDBOX`                        | Pins backend (`subprocess` \| `matrixlab` \| `off`)               |
| `GITPILOT_MATRIXLAB_URL`                  | MatrixLab Runner base URL (default `http://localhost:8765`)       |
| `GITPILOT_MATRIXLAB_TOKEN`                | Bearer token sent on every request                                |
| `GITPILOT_MATRIXLAB_IMAGE`                | Default image override (e.g. `matrix-lab-sandbox-python:latest`)  |
| `GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE`     | Set to `1` to enable the Install / Start / Stop buttons           |

### From `settings.json`

```json
{
  "sandbox": {
    "backend": "matrixlab",
    "matrixlab_url": "http://localhost:8765",
    "matrixlab_token": "",
    "matrixlab_image": "",
    "allow_network": false,
    "timeout_sec": 120
  }
}
```

Secrets never round-trip to the browser: `GET /api/settings` returns
`has_token: true|false` instead of the token itself.

## HTTP API

### `GET /api/sandbox/status`

Returns the live backend, reachability of the configured MatrixLab Runner,
and `env_override` if an env var is shadowing the persisted choice.

### `PUT /api/sandbox/config`

Updates any subset of the persisted `SandboxSettings`. Unknown backend
values return `400` (only `subprocess`, `matrixlab`, `off` accepted).

### `POST /api/sandbox/run`

```jsonc
// request
{ "language": "python", "code": "print(2 + 2)", "timeout_sec": 60 }

// response
{
  "backend": "matrixlab",
  "language": "python",
  "command": "python <snippet>",
  "exit_code": 0,
  "stdout": "4\n",
  "stderr": "",
  "duration_ms": 1868,
  "truncated": false,
  "timed_out": false,
  "sandbox_id": "63baa623-…"   // assigned by MatrixLab when backend=matrixlab
}
```

Supported languages: `python` (`py`), `javascript` (`js`/`node`), `bash`
(`sh`/`shell`). Unknown languages return `400`. Snippets run in an
ephemeral tempdir (not the workspace) so file-system side effects don't
pollute the repo.

### MatrixLab lifecycle

`GET /api/sandbox/matrixlab/lifecycle` reports `installed` (Docker image
present), `running` (URL reachable), `docker_available`, and
`lifecycle_enabled` (the env-flag gate). Always safe to call — pure
inspection.

The mutating endpoints below are gated behind
`GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE=1`. Without the flag they return
`403`, never silently execute Docker on behalf of a browser POST.

| Method | Path                              | Action                                  |
| ------ | --------------------------------- | --------------------------------------- |
| `POST` | `/api/sandbox/matrixlab/install`  | `docker pull` runner + sandbox images   |
| `POST` | `/api/sandbox/matrixlab/start`    | `docker run -d` (idempotent by name)    |
| `POST` | `/api/sandbox/matrixlab/stop`     | `docker stop gitpilot-matrixlab`        |

Each response carries the full `steps` transcript (`cmd`, `exit_code`,
`stdout`, `stderr`, `duration_ms` per step) so failures are debuggable
without SSH'ing to the host.

## Error retrieval

The point of running through a sandbox is that failures come back as
structured signals, not opaque silence. Every backend returns:

- `exit_code` — non-zero on failure; `-1` for "could not launch"
- `stderr` — full traceback / compiler diagnostic, verbatim
- `timed_out` — `true` when the runner killed the process
- `truncated` — `true` when output was clipped at the policy cap

This is what makes autonomous loops productive: the agent can read a
SyntaxError, plan the fix, and re-run. Same pattern Claude Code, Codex,
and Cursor use.

Example trace through `run_in_sandbox(language="python", code="raise ValueError('boom')")`:

```
Sandbox: MatrixLab
Command: python <snippet>
Exit code: 1
Duration: 440 ms
--- stderr ---
Traceback (most recent call last):
  File "/workspace/main.py", line 1, in <module>
    raise ValueError("boom")
ValueError: boom
sandbox_id: db3e427d-…
```

## Resource policy

`SandboxPolicy` enforces:

- **Wall-clock timeout** — caller-supplied or `timeout_sec` default (120s,
  clamped to 600s)
- **Output cap** — 512 KB per stream; sets `truncated: true` when hit
- **Network** — `allow_network: false` strips proxy env vars on
  `subprocess`; rejected at egress on `matrixlab`
- **Secret stripping** — `GITHUB_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `WATSONX_API_KEY`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` are never
  forwarded into the sandbox process
- **Destructive patterns** — `rm -rf /`, `mkfs`, `dd if=/dev/zero`,
  `:(){ :|:& };:`, `shutdown -h|-r` blocked before launch

## Quick start

1. `make install && make run` — defaults to `subprocess`, hello-world works.
2. Switch to MatrixLab once you need real isolation:
   ```bash
   curl -X PUT http://localhost:8000/api/sandbox/config \
     -H 'content-type: application/json' \
     -d '{"backend": "matrixlab", "matrixlab_url": "http://localhost:8765"}'
   ```
   …or click the radio in **Settings → Sandbox runtime**.
3. Run a snippet — plan, approve, then run (the endpoint refuses a run
   nobody approved):
   ```bash
   PLAN=$(curl -sX POST http://localhost:8000/api/sandbox/plan \
     -H 'content-type: application/json' \
     -d '{"language": "python", "code": "print(2 + 2)", "source": "code_block"}' \
     | jq -r .plan.plan_id)
   TOKEN=$(curl -sX POST http://localhost:8000/api/sandbox/approve \
     -H 'content-type: application/json' \
     -d "{\"plan_id\": \"$PLAN\"}" | jq -r .approval_token)
   curl -X POST http://localhost:8000/api/sandbox/run \
     -H 'content-type: application/json' \
     -d "{\"language\": \"python\", \"code\": \"print(2 + 2)\", \
          \"approval_token\": \"$TOKEN\"}"
   ```

## Troubleshooting

`make sandbox-debug` walks the whole path on your machine — settings,
the local backend, stdin handling, the approval gate, "run \<file\>"
routing, and the Runner — and prints which stage breaks plus the command
that fixes it. `make sandbox-debug MATRIXLAB=1` probes the Runner even
when the local backend is selected. It is read-only and exits non-zero on
failure, so CI can gate on it.
`tests/test_sandbox_run_file_e2e.py` is its offline counterpart.

Symptoms worth recognising:

| Symptom                                                     | Cause                                                                                                                  |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| "Run demo.py" gets an *explanation* of how to run it         | The goal never classified as `execute`, so the LLM planner answered it. `make sandbox-debug` stage 5 checks this.        |
| The execution card says the run was not approved             | The apply path called `/api/sandbox/run` without minting an approval token. Stage 4.                                     |
| A run produces no output and ends at the timeout             | The script is waiting on stdin. Sandboxed processes get `DEVNULL`, so `input()` raises `EOFError` — anything else is a bug. |
| Output appears empty even though the script printed          | Output produced before a timeout must survive the kill. Stage 3.                                                         |
| "MatrixLab is installed, but GitPilot cannot connect"        | Wrong port (Runner is on host **8765**, GitPilot on **8000**), the Runner's Docker is down, or a stale `matrixlab_url` — `make fix-matrixlab-url`. |
| MatrixLab runs snippets but not files                        | Workspace commands go to `POST /run` (native contract, workspace shipped as a zip), **not** `POST /repo/run`, which clones a git repo and requires `repo_url`. |

## See also

- `gitpilot/sandbox.py` — backend abstraction (`NullSandbox`,
  `SubprocessSandbox`, `MatrixLabSandbox`) + `SandboxPolicy`
- `gitpilot/sandbox_api.py` — HTTP surface, lifecycle endpoints
- `gitpilot/local_tools.py` — agent `run_command` + `run_in_sandbox` tools
- `frontend/components/SettingsModal.jsx` — Sandbox runtime panel
- `frontend/components/RunnableCodeBlock.jsx` — chat ▶ Run button
- `tests/test_sandbox.py`, `tests/test_sandbox_api.py` — unit tests
- `tests/test_sandbox_run_file_e2e.py` — end-to-end regression suite for
  "run this file", offline (no Docker, no Ollama, no GitHub)
- `scripts/sandbox_debug.py` — live diagnostics (`make sandbox-debug`)
