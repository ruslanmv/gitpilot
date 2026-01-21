"""
Verify that the built binary exists and the embedded UI is present.

Checks:
1. Embedded UI exists in gitpilot/web/
2. PyInstaller binary was built
3. Binary can be invoked (best-effort)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_NAME = os.environ.get("APP_NAME", "gitpilot")

DIST_DIR = REPO_ROOT / "dist" / APP_NAME
EMBED_WEB = REPO_ROOT / "gitpilot" / "web"


def main() -> None:
    errors = []

    # 1) Verify embedded UI exists
    index_html = EMBED_WEB / "index.html"
    if not index_html.exists():
        errors.append(f"Missing embedded UI: {index_html} (run frontend build + embed)")
    else:
        print(f"OK: embedded UI exists -> {index_html}")

    # Check for assets directory (Vite typically creates this)
    assets_dir = EMBED_WEB / "assets"
    if assets_dir.exists():
        print(f"OK: assets directory exists -> {assets_dir}")
    else:
        print(f"NOTE: no assets directory (may be normal for simple builds)")

    # 2) Verify PyInstaller output
    if sys.platform.startswith("win"):
        bin_path = DIST_DIR / f"{APP_NAME}.exe"
    else:
        bin_path = DIST_DIR / APP_NAME

    if not bin_path.exists():
        errors.append(f"Missing built binary: {bin_path}")
    else:
        print(f"OK: binary exists -> {bin_path}")

    # 3) Best-effort invoke help
    if bin_path.exists():
        try:
            result = subprocess.run(
                [str(bin_path), "--help"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            if result.returncode == 0:
                print("OK: binary invoked with --help successfully.")
            else:
                print(f"NOTE: binary --help returned code {result.returncode}")
        except subprocess.TimeoutExpired:
            print("NOTE: binary --help timed out (may need different args)")
        except Exception as e:
            print(f"NOTE: could not run binary --help: {e}")

    if errors:
        print("\nVerification FAILED:")
        for err in errors:
            print(f"  - {err}")
        raise SystemExit(1)

    print("\nVerification PASSED!")


if __name__ == "__main__":
    main()
