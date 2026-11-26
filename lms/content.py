"""Learning content: catalog, curriculum, delivery, progress and quizzes.

Courses are the catalog record; ``course_modules`` and ``lessons`` hold the
curriculum. Progress is tracked per employee and lesson, and an assignment is
completed automatically once every required lesson (and any required quiz) is
done. Authoring rules and validation live here, not in the HTTP layer.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from . import media, services
from .audit import record
from .db import connection, row

LESSON_TYPES = ("text", "video", "youtube", "link", "quiz")
COURSE_STATUSES = ("draft", "published", "archived")
LEVELS = ("Beginner", "Intermediate", "Advanced", "All levels")
ACTIVE_ASSIGNMENT_STATUSES = ("assigned", "in_progress", "failed")

_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_PATTERNS = (
    re.compile(r"(?:youtube\.com/watch\?(?:[^#]*&)?v=)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtu\.be/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/live/)([A-Za-z0-9_-]{11})"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def normalize_youtube(value) -> str:
    """Accept a YouTube URL or bare id and return the 11-character video id."""
    text = str(value or "").strip()
    if _YOUTUBE_ID.match(text):
        return text
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        query = parse_qs(parsed.query)
        candidate = (query.get("v") or [""])[0]
        if _YOUTUBE_ID.match(candidate):
            return candidate
    for pattern in _YOUTUBE_PATTERNS:
        match = pattern.search(text)
        if match and _YOUTUBE_ID.match(match.group(1)):
            return match.group(1)
    raise ValueError("Enter a valid YouTube URL or 11-character video id")


def _validate_url(value, field: str) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{field} must be an http(s) URL")
    return text


def _split_lines(value) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _split_commas(value) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _decorate_course(item: dict) -> dict:
    item["objectives_list"] = _split_lines(item.get("objectives"))
    item["tags_list"] = _split_commas(item.get("tags"))
    item["prerequisite_list"] = _split_commas(item.get("prerequisite_codes"))
    if item.get("thumbnail_media_id"):
        item["thumbnail"] = media.url_for(item["thumbnail_media_id"])
    else:
        item["thumbnail"] = item.get("thumbnail_url") or ""
    item["is_published"] = item.get("status") == "published"
    item["mandatory"] = bool(item.get("mandatory"))
    item["active"] = bool(item.get("active"))
    return item


def _latest_assignment(conn, employee_id, course_code):
    found = conn.execute(
        "SELECT * FROM assignments WHERE employee_id=? AND course_code=? "
        "ORDER BY cycle DESC LIMIT 1",
        (employee_id, course_code),
    ).fetchone()
    return dict(found) if found else None


def _progress_summary(conn, employee_id, course_code) -> dict:
    totals = conn.execute(
        """
        SELECT COUNT(*) total,
               COALESCE(SUM(CASE WHEN is_required=1 THEN 1 ELSE 0 END),0) required
        FROM lessons WHERE course_code=?
        """,
        (course_code,),
    ).fetchone()
    completed = conn.execute(
        """
        SELECT COUNT(*) FROM lesson_progress p
        JOIN lessons l ON l.id=p.lesson_id
        WHERE p.employee_id=? AND l.course_code=? AND p.status='completed'
        """,
        (employee_id, course_code),
    ).fetchone()[0]
    completed_required = conn.execute(
        """
        SELECT COUNT(*) FROM lesson_progress p
        JOIN lessons l ON l.id=p.lesson_id
        WHERE p.employee_id=? AND l.course_code=? AND l.is_required=1
          AND p.status='completed'
        """,
        (employee_id, course_code),
    ).fetchone()[0]
    total = int(totals["total"] or 0)
    required = int(totals["required"] or 0)
    percent = round(100 * int(completed_required) / required, 1) if required else 0
    return {
        "total_lessons": total,
        "required_lessons": required,
        "completed_lessons": int(completed),
        "completed_required": int(completed_required),
        "percent": percent,
        "is_complete": bool(required) and int(completed_required) >= required,
    }


def _video_ttl(lesson) -> int:
    """Keep a lesson's signed stream valid long enough to finish watching it."""
    minutes = int(lesson.get("duration_minutes") or 0)
    return max(900, minutes * 60 + 600)


def _serialize_lesson(lesson, progress, conn) -> dict:
    item = dict(lesson)
    item["type"] = item.pop("lesson_type")
    item["is_required"] = bool(item.get("is_required"))
    item["question_count"] = int(conn.execute(
        "SELECT COUNT(*) FROM quiz_questions WHERE lesson_id=?", (item["id"],)
    ).fetchone()[0])
    if item["type"] == "youtube":
        item["youtube_id"] = item["content"]
    elif item["type"] == "video":
        item["stream_url"] = (
            media.url_for(item["media_id"], ttl=_video_ttl(item))
            if item.get("media_id") else item.get("content", "")
        )
    item["progress"] = progress
    return item


def _course_or_raise(conn, course_code):
    course = conn.execute(
        "SELECT * FROM courses WHERE course_code=?", (str(course_code).upper(),)
    ).fetchone()
    if not course:
        raise ValueError(f"Unknown course: {course_code}")
    return course


# ---------------------------------------------------------------------------
# Catalog reads
# ---------------------------------------------------------------------------

def list_courses(actor=None, *, include_unpublished=False, status=None,
                 category=None, q=None, active_only=False) -> list[dict]:
    clauses, params = [], []
    if not include_unpublished:
        clauses.append("c.status='published'")
        if status and str(status).strip().lower() != "published":
            clauses.append("1 = 0")
    elif status:
        clauses.append("c.status=?")
        params.append(status)
    if not include_unpublished or active_only:
        clauses.append("c.active=1")
    if category:
        clauses.append("c.category=?")
        params.append(category)
    if q:
        clauses.append("(c.title LIKE ? OR c.course_code LIKE ? OR c.description LIKE ? "
                       "OR c.tags LIKE ? OR c.author LIKE ?)")
        like = f"%{q}%"
        params.extend([like] * 5)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connection() as conn:
        items = [dict(r) for r in conn.execute(
            f"""
            SELECT c.*,
              (SELECT COUNT(*) FROM course_modules m WHERE m.course_code=c.course_code) module_count,
              (SELECT COUNT(*) FROM lessons l WHERE l.course_code=c.course_code) lesson_count,
              (SELECT COALESCE(SUM(l.duration_minutes),0) FROM lessons l
                 WHERE l.course_code=c.course_code) content_minutes
            FROM courses c{where}
            ORDER BY CASE c.status WHEN 'published' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,
                     c.title
            """,
            params,
        ).fetchall()]
        for item in items:
            _decorate_course(item)
            if actor:
                item["my_progress"] = _progress_summary(conn, actor, item["course_code"])
                item["assignment"] = _latest_assignment(conn, actor, item["course_code"])
    return items


def get_course_detail(course_code, actor=None, *, include_unpublished=False):
    with connection() as conn:
        course = conn.execute(
            "SELECT * FROM courses WHERE course_code=?", (str(course_code).upper(),)
        ).fetchone()
        if not course:
            return None
        course = dict(course)
        if course["status"] != "published" and not include_unpublished:
            return None
        progress_map = {}
        if actor:
            for item in conn.execute(
                """
                SELECT p.* FROM lesson_progress p
                JOIN lessons l ON l.id=p.lesson_id
                WHERE p.employee_id=? AND l.course_code=?
                """,
                (actor, course["course_code"]),
            ).fetchall():
                progress_map[item["lesson_id"]] = dict(item)
        modules = [dict(r) for r in conn.execute(
            "SELECT * FROM course_modules WHERE course_code=? ORDER BY position, id",
            (course["course_code"],),
        ).fetchall()]
        for module in modules:
            lessons = conn.execute(
                "SELECT * FROM lessons WHERE module_id=? ORDER BY position, id",
                (module["id"],),
            ).fetchall()
            module["lessons"] = [
                _serialize_lesson(lesson, progress_map.get(lesson["id"]), conn)
                for lesson in lessons
            ]
            module["lesson_count"] = len(module["lessons"])
            module["duration_minutes"] = sum(
                int(lesson.get("duration_minutes") or 0) for lesson in module["lessons"]
            )
        _decorate_course(course)
        course["modules"] = modules
        course["module_count"] = len(modules)
        course["lesson_count"] = sum(module["lesson_count"] for module in modules)
        course["content_minutes"] = sum(module["duration_minutes"] for module in modules)
        course["progress"] = _progress_summary(conn, actor, course["course_code"]) if actor else None
        course["assignment"] = _latest_assignment(conn, actor, course["course_code"]) if actor else None
        course["quiz_attempts"] = [
            dict(r) for r in conn.execute(
                """
                SELECT qa.* FROM quiz_attempts qa
                JOIN lessons l ON l.id=qa.lesson_id
                WHERE qa.employee_id=? AND l.course_code=?
                ORDER BY qa.id DESC LIMIT 20
                """,
                (actor, course["course_code"]),
            ).fetchall()
        ] if actor else []
        return course


# ---------------------------------------------------------------------------
# Course authoring
# ---------------------------------------------------------------------------

def create_course(data, actor="admin"):
    course = services.create_course(data, actor)
    return get_course_detail(course["course_code"], actor=actor, include_unpublished=True)


def update_course(course_code, data, actor="admin"):
    course_code = str(course_code).strip().upper()
    allowed = {
        "title", "description", "summary", "category", "level", "language",
        "thumbnail_url", "thumbnail_media_id", "author", "objectives", "tags",
        "status", "prerequisite_codes", "duration_minutes", "mandatory",
        "version", "recertification_days", "passing_score", "content_url", "active",
    }
    updates = {key: data[key] for key in allowed if key in data}
    if not updates:
        raise ValueError("No supported fields were supplied")
    if "title" in updates and not str(updates["title"]).strip():
        raise ValueError("title cannot be empty")
    if "status" in updates and str(updates["status"]) not in COURSE_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(COURSE_STATUSES)}")
    if "level" in updates and str(updates["level"]) not in LEVELS:
        raise ValueError(f"level must be one of: {', '.join(LEVELS)}")
    if "duration_minutes" in updates:
        updates["duration_minutes"] = int(updates["duration_minutes"])
        if not 1 <= updates["duration_minutes"] <= 100_000:
            raise ValueError("duration_minutes must be between 1 and 100000")
    if "recertification_days" in updates:
        updates["recertification_days"] = int(updates["recertification_days"])
        if updates["recertification_days"] < 0:
            raise ValueError("recertification_days cannot be negative")
    if "passing_score" in updates:
        if updates["passing_score"] in (None, ""):
            updates["passing_score"] = None
        else:
            updates["passing_score"] = float(updates["passing_score"])
            if not 0 <= updates["passing_score"] <= 100:
                raise ValueError("passing_score must be between 0 and 100")
    if "thumbnail_media_id" in updates:
        if updates["thumbnail_media_id"] in (None, "", 0):
            updates["thumbnail_media_id"] = None
        else:
            updates["thumbnail_media_id"] = int(updates["thumbnail_media_id"])
            item = media.get(updates["thumbnail_media_id"])
            if not item or item["kind"] != "image":
                raise ValueError("thumbnail_media_id must reference an uploaded image")
    for flag in ("mandatory", "active"):
        if flag in updates:
            updates[flag] = 1 if updates[flag] else 0
    for key in ("thumbnail_url", "content_url"):
        if key in updates and updates[key]:
            updates[key] = _validate_url(updates[key], key)
    updates["updated_at"] = _now()
    updates["updated_by"] = actor
    with connection() as conn:
        _course_or_raise(conn, course_code)
        assignments = ", ".join(f"{key}=?" for key in updates)
        conn.execute(
            f"UPDATE courses SET {assignments} WHERE course_code=?",
            list(updates.values()) + [course_code],
        )
        record("CourseUpdated", actor, "course", course_code,
               {"fields": sorted(updates)}, conn=conn)
    return get_course_detail(course_code, actor=actor, include_unpublished=True)


# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------

def create_module(course_code, data, actor="admin"):
    course_code = str(course_code).strip().upper()
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("A module title is required")
    with connection() as conn:
        _course_or_raise(conn, course_code)
        position = data.get("position")
        if position in (None, ""):
            position = conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM course_modules WHERE course_code=?",
                (course_code,),
            ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO course_modules(course_code,title,summary,position) VALUES(?,?,?,?)",
            (course_code, title, str(data.get("summary", "")), int(position)),
        )
        module_id = int(cur.lastrowid)
        record("ModuleCreated", actor, "module", module_id,
               {"course_code": course_code, "title": title}, conn=conn)
    return row("SELECT * FROM course_modules WHERE id=?", (module_id,))


def update_module(module_id, data, actor="admin"):
    updates = {}
    if "title" in data:
        title = str(data["title"]).strip()
        if not title:
            raise ValueError("A module title is required")
        updates["title"] = title
    if "summary" in data:
        updates["summary"] = str(data["summary"])
    if "position" in data:
        updates["position"] = int(data["position"])
    if not updates:
        raise ValueError("No supported fields were supplied")
    updates["updated_at"] = _now()
    with connection() as conn:
        module = conn.execute("SELECT * FROM course_modules WHERE id=?", (int(module_id),)).fetchone()
        if not module:
            raise ValueError("Module not found")
        conn.execute(
            f"UPDATE course_modules SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
            list(updates.values()) + [int(module_id)],
        )
        record("ModuleUpdated", actor, "module", int(module_id),
               {"fields": sorted(updates)}, conn=conn)
    return row("SELECT * FROM course_modules WHERE id=?", (int(module_id),))


def delete_module(module_id, actor="admin"):
    with connection() as conn:
        module = conn.execute("SELECT * FROM course_modules WHERE id=?", (int(module_id),)).fetchone()
        if not module:
            raise ValueError("Module not found")
        lessons = conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE module_id=?", (int(module_id),)
        ).fetchone()[0]
        conn.execute("DELETE FROM course_modules WHERE id=?", (int(module_id),))
        record("ModuleDeleted", actor, "module", int(module_id),
               {"course_code": module["course_code"], "lessons": int(lessons)}, conn=conn)
    return {"deleted": True, "id": int(module_id)}


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------

def _prepare_lesson(data, lesson_type, existing=None):
    def pick(key, default=None):
        return data[key] if key in data else (existing[key] if existing else default)

    content = str(pick("content", "") or "").strip()
    media_id = pick("media_id", None)
    if lesson_type == "youtube":
        content = normalize_youtube(content)
        media_id = None
    elif lesson_type == "link":
        content = _validate_url(content, "content")
        media_id = None
    elif lesson_type == "video":
        if media_id not in (None, "", 0):
            media_id = int(media_id)
            item = media.get(media_id)
            if not item or item["kind"] != "video":
                raise ValueError("media_id must reference an uploaded video")
            content = ""
        elif content:
            content = _validate_url(content, "content")
        else:
            raise ValueError("A video lesson needs an uploaded video or an https video URL")
    elif lesson_type == "text":
        if not content:
            raise ValueError("Text lessons need content")
        media_id = None
    elif lesson_type == "quiz":
        media_id = None
    return content, media_id


def create_lesson(module_id, data, actor="admin"):
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("A lesson title is required")
    lesson_type = str(data.get("lesson_type", "text")).strip().lower()
    if lesson_type not in LESSON_TYPES:
        raise ValueError(f"lesson_type must be one of: {', '.join(LESSON_TYPES)}")
    duration = int(data.get("duration_minutes", 0) or 0)
    if duration < 0 or duration > 100_000:
        raise ValueError("duration_minutes must be between 0 and 100000")
    raw_score = data.get("quiz_pass_score", 70)
    pass_score = 70 if raw_score in (None, "") else float(raw_score)
    if not 0 <= pass_score <= 100:
        raise ValueError("quiz_pass_score must be between 0 and 100")
    content, media_id = _prepare_lesson(data, lesson_type)
    with connection() as conn:
        module = conn.execute(
            "SELECT * FROM course_modules WHERE id=?", (int(module_id),)
        ).fetchone()
        if not module:
            raise ValueError("Module not found")
        position = data.get("position")
        if position in (None, ""):
            position = conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM lessons WHERE module_id=?",
                (int(module_id),),
            ).fetchone()[0]
        cur = conn.execute(
            """
            INSERT INTO lessons(module_id,course_code,title,lesson_type,content,media_id,
                                duration_minutes,position,is_required,quiz_pass_score)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (int(module_id), module["course_code"], title, lesson_type, content,
             media_id, duration, int(position),
             1 if data.get("is_required", True) else 0, pass_score),
        )
        lesson_id = int(cur.lastrowid)
        record("LessonCreated", actor, "lesson", lesson_id,
               {"course_code": module["course_code"], "type": lesson_type, "title": title},
               conn=conn)
    return _lesson_with_counts(lesson_id)


def _lesson_with_counts(lesson_id):
    with connection() as conn:
        lesson = conn.execute("SELECT * FROM lessons WHERE id=?", (int(lesson_id),)).fetchone()
        if not lesson:
            return None
        return _serialize_lesson(lesson, None, conn)


def update_lesson(lesson_id, data, actor="admin"):
    with connection() as conn:
        existing = conn.execute("SELECT * FROM lessons WHERE id=?", (int(lesson_id),)).fetchone()
        if not existing:
            raise ValueError("Lesson not found")
        lesson_type = str(data.get("lesson_type", existing["lesson_type"])).strip().lower()
        if lesson_type not in LESSON_TYPES:
            raise ValueError(f"lesson_type must be one of: {', '.join(LESSON_TYPES)}")
        content, media_id = _prepare_lesson(data, lesson_type, existing)
        title = str(data.get("title", existing["title"])).strip()
        if not title:
            raise ValueError("A lesson title is required")
        duration = int(data.get("duration_minutes", existing["duration_minutes"]) or 0)
        if duration < 0 or duration > 100_000:
            raise ValueError("duration_minutes must be between 0 and 100000")
        raw_score = data.get("quiz_pass_score", existing["quiz_pass_score"])
        pass_score = float(existing["quiz_pass_score"]) if raw_score in (None, "") else float(raw_score)
        if not 0 <= pass_score <= 100:
            raise ValueError("quiz_pass_score must be between 0 and 100")
        is_required = 1 if data.get("is_required", bool(existing["is_required"])) else 0
        position = int(data.get("position", existing["position"]))
        conn.execute(
            """
            UPDATE lessons SET title=?, lesson_type=?, content=?, media_id=?,
                   duration_minutes=?, position=?, is_required=?, quiz_pass_score=?,
                   updated_at=?
            WHERE id=?
            """,
            (title, lesson_type, content, media_id, duration, position, is_required,
             pass_score, _now(), int(lesson_id)),
        )
        record("LessonUpdated", actor, "lesson", int(lesson_id),
               {"fields": sorted(data), "type": lesson_type}, conn=conn)
    return _lesson_with_counts(lesson_id)


def delete_lesson(lesson_id, actor="admin"):
    with connection() as conn:
        lesson = conn.execute("SELECT * FROM lessons WHERE id=?", (int(lesson_id),)).fetchone()
        if not lesson:
            raise ValueError("Lesson not found")
        conn.execute("DELETE FROM lessons WHERE id=?", (int(lesson_id),))
        record("LessonDeleted", actor, "lesson", int(lesson_id),
               {"course_code": lesson["course_code"], "title": lesson["title"]}, conn=conn)
    return {"deleted": True, "id": int(lesson_id)}


# ---------------------------------------------------------------------------
# Quizzes
# ---------------------------------------------------------------------------

def _options(data) -> list[str]:
    options = data.get("options")
    if isinstance(options, str):
        options = [line.strip() for line in options.splitlines() if line.strip()]
    if not isinstance(options, list) or not 2 <= len(options) <= 8:
        raise ValueError("A question needs between 2 and 8 options")
    options = [str(option).strip() for option in options]
    if any(not option for option in options):
        raise ValueError("Question options cannot be empty")
    return options


def add_question(lesson_id, data, actor="admin"):
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("A question prompt is required")
    options = _options(data)
    correct = int(data.get("correct_index", 0))
    if not 0 <= correct < len(options):
        raise ValueError("correct_index is outside the option list")
    with connection() as conn:
        lesson = conn.execute("SELECT * FROM lessons WHERE id=?", (int(lesson_id),)).fetchone()
        if not lesson:
            raise ValueError("Lesson not found")
        if lesson["lesson_type"] != "quiz":
            raise ValueError("Questions can only be added to quiz lessons")
        position = conn.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM quiz_questions WHERE lesson_id=?",
            (int(lesson_id),),
        ).fetchone()[0]
        cur = conn.execute(
            """
            INSERT INTO quiz_questions(lesson_id,prompt,options_json,correct_index,explanation,position)
            VALUES(?,?,?,?,?,?)
            """,
            (int(lesson_id), prompt, json.dumps(options), correct,
             str(data.get("explanation", "")), int(position)),
        )
        question_id = int(cur.lastrowid)
        record("QuizQuestionCreated", actor, "quiz_question", question_id,
               {"lesson_id": int(lesson_id)}, conn=conn)
    return _question(question_id, reveal=True)


def _question(question_id, reveal=False):
    with connection() as conn:
        item = conn.execute(
            "SELECT * FROM quiz_questions WHERE id=?", (int(question_id),)
        ).fetchone()
    if not item:
        return None
    item = dict(item)
    item["options"] = json.loads(item.pop("options_json") or "[]")
    if not reveal:
        item.pop("correct_index", None)
        item.pop("explanation", None)
    return item


def update_question(question_id, data, actor="admin"):
    with connection() as conn:
        existing = conn.execute(
            "SELECT * FROM quiz_questions WHERE id=?", (int(question_id),)
        ).fetchone()
        if not existing:
            raise ValueError("Question not found")
        prompt = str(data.get("prompt", existing["prompt"])).strip()
        if not prompt:
            raise ValueError("A question prompt is required")
        options = _options(data) if "options" in data else json.loads(existing["options_json"])
        correct = int(data.get("correct_index", existing["correct_index"]))
        if not 0 <= correct < len(options):
            raise ValueError("correct_index is outside the option list")
        conn.execute(
            "UPDATE quiz_questions SET prompt=?, options_json=?, correct_index=?, explanation=? "
            "WHERE id=?",
            (prompt, json.dumps(options), correct,
             str(data.get("explanation", existing["explanation"])), int(question_id)),
        )
        record("QuizQuestionUpdated", actor, "quiz_question", int(question_id),
               {"fields": sorted(data)}, conn=conn)
    return _question(question_id, reveal=True)


def delete_question(question_id, actor="admin"):
    with connection() as conn:
        existing = conn.execute(
            "SELECT * FROM quiz_questions WHERE id=?", (int(question_id),)
        ).fetchone()
        if not existing:
            raise ValueError("Question not found")
        conn.execute("DELETE FROM quiz_questions WHERE id=?", (int(question_id),))
        record("QuizQuestionDeleted", actor, "quiz_question", int(question_id),
               {"lesson_id": existing["lesson_id"]}, conn=conn)
    return {"deleted": True, "id": int(question_id)}


def list_questions(lesson_id, *, reveal=False):
    with connection() as conn:
        lesson = conn.execute("SELECT * FROM lessons WHERE id=?", (int(lesson_id),)).fetchone()
        if not lesson:
            return None
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM quiz_questions WHERE lesson_id=? ORDER BY position, id",
            (int(lesson_id),),
        ).fetchall()]
    for item in items:
        item["options"] = json.loads(item.pop("options_json") or "[]")
        if not reveal:
            item.pop("correct_index", None)
            item.pop("explanation", None)
    return {
        "lesson_id": int(lesson_id),
        "title": lesson["title"],
        "pass_score": float(lesson["quiz_pass_score"] or 0),
        "reveal": reveal,
        "questions": items,
    }


# ---------------------------------------------------------------------------
# Learner progress and quizzes
# ---------------------------------------------------------------------------

def get_progress(employee_id, course_code):
    course_code = str(course_code).upper()
    with connection() as conn:
        course = conn.execute(
            "SELECT course_code,title FROM courses WHERE course_code=?", (course_code,)
        ).fetchone()
        if not course:
            return None
        progress_map = {
            item["lesson_id"]: dict(item) for item in conn.execute(
                """
                SELECT p.* FROM lesson_progress p
                JOIN lessons l ON l.id=p.lesson_id
                WHERE p.employee_id=? AND l.course_code=?
                """,
                (employee_id, course_code),
            ).fetchall()
        }
        modules = [dict(r) for r in conn.execute(
            "SELECT * FROM course_modules WHERE course_code=? ORDER BY position, id",
            (course_code,),
        ).fetchall()]
        for module in modules:
            lessons = conn.execute(
                "SELECT * FROM lessons WHERE module_id=? ORDER BY position, id",
                (module["id"],),
            ).fetchall()
            module["lessons"] = [
                _serialize_lesson(lesson, progress_map.get(lesson["id"]), conn)
                for lesson in lessons
            ]
        attempts = [dict(r) for r in conn.execute(
            """
            SELECT qa.* FROM quiz_attempts qa
            JOIN lessons l ON l.id=qa.lesson_id
            WHERE qa.employee_id=? AND l.course_code=?
            ORDER BY qa.id DESC LIMIT 20
            """,
            (employee_id, course_code),
        ).fetchall()]
        summary = _progress_summary(conn, employee_id, course_code)
        assignment = _latest_assignment(conn, employee_id, course_code)
    return {
        "course_code": course_code,
        "course_title": course["title"],
        "employee_id": employee_id,
        "modules": modules,
        "progress": summary,
        "assignment": assignment,
        "quiz_attempts": attempts,
    }


def _upsert_progress(conn, employee_id, lesson_id, *, status, position_seconds=None,
                     completed=False):
    existing = conn.execute(
        "SELECT * FROM lesson_progress WHERE employee_id=? AND lesson_id=?",
        (employee_id, int(lesson_id)),
    ).fetchone()
    if existing and existing["status"] == "completed":
        status = "completed"
    position = existing["position_seconds"] if existing else 0
    if position_seconds is not None:
        position = max(0.0, float(position_seconds))
    completed_at = existing["completed_at"] if existing else None
    if status == "completed" and not completed_at:
        completed_at = _now()
    if existing:
        conn.execute(
            "UPDATE lesson_progress SET status=?, position_seconds=?, completed_at=?, "
            "updated_at=? WHERE id=?",
            (status, position, completed_at, _now(), existing["id"]),
        )
        newly_completed = status == "completed" and existing["status"] != "completed"
    else:
        conn.execute(
            """
            INSERT INTO lesson_progress(employee_id,lesson_id,status,position_seconds,completed_at,updated_at)
            VALUES(?,?,?,?,?,?)
            """,
            (employee_id, int(lesson_id), status, position, completed_at, _now()),
        )
        newly_completed = status == "completed"
    return newly_completed


def record_lesson_progress(employee_id, lesson_id, *, position_seconds=None,
                           completed=False, actor=None):
    actor = actor or employee_id
    with connection() as conn:
        lesson = conn.execute("SELECT * FROM lessons WHERE id=?", (int(lesson_id),)).fetchone()
        if not lesson:
            raise ValueError("Lesson not found")
        if completed and lesson["lesson_type"] == "quiz":
            raise ValueError("Pass the quiz to complete this lesson")
        status = "completed" if completed else "in_progress"
        newly_completed = _upsert_progress(
            conn, employee_id, lesson_id, status=status,
            position_seconds=position_seconds, completed=completed,
        )
        if newly_completed:
            record("LessonCompleted", actor, "lesson", int(lesson_id),
                   {"course_code": lesson["course_code"]}, conn=conn)
        assignment = _latest_assignment(conn, employee_id, lesson["course_code"])
        if assignment and assignment["status"] == "assigned":
            conn.execute(
                "UPDATE assignments SET status='in_progress', "
                "started_at=COALESCE(started_at,?), attempts=MAX(attempts,1), updated_at=? "
                "WHERE id=?",
                (_now(), _now(), assignment["id"]),
            )
        course_code = lesson["course_code"]
    assignment = _maybe_complete(employee_id, course_code)
    return {
        "lesson_id": int(lesson_id),
        "progress": get_progress(employee_id, course_code),
        "assignment": assignment,
    }


def _normalise_answers(answers, count):
    if isinstance(answers, dict):
        values = [answers.get(str(i), answers.get(i, -1)) for i in range(count)]
    elif isinstance(answers, list):
        values = list(answers) + [-1] * max(0, count - len(answers))
    else:
        values = [-1] * count
    result = []
    for value in values[:count]:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            result.append(-1)
    return result


def submit_quiz(employee_id, lesson_id, answers, actor=None):
    actor = actor or employee_id
    with connection() as conn:
        lesson = conn.execute("SELECT * FROM lessons WHERE id=?", (int(lesson_id),)).fetchone()
        if not lesson:
            raise ValueError("Lesson not found")
        if lesson["lesson_type"] != "quiz":
            raise ValueError("This lesson is not a quiz")
        questions = [dict(r) for r in conn.execute(
            "SELECT * FROM quiz_questions WHERE lesson_id=? ORDER BY position, id",
            (int(lesson_id),),
        ).fetchall()]
        if not questions:
            raise ValueError("This quiz has no questions yet")
        given = _normalise_answers(answers, len(questions))
        correct = sum(1 for question, answer in zip(questions, given)
                      if answer == question["correct_index"])
        score = round(100 * correct / len(questions), 1)
        pass_score = float(lesson["quiz_pass_score"] or 0)
        passed = score >= pass_score
        results = []
        for question, answer in zip(questions, given):
            is_correct = answer == question["correct_index"]
            item = {
                "question_id": question["id"],
                "given_index": answer,
                "correct": is_correct,
            }
            if passed:
                item["explanation"] = question["explanation"]
            results.append(item)
        conn.execute(
            "INSERT INTO quiz_attempts(employee_id,lesson_id,score,passed,answers_json) "
            "VALUES(?,?,?,?,?)",
            (employee_id, int(lesson_id), score, 1 if passed else 0, json.dumps(given)),
        )
        if passed:
            _upsert_progress(conn, employee_id, lesson_id, status="completed",
                             completed=True)
        record("QuizAttempted", actor, "lesson", int(lesson_id),
               {"score": score, "passed": passed, "course_code": lesson["course_code"]},
               conn=conn)
        attempts = int(conn.execute(
            "SELECT COUNT(*) FROM quiz_attempts WHERE employee_id=? AND lesson_id=?",
            (employee_id, int(lesson_id)),
        ).fetchone()[0])
        course_code = lesson["course_code"]
    assignment = _maybe_complete(employee_id, course_code)
    return {
        "lesson_id": int(lesson_id),
        "score": score,
        "pass_score": pass_score,
        "passed": passed,
        "correct": correct,
        "total": len(questions),
        "results": results,
        "attempts": attempts,
        "assignment": assignment,
        "progress": get_progress(employee_id, course_code),
    }


def _maybe_complete(employee_id, course_code):
    with connection() as conn:
        assignment = _latest_assignment(conn, employee_id, course_code)
        if not assignment or assignment["status"] not in ACTIVE_ASSIGNMENT_STATUSES:
            return assignment
        required = conn.execute(
            "SELECT id FROM lessons WHERE course_code=? AND is_required=1",
            (course_code,),
        ).fetchall()
        if not required:
            return assignment
        completed = {
            item["id"] for item in conn.execute(
                """
                SELECT l.id FROM lessons l
                JOIN lesson_progress p ON p.lesson_id=l.id AND p.employee_id=?
                WHERE l.course_code=? AND l.is_required=1 AND p.status='completed'
                """,
                (employee_id, course_code),
            ).fetchall()
        }
        if any(item["id"] not in completed for item in required):
            return assignment
        best = conn.execute(
            """
            SELECT MAX(qa.score) FROM quiz_attempts qa
            JOIN lessons l ON l.id=qa.lesson_id
            WHERE qa.employee_id=? AND l.course_code=? AND qa.passed=1
            """,
            (employee_id, course_code),
        ).fetchone()[0]
    try:
        return services.complete_assignment(employee_id, course_code, best, employee_id)
    except ValueError:
        return row(
            "SELECT * FROM assignments WHERE employee_id=? AND course_code=? "
            "ORDER BY cycle DESC LIMIT 1",
            (employee_id, course_code),
        )


def enroll(employee_id, course_code, actor=None):
    course_code = str(course_code).strip().upper()
    with connection() as conn:
        course = conn.execute(
            "SELECT * FROM courses WHERE course_code=?", (course_code,)
        ).fetchone()
        if not course:
            raise ValueError("Course not found")
        if course["status"] != "published" or not course["active"]:
            raise ValueError("This course is not open for enrollment")
        latest = _latest_assignment(conn, employee_id, course_code)
        if latest:
            return latest
    return services.assign_course(employee_id, course_code, 30, actor or employee_id,
                                  source="self")


__all__ = [
    "COURSE_STATUSES", "LESSON_TYPES", "LEVELS", "add_question", "create_course",
    "create_lesson", "create_module", "delete_lesson", "delete_module",
    "delete_question", "enroll", "get_course_detail", "get_progress", "list_courses",
    "list_questions", "normalize_youtube", "record_lesson_progress", "submit_quiz",
    "update_course", "update_lesson", "update_module", "update_question",
]
