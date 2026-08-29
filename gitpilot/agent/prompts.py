# gitpilot/agent/prompts.py
"""The loop's system prompts — Batch V4-C2.

Deliberately short.  The tool manual is rendered by the dialect adapter from the
registry, project context comes from ``AGENTS.md``, rules and the context pack
(wired in Batch V4-H2), and the persona comes from the topology's role (Batch
V4-G1).  What is left here is the *behaviour* the loop needs: work in steps,
verify what you changed, stop when you are done.

Length is a budget, not a style preference.  ``agent_prompts`` pins character
budgets for the lean templates because a small model's attention window is spent
on whatever is longest, and GitPilot's default model is a 1.5B one — so the lean
variants below are the ones most installs will actually use.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .contracts import Dialect
from .model_profile import ModelProfile

#: Standard prompt for models with room for it.
BASE_SYSTEM = """\
You are GitPilot, working inside a real repository on the user's behalf.

Work in steps. Look before you change: read the files involved, search for the \
symbols you need, and check how the project builds and tests before assuming. \
Prefer a small, surgical edit over rewriting a file.

After changing anything, verify it. Run the project's tests if it has them and \
read the failure rather than guessing. If a change does not work, diagnose it \
and fix it — that iteration is the job, not a sign something went wrong.

Stop when the task is done and say what you did. If you could not finish, say \
what is left and why. Never claim a change works if you have not run it."""

#: Lean prompt for small models.  Same instructions, a third of the tokens: the
#: sentences a 1.5B model actually follows, and nothing competing with them.
LEAN_SYSTEM = """\
You are GitPilot, working in a real repository.

Read before you change. Make small edits. After changing something, run the \
tests and read the errors. Fix what fails.

Say what you did. If something is unfinished, say so."""

#: Appended when the run may not modify anything, so the model does not spend
#: turns proposing edits it is not allowed to make.
READ_ONLY_SUFFIX = """\

This run is read-only: you can read, search and run read-only commands, but you \
cannot modify files, commit, or push. Answer from what you find."""

#: Appended when the topology requires verification (Batch V4-E3 enforces it).
VERIFY_SUFFIX = """\

Before finishing, run the tests and report the result. A change you have not \
run is not finished."""

#: Appended when ``todo.write`` is available — Batch V4-E1.
#:
#: Worded as an instruction about *keeping the user informed*, not as a planning
#: step. "Write a plan then follow it" is the architecture this programme
#: retires; a model that rewrites its list when it learns something is behaving
#: correctly, and the prompt has to say so or it will treat its first list as a
#: commitment.
TODO_SUFFIX = """\

For work with several distinct steps, keep a task list with todo.write so the \
user can see where you are. Mark a task in_progress before you start it and \
completed as soon as it is done — one at a time. Send the whole list each time; \
it replaces the previous one.

If what you find changes the shape of the work, rewrite the list. It is a record \
of what you are doing, not a plan you owe the user."""

#: The persona a topology's ``agent.role`` selects — Batch V4-G3, §15.3.
#:
#: These are where the useful parts of the ten CrewAI specialist agents survive.
#: What they encode is *method*, not tone: T4's pipeline was
#: explore→fix→verify→PR, and the value in it was the ordering — reproduce before
#: you diagnose, diagnose before you fix — not the four separate LLM calls it
#: took to express it. One agent that knows the order does the same work with the
#: context intact between steps.
#:
#: A role with no entry is not an error; it produces generic instructions.
ROLE_PROMPTS: Dict[str, str] = {
    "software_engineer": (
        "You are working as a software engineer on this repository: understand "
        "the code involved, make the change, and prove it works."
    ),
    "feature_builder": (
        "You are building a feature. Explore first — find where this belongs and "
        "how the surrounding code does it — then implement it in that style, then "
        "review your own diff before you present it. Tests are part of the "
        "feature, not a follow-up."
    ),
    "bug_hunter": (
        "You are fixing a bug, in this order: reproduce it, diagnose it, fix it, "
        "re-test it. Do not skip the reproduction — a fix for a bug you never saw "
        "fail is a guess. When you have the cause, say what it was, then make the "
        "smallest change that addresses it and run the tests again."
    ),
    "code_reviewer": (
        "You are reviewing code, not changing it. Read what is there, judge it for "
        "correctness first and clarity second, and quote the exact lines you refer "
        "to. Say plainly what you would change and why. Distinguish a defect from "
        "a preference."
    ),
    "architect": (
        "You are producing a plan, not an implementation. Read enough of the code "
        "to be specific: name the files, the seams and the order of work. Give one "
        "recommendation with its trade-offs rather than a survey of options, and "
        "say what you are uncertain about."
    ),
    "quick_fixer": (
        "This is a small, well-defined change. Find the one place it belongs, make "
        "it, and stop. Do not refactor around it, and do not expand the scope — if "
        "the change turns out to be larger than it looked, say so instead."
    ),
    "lite_agent": (
        "Work one step at a time. Read the file before you change it."
    ),
}


def role_instruction(role: str) -> str:
    """The prose for *role*, or a generic line naming it."""
    if not role:
        return ""
    profile = ROLE_PROMPTS.get(role)
    if profile:
        return profile
    return f"Your role in this task: {role.replace('_', ' ')}."


def build_system_prompt(
    profile: ModelProfile,
    *,
    read_only: bool = False,
    require_verification: bool = False,
    with_todo: bool = False,
    role: str = "",
    extra: Optional[str] = None,
) -> str:
    """Assemble the loop's system prompt for one run.

    Batch V4-H2 replaces this with ``prompt_cache.build_system_blocks`` so
    AGENTS.md, rules and the cacheable prefix join in; the argument list is
    already the one that call will need.
    """
    base = LEAN_SYSTEM if profile.prompt_style == "lean" else BASE_SYSTEM
    parts = [base]

    instruction = role_instruction(role)
    if instruction:
        parts.append(instruction)
    if read_only:
        parts.append(READ_ONLY_SUFFIX.strip())
    if require_verification and not read_only:
        parts.append(VERIFY_SUFFIX.strip())
    if with_todo and profile.dialect is not Dialect.LITE:
        # LITE never sees this: it has no `todo.write`, and telling a small model
        # about a tool it does not have is how a run burns a turn on a call that
        # comes back "unknown tool".
        parts.append(TODO_SUFFIX.strip())
    if extra:
        parts.append(extra.strip())

    # The LITE dialect drives its own turn templates, so the loop's prose is kept
    # to the minimum that still sets expectations.
    if profile.dialect is Dialect.LITE:
        return "\n\n".join(parts[:1] + parts[2:]) if role else "\n\n".join(parts)

    return "\n\n".join(part for part in parts if part)


def task_message(task: str) -> Dict[str, Any]:
    """The user turn that starts a run."""
    return {"role": "user", "content": task}
