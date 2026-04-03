# gitpilot/context_vault.py
"""Context Vault — upload, extract, index, and retrieve project context assets.

Non-destructive, additive feature. Stores everything under:
    ~/.gitpilot/workspaces/{owner}/{repo}/.gitpilot/context/

Directory layout:
    context/
        assets/           raw uploaded files
        extracted/        extracted text + metadata JSON
        index/            SQLite metadata + chunk index
        use_cases/        structured use-case JSON + markdown exports

This module handles asset lifecycle (upload, extract, index, delete)
and chunk retrieval for context-pack injection into agent prompts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB default
MAX_EXTRACT_CHARS = 500_000
CHUNK_SIZE = 800  # chars per chunk (approx)
CHUNK_OVERLAP = 100
MAX_RETRIEVAL_CHUNKS = 8
MAX_RETRIEVAL_CHARS = 6_000


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class AssetMeta:
    asset_id: str
    filename: str
    mime: str
    size_bytes: int
    created_at: str
    extracted_chars: int = 0
    indexed_chunks: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtractedAsset:
    asset_id: str
    filename: str
    mime: str
    extracted_text: str
    pages: Optional[int] = None
    created_at: str = ""
    notes: str = ""


@dataclass
class ChunkResult:
    asset_id: str
    filename: str
    chunk_index: int
    text: str
    score: float = 0.0


# ---------------------------------------------------------------------------
# Vault manager
# ---------------------------------------------------------------------------
class ContextVault:
    """Manages per-repo context vault under .gitpilot/context/."""

    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.vault_dir = workspace_path / ".gitpilot" / "context"
        self.assets_dir = self.vault_dir / "assets"
        self.extracted_dir = self.vault_dir / "extracted"
        self.index_dir = self.vault_dir / "index"
        self.use_cases_dir = self.vault_dir / "use_cases"

    # ------------------------------------------------------------------
    # Init & safety
    # ------------------------------------------------------------------
    def _ensure_dirs(self):
        for d in (self.assets_dir, self.extracted_dir, self.index_dir, self.use_cases_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _safe_resolve(self, base: Path, name: str) -> Path:
        """Prevent path traversal attacks."""
        full = (base / name).resolve()
        if not str(full).startswith(str(base.resolve())):
            raise PermissionError(f"Path traversal blocked: {name}")
        return full

    # ------------------------------------------------------------------
    # Asset CRUD
    # ------------------------------------------------------------------
    def list_assets(self) -> List[AssetMeta]:
        """Return metadata for all uploaded assets."""
        self._ensure_dirs()
        results: List[AssetMeta] = []
        for ext_file in sorted(self.extracted_dir.glob("*.json")):
            try:
                data = json.loads(ext_file.read_text(encoding="utf-8"))
                results.append(AssetMeta(
                    asset_id=data.get("asset_id", ext_file.stem),
                    filename=data.get("filename", ""),
                    mime=data.get("mime", ""),
                    size_bytes=data.get("size_bytes", 0),
                    created_at=data.get("created_at", ""),
                    extracted_chars=len(data.get("extracted_text", "")),
                    indexed_chunks=data.get("indexed_chunks", 0),
                    notes=data.get("notes", ""),
                ))
            except Exception:
                logger.warning("Skipping corrupt metadata: %s", ext_file)
        return results

    def upload_asset(self, filename: str, content: bytes, mime: str = "") -> AssetMeta:
        """Store a raw asset and run extraction + indexing."""
        self._ensure_dirs()

        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"File too large ({len(content)} bytes). Max is {MAX_UPLOAD_BYTES}."
            )

        asset_id = uuid.uuid4().hex[:12]
        safe_name = re.sub(r"[^\w.\-]", "_", filename)
        stored_name = f"{asset_id}_{safe_name}"

        asset_path = self._safe_resolve(self.assets_dir, stored_name)
        asset_path.write_bytes(content)

        if not mime:
            mime = _guess_mime(filename)

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Extract text
        extracted_text = _extract_text(asset_path, mime)

        # Chunk + index
        chunks = _chunk_text(extracted_text, CHUNK_SIZE, CHUNK_OVERLAP)
        indexed_count = self._index_chunks(asset_id, filename, chunks)

        # Save extracted metadata
        meta_data = {
            "asset_id": asset_id,
            "filename": filename,
            "stored_name": stored_name,
            "mime": mime,
            "size_bytes": len(content),
            "extracted_text": extracted_text[:MAX_EXTRACT_CHARS],
            "pages": None,
            "created_at": now,
            "indexed_chunks": indexed_count,
            "notes": "",
        }
        meta_path = self._safe_resolve(self.extracted_dir, f"{asset_id}.json")
        meta_path.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")

        return AssetMeta(
            asset_id=asset_id,
            filename=filename,
            mime=mime,
            size_bytes=len(content),
            created_at=now,
            extracted_chars=len(extracted_text),
            indexed_chunks=indexed_count,
        )

    def delete_asset(self, asset_id: str) -> bool:
        """Remove asset, extracted data, and index entries."""
        self._ensure_dirs()

        # Remove extracted metadata
        meta_path = self.extracted_dir / f"{asset_id}.json"
        stored_name = None
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                stored_name = data.get("stored_name")
            except Exception:
                pass
            meta_path.unlink()

        # Remove raw asset
        if stored_name:
            asset_path = self.assets_dir / stored_name
            if asset_path.exists():
                asset_path.unlink()
        else:
            # fallback: find by prefix
            for f in self.assets_dir.iterdir():
                if f.name.startswith(asset_id):
                    f.unlink()
                    break

        # Remove from index
        self._remove_from_index(asset_id)

        return True

    def get_asset_path(self, asset_id: str) -> Optional[Path]:
        """Return the raw asset path for download."""
        self._ensure_dirs()
        meta_path = self.extracted_dir / f"{asset_id}.json"
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                stored_name = data.get("stored_name", "")
                if stored_name:
                    p = self.assets_dir / stored_name
                    if p.exists():
                        return p
            except Exception:
                pass

        # fallback
        for f in self.assets_dir.iterdir():
            if f.name.startswith(asset_id):
                return f
        return None

    def get_asset_filename(self, asset_id: str) -> str:
        """Return original filename for an asset."""
        meta_path = self.extracted_dir / f"{asset_id}.json"
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                return data.get("filename", "unknown")
            except Exception:
                pass
        return "unknown"

    # ------------------------------------------------------------------
    # Indexing (SQLite-backed)
    # ------------------------------------------------------------------
    def _get_db(self) -> sqlite3.Connection:
        self._ensure_dirs()
        db_path = self.index_dir / "context.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_asset ON chunks(asset_id)
        """)
        conn.commit()
        return conn

    def _index_chunks(self, asset_id: str, filename: str, chunks: List[str]) -> int:
        conn = self._get_db()
        try:
            # Remove old entries for this asset (re-index)
            conn.execute("DELETE FROM chunks WHERE asset_id = ?", (asset_id,))
            for i, chunk_text in enumerate(chunks):
                conn.execute(
                    "INSERT INTO chunks (asset_id, filename, chunk_index, text) VALUES (?, ?, ?, ?)",
                    (asset_id, filename, i, chunk_text),
                )
            conn.commit()
            return len(chunks)
        finally:
            conn.close()

    def _remove_from_index(self, asset_id: str):
        try:
            conn = self._get_db()
            conn.execute("DELETE FROM chunks WHERE asset_id = ?", (asset_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    def search_chunks(
        self,
        query: str,
        max_chunks: int = MAX_RETRIEVAL_CHUNKS,
        max_chars: int = MAX_RETRIEVAL_CHARS,
    ) -> List[ChunkResult]:
        """Simple keyword-based retrieval (BM25-like scoring).

        Phase 1: naive keyword matching. Phase 2 can add embeddings.
        """
        if not query.strip():
            return []

        keywords = _extract_keywords(query)
        if not keywords:
            return []

        try:
            conn = self._get_db()
        except Exception:
            return []

        try:
            rows = conn.execute(
                "SELECT asset_id, filename, chunk_index, text FROM chunks"
            ).fetchall()
        finally:
            conn.close()

        scored: List[ChunkResult] = []
        for asset_id, filename, chunk_index, text in rows:
            text_lower = text.lower()
            score = 0.0
            for kw in keywords:
                count = text_lower.count(kw.lower())
                if count > 0:
                    # simple TF score
                    score += count * (1.0 + len(kw) * 0.1)
            if score > 0:
                scored.append(ChunkResult(
                    asset_id=asset_id,
                    filename=filename,
                    chunk_index=chunk_index,
                    text=text,
                    score=score,
                ))

        scored.sort(key=lambda c: c.score, reverse=True)

        # Enforce limits
        results: List[ChunkResult] = []
        total_chars = 0
        for chunk in scored[:max_chunks * 2]:  # over-fetch then trim
            if len(results) >= max_chunks:
                break
            if total_chars + len(chunk.text) > max_chars:
                break
            results.append(chunk)
            total_chars += len(chunk.text)

        return results


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------
def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    mime_map = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".csv": "text/csv",
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".vtt": "text/vtt",
        ".srt": "text/srt",
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".ts": "text/typescript",
        ".jsx": "text/jsx",
        ".tsx": "text/tsx",
        ".html": "text/html",
        ".css": "text/css",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".toml": "text/toml",
        ".xml": "text/xml",
        ".sh": "text/x-shellscript",
        ".go": "text/x-go",
        ".rs": "text/x-rust",
        ".java": "text/x-java",
        ".rb": "text/x-ruby",
    }
    return mime_map.get(ext, "application/octet-stream")


def _extract_text(path: Path, mime: str) -> str:
    """Best-effort text extraction from a file."""
    # Text-based files
    if mime.startswith("text/") or mime in (
        "application/json",
        "text/markdown",
        "text/csv",
        "text/vtt",
        "text/srt",
        "text/toml",
        "text/yaml",
    ):
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:MAX_EXTRACT_CHARS]
        except Exception:
            return ""

    # PDF
    if mime == "application/pdf":
        return _extract_pdf(path)

    # DOCX
    if "wordprocessingml" in mime or mime == "application/msword":
        return _extract_docx(path)

    # Binary/media — no extraction, store only
    return ""


def _extract_pdf(path: Path) -> str:
    """Extract text from PDF. Tries pypdf/PyPDF2 first, falls back gracefully."""
    try:
        import pypdf
        try:
            reader = pypdf.PdfReader(str(path))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)[:MAX_EXTRACT_CHARS]
        except Exception as e:
            logger.warning("PDF extraction failed with pypdf: %s", e)
            return ""
    except ImportError:
        pass

    try:
        import PyPDF2  # noqa: N813
        try:
            reader = PyPDF2.PdfReader(str(path))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)[:MAX_EXTRACT_CHARS]
        except Exception as e:
            logger.warning("PDF extraction failed with PyPDF2: %s", e)
            return ""
    except ImportError:
        pass

    logger.info("PDF extraction unavailable (install pypdf or PyPDF2). Storing PDF without text.")
    return ""

def _extract_docx(path: Path) -> str:
    """Extract text from DOCX."""
    try:
        import docx
        doc = docx.Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)[:MAX_EXTRACT_CHARS]
    except ImportError:
        logger.info("DOCX extraction unavailable (install python-docx). Storing without text.")
        return ""
    except Exception as e:
        logger.warning("DOCX extraction failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks."""
    if not text:
        return []

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap
        if start >= len(text):
            break

    return chunks


def _extract_keywords(query: str) -> List[str]:
    """Extract meaningful keywords from a query string."""
    # Remove common stop words
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "above",
        "below", "between", "out", "off", "over", "under", "again",
        "further", "then", "once", "here", "there", "when", "where", "why",
        "how", "all", "both", "each", "few", "more", "most", "other",
        "some", "such", "no", "nor", "not", "only", "own", "same", "so",
        "than", "too", "very", "just", "because", "but", "and", "or", "if",
        "while", "what", "which", "who", "whom", "this", "that", "these",
        "those", "i", "me", "my", "we", "our", "you", "your", "he", "him",
        "she", "her", "it", "its", "they", "them", "their",
    }

    words = re.findall(r"\w+", query.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 1]
    return keywords
