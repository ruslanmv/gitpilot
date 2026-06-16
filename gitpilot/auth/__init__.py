"""GitPilot accounts — account‑first identity (email/password + verification).

See ``gitpilot/auth/router.py`` for the API and ``docs/auth.md`` for the design.
"""

from .router import build_account_router, current_account

__all__ = ["build_account_router", "current_account"]
