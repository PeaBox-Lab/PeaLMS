"""Notification outbox and rendering.

Notifications are persisted before delivery so that an operator can audit what
the platform intended to tell a learner or manager. The default transport logs
the message; a production deployment plugs an email or chat transport into
``deliver`` without changing call sites.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from string import Template

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


def deliver(transport=None, *, limit=100) -> int:
    """Deliver queued notifications through a transport.

    ``transport`` receives ``(notification_dict)`` and may raise to signal a
    transient failure; failed rows stay queued for the next sweep.
    """
    transport = transport or _log_transport
    with connection() as conn:
        queued = [dict(r) for r in conn.execute(
            "SELECT * FROM notifications WHERE status='queued' ORDER BY id LIMIT ?",
            (int(limit),),
        ).fetchall()]
    delivered = 0
    for item in queued:
        try:
            transport(item)
        except Exception:  # pragma: no cover - transport specific
            logger.exception("Notification delivery failed for id=%s", item["id"])
            continue
        with connection() as conn:
            conn.execute(
                "UPDATE notifications SET status='sent', sent_at=? WHERE id=?",
                (_now(), item["id"]),
            )
        delivered += 1
    return delivered


def _log_transport(item: dict) -> None:
    logger.info(
        "notification delivered recipient=%s template=%s subject=%r",
        item["recipient_employee_id"], item["template"], item["subject"],
    )
