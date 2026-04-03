"""Tests for gitpilot.context_vault module."""
from __future__ import annotations

import json

import pytest

from gitpilot.context_vault import (
    ContextVault,
    AssetMeta,
    MAX_UPLOAD_BYTES,
    _chunk_text,
    _extract_keywords,
    _guess_mime,
)


# ---------------------------------------------------------------------------
# Mime guessing
# ---------------------------------------------------------------------------

class TestGuessMime:
    def test_known_extensions(self):
        assert _guess_mime("report.pdf") == "application/pdf"
        assert _guess_mime("notes.txt") == "text/plain"
        assert _guess_mime("data.csv") == "text/csv"
        assert _guess_mime("readme.md") == "text/markdown"
        assert _guess_mime("app.py") == "text/x-python"
        assert _guess_mime("main.js") == "text/javascript"

    def test_unknown_extension(self):
        assert _guess_mime("file.xyz123") == "application/octet-stream"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

class TestChunking:
    def test_empty_text(self):
        assert _chunk_text("") == []

    def test_short_text(self):
        result = _chunk_text("hello world", chunk_size=100, overlap=10)
        assert len(result) == 1
        assert result[0] == "hello world"

    def test_overlapping_chunks(self):
        text = "A" * 200
        chunks = _chunk_text(text, chunk_size=100, overlap=20)
        assert len(chunks) >= 2
        # Each chunk should be at most 100 chars
        for c in chunks:
            assert len(c) <= 100


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

class TestKeywords:
    def test_extracts_meaningful_words(self):
        kws = _extract_keywords("How to implement authentication in Python")
        assert "implement" in kws
        assert "authentication" in kws
        assert "python" in kws
        assert "how" not in kws  # stop word
        assert "to" not in kws  # stop word

    def test_empty_query(self):
        assert _extract_keywords("") == []

    def test_only_stop_words(self):
        assert _extract_keywords("the a an is are") == []


# ---------------------------------------------------------------------------
# Vault CRUD
# ---------------------------------------------------------------------------

class TestVaultCRUD:
    def test_list_empty(self, tmp_path):
        vault = ContextVault(tmp_path)
        assert vault.list_assets() == []

    def test_upload_and_list(self, tmp_path):
        vault = ContextVault(tmp_path)
        meta = vault.upload_asset("notes.txt", b"Hello world", mime="text/plain")
        assert isinstance(meta, AssetMeta)
        assert meta.filename == "notes.txt"
        assert meta.mime == "text/plain"
        assert meta.size_bytes == 11
        assert meta.extracted_chars > 0

        assets = vault.list_assets()
        assert len(assets) == 1
        assert assets[0].asset_id == meta.asset_id

    def test_upload_too_large(self, tmp_path):
        vault = ContextVault(tmp_path)
        with pytest.raises(ValueError, match="too large"):
            vault.upload_asset("big.bin", b"x" * (MAX_UPLOAD_BYTES + 1))

    def test_delete_asset(self, tmp_path):
        vault = ContextVault(tmp_path)
        meta = vault.upload_asset("temp.txt", b"temporary", mime="text/plain")
        assert len(vault.list_assets()) == 1
        vault.delete_asset(meta.asset_id)
        assert len(vault.list_assets()) == 0

    def test_get_asset_path(self, tmp_path):
        vault = ContextVault(tmp_path)
        meta = vault.upload_asset("doc.md", b"# Title\n\nContent", mime="text/markdown")
        path = vault.get_asset_path(meta.asset_id)
        assert path is not None
        assert path.exists()
        assert path.read_bytes() == b"# Title\n\nContent"

    def test_get_asset_path_not_found(self, tmp_path):
        vault = ContextVault(tmp_path)
        assert vault.get_asset_path("nonexistent") is None

    def test_get_asset_filename(self, tmp_path):
        vault = ContextVault(tmp_path)
        meta = vault.upload_asset("report.pdf", b"fake-pdf", mime="application/pdf")
        assert vault.get_asset_filename(meta.asset_id) == "report.pdf"


# ---------------------------------------------------------------------------
# Search / retrieval
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_empty_vault(self, tmp_path):
        vault = ContextVault(tmp_path)
        assert vault.search_chunks("hello") == []

    def test_search_finds_content(self, tmp_path):
        vault = ContextVault(tmp_path)
        vault.upload_asset(
            "meeting.txt",
            b"We discussed authentication and database migration strategies.",
            mime="text/plain",
        )
        results = vault.search_chunks("authentication")
        assert len(results) >= 1
        assert "authentication" in results[0].text.lower()

    def test_search_empty_query(self, tmp_path):
        vault = ContextVault(tmp_path)
        vault.upload_asset("notes.txt", b"some content", mime="text/plain")
        assert vault.search_chunks("") == []

    def test_search_no_match(self, tmp_path):
        vault = ContextVault(tmp_path)
        vault.upload_asset("notes.txt", b"hello world", mime="text/plain")
        assert vault.search_chunks("xyznonexistent") == []


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

class TestPathSafety:
    def test_safe_resolve_blocks_traversal(self, tmp_path):
        vault = ContextVault(tmp_path)
        vault._ensure_dirs()
        with pytest.raises(PermissionError, match="traversal"):
            vault._safe_resolve(vault.assets_dir, "../../etc/passwd")
