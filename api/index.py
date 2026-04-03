"""
Vercel serverless function entrypoint for GitPilot.

This module exports the FastAPI app instance for Vercel's Python runtime.
All API routes are defined in gitpilot.api module.
"""

from gitpilot.api import app

# Vercel will use this `app` instance to handle requests
__all__ = ["app"]
