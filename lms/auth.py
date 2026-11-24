"""Session tokens for demo and pilot deployments.

The tokens are signed with HMAC-SHA256 and carry a short expiry. They are not a
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
from .permissions import ROLE_PERMISSIONS

_ISSUER = "corporate-lms"


def issue_token(role: str, actor: str, ttl_seconds: int | None = None) -> str:
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
        "jti": secrets.token_hex(8),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(
        settings.session_secret.encode(), encoded, hashlib.sha256
    ).hexdigest().encode()
    return (encoded + b"." + signature).decode()


def verify_token(token: str) -> dict | None:
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
        if not payload.get("actor"):
            return None
        return payload
    except Exception:
        return None
