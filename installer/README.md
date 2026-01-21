# GitPilot Installer Toolkit (All-in-One Local)

This folder packages GitPilot as a **local web service**:
- build frontend (Vite) -> embed into backend (`gitpilot/web`)
- freeze backend + embedded UI into a single binary using PyInstaller
- ship:
  - Windows: Inno Setup installer (Program Files + Add/Remove Programs)
  - macOS/Linux: portable archive (zip/tar.gz), no root needed

## Quick Start (local)

```bash
make installer-build
make installer-verify
make installer-package
```

## Windows installer (local build)

Install Inno Setup, then:

```bash
make installer-windows
```

## Build Pipeline

### Step A - Build frontend
- run `npm ci`
- run `npm run build`
- output -> `frontend/dist/`

### Step B - Embed frontend into backend
Copy the Vite output into:
- `gitpilot/web/`
  - `index.html`
  - `/assets/*`
  - any other dist files

This matches your backend's `STATIC_DIR` logic in `gitpilot/api.py`.

### Step C - Freeze backend into executable
Use PyInstaller to build a binary for each OS:
- includes Python code
- includes `gitpilot/web/**` (embedded frontend)
- includes `assets/` if used at runtime

### Step D - Package distribution
- **Windows:** Inno Setup builds a real installer `.exe`
- **macOS:** portable `.zip` (no root)
- **Linux:** portable `.tar.gz` (no root)

## Notes

- Backend serves UI from `gitpilot/web` (see `gitpilot/api.py`).
- Portable macOS/Linux runs from any folder, storing data in `./GitPilotData` next to where you run it.
- Windows stores data in `%LOCALAPPDATA%\GitPilot`.

## Directory Structure

```
installer/
├── README.md
├── build/
│   ├── embed_frontend.py
│   ├── verify_bundle.py
│   └── package_portable.py
├── entrypoints/
│   └── gitpilot_bootstrap.py
├── pyinstaller/
│   └── gitpilot.spec
├── windows/
│   ├── gitpilot.iss
│   └── build_installer.py
├── macos/
│   └── portable_zip.sh
└── linux/
    └── portable_tar.sh
```

## Environment Variables

- `GITPILOT_DATA_DIR`: Override data directory location
- `GITPILOT_OPEN_BROWSER`: Set to "0" to disable auto browser open
- `GITPILOT_URL`: Override the URL to open (default: http://127.0.0.1:8000)
