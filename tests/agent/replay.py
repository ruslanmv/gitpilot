"""Record/replay harness for provider clients — Batch V4-B1.

Provider responses are captured once as JSON fixtures and replayed through an
``httpx.MockTransport``, so the tests exercise the real client — real body
assembly, real header construction, real parsing — with no network in CI.

Two things this deliberately does *not* do:

* **It does not mock the client.**  A test double for ``complete()`` would prove
  our model of a provider matches itself.  The transport is the lowest layer we
  can fake while still testing everything we wrote.
* **It does not invent payloads.**  The fixtures under ``fixtures/`` are shaped
  from the documented response formats and from the corner cases
  :mod:`gitpilot.direct_chat` recorded in its comments (list content, an empty
  ``content`` with the substance in ``reasoning``, the legacy completion shape).
  When a real capture disagrees, the fixture is what changes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@dataclass
class RecordedCall:
    """What the client sent, kept for assertions."""

    url: str
    headers: Dict[str, str]
    body: Dict[str, Any]


@dataclass
class ReplayTransport:
    """An ``httpx`` transport that answers from a queue of canned responses."""

    responses: List[httpx.Response] = field(default_factory=list)
    calls: List[RecordedCall] = field(default_factory=list)

    def as_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            try:
                body = json.loads(request.content.decode() or "{}")
            except ValueError:
                body = {"_raw": request.content.decode("utf-8", "replace")}
            self.calls.append(
                RecordedCall(
                    url=str(request.url),
                    headers=dict(request.headers),
                    body=body,
                )
            )
            if not self.responses:
                raise AssertionError(f"no queued response for {request.url}")
            return self.responses.pop(0)

        return httpx.MockTransport(handler)

    @property
    def last(self) -> RecordedCall:
        assert self.calls, "no request was made"
        return self.calls[-1]


def load_fixture(name: str) -> Dict[str, Any]:
    """Read a captured response payload."""
    path = FIXTURE_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"missing provider fixture {name!r}; expected {path}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def json_response(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=load_fixture(name))


def error_response(status: int, body: Any = None, text: Optional[str] = None) -> httpx.Response:
    if text is not None:
        return httpx.Response(status, text=text)
    return httpx.Response(status, json=body or {"error": {"message": "failure"}})


def sse_response(lines: List[str], status: int = 200) -> httpx.Response:
    """A streamed body, framed the way providers send it."""
    payload = "".join(f"{line}\n\n" for line in lines)
    return httpx.Response(
        status,
        content=payload.encode(),
        headers={"content-type": "text/event-stream"},
    )


def sse_lines_from_fixture(name: str) -> List[str]:
    """Turn a fixture's ``lines`` array into SSE ``data:`` frames."""
    payload = load_fixture(name)
    out: List[str] = []
    for entry in payload["lines"]:
        if isinstance(entry, str):
            out.append(entry)
        else:
            out.append("data: " + json.dumps(entry))
    return out
