"""
Build Windows installer using Inno Setup.

Compiles the .iss file using ISCC.exe to create gitpilot-setup.exe
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ISS_FILE = REPO_ROOT / "installer" / "windows" / "gitpilot.iss"


def find_iscc() -> Path:
    """Find ISCC.exe (Inno Setup Compiler)."""
    # Typical installation paths
    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 5\ISCC.exe"),
    ]

    for c in candidates:
        if c.exists():
            return c

    # Try PATH
    import shutil
    iscc_path = shutil.which("ISCC")
    if iscc_path:
        return Path(iscc_path)

    raise SystemExit(
        "ISCC.exe not found. Install Inno Setup first.\n"
        "Download from: https://jrsoftware.org/isdownload.php"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Build Windows installer with Inno Setup")
    ap.add_argument("--version", required=True, help="Version string for installer")
    args = ap.parse_args()

    if not sys.platform.startswith("win"):
        raise SystemExit("This script is Windows-only.")

    if not ISS_FILE.exists():
        raise SystemExit(f"ISS file not found: {ISS_FILE}")

    iscc = find_iscc()
    version = args.version.replace("/", "_").replace("\\", "_")

    print(f"Building Windows installer...")
    print(f"  ISCC: {iscc}")
    print(f"  ISS: {ISS_FILE}")
    print(f"  Version: {version}")

    cmd = [str(iscc), f"/DAppVersion={version}", str(ISS_FILE)]
    print(f"Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise SystemExit(f"Inno Setup failed with code {result.returncode}")

    out = ISS_FILE.parent / "gitpilot-setup.exe"
    if not out.exists():
        raise SystemExit(f"Expected output not found: {out}")

    print(f"\nWindows installer built successfully: {out}")


if __name__ == "__main__":
    main()
