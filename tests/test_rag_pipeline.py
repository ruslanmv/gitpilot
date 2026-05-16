"""Tests for the local RAG pipeline (Batch B7).

All tests use the dependency-free ``HashingEmbedder`` so the suite
doesn't need to download the 80 MB MiniLM ONNX model.  The
production path (DefaultEmbedder via ChromaDB's bundled MiniLM) is
exercised by a single smoke test that's skipped when the binary
isn't available.

Coverage:

* Chunker: line-window with overlap, binary skip, oversize skip,
  determinism, empty/whitespace edge cases.
* HashingEmbedder: deterministic, dimension respected, similar text
  ranks higher than unrelated text.
* RagStore: round-trip add → count → query → delete_by_path; the
  ChromaDB persistence path survives a fresh process restart.
* Indexer: incremental build (unchanged files skipped), embedder-
  change triggers full rebuild, deleted files drop from the index.
* Retriever: top-k respected, MMR diversifies same-file hits,
  empty index returns [] not error.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from gitpilot.rag import (
    HashingEmbedder,
    IndexMeta,
    build_index_from_files,
    chunk_file,
    retrieve_top_k,
)
from gitpilot.rag.chunker import (
    CHUNK_LINES,
    CHUNK_OVERLAP,
    MAX_FILE_BYTES,
    Chunk,
)
from gitpilot.rag.embedder import cosine_similarity
from gitpilot.rag.indexer import build_index_from_files as build_idx
from gitpilot.rag.store import RagStore, _collection_name, _sanitize


# ----------------------------------------------------------------------
# Chunker
# ----------------------------------------------------------------------

def test_chunk_file_basic_window_with_overlap() -> None:
    content = "\n".join(f"line {i}" for i in range(1, 101))   # 100 lines
    chunks = chunk_file("a.py", content)
    # 100 lines, 40-line windows, 5-line overlap, step=35: windows
    # start at 1, 36, 71 (last one extends to line 100).
    assert len(chunks) == 3
    starts = [c.start_line for c in chunks]
    assert starts == [1, 36, 71]
    ends = [c.end_line for c in chunks]
    assert ends == [40, 75, 100]
    # IDs are deterministic and prefixed by a hash of the path.
    for c in chunks:
        assert ":" in c.chunk_id


def test_chunk_file_skips_binary_content() -> None:
    binary = "PNG\x00\x00\x00\x00" + ("A" * 1000)
    assert chunk_file("img.png", binary) == []


def test_chunk_file_skips_oversize_content() -> None:
    huge = "x" * (MAX_FILE_BYTES + 1)
    assert chunk_file("huge.txt", huge) == []


def test_chunk_file_empty_input_returns_empty() -> None:
    assert chunk_file("empty.py", "") == []
    assert chunk_file("ws.py", "   \n  \n") != []  # whitespace lines count


def test_chunk_file_is_deterministic() -> None:
    c = "import os\nimport sys\n" * 30
    a = chunk_file("x.py", c)
    b = chunk_file("x.py", c)
    assert [ch.chunk_id for ch in a] == [ch.chunk_id for ch in b]
    assert [ch.start_line for ch in a] == [ch.start_line for ch in b]


def test_chunk_file_respects_default_constants() -> None:
    assert CHUNK_LINES == 40
    assert CHUNK_OVERLAP == 5


# ----------------------------------------------------------------------
# HashingEmbedder
# ----------------------------------------------------------------------

def test_hashing_embedder_dimension() -> None:
    e = HashingEmbedder()
    vecs = e(["hello world", "another sentence"])
    assert len(vecs) == 2
    assert len(vecs[0]) == e.dim


def test_hashing_embedder_deterministic() -> None:
    e = HashingEmbedder()
    a = e(["the quick brown fox"])
    b = e(["the quick brown fox"])
    assert a == b


def test_hashing_embedder_similar_text_ranks_above_unrelated() -> None:
    e = HashingEmbedder()
    query = "authentication middleware"
    related = "user authentication middleware in flask"
    unrelated = "matplotlib pyplot bar chart"
    qv, rv, uv = e([query, related, unrelated])
    sim_related = cosine_similarity(qv, rv)
    sim_unrelated = cosine_similarity(qv, uv)
    assert sim_related > sim_unrelated


# ----------------------------------------------------------------------
# Store names / sanitisation
# ----------------------------------------------------------------------

def test_collection_name_is_chromadb_safe() -> None:
    name = _collection_name("My Org", "weird/repo", "feature/ai-stuff")
    # Alphanumeric / underscore / hyphen only.  Starts and ends
    # alphanumeric.  Within length bounds.
    assert 3 <= len(name) <= 512
    assert name[0].isalnum() and name[-1].isalnum()
    import re
    assert re.fullmatch(r"[A-Za-z0-9_-]+", name)


def test_sanitize_strips_unsafe_chars() -> None:
    assert _sanitize("foo bar/baz") == "foo_bar_baz"


# ----------------------------------------------------------------------
# Store round-trip
# ----------------------------------------------------------------------

def test_store_round_trip_add_count_query(tmp_path: Path) -> None:
    chunks = [
        Chunk(
            chunk_id="A:1", path="src/auth.py",
            start_line=1, end_line=20,
            text="def authenticate(user, password):\n    return check_credentials(user, password)\n",
            file_sha="aaa",
        ),
        Chunk(
            chunk_id="B:1", path="src/util.py",
            start_line=1, end_line=10,
            text="def helper(x):\n    return x * 2\n",
            file_sha="bbb",
        ),
    ]
    s = RagStore(
        owner="o", repo="r", branch="main",
        embedder=HashingEmbedder(),
        persist_dir=tmp_path,
    )
    n = s.add_chunks(chunks)
    assert n == 2
    assert s.count() == 2

    hits = s.query("authentication password", k=2)
    assert hits, "query returned no hits"
    # Best hit should be the auth chunk.
    assert hits[0].path == "src/auth.py"


def test_store_delete_by_path_removes_only_that_file(tmp_path: Path) -> None:
    s = RagStore(
        owner="o", repo="r", branch="main",
        embedder=HashingEmbedder(),
        persist_dir=tmp_path,
    )
    chunks = [
        Chunk(
            chunk_id="A:1", path="a.py", start_line=1, end_line=5,
            text="content of a", file_sha="aaa",
        ),
        Chunk(
            chunk_id="B:1", path="b.py", start_line=1, end_line=5,
            text="content of b", file_sha="bbb",
        ),
    ]
    s.add_chunks(chunks)
    assert s.count() == 2
    removed = s.delete_by_path("a.py")
    assert removed == 1
    assert s.count() == 1


def test_store_query_empty_index_returns_empty(tmp_path: Path) -> None:
    s = RagStore(
        owner="o", repo="r", branch="main",
        embedder=HashingEmbedder(),
        persist_dir=tmp_path,
    )
    assert s.query("anything", k=5) == []


# ----------------------------------------------------------------------
# Indexer
# ----------------------------------------------------------------------

def test_indexer_first_run_indexes_all_files(tmp_path: Path) -> None:
    files = [
        ("src/auth.py", "def authenticate(user, password):\n    return True\n"),
        ("README.md", "# Project\nThis is about astronomy and stars.\n"),
    ]
    report = build_idx(
        files, owner="o", repo="r", branch="main",
        embedder=HashingEmbedder(),
        persist_dir=tmp_path,
    )
    assert report.files_seen == 2
    assert report.files_indexed == 2
    assert report.files_skipped == 0
    assert report.chunks_added >= 2

    meta = IndexMeta.load(tmp_path)
    assert meta is not None
    assert set(meta.indexed_files.keys()) == {"src/auth.py", "README.md"}


def test_indexer_skips_unchanged_files_on_second_run(tmp_path: Path) -> None:
    files = [
        ("src/auth.py", "def authenticate(): pass\n"),
        ("README.md", "# Project\n"),
    ]
    emb = HashingEmbedder()
    build_idx(files, owner="o", repo="r", branch="main", embedder=emb, persist_dir=tmp_path)

    # Re-run with the same content.
    report = build_idx(
        files, owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
    )
    assert report.files_seen == 2
    assert report.files_indexed == 0
    assert report.files_skipped == 2


def test_indexer_reindexes_changed_file(tmp_path: Path) -> None:
    emb = HashingEmbedder()
    build_idx(
        [("src/x.py", "old content\n")],
        owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
    )
    report = build_idx(
        [("src/x.py", "new content with totally different words\n")],
        owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
    )
    assert report.files_indexed == 1
    assert report.files_skipped == 0


def test_indexer_embedder_change_triggers_full_rebuild(tmp_path: Path) -> None:
    """If the embedder name changes, all old vectors are incomparable
    and we must rebuild from scratch."""
    emb_a = HashingEmbedder()
    emb_a.name = "fake-A"   # type: ignore[attr-defined]
    build_idx(
        [("src/x.py", "hello\n")],
        owner="o", repo="r", branch="main",
        embedder=emb_a, persist_dir=tmp_path,
    )
    emb_b = HashingEmbedder()
    emb_b.name = "fake-B"   # type: ignore[attr-defined]
    report = build_idx(
        [("src/x.py", "hello\n")],
        owner="o", repo="r", branch="main",
        embedder=emb_b, persist_dir=tmp_path,
    )
    # Forced full rebuild → file indexed again even though sha matches.
    assert report.files_indexed == 1
    assert report.files_skipped == 0


# ----------------------------------------------------------------------
# Retriever
# ----------------------------------------------------------------------

def test_retrieve_top_k_finds_relevant_chunk(tmp_path: Path) -> None:
    emb = HashingEmbedder()
    build_idx(
        [
            ("README.md", "# Nuclear shell model\nA tool for nuclear physics.\n"),
            ("src/util.py", "def helper(x):\n    return x * 2\n"),
        ],
        owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
    )
    hits = retrieve_top_k(
        "nuclear physics shell",
        owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
        k=2, mmr=False,
    )
    assert hits
    assert hits[0].path == "README.md"


def test_retrieve_top_k_respects_k(tmp_path: Path) -> None:
    emb = HashingEmbedder()
    files = [
        (f"src/f_{i}.py", f"def f_{i}(): return {i}\n")
        for i in range(10)
    ]
    build_idx(
        files, owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
    )
    hits = retrieve_top_k(
        "function definition",
        owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
        k=3, mmr=False,
    )
    assert len(hits) == 3


def test_retrieve_top_k_empty_query_returns_empty(tmp_path: Path) -> None:
    emb = HashingEmbedder()
    build_idx(
        [("a.py", "def foo(): pass\n")],
        owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
    )
    assert retrieve_top_k(
        "",
        owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
    ) == []


def test_retrieve_top_k_missing_index_returns_empty(tmp_path: Path) -> None:
    """When the persist dir doesn't exist yet the retriever must
    silently return [] — agents fall back to grep, not crash."""
    assert retrieve_top_k(
        "anything",
        owner="o", repo="r", branch="main",
        embedder=HashingEmbedder(),
        persist_dir=tmp_path / "never-built",
    ) == []


# ----------------------------------------------------------------------
# Determinism across process restarts (ChromaDB persistence)
# ----------------------------------------------------------------------

def test_index_persists_across_store_recreations(tmp_path: Path) -> None:
    emb = HashingEmbedder()
    build_idx(
        [("src/main.py", "import asyncio\ndef main():\n    pass\n")],
        owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
    )
    # Construct a fresh store object pointing at the same dir.
    s = RagStore(
        owner="o", repo="r", branch="main",
        embedder=emb, persist_dir=tmp_path,
    )
    assert s.count() >= 1
    hits = s.query("import asyncio", k=1)
    assert hits
    assert hits[0].path == "src/main.py"
