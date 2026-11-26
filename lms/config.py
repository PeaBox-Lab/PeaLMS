"""Central, validated configuration for the LMS.

All environment access happens here so that the rest of the code never reads
process state directly. Settings are cached lazily; ``reset_settings`` exists so
tests and embedding applications can reconfigure without reloading modules.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.4.0"
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
STATIC_DIR = PACKAGE_ROOT / "static"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _env(env: dict, name: str, default: str = "") -> str:
    value = env.get(name)
    return default if value is None else str(value).strip()


def _bool(env: dict, name: str, default: bool = False) -> bool:
    raw = _env(env, name, "").lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return default if raw == "" else False
    raise ValueError(f"{name} must be a boolean (true/false), got {raw!r}")


def _int(env: dict, name: str, default: int, minimum: int | None = None,
         maximum: int | None = None) -> int:
    raw = _env(env, name, "")
    if raw == "":
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    version: str = VERSION
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = field(default_factory=lambda: Path.home() / ".corporate-lms")
    db_path: Path = field(default_factory=lambda: Path.home() / ".corporate-lms" / "lms.db")
    media_dir: Path = field(default_factory=lambda: Path.home() / ".corporate-lms" / "media")
    demo_mode: bool = False
    allow_remote_demo: bool = False
    session_secret: str = ""
    session_ttl_seconds: int = 3600
    max_body_bytes: int = 5_000_000
    max_upload_bytes: int = 200_000_000
    rate_limit_per_minute: int = 240
    log_level: str = "INFO"
    seed_on_start: bool = True
    static_dir: Path = STATIC_DIR

    @property
    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    def validate_binding(self) -> None:
        if self.demo_mode and not self.is_loopback and not self.allow_remote_demo:
            raise ValueError(
                "Demo mode may bind only to loopback unless "
                "LMS_ALLOW_REMOTE_DEMO=true is explicitly set"
            )


def load_settings(env: dict | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    data_dir = Path(_env(env, "LMS_DATA_DIR", str(Path.home() / ".corporate-lms"))).expanduser()
    db_path = Path(_env(env, "LMS_DB_PATH", str(data_dir / "lms.db"))).expanduser()
    media_dir = Path(_env(env, "LMS_MEDIA_DIR", str(data_dir / "media"))).expanduser()
    demo_mode = _bool(env, "LMS_DEMO_MODE", False)
    allow_remote = _bool(env, "LMS_ALLOW_REMOTE_DEMO", False)
    secret = _env(env, "LMS_SESSION_SECRET", "")
    if not secret:
        # Ephemeral secret: fine for demos and single-process deployments. A
        # multi-process deployment must set LMS_SESSION_SECRET explicitly.
        secret = secrets.token_hex(32)
    ttl = _int(env, "LMS_SESSION_TTL_SECONDS", 3600, minimum=60, maximum=86400)
    log_level = _env(env, "LMS_LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"LMS_LOG_LEVEL must be a standard level, got {log_level!r}")
    settings = Settings(
        host=_env(env, "LMS_HOST", "127.0.0.1"),
        port=_int(env, "LMS_PORT", 8080, minimum=1, maximum=65535),
        data_dir=data_dir,
        db_path=db_path,
        media_dir=media_dir,
        demo_mode=demo_mode,
        allow_remote_demo=allow_remote,
        session_secret=secret,
        session_ttl_seconds=ttl,
        max_body_bytes=_int(env, "LMS_MAX_BODY_BYTES", 5_000_000, minimum=1024),
        max_upload_bytes=_int(env, "LMS_MAX_UPLOAD_BYTES", 200_000_000, minimum=1024),
        rate_limit_per_minute=_int(env, "LMS_RATE_LIMIT_PER_MINUTE", 240, minimum=0),
        log_level=log_level,
        seed_on_start=_bool(env, "LMS_SEED_ON_START", True),
        static_dir=STATIC_DIR,
    )
    settings.validate_binding()
    return settings


_cached: Settings | None = None


def get_settings() -> Settings:
    global _cached
    if _cached is None:
        _cached = load_settings()
    return _cached


def reset_settings() -> None:
    """Drop the cached settings so the next call re-reads the environment."""
    global _cached
    _cached = None
