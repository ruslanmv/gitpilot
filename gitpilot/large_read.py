"""Reading large files without flooding the model's context.

Every file-read tool in GitPilot (repository, local workspace, tool registry)
frames its output through :func:`frame`. Small files come back exactly as
before. Large ones come back the way Claude Code reads them:

* **Paged.** At most ``READ_PAGE_LINES`` lines and ``READ_PAGE_CHARS``
  characters per call, with a footer that names the next ``offset`` so the
  agent can keep reading with the window tool.
* **Line-numbered** (``cat -n`` style), so the agent can cite and edit exact
  lines and ask for the right window next.
* **Long lines cut** at ``LINE_MAX_CHARS`` with a note of how much was cut, so
  one minified line cannot use up the budget.
* **Binary files are described, not dumped.**

Conversation-level compaction (folding older turns into a summary) lives in
:mod:`gitpilot.auto_compact` and :mod:`gitpilot.context_budget`; this module
keeps any single tool result bounded so compaction rarely has to run.
"""
from __future__ import annotations

READ_PAGE_LINES = 2000
READ_PAGE_CHARS = 100_000
LINE_MAX_CHARS = 2000
_BINARY_SNIFF = 8192


def is_binary(content: str) -> bool:
    return "\x00" in content[:_BINARY_SNIFF]


def fits_inline(content: str) -> bool:
    """True when the whole file can be returned in one read."""
    if len(content) > READ_PAGE_CHARS:
        return False
    lines = content.splitlines()
    return len(lines) <= READ_PAGE_LINES and all(len(line) <= LINE_MAX_CHARS for line in lines)


def frame(path: str, content: str | None) -> str:
    """The read-tool result for ``path``: the legacy framing when the file fits,
    otherwise the first page (see :func:`window`)."""
    content = content or ""
    if is_binary(content):
        return f"Content of {path}:\n---\n(binary file, {len(content)} bytes, not shown)\n---"
    if fits_inline(content):
        return f"Content of {path}:\n---\n{content}\n---"
    return window(path, content, 0, READ_PAGE_LINES)


def window(path: str, content: str | None, offset: int, limit: int) -> str:
    """Lines ``offset`` (0-indexed) to ``offset + limit``, numbered and bounded."""
    content = content or ""
    if is_binary(content):
        return f"Content of {path}:\n---\n(binary file, {len(content)} bytes, not shown)\n---"
    lines = content.splitlines()
    total = len(lines)
    if total == 0:
        return f"Content of {path}:\n---\n(empty file)\n---"
    start = max(0, min(offset, total))
    stop = min(total, start + max(1, limit))
    out: list[str] = []
    used = 0
    cut_lines = 0
    end = start
    for index in range(start, stop):
        line = lines[index]
        if len(line) > LINE_MAX_CHARS:
            extra = len(line) - LINE_MAX_CHARS
            line = f"{line[:LINE_MAX_CHARS]}… [{extra} more characters on this line]"
            cut_lines += 1
        numbered = f"{index + 1:>6}\t{line}"
        if out and used + len(numbered) + 1 > READ_PAGE_CHARS:
            break
        out.append(numbered)
        used += len(numbered) + 1
        end = index + 1
    header = f"Content of {path} (lines {start + 1}-{end} of {total})"
    notes: list[str] = []
    if cut_lines:
        notes.append(f"{cut_lines} long line(s) were shortened.")
    if end < total:
        notes.append(
            f"{total - end} more lines. Continue with the window tool: "
            f"offset={end}, limit={READ_PAGE_LINES}."
        )
    footer = ("\n" + " ".join(notes)) if notes else ""
    return f"{header}:\n---\n" + "\n".join(out) + f"\n---{footer}"
