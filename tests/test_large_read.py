"""Large-file reading: small files unchanged, large ones paged like Claude Code."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from gitpilot import large_read
from gitpilot.large_read import READ_PAGE_LINES, LINE_MAX_CHARS, frame, window


def test_small_files_keep_the_legacy_framing() -> None:
    assert frame("a.py", "print('x')\n") == "Content of a.py:\n---\nprint('x')\n\n---"


def test_a_long_file_returns_a_numbered_first_page_with_the_next_offset() -> None:
    content = "\n".join(f"line {i}" for i in range(1, 5001))
    out = frame("big.py", content)
    assert out.startswith(f"Content of big.py (lines 1-{READ_PAGE_LINES} of 5000):")
    assert "     1\tline 1" in out and f"  {READ_PAGE_LINES}\tline {READ_PAGE_LINES}" in out
    assert f"line {READ_PAGE_LINES + 1}\n" not in out
    assert f"offset={READ_PAGE_LINES}" in out and "3000 more lines" in out


def test_a_window_continues_where_the_page_ended() -> None:
    content = "\n".join(f"line {i}" for i in range(1, 5001))
    out = window("big.py", content, 4990, 50)
    assert "(lines 4991-5000 of 5000)" in out and "  5000\tline 5000" in out
    assert "more lines" not in out


def test_long_lines_are_shortened_and_counted() -> None:
    out = frame("bundle.js", "x" * (LINE_MAX_CHARS + 500))
    assert "[500 more characters on this line]" in out and "1 long line(s) were shortened" in out
    assert len(out) < LINE_MAX_CHARS + 300


def test_the_page_respects_the_character_budget(monkeypatch) -> None:
    monkeypatch.setattr(large_read, "READ_PAGE_CHARS", 1_000)
    out = frame("wide.py", "\n".join("y" * 90 for _ in range(100)))
    body = out.split("---")[1]
    assert len(body) <= 1_100 and "more lines" in out


def test_binary_files_are_described_not_dumped() -> None:
    out = frame("img.png", "\x89PNG\x00\x00" + "z" * 50_000)
    assert "binary file" in out and "zzzz" not in out


def test_fs_read_pages_with_offset_and_limit() -> None:
    from gitpilot.toolkit import RepoBinding, ToolCall, ToolExecutionContext, ToolRegistry
    from gitpilot.toolkit.fs import register

    registry = ToolRegistry()
    register(registry)
    ctx = ToolExecutionContext(repo=RepoBinding(owner="o", repo="r", token="t", branch="main"))
    content = "\n".join(f"line {i}" for i in range(1, 101))
    with patch("gitpilot.github_api.get_file", new_callable=AsyncMock) as get_file:
        get_file.return_value = content
        result = asyncio.run(registry.execute(
            ToolCall(id="t", tool="fs.read", arguments={"path": "a.py", "offset": 10, "limit": 5}), ctx))
    assert result.ok and "(lines 11-15 of 100)" in result.content and "    11\tline 11" in result.content
    assert result.data["paged"] is True
