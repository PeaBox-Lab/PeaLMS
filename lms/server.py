"""HTTP transport for the LMS.

Kept intentionally thin: parse the request, apply transport controls (rate
limiting, security headers, request ids), delegate to :mod:`lms.api`, write the
response. Business rules live in services.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import tempfile
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import api, identity as proxy_identity, media
from .auth import issue_token as issue_demo_token
from .auth import verify_token
from .config import get_settings
from .db import init_db
from .security import (
    RateLimiter,
    configure_logging,
    log_access,
    new_request_id,
    security_headers,
)
from .seed import seed

__all__ = ["Handler", "LmsServer", "create_server", "issue_demo_token", "main", "verify_token"]

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "CorporateLMS"
    protocol_version = "HTTP/1.1"

    # -- response helpers -------------------------------------------------
    def _write(self, status, payload, content_type="application/json; charset=utf-8",
               extra_headers=None, request_id=""):
        if isinstance(payload, (dict, list)):
            raw = json.dumps(payload, default=str).encode("utf-8")
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if request_id:
            self.send_header("X-Request-ID", request_id)
        for name, value in security_headers(tls=self._is_tls()).items():
            self.send_header(name, value)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _is_tls(self):
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _json_error(self, status, error, message, request_id="", extra_headers=None):
        self._write(status, {"error": error, "message": message},
                    extra_headers=extra_headers, request_id=request_id)

    def _method_not_allowed(self, request_id=""):
        # Do not leave an unread request body on a keep-alive connection.
        self.close_connection = True
        self._json_error(405, "method_not_allowed", "Method not allowed",
                         request_id,
                         extra_headers={"Connection": "close", "Allow": "GET, HEAD"})

    # -- request helpers --------------------------------------------------
    def _identity(self):
        settings = get_settings()
        verified = proxy_identity.from_headers(
            self.headers, remote=self.client_address[0]
        )
        if verified:
            return verified
        if settings.auth_mode == "proxy":
            return None
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return verify_token(
                header[7:].strip(),
                fingerprint=self.headers.get("User-Agent", ""),
            )
        return None

    def _body(self):
        settings = get_settings()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length header")
        if length < 0 or length > settings.max_body_bytes:
            raise OverflowError("Request body exceeds the configured limit")
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object")
        return parsed

    def _stream_body(self, limit):
        """Spool an upload to disk after a small threshold to bound memory use."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length header")
        if length <= 0:
            raise ValueError("An upload body is required")
        if length > limit:
            raise OverflowError(f"Upload exceeds the {limit} byte limit")
        stream = tempfile.SpooledTemporaryFile(max_size=1_048_576, mode="w+b")
        remaining = length
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    raise ValueError("Upload ended before Content-Length was reached")
                stream.write(chunk)
                remaining -= len(chunk)
            stream.seek(0)
            return stream
        except Exception:
            stream.close()
            raise

    def _serve_media(self, path, query, request_id):
        """Serve a signed media object, honouring HTTP range requests."""
        match = re.match(r"^/media/(\d+)$", path)
        if not match:
            self._json_error(404, "not_found", "Resource not found", request_id)
            return 404
        media_id = int(match.group(1))
        if not media.verify(media_id, query.get("exp"), query.get("sig")):
            self._json_error(404, "not_found", "Media link is invalid or expired", request_id)
            return 404
        item = media.get(media_id)
        if not item:
            self._json_error(404, "not_found", "Resource not found", request_id)
            return 404
        try:
            target = media.file_path(item)
        except ValueError:
            self._json_error(404, "not_found", "Resource not found", request_id)
            return 404
        if not target.is_file():
            self._json_error(404, "not_found", "Resource not found", request_id)
            return 404

        size = target.stat().st_size
        start, end, status = 0, max(0, size - 1), 200
        range_header = (self.headers.get("Range") or "").strip()
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            valid = bool(match and (match.group(1) or match.group(2)))
            if valid:
                first, last = match.group(1), match.group(2)
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                    valid = start < size and end >= start
                else:
                    suffix = int(last or 0)
                    valid = suffix > 0 and size > 0
                    start, end = max(0, size - suffix), size - 1
                if valid:
                    end = min(end, size - 1)
                    status = 206
            if not valid:
                self._write(
                    416,
                    {"error": "range_not_satisfiable",
                     "message": "The requested byte range is not satisfiable"},
                    extra_headers={"Content-Range": f"bytes */{size}",
                                   "Cache-Control": "no-store"},
                    request_id=request_id,
                )
                return 416

        length = max(0, end - start + 1) if size else 0
        headers = {"Cache-Control": "private, max-age=300", "Accept-Ranges": "bytes"}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"

        self.send_response(status)
        self.send_header("Content-Type", item["content_type"])
        self.send_header("Content-Length", str(length))
        if request_id:
            self.send_header("X-Request-ID", request_id)
        for name, value in security_headers(tls=self._is_tls()).items():
            self.send_header(name, value)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD" and length:
            with open(target, "rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        return status

    def _serve_static(self, path, request_id):
        settings = get_settings()
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        root = Path(settings.static_dir).resolve()
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            self._json_error(404, "not_found", "Resource not found", request_id)
            return 404
        if not target.is_file():
            self._json_error(404, "not_found", "Resource not found", request_id)
            return 404
        data = target.read_bytes()
        content_type = STATIC_TYPES.get(target.suffix.lower()) or (
            mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        )
        self._write(200, data, content_type=content_type,
                    extra_headers={"Cache-Control": "no-cache"},
                    request_id=request_id)
        return 200

    # -- dispatch ---------------------------------------------------------
    def _handle(self, method):
        started = time.monotonic()
        request_id = new_request_id()
        parsed = urlparse(self.path)
        path = parsed.path
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        remote = self.client_address[0] if self.client_address else "-"
        status = 500
        actor = None
        fingerprint = self.headers.get("User-Agent", "")
        identity = self._identity()
        actor = (identity or {}).get("actor")

        limiter: RateLimiter = self.server.rate_limiter
        allowed, retry_after = limiter.allow(f"{remote}|{actor or '-'}")
        if not allowed:
            status = 429
            self._json_error(429, "rate_limited",
                             "Too many requests; slow down and retry.", request_id,
                             extra_headers={"Retry-After": str(retry_after)})
            self._log(method, path, status, started, remote, actor, request_id)
            return

        if path.startswith("/media/"):
            if method not in ("GET", "HEAD"):
                status = 405
                self._method_not_allowed(request_id)
            else:
                status = self._serve_media(path, query, request_id)
        elif path.startswith("/api"):
            try:
                if method == "POST" and api.is_media_upload(path):
                    with self._stream_body(get_settings().max_upload_bytes) as upload:
                        result = api.handle_media_upload(
                            identity,
                            self.headers.get("Content-Type", ""),
                            self.headers.get("X-Filename", ""),
                            upload,
                            request_id,
                        )
                else:
                    body = self._body() if method in ("POST", "PUT", "PATCH", "DELETE") else {}
                    result = api.handle(method, path, query, body, identity,
                                        request_id=request_id, remote=remote,
                                        fingerprint=fingerprint)
                status = result.status
                self._write(status, result.payload, result.content_type,
                            result.headers, request_id)
            except OverflowError as exc:
                status = 413
                self._json_error(413, "payload_too_large", str(exc), request_id)
            except ValueError as exc:
                status = 400
                self._json_error(400, "validation_error", str(exc), request_id)
            except Exception:
                traceback.print_exc()
                status = 500
                self._json_error(500, "internal_error",
                                 "The operation failed. Check server logs for details.",
                                 request_id)
        elif method == "GET":
            status = self._serve_static(path, request_id)
        else:
            status = 405
            self._method_not_allowed(request_id)

        self._log(method, path, status, started, remote, actor, request_id)

    def _log(self, method, path, status, started, remote, actor, request_id):
        log_access(
            request_id=request_id, method=method, path=path, status=status,
            duration_ms=(time.monotonic() - started) * 1000,
            remote=remote, actor=actor,
        )

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def log_message(self, fmt, *args):  # pragma: no cover - overridden by structured log
        return


class LmsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler=Handler, rate_limit_per_minute=None):
        settings = get_settings()
        if rate_limit_per_minute is None:
            rate_limit_per_minute = settings.rate_limit_per_minute
        self.rate_limiter = RateLimiter(rate_limit_per_minute)
        super().__init__(address, handler)


def create_server(host=None, port=None, *, rate_limit_per_minute=None):
    settings = get_settings()
    server = LmsServer(
        (host or settings.host, int(port if port is not None else settings.port)),
        rate_limit_per_minute=rate_limit_per_minute,
    )
    return server


def main(argv=None):
    settings = get_settings()
    configure_logging(settings.log_level)
    parser = argparse.ArgumentParser(description="Corporate LMS server")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-seed", action="store_true")
    parser.add_argument("--migrate-only", action="store_true",
                        help="Apply database migrations and exit")
    parser.add_argument("--sweep-notifications", action="store_true",
                        help="Run due/overdue/recertification notification sweeps and exit")
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost", "::1") and not settings.allow_remote_demo:
        if settings.demo_mode:
            parser.error(
                "Demo mode may bind only to loopback unless "
                "LMS_ALLOW_REMOTE_DEMO=true is explicitly set"
            )

    init_db()
    if args.migrate_only:
        from .db import schema_version
        print(f"Database schema version: {schema_version()}")
        return 0
    if args.sweep_notifications:
        from . import services
        print(json.dumps(services.run_notification_sweeps(), indent=2))
        return 0
    if not args.no_seed and settings.seed_on_start:
        seed()

    server = create_server(args.host, args.port)
    print(f"Corporate LMS v{settings.version} running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
