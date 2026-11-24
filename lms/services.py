"""Domain services.

The module is the single place where business rules live. HTTP handlers should
stay thin: parse input, check permission, call a service, serialise the result.

Conventions
-----------
* Every mutating function writes an audit event in the same transaction.
* Functions that mutate data accept an optional ``conn`` so callers can compose
  a larger transaction (the HRIS pipeline does this).
* ``ValueError`` means a validation or state conflict and maps to HTTP 400/409.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from .audit import record
from .db import connection, row, rows
from . import notifications

EMPLOYEE_FIELDS = [
    "employee_id", "user_name", "first_name", "last_name", "display_name", "work_email",
    "employment_status", "hire_date", "termination_date", "manager_employee_id", "legal_entity",
    "business_unit", "department", "location", "job_code", "job_title", "job_family", "grade",
    "worker_type", "country", "time_zone", "locale", "cost_center", "source_last_modified_at",
]

ASSIGNMENT_STATUSES = ("assigned", "in_progress", "completed", "failed", "exempted", "expired")
ACTIVE_STATUSES = ("assigned", "in_progress", "failed")
POLICY_OPERATORS = ("equals", "not_equals", "contains", "not_contains", "in", "not_in", "starts_with")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> date:
    return date.today()


def _parse_date(value, field="date"):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc


def _score_clause(score):
    if score is None:
        return ""
    return f" with a score of {float(score):g}"


def _page(total, items, limit, offset):
    return {"items": items, "total": int(total), "limit": int(limit), "offset": int(offset)}


@contextmanager
def _maybe_connection(conn):
    """Reuse a caller's transaction, or open and commit a new one."""
    if conn is not None:
        yield conn
        return
    with connection() as own:
        yield own


# ---------------------------------------------------------------------------
# People and organization
# ---------------------------------------------------------------------------

def list_employees():
    result = rows("SELECT * FROM employees ORDER BY display_name")
    role_rows = rows("SELECT employee_id, role, scope FROM employee_roles ORDER BY role")
    by_emp: dict[str, list] = {}
    for item in role_rows:
        by_emp.setdefault(item["employee_id"], []).append(
            {"role": item["role"], "scope": item["scope"]}
        )
    for item in result:
        item["roles"] = by_emp.get(item["employee_id"], [])
    return result


def search_employees(q=None, department=None, status=None, role=None, limit=50, offset=0):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    clauses, params = [], []
    if q:
        clauses.append("(display_name LIKE ? OR employee_id LIKE ? OR user_name LIKE ? OR work_email LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like])
    if department:
        clauses.append("department = ?")
        params.append(department)
    if status:
        clauses.append("employment_status = ?")
        params.append(status)
    if role:
        clauses.append("EXISTS (SELECT 1 FROM employee_roles r WHERE r.employee_id = employees.employee_id AND r.role = ?)")
        params.append(role)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM employees{where}", params).fetchone()[0]
        items = [dict(r) for r in conn.execute(
            f"SELECT * FROM employees{where} ORDER BY display_name LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()]
        roles = [dict(r) for r in conn.execute(
            "SELECT employee_id, role, scope FROM employee_roles ORDER BY role"
        ).fetchall()]
    by_emp: dict[str, list] = {}
    for item in roles:
        by_emp.setdefault(item["employee_id"], []).append(
            {"role": item["role"], "scope": item["scope"]}
        )
    for item in items:
        item["roles"] = by_emp.get(item["employee_id"], [])
    return _page(total, items, limit, offset)


def get_employee(employee_id):
    emp = row("SELECT * FROM employees WHERE employee_id=?", (employee_id,))
    if emp:
        emp["roles"] = rows(
            "SELECT role,scope FROM employee_roles WHERE employee_id=? ORDER BY role",
            (employee_id,),
        )
    return emp


def create_employee(data, actor="admin"):
    required = ["employee_id", "user_name", "first_name", "last_name"]
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))
    clean = {k: str(data.get(k, "") or "").strip() for k in EMPLOYEE_FIELDS}
    clean["display_name"] = clean["display_name"] or f"{clean['first_name']} {clean['last_name']}"
    clean["employment_status"] = clean["employment_status"] or "active"
    if clean["manager_employee_id"] == clean["employee_id"]:
        raise ValueError("An employee cannot be their own manager")
    with connection() as conn:
        cols = ",".join(EMPLOYEE_FIELDS)
        marks = ",".join("?" for _ in EMPLOYEE_FIELDS)
        conn.execute(
            f"INSERT INTO employees({cols}) VALUES({marks})",
            [clean[k] for k in EMPLOYEE_FIELDS],
        )
        conn.execute(
            "INSERT OR IGNORE INTO employee_roles(employee_id,role,scope,assigned_by) VALUES(?,?,?,?)",
            (clean["employee_id"], "learner", "self", actor),
        )
        record("EmployeeCreated", actor, "employee", clean["employee_id"], clean, conn=conn)
    return get_employee(clean["employee_id"])


def manager_subtree(manager_employee_id) -> list[str]:
    """All employee ids reporting to the manager, at any depth."""
    result = rows(
        """
        WITH RECURSIVE team(employee_id) AS (
          SELECT employee_id FROM employees WHERE manager_employee_id = ?
          UNION
          SELECT e.employee_id FROM employees e
          JOIN team t ON e.manager_employee_id = t.employee_id
        )
        SELECT employee_id FROM team
        """,
        (manager_employee_id,),
    )
    return [item["employee_id"] for item in result]


def is_manager_of(manager_employee_id, employee_id) -> bool:
    if not manager_employee_id or not employee_id:
        return False
    if manager_employee_id == employee_id:
        return True
    return employee_id in set(manager_subtree(manager_employee_id))


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def list_courses(active_only=False):
    where = " WHERE active=1" if active_only else ""
    return rows(f"SELECT * FROM courses{where} ORDER BY active DESC, title")


def get_course(course_code):
    return row("SELECT * FROM courses WHERE course_code=?", (str(course_code).upper(),))


def create_course(data, actor="admin"):
    code = str(data.get("course_code", "")).strip().upper()
    title = str(data.get("title", "")).strip()
    if not code or not title:
        raise ValueError("course_code and title are required")
    duration = int(data.get("duration_minutes", 30))
    if duration < 1 or duration > 100_000:
        raise ValueError("duration_minutes must be between 1 and 100000")
    recert = int(data.get("recertification_days", 0) or 0)
    if recert < 0:
        raise ValueError("recertification_days cannot be negative")
    passing = data.get("passing_score")
    if passing in (None, ""):
        passing = None
    else:
        passing = float(passing)
        if passing < 0 or passing > 100:
            raise ValueError("passing_score must be between 0 and 100")
    status = str(data.get("status", "published")).strip().lower()
    if status not in ("draft", "published", "archived"):
        raise ValueError("status must be draft, published or archived")
    level = str(data.get("level", "All levels")).strip()
    if level not in ("Beginner", "Intermediate", "Advanced", "All levels"):
        raise ValueError("level must be Beginner, Intermediate, Advanced or All levels")
    thumbnail_media_id = data.get("thumbnail_media_id")
    if thumbnail_media_id in (None, "", 0):
        thumbnail_media_id = None
    else:
        thumbnail_media_id = int(thumbnail_media_id)
    values = (
        code, title, str(data.get("description", "")), str(data.get("category", "General")),
        duration, 1 if data.get("mandatory") else 0, str(data.get("version", "1.0")),
        recert, passing, str(data.get("content_url", "")),
        str(data.get("summary", "")), level, str(data.get("language", "en")),
        str(data.get("thumbnail_url", "")), thumbnail_media_id,
        str(data.get("author", "")), str(data.get("objectives", "")),
        str(data.get("tags", "")), status, str(data.get("prerequisite_codes", "")),
        actor,
    )
    with connection() as conn:
        if thumbnail_media_id is not None:
            thumb = conn.execute(
                "SELECT kind FROM media WHERE id=?", (thumbnail_media_id,)
            ).fetchone()
            if not thumb or thumb["kind"] != "image":
                raise ValueError("thumbnail_media_id must reference an uploaded image")
        conn.execute(
            """
            INSERT INTO courses
              (course_code,title,description,category,duration_minutes,mandatory,
               version,recertification_days,passing_score,content_url,summary,level,
               language,thumbnail_url,thumbnail_media_id,author,objectives,tags,
               status,prerequisite_codes,updated_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            values,
        )
        record("CourseCreated", actor, "course", code, dict(data), conn=conn)
    return get_course(code)


# ---------------------------------------------------------------------------
# Assignment policies
# ---------------------------------------------------------------------------

def create_policy(data, actor="admin"):
    required = ["name", "course_code", "attribute", "value"]
    missing = [x for x in required if not str(data.get(x, "")).strip()]
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))
    operator = str(data.get("operator", "equals"))
    if operator not in POLICY_OPERATORS:
        raise ValueError(f"operator must be one of: {', '.join(POLICY_OPERATORS)}")
    course_code = str(data["course_code"]).strip().upper()
    if not get_course(course_code):
        raise ValueError(f"Unknown course: {course_code}")
    due_days = int(data.get("due_days", 30))
    if due_days < 0 or due_days > 3650:
        raise ValueError("due_days must be between 0 and 3650")
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO assignment_policies(name,course_code,attribute,operator,value,due_days) VALUES(?,?,?,?,?,?)",
            (data["name"], course_code, data["attribute"], operator, data["value"], due_days),
        )
        pid = cur.lastrowid
        record("AssignmentPolicyCreated", actor, "assignment_policy", pid, data, conn=conn)
    return row("SELECT * FROM assignment_policies WHERE id=?", (pid,))


def list_policies():
    return rows(
        "SELECT p.*, c.title course_title FROM assignment_policies p "
        "JOIN courses c ON c.course_code=p.course_code ORDER BY p.id DESC"
    )


def policy_matches(emp, policy) -> bool:
    actual = str(emp.get(policy["attribute"], "") or "").strip().lower()
    expected = str(policy["value"]).strip().lower()
    op = policy.get("operator", "equals")
    if op == "equals":
        return actual == expected
    if op == "not_equals":
        return actual != expected
    if op == "contains":
        return expected in actual
    if op == "not_contains":
        return expected not in actual
    if op == "in":
        return actual in [x.strip().lower() for x in expected.split("|")]
    if op == "not_in":
        return actual not in [x.strip().lower() for x in expected.split("|")]
    if op == "starts_with":
        return actual.startswith(expected)
    return False


def apply_policies(employee_ids=None, actor="system", conn=None):
    with _maybe_connection(conn) as db:
        query = "SELECT * FROM employees WHERE employment_status='active'"
        params: list = []
        if employee_ids:
            query += " AND employee_id IN (%s)" % ",".join("?" for _ in employee_ids)
            params = list(employee_ids)
        employees = [dict(r) for r in db.execute(query, params).fetchall()]
        policies = [dict(r) for r in db.execute(
            "SELECT * FROM assignment_policies WHERE active=1"
        ).fetchall()]
        courses = {r["course_code"]: dict(r) for r in db.execute("SELECT * FROM courses").fetchall()}
        created = 0
        for emp in employees:
            for pol in policies:
                if not policy_matches(emp, pol):
                    continue
                exists = db.execute(
                    "SELECT 1 FROM assignments WHERE employee_id=? AND course_code=? LIMIT 1",
                    (emp["employee_id"], pol["course_code"]),
                ).fetchone()
                if exists:
                    continue
                due = (today() + timedelta(days=int(pol["due_days"]))).isoformat()
                cur = db.execute(
                    """
                    INSERT INTO assignments(employee_id,course_code,cycle,due_date,assignment_source,policy_id)
                    VALUES(?,?,1,?,'policy',?)
                    """,
                    (emp["employee_id"], pol["course_code"], due, pol["id"]),
                )
                if not cur.rowcount:
                    continue
                created += 1
                entity = f"{emp['employee_id']}:{pol['course_code']}"
                record("AssignmentCreated", actor, "assignment", entity,
                       {"policy_id": pol["id"], "due_date": due, "cycle": 1}, conn=db)
                course = courses.get(pol["course_code"], {})
                notifications.queue(
                    db, emp["employee_id"], "assignment_created",
                    {
                        "first_name": emp.get("first_name") or emp["display_name"],
                        "course_title": course.get("title", pol["course_code"]),
                        "course_code": pol["course_code"],
                        "due_date": due,
                    },
                    entity_type="assignment", entity_id=entity,
                )
        return created


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

def _latest_assignment(conn, employee_id, course_code):
    return conn.execute(
        "SELECT * FROM assignments WHERE employee_id=? AND course_code=? "
        "ORDER BY cycle DESC LIMIT 1",
        (employee_id, course_code),
    ).fetchone()


def assign_course(employee_id, course_code, due_days=30, actor="admin", conn=None,
                  source="manual"):
    course_code = str(course_code).strip().upper()
    due = (today() + timedelta(days=int(due_days))).isoformat()
    with _maybe_connection(conn) as db:
        employee = db.execute(
            "SELECT * FROM employees WHERE employee_id=?", (employee_id,)
        ).fetchone()
        if not employee:
            raise ValueError(f"Unknown employee: {employee_id}")
        course = db.execute(
            "SELECT * FROM courses WHERE course_code=?", (course_code,)
        ).fetchone()
        if not course:
            raise ValueError(f"Unknown course: {course_code}")
        latest = _latest_assignment(db, employee_id, course_code)
        if latest and latest["status"] in ACTIVE_STATUSES:
            raise ValueError("An active assignment already exists for this employee and course")
        cycle = (latest["cycle"] + 1) if latest else 1
        cur = db.execute(
            """
            INSERT INTO assignments(employee_id,course_code,cycle,due_date,assignment_source)
            VALUES(?,?,?,?,?)
            """,
            (employee_id, course_code, cycle, due, source),
        )
        assignment_id = cur.lastrowid
        if cycle > 1:
            # A new certification cycle starts with a clean content slate.
            db.execute(
                "DELETE FROM lesson_progress WHERE employee_id=? AND lesson_id IN "
                "(SELECT id FROM lessons WHERE course_code=?)",
                (employee_id, course_code),
            )
        entity = f"{employee_id}:{course_code}"
        record("AssignmentCreated", actor, "assignment", entity,
               {"due_date": due, "source": source, "cycle": cycle}, conn=db)
        notifications.queue(
            db, employee_id, "assignment_created",
            {
                "first_name": employee["first_name"] or employee["display_name"],
                "course_title": course["title"],
                "course_code": course_code,
                "due_date": due,
            },
            entity_type="assignment", entity_id=f"{entity}:{cycle}",
        )
    return row("SELECT * FROM assignments WHERE id=?", (assignment_id,))


def start_assignment(employee_id, course_code, actor=None):
    actor = actor or employee_id
    with connection() as conn:
        assignment = _latest_assignment(conn, employee_id, course_code)
        if not assignment:
            raise ValueError("Assignment not found")
        if assignment["status"] == "completed":
            raise ValueError("Assignment is already completed")
        if assignment["status"] == "exempted":
            raise ValueError("Assignment is exempted")
        if assignment["status"] == "in_progress":
            return dict(assignment)
        conn.execute(
            "UPDATE assignments SET status='in_progress', started_at=?, attempts=attempts+1, "
            "updated_at=? WHERE id=?",
            (utc_now(), utc_now(), assignment["id"]),
        )
        record("LearningStarted", actor, "assignment",
               f"{employee_id}:{course_code}",
               {"cycle": assignment["cycle"]}, conn=conn)
    return row("SELECT * FROM assignments WHERE id=?", (assignment["id"],))


def complete_assignment(employee_id, course_code, score=None, actor=None):
    actor = actor or employee_id
    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError) as exc:
            raise ValueError("Score must be numeric") from exc
        if score < 0 or score > 100:
            raise ValueError("Score must be between 0 and 100")
    with connection() as conn:
        assignment = _latest_assignment(conn, employee_id, course_code)
        if not assignment:
            raise ValueError("Assignment not found")
        if assignment["status"] == "completed":
            raise ValueError("Assignment is already completed")
        if assignment["status"] == "exempted":
            raise ValueError("Assignment is exempted")
        course = conn.execute(
            "SELECT * FROM courses WHERE course_code=?", (course_code,)
        ).fetchone()
        employee = conn.execute(
            "SELECT * FROM employees WHERE employee_id=?", (employee_id,)
        ).fetchone()
        passing = course["passing_score"] if course else None
        passed = passing is None or score is None or score >= passing
        status = "completed" if passed else "failed"
        completed_at = utc_now()
        recert_due = None
        if status == "completed" and course and int(course["recertification_days"] or 0) > 0:
            recert_due = (today() + timedelta(days=int(course["recertification_days"]))).isoformat()
        conn.execute(
            """
            UPDATE assignments
            SET status=?, completed_at=?, score=?, attempts=MAX(attempts,1),
                recert_due_date=?, completed_by=?, updated_at=?
            WHERE id=?
            """,
            (status, completed_at, score, recert_due, actor, completed_at, assignment["id"]),
        )
        entity = f"{employee_id}:{course_code}"
        record(
            "LearningCompleted" if status == "completed" else "LearningFailed",
            actor, "assignment", entity,
            {"score": score, "cycle": assignment["cycle"], "recert_due_date": recert_due},
            conn=conn,
        )
        if status == "completed" and employee and course:
            context = {
                "first_name": employee["first_name"] or employee["display_name"],
                "course_title": course["title"],
                "course_code": course_code,
                "score_clause": _score_clause(score),
                "completed_on": completed_at[:10],
            }
            notifications.queue(conn, employee_id, "completion_recorded", context,
                                entity_type="assignment", entity_id=entity)
            if employee["manager_employee_id"]:
                notifications.queue(
                    conn, employee["manager_employee_id"], "completion_recorded_manager",
                    {
                        **context,
                        "employee_name": employee["display_name"],
                    },
                    entity_type="assignment", entity_id=entity,
                )
    return row("SELECT * FROM assignments WHERE id=?", (assignment["id"],))


def exempt_assignment(employee_id, course_code, reason, actor):
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("An exemption reason is required")
    with connection() as conn:
        assignment = _latest_assignment(conn, employee_id, course_code)
        if not assignment:
            raise ValueError("Assignment not found")
        if assignment["status"] == "completed":
            raise ValueError("A completed assignment cannot be exempted")
        if assignment["status"] == "exempted":
            raise ValueError("Assignment is already exempted")
        conn.execute(
            "UPDATE assignments SET status='exempted', exempted=1, exemption_reason=?, "
            "updated_at=? WHERE id=?",
            (reason, utc_now(), assignment["id"]),
        )
        entity = f"{employee_id}:{course_code}"
        record("AssignmentExempted", actor, "assignment", entity,
               {"reason": reason, "cycle": assignment["cycle"]}, conn=conn)
        employee = conn.execute(
            "SELECT * FROM employees WHERE employee_id=?", (employee_id,)
        ).fetchone()
        course = conn.execute(
            "SELECT * FROM courses WHERE course_code=?", (course_code,)
        ).fetchone()
        if employee and course:
            notifications.queue(
                conn, employee_id, "exemption_granted",
                {
                    "first_name": employee["first_name"] or employee["display_name"],
                    "course_title": course["title"],
                    "course_code": course_code,
                    "reason": reason,
                },
                entity_type="assignment", entity_id=entity,
            )
    return row("SELECT * FROM assignments WHERE id=?", (assignment["id"],))


def extend_due_date(employee_id, course_code, due_date, actor):
    new_due = _parse_date(due_date, "due_date")
    with connection() as conn:
        assignment = _latest_assignment(conn, employee_id, course_code)
        if not assignment:
            raise ValueError("Assignment not found")
        if assignment["status"] in ("completed", "exempted"):
            raise ValueError("A closed assignment cannot be extended")
        previous = assignment["due_date"]
        conn.execute(
            "UPDATE assignments SET due_date=?, updated_at=? WHERE id=?",
            (new_due.isoformat(), utc_now(), assignment["id"]),
        )
        entity = f"{employee_id}:{course_code}"
        record("AssignmentDueDateExtended", actor, "assignment", entity,
               {"from": previous, "to": new_due.isoformat()}, conn=conn)
    return row("SELECT * FROM assignments WHERE id=?", (assignment["id"],))


def reassign_course(employee_id, course_code, due_days=30, actor="admin"):
    """Create the next certification cycle for a closed assignment."""
    with connection() as conn:
        latest = _latest_assignment(conn, employee_id, course_code)
        if latest and latest["status"] in ACTIVE_STATUSES:
            raise ValueError("An active assignment already exists for this employee and course")
    return assign_course(employee_id, course_code, due_days, actor)


def list_assignments(employee_id=None):
    q = """
        SELECT a.*, e.display_name, e.department, c.title course_title, c.category,
               c.duration_minutes, c.recertification_days, c.passing_score
        FROM assignments a
        JOIN employees e ON e.employee_id=a.employee_id
        JOIN courses c ON c.course_code=a.course_code
    """
    params: tuple = ()
    if employee_id:
        q += " WHERE a.employee_id=?"
        params = (employee_id,)
    q += """
        ORDER BY CASE a.status
                   WHEN 'assigned' THEN 0 WHEN 'in_progress' THEN 1
                   WHEN 'failed' THEN 2 ELSE 3 END,
                 a.due_date
    """
    return rows(q, params)


def search_assignments(employee_id=None, status=None, course_code=None,
                       department=None, overdue=None, q=None, limit=100, offset=0):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    clauses, params = [], []
    if employee_id:
        clauses.append("a.employee_id = ?")
        params.append(employee_id)
    if status:
        clauses.append("a.status = ?")
        params.append(status)
    if course_code:
        clauses.append("a.course_code = ?")
        params.append(str(course_code).upper())
    if department:
        clauses.append("e.department = ?")
        params.append(department)
    if overdue:
        clauses.append("a.status IN ('assigned','in_progress','failed') AND a.due_date < date('now')")
    if q:
        clauses.append("(e.display_name LIKE ? OR a.course_code LIKE ? OR c.title LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    base = (
        "FROM assignments a JOIN employees e ON e.employee_id=a.employee_id "
        "JOIN courses c ON c.course_code=a.course_code"
    )
    with connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) {base}{where}", params).fetchone()[0]
        items = [dict(r) for r in conn.execute(
            f"""
            SELECT a.*, e.display_name, e.department, e.manager_employee_id,
                   c.title course_title, c.category, c.duration_minutes,
                   c.recertification_days, c.passing_score
            {base}{where}
            ORDER BY CASE a.status
                       WHEN 'assigned' THEN 0 WHEN 'in_progress' THEN 1
                       WHEN 'failed' THEN 2 ELSE 3 END,
                     a.due_date, e.display_name
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()]
    return _page(total, items, limit, offset)


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------

def personal_dashboard(employee_id):
    with connection() as conn:
        counts = dict(conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM courses WHERE active=1) active_courses,
              (SELECT COUNT(*) FROM assignments WHERE employee_id=? AND status NOT IN ('completed','exempted')) total_assignments,
              (SELECT COUNT(*) FROM assignments WHERE employee_id=? AND status='completed') completed_assignments,
              (SELECT COUNT(*) FROM assignments WHERE employee_id=? AND status='in_progress') in_progress_assignments,
              (SELECT COUNT(*) FROM assignments WHERE employee_id=? AND status='exempted') exempted_assignments,
              (SELECT COUNT(*) FROM assignments WHERE employee_id=? AND status IN ('assigned','in_progress','failed') AND due_date < date('now')) overdue_assignments,
              (SELECT COUNT(*) FROM notifications WHERE recipient_employee_id=? AND read_at IS NULL) unread_notifications
            """,
            (employee_id,) * 6,
        ).fetchone())
        counts["active_employees"] = 1
        counts["audit_events"] = 0
        denominator = counts["total_assignments"] + counts["completed_assignments"]
        counts["completion_rate"] = (
            round(100 * counts["completed_assignments"] / denominator, 1) if denominator else 0
        )
        counts["departments"] = []
        counts["assignment_status"] = [dict(r) for r in conn.execute(
            "SELECT status label, COUNT(*) value FROM assignments WHERE employee_id=? "
            "GROUP BY status ORDER BY value DESC",
            (employee_id,),
        ).fetchall()]
        counts["upcoming"] = [dict(r) for r in conn.execute(
            """
            SELECT a.course_code, c.title course_title, a.due_date, a.status
            FROM assignments a JOIN courses c ON c.course_code=a.course_code
            WHERE a.employee_id=? AND a.status IN ('assigned','in_progress','failed')
            ORDER BY a.due_date LIMIT 5
            """,
            (employee_id,),
        ).fetchall()]
        return counts


def dashboard():
    with connection() as conn:
        counts = dict(conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM employees WHERE employment_status='active') active_employees,
              (SELECT COUNT(*) FROM courses WHERE active=1) active_courses,
              (SELECT COUNT(*) FROM assignments) total_assignments,
              (SELECT COUNT(*) FROM assignments WHERE status='completed') completed_assignments,
              (SELECT COUNT(*) FROM assignments WHERE status='in_progress') in_progress_assignments,
              (SELECT COUNT(*) FROM assignments WHERE status='exempted') exempted_assignments,
              (SELECT COUNT(*) FROM assignments WHERE status IN ('assigned','in_progress','failed') AND due_date < date('now')) overdue_assignments,
              (SELECT COUNT(*) FROM assignments WHERE status='completed' AND recert_due_date IS NOT NULL AND recert_due_date <= date('now','+30 day')) recertifications_due_30d,
              (SELECT COUNT(*) FROM audit_events) audit_events
            """
        ).fetchone())
        denominator = counts["total_assignments"] - counts["exempted_assignments"]
        counts["completion_rate"] = (
            round(100 * counts["completed_assignments"] / denominator, 1) if denominator else 0
        )
        counts["departments"] = [dict(r) for r in conn.execute(
            "SELECT COALESCE(department,'Unassigned') label, COUNT(*) value "
            "FROM employees WHERE employment_status='active' GROUP BY department ORDER BY value DESC"
        ).fetchall()]
        counts["assignment_status"] = [dict(r) for r in conn.execute(
            "SELECT status label, COUNT(*) value FROM assignments GROUP BY status ORDER BY value DESC"
        ).fetchall()]
        counts["compliance_by_department"] = [dict(r) for r in conn.execute(
            """
            SELECT COALESCE(e.department,'Unassigned') label,
                   COUNT(*) total,
                   SUM(CASE WHEN a.status='completed' THEN 1 ELSE 0 END) completed,
                   ROUND(100.0 * SUM(CASE WHEN a.status='completed' THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(*) - SUM(CASE WHEN a.status='exempted' THEN 1 ELSE 0 END), 0), 1) rate
            FROM assignments a JOIN employees e ON e.employee_id=a.employee_id
            GROUP BY e.department ORDER BY rate DESC
            """
        ).fetchall()]
        return counts


def team_dashboard(manager_employee_id):
    ids = manager_subtree(manager_employee_id)
    if not ids:
        return {
            "team_size": 0, "total_assignments": 0, "completed_assignments": 0,
            "overdue_assignments": 0, "completion_rate": 0,
            "assignment_status": [], "members": [],
        }
    marks = ",".join("?" for _ in ids)
    with connection() as conn:
        counts = dict(conn.execute(
            f"""
            SELECT
              COUNT(DISTINCT a.employee_id) team_size,
              COUNT(*) total_assignments,
              SUM(CASE WHEN a.status='completed' THEN 1 ELSE 0 END) completed_assignments,
              SUM(CASE WHEN a.status='exempted' THEN 1 ELSE 0 END) exempted_assignments,
              SUM(CASE WHEN a.status IN ('assigned','in_progress','failed') AND a.due_date < date('now') THEN 1 ELSE 0 END) overdue_assignments
            FROM assignments a WHERE a.employee_id IN ({marks})
            """,
            ids,
        ).fetchone())
        counts["team_size"] = counts["team_size"] or 0
        counts["total_assignments"] = counts["total_assignments"] or 0
        counts["completed_assignments"] = counts["completed_assignments"] or 0
        counts["exempted_assignments"] = counts["exempted_assignments"] or 0
        counts["overdue_assignments"] = counts["overdue_assignments"] or 0
        denominator = counts["total_assignments"] - counts["exempted_assignments"]
        counts["completion_rate"] = (
            round(100 * counts["completed_assignments"] / denominator, 1)
            if denominator else 0
        )
        counts["assignment_status"] = [dict(r) for r in conn.execute(
            f"SELECT status label, COUNT(*) value FROM assignments "
            f"WHERE employee_id IN ({marks}) GROUP BY status ORDER BY value DESC",
            ids,
        ).fetchall()]
        counts["members"] = [dict(r) for r in conn.execute(
            f"""
            SELECT e.employee_id, e.display_name, e.department, e.job_title,
                   COUNT(a.id) assignments,
                   SUM(CASE WHEN a.status='completed' THEN 1 ELSE 0 END) completed,
                   SUM(CASE WHEN a.status IN ('assigned','in_progress','failed') AND a.due_date < date('now') THEN 1 ELSE 0 END) overdue
            FROM employees e LEFT JOIN assignments a ON a.employee_id=e.employee_id
            WHERE e.employee_id IN ({marks})
            GROUP BY e.employee_id ORDER BY e.display_name
            """,
            ids,
        ).fetchall()]
        counts["team_size"] = len(counts["members"])
        return counts


def team_assignments(manager_employee_id, status=None, limit=200, offset=0):
    ids = manager_subtree(manager_employee_id)
    if not ids:
        return _page(0, [], limit, offset)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    marks = ",".join("?" for _ in ids)
    params: list = list(ids)
    clause = f" WHERE a.employee_id IN ({marks})"
    if status:
        clause += " AND a.status=?"
        params.append(status)
    base = (
        "FROM assignments a JOIN employees e ON e.employee_id=a.employee_id "
        "JOIN courses c ON c.course_code=a.course_code"
    )
    with connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) {base}{clause}", params).fetchone()[0]
        items = [dict(r) for r in conn.execute(
            f"""
            SELECT a.*, e.display_name, e.department, c.title course_title,
                   c.category, c.duration_minutes
            {base}{clause}
            ORDER BY CASE a.status WHEN 'assigned' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                     a.due_date
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()]
    return _page(total, items, limit, offset)


def audit_events(limit=200):
    events = rows("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (int(limit),))
    for event in events:
        try:
            event["details"] = json.loads(event.pop("details_json"))
        except Exception:
            event["details"] = {}
    return events


def search_audit(event_type=None, actor=None, entity_type=None, limit=100, offset=0):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    clauses, params = [], []
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if actor:
        clauses.append("actor = ?")
        params.append(actor)
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM audit_events{where}", params).fetchone()[0]
        items = [dict(r) for r in conn.execute(
            f"SELECT * FROM audit_events{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()]
    for item in items:
        try:
            item["details"] = json.loads(item.pop("details_json"))
        except Exception:
            item["details"] = {}
    return _page(total, items, limit, offset)


def import_jobs():
    result = rows("SELECT * FROM import_jobs ORDER BY id DESC LIMIT 100")
    for item in result:
        try:
            item["summary"] = json.loads(item.pop("summary_json"))
        except Exception:
            item["summary"] = {}
    return result


# ---------------------------------------------------------------------------
# Scheduled notification sweeps
# ---------------------------------------------------------------------------

def _already_notified(conn, template, entity_id) -> bool:
    found = conn.execute(
        "SELECT 1 FROM notifications WHERE template=? AND entity_id=? LIMIT 1",
        (template, entity_id),
    ).fetchone()
    return found is not None


def notify_due_soon(days=7) -> int:
    horizon = (today() + timedelta(days=int(days))).isoformat()
    with connection() as conn:
        items = [dict(r) for r in conn.execute(
            """
            SELECT a.employee_id, a.course_code, a.cycle, a.due_date,
                   e.first_name, e.display_name, c.title course_title
            FROM assignments a
            JOIN employees e ON e.employee_id=a.employee_id
            JOIN courses c ON c.course_code=a.course_code
            WHERE a.status IN ('assigned','in_progress')
              AND a.due_date IS NOT NULL
              AND a.due_date >= date('now')
              AND a.due_date <= ?
            """,
            (horizon,),
        ).fetchall()]
        sent = 0
        for item in items:
            entity = f"{item['employee_id']}:{item['course_code']}:{item['cycle']}"
            if _already_notified(conn, "assignment_due_soon", entity):
                continue
            notifications.queue(
                conn, item["employee_id"], "assignment_due_soon",
                {
                    "first_name": item["first_name"] or item["display_name"],
                    "course_title": item["course_title"],
                    "course_code": item["course_code"],
                    "due_date": item["due_date"],
                },
                entity_type="assignment", entity_id=entity,
            )
            sent += 1
        return sent


def notify_overdue() -> int:
    with connection() as conn:
        items = [dict(r) for r in conn.execute(
            """
            SELECT a.employee_id, a.course_code, a.cycle, a.due_date,
                   e.first_name, e.display_name, e.manager_employee_id,
                   c.title course_title
            FROM assignments a
            JOIN employees e ON e.employee_id=a.employee_id
            JOIN courses c ON c.course_code=a.course_code
            WHERE a.status IN ('assigned','in_progress','failed')
              AND a.due_date IS NOT NULL AND a.due_date < date('now')
            """
        ).fetchall()]
        sent = 0
        for item in items:
            entity = f"{item['employee_id']}:{item['course_code']}:{item['cycle']}"
            if _already_notified(conn, "assignment_overdue", entity):
                continue
            context = {
                "first_name": item["first_name"] or item["display_name"],
                "course_title": item["course_title"],
                "course_code": item["course_code"],
                "due_date": item["due_date"],
            }
            notifications.queue(conn, item["employee_id"], "assignment_overdue",
                                context, entity_type="assignment", entity_id=entity)
            sent += 1
        return sent


def notify_recertifications(days=30) -> int:
    horizon = (today() + timedelta(days=int(days))).isoformat()
    with connection() as conn:
        items = [dict(r) for r in conn.execute(
            """
            SELECT a.employee_id, a.course_code, a.cycle, a.recert_due_date,
                   e.first_name, e.display_name, c.title course_title
            FROM assignments a
            JOIN employees e ON e.employee_id=a.employee_id
            JOIN courses c ON c.course_code=a.course_code
            WHERE a.status='completed' AND a.recert_due_date IS NOT NULL
              AND a.recert_due_date <= ?
            """,
            (horizon,),
        ).fetchall()]
        sent = 0
        for item in items:
            entity = f"{item['employee_id']}:{item['course_code']}:{item['cycle']}"
            if _already_notified(conn, "recertification_due", entity):
                continue
            notifications.queue(
                conn, item["employee_id"], "recertification_due",
                {
                    "first_name": item["first_name"] or item["display_name"],
                    "course_title": item["course_title"],
                    "course_code": item["course_code"],
                    "recert_due_date": item["recert_due_date"],
                },
                entity_type="assignment", entity_id=entity,
            )
            sent += 1
        return sent


def run_notification_sweeps(days_soon=7, days_recert=30) -> dict:
    return {
        "due_soon": notify_due_soon(days_soon),
        "overdue": notify_overdue(),
        "recertification": notify_recertifications(days_recert),
        "delivered": notifications.deliver(),
    }
