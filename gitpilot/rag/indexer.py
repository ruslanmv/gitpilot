"""Index-builder orchestration for the RAG pipeline (Batch B7).

Take a list of ``(path, content)`` pairs, run them through the
chunker, push the chunks into the :class:`RagStore`, and persist a
small ``meta.json`` next to the Chroma directory so subsequent runs
can do incremental re-indexing instead of re-embedding everything.

Public surface is :func:`build_index_from_files` and
:class:`IndexBuildReport`.  The function is **synchronous** —
embedding is CPU-bound and we deliberately stay off async so future
batching / multi-process parallelism doesn't fight an event loop.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Optional

from .chunker import chunk_file
from .embedder import Embedder, get_default_embedder
from .store import RagStore, _persist_dir

logger = logging.getLogger(__name__)


@dataclass
class IndexMeta:
    """On-disk header for an index — small JSON next to the Chroma dir."""
    owner: str
    repo: str
    branch: str
    embedder: str
    embedding_dim: int
    indexed_files: dict[str, str] = field(default_factory=dict)  # path -> file_sha
    updated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )

    @classmethod
    def load(cls, persist_dir: Path) -> Optional["IndexMeta"]:
        path = persist_dir / "meta.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        try:
            return cls(
                owner=str(raw.get("owner", "") or ""),
                repo=str(raw.get("repo", "") or ""),
                branch=str(raw.get("branch", "") or ""),
                embedder=str(raw.get("embedder", "") or ""),
                embedding_dim=int(raw.get("embedding_dim", 0) or 0),
                indexed_files={
                    str(k): str(v)
                    for k, v in (raw.get("indexed_files") or {}).items()
                },
                updated_at=str(raw.get("updated_at", "") or ""),
            )
        except Exception:
            return None

    def save(self, persist_dir: Path) -> None:
        persist_dir.mkdir(parents=True, exist_ok=True)
        (persist_dir / "meta.json").write_text(
            json.dumps(
                {
                    "owner": self.owner,
                    "repo": self.repo,
                    "branch": self.branch,
                    "embedder": self.embedder,
                    "embedding_dim": self.embedding_dim,
                    "indexed_files": self.indexed_files,
                    "updated_at": self.updated_at,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


@dataclass
class IndexBuildReport:
    files_seen: int = 0
    files_indexed: int = 0      # actually re-embedded this run
    files_skipped: int = 0      # unchanged since last index
    chunks_added: int = 0
    embedder_name: str = ""
    embedding_dim: int = 0


def _file_sha(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()[:16]


def build_index_from_files(
    files: Iterable[tuple[str, str]],
    *,
    owner: str,
    repo: str,
    branch: str,
    embedder: Optional[Embedder] = None,
    persist_dir: Optional[Path] = None,
    force_full_rebuild: bool = False,
) -> IndexBuildReport:
    """Index a batch of files into the per-(owner/repo/branch) store.

    Incremental: a file whose content hasn't changed since the last
    build (matching ``file_sha`` in ``meta.json``) is skipped entirely
    — no re-chunking, no re-embedding.

    ``force_full_rebuild=True`` deletes existing chunks and re-indexes
    everything.  Used by /api/repos/.../index/build with force=True.
    """
    emb = embedder or get_default_embedder()
    pdir = persist_dir or _persist_dir(owner, repo, branch)
    store = RagStore(
        owner=owner, repo=repo, branch=branch,
        embedder=emb, persist_dir=pdir,
    )
    meta = IndexMeta.load(pdir) or IndexMeta(
        owner=owner, repo=repo, branch=branch,
        embedder=emb.name, embedding_dim=emb.dim,
    )
    if meta.embedder != emb.name or meta.embedding_dim != emb.dim:
        # Embedder changed since last build — vectors are incomparable
        # so we must rebuild from scratch.
        logger.info(
            "[rag] embedder changed (%s/%d → %s/%d) — full rebuild",
            meta.embedder, meta.embedding_dim, emb.name, emb.dim,
        )
        force_full_rebuild = True
        meta = IndexMeta(
            owner=owner, repo=repo, branch=branch,
            embedder=emb.name, embedding_dim=emb.dim,
        )

    report = IndexBuildReport(
        embedder_name=emb.name,
        embedding_dim=emb.dim,
    )

    new_indexed: dict[str, str] = {} if force_full_rebuild else dict(meta.indexed_files)

    pending_chunks = []
    for path, content in files:
        if not path or content is None:
            continue
        report.files_seen += 1
        sha = _file_sha(content)
        old_sha = meta.indexed_files.get(path)
        if not force_full_rebuild and old_sha == sha:
            report.files_skipped += 1
            continue

        # Drop stale chunks for this file before adding fresh ones.
        if old_sha is not None:
            store.delete_by_path(path)

        chunks = chunk_file(path, content)
        if not chunks:
            # File was empty / binary / too large.  Drop it from the
            # index entirely so we don't keep referencing stale chunks.
            new_indexed.pop(path, None)
            continue
        pending_chunks.extend(chunks)
        new_indexed[path] = sha
        report.files_indexed += 1

    if force_full_rebuild:
        # Drop everything for paths NOT in the new set as well — handles
        # files that disappeared.
        for stale_path in set(meta.indexed_files) - set(new_indexed):
            store.delete_by_path(stale_path)

    if pending_chunks:
        report.chunks_added = store.add_chunks(pending_chunks)

    meta.indexed_files = new_indexed
    meta.embedder = emb.name
    meta.embedding_dim = emb.dim
    meta.updated_at = datetime.now(UTC).isoformat()
    meta.save(pdir)

    return report


__all__ = ["IndexMeta", "IndexBuildReport", "build_index_from_files"]
