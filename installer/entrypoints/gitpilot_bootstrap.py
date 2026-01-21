"""
GitPilot bootstrap entrypoint for packaged builds.

- macOS/Linux: portable install -> store data in CURRENT FOLDER: ./GitPilotData
- Windows: binary installed to Program Files, so store data in %LOCALAPPDATA%\\GitPilot

It also loads an optional config file:
  <DATA_DIR>/config.env
which can contain environment variables (KEY=VALUE).

Then it starts the normal API server via gitpilot.cli:serve_only
(listens on http://127.0.0.1:8000 per current repo behavior).
"""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from pathlib import Path


def default_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "GitPilot"
    # portable on macOS/Linux: current folder
    return Path.cwd() / "GitPilotData"


def load_env_file(env_path: Path) -> None:
    """
    Minimal .env loader (KEY=VALUE lines).
    - ignores empty lines and comments
    - does not parse quotes/export
    """
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        os.environ.setdefault(k, v)


def ensure_dirs(data_dir: Path) -> None:
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)


def main() -> int:
    data_dir = Path(os.environ.get("GITPILOT_DATA_DIR") or default_data_dir())
    os.environ.setdefault("GITPILOT_DATA_DIR", str(data_dir))
    ensure_dirs(data_dir)

    # Load portable config if present
    load_env_file(data_dir / "config.env")

    # Optional: open browser automatically (default ON for packaged builds)
    open_browser = os.environ.get("GITPILOT_OPEN_BROWSER", "1").lower() not in {"0", "false", "no"}
    url = os.environ.get("GITPILOT_URL", "http://127.0.0.1:8000")

    # Start server with optional browser open
    if open_browser:
        # small delay so server has time to start
        def _open():
            time.sleep(1.5)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        # fire-and-forget: open browser after delay
        import threading
        threading.Thread(target=_open, daemon=True).start()

    from gitpilot.cli import serve_only

    serve_only()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
