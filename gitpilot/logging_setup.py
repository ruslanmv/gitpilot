"""Make GitPilot's own log lines actually appear.

``uvicorn.run(log_level="info")`` reads as "log at info" and configures only
uvicorn's three loggers. Everything GitPilot logs propagates to the root
logger, which has no handler — so Python falls back to ``logging.lastResort``,
whose level is WARNING. The result is a server log that shows

    INFO:     127.0.0.1:59698 - "GET /api/health HTTP/1.1" 200 OK
    WARNING:gitpilot._api_app:[HTTP] 🐢 POST /api/chat/send took 33.52s

and not one ``INFO`` line from GitPilot itself: every route decision, every
provider call, every timing was being written and thrown away. The uvicorn
lines being present is what makes it look like logging works.

This attaches a handler to the ``gitpilot`` namespace so those lines reach
the console, at a level the operator chooses. It is deliberately not
``basicConfig``: that touches the root logger, and taking over logging for
every library in the process is not this package's business — GitPilot is
importable as a library, not only run as a server.
"""

from __future__ import annotations

import logging
import os
import sys

#: The env var, so a `uvicorn --reload` child configures itself the same way
#: the parent did. The reloader re-imports the app in a fresh process, where
#: nothing the CLI did in memory survives.
LEVEL_ENV_VAR = "GITPILOT_LOG_LEVEL"

DEFAULT_LEVEL = "INFO"

#: Marks our handler so repeated calls replace rather than stack. Without it,
#: a re-import doubles every line.
_HANDLER_NAME = "gitpilot-console"

_VALID = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def resolve_level(level: str | int | None = None) -> int:
    """Turn a name, a number, or nothing into a logging level.

    Precedence: the explicit argument, then the environment, then INFO.
    An unrecognised name falls back rather than raising — a typo in a log
    level should not stop the server from starting.
    """
    raw = level if level is not None else os.getenv(LEVEL_ENV_VAR) or DEFAULT_LEVEL
    if isinstance(raw, int):
        return raw
    name = str(raw).strip().upper()
    if name not in _VALID:
        return logging.INFO
    return getattr(logging, name)


def configure(level: str | int | None = None, *, stream=None) -> int:
    """Route the ``gitpilot`` logger to the console. Returns the level set.

    Idempotent: calling it again re-levels the existing handler instead of
    adding a second one.
    """
    resolved = resolve_level(level)

    pkg_logger = logging.getLogger("gitpilot")
    pkg_logger.setLevel(resolved)

    for existing in pkg_logger.handlers:
        if getattr(existing, "name", None) == _HANDLER_NAME:
            existing.setLevel(resolved)
            return resolved

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.name = _HANDLER_NAME
    handler.setLevel(resolved)
    # Uvicorn's format for the same stream, so the two interleave readably.
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    pkg_logger.addHandler(handler)

    # The root logger may gain a handler later (uvicorn, pytest, a host app).
    # Propagating as well would print every line twice.
    pkg_logger.propagate = False

    return resolved


def configure_from_env() -> int:
    """Configure at import time, for processes the CLI did not start."""
    return configure()
