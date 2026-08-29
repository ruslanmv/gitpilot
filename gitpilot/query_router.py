"""Deterministic query router (Batch B9).

Classifies a user goal into one of a handful of *intents* (fix /
find / info / create / delete / modify), extracts any files the user
mentioned, and emits a strategy hint the planner can either follow
or override.

Pure Python, no LLM call.  The point is that **small local models
(llama3:8b)** sometimes fail to pick the right tool even with rich
descriptions — a deterministic pre-router keeps them on the rails.
Big models can ignore the hint when their judgment is better than
the heuristic; we treat it as advisory, not constraining.

Auto-RAG decision:
* The router signals ``auto_index_repo=True`` only when the query
  is *fuzzy* (natural-language, no symbol tokens, no path mentions)
  AND the repo is big enough to benefit (>= 50 files) AND a RAG
  index doesn't already exist.
* When consent has not been granted yet, the API layer turns that
  signal into an INDEX plan step (see Batch B9 design).  When
  consent IS granted, the API layer auto-builds in the background.

This module returns the *decision*; the API layer translates it
into either a plan step or a background task.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Sequence

# ----------------------------------------------------------------------
# Constants — exposed so tests can pin the heuristics
# ----------------------------------------------------------------------

INTENT_LITERALS = (
    "fix",
    "find",
    "info",
    "create",
    "delete",
    "modify",
    "execute",
    "unknown",
)

# Per-intent trigger words, lowercased.  Order in this table = priority
# when several intents match (first hit wins, except "fix" beats
# "modify" because every fix is a modify but not every modify is a fix).
_INTENT_TRIGGERS: list[tuple[str, tuple[str, ...]]] = [
    # "execute" sits above "fix" because "run the failing test" is an
    # execute request, not a fix request — the user wants to see output,
    # not change the file.  Triggers kept narrow enough that "modify
    # the code" still hits modify, broad enough that the natural ways
    # operators phrase a run-request all classify here:
    #
    #   execute / run / invoke / launch / interpret / evaluate
    #   "in the sandbox" / "in sandbox" — explicit sandbox routing
    #   "test the script" / "try running" — colloquial run synonyms
    ("execute", ("execute ",                      # execute this / the / demo.py / it
                 "run the ", "run this",          # run the script / run this file
                 "run hello", "run main", "run demo",
                 "run my ", "run our ",            # run my script / run our test
                 "invoke ", "launch the script", "launch the program",
                 "interpret the script", "evaluate the script",
                 "in the sandbox", "in sandbox",   # explicit sandbox routing
                 "try running",                    # "try running this"
                 "test the script", "test this script",
                 "test the file",   "test this file",
                 "please execute", "can you execute",
                 "please run",      "can you run")),
    ("fix",    ("fix ", "bug", " error", "broken", "doesn't work",
                "doesnt work", "crash", "traceback", "exception",
                "fails", "failing", "regression")),
    ("delete", ("delete ", "remove ", "drop ", "get rid of",
                "uninstall", "clean up")),
    ("create", ("create ", "add ", "generate ", "new file",
                "write a new", "build a", "make a", "scaffold")),
    ("modify", ("modify ", "change ", "update ", "rename ", "refactor ",
                "replace ", "rewrite ", "convert ", "migrate ", "edit ")),
    ("find",   ("where ", "find ", "search ", "locate ", "show me ",
                "list ", "which file", "look for")),
    ("info",   ("what is ", "what does ", "explain ", "describe ",
                "how does ", "how do ", "tell me ", "summari",
                "overview", "what do you think")),
]

# Runnable extensions — a goal that names one of these files after a run
# verb is an execute request, whatever the surrounding phrasing.  Kept in
# sync with ``agentic._RUNNABLE_EXTENSIONS`` (the short-circuit rejects
# anything else, so a wider list here would only produce dead intents).
_RUNNABLE_EXTS_RE = "py|js|mjs|cjs|sh|bash"

# "run nuclear_shell_demo.py", "please execute src/main.py", "launch
# `build.sh`" — the literal-substring table below cannot express "a run
# verb followed by a filename", so it used to miss every run request that
# named a file it had not been taught by name ("run demo", "run main").
# That is the defect this regex closes: the verb and the file matter, the
# words in between do not.
_RUN_VERB_FILE_RE = re.compile(
    r"\b(?:run|runs|running|execute|exec|launch|invoke|start|interpret|evaluate)\b"
    r"(?:\s+(?:the|this|that|my|our|a|an|it|file|script|program|code|again))*"
    r"\s+[`'\"]?[\w./\-]+\.(?:" + _RUNNABLE_EXTS_RE + r")\b",
    re.IGNORECASE,
)

# "python nuclear_shell_demo.py", "node server.js", "bash install.sh" —
# naming the interpreter is a run request even with no verb at all.
_INTERPRETER_FILE_RE = re.compile(
    r"\b(?:python3?|py|node|nodejs|bash|sh|zsh)\s+[`'\"]?[\w./\-]+\.(?:"
    + _RUNNABLE_EXTS_RE + r")\b",
    re.IGNORECASE,
)

# Repo-file extension whitelist for path extraction — every match
# must end in a "real" extension OR be a well-known extensionless
# file.  Keeps the extractor from grabbing words like "asyncio" that
# happen to contain dots in surrounding punctuation.
_PATH_EXTENSIONS = (
    "py", "ts", "tsx", "js", "jsx", "mjs", "cjs",
    "go", "rs", "rb", "java", "kt", "scala", "swift",
    "c", "h", "cpp", "hpp", "cc", "cxx",
    "md", "rst", "txt", "html", "css", "scss", "sass",
    "json", "yml", "yaml", "toml", "ini", "cfg",
    "sh", "bash", "zsh", "fish",
    "sql", "graphql", "proto",
)
_EXTENSIONLESS_KEY_FILES = (
    "Dockerfile", "Makefile", "LICENSE", "CHANGELOG", "NOTICE",
    "AUTHORS", "Procfile", "Gemfile", "Rakefile", ".gitignore",
    ".env", ".env.example", ".dockerignore",
)

# Indentation-sensitive extensions — the planner gets a stronger hint
# to use Edit (surgical) rather than Write (regenerate).
_INDENTATION_SENSITIVE_EXTS = (
    "py", "yml", "yaml", "haml", "slim", "pug", "jade",
)

# Generated / lock files we refuse to MODIFY.
_FORBIDDEN_EDIT_EXTS = (
    "lock", "min.js", "min.css",
)
_FORBIDDEN_EDIT_NAMES = (
    "poetry.lock", "package-lock.json", "yarn.lock",
    "Cargo.lock", "Gemfile.lock", "go.sum",
)

# Quoted-path regex covers `'README.md'`, `"src/main.py"`, `` `LICENSE` ``.
_QUOTED_PATH_RE = re.compile(r"""[`'"]([\w./\-]+)[`'"]""")
# Bareword path: ``src/main.py``, ``README.md``.  Must contain at
# least one ".ext" or "/".
_BAREWORD_PATH_RE = re.compile(
    r"(?<![\w/])([\w./\-]*[A-Za-z0-9](?:/[\w.\-]+)+|[\w.\-]+\.[A-Za-z][A-Za-z0-9]+)(?!\w)"
)

# A query is "fuzzy" if it reads like natural language (≥4 words,
# no exact symbols, no path-shaped tokens).  Tuned by experimentation
# on representative dev prompts.
_FUZZY_MIN_WORDS = 4
_SYMBOL_RE = re.compile(r"[A-Z][a-z]+(?:[A-Z][a-z]+)+|[a-z_]+_[a-z_]+|[A-Z]{2,}")


# ----------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------

Intent = Literal["fix", "find", "info", "create", "delete", "modify", "execute", "unknown"]
EditStrategy = Literal["surgical", "regenerate", "reject"]


@dataclass(frozen=True)
class RouterDecision:
    """Everything the planner / executor needs to pick a strategy."""

    intent: Intent
    target_files: List[str] = field(default_factory=list)
    tool_priority: List[str] = field(default_factory=list)
    rag_recommended: bool = False
    auto_index_repo: bool = False
    edit_strategy: EditStrategy = "surgical"
    file_policy_notes: str = ""
    rationale: str = ""        # one-liner for the Tasks panel
    repo_too_small_for_rag: bool = False


# ----------------------------------------------------------------------
# Heuristics
# ----------------------------------------------------------------------

def _detect_intent(goal: str) -> Intent:
    # Regex first: "<run verb> <runnable file>" and "<interpreter> <file>"
    # are execute requests no matter how the rest of the sentence reads,
    # and no substring table can enumerate every filename a user may type.
    if _RUN_VERB_FILE_RE.search(goal) or _INTERPRETER_FILE_RE.search(goal):
        return "execute"
    q = " " + goal.lower() + " "
    for intent, triggers in _INTENT_TRIGGERS:
        for t in triggers:
            if t in q:
                return intent  # type: ignore[return-value]
    return "unknown"


def _extract_path_candidates(goal: str) -> List[str]:
    """Pull every plausibly-file-shaped token out of the goal."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _push(tok: str) -> None:
        tok = tok.strip().strip(".,:;()")
        if not tok or tok in seen:
            return
        seen.add(tok)
        candidates.append(tok)

    for m in _QUOTED_PATH_RE.findall(goal):
        _push(m)
    for m in _BAREWORD_PATH_RE.findall(goal):
        _push(m)
    return candidates


def _verify_against_repo(
    candidates: Sequence[str],
    repo_files: Optional[Sequence[str]],
) -> List[str]:
    """Drop candidates that don't exist in the repo.

    Match strategy: exact path or basename.  Returns the canonical
    repo path so the planner's prompt always uses the same casing
    as the actual file (lower vs upper-case readme.md vs README.md).
    """
    if not repo_files:
        return list(candidates)
    file_set = set(repo_files)
    basename_map: dict[str, str] = {}
    for p in repo_files:
        basename = p.rsplit("/", 1)[-1]
        basename_map.setdefault(basename, p)
    out: list[str] = []
    for tok in candidates:
        if tok in file_set:
            out.append(tok)
        elif tok in basename_map:
            out.append(basename_map[tok])
        # Case-insensitive last-chance match.
        else:
            lower = tok.lower()
            for p in repo_files:
                if p.lower() == lower:
                    out.append(p)
                    break
    return out


def _ext_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if name in _EXTENSIONLESS_KEY_FILES:
        return ""
    # Special-case multi-dot extensions before the simple split.
    lname = name.lower()
    if lname.endswith(".min.js"):
        return "min.js"
    if lname.endswith(".min.css"):
        return "min.css"
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _is_fuzzy(goal: str) -> bool:
    """A query is fuzzy when it reads like natural language — no
    symbol-shaped tokens, no path mentions, ≥ N content words."""
    g = goal.strip()
    if not g:
        return False
    if _QUOTED_PATH_RE.search(g) or _BAREWORD_PATH_RE.search(g):
        return False
    if _SYMBOL_RE.search(g):
        return False
    words = [w for w in re.split(r"\s+", g) if len(w) > 2]
    return len(words) >= _FUZZY_MIN_WORDS


def _looks_like_symbol_search(goal: str) -> bool:
    return bool(_SYMBOL_RE.search(goal))


def _file_policy_notes(target_files: Sequence[str]) -> tuple[EditStrategy, str]:
    """Roll up per-file policy into one human-readable note for the
    planner prompt and one machine-readable strategy."""
    if not target_files:
        return "surgical", ""

    forbidden = []
    indentation_sensitive = []
    for p in target_files:
        ext = _ext_of(p)
        name = p.rsplit("/", 1)[-1]
        if ext in _FORBIDDEN_EDIT_EXTS or name in _FORBIDDEN_EDIT_NAMES:
            forbidden.append(p)
        elif ext in _INDENTATION_SENSITIVE_EXTS:
            indentation_sensitive.append(p)

    if forbidden:
        notes = (
            f"Refuse to MODIFY: {', '.join(forbidden)} — these are "
            "generated / lock files.  Edit the source manifest instead."
        )
        return "reject", notes

    if indentation_sensitive:
        notes = (
            "Use 'Edit a section of a file' (surgical) — the file "
            "extension is indentation-sensitive.  Quote leading "
            "whitespace exactly when constructing old_string."
        )
        return "surgical", notes

    return "surgical", "Use 'Edit a section of a file' for any MODIFY action."


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------

DEFAULT_FUZZY_REPO_SIZE_FOR_RAG = 50


def classify(
    goal: str,
    *,
    repo_files: Optional[Sequence[str]] = None,
    rag_index_exists: bool = False,
    force_no_rag: bool = False,
) -> RouterDecision:
    """Classify a user goal into a :class:`RouterDecision`.

    Pure: same inputs → identical outputs, no I/O, no LLM call.

    ``repo_files`` is optional but recommended — without it we can't
    verify that the files the user mentioned actually exist.
    """
    if not goal or not goal.strip():
        return RouterDecision(
            intent="unknown",
            rationale="empty goal",
            tool_priority=["Get repository summary"],
        )

    intent = _detect_intent(goal)
    raw_candidates = _extract_path_candidates(goal)
    targets = _verify_against_repo(raw_candidates, repo_files)
    fuzzy = _is_fuzzy(goal)
    repo_size = len(repo_files) if repo_files is not None else 0
    too_small_for_rag = repo_size > 0 and repo_size < DEFAULT_FUZZY_REPO_SIZE_FOR_RAG

    # RAG / semantic search is only useful for *read-leaning* intents
    # — finding something, diagnosing a fix, refactoring across files.
    # Informational queries are answered from the repo map (no need
    # for vectors); create / delete are structural and benefit from
    # Glob, not embeddings.
    _rag_eligible_intent = intent in ("find", "fix", "modify", "unknown")
    rag_recommended = (
        fuzzy
        and _rag_eligible_intent
        and not force_no_rag
        and not too_small_for_rag
    )
    auto_index_repo = (
        rag_recommended
        and not rag_index_exists
        and not too_small_for_rag
    )

    edit_strategy, file_notes = _file_policy_notes(targets)

    tools: list[str]
    if intent == "info":
        # Informational: read README + repo map, no plan.
        tools = ["Read file content", "Get repository summary"]
    elif intent in ("fix", "modify") and targets:
        tools = ["Read file content", "Edit a section of a file"]
        if edit_strategy == "reject":
            tools = ["Read file content"]
    elif intent in ("fix", "modify") and not targets:
        # Need to find the file first.
        if rag_recommended:
            tools = [
                "Find code by semantic search",
                "Search file contents",
                "Read file content",
                "Edit a section of a file",
            ]
        else:
            tools = [
                "Search file contents",
                "Read file content",
                "Edit a section of a file",
            ]
    elif intent == "find":
        if rag_recommended:
            tools = ["Find code by semantic search", "Search file contents",
                     "Read file content"]
        elif _looks_like_symbol_search(goal):
            tools = ["Search file contents", "Read file content"]
        else:
            tools = ["Find files matching a pattern", "Search file contents",
                     "Read file content"]
    elif intent == "create":
        tools = ["Get repository summary", "Read file content",
                 "Write or update a file in the repository"]
    elif intent == "delete":
        tools = ["Find files matching a pattern",
                 "Delete a file from the repository"]
    elif intent == "execute":
        # The EXECUTE action in the planner needs the file's content so
        # the executor can ship it to the sandbox. Reading is the only
        # prerequisite — execution itself happens at apply-time, not
        # plan-time.
        tools = ["Read file content"]
    else:
        # unknown — default to the safe exploration set.
        tools = [
            "Get repository summary",
            "Find files matching a pattern",
            "Search file contents",
            "Read file content",
        ]

    rationale = _build_rationale(
        intent=intent, targets=targets, rag=rag_recommended,
        auto_index=auto_index_repo, fuzzy=fuzzy,
    )

    return RouterDecision(
        intent=intent,
        target_files=targets,
        tool_priority=tools,
        rag_recommended=rag_recommended,
        auto_index_repo=auto_index_repo,
        edit_strategy=edit_strategy,
        file_policy_notes=file_notes,
        rationale=rationale,
        repo_too_small_for_rag=too_small_for_rag,
    )


def _build_rationale(
    *, intent: Intent, targets: Sequence[str], rag: bool,
    auto_index: bool, fuzzy: bool,
) -> str:
    parts = [f"intent={intent}"]
    if targets:
        parts.append(f"targets={','.join(targets[:3])}")
    if rag:
        parts.append("rag=preferred")
    if auto_index:
        parts.append("auto-index=requested")
    if fuzzy and not rag:
        parts.append("fuzzy")
    return " · ".join(parts)


# ----------------------------------------------------------------------
# Hint rendering — what the planner sees inside its prompt
# ----------------------------------------------------------------------

def render_planner_hint(decision: RouterDecision) -> str:
    """Render the decision as a small markdown block to splice into
    the planner's context_pack.  Advisory tone — the planner may
    override when context demands it."""
    lines = [
        "## ROUTING HINT (advisory — override if the goal demands more)",
        f"- Intent: **{decision.intent}**",
    ]
    if decision.target_files:
        lines.append(
            "- Likely target files: " + ", ".join(
                f"`{p}`" for p in decision.target_files[:5]
            )
        )
    if decision.tool_priority:
        lines.append(
            "- Preferred tools (in order): "
            + " → ".join(f"`{t}`" for t in decision.tool_priority)
        )
    if decision.file_policy_notes:
        lines.append(f"- File policy: {decision.file_policy_notes}")
    if decision.rag_recommended:
        lines.append(
            "- Semantic search recommended for this fuzzy query.  "
            "Prefer `Find code by semantic search` before `Search file contents`."
        )
    if decision.auto_index_repo:
        lines.append(
            "- A semantic index has not been built for this repo yet.  "
            "Include a Step 1 with action `INDEX` so the user can "
            "approve the one-time build (~30 s, local, ~12 MB)."
        )
    if decision.intent == "info":
        lines.append(
            "- This is an informational query.  Produce a plan with "
            "READ-only file actions and a substantive summary — do NOT "
            "create / modify / delete files."
        )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_FUZZY_REPO_SIZE_FOR_RAG",
    "RouterDecision",
    "classify",
    "render_planner_hint",
]
