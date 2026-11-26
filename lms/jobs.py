"""Durable job ledger and worker primitives.

Jobs are enqueued inside the same transaction as the business change when
possible. A separate worker process claims them with SQLite locking, retries
with backoff and records terminal failures instead of losing work.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from . import audit, notifications, services
from .db import connection

logger = logging.getLogger("lms.jobs")

JOB_TYPES = ("notification_sweep", "policy_apply", "outbox_delivery")
STATUSES = ("queued", "running", "succeeded", "failed")


class UnknownJobType(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _payload(value: dict | None) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        raise ValueError("Job payload must be an object")
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _event(conn, job_id: int, event: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO job_events(job_id,event,detail) VALUES(?,?,?)",
        (int(job_id), event, detail[:1000]),
    )


def enqueue(job_type: str, payload: dict | None = None, *, actor: str = "system",
            run_at: str | None = None, max_attempts: int = 3, conn=None) -> int:
    if job_type not in JOB_TYPES:
        raise UnknownJobType(f"Unknown job type: {job_type}")
    max_attempts = max(1, min(int(max_attempts), 10))
    correlation_id = str(uuid.uuid4())

    def insert(db):
        cur = db.execute(
            """
            INSERT INTO jobs(job_type,payload_json,status,attempts,max_attempts,run_at)
            VALUES(?,?,'queued',0,?,?)
            """,
            (job_type, _payload(payload), max_attempts, run_at or _now()),
        )
        job_id = int(cur.lastrowid)
        _event(db, job_id, "queued", job_type)
        audit.record("JobQueued", actor, "job", job_id,
                     {"job_type": job_type, "payload": payload or {}},
                     correlation_id=correlation_id, conn=db)
        return job_id

    if conn is not None:
        return insert(conn)
    with connection() as db:
        return insert(db)


def claim(worker_id: str | None = None, *, limit: int = 10,
          stale_after_seconds: int = 300) -> list[dict]:
    worker_id = worker_id or secrets.token_hex(8)
    limit = max(1, min(int(limit), 100))
    now = _now()
    stale = _iso(-stale_after_seconds)
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidates = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status='queued' AND run_at<=?
              AND (locked_at IS NULL OR locked_at<=?)
            ORDER BY run_at, id LIMIT ?
            """,
            (now, stale, limit),
        ).fetchall()
        claimed: list[dict] = []
        for candidate in candidates:
            updated = conn.execute(
                """
                UPDATE jobs
                SET status='running', attempts=attempts+1, locked_at=?, locked_by=?,
                    updated_at=?, last_error=NULL
                WHERE id=? AND status='queued'
                """,
                (now, worker_id, now, candidate["id"]),
            )
            if updated.rowcount:
                item = dict(candidate)
                item["attempts"] = int(item["attempts"]) + 1
                item["status"] = "running"
                item["locked_by"] = worker_id
                _event(conn, item["id"], "claimed", worker_id)
                claimed.append(item)
        conn.commit()
    return claimed


def complete(job_id: int, result=None) -> bool:
    now = _now()
    detail = json.dumps(result or {}, sort_keys=True, default=str)[:1000]
    with connection() as conn:
        updated = conn.execute(
            """
            UPDATE jobs SET status='succeeded', completed_at=?, updated_at=?,
                locked_at=NULL, locked_by=NULL, last_error=NULL
            WHERE id=? AND status='running'
            """,
            (now, now, int(job_id)),
        )
        if not updated.rowcount:
            return False
        _event(conn, job_id, "succeeded", detail)
        audit.record("JobSucceeded", "worker", "job", job_id,
                     {"result": result or {}}, conn=conn)
        return True


def fail(job_id: int, error: str, *, attempts: int | None = None) -> bool:
    with connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if not job or job["status"] != "running":
            return False
        used = int(attempts if attempts is not None else job["attempts"])
        maximum = int(job["max_attempts"])
        now = _now()
        if used >= maximum:
            conn.execute(
                """
                UPDATE jobs SET status='failed', last_error=?, updated_at=?,
                    completed_at=?, locked_at=NULL, locked_by=NULL
                WHERE id=?
                """,
                (str(error)[:2000], now, now, int(job_id)),
            )
            event = "failed"
            audit.record("JobFailed", "worker", "job", job_id,
                         {"error": str(error)[:500]}, conn=conn)
        else:
            delay = min(300, 30 * (2 ** max(0, used - 1)))
            conn.execute(
                """
                UPDATE jobs SET status='queued', last_error=?, updated_at=?,
                    run_at=?, locked_at=NULL, locked_by=NULL
                WHERE id=?
                """,
                (str(error)[:2000], now, _iso(delay), int(job_id)),
            )
            event = "retry_scheduled"
        _event(conn, job_id, event, str(error))
        return True


def retry(job_id: int, actor: str = "system") -> bool:
    with connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if not job or job["status"] != "failed":
            return False
        conn.execute(
            """
            UPDATE jobs SET status='queued', attempts=0, run_at=?, updated_at=?,
                completed_at=NULL, locked_at=NULL, locked_by=NULL, last_error=NULL
            WHERE id=?
            """,
            (_now(), _now(), int(job_id)),
        )
        _event(conn, job_id, "retried", actor)
        audit.record("JobRetryQueued", actor, "job", job_id, {}, conn=conn)
        return True


def get_job(job_id: int) -> dict | None:
    with connection() as conn:
        found = conn.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        return dict(found) if found else None


def list_jobs(*, status: str = "", limit: int = 100, offset: int = 0) -> dict:
    if status and status not in STATUSES:
        raise ValueError("Unknown job status")
    where = "WHERE status=?" if status else ""
    params: list = [status] if status else []
    with connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]
        items = [dict(item) for item in conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)],
        ).fetchall()]
    return {"items": items, "total": total, "limit": int(limit), "offset": int(offset)}


def run_job(job: dict) -> dict:
    try:
        payload = json.loads(job.get("payload_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    job_type = job.get("job_type")
    if job_type == "notification_sweep":
        return services.run_notification_sweeps(
            days_soon=int(payload.get("days_soon", 7)),
            days_recert=int(payload.get("days_recert", 30)),
        )
    if job_type == "policy_apply":
        return {
            "created": services.apply_policies(
                payload.get("employee_ids"), actor=payload.get("actor", "worker")
            )
        }
    if job_type == "outbox_delivery":
        return {"delivered": notifications.deliver(limit=int(payload.get("limit", 100)))}
    raise UnknownJobType(f"Unknown job type: {job_type}")


def run_once(worker_id: str | None = None, *, batch_size: int = 10) -> list[dict]:
    results: list[dict] = []
    for job in claim(worker_id, limit=batch_size):
        try:
            result = run_job(job)
        except Exception as exc:  # one bad job must not stop the batch
            logger.exception("Job %s failed", job["id"])
            fail(job["id"], str(exc), attempts=job["attempts"])
            results.append({"id": job["id"], "status": "failed", "error": str(exc)})
        else:
            complete(job["id"], result)
            results.append({"id": job["id"], "status": "succeeded", "result": result})
    return results


__all__ = [
    "JOB_TYPES", "STATUSES", "UnknownJobType", "claim", "complete", "enqueue",
    "fail", "get_job", "list_jobs", "retry", "run_job", "run_once",
]
