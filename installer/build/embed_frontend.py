"""
Embed frontend build output into backend's web directory.

Build result `frontend/dist/` gets copied into `gitpilot/web/` so backend serves the UI.
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
TARGET_WEB = REPO_ROOT / "gitpilot" / "web"


def main() -> None:
    if not FRONTEND_DIST.exists():
        raise SystemExit(
            f"Frontend dist not found: {FRONTEND_DIST}. Run `npm run build` in frontend/ first."
        )

    # Replace embedded web directory contents
    TARGET_WEB.mkdir(parents=True, exist_ok=True)

    # Clean target but keep folder
    for child in TARGET_WEB.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    # Copy dist -> gitpilot/web
    for item in FRONTEND_DIST.iterdir():
        dst = TARGET_WEB / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)

    print(f"Embedded frontend: {FRONTEND_DIST} -> {TARGET_WEB}")


if __name__ == "__main__":
    main()
