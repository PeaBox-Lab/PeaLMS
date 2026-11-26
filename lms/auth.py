"""Session tokens for demo and pilot deployments.

Tokens are signed with HMAC-SHA256, carry a short expiry, and are checked
against a persisted revocation list and session epoch. They are not a
substitute for OIDC/SAML federation; see docs/SECURITY.md.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from .config import get_settings
from .db import connection, row
from .permissions import ROLE_PERMISSIONS

_ISSUER = "corporate-lms"
_EPOCH_KEY = "session_epoch"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def session_epoch() -> int:
    """Current server-side epoch; incrementing it invalidates every token."""
    item = row("SELECT value FROM app_state WHERE key=?", (_EPOCH_KEY,))
    try:
        return int(item["value"]) if item else 0
    except (TypeError, ValueError):
        return 0


def _encode(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    settings = get_settings()
    signature = hmac.new(
        settings.session_secret.encode(), encoded, hashlib.sha256
    ).hexdigest().encode()
    return (encoded + b"." + signature).decode()


def _decode(token: str) -> dict | None:
    """Validate signature and claims without applying revocation checks."""
    try:
        encoded, signature = token.encode().split(b".", 1)
        settings = get_settings()
        expected = hmac.new(
            settings.session_secret.encode(), encoded, hashlib.sha256
        ).hexdigest().encode()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4)))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        if payload.get("iss") != _ISSUER:
            return None
        if payload.get("role") not in ROLE_PERMISSIONS:
            return None
        if not payload.get("actor") or not payload.get("jti"):
            return None
        return payload
    except Exception:
        return None


def issue_token(role: str, actor: str, ttl_seconds: int | None = None,
                *, fingerprint: str = "") -> str:
    settings = get_settings()
    ttl = int(ttl_seconds or settings.session_ttl_seconds)
    now = int(time.time())
    payload = {
        "iss": _ISSUER,
        "sub": actor,
        "actor": actor,
        "role": role,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_hex(16),
        "epoch": session_epoch(),
        "fp": _fingerprint(fingerprint),
    }
    return _encode(payload)


def verify_token(token: str, *, fingerprint: str | None = None) -> dict | None:
    payload = _decode(token)
    if not payload:
        return None
    if int(payload.get("epoch", -1)) != session_epoch():
        return None
    if fingerprint is not None and payload.get("fp") != _fingerprint(fingerprint):
        return None
    if row("SELECT 1 FROM session_revocations WHERE jti=?", (payload["jti"],)):
        return None
    return payload


def revoke_jti(jti: str, actor: str = "") -> bool:
    """Revoke a single session by its token id. Safe to call repeatedly."""
    if not jti:
        return False
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO session_revocations(jti, actor) VALUES(?,?)",
            (str(jti), str(actor or "")),
        )
    return True


def revoke_token(token: str) -> bool:
    payload = _decode(token)
    if not payload:
        return False
    return revoke_jti(payload["jti"], payload["actor"])


def rotate_session_epoch() -> int:
    """Invalidate all outstanding tokens; use after a credential compromise."""
    with connection() as conn:
        conn.execute(
            "UPDATE app_state SET value=CAST(value AS INTEGER)+1, "
            "updated_at=CURRENT_TIMESTAMP WHERE key=?",
            (_EPOCH_KEY,),
        )
        if not conn.execute(
            "SELECT 1 FROM app_state WHERE key=?", (_EPOCH_KEY,)
        ).fetchone():
            conn.execute(
                "INSERT INTO app_state(key, value) VALUES(?, '1')", (_EPOCH_KEY,)
            )
    return session_epoch()
