"""localhost resolves to IPv4 first (gitpilot/loopback.py).

Found while benchmarking through a WSL-hosted gateway from Windows: every
synchronous request to ``localhost`` waited ~21 s on an IPv6 attempt that never
connected, while ``127.0.0.1`` answered in a fraction of a second.
"""
from __future__ import annotations

import os
import socket

import pytest

from gitpilot import loopback

V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 11435, 0, 0))
V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 11435))
OTHER_V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))
OTHER_V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))


def _fake_resolver(calls):
    def resolve(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002, ARG001, PLR0917
        calls.append((host, family))
        return [V6, V4] if loopback.is_loopback_name(host) else [OTHER_V6, OTHER_V4]
    return resolve


@pytest.mark.parametrize(("host", "expected"), [
    ("localhost", True), ("LOCALHOST", True), ("localhost.", True), ("api.localhost", True),
    (b"localhost", True), ("127.0.0.1", False), ("::1", False), ("example.com", False),
    ("mylocalhost.com", False), (None, False),
])
def test_only_loopback_names_are_affected(host, expected):
    assert loopback.is_loopback_name(host) is expected


def test_localhost_gets_ipv4_first_and_keeps_ipv6_as_fallback():
    resolve = loopback._wrap(_fake_resolver([]))
    assert resolve("localhost", 11435) == [V4, V6]


def test_other_hosts_resolve_exactly_as_before():
    resolve = loopback._wrap(_fake_resolver([]))
    assert resolve("example.com", 443) == [OTHER_V6, OTHER_V4]


def test_an_explicit_family_request_is_left_alone():
    """A caller that asked for IPv6 on purpose gets the resolver's answer."""
    resolve = loopback._wrap(_fake_resolver([]))
    assert resolve("localhost", 11435, socket.AF_INET6) == [V6, V4]


def test_install_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver(calls))
    monkeypatch.delenv(loopback.ENV_SWITCH, raising=False)

    assert loopback.install() is True
    patched = socket.getaddrinfo
    assert loopback.install() is True
    assert socket.getaddrinfo is patched, "a second install must not wrap twice"
    assert socket.getaddrinfo("localhost", 1)[0][0] == socket.AF_INET
    assert len(calls) == 1


def test_it_can_be_switched_off(monkeypatch):
    original = _fake_resolver([])
    monkeypatch.setattr(socket, "getaddrinfo", original)
    monkeypatch.setenv(loopback.ENV_SWITCH, "0")

    assert loopback.install() is False
    assert socket.getaddrinfo is original


def test_uninstall_restores_the_original(monkeypatch):
    original = _fake_resolver([])
    monkeypatch.setattr(socket, "getaddrinfo", original)
    monkeypatch.delenv(loopback.ENV_SWITCH, raising=False)
    loopback.install()
    loopback.uninstall()
    assert socket.getaddrinfo is original


def test_importing_gitpilot_installs_it():
    """This module imported ``gitpilot`` at the top, so the resolver is already wrapped."""
    switched_off = os.environ.get(loopback.ENV_SWITCH) == "0"
    assert switched_off or getattr(socket.getaddrinfo, "__gitpilot_ipv4_first__", False)
