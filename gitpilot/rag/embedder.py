"""Local embedding backends for the RAG pipeline (Batch B7).

GitPilot is on-prem-first.  We refuse to require a cloud API key for
the indexing path.  Two backends ship:

* :class:`DefaultEmbedder` — wraps ChromaDB's bundled
  ``all-MiniLM-L6-v2`` ONNX model.  ~80 MB downloaded once, 384-dim
  vectors, free.  This is the production default.
* :class:`HashingEmbedder` — pure-Python, deterministic, zero deps.
  Produces a 256-dim sparse-hash representation.  Quality is below
  MiniLM but the recall is good enough for unit tests and for
  environments where ``onnxruntime`` can't be installed.

Both implement the same :class:`Embedder` Protocol so callers can swap
freely.  Tests inject :class:`HashingEmbedder` so the suite doesn't
require an 80 MB download.

The selection function :func:`get_default_embedder` tries the
production embedder first and falls back transparently when the
underlying deps aren't available.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Iterable, List, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

HASHING_DIM = 256
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@runtime_checkable
class Embedder(Protocol):
    """Embedder Protocol — matches ChromaDB's EmbeddingFunction shape."""

    @property
    def name(self) -> str:
        ...

    @property
    def dim(self) -> int:
        ...

    def __call__(self, texts: List[str]) -> List[List[float]]:
        ...


# ----------------------------------------------------------------------
# HashingEmbedder — dependency-free fallback
# ----------------------------------------------------------------------

class HashingEmbedder:
    """Deterministic hash-bucket embedder.

    Tokenises the input on identifier boundaries, hashes each token
    into one of :data:`HASHING_DIM` buckets, counts occurrences, then
    L2-normalises.  Two semantically-similar code snippets that share
    identifier vocabulary will produce vectors close in cosine
    distance — good enough for "find the file that mentions
    ``foo_bar``" without any model download.
    """
    name = "hashing-v1"

    def __init__(self, dim: int = HASHING_DIM) -> None:
        self._dim = max(32, int(dim))

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_one(self, text: str) -> List[float]:
        buckets = [0.0] * self._dim
        for tok in TOKEN_RE.findall(text.lower()):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            buckets[h % self._dim] += 1.0
        # L2 normalise so cosine similarity stays in [0, 1].
        norm = math.sqrt(sum(b * b for b in buckets))
        if norm == 0.0:
            return buckets
        return [b / norm for b in buckets]

    def __call__(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_one(t) for t in texts]


# ----------------------------------------------------------------------
# DefaultEmbedder — ChromaDB's bundled MiniLM
# ----------------------------------------------------------------------

class DefaultEmbedder:
    """Wraps Chroma's :class:`DefaultEmbeddingFunction` so it matches
    our :class:`Embedder` Protocol.  Lazily constructed so we don't
    pay the ONNX model load when only HashingEmbedder is used."""
    name = "chromadb-default-minilm-l6-v2"
    _ef: object | None = None

    @property
    def dim(self) -> int:
        # MiniLM-L6-v2 is 384-dim.  Hard-coded because Chroma's EF
        # doesn't expose this through a stable attribute.
        return 384

    def _load(self) -> object:
        if self._ef is None:
            try:
                from chromadb.utils.embedding_functions import (
                    DefaultEmbeddingFunction,
                )
            except Exception as exc:
                raise RuntimeError(
                    "DefaultEmbedder requires chromadb + onnxruntime. "
                    "Install them or use HashingEmbedder instead."
                ) from exc
            self._ef = DefaultEmbeddingFunction()
        return self._ef

    def __call__(self, texts: List[str]) -> List[List[float]]:
        ef = self._load()
        out = ef(texts)  # type: ignore[operator]
        # Ensure we return plain lists of floats (some Chroma versions
        # return numpy arrays).
        return [[float(x) for x in vec] for vec in out]


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------

def get_default_embedder() -> Embedder:
    """Return the production embedder if available, else the hashing
    fallback.  Caller doesn't need to know which one was picked —
    both honour the same Protocol."""
    try:
        emb = DefaultEmbedder()
        # Trigger lazy load up-front so we fail fast if onnxruntime
        # is missing — easier to recover than discovering it at the
        # first ``__call__``.
        emb._load()
        return emb
    except Exception as exc:
        logger.info(
            "[rag] falling back to HashingEmbedder (DefaultEmbedder unavailable): %s",
            exc,
        )
        return HashingEmbedder()


def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    """L2-cosine.  Used by the in-process retriever for the hashing
    backend (ChromaDB handles this internally on its own vectors)."""
    al = list(a)
    bl = list(b)
    if not al or not bl:
        return 0.0
    n = min(len(al), len(bl))
    dot = sum(al[i] * bl[i] for i in range(n))
    na = math.sqrt(sum(x * x for x in al[:n]))
    nb = math.sqrt(sum(x * x for x in bl[:n]))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
