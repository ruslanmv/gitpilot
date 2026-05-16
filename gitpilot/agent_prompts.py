"""Lean agent-prompt templates for GitPilot (Batch B12).

Rewrites every agent persona and task description with the small-
model rules:

* No emotional intensifiers (CRITICAL, THOROUGHLY).
* No "etc." — explicit list or omit.
* No speculative example filenames (package.json on a repo that
  doesn't have one is hallucination bait).
* Facts block lives at the bottom of every prompt — small models
  over-weight the last segment.
* Per-intent rule blocks instead of one universal block — we only
  inject the create / modify / delete / info rules that match what
  the user actually asked for, picked off the B9 query router's
  ``RouterDecision.intent``.

The templates are plain ``str.format``-friendly so callers don't
have to know about the placeholders.  Single source of truth so
tests can pin character budgets and forbidden-keyword bans without
chasing duplicated strings across ``agentic.py``.

Gated by the ``lean_prompts`` feature flag (default **on**).  When
off, callers fall back to the legacy verbose prompts in agentic.py.
"""
from __future__ import annotations

from . import flags

FLAG_LEAN_PROMPTS = "lean_prompts"

# Character budgets per prompt — pinned by tests so a future
# "let me add one more rule" edit can't silently bloat them.
# Budget covers framing + a typical 10-15 file list.  Larger repos
# pay more in the file-list section — that's the useful facts the
# planner needs and we count it under the planner-stack test instead.
PLAN_TASK_CHAR_BUDGET           = 1_400
EXPLORER_TASK_CHAR_BUDGET       =   500
CREATE_FILE_TASK_CHAR_BUDGET    =   700
MODIFY_FILE_TASK_CHAR_BUDGET    =   600
CODE_WRITER_BACKSTORY_BUDGET    =   300
EXPLORER_BACKSTORY_BUDGET       =   200
PLANNER_BACKSTORY_BUDGET        =   250
SPECIALIST_BACKSTORY_BUDGET     =   220

# Words to scrub from every prompt.  These are the high-volume,
# small-model-confusing tokens identified in the inventory.
FORBIDDEN_KEYWORDS = (
    "CRITICAL",
    "THOROUGHLY",
    "MUST",            # emotional imperative — replace with the verb
    "etc.",
    "package.json",   # speculative example file that primed hallucination
)


# ----------------------------------------------------------------------
# Backstories
# ----------------------------------------------------------------------

EXPLORER_BACKSTORY = (
    "You inspect repositories using the supplied tools. "
    "You report only what the tools return.  You do not "
    "speculate about files or structure."
)

EXPLORER_GOAL = (
    "Inspect the repository and produce a fact-only summary"
)


PLANNER_BACKSTORY = (
    "You write structured refactor plans from verified repository "
    "facts.  You only reference files that appear in the supplied "
    "file list.  DELETE actions require that the file exists in the "
    "list.  CREATE actions require that the path does not."
)

PLANNER_GOAL = (
    "Design a JSON plan for the user goal using only verified files"
)


CODE_WRITER_BACKSTORY = (
    "You write clean, working code or documentation that matches "
    "the requested file path's extension.  When reading existing "
    "files is needed, you use the supplied tools first."
)

CODE_WRITER_GOAL = (
    "Generate file content that satisfies the plan step"
)


# ----------------------------------------------------------------------
# Explorer task
# ----------------------------------------------------------------------

EXPLORER_TASK_TEMPLATE = """\
Repository: {repo_full_name}
Active ref: {active_ref}

Required tool calls (in order):
1. Get repository summary
2. List all files in repository
3. Get directory structure
4. Read README.md only if it appears in the list

Rules:
- Mention only files returned by the tools.
- Do not invent files or folders.

Return exactly:

REPOSITORY EXPLORATION REPORT
Files Found:
- <one path per line>

Key Files:
- <subset>

Directory Structure:
<tree>

File Types:
<ext>=<count>, ...
"""


# ----------------------------------------------------------------------
# Plan task — intent-routed rule blocks
# ----------------------------------------------------------------------

# Header + footer wrap each per-intent block.  The footer is the
# "facts block" the user flagged: lives at the bottom so small
# models give it the most attention weight.
PLAN_TASK_HEADER_TEMPLATE = """\
User goal: {goal}
Repository: {repo_full_name}
Active ref: {active_ref}

Existing files (verified by tools):
{file_list_lines}

"""

PLAN_TASK_RULES_CREATE = """\
Rules:
- The user asked to create new content.  Include at least one CREATE file.
- READ existing files only when needed as input for the new file.
- Do not include MODIFY or DELETE unless the goal asks for them.
"""

PLAN_TASK_RULES_MODIFY = """\
Rules:
- Use MODIFY only for files in the existing-files list above.
- READ a file when you need its content before modifying.
- Do not CREATE or DELETE unless the goal asks for it.
"""

PLAN_TASK_RULES_DELETE = """\
Rules:
- Use DELETE only for files in the existing-files list above.
- Do not include CREATE or MODIFY actions.
- Files the user wants to keep are absent from the plan.
"""

PLAN_TASK_RULES_FIND = """\
Rules:
- The user asked a search question.
- Plan READ actions for files likely to contain the answer.
- Include a substantive summary that answers the question.
"""

PLAN_TASK_RULES_INFO = """\
Rules:
- The user asked an informational question.
- Empty steps is fine; the summary itself is the answer.
- Use READ only when you need a specific file's content.
"""

PLAN_TASK_RULES_UNKNOWN = """\
Rules:
- READ / MODIFY / DELETE only for files in the existing-files list.
- CREATE only for paths NOT in that list.
- Match the action to what the user goal asks for.
"""

# Schema block kept tight — one example object, no prose explanations.
PLAN_TASK_SCHEMA = """\
Return one JSON object only (no markdown fences, no prose):
{
  "goal": "...",
  "summary": "...",
  "steps": [
    {
      "step_number": 1,
      "title": "...",
      "description": "...",
      "files": [
        {"path": "<existing file>", "action": "READ"},
        {"path": "<new file>",      "action": "CREATE"}
      ],
      "risks": null
    }
  ]
}

JSON rules:
- "action" is one of: CREATE, MODIFY, DELETE, READ, INDEX
- "step_number" is a positive integer
- "risks" is either a string or null (the JSON null literal)
- The entire response is the JSON object — nothing before or after
"""

# Footer = the facts block.  Last 200-300 chars of the prompt =
# highest attention weight on small models.
PLAN_TASK_FOOTER_TEMPLATE = """\
Known facts:
- Total files in repository: {file_count}
- A path NOT in the existing-files list above does NOT exist.
- Never mention a file that is not in that list as if it exists.

Now produce the JSON plan.
"""


_INTENT_TO_RULES = {
    "create":  PLAN_TASK_RULES_CREATE,
    "modify":  PLAN_TASK_RULES_MODIFY,
    "fix":     PLAN_TASK_RULES_MODIFY,    # fix = modify under the hood
    "delete":  PLAN_TASK_RULES_DELETE,
    "find":    PLAN_TASK_RULES_FIND,
    "info":    PLAN_TASK_RULES_INFO,
    "unknown": PLAN_TASK_RULES_UNKNOWN,
}


def render_plan_task(
    *,
    goal: str,
    repo_full_name: str,
    active_ref: str,
    file_list: list[str],
    intent: str | None,
) -> str:
    """Build the planner's task description from verified facts.

    ``intent`` is the literal from :class:`gitpilot.query_router.RouterDecision`.
    Unknown / missing intent falls back to the generic rule block.
    """
    file_lines = "\n".join(f"- {p}" for p in file_list) if file_list else "(empty repository)"
    rules = _INTENT_TO_RULES.get((intent or "unknown").lower(), PLAN_TASK_RULES_UNKNOWN)
    return (
        PLAN_TASK_HEADER_TEMPLATE.format(
            goal=goal, repo_full_name=repo_full_name,
            active_ref=active_ref, file_list_lines=file_lines,
        )
        + rules
        + "\n"
        + PLAN_TASK_SCHEMA
        + "\n"
        + PLAN_TASK_FOOTER_TEMPLATE.format(file_count=len(file_list))
    )


def render_explorer_task(*, repo_full_name: str, active_ref: str) -> str:
    return EXPLORER_TASK_TEMPLATE.format(
        repo_full_name=repo_full_name, active_ref=active_ref,
    )


# ----------------------------------------------------------------------
# Code-writer tasks — CREATE and MODIFY
# ----------------------------------------------------------------------

CREATE_FILE_TASK_TEMPLATE = """\
Generate the full contents of a new file: {file_path}

Goal: {goal}
Step context: {step_description}

Rules:
- Match the file extension's conventions ({extension}).
- If existing files are relevant, use the supplied tools to read them.
- Output ONLY the file content (no explanations, no markdown fences).
"""

MODIFY_FILE_TASK_TEMPLATE = """\
Modify the file: {file_path}

Goal: {goal}
Step context: {step_description}

Current file content:
{current_content}

Rules:
- Preserve every line that does not need to change.
- Match the file extension's conventions ({extension}).
- Output ONLY the complete updated file (no explanations, no fences).
"""


def render_create_file_task(
    *,
    file_path: str,
    goal: str,
    step_description: str,
) -> str:
    return CREATE_FILE_TASK_TEMPLATE.format(
        file_path=file_path,
        goal=goal,
        step_description=step_description,
        extension=_ext_of(file_path),
    )


def render_modify_file_task(
    *,
    file_path: str,
    goal: str,
    step_description: str,
    current_content: str,
) -> str:
    return MODIFY_FILE_TASK_TEMPLATE.format(
        file_path=file_path,
        goal=goal,
        step_description=step_description,
        extension=_ext_of(file_path),
        current_content=current_content,
    )


def _ext_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return name or "(no extension)"
    return "." + name.rsplit(".", 1)[-1].lower()


# ----------------------------------------------------------------------
# Specialist agent backstories (Issue / PR / Search / Code Review / …)
# ----------------------------------------------------------------------
#
# These were ~500-char persona blocks under the previous design.  Each
# is now ~150-200 chars: role + scope + single tool-use sentence.

SPECIALIST_BACKSTORIES = {
    "issue_management": (
        "You manage GitHub issues — list, create, comment, label, close, assign. "
        "You use the supplied issue tools and report concrete results."
    ),
    "pr_management": (
        "You manage GitHub pull requests — list, create, review, comment, merge. "
        "You use the supplied PR tools and report concrete results."
    ),
    "search_discovery": (
        "You answer search and discovery questions about the repository. "
        "You use file-listing and content-search tools and cite exact matches."
    ),
    "code_review": (
        "You review code for correctness, clarity, and obvious bugs. "
        "You quote the specific lines you reference."
    ),
    "learning_guidance": (
        "You answer GitHub how-to and convention questions in plain text. "
        "You do not modify the repository."
    ),
    "local_editor": (
        "You edit local files using the supplied filesystem tools. "
        "You preserve existing content unless instructed to change it."
    ),
    "terminal_executor": (
        "You run terminal commands using the supplied shell tool. "
        "You explain command output briefly."
    ),
}


# ----------------------------------------------------------------------
# Flag check helper
# ----------------------------------------------------------------------

def lean_prompts_enabled() -> bool:
    """Single source of truth for callers in agentic.py — flag-on means
    use the templates here; flag-off falls back to legacy verbose
    strings still defined inline in agentic.py."""
    return flags.is_on(FLAG_LEAN_PROMPTS, default=True)


__all__ = [
    "FLAG_LEAN_PROMPTS",
    "FORBIDDEN_KEYWORDS",
    "PLAN_TASK_CHAR_BUDGET",
    "EXPLORER_TASK_CHAR_BUDGET",
    "CREATE_FILE_TASK_CHAR_BUDGET",
    "MODIFY_FILE_TASK_CHAR_BUDGET",
    "CODE_WRITER_BACKSTORY_BUDGET",
    "EXPLORER_BACKSTORY_BUDGET",
    "PLANNER_BACKSTORY_BUDGET",
    "SPECIALIST_BACKSTORY_BUDGET",
    "EXPLORER_BACKSTORY",
    "EXPLORER_GOAL",
    "PLANNER_BACKSTORY",
    "PLANNER_GOAL",
    "CODE_WRITER_BACKSTORY",
    "CODE_WRITER_GOAL",
    "SPECIALIST_BACKSTORIES",
    "lean_prompts_enabled",
    "render_create_file_task",
    "render_explorer_task",
    "render_modify_file_task",
    "render_plan_task",
]
