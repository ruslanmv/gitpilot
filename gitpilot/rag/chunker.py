"""File-to-chunk splitter for the RAG indexer (Batch B7).

Strategy (simplest viable):

* **Line-window chunking with overlap.**  Each chunk is up to
  ``CHUNK_LINES`` source lines (default 40), with ``CHUNK_OVERLAP``
  lines (default 5) of overlap to preserve context across boundaries.
* **Binary / oversize skip.**  Files larger than ``MAX_FILE_BYTES``
  or detected as binary are skipped silently.  We never want a 10 MB
  minified JS or a binary blob to poison the index.
* **Deterministic chunk ids.**  ``<sha1(path)>:<start_line>``, so
  re-indexing the same file produces identical ids and ChromaDB's
  upsert keeps the collection tidy.

The chunker is intentionally language-naive — AST-aware splitting
(tree-sitter per language) is the next refinement once the simpler
approach is working.  Even the naive version dramatically outperforms
"read every file" on >100-file repos.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Iterator, List

CHUNK_LINES = 40
CHUNK_OVERLAP = 5
MAX_FILE_BYTES = 256 * 1024   # 256 KB per file — bigger files are
                              # almost always generated / minified.


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    path: str
    start_line: int          # 1-indexed, inclusive
    end_line: int            # 1-indexed, inclusive
    text: str
    file_sha: str            # short sha of the source file at chunk time


def _short_sha(data: str) -> str:
    return hashlib.sha1(data.encode("utf-8", errors="replace")).hexdigest()[:16]


def _looks_binary(content: str) -> bool:
    """Heuristic — null bytes or a high non-printable ratio in the
    first chunk usually means binary.  Anything ChromaDB embeds must
    be reasonable text."""
    sample = content[:2048]
    if "\x00" in sample:
        return True
    if not sample:
        return False
    bad = sum(
        1 for c in sample
        if not (c.isprintable() or c in "\n\r\t ")
    )
    return bad / max(1, len(sample)) > 0.3


def chunk_file(
    path: str,
    content: str,
    *,
    chunk_lines: int = CHUNK_LINES,
    overlap: int = CHUNK_OVERLAP,
) -> List[Chunk]:
    """Split a single file's content into overlapping line windows.

    Returns an empty list when the file is empty, binary, or above
    :data:`MAX_FILE_BYTES`.  Never raises on bad input.
    """
    if not content:
        return []
    if len(content.encode("utf-8", errors="replace")) > MAX_FILE_BYTES:
        return []
    if _looks_binary(content):
        return []

    chunk_lines = max(5, int(chunk_lines))
    overlap = max(0, min(int(overlap), chunk_lines - 1))
    step = chunk_lines - overlap

    lines = content.splitlines()
    if not lines:
        return []
    file_sha = _short_sha(content)
    path_sha = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]

    out: List[Chunk] = []
    i = 0
    while i < len(lines):
        window = lines[i : i + chunk_lines]
        if not window:
            break
        start = i + 1
        end = i + len(window)
        chunk_id = f"{path_sha}:{start}"
        out.append(
            Chunk(
                chunk_id=chunk_id,
                path=path,
                start_line=start,
                end_line=end,
                text="\n".join(window),
                file_sha=file_sha,
            )
        )
        if end >= len(lines):
            break
        i += step
    return out


def chunk_files(
    files: Iterable[tuple[str, str]],
) -> Iterator[Chunk]:
    """Yield chunks across an iterable of (path, content) pairs."""
    for path, content in files:
        for chunk in chunk_file(path, content):
            yield chunk
