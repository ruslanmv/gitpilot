"""GitPilot HTTP API package.

This package preserves the historical ``gitpilot.api`` module surface: the
main FastAPI application and every public symbol previously importable from
``gitpilot.api`` now live in :mod:`gitpilot._api_app` and are re-exported here
verbatim. This keeps ``from gitpilot.api import app`` (and every other prior
import) working unchanged while allowing additive submodules such as
:mod:`gitpilot.api.repair_server` to live alongside the main app.
"""

from __future__ import annotations

# Re-export the entire historical module surface. ``_api_app`` is the original
# ``gitpilot/api.py`` content moved verbatim (relative imports unchanged), so
# every symbol (``app``, ``api_list_branches``, ``EnvironmentConfig``, …) and
# submodule attribute remains importable as ``gitpilot.api.<name>``.
from gitpilot._api_app import *  # noqa: F401,F403
from gitpilot import _api_app as _api_app

# ``import *`` only pulls names listed in ``__all__`` (if defined) or the
# module's public names. To guarantee 100% parity with the old flat module,
# copy across every public attribute that ``import *`` may have skipped.
for _name in dir(_api_app):
    if _name.startswith("__"):
        continue
    globals().setdefault(_name, getattr(_api_app, _name))
del _name
