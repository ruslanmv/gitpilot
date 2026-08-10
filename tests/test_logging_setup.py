"""Whether GitPilot's own log lines reach the console.

The report these come from: a server log full of uvicorn's INFO lines and
GitPilot's WARNING lines, and not one GitPilot INFO line — so every route
decision, provider call and timing the code writes was being discarded,
while the uvicorn lines made it look like logging was working fine.

The cause is that ``uvicorn.run(log_level="info")`` configures uvicorn's
three loggers and nothing else. GitPilot's records propagate to a root
logger with no handler, so Python falls back to ``logging.lastResort``,
whose level is WARNING. Hence: warnings through, everything below dropped.
"""

from __future__ import annotations

import io
import logging

import pytest

from gitpilot import logging_setup


@pytest.fixture(autouse=True)
def _restore_gitpilot_logger():
    """Put the shared logger back; these tests reconfigure it on purpose."""
    logger = logging.getLogger("gitpilot")
    handlers = list(logger.handlers)
    level, propagate = logger.level, logger.propagate
    yield
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate


def _emit(level: str = "info", name: str = "gitpilot._api_app") -> str:
    stream = io.StringIO()
    logging_setup.configure("INFO", stream=stream)
    getattr(logging.getLogger(name), level)("the message")
    return stream.getvalue()


def test_info_reaches_the_console() -> None:
    """The whole point: this is what was being thrown away."""
    assert "the message" in _emit("info")


def test_warning_still_reaches_the_console() -> None:
    """The one level that always worked must keep working."""
    assert "the message" in _emit("warning")


def test_debug_is_filtered_at_info() -> None:
    assert "the message" not in _emit("debug")


def test_a_submodule_is_covered_by_the_package_logger() -> None:
    """Every module logs under `gitpilot.<something>`; one handler serves all."""
    assert "the message" in _emit("info", "gitpilot.agentic.deeply.nested")


def test_configuring_twice_does_not_double_every_line() -> None:
    """A re-import must not turn one line into two."""
    stream = io.StringIO()
    logging_setup.configure("INFO", stream=stream)
    logging_setup.configure("INFO", stream=stream)
    logging_setup.configure("DEBUG", stream=stream)

    logging.getLogger("gitpilot.x").info("once")

    assert stream.getvalue().count("once") == 1


def test_the_second_call_re_levels_the_existing_handler() -> None:
    stream = io.StringIO()
    logging_setup.configure("INFO", stream=stream)
    logging_setup.configure("DEBUG", stream=stream)

    logging.getLogger("gitpilot.x").debug("now visible")

    assert "now visible" in stream.getvalue()


@pytest.mark.parametrize(
    "given, expected",
    [
        ("DEBUG", logging.DEBUG),
        ("debug", logging.DEBUG),
        ("  Info  ", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        (logging.DEBUG, logging.DEBUG),
    ],
)
def test_levels_are_read_however_they_are_written(given, expected) -> None:
    assert logging_setup.resolve_level(given) == expected


def test_a_typo_does_not_stop_the_server(monkeypatch) -> None:
    """A bad --log-level is a typo, not a reason to refuse to start."""
    monkeypatch.delenv(logging_setup.LEVEL_ENV_VAR, raising=False)
    assert logging_setup.resolve_level("VERBOSE") == logging.INFO


def test_the_environment_supplies_the_level_when_nothing_is_passed(monkeypatch) -> None:
    """`uvicorn --reload` re-imports in a child; the env var is the only carrier."""
    monkeypatch.setenv(logging_setup.LEVEL_ENV_VAR, "DEBUG")
    assert logging_setup.resolve_level(None) == logging.DEBUG


def test_the_default_is_info(monkeypatch) -> None:
    monkeypatch.delenv(logging_setup.LEVEL_ENV_VAR, raising=False)
    assert logging_setup.resolve_level(None) == logging.INFO


def test_an_explicit_level_beats_the_environment(monkeypatch) -> None:
    monkeypatch.setenv(logging_setup.LEVEL_ENV_VAR, "ERROR")
    assert logging_setup.resolve_level("DEBUG") == logging.DEBUG


def test_propagation_is_off_so_uvicorn_does_not_print_everything_twice() -> None:
    """Once uvicorn installs a root handler, propagating would duplicate."""
    logging_setup.configure("INFO", stream=io.StringIO())
    assert logging.getLogger("gitpilot").propagate is False


def test_the_root_logger_is_left_alone() -> None:
    """GitPilot is importable as a library; it does not own the process's logging."""
    root_before = list(logging.getLogger().handlers)

    logging_setup.configure("INFO", stream=io.StringIO())

    assert logging.getLogger().handlers == root_before, (
        "basicConfig would have taken over logging for every library in the process"
    )
