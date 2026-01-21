# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for GitPilot.

Build with:
    pyinstaller installer/pyinstaller/gitpilot.spec

This bundles:
- Python backend (gitpilot package)
- Embedded UI folder (gitpilot/web/**)
- Required data files
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files
import os
from pathlib import Path

block_cipher = None

# Compute repo root from spec file location
SPEC_DIR = Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parent.parent  # installer/pyinstaller -> installer -> repo root

# Collect all submodules from gitpilot
hiddenimports = collect_submodules("gitpilot")

# Add uvicorn and fastapi dependencies
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "email.mime.multipart",
    "email.mime.text",
    "httptools",
    "websockets",
    "watchfiles",
    "anyio",
    "h11",
    "httpx",
    "httpcore",
    "starlette",
    "pydantic",
    "typer",
    "rich",
]

# Collect data files from packages that need them
crewai_datas = collect_data_files("crewai", include_py_files=True)
certifi_datas = collect_data_files("certifi")
tzdata_datas = collect_data_files("tzdata")
jsonschema_datas = collect_data_files("jsonschema_specifications")

# Data files to include (using absolute paths)
datas = [
    # Embed the built frontend (served by gitpilot/api.py from gitpilot/web)
    (str(REPO_ROOT / "gitpilot" / "web"), "gitpilot/web"),
]

# Add collected package data files
datas += crewai_datas
datas += certifi_datas
datas += tzdata_datas
datas += jsonschema_datas

# Ensure crewai utilities directory exists for relative path resolution
# The i18n module uses __file__/../translations/en.json
import site
site_packages = site.getsitepackages()
if hasattr(site, 'getusersitepackages'):
    try:
        site_packages.append(site.getusersitepackages())
    except Exception:
        pass
for sp in site_packages:
    crewai_utils = Path(sp) / "crewai" / "utilities"
    if crewai_utils.exists():
        # Add the utilities folder (including __init__.py for path resolution)
        datas.append((str(crewai_utils), "crewai/utilities"))
        break

# Add optional files if they exist
optional_files = [
    ("README.md", "."),
    (".env.example", "."),
    ("LICENSE", "."),
]

for src, dst in optional_files:
    src_path = REPO_ROOT / src
    if src_path.exists():
        datas.append((str(src_path), dst))

a = Analysis(
    [str(REPO_ROOT / "installer" / "entrypoints" / "gitpilot_bootstrap.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test packages
        "pytest",
        "hypothesis",
        # Exclude dev tools
        "ruff",
        "black",
        "mypy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gitpilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Console app (shows server logs)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="gitpilot",
)
