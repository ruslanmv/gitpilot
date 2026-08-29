# gitpilot/shell_safety.py
"""Single source of truth for shell-command safety — Batch V4-0C.

Two guards, shared by every executor that can spawn a shell:

* :data:`BLOCKED_PATTERNS` — a conservative substring denylist of commands
  that are never worth running on an agent's behalf.
* :func:`strip_secret_env` — drops credentials (and, optionally, proxy
  configuration) from an inherited environment.

Why this module exists: the denylist used to live in two places.
``gitpilot.sandbox`` carried six patterns; ``gitpilot.terminal`` carried four
— so ``shutdown -h`` was refused by the sandbox and accepted by the terminal
executor.  Secret stripping existed only in ``SubprocessSandbox``, which meant
the terminal executor still used for the lint/test validation phase handed
``GITHUB_TOKEN`` and every provider API key to whatever it ran.  Both guards
now come from here and neither module keeps a copy.

The denylist is a backstop, not a security boundary — real containment is the
sandbox's workspace jail and, for untrusted code, the MatrixLab container
backend.  Semantic classification of commands (READ_ONLY … DESTRUCTIVE)
arrives with the policy engine in Batch V4-D2 and will consume these tables
rather than replace them.
"""
from __future__ import annotations

import os
from typing import Dict, Mapping, Optional, Tuple

#: Substring patterns refused before launch, matched case-insensitively
#: against the whole command line.  The union of the two lists that used to
#: exist separately in :mod:`gitpilot.sandbox` and :mod:`gitpilot.terminal`.
BLOCKED_PATTERNS: Tuple[str, ...] = (
    "rm -rf /",
    "mkfs",
    "dd if=/dev/zero",
    ":(){ :|:& };:",
    "shutdown -h",
    "shutdown -r",
)

#: Credentials always removed from an inherited environment.  A sandboxed or
#: agent-driven command has no business reading the operator's tokens.
SECRET_ENV_KEYS: Tuple[str, ...] = (
    "GITHUB_TOKEN",
    "GITPILOT_GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "WATSONX_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)

#: Proxy configuration removed when network access is disallowed, so a command
#: cannot reach the outside world through an inherited proxy.
NETWORK_ENV_KEYS: Tuple[str, ...] = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)


def blocked_reason(command: str, patterns: Optional[Tuple[str, ...]] = None) -> Optional[str]:
    """Return the denylist pattern *command* matches, or ``None`` if it is clean.

    Returning the pattern rather than a bool lets callers explain the refusal
    ("blocked by pattern 'mkfs'") instead of only reporting one.
    """
    haystack = (command or "").lower().strip()
    for pattern in (patterns if patterns is not None else BLOCKED_PATTERNS):
        if pattern in haystack:
            return pattern
    return None


def is_blocked(command: str, patterns: Optional[Tuple[str, ...]] = None) -> bool:
    """Return whether *command* matches the denylist."""
    return blocked_reason(command, patterns) is not None


def strip_secret_env(
    base: Optional[Mapping[str, str]] = None,
    *,
    allow_network: bool = True,
    overrides: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Build a child environment with credentials removed.

    *base* defaults to the current process environment.  Secrets in
    :data:`SECRET_ENV_KEYS` are always dropped; :data:`NETWORK_ENV_KEYS` are
    dropped as well when *allow_network* is false.

    *overrides* are applied **after** stripping and are never filtered: a
    caller that passes a variable explicitly means it, and callers that need a
    token in the child (a git push, say) must opt in that way rather than
    relying on ambient inheritance.
    """
    source = os.environ if base is None else base
    env: Dict[str, str] = {
        key: value for key, value in source.items() if key not in SECRET_ENV_KEYS
    }
    if not allow_network:
        for key in NETWORK_ENV_KEYS:
            env.pop(key, None)
    if overrides:
        env.update(overrides)
    return env
