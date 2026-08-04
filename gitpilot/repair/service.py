"""Repair flow orchestration.

Pipeline (see deliverable spec):

1. validate schema
2. clone repo into a temp workspace (credentialed; stubbed only in demo mode)
3. create local branch ``gitpilot/<task_id>``
4. refuse forbidden paths
5. call ``code-fast`` to inspect context
6. call ``code-coder`` to generate a patch (unified diff) constrained to allowed_paths
7. apply patch locally (dry-run: just compute preview)
8. call ``code-reviewer`` to review
9. send branch/workspace to MatrixLab ``validate-patch`` (sandbox policy applies)
10. build repair-response
11. draft_pr mode -> stub (no real PR in first wave)
12. dry_run -> return patch preview only, NO real PR

The whole flow is OFFLINE-safe in demo mode: clients degrade to stubs.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any

from ..inference.coder_registry import build_coder
from ..inference.ollabridge_client import OllaBridgeClient
from ..inference.openai_compatible_client import demo_mode_enabled
from ..sandbox_providers.base import SandboxProvider, SandboxResult
from ..sandbox_providers.matrixlab_client import MatrixLabClient
from .policy import (
    PolicyViolation,
    check_risk_policy,
    check_sandbox_policy,
    validate_changed_files,
)
from .pr_writer import DraftPRWriter
from .schema import (
    ChangedFile,
    RepairMode,
    RepairRequest,
    RepairResponse,
    RiskLevel,
)
from .workspace import WorkspaceError, create_branch
from .workspace import clone as clone_repository
from .workspace import redact as redact_credentials

# Matches the file path on the `+++ b/<path>` line of a unified diff.
_DIFF_PLUS = re.compile(r"^\+\+\+ [ab]/(?P<path>.+?)\s*$", re.MULTILINE)
_DIFF_MINUS = re.compile(r"^--- [ab]/(?P<path>.+?)\s*$", re.MULTILINE)


@dataclass
class RepairContext:
    """Carries workspace state through the pipeline."""

    workspace: str | None = None
    cloned: bool = False
    branch: str = ""


class RepairService:
    """Orchestrates the GENERIC GitPilot repair flow."""

    def __init__(
        self,
        coder: OllaBridgeClient | None = None,
        sandbox: SandboxProvider | None = None,
        pr_writer: DraftPRWriter | None = None,
        demo_mode: bool | None = None,
    ) -> None:
        self._demo = demo_mode if demo_mode is not None else demo_mode_enabled()
        # An explicitly injected coder always wins (tests, embedders). Otherwise
        # the coder is resolved per run from the request's CoderSpec, so a
        # workspace can pick the built-in model or a coding agent per task.
        self._explicit_coder = coder is not None
        self.coder = coder or OllaBridgeClient(demo_mode=self._demo)
        self.sandbox = sandbox  # may be None; built on demand from request
        self.pr_writer = pr_writer or DraftPRWriter()

    # ------------------------------------------------------------------ #
    # Public entrypoint
    # ------------------------------------------------------------------ #
    def run(self, request: RepairRequest) -> RepairResponse:
        response = RepairResponse(
            task_id=request.task_id,
            mode=request.mode,
            status="ok",
        )
        ctx = RepairContext(branch=f"gitpilot/{request.task_id}")

        # Per-run coder selection: the built-in model, or a coding agent that
        # works directly in the prepared workspace. An injected coder wins.
        if not self._explicit_coder:
            self.coder = build_coder(request.coder, demo_mode=self._demo)

        try:
            # (4) refuse forbidden paths up front (empty allowed_paths => block)
            if not request.allowed_paths:
                response.status = "blocked"
                response.warnings.append(
                    "allowed_paths is empty (fail-closed): no changes permitted"
                )
                return response

            # (2) clone repo — a run with no code to read fails here
            self._prepare_workspace(request, ctx, response)
            if response.status == "error":
                return response

            # (3) branch creation noted (best-effort, local only)
            response.messages.append(f"Local branch: {ctx.branch}")

            # (5) inspect context with code-fast
            issues_text = self._summarize_issues(request)
            inspection = self.coder.fast(
                user=(
                    "Inspect this repository context and the reported issues. "
                    "Summarize what minimal, additive change is needed.\n\n"
                    f"Repo: {request.repo_url}\nIssues:\n{issues_text}"
                ),
                system="You are a fast code-context inspector.",
            )
            response.messages.append("Context inspected via code-fast.")

            # (6) generate a patch with code-coder, constrained to allowed_paths
            patch = self.coder.code(
                user=(
                    "Generate a minimal unified diff (git format) that fixes the "
                    "issues. ONLY modify files matching these allowed globs: "
                    f"{request.allowed_paths}. Do NOT touch .env, secrets, or any "
                    "token/secret files.\n\n"
                    f"Context summary:\n{inspection}\n\nIssues:\n{issues_text}"
                ),
                system="You are an expert software engineer producing safe unified diffs.",
                # Agent coders work inside the prepared checkout and read their
                # diff back from it; the built-in client ignores these extras.
                workspace=ctx.workspace,
                dry_run=(request.mode == RepairMode.dry_run),
                allowed_paths=list(request.allowed_paths),
            )
            patch = _ensure_diff(patch)
            response.patch_preview = patch

            changed = _changed_files_from_diff(patch)
            response.changed_files = [
                ChangedFile(path=p, change_type=t) for p, t in changed
            ]

            # (4 cont.) enforce path policy — FAIL CLOSED
            pol = validate_changed_files([p for p, _ in changed], request)
            if not pol.allowed:
                response.status = "blocked"
                response.warnings.extend(pol.violations)
                return response

            # (7) apply patch locally only when not dry-run and we have a workspace
            if request.mode != RepairMode.dry_run and ctx.cloned and ctx.workspace:
                self._apply_patch(ctx.workspace, patch, response)

            # (8) review with code-reviewer
            review = self.coder.review(
                user=(
                    "Review the following unified diff for correctness, safety, "
                    "and secret exposure. Give a one-paragraph verdict and a risk "
                    f"level (low/medium/high).\n\n{patch}"
                ),
                system="You are a senior code reviewer.",
            )
            response.review = review

            # risk assessment (heuristic, conservative)
            risk = _assess_risk(review, changed)
            response.risk_level = risk

            # (9) sandbox validation
            self._run_sandbox(request, ctx, patch, response)

            # risk gate — high risk needs human approval (fail-closed)
            risk_pol = check_risk_policy(risk.value, request.human_approval)
            if not risk_pol.allowed:
                response.status = "needs_approval"
                response.warnings.extend(risk_pol.violations)
                return response

            # (11) draft_pr -> stub; (12) dry_run -> preview only
            if request.mode == RepairMode.draft_pr:
                pr = self.pr_writer.create_draft_pr(
                    repo_url=request.repo_url,
                    branch=ctx.branch,
                    title=f"GitPilot repair: {request.task_id}",
                    body=review,
                    base=request.branch,
                )
                response.pr_url = pr.url
                response.messages.append(pr.message)
            elif request.mode == RepairMode.dry_run:
                response.messages.append(
                    "Dry-run: patch preview only. No PR opened, no changes pushed."
                )

        except PolicyViolation as exc:
            response.status = "blocked"
            response.warnings.extend(exc.violations)
        except Exception as exc:  # never crash the caller
            response.status = "error"
            response.warnings.append(f"repair flow error: {exc}")
        finally:
            self._cleanup(ctx)

        return response

    # ------------------------------------------------------------------ #
    # Pipeline helpers
    # ------------------------------------------------------------------ #
    def _prepare_workspace(
        self, request: RepairRequest, ctx: RepairContext, response: RepairResponse
    ) -> None:
        if self._demo:
            response.messages.append("Demo mode: workspace clone stubbed (offline).")
            return
        # A real checkout, with credentials when the repository needs them. An
        # agent can only build what it can read, so a failed clone fails the run
        # rather than letting a coder invent a patch for code it never saw.
        try:
            workspace = clone_repository(request.repo_url, branch=request.branch)
            ctx.workspace = workspace.path
            ctx.cloned = True
            create_branch(ctx.workspace, ctx.branch)
            if workspace.fell_back_to_default:
                response.warnings.append(
                    f"base branch {request.branch!r} not found; used the repository default"
                )
            response.messages.append(
                f"Cloned {redact_credentials(request.repo_url)} into a temp workspace."
            )
        except WorkspaceError as exc:
            response.status = "error"
            response.warnings.append(str(exc))

    def _apply_patch(
        self, workspace: str, patch: str, response: RepairResponse
    ) -> None:
        try:
            proc = subprocess.run(
                ["git", "-C", workspace, "apply", "--whitespace=nowarn", "-"],
                input=patch.encode(),
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                response.warnings.append(
                    f"patch did not apply cleanly: {proc.stderr.decode()[:300]}"
                )
            else:
                response.messages.append("Patch applied locally.")
        except Exception as exc:
            response.warnings.append(f"patch apply error: {exc}")

    def _run_sandbox(
        self,
        request: RepairRequest,
        ctx: RepairContext,
        patch: str,
        response: RepairResponse,
    ) -> None:
        sandbox = self.sandbox
        if sandbox is None and request.sandbox.provider == "matrixlab":
            sandbox = MatrixLabClient()

        if sandbox is None:
            if request.sandbox.required:
                response.status = "blocked"
                response.warnings.append(
                    "sandbox.required=true but no sandbox provider configured"
                )
            return

        healthy = False
        try:
            healthy = sandbox.health()
        except Exception:
            healthy = False

        sand_pol = check_sandbox_policy(request, healthy)
        if not sand_pol.allowed:
            response.status = "blocked"
            response.warnings.extend(sand_pol.violations)
            response.sandbox_result = SandboxResult.skipped_result(
                "sandbox required but unavailable"
            ).to_dict()
            return

        if not healthy:
            # Not required -> note skipped (dry-run friendly).
            response.sandbox_result = SandboxResult.skipped_result(
                "sandbox unreachable (not required)"
            ).to_dict()
            response.messages.append("Sandbox skipped: unreachable and not required.")
            return

        try:
            result = sandbox.validate_patch(
                client_id=request.client_id,
                workspace_id=request.workspace_id,
                repo_url=request.repo_url,
                branch=ctx.branch,
                profile=request.sandbox.profile,
                patch=patch,
            )
        except Exception as exc:
            result = SandboxResult(status="error", reason=str(exc))
        response.sandbox_result = result.to_dict()

        if not result.skipped and result.status == "failed":
            response.status = "blocked"
            response.warnings.append("sandbox validation failed")

    def _summarize_issues(self, request: RepairRequest) -> str:
        if not request.issues:
            return "(no specific issues provided)"
        return "\n".join(
            f"- [{i.severity.value}] {i.id}: {i.description} "
            f"(action: {i.recommended_action})"
            for i in request.issues
        )

    def _cleanup(self, ctx: RepairContext) -> None:
        if ctx.workspace and ctx.cloned:
            try:
                import shutil

                shutil.rmtree(ctx.workspace, ignore_errors=True)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Diff utilities
# --------------------------------------------------------------------------- #
def _ensure_diff(text: str) -> str:
    """Strip code fences and ensure the text looks like a unified diff."""
    t = text.strip()
    if t.startswith("```"):
        # remove leading/trailing code fences
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _changed_files_from_diff(diff: str) -> list[tuple[str, str]]:
    """Extract (path, change_type) tuples from a unified diff."""
    out: list[tuple[str, str]] = []
    minus_paths = {m.group("path") for m in _DIFF_MINUS.finditer(diff)}
    seen: set[str] = set()
    for m in _DIFF_PLUS.finditer(diff):
        path = m.group("path").strip()
        if path == "/dev/null":
            continue
        if path in seen:
            continue
        seen.add(path)
        # determine change type
        if "/dev/null" in diff[max(0, m.start() - 80): m.start()]:
            change = "added"
        elif path in minus_paths:
            change = "modified"
        else:
            change = "modified"
        out.append((path, change))
    # deletions: `+++ /dev/null` with a `--- a/<path>`
    for m in _DIFF_MINUS.finditer(diff):
        path = m.group("path").strip()
        seg = diff[m.start(): m.start() + 200]
        if "+++ /dev/null" in seg and path not in seen:
            out.append((path, "deleted"))
            seen.add(path)
    return out


def _assess_risk(review: str, changed: list[tuple[str, str]]) -> RiskLevel:
    rl = review.lower()
    if "risk: high" in rl or "high risk" in rl:
        return RiskLevel.high
    if "risk: medium" in rl or "medium risk" in rl:
        return RiskLevel.medium
    # Many changed files -> medium.
    if len(changed) > 5:
        return RiskLevel.medium
    return RiskLevel.low


def run_repair(
    request: RepairRequest, demo_mode: bool | None = None, **kwargs: Any
) -> RepairResponse:
    """Convenience function: build a service and run the request."""
    return RepairService(demo_mode=demo_mode, **kwargs).run(request)
