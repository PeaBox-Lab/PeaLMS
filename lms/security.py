"""Transport-level security and observability helpers.

These are deliberately small, dependency-free primitives. A production
deployment terminates TLS at a proxy and keeps the same headers; the rate
limiter is in-process, so a multi-replica deployment must move it to a shared
store (documented in docs/SECURITY.md).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque

logger = logging.getLogger("lms.access")


class RateLimiter:
    """Thread-safe sliding-window limiter keyed by client address."""

    def __init__(self, limit_per_minute: int):
        self.limit = max(0, int(limit_per_minute))
        self.window = 60.0
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        if self.limit == 0:
            return True, 0
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            cutoff = now - self.window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry = max(1, int(self.window - (now - bucket[0])) + 1)
                return False, retry
            bucket.append(now)
        return True, 0

    def reset(self):
        with self._lock:
            self._hits.clear()


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data: https:; media-src 'self'; "
        "frame-src https://www.youtube-nocookie.com https://www.youtube.com; "
        "connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def security_headers(*, tls: bool = False) -> dict[str, str]:
    headers = dict(SECURITY_HEADERS)
    if tls:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def new_request_id() -> str:
    return uuid.uuid4().hex


def configure_logging(level: str = "INFO") -> None:
    if logging.getLogger().handlers:
        logging.getLogger().setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def log_access(*, request_id, method, path, status, duration_ms, remote, actor=None):
    logger.info(json.dumps({
        "request_id": request_id,
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": round(duration_ms, 2),
        "remote": remote,
        "actor": actor,
        "pid": os.getpid(),
    }, separators=(",", ":")))
