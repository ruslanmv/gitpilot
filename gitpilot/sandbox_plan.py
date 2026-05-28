"""Deterministic Execution-Plan builder for the sandbox.

The contract is approval-first: nothing runs in the sandbox until the
user approves an :class:`ExecutionPlan` produced by this module. The
plan is built *without* the LLM — purely from the router's classified
intent, the resolved file path (or inline snippet), and a quick
deterministic safety scan. That means:

* No "empty plan" failures — the plan is computed in pure Python.
* No model round-trip — the user sees the approval card in milliseconds.
* Auditable — every check / warning is a single regex or stat call.

The plan returned here is *not* the same shape as :class:`PlanResult`
(which is a repo Action Plan: READ/CREATE/MODIFY/DELETE). It is a
distinct surface so the UI can render a different card with a
different colour, different verbs, and different next actions.

This module is intentionally pure: it never reads settings, never
issues network calls, never touches disk except through the
``file_reader`` callback the caller provides. That keeps it trivial
to unit-test and safe to call from any context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Literal, Optional, Sequence

# Re-use the executor's own runnable-extension set so the two stay in
# lockstep — if a new extension is added there, it must be added here
# too.  Failing closed (unknown extension → not runnable) is the
# safe default.
from .agentic import _RUNNABLE_EXTENSIONS, _is_runnable_path

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Severity = Literal["low", "medium", "high"]
PlanSource = Literal["chat", "code_block", "file_run", "canvas", "rerun"]
SandboxBackend = Literal["subprocess", "matrixlab"]


@dataclass(frozen=True)
class SafetyCheck:
    """A passing precondition the user can see in the approval card."""
    label: str
    ok: bool = True


@dataclass(frozen=True)
class SafetyWarning:
    """A non-blocking advisory shown above the Run button.

    Warnings never reject a plan — that's the user's call. They make
    the approval prompt honest about what the code does so consent
    is informed.
    """
    severity: Severity
    label: str
    detail: str = ""


@dataclass
class ExecutionPlan:
    """A deterministic, user-approvable description of one sandbox run.

    Stable across both file-run and inline-code paths so the approval
    card is one component. ``inline_code`` is mutually exclusive with
    ``file``: a file run reads the content at execute time (so edits
    between plan and approval are picked up), while an inline run
    captures the snippet at plan time.
    """

    plan_id: str
    goal: str
    source: PlanSource
    language: str                        # "python" | "node" | "bash"
    command: List[str]                   # ["python", "demo.py"]
    sandbox: SandboxBackend
    timeout_sec: int
    network: bool
    workdir: str
    capture_artifacts: bool = True
    file: Optional[str] = None           # repo-relative path for file-run
    inline_code: Optional[str] = None    # captured snippet for code_block/canvas
    safety_checks: List[SafetyCheck] = field(default_factory=list)
    safety_warnings: List[SafetyWarning] = field(default_factory=list)
    requires_approval: bool = True
    parent_run_id: Optional[str] = None  # set on Rerun

    def to_dict(self) -> dict:
        """JSON-safe representation for the HTTP layer."""
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "source": self.source,
            "language": self.language,
            "command": list(self.command),
            "sandbox": self.sandbox,
            "timeout_sec": self.timeout_sec,
            "network": self.network,
            "workdir": self.workdir,
            "capture_artifacts": self.capture_artifacts,
            "file": self.file,
            "inline_code": self.inline_code,
            "safety": {
                "checks": [
                    {"label": c.label, "ok": c.ok} for c in self.safety_checks
                ],
                "warnings": [
                    {"severity": w.severity, "label": w.label, "detail": w.detail}
                    for w in self.safety_warnings
                ],
            },
            "requires_approval": self.requires_approval,
            "parent_run_id": self.parent_run_id,
        }


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

# Mapping from file extension → (canonical language, interpreter argv[0]).
# Kept in lockstep with sandbox_api's executor so the command shown in
# the approval card is exactly the command that will run.
_LANG_BY_EXT = {
    "py":   ("python",     "python"),
    "js":   ("javascript", "node"),
    "mjs":  ("javascript", "node"),
    "cjs":  ("javascript", "node"),
    "sh":   ("bash",       "bash"),
    "bash": ("bash",       "bash"),
}


def _language_for_path(path: str) -> tuple[str, str]:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _LANG_BY_EXT.get(ext, ("text", ""))


def _language_for_inline(language: str) -> tuple[str, str]:
    """Resolve a free-form language tag (``py``, ``python``, ``js`` …)
    to its canonical name and interpreter."""
    lang = (language or "").strip().lower()
    aliases = {
        "py": "py", "python": "py", "python3": "py",
        "js": "js", "javascript": "js", "node": "js",
        "sh": "sh", "bash": "sh", "shell": "sh",
    }
    canonical_ext = aliases.get(lang)
    if not canonical_ext:
        return ("text", "")
    return _LANG_BY_EXT[canonical_ext]


# ---------------------------------------------------------------------------
# Safety analysis
# ---------------------------------------------------------------------------
#
# Each rule is a regex over the source code. Matches add a warning but
# never block — the user is the final arbiter. Rules are intentionally
# coarse: false positives are cheap (one extra warning chip in the UI),
# false negatives erode trust.

_SAFETY_RULES: tuple[tuple[Severity, str, str, re.Pattern[str]], ...] = (
    (
        "high",
        "Uses os.system / exec / eval",
        "These primitives execute arbitrary shell or Python — review the snippet carefully.",
        re.compile(r"\b(os\.system|subprocess\.(call|run|Popen)|\beval\(|\bexec\()"),
    ),
    (
        "medium",
        "Imports network library",
        "Script may reach the network — disabled by default in the sandbox.",
        re.compile(r"\b(import\s+(socket|urllib|http|requests|httpx|aiohttp)|from\s+(socket|urllib|http|requests|httpx|aiohttp))\b"),
    ),
    (
        "low",
        "Uses plt.show()",
        "Headless sandbox cannot render windows — GitPilot will inject MPLBACKEND=Agg.",
        re.compile(r"plt\.show\(|matplotlib\.pyplot\.show\("),
    ),
    (
        "low",
        "Writes files",
        "Script writes files to the workspace — they appear as artifacts in the result.",
        re.compile(r"\bopen\([^)]*['\"][rwa+xb]*['\"]\s*\)|\.write\(|savefig\(|to_csv\(|to_excel\("),
    ),
    (
        "medium",
        "Reads environment variables",
        "Script reads env vars — only sandbox-scoped variables are present.",
        re.compile(r"os\.environ|os\.getenv\("),
    ),
    (
        "medium",
        "Spawns child processes",
        "Script spawns subprocesses — limited by the sandbox's process budget.",
        re.compile(r"\bsubprocess\.|\bos\.spawn|\bos\.popen"),
    ),
)


def analyze_safety(code: str, language: str) -> List[SafetyWarning]:
    """Run the deterministic warning rules over a snippet.

    Returns warnings sorted by severity descending so the UI can
    surface the worst-case advisory first. Empty input → empty list,
    never an exception.
    """
    if not code:
        return []
    out: List[SafetyWarning] = []
    seen_labels: set[str] = set()
    for severity, label, detail, pattern in _SAFETY_RULES:
        if label in seen_labels:
            continue
        if pattern.search(code):
            out.append(SafetyWarning(severity=severity, label=label, detail=detail))
            seen_labels.add(label)
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda w: order[w.severity])
    return out


# ---------------------------------------------------------------------------
# Plan builders
# ---------------------------------------------------------------------------


def _new_plan_id() -> str:
    """Short ULID-ish id — good enough for a per-session plan."""
    import secrets
    return "plan_" + secrets.token_hex(8)


def build_execution_plan_for_file(
    *,
    file: str,
    repo_files: Sequence[str],
    file_reader: Optional[Callable[[str], Optional[str]]] = None,
    sandbox: SandboxBackend = "matrixlab",
    timeout_sec: int = 120,
    network: bool = False,
    workdir: str = ".",
    source: PlanSource = "chat",
    parent_run_id: Optional[str] = None,
) -> Optional[ExecutionPlan]:
    """Build a deterministic plan to run an existing repo file.

    Returns ``None`` when the file is not in the repo or its extension
    isn't on the runnable allowlist. That's the safe failure mode —
    callers must distinguish "no plan possible" from "plan ready".

    ``file_reader`` is optional. When provided it's used to scan the
    file's contents for safety warnings. When absent the plan still
    builds (no warnings — the UI shows only the checks).
    """
    if file not in set(repo_files):
        return None
    if not _is_runnable_path(file):
        return None

    language, argv0 = _language_for_path(file)
    if not argv0:
        return None

    checks = [
        SafetyCheck(label="File exists"),
        SafetyCheck(label=f"File is a runnable {language} file"),
        SafetyCheck(label="Runs inside the sandbox"),
        SafetyCheck(label=f"Timeout: {timeout_sec}s"),
        SafetyCheck(label="Network: " + ("enabled" if network else "disabled")),
    ]

    warnings: List[SafetyWarning] = []
    if file_reader is not None:
        try:
            content = file_reader(file)
        except Exception:
            content = None
        if content:
            warnings = analyze_safety(content, language)

    return ExecutionPlan(
        plan_id=_new_plan_id(),
        goal=f"Run {file}",
        source=source,
        language=language,
        command=[argv0, file],
        sandbox=sandbox,
        timeout_sec=timeout_sec,
        network=network,
        workdir=workdir,
        file=file,
        inline_code=None,
        safety_checks=checks,
        safety_warnings=warnings,
        parent_run_id=parent_run_id,
    )


def build_execution_plan_for_inline(
    *,
    code: str,
    language: str,
    source: PlanSource = "code_block",
    sandbox: SandboxBackend = "matrixlab",
    timeout_sec: int = 120,
    network: bool = False,
    workdir: str = ".",
    parent_run_id: Optional[str] = None,
    display_filename: Optional[str] = None,
) -> Optional[ExecutionPlan]:
    """Build a deterministic plan to run an inline snippet.

    The snippet is captured *at plan time* so the user approves the
    exact bytes that will run. Editing the chat after approval can't
    change what executes.

    ``display_filename`` overrides the synthetic ``inline.<ext>`` name
    in the displayed command — used by the file-aware Canvas so a
    repo file opened for editing produces a plan that reads
    ``python demo.py`` (the user's mental model) rather than
    ``python inline.py``.  Pure cosmetic: the actual execution still
    happens via the inline path the executor knows about.
    """
    if not code or not code.strip():
        return None
    canonical, argv0 = _language_for_inline(language)
    if not argv0:
        return None

    suffix = {"python": "py", "javascript": "js", "bash": "sh"}[canonical]
    # The synthetic filename is shown in the command line for clarity.
    # The actual run streams the code via stdin / -c so no file is
    # written to the repo at plan time.
    pseudo_name = (display_filename or "").strip() or f"inline.{suffix}"

    inline_label = (
        f"Canvas edit of {display_filename}"
        if display_filename else "Inline snippet captured"
    )
    checks = [
        SafetyCheck(label=inline_label),
        SafetyCheck(label=f"Runnable {canonical} code"),
        SafetyCheck(label="Runs inside the sandbox"),
        SafetyCheck(label=f"Timeout: {timeout_sec}s"),
        SafetyCheck(label="Network: " + ("enabled" if network else "disabled")),
    ]
    warnings = analyze_safety(code, canonical)

    # The displayed command is the one the user will mentally model.
    # The executor itself may choose -c vs file-based dispatch.
    display_command = {
        "python": ["python", pseudo_name],
        "javascript": ["node", pseudo_name],
        "bash": ["bash", pseudo_name],
    }[canonical]

    return ExecutionPlan(
        plan_id=_new_plan_id(),
        goal=f"Run inline {canonical} snippet",
        source=source,
        language=canonical,
        command=display_command,
        sandbox=sandbox,
        timeout_sec=timeout_sec,
        network=network,
        workdir=workdir,
        file=None,
        inline_code=code,
        safety_checks=checks,
        safety_warnings=warnings,
        parent_run_id=parent_run_id,
    )


__all__ = [
    "ExecutionPlan",
    "SafetyCheck",
    "SafetyWarning",
    "analyze_safety",
    "build_execution_plan_for_file",
    "build_execution_plan_for_inline",
]
