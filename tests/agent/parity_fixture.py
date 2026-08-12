"""A shared fixture repository for agent tests — Batch V4-C3.

Reuses the Phase A parity fixture so a loop test runs against a real git
checkout with real files, rather than a mocked filesystem the tools would not
behave the same against.
"""
from __future__ import annotations

import pytest

from ..toolkit.parity.fixture_repo import build_fixture_repo


@pytest.fixture()
def fixture_repo(tmp_path):
    return build_fixture_repo(tmp_path / "repo")
