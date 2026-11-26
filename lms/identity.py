"""Trusted reverse-proxy identity for enterprise pilots.

A reverse proxy that already performed OIDC/SAML authentication signs a short
assertion with a shared secret. PeaLMS verifies the signature, freshness, replay
nonce and trusted proxy address, then derives the application role from a
server-side group map. No request field can supply a role directly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import threading
import time

from .config import get_settings
from .db import row
from .permissions import ROLE_PERMISSIONS

logger = logging.getLogger("lms.identity")

_ROLE_PRIORITY = (
    "platform_admin", "learning_admin", "compliance_officer", "auditor",
    "instructor", "manager", "learner",
)
_MAX_ASSERTION_BYTES = 8192
_MAX_GROUPS = 64

_nonce_lock = threading.Lock()
_seen_nonces: dict[str, int] = {}


class ProxyIdentityError(ValueError):
    """The signed proxy assertion is missing, invalid or stale."""


def reset_nonce_cache() -> None:
    """Clear replay state; primarily useful between test configurations."""
    with _nonce_lock:
        _seen_nonces.clear()


def sign_assertion(payload: dict, secret: str | None = None) -> str:
    """Create a signed assertion for a trusted proxy or a test harness."""
    if secret is None:
        secret = get_settings().proxy_secret
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(
        secret.encode("utf-8"), encoded, hashlib.sha256
    ).hexdigest().encode("ascii")
    return (encoded + b"." + signature).decode("ascii")


def _trusted_remote(remote: str, networks: tuple[str, ...]) -> bool:
    try:
        address = ipaddress.ip_address(str(remote or "").strip())
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    for value in networks:
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            logger.warning("Ignoring invalid LMS_TRUSTED_PROXIES entry %r", value)
    return False


def _consume_nonce(nonce: str, expires_at: int, now: int) -> None:
    with _nonce_lock:
        expired = [key for key, value in _seen_nonces.items() if value <= now]
        for key in expired:
            _seen_nonces.pop(key, None)
        if nonce in _seen_nonces:
            raise ProxyIdentityError("The proxy assertion has already been used")
        _seen_nonces[nonce] = expires_at


def verify_header(header: str, *, remote: str = "", now: int | None = None) -> dict:
    """Verify a ``X-LMS-Identity`` assertion and derive a server-side role."""
    settings = get_settings()
    if settings.auth_mode not in ("proxy", "hybrid"):
        raise ProxyIdentityError("Proxy authentication is not enabled")
    if not settings.proxy_secret:
        raise ProxyIdentityError("Proxy authentication has no shared secret")
    if not header or len(header) > _MAX_ASSERTION_BYTES:
        raise ProxyIdentityError("Missing or oversized proxy assertion")
    if not _trusted_remote(remote, settings.proxy_trusted_proxies):
        raise ProxyIdentityError("Proxy address is not trusted")
    try:
        encoded, signature = header.encode("ascii").split(b".", 1)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ProxyIdentityError("Malformed proxy assertion") from exc
    expected = hmac.new(
        settings.proxy_secret.encode("utf-8"), encoded, hashlib.sha256
    ).hexdigest().encode("ascii")
    if not hmac.compare_digest(signature, expected):
        raise ProxyIdentityError("Proxy assertion signature is invalid")
    try:
        payload = json.loads(base64.urlsafe_b64decode(
            encoded + b"=" * (-len(encoded) % 4)
        ))
    except Exception as exc:
        raise ProxyIdentityError("Proxy assertion is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ProxyIdentityError("Proxy assertion must be an object")

    current = int(time.time()) if now is None else int(now)
    try:
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProxyIdentityError("Proxy assertion needs numeric iat and exp") from exc
    if expires_at <= current:
        raise ProxyIdentityError("Proxy assertion has expired")
    if issued_at > current + settings.proxy_clock_skew_seconds:
        raise ProxyIdentityError("Proxy assertion is issued in the future")
    if current - issued_at > settings.proxy_clock_skew_seconds:
        raise ProxyIdentityError("Proxy assertion is too old")
    if expires_at - issued_at > 3600:
        raise ProxyIdentityError("Proxy assertion lifetime is too long")

    nonce = str(payload.get("nonce", "")).strip()
    if not 8 <= len(nonce) <= 128:
        raise ProxyIdentityError("Proxy assertion needs a replay nonce")
    _consume_nonce(nonce, expires_at + settings.proxy_nonce_ttl_seconds, current)

    actor = str(payload.get("sub", "")).strip()
    if not actor or len(actor) > 128:
        raise ProxyIdentityError("Proxy assertion needs a subject")
    groups = payload.get("groups", [])
    if not isinstance(groups, list) or len(groups) > _MAX_GROUPS:
        raise ProxyIdentityError("Proxy groups must be a small list")
    clean_groups = [str(value).strip() for value in groups if str(value).strip()]
    if any(len(value) > 128 for value in clean_groups):
        raise ProxyIdentityError("Proxy group name is too long")

    mapped = {
        settings.proxy_role_groups[group]
        for group in clean_groups
        if group in settings.proxy_role_groups
    }
    role = min(mapped, key=_ROLE_PRIORITY.index) if mapped else settings.proxy_default_role
    if role not in ROLE_PERMISSIONS:
        raise ProxyIdentityError("Proxy role mapping produced an unknown role")

    employee = row(
        "SELECT employee_id, work_email FROM employees WHERE employee_id=?", (actor,)
    )
    if not employee:
        raise ProxyIdentityError("Proxy subject is not a known employee")
    return {
        "actor": actor,
        "role": role,
        "source": "proxy",
        "groups": clean_groups,
        "email": employee.get("work_email") or "",
        "auth_time": current,
    }


def from_headers(headers, *, remote: str = "", now: int | None = None) -> dict | None:
    """Return a verified identity when a trusted proxy assertion is present."""
    settings = get_settings()
    if settings.auth_mode not in ("proxy", "hybrid"):
        return None
    header = headers.get(settings.proxy_header, "") if headers else ""
    if not header:
        return None
    try:
        return verify_header(header, remote=remote, now=now)
    except ProxyIdentityError as exc:
        logger.warning("Rejected proxy identity: %s", exc)
        return None


__all__ = [
    "ProxyIdentityError", "from_headers", "reset_nonce_cache",
    "sign_assertion", "verify_header",
]
