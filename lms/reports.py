"""Read-only reporting and export helpers."""

from __future__ import annotations

import csv
import io

from .db import connection

EXPORT_COLUMNS = [
    "employee_id", "display_name", "department", "manager_employee_id",
    "course_code", "course_title", "cycle", "status", "assigned_at",
    "started_at", "due_date", "completed_at", "score", "recert_due_date",
    "assignment_source",
]


def _fetch(employee_ids=None, department=None):
    clauses, params = [], []
    if employee_ids is not None:
        if not employee_ids:
            return []
        clauses.append("a.employee_id IN (%s)" % ",".join("?" for _ in employee_ids))
        params.extend(employee_ids)
    if department:
        clauses.append("e.department = ?")
        params.append(department)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connection() as conn:
        return [dict(r) for r in conn.execute(
            f"""
            SELECT a.employee_id, e.display_name, e.department, e.manager_employee_id,
                   a.course_code, c.title course_title, a.cycle, a.status,
                   a.assigned_at, a.started_at, a.due_date, a.completed_at, a.score,
                   a.recert_due_date, a.assignment_source
            FROM assignments a
            JOIN employees e ON e.employee_id=a.employee_id
            JOIN courses c ON c.course_code=a.course_code
            {where}
            ORDER BY e.display_name, a.course_code, a.cycle
            """,
            params,
        ).fetchall()]


def _csv_safe(value):
    """Neutralise spreadsheet formula injection from HRIS-controlled text."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    text = value.lstrip("\ufeff")
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def compliance_csv(employee_ids=None, department=None) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in _fetch(employee_ids, department):
        writer.writerow({
            key: _csv_safe(row.get(key)) for key in EXPORT_COLUMNS
        })
    return buffer.getvalue()


def compliance_rows(employee_ids=None, department=None):
    return _fetch(employee_ids, department)
