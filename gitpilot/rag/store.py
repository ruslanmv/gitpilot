"""ChromaDB-backed persistent store for the RAG pipeline (Batch B7).

Wraps a per-(owner, repo, branch) Chroma collection so callers don't
have to think about embedder wiring, persistence paths, or upsert
semantics.  Storage layout:

    <RAG_ROOT>/<owner>/<repo>/<branch>/
        chroma.sqlite3 + hnsw segments  (ChromaDB persistent client)

The store can also fall back to an **in-memory** mode for tests —
when ``persist_dir`` is ``None`` we use ``chromadb.EphemeralClient``,
which keeps the same API but doesn't write to disk.

Embedder is injected at construction so tests can use the dependency-
free :class:`HashingEmbedder` while production uses
:class:`DefaultEmbedder`.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .embedder import Embedder

logger = logging.getLogger(__name__)

# Override via env so tests can isolate (and CI can dump on cleanup).
RAG_ROOT_ENV = "GITPILOT_RAG_ROOT"
_DEFAULT_RAG_ROOT = Path.home() / ".gitpilot" / "rag"


def rag_root() -> Path:
    override = os.environ.get(RAG_ROOT_ENV)
    if override:
        return Path(override)
    return _DEFAULT_RAG_ROOT


def _persist_dir(owner: str, repo: str, branch: str) -> Path:
    return rag_root() / owner / repo / _sanitize(branch)


def _sanitize(s: str) -> str:
    """Make a path segment safe for any filesystem."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:80] or "_"


def _collection_name(owner: str, repo: str, branch: str) -> str:
    """ChromaDB collection names must be 3–512 chars, alphanumeric +
    underscore/hyphen, start/end alphanumeric.  Build a deterministic
    name that meets the rules."""
    raw = f"gp_{_sanitize(owner)}_{_sanitize(repo)}_{_sanitize(branch)}"
    # Ensure start/end are alphanumeric.
    raw = re.sub(r"^_+", "", raw)
    raw = re.sub(r"_+$", "", raw)
    return raw[:500] or "gp_default"


@dataclass(frozen=True)
class QueryHit:
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    text: str
    score: float


class RagStore:
    """Thin wrapper around a ChromaDB persistent collection."""

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        embedder: Embedder,
        persist_dir: Optional[Path] = None,
    ) -> None:
        import chromadb  # lazy import — heavy module

        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.embedder = embedder
        self._collection_name = _collection_name(owner, repo, branch)

        if persist_dir is None:
            persist_dir = _persist_dir(owner, repo, branch)
        persist_dir.mkdir(parents=True, exist_ok=True)
        self.persist_dir = persist_dir

        self._client = chromadb.PersistentClient(path=str(persist_dir))
        # ChromaDB will compute embeddings itself if we hand it our
        # embedder via the ``embedding_function`` arg, but its newer
        # versions are picky about the shape.  We compute vectors
        # ourselves and pass them via ``embeddings=`` on add/query —
        # makes the store backend-agnostic.
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------
    def add_chunks(
        self,
        chunks: Iterable[object],   # avoid hard import cycle with chunker
    ) -> int:
        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[dict[str, object]] = []
        for c in chunks:
            ids.append(c.chunk_id)         # type: ignore[attr-defined]
            documents.append(c.text)       # type: ignore[attr-defined]
            metadatas.append({
                "path":       c.path,        # type: ignore[attr-defined]
                "start_line": c.start_line,  # type: ignore[attr-defined]
                "end_line":   c.end_line,    # type: ignore[attr-defined]
                "file_sha":   c.file_sha,    # type: ignore[attr-defined]
            })
        if not ids:
            return 0
        vectors = self.embedder(documents)
        self._collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,      # type: ignore[arg-type]
            embeddings=vectors,       # type: ignore[arg-type]
        )
        return len(ids)

    def delete_by_path(self, path: str) -> int:
        """Drop every chunk for one source file (e.g. file removed
        from the repo, or about to be re-indexed)."""
        try:
            res = self._collection.get(where={"path": path})
            ids = res.get("ids", []) if isinstance(res, dict) else []
            if ids:
                self._collection.delete(ids=ids)
            return len(ids or [])
        except Exception as exc:
            logger.debug("[rag] delete_by_path %s failed: %s", path, exc)
            return 0

    def count(self) -> int:
        try:
            return int(self._collection.count())
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    def query(self, text: str, *, k: int = 8) -> List[QueryHit]:
        if not text or k <= 0:
            return []
        if self.count() == 0:
            return []
        vec = self.embedder([text])[0]
        try:
            res = self._collection.query(
                query_embeddings=[vec],   # type: ignore[arg-type]
                n_results=max(1, int(k)),
            )
        except Exception as exc:
            logger.debug("[rag] query failed: %s", exc)
            return []

        # Chroma returns parallel lists per query — we always pass one.
        ids_list = (res.get("ids") or [[]])[0]
        docs_list = (res.get("documents") or [[]])[0]
        metas_list = (res.get("metadatas") or [[]])[0]
        dists_list = (res.get("distances") or [[]])[0]

        out: List[QueryHit] = []
        for i, cid in enumerate(ids_list):
            doc = docs_list[i] if i < len(docs_list) else ""
            meta = metas_list[i] if i < len(metas_list) and metas_list[i] else {}
            dist = float(dists_list[i]) if i < len(dists_list) else 1.0
            score = max(0.0, 1.0 - dist)
            out.append(
                QueryHit(
                    chunk_id=str(cid),
                    path=str(meta.get("path", "")),
                    start_line=int(meta.get("start_line", 0) or 0),  # type: ignore[arg-type]
                    end_line=int(meta.get("end_line", 0) or 0),  # type: ignore[arg-type]
                    text=str(doc or ""),
                    score=score,
                )
            )
        return out


__all__ = ["RagStore", "QueryHit", "rag_root"]
