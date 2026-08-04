"""A busy port must not stop GitPilot — and must not move it behind your back.

Both halves matter. Someone starting GitPilot next to another service (a
HomePilot backend on :8000 is the common case) should just get a server. Someone
who wrote `--port 9000` into a proxy config, a container port map, or a bookmark
should get an error if 9000 is taken, not a server somewhere else.
"""

from __future__ import annotations

import socket

import pytest
from typer.testing import CliRunner

from gitpilot.cli import cli
from gitpilot.portutil import (
    NoFreePortError,
    PortChoice,
    find_free_port,
    is_port_free,
    resolve_port,
)

runner = CliRunner()


@pytest.fixture()
def occupied():
    """A really-bound port, not a mock — the probe must agree with a real bind."""
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    yield held.getsockname()[1]
    held.close()


# ── Availability is decided by binding, not by connecting ───────────────────

def test_a_bound_port_is_not_free(occupied):
    assert is_port_free("127.0.0.1", occupied) is False


def test_an_unbound_port_is_free():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert is_port_free("127.0.0.1", port) is True


def test_a_privileged_port_reads_as_unavailable_rather_than_crashing():
    """Port 1 needs root; that is 'cannot bind', not an exception to the caller."""
    assert is_port_free("127.0.0.1", 1) in (True, False)


# ── The drift ───────────────────────────────────────────────────────────────

def test_a_busy_port_moves_to_the_next_free_one(occupied):
    assert find_free_port("127.0.0.1", occupied) == occupied + 1


def test_the_scan_is_bounded_and_says_what_to_do(occupied):
    with pytest.raises(NoFreePortError) as exc:
        find_free_port("127.0.0.1", occupied, attempts=1)
    assert "--port" in str(exc.value)


def test_a_free_preferred_port_is_kept():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    choice = resolve_port("127.0.0.1", port, strict=False)
    assert choice == PortChoice(port=port, requested=port)
    assert choice.moved is False


def test_strict_refuses_rather_than_moving(occupied):
    """A port you named is a promise to whatever is configured to reach it."""
    with pytest.raises(NoFreePortError) as exc:
        resolve_port("127.0.0.1", occupied, strict=True)
    assert "--no-strict-port" in str(exc.value)

    choice = resolve_port("127.0.0.1", occupied, strict=False)
    assert choice.moved and choice.port == occupied + 1


# ── The CLI applies the rule, and reports what it did ───────────────────────

def test_free_port_prints_only_the_number(occupied):
    result = runner.invoke(cli, ["free-port", "--port", str(occupied)])
    assert result.exit_code == 0
    assert result.stdout.strip() == str(occupied + 1)


def test_free_port_exits_nonzero_when_the_window_is_full(occupied):
    result = runner.invoke(cli, ["free-port", "--port", str(occupied), "--attempts", "1"])
    assert result.exit_code == 1


@pytest.fixture()
def serve_calls(monkeypatch):
    """Run `serve` without booting anything, recording how it resolved the port."""
    calls: list[dict] = []

    monkeypatch.setattr("gitpilot.cli._maybe_bootstrap_workspace", lambda _p: None)
    monkeypatch.setattr("gitpilot.cli._display_startup_banner", lambda *_a, **_k: None)

    def _resolve(host, preferred, strict, *args, **kwargs):
        calls.append({"host": host, "preferred": preferred, "strict": strict})
        return PortChoice(port=preferred, requested=preferred)

    monkeypatch.setattr("gitpilot.cli.resolve_port", _resolve)

    class _Thread:
        def __init__(self, **kwargs):
            calls[-1]["bound"] = (kwargs.get("kwargs") or {}).get("port")

        def start(self):
            pass

        def join(self):
            pass

    monkeypatch.setattr("gitpilot.cli.threading.Thread", _Thread)
    return calls


def test_the_default_port_may_drift_but_a_named_one_may_not(serve_calls, monkeypatch):
    """Where the port came from decides whether it is allowed to move."""
    assert runner.invoke(cli, ["serve", "--no-open", "--skip-init"]).exit_code == 0
    assert serve_calls[-1]["strict"] is False  # nobody named it → free to drift

    runner.invoke(cli, ["serve", "--no-open", "--skip-init", "--port", "9000"])
    assert serve_calls[-1] == {"host": "127.0.0.1", "preferred": 9000,
                               "strict": True, "bound": 9000}

    # An environment variable is just as deliberate as a flag.
    monkeypatch.setenv("GITPILOT_PORT", "9100")
    runner.invoke(cli, ["serve", "--no-open", "--skip-init"])
    assert serve_calls[-1]["preferred"] == 9100 and serve_calls[-1]["strict"] is True
    monkeypatch.delenv("GITPILOT_PORT")

    # ...and the rule can be overridden in either direction.
    runner.invoke(cli, ["serve", "--no-open", "--skip-init", "--port", "9000", "--no-strict-port"])
    assert serve_calls[-1]["strict"] is False
    runner.invoke(cli, ["serve", "--no-open", "--skip-init", "--strict-port"])
    assert serve_calls[-1]["strict"] is True


def test_a_strictly_requested_busy_port_fails_with_the_way_out(monkeypatch, occupied):
    monkeypatch.setattr("gitpilot.cli._maybe_bootstrap_workspace", lambda _p: None)
    result = runner.invoke(
        cli, ["serve", "--no-open", "--skip-init", "--port", str(occupied)]
    )
    assert result.exit_code == 1
    assert "already in use" in result.stdout
    assert "--no-strict-port" in result.stdout


def test_a_drift_is_announced_not_silent(monkeypatch, occupied):
    """Moving the server without saying so is how people lose ten minutes."""
    monkeypatch.setattr("gitpilot.cli._maybe_bootstrap_workspace", lambda _p: None)
    monkeypatch.setattr("gitpilot.cli._display_startup_banner", lambda *_a, **_k: None)

    class _Thread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

        def join(self):
            pass

    monkeypatch.setattr("gitpilot.cli.threading.Thread", _Thread)
    result = runner.invoke(
        cli,
        ["serve", "--no-open", "--skip-init", "--port", str(occupied), "--no-strict-port"],
    )
    assert result.exit_code == 0
    assert str(occupied) in result.stdout and str(occupied + 1) in result.stdout


def test_the_bound_port_is_written_for_wrappers(monkeypatch, tmp_path, occupied):
    """`make run` polls health on whatever port the server really took."""
    monkeypatch.setattr("gitpilot.cli._maybe_bootstrap_workspace", lambda _p: None)
    monkeypatch.setattr("gitpilot.cli._display_startup_banner", lambda *_a, **_k: None)

    class _Thread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

        def join(self):
            pass

    monkeypatch.setattr("gitpilot.cli.threading.Thread", _Thread)

    port_file = tmp_path / "nested" / "port"
    result = runner.invoke(cli, [
        "serve", "--no-open", "--skip-init", "--no-strict-port",
        "--port", str(occupied), "--port-file", str(port_file),
    ])
    assert result.exit_code == 0
    assert port_file.read_text().strip() == str(occupied + 1)
