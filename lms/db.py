import sqlite3
from contextlib import contextmanager

from .config import get_settings
from .migrations import SCHEMA_VERSION, applied_versions, migrate


def db_path():
    return get_settings().db_path


@contextmanager
def connection():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connection() as conn:
        migrate(conn)


def schema_version():
    with connection() as conn:
        versions = applied_versions(conn)
        return max(versions) if versions else 0


def rows(query, params=()):
    with connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def row(query, params=()):
    with connection() as conn:
        found = conn.execute(query, params).fetchone()
        return dict(found) if found else None


__all__ = ["SCHEMA_VERSION", "connection", "db_path", "init_db", "row", "rows", "schema_version"]
