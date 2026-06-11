# GitPilot Repair API contract

GitPilot is the **only** component that generates patches. The repair flow is
**generic** — usable by SelfRepair, Agent-Matrix, CI, or a developer — and is
provider-neutral. It calls OllaBridge (or any OpenAI-compatible endpoint) via
`OPENAI_BASE_URL` + `OPENAI_API_KEY` and **never reads `HF_TOKEN`**.

## Repair request (== SelfRepair `repair-plan.json`)

```jsonc
{
  "client_id": "agent-matrix",
  "workspace_id": "ws-123",
  "task_id": "fix-health-test",
  "repo_url": "https://github.com/acme/app",
  "branch": "main",
  "mode": "dry_run",                 // "dry_run" | "draft_pr" | "apply"
  "issues": [
    {
      "id": "missing-health-test",
      "severity": "medium",          // low | medium | high | critical
      "description": "No smoke test for /health",
      "recommended_action": "Add tests/test_health.py"
    }
  ],
  "allowed_paths": ["tests/**"],     // globs; EMPTY => fail-closed (no changes)
  "forbidden_paths": [".env", "secrets/**", "**/*token*", "**/*secret*"],
  "coder":   { "provider": "ollabridge", "model": "code-coder" },
  "sandbox": { "provider": "matrixlab", "profile": "default", "required": false },
  "human_approval": false            // required to proceed on risk_level=high
}
```

Pydantic model: `gitpilot.repair.schema.RepairRequest`.

## Repair response (`repair-response.json`)

```jsonc
{
  "task_id": "fix-health-test",
  "status": "ok",                    // ok | blocked | error | needs_approval
  "mode": "dry_run",
  "patch_preview": "--- /dev/null\n+++ b/tests/test_health.py\n...",
  "changed_files": [{ "path": "tests/test_health.py", "change_type": "added" }],
  "review": "Review: change is small, additive ... Risk: low.",
  "sandbox_result": null,            // or MatrixLab result / {skipped, reason}
  "risk_level": "low",               // low | medium | high
  "pr_url": null,
  "messages": ["Dry-run: patch preview only. No PR opened."],
  "warnings": []
}
```

Pydantic model: `gitpilot.repair.schema.RepairResponse`.

## Pipeline (`gitpilot.repair.service.RepairService.run`)

1. Validate schema.
2. Clone repo into a temp workspace (stubbed in dry-run/demo or if unreachable).
3. Create local branch `gitpilot/<task_id>`.
4. Refuse forbidden paths (fail-closed).
5. `code-fast` inspects context.
6. `code-coder` generates a unified diff constrained to `allowed_paths`.
7. Apply patch locally (dry-run computes preview only).
8. `code-reviewer` reviews the diff.
9. MatrixLab `validate-patch` (skipped if not required and unreachable;
   fail-closed if `required` and unreachable; blocked if validation fails).
10. Build `repair-response.json`.
11. `draft_pr` mode → safe no-op stub (no real PR in the first wave).
12. `dry_run` mode → patch preview only, never a real PR.

## Fail-closed safety (`gitpilot.repair.policy`)

A change is **blocked** when: `allowed_paths` is empty; a changed file is
outside `allowed_paths`; a changed file matches `forbidden_paths`; the patch
touches `.env` or any secret/token material; `sandbox.required=true` and
MatrixLab is unavailable; sandbox validation fails; or `risk_level=high` with
no human approval.

## Environment

`GITPILOT_CLIENT_ID`, `GITPILOT_WORKSPACE_ID`, `OPENAI_BASE_URL`,
`OPENAI_API_KEY`, `GITPILOT_MODEL_FAST` (`code-fast`),
`GITPILOT_MODEL_CODER` (`code-coder`), `GITPILOT_MODEL_REVIEWER`
(`code-reviewer`), `GITPILOT_SANDBOX_PROVIDER` (`matrixlab`), `MATRIXLAB_URL`,
`MATRIXLAB_TOKEN`, `GITPILOT_DRAFT_PR_ENABLED` (`false`),
`GITPILOT_DEMO_MODE` (`true`).

## CLI

```bash
gitpilot repair --repo <url> --plan repair-plan.json --sandbox matrixlab --dry-run
# standalone:
python -m gitpilot.repair.cli --plan repair-plan.json --dry-run --demo
```
