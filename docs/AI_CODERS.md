# Choosing the AI coder for a run

GitPilot's repair pipeline owns the *governed* part of a coding run — the
throwaway workspace, the allowed/forbidden path policy, patch application, the
sandbox check, risk scoring, and the draft PR. **Who writes the patch** is a
separate, per-run choice.

| Provider | What runs | Best for |
|---|---|---|
| `ollabridge` *(default)* | The built-in model, one shot: prompt → unified diff | Fast, cheap, deterministic repairs |
| `claude_code` | Claude Code headless, in the workspace | Deep multi-file reasoning; plan mode |
| `codex` | Codex `exec` headless, in the workspace | Fast non-interactive execution |
| *anything else* | A generic agent you define (see below) | Any other headless coding agent |

Changing the coder changes **only who produces the diff**. Every guardrail is
unchanged, and no coder ever commits, pushes, or opens a PR — it returns a
patch, and the platform decides what happens to it.

## Per run

Add a `coder` to the run request:

```json
POST /api/v1/gitpilot/runs
{
  "task": "Add a health endpoint",
  "repo": "https://github.com/acme/widget",
  "mode": "ask",
  "baseBranch": "main",
  "coder": { "provider": "claude_code", "model": "sonnet" }
}
```

Omit `coder` and the run uses the deployment default. An unknown provider falls
back to the built-in coder rather than failing the run.

## Per deployment

```bash
GITPILOT_CODER_PROVIDER=codex   # default for every run that doesn't ask
```

## What this deployment can actually run

```
GET /api/v1/gitpilot/coders
```

Returns each coder with an honest `available` flag and, when it isn't, the
reason — the agent's CLI is missing, or its credential isn't set. A control
plane builds its picker from this rather than from a hard-coded list, so it
never offers a coder that would fail at run time.

## Repository access

The run works in a throwaway clone, and a clone that fails **fails the run** —
a coder never invents a patch for code it could not read. Private repositories
need a credential:

```bash
GITPILOT_GIT_TOKEN=...            # or GITHUB_TOKEN / GH_TOKEN / GIT_TOKEN
GITPILOT_GIT_USERNAME=x-access-token   # optional; this is the default
```

The token is passed to git through a short-lived askpass helper, never on the
command line and never written into the checkout's `.git/config`, and it is
redacted from anything the run reports. SSH remotes use the host's key instead.
If the run's base branch doesn't exist, the repository's default branch is used
and the run says so.

## Any other agent (generic)

A new agent is configuration, not code:

```bash
GITPILOT_AGENT_CMD="my-agent run {prompt}"     # {prompt} = the task text
GITPILOT_AGENT_READONLY_FLAGS="--dry-run"      # used when mode is dry_run
GITPILOT_AGENT_WRITE_FLAGS="--apply"           # used when the run may edit
GITPILOT_AGENT_CREDENTIAL_ENV="MY_AGENT_TOKEN" # optional
GITPILOT_AGENT_TIMEOUT=900
```

## How an agent run works

1. The pipeline prepares the workspace (credentialed shallow clone + branch).
2. The agent is invoked **with the workspace as its working directory**, and the
   run's `dry_run` is mapped onto that agent's own read-only mode
   (`--permission-mode plan`, `--sandbox read-only`, …) — so "preview only" is
   enforced by the agent as well as by GitPilot.
3. GitPilot reads the agent's edits back out as a unified diff (`git diff`,
   including new files). **It never commits or pushes.**
4. From there the run is identical to any other: forbidden paths refused, patch
   applied only outside dry-run, sandbox validated, risk scored, PR only on an
   approved verdict.

Failure is survivable by design: a missing executable, a timeout, or a
non-zero exit returns whatever edits exist (or an empty patch) instead of
aborting — and with no workspace the coder returns no patch rather than
guessing one.

## From DayPilot

DayPilot needs no changes: it already posts native coding runs to this facade
and governs the result through its own approval layer. Set the deployment
default, or have DayPilot include `coder` in the run to pick per task.
