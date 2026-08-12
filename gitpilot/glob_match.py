# gitpilot/glob_match.py
"""Path globbing with `/`-aware semantics — extracted in Batch V4-A3.

Moved here verbatim from :mod:`gitpilot.agent_tools` so the canonical
``fs.glob`` / ``fs.grep`` handlers can use the exact same matcher without
importing that module, which pulls CrewAI (and litellm behind it) into the
process.  ``agent_tools`` re-exports these names, so the CrewAI tools and the
registry tools provably share one implementation rather than two that agree
today.
"""
from __future__ import annotations

import re
from typing import List, Pattern

GLOB_DEFAULT_MAX_RESULTS = 200   # cap for "Find files matching a pattern"
GLOB_HARD_MAX_RESULTS = 1_000


def glob_to_regex(pattern: str) -> Pattern[str]:
    """Translate a shell-style glob into a regex with proper `/`-aware
    semantics — the same contract Claude Code, ripgrep and bash use:

    * ``*``  matches anything **except** ``/``
    * ``**`` matches anything **including** ``/`` (any number of segments)
    * ``?``  matches exactly one non-``/`` character
    * ``[abc]`` character class (passed through to regex)
    * everything else is literal

    The result is anchored with ``\\A`` and ``\\Z`` so it must match the
    full path — ``*.py`` will not falsely match ``src/foo.py``.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # `**` — match any number of full segments.  When the
                # following character is `/` consume it as part of the
                # match (so `**/foo.py` correctly matches `foo.py`
                # at the repo root).
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == ".":
            out.append(r"\.")
            i += 1
        elif c == "[":
            # Character class — pass through up to the matching ']'.
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(r"\[")
                i += 1
            else:
                out.append(pattern[i : j + 1])
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile(r"\A" + "".join(out) + r"\Z")


def glob_match(paths: List[str], pattern: str) -> List[str]:
    """Match paths against a glob with `/`-aware semantics."""
    rx = glob_to_regex(pattern)
    return [p for p in paths if rx.match(p)]
