"""Resolve ``localhost`` to IPv4 first, so local services answer without a 21-second wait.

On Windows, ``localhost`` resolves to ``::1`` before ``127.0.0.1``. When the
service behind it runs in WSL with mirrored networking (OllaBridge, an MCP
gateway, a sandbox runner) the IPv6 attempt neither connects nor is refused: it
waits out Windows' TCP connect retry, about 21 seconds, before the client moves
on to IPv4. Synchronous clients try addresses one after another, so every
request pays that wait. GitPilot's async httpx clients race the two families
and do not, which is why the agent loop looked fine while the legacy CrewAI
path (LiteLLM), the synchronous inference client and the sandbox runner client
were each 21 seconds slow per call.

The fix is the one Node ships as ``dns.setDefaultResultOrder("ipv4first")``:
for loopback names only, put IPv4 answers first. Nothing is removed, so a
service that listens only on ``::1`` is still reached on the next attempt, and
every other hostname resolves exactly as before. It also covers URLs already
saved in a user's settings, which a changed default could not.

Set ``GITPILOT_LOCALHOST_IPV4_FIRST=0`` to turn it off.
"""
from __future__ import annotations

import os
import socket
from typing import Any, Callable, List, Tuple

ENV_SWITCH = "GITPILOT_LOCALHOST_IPV4_FIRST"
_MARK = "__gitpilot_ipv4_first__"

Resolver = Callable[..., List[Tuple[Any, ...]]]


def is_loopback_name(host: Any) -> bool:
    """``localhost``, ``localhost.`` and ``*.localhost`` (RFC 6761), any case."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if not isinstance(host, str):
        return False
    name = host.strip().lower().rstrip(".")
    return name == "localhost" or name.endswith(".localhost")


def _wrap(original: Resolver) -> Resolver:
    def getaddrinfo(host: Any, port: Any, family: int = 0, type: int = 0,  # noqa: A002 - socket's names
                    proto: int = 0, flags: int = 0) -> List[Tuple[Any, ...]]:
        results = original(host, port, family, type, proto, flags)
        if family == socket.AF_UNSPEC and is_loopback_name(host):
            # Stable sort: IPv4 first, everything else in the resolver's order.
            results = sorted(results, key=lambda entry: entry[0] != socket.AF_INET)
        return results

    setattr(getaddrinfo, _MARK, True)
    getaddrinfo.__wrapped__ = original  # type: ignore[attr-defined]
    return getaddrinfo


def install() -> bool:
    """Patch ``socket.getaddrinfo`` once per process; True when it is active."""
    if os.environ.get(ENV_SWITCH, "1").strip() == "0":
        return False
    if getattr(socket.getaddrinfo, _MARK, False):
        return True
    socket.getaddrinfo = _wrap(socket.getaddrinfo)
    return True


def uninstall() -> None:
    """Restore the original resolver (tests)."""
    original = getattr(socket.getaddrinfo, "__wrapped__", None)
    if getattr(socket.getaddrinfo, _MARK, False) and original is not None:
        socket.getaddrinfo = original
