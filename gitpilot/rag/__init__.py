"""GitPilot local RAG pipeline (Batch B7).

On-prem-first design — no cloud calls, no API keys.  Defaults to
ChromaDB's bundled MiniLM-L6-v2 (downloaded once, ~80 MB on disk).
A pure-Python ``HashingEmbedder`` is shipped alongside as a dependency-
free fallback for tests and minimal-footprint deployments.

Public entry points:

* :func:`build_index_from_files` — given an iterable of
  ``(path, content)`` pairs, chunk + embed + persist to ChromaDB.
* :func:`retrieve_top_k` — embed a query, return the best-matching
  chunks across the persisted index.
* :func:`semantic_search_tool` — the CrewAI tool wrapper.

Storage layout:

    <RAG_ROOT>/<owner>/<repo>/<branch>/      ← Chroma persistent client
    <RAG_ROOT>/<owner>/<repo>/<branch>/meta.json
        {
          "indexed_files": {"<path>": "<file_sha>"},
          "embedder":      "default" | "hashing",
          "embedding_dim": 384,
          "updated_at":    "ISO-8601",
        }

Flags:

* ``rag_retrieval`` — gates the agent tool registration and the
  /api/repos/.../index endpoints.  Default **off** (opt-in apex).
"""
from __future__ import annotations

FLAG_RAG_RETRIEVAL = "rag_retrieval"

from .chunker import Chunk, chunk_file, chunk_files  # noqa: E402
from .embedder import (  # noqa: E402
    Embedder,
    HashingEmbedder,
    get_default_embedder,
)
from .indexer import (  # noqa: E402
    IndexBuildReport,
    IndexMeta,
    build_index_from_files,
)
from .retriever import RetrievedChunk, retrieve_top_k  # noqa: E402
from .store import RagStore  # noqa: E402

__all__ = [
    "FLAG_RAG_RETRIEVAL",
    "Chunk",
    "Embedder",
    "HashingEmbedder",
    "IndexBuildReport",
    "IndexMeta",
    "RagStore",
    "RetrievedChunk",
    "build_index_from_files",
    "chunk_file",
    "chunk_files",
    "get_default_embedder",
    "retrieve_top_k",
]
