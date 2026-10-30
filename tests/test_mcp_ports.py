"""Picking host ports for the MCP stack, and keeping the Forge image startable.

`make run` used to publish MCP Context Forge on :4444 no matter what held it.
Now a port something else holds moves the service to the next free port (saved
to .mcp.env so every later command agrees), while a port our own container
already publishes is kept. Separately, a Forge image whose MCP SDK is too new
for its code gets the SDK pinned instead of crash-looping.

Both libraries are bash, so these tests drive them through bash with a stub
`docker` on PATH and, where a port must really be busy, a real listening socket.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PORTS_LIB = ROOT / "scripts" / "lib" / "mcp-ports.sh"
IMAGE_LIB = ROOT / "scripts" / "lib" / "forge-image.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="the MCP scripts run under a POSIX bash (Linux, macOS, WSL)",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def busy_port():
    """A port that really is taken for the duration of the test."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


@pytest.fixture()
def bash(tmp_path):
    """Run a snippet with the libraries sourced and a stub `docker` first on PATH.

    The stub answers `docker ps --filter ...` with OWN_PORTS (what our container
    publishes) and a plain `docker ps` with ALL_PORTS; `docker run` exits with
    RUN_EXIT and `docker build` with BUILD_EXIT. Every call is logged.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >> "{tmp_path}/docker.log"
        case "$1" in
          ps)
            if [[ "$*" == *--filter* ]]; then printf '%s\\n' "${{OWN_PORTS:-}}"
            else printf '%s\\n' "${{ALL_PORTS:-}}"; fi ;;
          image) exit "${{IMAGE_EXIT:-0}}" ;;
          run) exit "${{RUN_EXIT:-0}}" ;;
          build) exit "${{BUILD_EXIT:-0}}" ;;
        esac
        exit 0
    """), encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)

    def _run(snippet: str, env: dict | None = None) -> subprocess.CompletedProcess:
        full_env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "MCP_ENV_FILE": str(tmp_path / ".mcp.env"),
        }
        full_env.update(env or {})
        script = f'source "{PORTS_LIB}"; source "{IMAGE_LIB}"\n{snippet}'
        return subprocess.run(
            ["bash", "-c", script], env=full_env, cwd=tmp_path,
            capture_output=True, text=True, timeout=60,
        )

    return _run


def _docker_log(tmp_path: Path) -> str:
    log = tmp_path / "docker.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


def test_a_free_port_is_kept_and_nothing_is_rewritten(bash, tmp_path):
    port = _free_port()
    env_file = tmp_path / ".mcp.env"
    env_file.write_text(f"MCP_FORGE_PORT={port}\n", encoding="utf-8")

    result = bash(
        f'MCP_FORGE_PORT={port} mcp_claim_port MCP_FORGE_PORT 4444 gitpilot-mcp-context-forge'
        ' && echo "chosen=$MCP_FORGE_PORT"',
        env={"MCP_FORGE_PORT": str(port)},
    )

    assert result.returncode == 0, result.stderr
    assert f"chosen={port}" in result.stdout
    assert "in use" not in result.stdout
    assert env_file.read_text(encoding="utf-8") == f"MCP_FORGE_PORT={port}\n"


def test_a_port_held_by_something_else_moves_to_the_next_free_one(bash, tmp_path, busy_port):
    env_file = tmp_path / ".mcp.env"
    env_file.write_text(f"# ports\nMCP_FORGE_PORT={busy_port}\nMCP_POSTGRE_PORT=8080\n",
                        encoding="utf-8")

    result = bash(
        'mcp_claim_port MCP_FORGE_PORT 4444 gitpilot-mcp-context-forge && echo "chosen=$MCP_FORGE_PORT"',
        env={"MCP_FORGE_PORT": str(busy_port)},
    )

    assert result.returncode == 0, result.stderr
    chosen = int(result.stdout.split("chosen=")[1].split()[0])
    assert chosen > busy_port
    assert f"Port {busy_port} is in use" in result.stdout
    # Saved in place, so register/stop/logs and the backend follow the move;
    # the other lines are untouched.
    assert env_file.read_text(encoding="utf-8") == (
        f"# ports\nMCP_FORGE_PORT={chosen}\nMCP_POSTGRE_PORT=8080\n"
    )


def test_a_port_our_own_container_publishes_is_kept(bash, tmp_path, busy_port):
    """Re-running `make run` with the stack up must not move it out from under itself."""
    env_file = tmp_path / ".mcp.env"
    env_file.write_text(f"MCP_FORGE_PORT={busy_port}\n", encoding="utf-8")

    result = bash(
        'mcp_claim_port MCP_FORGE_PORT 4444 gitpilot-mcp-context-forge && echo "chosen=$MCP_FORGE_PORT"',
        env={"MCP_FORGE_PORT": str(busy_port),
             "OWN_PORTS": f"0.0.0.0:{busy_port}->4444/tcp"},
    )

    assert result.returncode == 0, result.stderr
    assert f"chosen={busy_port}" in result.stdout
    assert env_file.read_text(encoding="utf-8") == f"MCP_FORGE_PORT={busy_port}\n"


def test_a_port_another_container_publishes_counts_as_taken(bash, tmp_path):
    """Docker Desktop publishes on its VM, where `ss` on the host may not look."""
    port = _free_port()
    (tmp_path / ".mcp.env").write_text("", encoding="utf-8")

    result = bash(
        'mcp_claim_port MCP_POSTGRE_PORT 8080 gitpilot-mcp-postgre-server && echo "chosen=$MCP_POSTGRE_PORT"',
        env={"MCP_POSTGRE_PORT": str(port),
             "ALL_PORTS": f"0.0.0.0:{port}->5432/tcp, :::{port}->5432/tcp"},
    )

    assert result.returncode == 0, result.stderr
    chosen = int(result.stdout.split("chosen=")[1].split()[0])
    assert chosen != port
    # The line did not exist, so it is appended rather than lost.
    assert (tmp_path / ".mcp.env").read_text(encoding="utf-8") == f"MCP_POSTGRE_PORT={chosen}\n"


def test_no_free_port_in_the_window_is_an_error_that_says_what_to_do(bash, busy_port):
    result = bash(
        'mcp_claim_port MCP_FORGE_PORT 4444 gitpilot-mcp-context-forge',
        env={"MCP_FORGE_PORT": str(busy_port), "MCP_PORT_SEARCH_SPAN": "0"},
    )

    assert result.returncode == 1
    assert "No free port" in result.stdout
    assert "MCP_FORGE_PORT" in result.stdout


@pytest.mark.parametrize(
    ("env_file", "env", "expected"),
    [
        ("MCP_FORGE_PORT=4444\n", {}, "MCP_FORGE_PORT=4444"),
        ("MCP_FORGE_PORT=4445\n", {},
         "MCP_FORGE_PORT=4445 GITPILOT_MCP_FORGE_URL=http://localhost:4445"),
        ("MCP_FORGE_PORT=4445\n", {"GITPILOT_MCP_FORGE_URL": "http://mine:9999"},
         "MCP_FORGE_PORT=4445"),
        ("MCP_FORGE_PORT=4445\nMCP_FORGE_URL=http://forge.lan:4444/\n", {},
         "MCP_FORGE_PORT=4445 GITPILOT_MCP_FORGE_URL=http://forge.lan:4444"),
        ("", {}, ""),
    ],
    ids=["default-port", "moved-port", "operator-url-wins", "pinned-forge-url", "no-port-line"],
)
def test_the_backend_is_told_where_a_moved_forge_is(bash, tmp_path, env_file, env, expected):
    (tmp_path / ".mcp.env").write_text(env_file, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(PORTS_LIB), "--backend-env"],
        env={"PATH": os.environ.get("PATH", ""), "MCP_ENV_FILE": str(tmp_path / ".mcp.env"), **env},
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# The Forge image's MCP SDK
# ---------------------------------------------------------------------------


def test_a_compatible_image_is_left_alone(bash, tmp_path):
    result = bash("forge_image_ensure_sdk", env={"RUN_EXIT": "0"})

    assert result.returncode == 0, result.stderr
    assert "build" not in _docker_log(tmp_path)


def test_an_image_with_a_too_new_sdk_gets_the_pin_layer(bash, tmp_path):
    result = bash("forge_image_ensure_sdk", env={"RUN_EXIT": "3"})

    assert result.returncode == 0, result.stderr
    build = [line for line in _docker_log(tmp_path).splitlines() if line.startswith("build")]
    assert len(build) == 1
    assert "FORGE_IMAGE=gitpilot/mcp-context-forge:local" in build[0]
    assert "MCP_SDK_SPEC=mcp>=1.25.0,<2" in build[0]
    assert "sdk-pin.Dockerfile" in build[0]
    assert "-t gitpilot/mcp-context-forge:local" in build[0]
    assert "pinned" in result.stdout


def test_a_failed_pin_is_reported_with_the_next_step(bash):
    result = bash("forge_image_ensure_sdk", env={"RUN_EXIT": "3", "BUILD_EXIT": "1"})

    assert result.returncode == 1
    assert "MCP_FORGE_REF" in result.stdout


def test_an_inconclusive_check_does_not_rebuild_anything(bash, tmp_path):
    """If the probe itself cannot run, starting the image as built is the
    honest default; guessing at a pin could downgrade a Forge that needs 2.x."""
    result = bash("forge_image_ensure_sdk", env={"RUN_EXIT": "125"})

    assert result.returncode == 0, result.stderr
    assert "build" not in _docker_log(tmp_path)
    assert "Could not check" in result.stdout


def test_no_image_yet_means_nothing_to_check(bash, tmp_path):
    result = bash("forge_image_ensure_sdk", env={"IMAGE_EXIT": "1"})

    assert result.returncode == 0, result.stderr
    assert "run" not in _docker_log(tmp_path).split()
