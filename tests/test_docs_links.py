"""Broken-link checker for in-repo markdown — Batch P4-D.

Walks every ``*.md`` file the repo ships, extracts relative links and
images, and asserts each target resolves to a file or directory on
disk.  External links (``http://``, ``https://``, ``mailto:``) and
in-document anchors (``#foo``) are intentionally skipped — those are
the job of an external link checker (e.g. the ``lychee`` CI job) and
of the docs author respectively.

Failing test = "you moved or renamed a file without fixing its
incoming links."  That's the cheapest catch we can get.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# ``[label](url)`` and ``![alt](url)`` —  url runs to the first whitespace, ``)``, or ``)`` followed by EOL.
_LINK_RE = re.compile(r"!?\[[^\]]*?\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

# Markdown files we consider.  We skip anything inside generated dirs
# (.venv, node_modules, build artefacts) and the legacy mcp-stack
# clone that the install workflow may drop into the repo.
_SKIP_PARTS = {".venv", "node_modules", "build", "dist", "htmlcov",
               ".git", "__pycache__", "mcp-stack"}


def _iter_markdown() -> Iterable[Path]:
    for path in REPO_ROOT.rglob("*.md"):
        if any(part in _SKIP_PARTS for part in path.relative_to(REPO_ROOT).parts):
            continue
        yield path


def _extract_local_targets(source: Path) -> List[str]:
    text = source.read_text(encoding="utf-8", errors="replace")
    out: List[str] = []
    for match in _LINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target:
            continue
        if target.startswith(("http://", "https://", "mailto:", "tel:")):
            continue
        if target.startswith("#"):                              # in-doc anchor
            continue
        # Strip any ``#anchor`` suffix, then ``?query`` suffix.
        target = target.split("#", 1)[0]
        target = target.split("?", 1)[0]
        if not target:
            continue
        out.append(target)
    return out


def _resolve(source: Path, target: str) -> Path:
    if target.startswith("/"):
        # Treat root-anchored targets as repo-relative.
        return (REPO_ROOT / target.lstrip("/")).resolve()
    return (source.parent / target).resolve()


@pytest.mark.parametrize("md_path", sorted(_iter_markdown()), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_markdown_links_resolve(md_path: Path) -> None:
    failures: List[Tuple[str, Path]] = []
    for target in _extract_local_targets(md_path):
        resolved = _resolve(md_path, target)
        if not resolved.exists():
            # Allow ``docs/deploy/`` style trailing-slash targets that
            # might omit the ``index.md`` we conventionally add.
            if (resolved / "index.md").exists():
                continue
            failures.append((target, resolved))
    assert not failures, (
        f"{md_path.relative_to(REPO_ROOT)} has broken links:\n"
        + "\n".join(f"  {t!r} → {p}" for t, p in failures)
    )
