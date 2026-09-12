"""Durable idempotency for mutating agent tools.

Human approval answers *whether* a side effect may happen. Idempotency answers a
different production question: what happens when the approved call is delivered
again because a model, worker, network, or orchestrator retries it?

GitPilot uses a tiny SQLite ledger because it is local, transactional, requires no
new service, and survives process restarts. Reads never touch this module. A
mutation reserves ``(scope, idempotency_key)`` before the side effect, binds the
key to a canonical hash of the exact arguments, and stores the successful result.
A retry with the same key and arguments replays the stored result without calling
the downstream service again.

There is deliberately no automatic retry after a process dies or an exception is
raised while a mutation is in flight. Many downstream APIs (including GitHub's
create-issue / create-PR APIs) do not accept an idempotency header, so after a
lost response the outcome is unknowable. The safe industry pattern is to mark
that key indeterminate and require reconciliation/new approval rather than risk a
duplicate externally-visible action.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

T = TypeVar("T")

DEFAULT_DB_PATH = Path.home() / ".gitpilot" / "idempotency.sqlite3"
MAX_KEY_LENGTH = 256
PENDING_FRESH_SECONDS = 300.0


class IdempotencyError(RuntimeError):
    """Base class for safe-to-surface idempotency failures."""


class IdempotencyConflict(IdempotencyError):
    """A key was reused for a different operation or argument set."""


class IdempotencyInProgress(IdempotencyError):
    """Another worker appears to be executing this key right now."""


class IdempotencyIndeterminate(IdempotencyError):
    """A previous execution may have committed but its result was lost."""


@dataclass(frozen=True)
class Reservation(Generic[T]):
    execute: bool
    result: Optional[T] = None


def _json_default(value: Any) -> str:
    """Stable fallback for values such as ``Path`` without storing secrets."""
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def canonical_fingerprint(scope: str, arguments: Any) -> str:
    """Hash a mutation's identity without persisting its raw arguments."""
    payload = json.dumps(
        {"scope": scope, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_key(key: str) -> str:
    value = str(key or "").strip()
    if not value:
        raise IdempotencyError(
            "idempotency_key is required for mutating tools; use the stable "
            "approval/request id and reuse it for retries"
        )
    if len(value) > MAX_KEY_LENGTH:
        raise IdempotencyError(
            f"idempotency_key is too long ({len(value)} > {MAX_KEY_LENGTH})"
        )
    return value


class IdempotencyStore:
    """Small durable execution ledger backed by SQLite.

    Each method opens a short-lived connection. Mutations are rare compared with
    reads, so this avoids shared-connection/thread hazards while WAL mode and
    ``BEGIN IMMEDIATE`` make concurrent reservations deterministic.
    """

    def __init__(self, path: Optional[Path | str] = None) -> None:
        configured = os.getenv("GITPILOT_IDEMPOTENCY_DB")
        self.path = Path(path or configured or DEFAULT_DB_PATH).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mutation_idempotency (
                scope TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (scope, idempotency_key)
            )
            """
        )
        return conn

    def reserve(
        self,
        *,
        scope: str,
        idempotency_key: str,
        arguments: Any,
    ) -> Reservation[Any]:
        key = _validate_key(idempotency_key)
        fingerprint = canonical_fingerprint(scope, arguments)
        now = time.time()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT fingerprint, status, result_json, error, updated_at "
                "FROM mutation_idempotency WHERE scope = ? AND idempotency_key = ?",
                (scope, key),
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO mutation_idempotency "
                    "(scope, idempotency_key, fingerprint, status, updated_at) "
                    "VALUES (?, ?, ?, 'pending', ?)",
                    (scope, key, fingerprint, now),
                )
                conn.commit()
                return Reservation(execute=True)

            if row["fingerprint"] != fingerprint:
                raise IdempotencyConflict(
                    "idempotency_key was already used for different arguments; "
                    "request a new approval instead of reusing the key"
                )

            status = str(row["status"])
            if status == "completed":
                raw = row["result_json"]
                return Reservation(
                    execute=False,
                    result=json.loads(raw) if raw is not None else None,
                )

            if status == "indeterminate":
                detail = f" ({row['error']})" if row["error"] else ""
                raise IdempotencyIndeterminate(
                    "a previous attempt may have committed but its response was lost"
                    f"{detail}; reconcile the target before requesting a new approval"
                )

            age = max(0.0, now - float(row["updated_at"] or now))
            if age <= PENDING_FRESH_SECONDS:
                raise IdempotencyInProgress(
                    "this approved mutation is already executing; do not issue it again"
                )
            raise IdempotencyIndeterminate(
                "a previous execution started but never recorded a result; "
                "reconcile the target before requesting a new approval"
            )

    def complete(
        self,
        *,
        scope: str,
        idempotency_key: str,
        arguments: Any,
        result: Any,
    ) -> None:
        key = _validate_key(idempotency_key)
        fingerprint = canonical_fingerprint(scope, arguments)
        encoded = json.dumps(result, ensure_ascii=False, default=_json_default)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE mutation_idempotency SET status = 'completed', result_json = ?, "
                "error = NULL, updated_at = ? WHERE scope = ? AND idempotency_key = ? "
                "AND fingerprint = ?",
                (encoded, time.time(), scope, key, fingerprint),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflict(
                    "could not complete idempotency record because its arguments changed"
                )

    def mark_indeterminate(
        self,
        *,
        scope: str,
        idempotency_key: str,
        arguments: Any,
        error: BaseException,
    ) -> None:
        key = _validate_key(idempotency_key)
        fingerprint = canonical_fingerprint(scope, arguments)
        message = f"{type(error).__name__}: {error}"[:500]
        with self._connect() as conn:
            conn.execute(
                "UPDATE mutation_idempotency SET status = 'indeterminate', error = ?, "
                "updated_at = ? WHERE scope = ? AND idempotency_key = ? AND fingerprint = ?",
                (message, time.time(), scope, key, fingerprint),
            )

    def run_once(
        self,
        *,
        scope: str,
        idempotency_key: str,
        arguments: Any,
        operation: Callable[[], T],
    ) -> T:
        reservation = self.reserve(
            scope=scope,
            idempotency_key=idempotency_key,
            arguments=arguments,
        )
        if not reservation.execute:
            return reservation.result  # type: ignore[return-value]

        try:
            result = operation()
        except BaseException as exc:
            # Do not silently retry an externally-visible side effect after an
            # ambiguous failure. The caller surfaces the original error; a
            # later retry of the same key gets the clearer indeterminate message.
            self.mark_indeterminate(
                scope=scope,
                idempotency_key=idempotency_key,
                arguments=arguments,
                error=exc,
            )
            raise

        self.complete(
            scope=scope,
            idempotency_key=idempotency_key,
            arguments=arguments,
            result=result,
        )
        return result

    async def run_once_async(
        self,
        *,
        scope: str,
        idempotency_key: str,
        arguments: Any,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """Async twin used by the production runtime registry."""
        reservation = self.reserve(
            scope=scope,
            idempotency_key=idempotency_key,
            arguments=arguments,
        )
        if not reservation.execute:
            return reservation.result  # type: ignore[return-value]

        try:
            result = await operation()
        except BaseException as exc:
            self.mark_indeterminate(
                scope=scope,
                idempotency_key=idempotency_key,
                arguments=arguments,
                error=exc,
            )
            raise

        self.complete(
            scope=scope,
            idempotency_key=idempotency_key,
            arguments=arguments,
            result=result,
        )
        return result


_default_store: Optional[IdempotencyStore] = None


def get_idempotency_store() -> IdempotencyStore:
    global _default_store
    if _default_store is None:
        _default_store = IdempotencyStore()
    return _default_store


def run_idempotent_mutation(
    *,
    scope: str,
    idempotency_key: str,
    arguments: Any,
    operation: Callable[[], T],
) -> T:
    """Execute one approved mutation at most once for a stable request key."""
    return get_idempotency_store().run_once(
        scope=scope,
        idempotency_key=idempotency_key,
        arguments=arguments,
        operation=operation,
    )


def run_legacy_mutation(
    *,
    scope: str,
    idempotency_key: str = "",
    arguments: Any,
    operation: Callable[[], T],
) -> T:
    """Compatibility bridge for pre-V4 CrewAI mutators.

    Historical CrewAI tools predate the approval/request-id plumbing and are also
    called directly by integrations and parity tests. Breaking those signatures
    would force callers—or worse, small models—to fabricate operational IDs.

    When the legacy caller supplies the real approval/request key, use the same
    durable ledger as the V4 runtime. When it does not, preserve the historical
    one-shot behavior. The production V4 runtime never uses this fallback: its
    :class:`RuntimeToolRegistry` always has the canonical call id and enforces
    durable replay protection there.
    """
    key = str(idempotency_key or "").strip()
    if not key:
        return operation()
    return run_idempotent_mutation(
        scope=scope,
        idempotency_key=key,
        arguments=arguments,
        operation=operation,
    )


async def run_idempotent_mutation_async(
    *,
    scope: str,
    idempotency_key: str,
    arguments: Any,
    operation: Callable[[], Awaitable[T]],
) -> T:
    """Async execution path for canonical agent tools."""
    return await get_idempotency_store().run_once_async(
        scope=scope,
        idempotency_key=idempotency_key,
        arguments=arguments,
        operation=operation,
    )
