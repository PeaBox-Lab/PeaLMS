"""Media storage for course videos, images and documents.

Files are stored outside the static directory and served through short-lived,
HMAC-signed URLs so a learner cannot enumerate or hot-link protected content.
The transport adds HTTP range support for video seeking.

Uploads are validated by both the declared MIME type and magic bytes. The
original filename is kept only as display metadata; the on-disk name is
generated, which removes path-traversal and collision risks.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from .audit import record
from .config import get_settings
from .db import connection, row

ALLOWED_TYPES: dict[str, tuple[str, str]] = {
    "video/mp4": ("video", ".mp4"),
    "video/webm": ("video", ".webm"),
    "video/quicktime": ("video", ".mov"),
    "video/x-matroska": ("video", ".mkv"),
    "image/png": ("image", ".png"),
    "image/jpeg": ("image", ".jpg"),
    "image/gif": ("image", ".gif"),
    "image/webp": ("image", ".webp"),
    "application/pdf": ("file", ".pdf"),
}

SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
DEFAULT_TTL = 3600


def media_dir() -> Path:
    directory = Path(get_settings().media_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _clean_filename(name: str) -> str:
    name = Path(str(name or "")).name.strip()
    name = SAFE_FILENAME.sub("_", name)
    return (name or "upload")[:180]


def _looks_like(content_type: str, data: bytes) -> bool:
    """Reject a file whose bytes contradict the declared type."""
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if content_type == "image/gif":
        return data.startswith(b"GIF87a") or data.startswith(b"GIF89a")
    if content_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if content_type == "video/webm":
        return data.startswith(b"\x1aE\xdf\xa3")
    if content_type in ("video/mp4", "video/quicktime"):
        return len(data) >= 12 and data[4:8] in (b"ftyp", b"moov", b"mdat", b"wide")
    if content_type == "application/pdf":
        return data.startswith(b"%PDF")
    return False


def store(filename: str, content_type: str, data: bytes, uploaded_by: str | None = None) -> dict:
    settings = get_settings()
    content_type = str(content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_TYPES:
        raise ValueError(
            "Unsupported media type. Allowed: " + ", ".join(sorted(ALLOWED_TYPES))
        )
    if not data:
        raise ValueError("The uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise ValueError(
            f"File exceeds the {settings.max_upload_bytes} byte upload limit"
        )
    if not _looks_like(content_type, data):
        raise ValueError("File content does not match the declared media type")

    kind, extension = ALLOWED_TYPES[content_type]
    stored_name = f"{uuid.uuid4().hex}{extension}"
    directory = media_dir()
    target = directory / stored_name
    temporary = directory / (stored_name + ".part")
    temporary.write_bytes(data)
    temporary.replace(target)

    checksum = hashlib.sha256(data).hexdigest()
    with connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO media(filename,stored_name,content_type,size_bytes,kind,checksum,uploaded_by)
            VALUES(?,?,?,?,?,?,?)
            """,
            (_clean_filename(filename), stored_name, content_type, len(data),
             kind, checksum, uploaded_by),
        )
        media_id = int(cur.lastrowid)
        record("MediaUploaded", uploaded_by or "system", "media", media_id,
               {"filename": _clean_filename(filename), "content_type": content_type,
                "size_bytes": len(data), "kind": kind}, conn=conn)
    return get(media_id)


def get(media_id) -> dict | None:
    item = row("SELECT * FROM media WHERE id=?", (int(media_id),))
    if item:
        item["url"] = url_for(item["id"])
    return item


def file_path(item: dict) -> Path:
    directory = media_dir().resolve()
    target = (directory / item["stored_name"]).resolve()
    if directory != target and directory not in target.parents:
        raise ValueError("Invalid media path")
    return target


def _signature(media_id: int, expires: int) -> str:
    secret = get_settings().session_secret.encode()
    return hmac.new(secret, f"{media_id}:{expires}".encode(), hashlib.sha256).hexdigest()


def url_for(media_id, ttl: int = DEFAULT_TTL) -> str:
    expires = int(time.time()) + int(ttl)
    query = urlencode({"exp": expires, "sig": _signature(int(media_id), expires)})
    return f"/media/{int(media_id)}?{query}"


def verify(media_id, expires, signature) -> bool:
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        return False
    if expires < int(time.time()):
        return False
    expected = _signature(int(media_id), expires)
    return hmac.compare_digest(str(signature or ""), expected)


__all__ = ["ALLOWED_TYPES", "file_path", "get", "media_dir", "store", "url_for", "verify"]
