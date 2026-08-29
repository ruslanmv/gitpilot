# gitpilot/agent/dialects/__init__.py
"""Three renderings of one loop — Batches V4-B3/B4/B5.

Each adapter speaks to a different class of model but consumes the same registry,
the same context and the same policy, so a feature that only works in one dialect
is an incomplete feature.
"""
from __future__ import annotations

from .lite import LiteAdapter
from .native import NativeAdapter
from .react_text import ReactTextAdapter

__all__ = ["LiteAdapter", "NativeAdapter", "ReactTextAdapter"]
