"""How the built web app is served: caching, root files, and the SPA fallback.

* ``index.html`` names the content-hashed bundle of one build, so it is always
  revalidated (``no-cache``). Without that, browsers cached it heuristically and
  kept showing a previous build after a deploy — the Hugging Face Space page
  showed an old, unstyled sign-in screen because that build's CSS was gone.
* ``/assets/*`` files are content-hashed by Vite and never change: cached for a
  year, ``immutable``.
* A request for an asset that does not exist comes from a page of an earlier
  build still sitting in the browser's cache. The 404 carries
  ``Clear-Site-Data: "cache"`` so that browser drops its cached copies of this
  origin (cookies and storage are untouched) and the next load is the current
  build — stale clients fix themselves instead of staying broken.
* Unknown paths that look like files (``/.env``, ``/.git/config``,
  ``/wp-login.php``, ``/file=../.env``) get a plain 404 instead of the app page:
  answering 200 tells scanners "something is here" and keeps them hammering.
  Real files at the root of the build (``favicon.svg``, ``robots.txt``…) are
  served as files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

INDEX_HTML_HEADERS = {"Cache-Control": "no-cache"}
IMMUTABLE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}
ROOT_FILE_HEADERS = {"Cache-Control": "public, max-age=3600"}
#: Sent with a missing-asset 404: makes a stale page's browser drop its cache.
STALE_BUILD_HEADERS = {"Cache-Control": "no-store", "Clear-Site-Data": '"cache"'}


class BuildAssets(StaticFiles):
    """``StaticFiles`` for Vite's content-hashed ``/assets`` directory."""

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return JSONResponse({"detail": "Not Found"}, status_code=404, headers=STALE_BUILD_HEADERS)
        if response.status_code == 404:
            return JSONResponse({"detail": "Not Found"}, status_code=404, headers=STALE_BUILD_HEADERS)
        if response.status_code in (200, 304):
            response.headers.update(IMMUTABLE_HEADERS)
        return response


def _not_found() -> JSONResponse:
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def _root_file(static_dir: Path, name: str) -> Optional[Path]:
    """A real file directly inside the build directory (no traversal)."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    try:
        root = static_dir.resolve()
        candidate = (root / name).resolve()
    except OSError:
        return None
    if candidate.parent != root or not candidate.is_file() or candidate.name == "index.html":
        return None
    return candidate


def spa_response(full_path: str, static_dir: Path) -> Response:
    """The response for a path no other route claimed."""
    path = full_path.strip("/")
    if path == "api" or path.startswith("api/"):
        return _not_found()
    segments = [s for s in path.split("/") if s]
    # Dotfiles and dot-directories (.env, .git/, .streamlit/…) and traversal.
    if any(s.startswith(".") or ".." in s for s in segments):
        return _not_found()
    if len(segments) == 1:
        found = _root_file(static_dir, segments[0])
        if found is not None:
            return FileResponse(found, headers=ROOT_FILE_HEADERS)
    # A path that names a file which is not there is not an app route: the
    # web app's routes (/, /auth, /auth/...) never contain a file extension.
    if segments and "." in segments[-1]:
        return _not_found()
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file, headers=INDEX_HTML_HEADERS)
    return JSONResponse(
        {"message": "GitPilot UI not built. The static files directory is missing."},
        status_code=500,
    )


__all__ = ["BuildAssets", "INDEX_HTML_HEADERS", "spa_response"]
