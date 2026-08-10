"""What the SSE stream says when it has nothing to do.

The report these come from: one question in VS Code, and a thinking
animation that ran, stopped, ran again and finally said "Failed" — while
the server had answered successfully three times over.

Part of that is the client's deadline, covered in the extension's own
tests. This is the other part. A session with no GitHub repository has no
repo for the multi-agent planner to plan against, so the streaming
executor closes the stream immediately and the client falls back to
``/api/chat/send``. That handover is correct and deliberate.

What was not correct was announcing it as completion. The executor emitted
``status_change("done")`` alongside the terminator, and "done" is what the
UI reads to stop the spinner — so a request that had not started yet was
reported as finished, the animation cleared, and the extension had to
raise it again for the batch call it was about to make. One question,
three visible states, none of them the truth.

``agent_done`` is the terminator; it alone closes the stream. The status
belongs to work that actually happened.
"""

from __future__ import annotations

import pytest

from gitpilot.agent_events import AgentEventBus, EventType
from gitpilot.agent_executor import StreamingAgentExecutor


class _RecordingBus(AgentEventBus):
    """A bus that keeps what was emitted instead of delivering it."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list = []

    async def emit(self, event) -> None:  # type: ignore[override]
        self.events.append(event)

    def types(self) -> list[str]:
        return [
            e.type.value if hasattr(e.type, "value") else str(e.type)
            for e in self.events
        ]

    def statuses(self) -> list[str]:
        return [
            str(e.data.get("status"))
            for e in self.events
            if (e.type.value if hasattr(e.type, "value") else str(e.type))
            == EventType.STATUS_CHANGE.value
        ]


def _executor(bus: _RecordingBus) -> StreamingAgentExecutor:
    return StreamingAgentExecutor(bus=bus, gate=object())


@pytest.mark.parametrize(
    "repo_full_name",
    [
        "",            # folder-only session — the reported case
        None,          # never set
        "no-slash",    # not owner/repo
        "owner/",      # half a name
        "/repo",
        "a/b/c",       # too many parts
    ],
)
@pytest.mark.anyio
async def test_a_session_without_a_repo_hands_off_without_claiming_success(
    repo_full_name,
) -> None:
    bus = _RecordingBus()

    result = await _executor(bus).execute(
        user_message="Explain this project's architecture",
        repo_full_name=repo_full_name,
    )

    assert result is None, "there is no plan to return for a repo-less session"
    assert EventType.DONE.value in bus.types(), (
        "the stream still has to terminate, or the client reads until it times out"
    )
    assert "done" not in bus.statuses(), (
        "announcing completion before any work starts is what made the "
        "spinner stop and restart for a single question"
    )


@pytest.mark.anyio
async def test_the_handoff_carries_no_text() -> None:
    """Empty text is the signal the client uses to choose the batch path.

    The extension treats a stream that produced no text as "the backend
    handed this to /api/chat/send". Emitting a stray delta here would make
    it keep an empty answer instead.
    """
    bus = _RecordingBus()

    await _executor(bus).execute(user_message="hi", repo_full_name="")

    assert EventType.TEXT_DELTA.value not in bus.types()
    done = [e for e in bus.events if e.type == EventType.DONE][0]
    assert done.data.get("summary") == ""


@pytest.mark.anyio
async def test_the_handoff_is_not_reported_as_an_error() -> None:
    """A folder session is a normal setup, not a fault.

    An ``error`` event renders as a user-visible notice in every SSE
    consumer, which would put a red banner on the perfectly ordinary act
    of asking a question about a local folder.
    """
    bus = _RecordingBus()

    await _executor(bus).execute(user_message="hi", repo_full_name="")

    assert EventType.ERROR.value not in bus.types()
