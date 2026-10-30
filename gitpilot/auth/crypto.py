"""Encryption at rest for provider credentials (GitHub / GitLab OAuth tokens).

Tokens are sealed with Fernet (AES-128-CBC + HMAC-SHA256, from ``cryptography``)
before they are written to the session database. The key comes from, in order:

1. ``GITPILOT_TOKEN_ENCRYPTION_KEY`` — one or more comma-separated Fernet keys.
   The first encrypts; all decrypt, so a key can be rotated without logging
   everyone out.
2. ``GITPILOT_AUTH_SECRET`` — when set to a real secret, a key is derived from it
   with HKDF-SHA256.
3. A random key generated once into ``<config dir>/auth/vault.key`` (mode 0600)
   for single-machine installs. A warning is logged: production deployments
   should set one of the variables above so every worker and restart shares it.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger("gitpilot.auth.crypto")

_DEV_SECRET = "gitpilot-dev-only-change-me"
_lock = threading.Lock()
_cipher: Optional[MultiFernet] = None


def auth_dir() -> Path:
    """Directory holding the session database and the generated vault key.

    ``GITPILOT_AUTH_DIR``, else a Hugging Face Space's persistent ``/data``
    volume when attached, else ``<GITPILOT_CONFIG_DIR or ~/.gitpilot>/auth``.
    """
    from .deployment import persistent_auth_dir

    path = persistent_auth_dir()
    if path is None:
        base = os.getenv("GITPILOT_CONFIG_DIR") or str(Path.home() / ".gitpilot")
        path = Path(base) / "auth"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover - Windows / read-only mounts
        pass
    return path


def _derived_key(secret: str) -> bytes:
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"gitpilot-token-vault-v1",
               info=b"provider-credentials").derive(secret.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _file_key() -> bytes:
    path = auth_dir() / "vault.key"
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    logger.warning(
        "Generated a local token-vault key at %s. Set GITPILOT_TOKEN_ENCRYPTION_KEY "
        "(or GITPILOT_AUTH_SECRET) in production so all workers share one key.", path,
    )
    return key


def _keys() -> list[bytes]:
    explicit = [k.strip() for k in (os.getenv("GITPILOT_TOKEN_ENCRYPTION_KEY") or "").split(",") if k.strip()]
    if explicit:
        return [k.encode("ascii") for k in explicit]
    secret = (os.getenv("GITPILOT_AUTH_SECRET") or "").strip()
    if secret and secret != _DEV_SECRET:
        return [_derived_key(secret)]
    return [_file_key()]


def cipher() -> MultiFernet:
    """The process-wide token cipher."""
    global _cipher
    with _lock:
        if _cipher is None:
            _cipher = MultiFernet([Fernet(k) for k in _keys()])
        return _cipher


def reset_cipher() -> None:
    """Forget the cached key (tests, key rotation)."""
    global _cipher
    with _lock:
        _cipher = None


def seal(value: Optional[str]) -> Optional[bytes]:
    """Encrypt a secret; ``None`` stays ``None``."""
    if value is None:
        return None
    return cipher().encrypt(value.encode("utf-8"))


def unseal(blob: Optional[bytes]) -> Optional[str]:
    """Decrypt a secret, or ``None`` if absent or undecryptable (wrong/rotated key)."""
    if not blob:
        return None
    try:
        return cipher().decrypt(bytes(blob)).decode("utf-8")
    except InvalidToken:
        logger.warning("A stored credential could not be decrypted; it will be treated as absent.")
        return None


__all__ = ["auth_dir", "cipher", "reset_cipher", "seal", "unseal"]
