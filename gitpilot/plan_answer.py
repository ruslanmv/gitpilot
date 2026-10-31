"""Turn a read-only plan into an answer.

``/api/chat/plan`` is the web chat's path when the live stream is not used.
For a question ("what is in this repo?", "what does this project do?") the
planner returns steps that only READ files, and the chat shows the plan's
``summary`` as the reply. But the planner is asked for a plan, so that summary
says what it *would* read ("Inspect the single existing file…"), and nothing
reads anything.

:func:`answer_read_only_plan` closes that gap: when every step only READs, it
reads those files (capped), asks the configured model one question grounded on
them and on the repository's file list, and stores the answer as the plan's
``summary``. The chat already renders such a plan as a plain reply. When the
model cannot be reached the answer is built from the files themselves, so the
reply is never just the plan's intent.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

MAX_FILES = 4
MAX_CHARS_PER_FILE = 12_000
MAX_TOTAL_CHARS = 32_000
MAX_LISTED_PATHS = 300

Fetch = Callable[..., Awaitable[str]]
Ask = Callable[[List[Dict[str, str]]], Awaitable[str]]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def read_only_paths(plan: Any) -> Optional[List[str]]:
    """The files a plan reads, or ``None`` when the plan does anything else."""
    if _get(plan, "execution_plan"):
        return None
    steps = _get(plan, "steps") or []
    if not steps:
        return None
    paths: List[str] = []
    for step in steps:
        for f in _get(step, "files") or []:
            if _get(f, "action") != "READ":
                return None
            path = str(_get(f, "path") or "").strip().lstrip("/")
            if path and path not in paths:
                paths.append(path)
    return paths


def _readme_paths(repo_paths: Sequence[str]) -> List[str]:
    """Top-level README files, as a default when the plan names nothing."""
    return [p for p in repo_paths if "/" not in p and p.lower().startswith("readme")][:1]


def build_messages(goal: str, repo_full_name: str, ref: Optional[str],
                   files: Dict[str, str], repo_paths: Sequence[str]) -> List[Dict[str, str]]:
    """The grounded question for the model."""
    listed = list(repo_paths)[:MAX_LISTED_PATHS]
    more = len(repo_paths) - len(listed)
    parts = [f"Question: {goal.strip()}", "",
             f"Repository: {repo_full_name}" + (f" (ref {ref})" if ref else ""),
             f"Files in the repository ({len(repo_paths)}):"]
    parts += [f"- {p}" for p in listed]
    if more > 0:
        parts.append(f"- … and {more} more")
    for path, text in files.items():
        parts += ["", f"----- {path} -----", text]
    system = (
        "You answer questions about a code repository. Use only the file list and the file "
        "contents provided. Name files by their paths. If they do not contain the answer, say "
        "so and say which file would. Do not describe a plan or what you would do: answer. "
        "Be concise and use Markdown."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(parts)}]


def fallback_answer(repo_full_name: str, files: Dict[str, str], repo_paths: Sequence[str]) -> str:
    """An answer built from the files alone, for when no model can be asked."""
    lines: List[str] = []
    if repo_paths:
        shown = list(repo_paths)[:30]
        noun = "file" if len(repo_paths) == 1 else "files"
        lines.append(f"**{repo_full_name}** contains {len(repo_paths)} {noun}:")
        lines += [f"- `{p}`" for p in shown]
        if len(repo_paths) > len(shown):
            lines.append(f"- … and {len(repo_paths) - len(shown)} more")
    for path, text in files.items():
        excerpt = text.strip()[:1200]
        if not excerpt:
            lines += ["", f"`{path}` is empty."]
            continue
        lines += ["", f"**{path}**:", "", "\n".join("> " + ln for ln in excerpt.splitlines())]
        if len(text.strip()) > len(excerpt):
            lines.append("> …")
    return "\n".join(lines).strip() or f"I could not read any files from {repo_full_name}."


async def _ask_configured_model(messages: List[Dict[str, str]]) -> str:
    from .direct_chat import chat, resolve_endpoint

    endpoint = resolve_endpoint()
    if endpoint is None:
        raise RuntimeError("the active provider has no OpenAI-compatible chat endpoint")
    return await chat(messages, endpoint=endpoint, timeout=120.0)


async def answer_read_only_plan(
    plan: Any,
    *,
    goal: str,
    owner: str,
    repo: str,
    token: Optional[str],
    ref: Optional[str],
    repo_paths: Optional[Sequence[str]] = None,
    fetch: Optional[Fetch] = None,
    list_tree: Optional[Callable[..., Awaitable[Any]]] = None,
    ask: Optional[Ask] = None,
) -> bool:
    """Replace a read-only plan's ``summary`` with an answer. ``True`` if it did.

    Plans that create, modify, delete, execute or index are left untouched.
    ``fetch`` / ``list_tree`` / ``ask`` default to the provider-aware
    :mod:`gitpilot.scm` helpers and the configured model (tests inject fakes).
    """
    paths = read_only_paths(plan)
    if paths is None:
        return False
    from . import scm

    fetch = fetch or scm.get_file
    known: List[str] = list(repo_paths or [])
    if not known:
        try:
            tree = await (list_tree or scm.get_repo_tree)(owner, repo, token=token, ref=ref or "HEAD")
            known = [t["path"] for t in (tree or []) if t.get("path")]
        except Exception as exc:  # noqa: BLE001 - the answer can still be attempted
            logger.info("[plan-answer] repository tree unavailable: %s", exc)
    if known:
        paths = [p for p in paths if p in set(known)]  # never read a path that is not there
    if not paths:
        paths = _readme_paths(known)

    files: Dict[str, str] = {}
    budget = MAX_TOTAL_CHARS
    for path in paths[:MAX_FILES]:
        if budget <= 0:
            break
        try:
            text = await fetch(owner, repo, path, token=token, ref=ref or "HEAD")
        except Exception as exc:  # noqa: BLE001 - skip unreadable files
            logger.info("[plan-answer] could not read %s: %s", path, exc)
            continue
        text = str(text or "")[: min(MAX_CHARS_PER_FILE, budget)]
        files[path] = text
        budget -= len(text)

    repo_full_name = f"{owner}/{repo}"
    answer = ""
    try:
        answer = (await (ask or _ask_configured_model)(
            build_messages(goal, repo_full_name, ref, files, known))).strip()
    except Exception as exc:  # noqa: BLE001 - fall back to the files themselves
        logger.info("[plan-answer] model answer unavailable, answering from files: %s", exc)
    if not answer:
        answer = fallback_answer(repo_full_name, files, known)

    if isinstance(plan, dict):
        plan["summary"] = answer
    else:
        plan.summary = answer
    logger.info("[plan-answer] answered a read-only plan from %d file(s)", len(files))
    return True


__all__ = ["answer_read_only_plan", "read_only_paths", "build_messages", "fallback_answer"]
