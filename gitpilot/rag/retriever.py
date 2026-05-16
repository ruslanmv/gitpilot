"""Top-k semantic retrieval for the RAG pipeline (Batch B7).

Public function :func:`retrieve_top_k` and dataclass
:class:`RetrievedChunk`.  Thin wrapper over :class:`RagStore.query`
that also applies a simple Maximum-Marginal-Relevance (MMR) re-rank
when ``mmr=True`` so the agent doesn't get N near-duplicates from
the same file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .embedder import Embedder, cosine_similarity, get_default_embedder
from .store import QueryHit, RagStore, _persist_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float


def _to_retrieved(h: QueryHit) -> RetrievedChunk:
    return RetrievedChunk(
        path=h.path,
        start_line=h.start_line,
        end_line=h.end_line,
        text=h.text,
        score=h.score,
    )


def _mmr_rerank(
    query_vec: List[float],
    candidates: List[QueryHit],
    *,
    k: int,
    lambda_: float = 0.7,
    embedder: Embedder,
) -> List[QueryHit]:
    """Maximum Marginal Relevance — pick a diverse top-k that still
    ranks by similarity to the query.  ``lambda_`` weights relevance
    vs. novelty: 1.0 = pure relevance, 0.0 = pure diversity."""
    if not candidates or k <= 0:
        return []
    # Pre-embed the candidate texts so we can compute pairwise novelty.
    texts = [c.text for c in candidates]
    vecs = embedder(texts)

    selected: List[int] = []
    remaining = list(range(len(candidates)))
    while remaining and len(selected) < k:
        best_idx = remaining[0]
        best_score = -1e9
        for idx in remaining:
            rel = cosine_similarity(query_vec, vecs[idx])
            if selected:
                novelty = max(
                    cosine_similarity(vecs[idx], vecs[s])
                    for s in selected
                )
            else:
                novelty = 0.0
            score = lambda_ * rel - (1 - lambda_) * novelty
            if score > best_score:
                best_score = score
                best_idx = idx
        selected.append(best_idx)
        remaining.remove(best_idx)
    return [candidates[i] for i in selected]


def retrieve_top_k(
    query: str,
    *,
    owner: str,
    repo: str,
    branch: str,
    k: int = 8,
    embedder: Optional[Embedder] = None,
    persist_dir: Optional[Path] = None,
    mmr: bool = True,
) -> List[RetrievedChunk]:
    """Return the k most-relevant chunks across the persisted index.

    Returns an empty list (silently) when:

    * the persist dir doesn't exist yet (no index built),
    * the embedder can't be initialised,
    * any internal error in ChromaDB.

    Callers should treat "no results" as "fall back to other tools",
    not as an error.
    """
    if not query or k <= 0:
        return []
    emb = embedder or get_default_embedder()
    pdir = persist_dir or _persist_dir(owner, repo, branch)
    if not pdir.exists():
        return []

    try:
        store = RagStore(
            owner=owner, repo=repo, branch=branch,
            embedder=emb, persist_dir=pdir,
        )
    except Exception as exc:
        logger.debug("[rag] retriever: store init failed: %s", exc)
        return []

    # Over-fetch when MMR is on so re-ranking has something to chew on.
    over_k = max(k, k * 3) if mmr else k
    hits = store.query(query, k=over_k)
    if not hits:
        return []
    if mmr and len(hits) > k:
        qv = emb([query])[0]
        hits = _mmr_rerank(qv, hits, k=k, embedder=emb)
    else:
        hits = hits[:k]
    return [_to_retrieved(h) for h in hits]


__all__ = ["RetrievedChunk", "retrieve_top_k"]
