"""Choosing the port the server actually binds.

A busy port should not stop you from starting GitPilot. It should also not make
GitPilot quietly appear somewhere a reverse proxy, a container port map, or a
teammate's bookmark isn't looking. Those two goals pull in opposite directions,
so the rule is:

* **You asked for a specific port** (``--port 9000``, ``GITPILOT_PORT=9000``) →
  it is honoured strictly. Something depends on that number; moving it silently
  would break whatever that is.
* **You asked for nothing** → the default drifts to the next free port and says
  so, loudly, once.

``--strict-port`` / ``--no-strict-port`` override the rule in either direction.

Availability is decided by *binding*, not by connecting. A connect probe asks
"can I reach a server here", which answers the wrong question: it misses a
listener bound to another interface of the same host, and it cannot see a socket
held without accepting. Binding asks exactly what uvicorn is about to ask, on
the same address family, so a port this says is free is a port the server can
actually take.
"""

from __future__ import annotations

import errno
import socket
from dataclasses import dataclass

#: How many consecutive ports to try before giving up. Bounded on purpose — a
#: machine with 20 busy ports in a row has a problem that scanning won't fix.
DEFAULT_ATTEMPTS = 20


class NoFreePortError(RuntimeError):
    """Every candidate port in the window was taken."""


@dataclass(frozen=True)
class PortChoice:
    """The port to bind, and whether it is the one that was asked for."""

    port: int
    requested: int

    @property
    def moved(self) -> bool:
        return self.port != self.requested


def _addr_families(host: str) -> list[tuple[int, str]]:
    """Resolve *host* to the (family, address) pairs a server would bind.

    Binding "localhost" can mean 127.0.0.1, ::1, or both depending on the
    machine; a port is only free if every one of them is free, otherwise the
    server picks the other family and fails after we promised it wouldn't.
    """
    try:
        # An empty host means "every interface", which is what the server would
        # bind; this call only enumerates families, it binds nothing.
        infos = socket.getaddrinfo(host or "0.0.0.0", None, type=socket.SOCK_STREAM)  # noqa: S104
    except socket.gaierror:
        return [(socket.AF_INET, host)]
    seen: list[tuple[int, str]] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        pair = (family, sockaddr[0])
        if pair not in seen:
            seen.append(pair)
    return seen or [(socket.AF_INET, host)]


def is_port_free(host: str, port: int) -> bool:
    """True when a server could bind *port* on every address *host* resolves to."""
    for family, address in _addr_families(host):
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            # Match what uvicorn does, so the probe and the real bind agree: a
            # port left in TIME_WAIT by a previous run is reusable, not busy.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((address, port))
            except OSError as exc:
                if exc.errno in (errno.EADDRINUSE, errno.EACCES, errno.EADDRNOTAVAIL):
                    return False
                raise
    return True


def find_free_port(
    host: str,
    preferred: int,
    attempts: int = DEFAULT_ATTEMPTS,
) -> int:
    """The first free port at or after *preferred*.

    Raises :class:`NoFreePortError` rather than falling back to an ephemeral port —
    a number nobody can predict is not a useful place to serve a UI from.
    """
    if preferred <= 0:
        raise ValueError("preferred port must be positive")
    last = min(preferred + attempts - 1, 65535)
    for candidate in range(preferred, last + 1):
        if is_port_free(host, candidate):
            return candidate
    raise NoFreePortError(
        f"no free port between {preferred} and {last} on {host}. "
        "Stop what is holding them, or pass --port to choose another range."
    )


def resolve_port(
    host: str,
    preferred: int,
    strict: bool,
    attempts: int = DEFAULT_ATTEMPTS,
) -> PortChoice:
    """Decide the port to bind.

    With ``strict``, a busy port is an error the caller reports; otherwise the
    next free one is used and :attr:`PortChoice.moved` says the caller should
    tell the human about it.
    """
    if strict:
        if not is_port_free(host, preferred):
            raise NoFreePortError(
                f"port {preferred} on {host} is already in use. "
                "Free it, choose another with --port, or pass --no-strict-port "
                "to start on the next available port instead."
            )
        return PortChoice(port=preferred, requested=preferred)
    return PortChoice(port=find_free_port(host, preferred, attempts), requested=preferred)
