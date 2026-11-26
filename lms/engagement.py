"""Engagement layer: cohorts, learning paths, gradebook, badges and discussions.

This module sits on top of the content model. It derives grades from quiz
attempts, sequences courses into learning paths, issues verifiable completion
credentials, and provides lightweight course discussions. All rules and
validation live here; the HTTP layer only checks permissions and serialises.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from . import services
from .audit import record
from .db import connection, row, rows

PATH_STATUSES = ("draft", "published", "archived")
BADGE_ICONS = ("star", "shield", "trophy", "certificate", "bolt", "book")
ACTIVE_ASSIGNMENT_STATUSES = ("assigned", "in_progress", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code(value) -> str:
    return str(value or "").strip().upper()


def _letter(score) -> str:
    if score is None:
        return "—"
    score = float(score)
    if score >= 93:
        return "A"
    if score >= 90:
        return "A-"
    if score >= 87:
        return "B+"
    if score >= 83:
        return "B"
    if score >= 80:
        return "B-"
    if score >= 77:
        return "C+"
    if score >= 73:
        return "C"
    if score >= 70:
        return "C-"
    if score >= 60:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Cohorts
# ---------------------------------------------------------------------------

def list_cohorts(q=None, active_only=False) -> list[dict]:
    clauses, params = [], []
    if active_only:
        clauses.append("c.active=1")
    if q:
        clauses.append("(c.code LIKE ? OR c.name LIKE ? OR c.description LIKE ?)")
        like = f"%{q}%"
        params.extend([like] * 3)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return rows(
        f"""
        SELECT c.*,
          (SELECT COUNT(*) FROM cohort_members cm WHERE cm.cohort_id=c.id) member_count
        FROM cohorts c{where}
        ORDER BY c.name
        """,
        params,
    )


def get_cohort(cohort_id) -> dict | None:
    cohort = row("SELECT * FROM cohorts WHERE id=?", (int(cohort_id),))
    if not cohort:
        return None
    cohort["active"] = bool(cohort["active"])
    cohort["members"] = rows(
        """
        SELECT cm.employee_id, cm.added_by, cm.created_at, e.display_name,
               e.work_email, e.department, e.employment_status
        FROM cohort_members cm
        JOIN employees e ON e.employee_id=cm.employee_id
        WHERE cm.cohort_id=?
        ORDER BY e.display_name
        """,
        (int(cohort_id),),
    )
    return cohort


def create_cohort(data, actor="admin") -> dict:
    code = _code(data.get("code"))
    name = str(data.get("name", "")).strip()
    if not code or not name:
        raise ValueError("code and name are required")
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO cohorts(code,name,description,active) VALUES(?,?,?,?)",
            (code, name, str(data.get("description", "")),
             1 if data.get("active", True) else 0),
        )
        cohort_id = int(cur.lastrowid)
        for employee_id in data.get("employees") or []:
            eid = str(employee_id).strip()
            if not conn.execute(
                "SELECT 1 FROM employees WHERE employee_id=?", (eid,)
            ).fetchone():
                raise ValueError(f"Unknown employee: {eid}")
            conn.execute(
                "INSERT INTO cohort_members(cohort_id,employee_id,added_by) VALUES(?,?,?)",
                (cohort_id, eid, actor),
            )
        record("CohortCreated", actor, "cohort", cohort_id,
               {"code": code, "name": name}, conn=conn)
    return get_cohort(cohort_id)


def update_cohort(cohort_id, data, actor="admin") -> dict:
    updates = {}
    for key in ("name", "description"):
        if key in data:
            updates[key] = str(data[key])
    if "code" in data:
        updates["code"] = _code(data["code"])
        if not updates["code"]:
            raise ValueError("code cannot be empty")
    if "active" in data:
        updates["active"] = 1 if data["active"] else 0
    if not updates:
        raise ValueError("No supported fields were supplied")
    updates["updated_at"] = _now()
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM cohorts WHERE id=?", (int(cohort_id),)).fetchone():
            raise ValueError("Cohort not found")
        conn.execute(
            f"UPDATE cohorts SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
            list(updates.values()) + [int(cohort_id)],
        )
        record("CohortUpdated", actor, "cohort", int(cohort_id),
               {"fields": sorted(updates)}, conn=conn)
    return get_cohort(cohort_id)


def delete_cohort(cohort_id, actor="admin") -> dict:
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM cohorts WHERE id=?", (int(cohort_id),)).fetchone():
            raise ValueError("Cohort not found")
        conn.execute("DELETE FROM cohorts WHERE id=?", (int(cohort_id),))
        record("CohortDeleted", actor, "cohort", int(cohort_id), {}, conn=conn)
    return {"deleted": True, "id": int(cohort_id)}


def add_cohort_member(cohort_id, employee_id, actor="admin") -> dict:
    employee_id = str(employee_id or "").strip()
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM cohorts WHERE id=?", (int(cohort_id),)).fetchone():
            raise ValueError("Cohort not found")
        if not conn.execute(
            "SELECT 1 FROM employees WHERE employee_id=?", (employee_id,)
        ).fetchone():
            raise ValueError("Unknown employee")
        conn.execute(
            "INSERT OR IGNORE INTO cohort_members(cohort_id,employee_id,added_by) VALUES(?,?,?)",
            (int(cohort_id), employee_id, actor),
        )
        record("CohortMemberAdded", actor, "cohort", int(cohort_id),
               {"employee_id": employee_id}, conn=conn)
    return get_cohort(cohort_id)


def remove_cohort_member(cohort_id, employee_id, actor="admin") -> dict:
    with connection() as conn:
        conn.execute(
            "DELETE FROM cohort_members WHERE cohort_id=? AND employee_id=?",
            (int(cohort_id), str(employee_id)),
        )
        record("CohortMemberRemoved", actor, "cohort", int(cohort_id),
               {"employee_id": employee_id}, conn=conn)
    return get_cohort(cohort_id)


def cohorts_for_employee(employee_id, conn=None) -> list[dict]:
    query = """
        SELECT c.* FROM cohorts c
        JOIN cohort_members cm ON cm.cohort_id=c.id
        WHERE cm.employee_id=? ORDER BY c.name
    """
    if conn is not None:
        return [dict(item) for item in conn.execute(query, (employee_id,)).fetchall()]
    return rows(query, (employee_id,))


def _path_progress(conn, employee_id, path_id) -> dict:
    courses = conn.execute(
        """
        SELECT pc.course_code, pc.position, pc.is_required, c.title
        FROM learning_path_courses pc
        JOIN courses c ON c.course_code=pc.course_code
        WHERE pc.path_id=? ORDER BY pc.position, pc.id
        """,
        (path_id,),
    ).fetchall()
    required = [c for c in courses if c["is_required"]]
    completed, next_course = set(), None
    for course in courses:
        assignment = conn.execute(
            """
            SELECT status FROM assignments
            WHERE employee_id=? AND course_code=? ORDER BY cycle DESC LIMIT 1
            """,
            (employee_id, course["course_code"]),
        ).fetchone()
        if assignment and assignment["status"] in ("completed", "exempted"):
            completed.add(course["course_code"])
        elif next_course is None:
            next_course = course["course_code"]
    total_required = len(required)
    completed_required = sum(1 for c in required if c["course_code"] in completed)
    percent = round(100 * completed_required / total_required, 1) if total_required else 0
    return {
        "total_courses": len(courses),
        "required_courses": total_required,
        "completed_courses": len(completed),
        "completed_required": completed_required,
        "percent": percent,
        "is_complete": bool(total_required) and completed_required == total_required,
        "next_course": next_course,
    }


# ---------------------------------------------------------------------------
# Learning paths
# ---------------------------------------------------------------------------

def _decorate_path(conn, item, actor=None, *, show_all):
    item["active"] = item["status"] == "published"
    item["course_count"] = int(conn.execute(
        "SELECT COUNT(*) FROM learning_path_courses WHERE path_id=?", (item["id"],)
    ).fetchone()[0])
    item["content_minutes"] = int(conn.execute(
        """
        SELECT COALESCE(SUM(c.duration_minutes),0) FROM learning_path_courses pc
        JOIN courses c ON c.course_code=pc.course_code WHERE pc.path_id=?
        """,
        (item["id"],),
    ).fetchone()[0])
    if actor:
        item["my_progress"] = _path_progress(conn, actor, item["id"])
    return item


def list_paths(actor=None, *, include_unpublished=False, q=None) -> list[dict]:
    clauses, params = [], []
    if not include_unpublished:
        clauses.append("status='published'")
    if q:
        clauses.append("(code LIKE ? OR title LIKE ? OR description LIKE ?)")
        like = f"%{q}%"
        params.extend([like] * 3)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connection() as conn:
        found = conn.execute(
            f"SELECT * FROM learning_paths{where} ORDER BY title", params
        ).fetchall()
        return [_decorate_path(conn, dict(item), actor, show_all=include_unpublished)
                for item in found]


def get_path(path_id, actor=None, *, include_unpublished=False) -> dict | None:
    with connection() as conn:
        found = conn.execute(
            "SELECT * FROM learning_paths WHERE id=?", (int(path_id),)
        ).fetchone()
        if not found:
            return None
        item = dict(found)
        if item["status"] != "published" and not include_unpublished:
            return None
        _decorate_path(conn, item, actor, show_all=include_unpublished)
        courses = conn.execute(
            """
            SELECT pc.*, c.title, c.summary, c.category, c.level, c.duration_minutes
            FROM learning_path_courses pc
            JOIN courses c ON c.course_code=pc.course_code
            WHERE pc.path_id=? ORDER BY pc.position, pc.id
            """,
            (int(path_id),),
        ).fetchall()
        item["courses"] = []
        for course in courses:
            entry = dict(course)
            entry["active"] = bool(course["is_required"])
            if actor:
                assignment = conn.execute(
                    """
                    SELECT * FROM assignments
                    WHERE employee_id=? AND course_code=? ORDER BY cycle DESC LIMIT 1
                    """,
                    (actor, course["course_code"]),
                ).fetchone()
                entry["assignment"] = dict(assignment) if assignment else None
                entry["grade"] = get_course_grade(actor, course["course_code"])
            item["courses"].append(entry)
        item["progress"] = _path_progress(conn, actor, int(path_id)) if actor else None
        return item


def create_path(data, actor="admin") -> dict:
    code = _code(data.get("code"))
    title = str(data.get("title", "")).strip()
    if not code or not title:
        raise ValueError("code and title are required")
    status = str(data.get("status", "published"))
    if status not in PATH_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(PATH_STATUSES)}")
    courses = data.get("courses") or []
    if isinstance(courses, str):
        courses = [item.strip() for item in courses.splitlines() if item.strip()]
    with connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO learning_paths(code,title,description,status,created_by)
            VALUES(?,?,?,?,?)
            """,
            (code, title, str(data.get("description", "")), status, actor),
        )
        path_id = int(cur.lastrowid)
        for position, course in enumerate(courses, start=1):
            course_code = _code(course)
            if not conn.execute(
                "SELECT 1 FROM courses WHERE course_code=?", (course_code,)
            ).fetchone():
                raise ValueError(f"Unknown course: {course_code}")
            conn.execute(
                """
                INSERT INTO learning_path_courses(path_id,course_code,position,is_required)
                VALUES(?,?,?,1)
                """,
                (path_id, course_code, position),
            )
        record("LearningPathCreated", actor, "learning_path", path_id,
               {"code": code, "title": title}, conn=conn)
    return get_path(path_id, actor=actor, include_unpublished=True)


def update_path(path_id, data, actor="admin") -> dict:
    updates = {}
    for key in ("title", "description", "code"):
        if key in data:
            value = _code(data[key]) if key == "code" else str(data[key])
            if not value:
                raise ValueError(f"{key} cannot be empty")
            updates[key] = value
    if "status" in data:
        status = str(data["status"])
        if status not in PATH_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(PATH_STATUSES)}")
        updates["status"] = status
    courses = None
    if "courses" in data:
        requested = data.get("courses") or []
        if isinstance(requested, str):
            requested = [item.strip() for item in requested.splitlines() if item.strip()]
        courses = list(dict.fromkeys(
            code for code in (_code(item) for item in requested) if code
        ))
    if not updates and courses is None:
        raise ValueError("No supported fields were supplied")
    updates["updated_at"] = _now()
    with connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM learning_paths WHERE id=?", (int(path_id),)
        ).fetchone():
            raise ValueError("Learning path not found")
        if courses is not None:
            for course_code in courses:
                if not conn.execute(
                    "SELECT 1 FROM courses WHERE course_code=?", (course_code,)
                ).fetchone():
                    raise ValueError(f"Unknown course: {course_code}")
        conn.execute(
            f"UPDATE learning_paths SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
            list(updates.values()) + [int(path_id)],
        )
        if courses is not None:
            conn.execute(
                "DELETE FROM learning_path_courses WHERE path_id=?", (int(path_id),)
            )
            conn.executemany(
                """
                INSERT INTO learning_path_courses(path_id,course_code,position,is_required)
                VALUES(?,?,?,1)
                """,
                [(int(path_id), course_code, position)
                 for position, course_code in enumerate(courses, start=1)],
            )
        record("LearningPathUpdated", actor, "learning_path", int(path_id),
               {"fields": sorted(updates) + (["courses"] if courses is not None else [])},
               conn=conn)
    return get_path(path_id, actor=actor, include_unpublished=True)


def delete_path(path_id, actor="admin") -> dict:
    with connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM learning_paths WHERE id=?", (int(path_id),)
        ).fetchone():
            raise ValueError("Learning path not found")
        conn.execute("DELETE FROM learning_paths WHERE id=?", (int(path_id),))
        record("LearningPathDeleted", actor, "learning_path", int(path_id), {}, conn=conn)
    return {"deleted": True, "id": int(path_id)}


def add_path_course(path_id, data, actor="admin") -> dict:
    course_code = _code(data.get("course_code"))
    if not course_code:
        raise ValueError("course_code is required")
    with connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM learning_paths WHERE id=?", (int(path_id),)
        ).fetchone():
            raise ValueError("Learning path not found")
        if not conn.execute(
            "SELECT 1 FROM courses WHERE course_code=?", (course_code,)
        ).fetchone():
            raise ValueError("Unknown course")
        position = data.get("position")
        if position in (None, ""):
            position = conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM learning_path_courses WHERE path_id=?",
                (int(path_id),),
            ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO learning_path_courses(path_id,course_code,position,is_required)
            VALUES(?,?,?,?)
            """,
            (int(path_id), course_code, int(position),
             1 if data.get("is_required", True) else 0),
        )
        record("PathCourseAdded", actor, "learning_path", int(path_id),
               {"course_code": course_code}, conn=conn)
    return get_path(path_id, actor=actor, include_unpublished=True)


def remove_path_course(path_id, course_code, actor="admin") -> dict:
    with connection() as conn:
        conn.execute(
            "DELETE FROM learning_path_courses WHERE path_id=? AND course_code=?",
            (int(path_id), _code(course_code)),
        )
        record("PathCourseRemoved", actor, "learning_path", int(path_id),
               {"course_code": _code(course_code)}, conn=conn)
    return get_path(path_id, actor=actor, include_unpublished=True)


def enroll_path(employee_id, path_id, actor=None) -> dict:
    actor = actor or employee_id
    path = get_path(path_id, actor=actor, include_unpublished=True)
    if not path:
        raise ValueError("Learning path not found")
    if path["status"] != "published":
        raise ValueError("This learning path is not open for enrollment")
    with connection() as conn:
        progress = _path_progress(conn, employee_id, int(path_id))
    if progress["is_complete"]:
        return {"path_id": int(path_id), "assignment": None, "already_complete": True}
    if not progress["next_course"]:
        return {"path_id": int(path_id), "assignment": None, "already_enrolled": True}
    assignment = services.assign_course(
        employee_id, progress["next_course"], 30, actor, source="path"
    )
    return {"path_id": int(path_id), "assignment": assignment}


def on_course_completed(employee_id, course_code, score=None, actor=None) -> dict:
    """Award badges and advance learning paths after a course completes."""
    actor = actor or employee_id
    awarded, advanced = [], []
    with connection() as conn:
        course_code = _code(course_code)
        badges = conn.execute(
            """
            SELECT * FROM badges
            WHERE active=1 AND criteria_course_code=?
            """,
            (course_code,),
        ).fetchall()
        for badge in badges:
            award = _award_badge(conn, dict(badge), employee_id, "course_completion", score)
            if award:
                awarded.append(award["verification_code"])

        path_ids = [
            item[0] for item in conn.execute(
                """
                SELECT DISTINCT pc.path_id FROM learning_path_courses pc
                JOIN learning_paths p ON p.id=pc.path_id
                WHERE pc.course_code=? AND p.status='published'
                """,
                (course_code,),
            ).fetchall()
        ]
        next_assignments = []
        for path_id in path_ids:
            courses = conn.execute(
                """
                SELECT course_code, position, is_required FROM learning_path_courses
                WHERE path_id=? ORDER BY position, id
                """,
                (path_id,),
            ).fetchall()
            current = next((c for c in courses if c["course_code"] == course_code), None)
            if not current or not current["is_required"]:
                continue
            later = [c for c in courses if c["position"] > current["position"]
                     and c["is_required"]]
            if not later:
                continue
            for course in later:
                latest = conn.execute(
                    """
                    SELECT status FROM assignments
                    WHERE employee_id=? AND course_code=? ORDER BY cycle DESC LIMIT 1
                    """,
                    (employee_id, course["course_code"]),
                ).fetchone()
                if not latest:
                    next_assignments.append((course["course_code"],))
                    break
                if latest["status"] not in ("completed", "exempted"):
                    break
        progress_paths = [pid for pid in path_ids]

        completed_paths = []
        for path_id in progress_paths:
            courses = conn.execute(
                """
                SELECT course_code FROM learning_path_courses
                WHERE path_id=? AND is_required=1
                """,
                (path_id,),
            ).fetchall()
            if not courses:
                continue
            if all(conn.execute(
                """
                SELECT 1 FROM assignments
                WHERE employee_id=? AND course_code=? AND status IN ('completed','exempted')
                LIMIT 1
                """,
                (employee_id, course["course_code"]),
            ).fetchone() for course in courses):
                completed_paths.append(path_id)

        for path_id in completed_paths:
            badges = conn.execute(
                "SELECT * FROM badges WHERE active=1 AND criteria_path_id=?", (path_id,)
            ).fetchall()
            for badge in badges:
                award = _award_badge(conn, dict(badge), employee_id, "path_completion", score)
                if award:
                    awarded.append(award["verification_code"])
        record("ProgressionEvaluated", actor, "employee", employee_id,
               {"course_code": course_code, "awards": len(awarded),
                "advanced_paths": len(next_assignments)}, conn=conn)

    for course in next_assignments:
        assignment = services.assign_course(
            employee_id, course[0], 30, actor, source="path"
        )
        advanced.append({"course_code": course[0], "assignment_id": assignment["id"]})
    return {"awarded": awarded, "advanced": advanced}


# ---------------------------------------------------------------------------
# Gradebook
# ---------------------------------------------------------------------------

def _letter_for(score):
    return _letter(score)


def get_course_grade(employee_id, course_code) -> dict | None:
    course_code = _code(course_code)
    course = row("SELECT course_code,title,passing_score FROM courses WHERE course_code=?",
                 (course_code,))
    if not course:
        return None
    with connection() as conn:
        assignment = conn.execute(
            """
            SELECT * FROM assignments
            WHERE employee_id=? AND course_code=? ORDER BY cycle DESC LIMIT 1
            """,
            (employee_id, course_code),
        ).fetchone()
        lessons = conn.execute(
            """
            SELECT id,title FROM lessons
            WHERE course_code=? AND lesson_type='quiz' ORDER BY position, id
            """,
            (course_code,),
        ).fetchall()
        components = []
        scores = []
        for lesson in lessons:
            attempts = conn.execute(
                """
                SELECT id,score,passed,created_at FROM quiz_attempts
                WHERE employee_id=? AND lesson_id=? ORDER BY id DESC
                """,
                (employee_id, lesson["id"]),
            ).fetchall()
            if attempts:
                best = max(float(item["score"]) for item in attempts)
                scores.append(best)
                latest = attempts[0]
            else:
                best = None
                latest = None
            components.append({
                "lesson_id": lesson["id"],
                "title": lesson["title"],
                "attempts": len(attempts),
                "best_score": best,
                "latest_score": float(latest["score"]) if latest else None,
                "latest_passed": bool(latest["passed"]) if latest else False,
            })
        assignment_score = float(assignment["score"]) if assignment and assignment["score"] is not None else None
        final = assignment_score
        if final is None and scores:
            final = round(sum(scores) / len(scores), 1)
    return {
        "course_code": course_code,
        "course_title": course["title"],
        "employee_id": employee_id,
        "status": assignment["status"] if assignment else "not_enrolled",
        "cycle": assignment["cycle"] if assignment else None,
        "score": final,
        "letter": _letter(final),
        "passing_score": course["passing_score"],
        "source": "assignment" if assignment_score is not None else (
            "quizzes" if scores else "none"
        ),
        "components": components,
    }


def gradebook(employee_id) -> list[dict]:
    course_codes = [
        item["course_code"] for item in rows(
            "SELECT DISTINCT course_code FROM assignments WHERE employee_id=? ORDER BY course_code",
            (employee_id,),
        )
    ]
    result = []
    for code in course_codes:
        grade = get_course_grade(employee_id, code)
        if grade:
            result.append(grade)
    return result


# ---------------------------------------------------------------------------
# Badges and verifiable certificates
# ---------------------------------------------------------------------------

def _decorate_badge(conn, item, employee_id=None) -> dict:
    badge = dict(item)
    badge["active"] = bool(badge["active"])
    badge["award_count"] = int(conn.execute(
        "SELECT COUNT(*) FROM badge_awards WHERE badge_id=?", (badge["id"],)
    ).fetchone()[0])
    if badge.get("criteria_course_code"):
        course = conn.execute(
            "SELECT title FROM courses WHERE course_code=?", (badge["criteria_course_code"],)
        ).fetchone()
        badge["criteria_title"] = course["title"] if course else badge["criteria_course_code"]
    elif badge.get("criteria_path_id"):
        path = conn.execute(
            "SELECT title FROM learning_paths WHERE id=?", (badge["criteria_path_id"],)
        ).fetchone()
        badge["criteria_title"] = path["title"] if path else "Learning path"
    else:
        badge["criteria_title"] = "Manual award"
    if employee_id:
        badge["awarded"] = bool(conn.execute(
            "SELECT 1 FROM badge_awards WHERE badge_id=? AND employee_id=?",
            (badge["id"], employee_id),
        ).fetchone())
    return badge


def list_badges(include_inactive=False, employee_id=None) -> list[dict]:
    where = "" if include_inactive else " WHERE active=1"
    with connection() as conn:
        found = conn.execute(f"SELECT * FROM badges{where} ORDER BY title").fetchall()
        return [_decorate_badge(conn, item, employee_id) for item in found]


def get_badge(badge_id, employee_id=None) -> dict | None:
    with connection() as conn:
        found = conn.execute("SELECT * FROM badges WHERE id=?", (int(badge_id),)).fetchone()
        if not found:
            return None
        return _decorate_badge(conn, found, employee_id)


def create_badge(data, actor="admin") -> dict:
    code = _code(data.get("code"))
    title = str(data.get("title", "")).strip()
    if not code or not title:
        raise ValueError("code and title are required")
    icon = str(data.get("icon", "star"))
    if icon not in BADGE_ICONS:
        raise ValueError(f"icon must be one of: {', '.join(BADGE_ICONS)}")
    course_code = _code(data.get("criteria_course_code")) or None
    path_id = data.get("criteria_path_id")
    path_id = int(path_id) if path_id not in (None, "", 0) else None
    with connection() as conn:
        if course_code and not conn.execute(
            "SELECT 1 FROM courses WHERE course_code=?", (course_code,)
        ).fetchone():
            raise ValueError("Unknown criteria course")
        if path_id and not conn.execute(
            "SELECT 1 FROM learning_paths WHERE id=?", (path_id,)
        ).fetchone():
            raise ValueError("Unknown criteria learning path")
        cur = conn.execute(
            """
            INSERT INTO badges(code,title,description,icon,criteria_course_code,
                               criteria_path_id,active)
            VALUES(?,?,?,?,?,?,?)
            """,
            (code, title, str(data.get("description", "")), icon, course_code, path_id,
             1 if data.get("active", True) else 0),
        )
        badge_id = int(cur.lastrowid)
        record("BadgeCreated", actor, "badge", badge_id,
               {"code": code, "title": title}, conn=conn)
    return get_badge(badge_id)


def update_badge(badge_id, data, actor="admin") -> dict:
    updates = {}
    for key in ("title", "description", "code"):
        if key in data:
            value = _code(data[key]) if key == "code" else str(data[key])
            if not value:
                raise ValueError(f"{key} cannot be empty")
            updates[key] = value
    if "icon" in data:
        icon = str(data["icon"])
        if icon not in BADGE_ICONS:
            raise ValueError(f"icon must be one of: {', '.join(BADGE_ICONS)}")
        updates["icon"] = icon
    if "active" in data:
        updates["active"] = 1 if data["active"] else 0
    if "criteria_course_code" in data:
        updates["criteria_course_code"] = _code(data["criteria_course_code"]) or None
    if "criteria_path_id" in data:
        value = data["criteria_path_id"]
        updates["criteria_path_id"] = int(value) if value not in (None, "", 0) else None
    if not updates:
        raise ValueError("No supported fields were supplied")
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM badges WHERE id=?", (int(badge_id),)).fetchone():
            raise ValueError("Badge not found")
        if updates.get("criteria_course_code") and not conn.execute(
            "SELECT 1 FROM courses WHERE course_code=?",
            (updates["criteria_course_code"],)
        ).fetchone():
            raise ValueError("Unknown criteria course")
        if updates.get("criteria_path_id") and not conn.execute(
            "SELECT 1 FROM learning_paths WHERE id=?", (updates["criteria_path_id"],)
        ).fetchone():
            raise ValueError("Unknown criteria learning path")
        conn.execute(
            f"UPDATE badges SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
            list(updates.values()) + [int(badge_id)],
        )
        record("BadgeUpdated", actor, "badge", int(badge_id),
               {"fields": sorted(updates)}, conn=conn)
    return get_badge(badge_id)


def delete_badge(badge_id, actor="admin") -> dict:
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM badges WHERE id=?", (int(badge_id),)).fetchone():
            raise ValueError("Badge not found")
        conn.execute("DELETE FROM badges WHERE id=?", (int(badge_id),))
        record("BadgeDeleted", actor, "badge", int(badge_id), {}, conn=conn)
    return {"deleted": True, "id": int(badge_id)}


def _award_badge(conn, badge, employee_id, source, score=None) -> dict | None:
    exists = conn.execute(
        "SELECT 1 FROM badge_awards WHERE badge_id=? AND employee_id=?",
        (badge["id"], employee_id),
    ).fetchone()
    if exists:
        return None
    verification = secrets.token_urlsafe(18)
    cur = conn.execute(
        """
        INSERT INTO badge_awards(badge_id,employee_id,verification_code,source,score)
        VALUES(?,?,?,?,?)
        """,
        (badge["id"], employee_id, verification, source, score),
    )
    award_id = int(cur.lastrowid)
    record("BadgeAwarded", "system", "badge_award", award_id,
           {"badge_id": badge["id"], "employee_id": employee_id, "source": source},
           conn=conn)
    return {"id": award_id, "verification_code": verification}


def award_badge(badge_id, employee_id, actor="admin", score=None) -> dict:
    with connection() as conn:
        badge = conn.execute("SELECT * FROM badges WHERE id=?", (int(badge_id),)).fetchone()
        if not badge:
            raise ValueError("Badge not found")
        award = _award_badge(conn, dict(badge), employee_id, "manual", score)
        if not award:
            raise ValueError("This badge has already been awarded to the employee")
        record("BadgeAwardedManually", actor, "badge", int(badge_id),
               {"employee_id": employee_id}, conn=conn)
    return get_award(award["id"])


def earned_badges(employee_id) -> list[dict]:
    return rows(
        """
        SELECT ba.id, ba.verification_code, ba.source, ba.score, ba.awarded_at,
               b.id badge_id, b.code, b.title, b.description, b.icon,
               b.criteria_course_code, b.criteria_path_id
        FROM badge_awards ba
        JOIN badges b ON b.id=ba.badge_id
        WHERE ba.employee_id=? ORDER BY ba.awarded_at DESC
        """,
        (employee_id,),
    )


def get_award(award_id) -> dict | None:
    return row(
        """
        SELECT ba.*, b.code, b.title, b.description, b.icon,
               b.criteria_course_code, b.criteria_path_id
        FROM badge_awards ba JOIN badges b ON b.id=ba.badge_id
        WHERE ba.id=?
        """,
        (int(award_id),),
    )


def verify_certificate(code) -> dict | None:
    award = row(
        """
        SELECT ba.*, b.title badge_title, b.description badge_description, b.icon,
               e.display_name,
               c.title course_title,
               p.title path_title
        FROM badge_awards ba
        JOIN badges b ON b.id=ba.badge_id
        JOIN employees e ON e.employee_id=ba.employee_id
        LEFT JOIN courses c ON c.course_code=b.criteria_course_code
        LEFT JOIN learning_paths p ON p.id=b.criteria_path_id
        WHERE ba.verification_code=?
        """,
        (str(code or "").strip(),),
    )
    if not award:
        return None
    return award


# ---------------------------------------------------------------------------
# Discussions
# ---------------------------------------------------------------------------

def _display_author(actor):
    employee = row(
        "SELECT display_name FROM employees WHERE employee_id=?", (actor,)
    )
    if employee:
        return employee["display_name"]
    return "admin" if actor in ("demo-admin", "system", "admin") else str(actor)


def list_discussions(course_code) -> list[dict]:
    return rows(
        """
        SELECT d.*, e.display_name author_name,
          (SELECT COUNT(*) FROM discussion_posts p WHERE p.discussion_id=d.id) post_count,
          (SELECT MAX(created_at) FROM discussion_posts p WHERE p.discussion_id=d.id) last_post_at
        FROM discussions d
        LEFT JOIN employees e ON e.employee_id=d.created_by
        WHERE d.course_code=? ORDER BY d.pinned DESC, d.updated_at DESC
        """,
        (_code(course_code),),
    )


def get_discussion(discussion_id) -> dict | None:
    discussion = row(
        """
        SELECT d.*, e.display_name author_name FROM discussions d
        LEFT JOIN employees e ON e.employee_id=d.created_by
        WHERE d.id=?
        """,
        (int(discussion_id),),
    )
    if not discussion:
        return None
    posts = rows(
        """
        SELECT p.*, e.display_name author_name FROM discussion_posts p
        LEFT JOIN employees e ON e.employee_id=p.author_id
        WHERE p.discussion_id=? ORDER BY p.created_at, p.id
        """,
        (int(discussion_id),),
    )
    discussion["posts"] = posts
    return discussion


def get_post(post_id) -> dict | None:
    return row("SELECT * FROM discussion_posts WHERE id=?", (int(post_id),))


def create_discussion(course_code, data, actor="admin") -> dict:
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("A discussion title is required")
    with connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM courses WHERE course_code=?", (_code(course_code),)
        ).fetchone():
            raise ValueError("Course not found")
        cur = conn.execute(
            """
            INSERT INTO discussions(course_code,title,body,created_by)
            VALUES(?,?,?,?)
            """,
            (_code(course_code), title, str(data.get("body", "")), actor),
        )
        discussion_id = int(cur.lastrowid)
        record("DiscussionCreated", actor, "discussion", discussion_id,
               {"course_code": _code(course_code), "title": title}, conn=conn)
    return get_discussion(discussion_id)


def update_discussion(discussion_id, data, actor="admin") -> dict:
    updates = {}
    for key in ("title", "body"):
        if key in data:
            updates[key] = str(data[key])
    if "title" in updates and not updates["title"].strip():
        raise ValueError("A discussion title is required")
    for key in ("pinned", "closed"):
        if key in data:
            updates[key] = 1 if data[key] else 0
    if not updates:
        raise ValueError("No supported fields were supplied")
    updates["updated_at"] = _now()
    with connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM discussions WHERE id=?", (int(discussion_id),)
        ).fetchone():
            raise ValueError("Discussion not found")
        conn.execute(
            f"UPDATE discussions SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
            list(updates.values()) + [int(discussion_id)],
        )
        record("DiscussionUpdated", actor, "discussion", int(discussion_id),
               {"fields": sorted(updates)}, conn=conn)
    return get_discussion(discussion_id)


def delete_discussion(discussion_id, actor="admin") -> dict:
    with connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM discussions WHERE id=?", (int(discussion_id),)
        ).fetchone():
            raise ValueError("Discussion not found")
        conn.execute("DELETE FROM discussions WHERE id=?", (int(discussion_id),))
        record("DiscussionDeleted", actor, "discussion", int(discussion_id), {}, conn=conn)
    return {"deleted": True, "id": int(discussion_id)}


def create_post(discussion_id, data, actor="admin") -> dict:
    body = str(data.get("body", "")).strip()
    if not body:
        raise ValueError("A post body is required")
    parent_id = data.get("parent_id")
    parent_id = int(parent_id) if parent_id not in (None, "", 0) else None
    with connection() as conn:
        discussion = conn.execute(
            "SELECT * FROM discussions WHERE id=?", (int(discussion_id),)
        ).fetchone()
        if not discussion:
            raise ValueError("Discussion not found")
        if discussion["closed"]:
            raise ValueError("This discussion is closed")
        if parent_id:
            parent = conn.execute(
                "SELECT * FROM discussion_posts WHERE id=? AND discussion_id=?",
                (parent_id, int(discussion_id)),
            ).fetchone()
            if not parent:
                raise ValueError("Parent post not found")
        cur = conn.execute(
            """
            INSERT INTO discussion_posts(discussion_id,parent_id,author_id,body)
            VALUES(?,?,?,?)
            """,
            (int(discussion_id), parent_id, actor, body),
        )
        post_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE discussions SET updated_at=? WHERE id=?", (_now(), int(discussion_id))
        )
        record("DiscussionPostCreated", actor, "discussion_post", post_id,
               {"discussion_id": int(discussion_id)}, conn=conn)
    return row("SELECT * FROM discussion_posts WHERE id=?", (post_id,))


def update_post(post_id, data, actor="admin") -> dict:
    body = str(data.get("body", "")).strip()
    if not body:
        raise ValueError("A post body is required")
    with connection() as conn:
        post = conn.execute(
            "SELECT * FROM discussion_posts WHERE id=?", (int(post_id),)
        ).fetchone()
        if not post:
            raise ValueError("Post not found")
        conn.execute(
            "UPDATE discussion_posts SET body=?, edited_at=? WHERE id=?",
            (body, _now(), int(post_id)),
        )
        record("DiscussionPostUpdated", actor, "discussion_post", int(post_id), {}, conn=conn)
    return row("SELECT * FROM discussion_posts WHERE id=?", (int(post_id),))


def delete_post(post_id, actor="admin") -> dict:
    with connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM discussion_posts WHERE id=?", (int(post_id),)
        ).fetchone():
            raise ValueError("Post not found")
        conn.execute("DELETE FROM discussion_posts WHERE id=?", (int(post_id),))
        record("DiscussionPostDeleted", actor, "discussion_post", int(post_id), {}, conn=conn)
    return {"deleted": True, "id": int(post_id)}


def can_post(employee_id, course_code) -> bool:
    return bool(row(
        """
        SELECT 1 FROM assignments
        WHERE employee_id=? AND course_code=? LIMIT 1
        """,
        (employee_id, _code(course_code)),
    ))


__all__ = [
    "PATH_STATUSES", "add_cohort_member", "add_path_course", "award_badge",
    "cohorts_for_employee", "create_badge", "create_cohort", "create_discussion",
    "create_path", "create_post", "delete_badge", "delete_cohort", "delete_discussion",
    "delete_path", "delete_post", "earned_badges", "enroll_path", "get_badge",
    "get_cohort", "get_course_grade", "get_discussion", "get_path", "get_post", "gradebook",
    "list_badges", "list_cohorts", "list_discussions", "list_paths",
    "on_course_completed", "remove_cohort_member", "remove_path_course",
    "update_badge", "update_cohort", "update_discussion", "update_path", "update_post",
    "verify_certificate",
]
