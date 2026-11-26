"""Notification outbox and rendering.

Notifications are persisted before delivery so that an operator can audit what
the platform intended to tell a learner or manager. The default transport logs
the message; a production deployment plugs an email or chat transport into
``deliver`` without changing call sites.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from string import Template

from . import email as email_transport
from .config import get_settings
from .db import connection, row, rows

logger = logging.getLogger("lms.notifications")

TEMPLATES: dict[str, dict[str, str]] = {
    "assignment_created": {
        "subject": "New learning assigned: $course_title",
        "body": (
            "Hi $first_name,\n\n"
            "$course_title ($course_code) has been assigned to you. "
            "It is due on $due_date.\n\n"
            "Open the LMS to start the course."
        ),
    },
    "assignment_due_soon": {
        "subject": "Learning due soon: $course_title",
        "body": (
            "Hi $first_name,\n\n"
            "$course_title ($course_code) is due on $due_date. "
            "Please complete it before the deadline."
        ),
    },
    "assignment_overdue": {
        "subject": "Overdue learning: $course_title",
        "body": (
            "Hi $first_name,\n\n"
            "$course_title ($course_code) was due on $due_date and is now overdue. "
            "Complete it as soon as possible."
        ),
    },
    "completion_recorded": {
        "subject": "Learning completed: $course_title",
        "body": (
            "Hi $first_name,\n\n"
            "Your completion of $course_title ($course_code) has been recorded"
            "$score_clause."
        ),
    },
    "completion_recorded_manager": {
        "subject": "$employee_name completed $course_title",
        "body": (
            "$employee_name completed $course_title ($course_code)"
            "$score_clause on $completed_on."
        ),
    },
    "recertification_due": {
        "subject": "Recertification due: $course_title",
        "body": (
            "Hi $first_name,\n\n"
            "Your certification for $course_title ($course_code) expires on "
            "$recert_due_date. Please recertify before then."
        ),
    },
    "exemption_granted": {
        "subject": "Exemption recorded: $course_title",
        "body": (
            "Hi $first_name,\n\n"
            "An exemption was recorded for $course_title ($course_code).\n"
            "Reason: $reason"
        ),
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def render(template: str, context: dict) -> tuple[str, str]:
    spec = TEMPLATES.get(template)
    if spec is None:
        raise ValueError(f"Unknown notification template: {template}")
    safe = {key: "" if value is None else str(value) for key, value in context.items()}
    return (
        Template(spec["subject"]).safe_substitute(safe),
        Template(spec["body"]).safe_substitute(safe),
    )


def queue(conn, recipient_employee_id, template, context, *,
          channel="in_app", entity_type=None, entity_id=None,
          correlation_id=None) -> int | None:
    """Persist a notification inside an existing transaction.

    Returns ``None`` when the recipient is unknown so callers do not fail an
    otherwise valid business transaction because of a missing mailbox.
    """
    if not recipient_employee_id:
        return None
    exists = conn.execute(
        "SELECT 1 FROM employees WHERE employee_id=?", (recipient_employee_id,)
    ).fetchone()
    if not exists:
        return None
    subject, body = render(template, context)
    cur = conn.execute(
        """
        INSERT INTO notifications
          (recipient_employee_id, template, channel, subject, body, status,
           entity_type, entity_id, correlation_id)
        VALUES(?,?,?,?,?,'queued',?,?,?)
        """,
        (recipient_employee_id, template, channel, subject, body,
         entity_type, None if entity_id is None else str(entity_id), correlation_id),
    )
    return int(cur.lastrowid)


def list_for(employee_id, *, unread_only=False, limit=50, offset=0):
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where = "WHERE recipient_employee_id=?"
    params: list = [employee_id]
    if unread_only:
        where += " AND read_at IS NULL"
    with connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM notifications {where}", params
        ).fetchone()[0]
        items = [dict(r) for r in conn.execute(
            f"""
            SELECT id, recipient_employee_id, template, channel, subject, body,
                   status, entity_type, entity_id, created_at, sent_at, read_at
            FROM notifications {where}
            ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def unread_count(employee_id) -> int:
    found = row(
        "SELECT COUNT(*) AS count FROM notifications "
        "WHERE recipient_employee_id=? AND read_at IS NULL",
        (employee_id,),
    )
    return int(found["count"]) if found else 0


def mark_read(notification_id, employee_id) -> bool:
    with connection() as conn:
        cur = conn.execute(
            "UPDATE notifications SET read_at=? "
            "WHERE id=? AND recipient_employee_id=? AND read_at IS NULL",
            (_now(), int(notification_id), employee_id),
        )
        return bool(cur.rowcount)


def mark_all_read(employee_id) -> int:
    with connection() as conn:
        cur = conn.execute(
            "UPDATE notifications SET read_at=? "
            "WHERE recipient_employee_id=? AND read_at IS NULL",
            (_now(), employee_id),
        )
        return int(cur.rowcount)


def _record_delivery(conn, item: dict, status: str, attempts: int, error: str,
                     transport_name: str) -> None:
    conn.execute(
        """
        INSERT INTO notification_deliveries
          (notification_id, channel, transport, status, attempts, last_error)
        VALUES(?,?,?,?,?,?)
        """,
        (item["id"], item.get("channel", "in_app"), transport_name,
         status, attempts, (error or "")[:1000]),
    )


def deliver(transport=None, *, limit=100, max_attempts=None) -> int:
    """Deliver due notifications with bounded retries and a dead-letter trail.

    ``transport`` receives ``(notification_dict)`` and may raise to signal a
    failure. Without an explicit transport, SMTP is used when configured and a
    structured log transport is used otherwise.
    """
    settings = get_settings()
    transport = transport or (
        email_transport.smtp_transport if settings.smtp_enabled else _log_transport
    )
    transport_name = getattr(transport, "__name__", "custom")
    maximum = int(max_attempts or settings.smtp_max_attempts)
    now = _now()
    with connection() as conn:
        queued = [dict(r) for r in conn.execute(
            """
            SELECT * FROM notifications
            WHERE status='queued'
              AND (next_attempt_at IS NULL OR next_attempt_at<=?)
            ORDER BY id LIMIT ?
            """,
            (now, int(limit)),
        ).fetchall()]
    delivered = 0
    for item in queued:
        attempts = int(item.get("attempts") or 0) + 1
        error = ""
        try:
            transport(item)
        except Exception as exc:
            error = str(exc)
            logger.warning("Notification delivery failed id=%s: %s", item["id"], error)
            if attempts >= maximum:
                status = "dead"
                next_attempt = None
            else:
                status = "queued"
                delay = min(300, 30 * (2 ** max(0, attempts - 1)))
                next_attempt = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()
        else:
            status = "sent"
            next_attempt = None
            delivered += 1
        with connection() as conn:
            conn.execute(
                """
                UPDATE notifications
                SET status=?, attempts=?, last_error=?, next_attempt_at=?, sent_at=?
                WHERE id=?
                """,
                (status, attempts, error[:1000] or None, next_attempt,
                 now if status == "sent" else None, item["id"]),
            )
            _record_delivery(
                conn, item,
                "sent" if status == "sent" else ("dead" if status == "dead" else "failed"),
                attempts, error, transport_name,
            )
    return delivered


def list_deliveries(*, status: str = "", limit: int = 100, offset: int = 0) -> dict:
    where = "WHERE d.status=?" if status else ""
    params: list = [status] if status else []
    with connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM notification_deliveries d {where}", params
        ).fetchone()[0]
        items = [dict(item) for item in conn.execute(
            f"""
            SELECT d.*, n.template, n.recipient_employee_id, n.subject
            FROM notification_deliveries d
            JOIN notifications n ON n.id=d.notification_id
            {where}
            ORDER BY d.id DESC LIMIT ? OFFSET ?
            """,
            params + [int(limit), int(offset)],
        ).fetchall()]
    return {"items": items, "total": total, "limit": int(limit), "offset": int(offset)}


def _log_transport(item: dict) -> None:
    logger.info(
        "notification delivered recipient=%s template=%s subject=%r",
        item["recipient_employee_id"], item["template"], item["subject"],
    )
