"""
Create portable archives for distribution.

Creates archives under `dist_artifacts/`:
- Windows: .zip
- macOS: .zip
- Linux: .tar.gz
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_NAME = os.environ.get("APP_NAME", "gitpilot")


def make_tar_gz(src_dir: Path, out_file: Path) -> None:
    import tarfile

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_file, "w:gz") as tf:
        for item in src_dir.iterdir():
            tf.add(item, arcname=item.name)
    print(f"Created: {out_file}")


def make_zip(src_dir: Path, out_file: Path) -> None:
    import zipfile

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_file, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in src_dir.rglob("*"):
            zf.write(p, p.relative_to(src_dir))
    print(f"Created: {out_file}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Create portable archive for current OS")
    ap.add_argument("--version", required=True, help="Version string for archive name")
    args = ap.parse_args()

    dist_dir = REPO_ROOT / "dist" / APP_NAME
    if not dist_dir.exists():
        raise SystemExit(f"dist folder not found: {dist_dir}")

    artifacts = REPO_ROOT / "dist_artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    # Sanitize version for filename
    version = args.version.replace("/", "_").replace("\\", "_")

    if sys.platform.startswith("win"):
        out = artifacts / f"{APP_NAME}-{version}-windows.zip"
        make_zip(dist_dir, out)
    elif sys.platform == "darwin":
        out = artifacts / f"{APP_NAME}-{version}-macos.zip"
        make_zip(dist_dir, out)
    else:
        out = artifacts / f"{APP_NAME}-{version}-linux.tar.gz"
        make_tar_gz(dist_dir, out)

    print(f"\nPortable archive created: {out}")


if __name__ == "__main__":
    main()
