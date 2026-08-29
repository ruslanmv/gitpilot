# gitpilot/sandbox_tokens.py
"""Server-side proof that a sandbox run was approved — Batch V4-D6 (F7).

``POST /api/sandbox/run`` took ``{language, code}`` and executed it. The
ExecutionPlan card, the safety warnings, the "Approve" button — all of it was
client-side. Nothing on the server checked that a plan had been shown, let alone
approved, so the approval was decoration: anything that could reach the endpoint
ran arbitrary code with the server's privileges.

The fix keeps the card exactly as it is and stops the server trusting it:

1. ``/plan`` stores the plan it just built, keyed by ``plan_id``.
2. ``/approve`` mints a single-use token bound to ``(plan_id, session_id)``.
3. ``/run`` requires that token **and** checks the code it was handed is the code
   the plan described.

Step 3 is the one that matters. A token bound only to a plan id would let a
client approve `print("hi")` and then run something else under the same token —
the binding has to be to the *content*, so the bytes that execute are the bytes
that were approved.

The alternative to a token is a session in `auto` mode, read from the persisted
session record — never from a request body (§8.1). A body-supplied mode would be
the same defect wearing a different hat.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: How long an approval stays usable.  Long enough to click, short enough that a
#: token leaking from a log is not a standing grant.
TOKEN_TTL_S = 300.0

#: Plans remembered at once.  Bounded because `/plan` is unauthenticated and a
#: caller could otherwise grow this without limit.
MAX_PLANS = 256


def content_digest(language: str, code: str) -> str:
    """A stable fingerprint of what will run."""
    payload = f"{(language or '').strip().lower()}\n{code or ''}".encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass
class StoredPlan:
    plan_id: str
    session_id: str
    digest: str
    created_at: float
    plan: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalToken:
    token: str
    plan_id: str
    session_id: str
    digest: str
    expires_at: float
    used: bool = False


class SandboxApprovalStore:
    """Plans awaiting approval, and the tokens their approval minted.

    In-process and deliberately so: a token that survived a restart would
    outlive the user's intent, and the safe failure mode for "the server
    restarted mid-approval" is another click.
    """

    def __init__(self, *, ttl_s: float = TOKEN_TTL_S, max_plans: int = MAX_PLANS) -> None:
        self._plans: Dict[str, StoredPlan] = {}
        self._tokens: Dict[str, ApprovalToken] = {}
        self._ttl = ttl_s
        self._max_plans = max_plans
        self._lock = threading.Lock()

    # -- plans -------------------------------------------------------------

    def remember(
        self,
        *,
        plan_id: str,
        session_id: str,
        language: str,
        code: str,
        plan: Optional[Dict[str, Any]] = None,
    ) -> StoredPlan:
        stored = StoredPlan(
            plan_id=plan_id,
            session_id=session_id or "",
            digest=content_digest(language, code),
            created_at=time.time(),
            plan=dict(plan or {}),
        )
        with self._lock:
            self._plans[plan_id] = stored
            self._evict_locked()
        return stored

    def plan(self, plan_id: str) -> Optional[StoredPlan]:
        with self._lock:
            return self._plans.get(plan_id)

    # -- approval ----------------------------------------------------------

    def approve(self, plan_id: str, session_id: str = "") -> Optional[ApprovalToken]:
        """Mint a token for an approved plan, or ``None`` if there is no plan.

        The session must match the one the plan was built for, so a token cannot
        be minted for someone else's plan.
        """
        with self._lock:
            stored = self._plans.get(plan_id)
            if stored is None:
                return None
            if stored.session_id and session_id and stored.session_id != session_id:
                logger.warning(
                    "refusing to approve plan %s for session %s; it belongs to %s",
                    plan_id, session_id, stored.session_id,
                )
                return None

            token = ApprovalToken(
                token=secrets.token_urlsafe(32),
                plan_id=plan_id,
                session_id=session_id or stored.session_id,
                digest=stored.digest,
                expires_at=time.time() + self._ttl,
            )
            self._tokens[token.token] = token
            return token

    def consume(
        self,
        token: str,
        *,
        language: str,
        code: str,
        session_id: str = "",
    ) -> Tuple[bool, str]:
        """Spend a token.  Returns ``(ok, reason)``.

        Single use: replaying a token is refused, because "approve once, run
        repeatedly" is not what the user agreed to.
        """
        with self._lock:
            record = self._tokens.get(token)
            if record is None:
                return False, "unknown or already-used approval token"
            if record.used:
                return False, "this approval token has already been used"
            if time.time() > record.expires_at:
                self._tokens.pop(token, None)
                return False, "this approval has expired; approve the plan again"
            if session_id and record.session_id and record.session_id != session_id:
                return False, "this approval belongs to a different session"
            if record.digest != content_digest(language, code):
                # The important check: the token proves a plan was approved, and
                # this proves the plan is what is about to run.
                return False, "the code does not match the approved plan"

            record.used = True
            self._tokens.pop(token, None)
            self._plans.pop(record.plan_id, None)
            return True, "approved"

    # -- housekeeping ------------------------------------------------------

    def _evict_locked(self) -> None:
        if len(self._plans) <= self._max_plans:
            return
        for plan_id in sorted(self._plans, key=lambda pid: self._plans[pid].created_at)[
            : len(self._plans) - self._max_plans
        ]:
            self._plans.pop(plan_id, None)

    def purge_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [t for t, record in self._tokens.items() if record.expires_at < now]
            for token in expired:
                self._tokens.pop(token, None)
        return len(expired)

    def __len__(self) -> int:
        return len(self._tokens)


_store: Optional[SandboxApprovalStore] = None


def get_store() -> SandboxApprovalStore:
    """The process-wide approval store, created on first use."""
    global _store
    if _store is None:
        _store = SandboxApprovalStore()
    return _store


def reset_store() -> None:
    """Drop the process store.  For tests."""
    global _store
    _store = None


def session_runs_without_asking(session_id: str) -> bool:
    """Whether this session's **persisted** mode is ``auto`` (§8.1).

    Read from the session record, never from a request body: a body-supplied
    ``permission_mode`` is exactly the client-side claim F7 is about.
    """
    if not session_id:
        return False
    try:
        from .session import SessionManager

        session = SessionManager().load(session_id)
    except Exception as exc:  # noqa: BLE001 - no such session, or no store
        logger.debug("could not read the mode for session %s: %s", session_id, exc)
        return False
    return str(getattr(session, "permission_mode", "") or "").strip().lower() == "auto"
