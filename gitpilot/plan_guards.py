# gitpilot/plan_guards.py
"""Post-hoc validation for planner output — catches the failure mode
where the planner LLM returns either a refusal or a hallucinated stock
plan that has nothing to do with the user's repository.

Background
----------
A real production trace surfaced this sequence:

1.  The Explorer agent failed to call ``Read file content`` three times
    (CrewAI's Pydantic validator rejected the schema-shaped dict the
    LLM produced — fixed separately in :mod:`gitpilot.agent_tools`).
2.  The Planner LLM, given no real file content, emitted the canned
    refusal ``"I cannot provide information or guidance on illegal or
    harmful activities"``.
3.  Despite that, a perfectly schema-valid but *completely unrelated*
    plan was rendered to the user — placeholder paths like
    ``/process/documents/new-process-document.pdf`` for a Python repo
    that only has a ``README.md``.

These guards add two cheap checks before the plan is shipped:

* :func:`detect_refusal`   — looks for known refusal phrases in any
  text artefact attached to the planner result.
* :func:`assess_plan`      — cross-references the plan's referenced
  paths against the repo's real file list and a small set of
  suspicious-prefix heuristics; raises :class:`PlanHallucinationError`
  when the plan is clearly not grounded in the actual repository.

Both helpers are pure functions; the caller decides what to do with
the verdict (typically: surface a friendly error to the user instead
of rendering the plan).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Set, Tuple

# Known refusal phrasings.  Add new ones as we observe them — kept
# case-insensitive and matched as substrings.
_REFUSAL_PATTERNS: Tuple[str, ...] = (
    "i cannot provide information",
    "i cannot help with",
    "i am unable to help",
    "i'm unable to help",
    "i cannot assist",
    "i can't assist",
    "i cannot fulfill",
    "i can't fulfill",
    "as an ai language model",
    "i must decline",
    "i refuse to",
    "illegal or harmful",
    "against my guidelines",
    # Tool-loop hallucinations — the agent's repeated tool calls hit
    # CrewAI's same-input limiter, panics, and pretends earlier
    # observations were never returned.  Surfaced in INFN-GE/Nuclear-
    # Physics session 83c77335cb344236 (Repository Explorer crew).
    "i cannot continue with this task",
    "i cannot proceed with this task",
    "i cannot proceed without",
    "i cannot continue without",
    "please provide them before proceeding",
    "please provide them before i can",
    "i cannot create a plan",
)

# Path prefixes that are suspicious on their own — they're the kind of
# placeholders that generic LLM training data inserts when the model has
# no real grounding.  Hits do not auto-fail; they raise the suspicion
# score, which is then weighed against the actual file list.
_SUSPICIOUS_PATH_TOKENS: Tuple[str, ...] = (
    "/process/documents/",
    "/process/manual/",
    "new-process-document",
    "new_process_document",
    "process/requirements",
    "/templates/example",
    "/example/sample",
    "/placeholder/",
    "tbd.",
    "todo.",
)


# ----------------------------------------------------------------------
# Refusal detection
# ----------------------------------------------------------------------

def detect_refusal(text: Any) -> Optional[str]:
    """Return the first matching refusal phrase in *text*, else ``None``.

    Accepts any input: strings, ``CrewOutput``-like objects with a
    ``.raw`` attribute, or arbitrary objects with a useful ``str``
    representation.  Empty / non-textual inputs return ``None``.
    """
    if text is None:
        return None
    body = _stringify(text)
    if not body:
        return None
    lowered = body.lower()
    for phrase in _REFUSAL_PATTERNS:
        if phrase in lowered:
            return phrase
    return None


# ----------------------------------------------------------------------
# Plan plausibility
# ----------------------------------------------------------------------

@dataclass
class PlanAssessment:
    """Outcome of cross-referencing a plan against the real repo."""

    total_files: int = 0
    hits_in_repo: int = 0
    misses_in_repo: int = 0
    suspicious_paths: List[str] = field(default_factory=list)
    create_paths: List[str] = field(default_factory=list)
    modify_paths: List[str] = field(default_factory=list)

    @property
    def hit_ratio(self) -> float:
        denom = self.hits_in_repo + self.misses_in_repo
        return (self.hits_in_repo / denom) if denom else 1.0

    @property
    def hallucinated(self) -> bool:
        """Heuristic verdict.  Triggers when the plan asks to modify /
        delete files that don't exist in the repo *and* contains
        placeholder-shaped paths.  Pure-CREATE plans are not flagged
        because creating new files is legitimate (e.g. ``demo.py``)."""
        if not self.suspicious_paths:
            return False
        # If every MODIFY/DELETE path missed the repo and the plan also
        # leans on placeholder-shaped paths, the plan is not grounded.
        if (self.hits_in_repo + self.misses_in_repo) > 0 and self.hit_ratio == 0.0:
            return True
        # Otherwise: too many suspicious paths relative to total.
        return len(self.suspicious_paths) * 2 >= max(1, self.total_files)


class PlanHallucinationError(RuntimeError):
    """Raised when a plan fails the plausibility check."""

    def __init__(self, message: str, assessment: PlanAssessment) -> None:
        super().__init__(message)
        self.assessment = assessment


def assess_plan(plan: Any, repo_files: Iterable[str]) -> PlanAssessment:
    """Cross-reference *plan*'s referenced paths against *repo_files*.

    *plan* must expose a ``.steps`` iterable of objects with a
    ``.files`` list of objects carrying ``.path`` and ``.action``
    attributes (matches :class:`gitpilot.agentic.PlanResult`).
    Unknown shapes are tolerated — fields we cannot read are skipped.
    """
    known: Set[str] = {_normalise_path(p) for p in repo_files if p}
    assessment = PlanAssessment()

    for step in _safe_iter(getattr(plan, "steps", None)):
        for file_ref in _safe_iter(getattr(step, "files", None)):
            path = str(getattr(file_ref, "path", "") or "").strip()
            action = str(getattr(file_ref, "action", "") or "").strip().upper()
            if not path:
                continue
            assessment.total_files += 1
            if action == "CREATE":
                assessment.create_paths.append(path)
            elif action in {"MODIFY", "DELETE"}:
                assessment.modify_paths.append(path)
                if _normalise_path(path) in known:
                    assessment.hits_in_repo += 1
                else:
                    assessment.misses_in_repo += 1
            if _is_suspicious(path):
                assessment.suspicious_paths.append(path)
    return assessment


def ensure_plan_grounded(plan: Any, repo_files: Iterable[str]) -> None:
    """Raise :class:`PlanHallucinationError` if *plan* is not grounded.

    Convenience wrapper around :func:`assess_plan` for callers that
    just want to bail loudly when the plan looks bogus.
    """
    assessment = assess_plan(plan, repo_files)
    if assessment.hallucinated:
        raise PlanHallucinationError(
            "The planner produced paths that do not match this repository. "
            "Suspicious entries: "
            + ", ".join(assessment.suspicious_paths[:5])
            + ("…" if len(assessment.suspicious_paths) > 5 else ""),
            assessment=assessment,
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _stringify(value: Any) -> str:
    # Collect every text fragment the planner might have produced and
    # join them — a refusal can hide in any of them.  We deliberately
    # walk ``tasks_output`` even when ``raw`` is set, because CrewAI's
    # ``raw`` is sometimes the empty-string sentinel even though the
    # per-task outputs carry the real text.
    parts: List[str] = []
    raw = getattr(value, "raw", None)
    if isinstance(raw, str) and raw:
        parts.append(raw)
    if hasattr(value, "tasks_output"):
        for task in getattr(value, "tasks_output", []) or []:
            text = getattr(task, "raw", None)
            if isinstance(text, str) and text:
                parts.append(text)
    if parts:
        return "\n".join(parts)
    if isinstance(value, str):
        return value
    return str(value)


def _safe_iter(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    try:
        return list(value)
    except TypeError:
        return ()


def _normalise_path(path: str) -> str:
    return re.sub(r"\s+", "", path.lstrip("./")).lower()


def _is_suspicious(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in _SUSPICIOUS_PATH_TOKENS)


# ----------------------------------------------------------------------
# READ-entry enrichment
# ----------------------------------------------------------------------
#
# Small / quantised LLMs (llama3:8b via Ollama is the canonical example
# we observe) consistently drop READ entries from plan steps — even when
# the step's *description* clearly says "Read the content of README.md
# and …".  Without that READ entry the Action Plan card shows only the
# CREATE / MODIFY / DELETE half of the work, which (a) misleads the user
# about what the agent will actually look at, and (b) makes the plan
# fail the "every file you reference must be in files[]" contract we
# documented in the v2 feasibility plan.
#
# ``enrich_plan_with_reads`` scans each step's description for paths
# that exist in the repo's actual file list (so we never invent missing
# files) and that aren't already on the step's ``files[]``.  Each match
# is appended with ``action="READ"``.  The plan object is mutated in
# place; the original step ordering is preserved.

# Quoted file mentions:   "README.md", `src/main.py`, 'docs/index.md'
_QUOTED_REF_RE = re.compile(r"[`'\"]([\w./\-]+\.\w+)[`'\"]")
# Bareword file mentions: README.md  src/main.py  docs/index.md
_BARE_REF_RE = re.compile(r"(?<![\w/])([\w./\-]+\.(?:md|py|ts|tsx|js|jsx|json|yml|yaml|toml|cfg|ini|txt|rst|sh|bash|go|rs|rb|java|c|h|cpp|hpp))(?!\w)")


def enrich_plan_with_reads(plan: Any, repo_files: Iterable[str]) -> int:
    """Inject missing READ entries into a plan based on description text.

    Returns the number of READ entries added.  Safe to call on legacy
    plans (no-op when the step shape is unfamiliar).  The plan is
    mutated in place — callers that need an immutable variant should
    pass a deep copy.
    """
    known = {_normalise_path(p): p for p in repo_files if p}
    if not known:
        return 0

    added = 0
    for step in _safe_iter(getattr(plan, "steps", None)):
        description = str(getattr(step, "description", "") or "")
        title = str(getattr(step, "title", "") or "")
        haystack = f"{title}\n{description}"
        if not haystack.strip():
            continue

        # Existing file paths on this step (any action) — we never add
        # a READ entry for something the planner already listed.
        existing_paths = {
            _normalise_path(str(getattr(f, "path", "") or ""))
            for f in _safe_iter(getattr(step, "files", None))
        }

        for pattern in (_QUOTED_REF_RE, _BARE_REF_RE):
            for match in pattern.finditer(haystack):
                raw = match.group(1)
                normalised = _normalise_path(raw)
                if normalised in existing_paths:
                    continue
                if normalised not in known:
                    continue            # don't invent files that aren't in the repo
                canonical = known[normalised]
                file_entry = _build_read_entry(canonical, step)
                if file_entry is None:
                    continue
                _append_file_to_step(step, file_entry)
                existing_paths.add(normalised)
                added += 1
    return added


def _append_file_to_step(step: Any, file_entry: Any) -> None:
    files = getattr(step, "files", None)
    if files is None:
        try:
            step.files = [file_entry]
        except Exception:
            return
        return
    try:
        files.append(file_entry)
    except AttributeError:
        try:
            step.files = list(files) + [file_entry]
        except Exception:
            return


def _build_read_entry(path: str, step: Any) -> Any:
    """Return an object shaped like the other entries on *step*'s ``files``
    list, with ``path=path`` and ``action="READ"``.  Falls back to a
    plain dict when the step has no template to mimic."""
    template: Any = None
    for existing in _safe_iter(getattr(step, "files", None)):
        template = existing
        break

    if template is None:
        return {"path": path, "action": "READ"}

    try:
        # Pydantic v2 BaseModel or dataclass: try ``.model_copy`` / new()
        copy = getattr(template, "model_copy", None)
        if callable(copy):
            return template.model_copy(update={"path": path, "action": "READ"})
    except Exception:
        pass
    try:
        cls = type(template)
        return cls(path=path, action="READ")
    except Exception:
        return {"path": path, "action": "READ"}
