# Phase 3 — Batch G · First-run wizard

Replaces "read the 6 KB ``.env.template``" with a four-question walkthrough
that produces exactly the files a new user actually needs.

## What ships

| Item | Where |
|---|---|
| Wizard module | `gitpilot/init_wizard.py` |
| CLI integration | `gitpilot init --wizard` (also `--provider`, `--mode`, `--api-key`, `--no-trust`, `--overwrite`) |
| Public API surface | `gitpilot.public_api.{run_wizard, WizardAnswers, WizardResult, …}` |
| Tests | `tests/test_init_wizard.py` (22 specs) |
| Flag | `init_wizard` (default off) |

## Behaviour at a glance

1. Pick a provider — Anthropic Claude, OpenAI, IBM watsonx, or Ollama.
2. Paste the API key (skipped for Ollama; input is hidden, never echoed).
3. Pick a starter mode — `coder`, `planner`, or `reviewer`.
4. Confirm workspace trust (writes a `TrustStore` entry).

Outputs (all atomic):

* `.env`                     — only the keys you actually picked (mode `0o600`).
* `.gitpilot/modes.yaml`     — one starter mode wired with the right tool groups.
* `AGENTS.md`                — via the existing `agents_md.run_init` helper.
* `~/.gitpilot/trusted.json` — trust entry for the workspace.

## Industry-grade guarantees

* **Atomic writes.** Every file is written to a sibling temp file,
  `fsync`-ed, then renamed.  An abort mid-run rolls back every
  successful write so a retry starts from a clean slate.
* **Secret safety.** API keys are never echoed back to stdout, are
  rejected if they contain control characters, and `.env` is set to
  `0o600` on POSIX.
* **Idempotent.** Re-running the wizard with the same inputs produces
  byte-identical files.  Existing files are skipped unless
  `--overwrite` is passed.
* **Non-interactive.** Every prompt has a CLI flag (`--provider`,
  `--mode`, `--api-key`, `--no-trust`), so CI and provisioning scripts
  can drive the same code path the human flow uses.
* **Flag-gated.** Without `init_wizard=1` the wizard refuses to run
  and the user is pointed at the legacy `gitpilot init`.

## Try it

```bash
# Interactive
GITPILOT_FLAGS="init_wizard=1" gitpilot init --wizard

# Non-interactive (CI)
GITPILOT_FLAGS="init_wizard=1" gitpilot init --wizard \
    --provider anthropic \
    --api-key  "$ANTHROPIC_API_KEY" \
    --mode     coder \
    .
```

Expected output:

```
wrote    ./.env
wrote    ./.gitpilot/modes.yaml
wrote    ./AGENTS.md
trusted  workspace recorded in ~/.gitpilot/trusted.json
done in 7 ms
```

## Rollback

* `GITPILOT_FLAGS="init_wizard=0"` — disables the new flow.  The
  legacy `gitpilot init` (just `.gitpilot/GITPILOT.md`) is unchanged
  and remains the default.
* Single revert of this commit removes the wizard module, CLI flags,
  and tests without disturbing any other batch.
