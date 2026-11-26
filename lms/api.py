"""HTTP API surface.

The router is a small regex table so the transport (``lms/server.py``) stays
free of business rules. Every handler returns a :class:`Result`; permission
checks are explicit and colocated with the data they protect.

Paths are versioned under ``/api/v1``. ``/api/...`` remains a compatibility
alias for v0.1 clients and is marked deprecated in the response headers.
"""

from __future__ import annotations

import io
import re
import sqlite3
from dataclasses import dataclass, field

from . import content, engagement, hris, jobs, media, notifications, reports, services
from .auth import issue_token, revoke_jti, rotate_session_epoch
from .config import get_settings
from .db import SCHEMA_VERSION
from .permissions import ROLE_PERMISSIONS, has_permission, permissions_for


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str = "", details=None):
        super().__init__(message or error)
        self.status = status
        self.error = error
        self.message = message or error
        self.details = details

    def payload(self):
        body = {"error": self.error, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return body


@dataclass
class Result:
    payload: object
    status: int = 200
    content_type: str = "application/json; charset=utf-8"
    headers: dict = field(default_factory=dict)


@dataclass
class Request:
    method: str
    path: str
    query: dict
    body: dict
    identity: dict | None
    request_id: str = ""
    remote: str = ""
    fingerprint: str = ""

    @property
    def role(self):
        return (self.identity or {}).get("role")

    @property
    def actor(self):
        return (self.identity or {}).get("actor")

    def can(self, permission: str) -> bool:
        return has_permission(self.role, permission)

    def require(self, permission: str) -> None:
        if not self.identity:
            raise ApiError(401, "unauthorized", "A valid session is required")
        if not self.can(permission):
            raise ApiError(403, "forbidden", f"Role {self.role} lacks {permission}")

    def require_self_or(self, employee_id, permission: str) -> None:
        if self.actor == employee_id:
            return
        self.require(permission)


def _int_param(req: Request, name: str, default: int, minimum=0, maximum=500) -> int:
    raw = req.query.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "validation_error", f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ApiError(400, "validation_error", f"{name} must be between {minimum} and {maximum}")
    return value


def _bool_param(req: Request, name: str) -> bool:
    return str(req.query.get(name, "")).lower() in ("1", "true", "yes")


def _paging(req: Request, default_limit=50):
    return _int_param(req, "limit", default_limit, 1, 500), _int_param(req, "offset", 0, 0, 10_000_000)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def health(req: Request) -> Result:
    settings = get_settings()
    return Result({
        "status": "ok",
        "version": settings.version,
        "schema_version": SCHEMA_VERSION,
        "demo_mode": settings.demo_mode,
    })


def meta(req: Request) -> Result:
    settings = get_settings()
    return Result({
        "version": settings.version,
        "schema_version": SCHEMA_VERSION,
        "auth_mode": settings.auth_mode,
        "demo_mode": settings.demo_mode,
        "smtp_enabled": settings.smtp_enabled,
        "roles": {role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()},
        "assignment_statuses": list(services.ASSIGNMENT_STATUSES),
        "policy_operators": list(services.POLICY_OPERATORS),
        "policy_attributes": list(services.POLICY_ATTRIBUTES),
        "learning_path_statuses": list(engagement.PATH_STATUSES),
        "badge_icons": list(engagement.BADGE_ICONS),
    })


def create_session(req: Request) -> Result:
    settings = get_settings()
    if not settings.demo_mode:
        raise ApiError(403, "disabled",
                       "Demo sessions are disabled; configure enterprise authentication.")
    loopback = {"", "-", "127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
    if req.remote not in loopback and not settings.allow_remote_demo:
        raise ApiError(403, "disabled",
                       "Demo sessions are limited to loopback clients")
    role = str(req.body.get("role", "learner"))
    actor = str(req.body.get("actor", "")).strip()
    if role not in ROLE_PERMISSIONS:
        raise ApiError(400, "validation_error", "Unknown role")
    if not actor:
        raise ApiError(400, "validation_error", "actor is required")
    return Result({
        "token": issue_token(role, actor, fingerprint=req.fingerprint),
        "role": role,
        "actor": actor,
        "expires_in": settings.session_ttl_seconds,
        "permissions": permissions_for(role),
    })


def me(req: Request) -> Result:
    req.require("read")
    employee = services.get_employee(req.actor)
    payload = employee or {"employee_id": req.actor, "display_name": req.actor, "roles": []}
    payload["session_role"] = req.role
    payload["permissions"] = permissions_for(req.role)
    payload["unread_notifications"] = notifications.unread_count(req.actor)
    return Result(payload)


def logout(req: Request) -> Result:
    req.require("read")
    identity = req.identity or {}
    revoke_jti(identity.get("jti", ""), req.actor)
    return Result({"revoked": True})


def rotate_sessions(req: Request) -> Result:
    req.require("manage_learning")
    return Result({"session_epoch": rotate_session_epoch()})


def dashboard(req: Request) -> Result:
    req.require("read")
    if req.role == "manager":
        data = services.team_dashboard(req.actor)
        data["scope"] = "team"
        return Result(data)
    if req.can("manage_people") or req.can("audit"):
        data = services.dashboard()
        data["scope"] = "organization"
        return Result(data)
    data = services.personal_dashboard(req.actor)
    data["scope"] = "personal"
    return Result(data)


def list_employees(req: Request) -> Result:
    req.require("manage_people")
    limit, offset = _paging(req, 100)
    return Result(services.search_employees(
        q=req.query.get("q"), department=req.query.get("department"),
        status=req.query.get("status"), role=req.query.get("role"),
        limit=limit, offset=offset,
    ))


def get_employee(req: Request, employee_id: str) -> Result:
    req.require("read")
    if req.actor != employee_id:
        if not req.can("manage_people") and not req.can("audit") and not (
            req.can("read_team") and services.is_manager_of(req.actor, employee_id)
        ):
            raise ApiError(403, "forbidden", "You may only view your own profile")
    employee = services.get_employee(employee_id)
    if not employee:
        raise ApiError(404, "not_found", "Employee not found")
    return Result(employee)


def create_employee(req: Request) -> Result:
    req.require("manage_people")
    return Result(services.create_employee(req.body, req.actor), 201)


def _may_view_employee(req: Request, employee_id: str) -> bool:
    if req.actor == employee_id:
        return True
    if (req.can("manage_people") or req.can("manage_learning") or
            req.can("complete_any") or req.can("audit")):
        return True
    return req.can("read_team") and services.is_manager_of(req.actor, employee_id)


def list_learners(req: Request) -> Result:
    """People whose learning the current role is allowed to inspect."""
    req.require("read")
    q = req.query.get("q")
    if (req.can("manage_people") or req.can("manage_learning") or
            req.can("complete_any") or req.can("audit")):
        found = services.search_employees(q=q, limit=500)["items"]
    elif req.can("read_team"):
        ids = [req.actor] + services.manager_subtree(req.actor)
        found = [services.get_employee(employee_id) for employee_id in ids]
        found = [item for item in found if item]
        if q:
            needle = str(q).lower()
            found = [item for item in found
                     if needle in str(item.get("display_name", "")).lower()
                     or needle in str(item.get("employee_id", "")).lower()]
    else:
        employee = services.get_employee(req.actor) or {
            "employee_id": req.actor, "display_name": req.actor,
        }
        found = [employee]
    return Result([{
        "employee_id": item["employee_id"],
        "display_name": item.get("display_name") or item["employee_id"],
        "department": item.get("department", ""),
    } for item in found])


def list_courses(req: Request) -> Result:
    req.require("read")
    include_unpublished = req.can("manage_content") or req.can("manage_learning")
    return Result(content.list_courses(
        req.actor,
        include_unpublished=include_unpublished,
        status=req.query.get("status"),
        category=req.query.get("category"),
        q=req.query.get("q"),
        active_only=_bool_param(req, "active_only"),
    ))


def get_course(req: Request, course_code: str) -> Result:
    req.require("read")
    include_unpublished = req.can("manage_content") or req.can("manage_learning")
    course = content.get_course_detail(
        course_code, actor=req.actor, include_unpublished=include_unpublished
    )
    if not course:
        raise ApiError(404, "not_found", "Course not found")
    return Result(course)


def create_course(req: Request) -> Result:
    req.require("manage_content")
    return Result(content.create_course(req.body, req.actor), 201)


def update_course(req: Request, course_code: str) -> Result:
    req.require("manage_content")
    return Result(content.update_course(course_code, req.body, req.actor))


def enroll_course(req: Request, course_code: str) -> Result:
    req.require("read")
    employee_id = str(req.body.get("employee_id") or req.actor).strip()
    if employee_id != req.actor and not req.can("manage_learning"):
        raise ApiError(403, "forbidden", "You may only enroll yourself")
    return Result(content.enroll(employee_id, course_code, req.actor), 201)


def course_progress(req: Request, course_code: str) -> Result:
    req.require("read")
    employee_id = str(req.query.get("employee_id") or req.actor).strip()
    if not _may_view_employee(req, employee_id):
        raise ApiError(403, "forbidden", "You may not view this learner's progress")
    data = content.get_progress(employee_id, course_code)
    if not data:
        raise ApiError(404, "not_found", "Course not found")
    return Result(data)


def create_module(req: Request, course_code: str) -> Result:
    req.require("manage_content")
    return Result(content.create_module(course_code, req.body, req.actor), 201)


def update_module(req: Request, module_id: str) -> Result:
    req.require("manage_content")
    return Result(content.update_module(int(module_id), req.body, req.actor))


def delete_module(req: Request, module_id: str) -> Result:
    req.require("manage_content")
    return Result(content.delete_module(int(module_id), req.actor))


def create_lesson(req: Request, module_id: str) -> Result:
    req.require("manage_content")
    return Result(content.create_lesson(int(module_id), req.body, req.actor), 201)


def update_lesson(req: Request, lesson_id: str) -> Result:
    req.require("manage_content")
    return Result(content.update_lesson(int(lesson_id), req.body, req.actor))


def delete_lesson(req: Request, lesson_id: str) -> Result:
    req.require("manage_content")
    return Result(content.delete_lesson(int(lesson_id), req.actor))


def record_progress(req: Request, lesson_id: str) -> Result:
    req.require("read")
    employee_id = str(req.body.get("employee_id") or req.actor).strip()
    if employee_id != req.actor and not req.can("complete_any"):
        raise ApiError(403, "forbidden", "You may only track your own progress")
    return Result(content.record_lesson_progress(
        employee_id, int(lesson_id),
        position_seconds=req.body.get("position_seconds"),
        completed=bool(req.body.get("completed")),
        actor=req.actor,
    ))


def lesson_quiz(req: Request, lesson_id: str) -> Result:
    req.require("read")
    data = content.list_questions(int(lesson_id), reveal=req.can("manage_content"))
    if not data:
        raise ApiError(404, "not_found", "Lesson not found")
    return Result(data)


def submit_quiz(req: Request, lesson_id: str) -> Result:
    req.require("read")
    employee_id = str(req.body.get("employee_id") or req.actor).strip()
    if employee_id != req.actor and not req.can("complete_any"):
        raise ApiError(403, "forbidden", "You may only submit your own quiz")
    return Result(content.submit_quiz(
        employee_id, int(lesson_id), req.body.get("answers", []), req.actor
    ), 201)


def create_question(req: Request, lesson_id: str) -> Result:
    req.require("manage_content")
    return Result(content.add_question(int(lesson_id), req.body, req.actor), 201)


def update_question(req: Request, question_id: str) -> Result:
    req.require("manage_content")
    return Result(content.update_question(int(question_id), req.body, req.actor))


def delete_question(req: Request, question_id: str) -> Result:
    req.require("manage_content")
    return Result(content.delete_question(int(question_id), req.actor))


def is_media_upload(path: str) -> bool:
    api_path, _ = _normalise(path)
    return api_path == "/media"


def handle_media_upload(identity, content_type, filename, raw, request_id=""):
    """Handle a raw binary media upload outside the JSON body parser."""
    try:
        request = Request("POST", "/media", {}, {}, identity, request_id)
        request.require("manage_content")
        stream = io.BytesIO(raw) if isinstance(raw, (bytes, bytearray)) else raw
        return Result(media.store_stream(filename, content_type, stream, request.actor), 201)
    except ApiError as exc:
        return Result(exc.payload(), exc.status)
    except (ValueError, KeyError, TypeError) as exc:
        return Result({"error": "validation_error", "message": str(exc)}, 400)


def list_policies(req: Request) -> Result:
    req.require("manage_learning")
    return Result(services.list_policies())


def create_policy(req: Request) -> Result:
    req.require("manage_learning")
    return Result(services.create_policy(req.body, req.actor), 201)


def apply_policies(req: Request) -> Result:
    req.require("manage_learning")
    created = services.apply_policies(actor=req.actor)
    return Result({"assignments_created": created})


def list_assignments(req: Request) -> Result:
    req.require("read")
    employee_id = req.query.get("employee_id")
    if employee_id and req.actor != employee_id:
        allowed = req.can("manage_people") or req.can("complete_any") or req.can("audit") or (
            req.can("read_team") and services.is_manager_of(req.actor, employee_id)
        )
        if not allowed:
            raise ApiError(403, "forbidden", "You may only view your own assignments")
    employee_ids = None
    if not employee_id:
        if req.can("manage_people") or req.can("complete_any") or req.can("audit"):
            pass  # Organization-wide readers keep the unfiltered view.
        elif req.can("read_team"):
            employee_ids = [req.actor] + services.manager_subtree(req.actor)
        else:
            employee_id = req.actor
    limit, offset = _paging(req, 100)
    return Result(services.search_assignments(
        employee_id=employee_id,
        employee_ids=employee_ids,
        status=req.query.get("status"),
        course_code=req.query.get("course_code"),
        department=req.query.get("department"),
        overdue=_bool_param(req, "overdue"),
        q=req.query.get("q"),
        limit=limit, offset=offset,
    ))


def create_assignment(req: Request) -> Result:
    req.require("manage_learning")
    employee_id = str(req.body.get("employee_id", "")).strip()
    course_code = str(req.body.get("course_code", "")).strip()
    if not employee_id or not course_code:
        raise ApiError(400, "validation_error", "employee_id and course_code are required")
    due_days = int(req.body.get("due_days", 30))
    return Result(services.assign_course(employee_id, course_code, due_days, req.actor), 201)


def _assignment_target(req: Request) -> tuple[str, str]:
    employee_id = str(req.body.get("employee_id", "")).strip()
    course_code = str(req.body.get("course_code", "")).strip()
    if not employee_id or not course_code:
        raise ApiError(400, "validation_error", "employee_id and course_code are required")
    return employee_id, course_code


def _may_manage_assignment(req: Request, employee_id: str) -> bool:
    if req.can("manage_learning"):
        return True
    return (req.can("complete_team") or req.can("read_team")) and services.is_manager_of(
        req.actor, employee_id
    )


def start_assignment(req: Request) -> Result:
    req.require("read")
    employee_id, course_code = _assignment_target(req)
    if req.actor != employee_id and not (
        req.can("complete_any") or _may_manage_assignment(req, employee_id)
    ):
        raise ApiError(403, "forbidden", "You may only start your own assignments")
    return Result(services.start_assignment(employee_id, course_code, req.actor))


def complete_assignment(req: Request) -> Result:
    req.require("read")
    employee_id, course_code = _assignment_target(req)
    if req.actor != employee_id:
        if not (req.can("complete_any") or (
            req.can("complete_team") and services.is_manager_of(req.actor, employee_id)
        )):
            raise ApiError(403, "forbidden", "You may not complete this assignment")
    elif not req.can("manage_learning"):
        # Learners cannot self-report a score or bypass required curriculum.
        if req.body.get("score") not in (None, ""):
            raise ApiError(403, "forbidden", "Scores are recorded from assessed learning")
        detail = content.get_course_detail(
            course_code, actor=req.actor, include_unpublished=True
        )
        if detail and detail.get("progress", {}).get("required_lessons"):
            raise ApiError(403, "forbidden", "Complete the required learning first")
    return Result(services.complete_assignment(
        employee_id, course_code, req.body.get("score"), req.actor
    ))


def exempt_assignment(req: Request) -> Result:
    req.require("read")
    employee_id, course_code = _assignment_target(req)
    if not _may_manage_assignment(req, employee_id):
        raise ApiError(403, "forbidden", "You may not exempt this assignment")
    return Result(services.exempt_assignment(
        employee_id, course_code, req.body.get("reason", ""), req.actor
    ))


def extend_assignment(req: Request) -> Result:
    req.require("read")
    employee_id, course_code = _assignment_target(req)
    if not _may_manage_assignment(req, employee_id):
        raise ApiError(403, "forbidden", "You may not extend this assignment")
    return Result(services.extend_due_date(
        employee_id, course_code, req.body.get("due_date"), req.actor
    ))


def reassign_course(req: Request) -> Result:
    req.require("manage_learning")
    employee_id, course_code = _assignment_target(req)
    return Result(services.reassign_course(
        employee_id, course_code, int(req.body.get("due_days", 30)), req.actor
    ), 201)


def team_dashboard(req: Request) -> Result:
    req.require("read_team")
    return Result(services.team_dashboard(req.actor))


def team_assignments(req: Request) -> Result:
    req.require("read_team")
    limit, offset = _paging(req, 200)
    return Result(services.team_assignments(
        req.actor, status=req.query.get("status"), limit=limit, offset=offset
    ))


def list_notifications(req: Request) -> Result:
    req.require("notifications")
    limit, offset = _paging(req, 50)
    return Result(notifications.list_for(
        req.actor, unread_only=_bool_param(req, "unread_only"),
        limit=limit, offset=offset,
    ))


def mark_notification_read(req: Request, notification_id: str) -> Result:
    req.require("notifications")
    if not notifications.mark_read(notification_id, req.actor):
        raise ApiError(404, "not_found", "Notification not found")
    return Result({"status": "read", "unread": notifications.unread_count(req.actor)})


def mark_all_notifications_read(req: Request) -> Result:
    req.require("notifications")
    updated = notifications.mark_all_read(req.actor)
    return Result({"updated": updated, "unread": 0})


def notification_sweep(req: Request) -> Result:
    req.require("manage_learning")
    job_id = jobs.enqueue(
        "notification_sweep",
        {"days_soon": 7, "days_recert": 30},
        actor=req.actor,
    )
    return Result({"queued": True, "job_id": job_id}, 202)


def _require_operations_read(req: Request) -> None:
    if not (req.can("audit") or req.can("manage_learning")):
        if not req.identity:
            raise ApiError(401, "unauthorized", "A valid session is required")
        raise ApiError(403, "forbidden", "Operations evidence requires audit or manage_learning")


def _job_id(req: Request, value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "validation_error", "Invalid job id") from exc


def list_jobs(req: Request) -> Result:
    _require_operations_read(req)
    limit, offset = _paging(req, 100)
    return Result(jobs.list_jobs(status=req.query.get("status", ""),
                                 limit=limit, offset=offset))


def get_job(req: Request, job_id: str) -> Result:
    _require_operations_read(req)
    item = jobs.get_job(_job_id(req, job_id))
    if not item:
        raise ApiError(404, "not_found", "Job not found")
    return Result(item)


def queue_job(req: Request) -> Result:
    req.require("manage_learning")
    job_type = str(req.body.get("job_type", "")).strip()
    if job_type not in jobs.JOB_TYPES:
        raise ApiError(400, "validation_error", "Unknown job type")
    payload = req.body.get("payload") or {}
    if not isinstance(payload, dict):
        raise ApiError(400, "validation_error", "payload must be an object")
    job_id = jobs.enqueue(job_type, payload, actor=req.actor)
    return Result({"queued": True, "job_id": job_id}, 202)


def retry_job(req: Request, job_id: str) -> Result:
    req.require("manage_learning")
    if not jobs.retry(_job_id(req, job_id), req.actor):
        raise ApiError(409, "conflict", "Only failed jobs can be retried")
    return Result({"status": "queued", "job_id": int(job_id)})


def list_deliveries(req: Request) -> Result:
    _require_operations_read(req)
    limit, offset = _paging(req, 100)
    return Result(notifications.list_deliveries(
        status=req.query.get("status", ""), limit=limit, offset=offset
    ))


def compliance_csv(req: Request) -> Result:
    req.require("export")
    department = req.query.get("department")
    employee_ids = None
    if req.can("read_team") and not (req.can("manage_people") or req.can("audit")):
        employee_ids = services.manager_subtree(req.actor)
    csv_text = reports.compliance_csv(employee_ids=employee_ids, department=department)
    return Result(
        csv_text,
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="compliance.csv"'},
    )


def audit(req: Request) -> Result:
    req.require("audit")
    limit, offset = _paging(req, 100)
    return Result(services.search_audit(
        event_type=req.query.get("event_type"),
        actor=req.query.get("actor"),
        entity_type=req.query.get("entity_type"),
        limit=limit, offset=offset,
    ))


def import_jobs(req: Request) -> Result:
    req.require("hris")
    return Result(services.import_jobs())


def _hris_request(req: Request) -> tuple[str, str, str, int]:
    csv_text = req.body.get("csv", "")
    if not isinstance(csv_text, str):
        raise ApiError(400, "validation_error", "csv must be a string")
    mode = str(req.body.get("mode", "full")).strip().lower()
    if mode not in ("full", "delta"):
        raise ApiError(400, "validation_error", "mode must be full or delta")
    filename = str(req.body.get("filename", "employee_extract.csv") or "").strip()
    filename = filename[:180] or "employee_extract.csv"
    try:
        threshold = int(req.body.get("max_population_change_pct", 25))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "validation_error",
                       "max_population_change_pct must be an integer") from exc
    return csv_text, mode, filename, threshold


def hris_analyze(req: Request) -> Result:
    req.require("hris")
    csv_text, mode, _, threshold = _hris_request(req)
    result = hris.analyze(csv_text, mode, threshold)
    result.pop("normalized", None)
    return Result(result)


def hris_apply(req: Request) -> Result:
    req.require("hris")
    csv_text, mode, filename, threshold = _hris_request(req)
    result = hris.apply(csv_text, filename, mode, req.actor, threshold)
    return Result(result, 201)


# ---------------------------------------------------------------------------
# Engagement: cohorts, learning paths, gradebook, credentials, discussions
# ---------------------------------------------------------------------------

def _require_manage_engagement(req: Request) -> None:
    req.require("manage_learning")


def _engagement_learner(req: Request, source: str = "query") -> str:
    values = req.query if source == "query" else req.body
    employee_id = str(values.get("employee_id") or req.actor).strip()
    if not _may_view_employee(req, employee_id):
        raise ApiError(403, "forbidden", "You may only view this learner's records")
    return employee_id


def _ensure_course_discussion_access(req: Request, course_code: str) -> None:
    if req.can("manage_content") or req.can("manage_learning"):
        return
    if not engagement.can_post(req.actor, course_code):
        raise ApiError(403, "forbidden", "Enroll in this course to join its discussion")


def _discussion_for_request(req: Request, discussion_id: str) -> dict:
    data = engagement.get_discussion(int(discussion_id))
    if not data:
        raise ApiError(404, "not_found", "Discussion not found")
    _ensure_course_discussion_access(req, data["course_code"])
    return data


def list_cohorts(req: Request) -> Result:
    _require_manage_engagement(req)
    return Result(engagement.list_cohorts(
        q=req.query.get("q"), active_only=_bool_param(req, "active_only")
    ))


def get_cohort(req: Request, cohort_id: str) -> Result:
    _require_manage_engagement(req)
    data = engagement.get_cohort(int(cohort_id))
    if not data:
        raise ApiError(404, "not_found", "Cohort not found")
    return Result(data)


def my_cohorts(req: Request) -> Result:
    req.require("read")
    return Result(engagement.cohorts_for_employee(req.actor))


def create_cohort(req: Request) -> Result:
    _require_manage_engagement(req)
    return Result(engagement.create_cohort(req.body, req.actor), 201)


def update_cohort(req: Request, cohort_id: str) -> Result:
    _require_manage_engagement(req)
    return Result(engagement.update_cohort(int(cohort_id), req.body, req.actor))


def delete_cohort(req: Request, cohort_id: str) -> Result:
    _require_manage_engagement(req)
    return Result(engagement.delete_cohort(int(cohort_id), req.actor))


def add_cohort_member(req: Request, cohort_id: str) -> Result:
    _require_manage_engagement(req)
    employee_id = str(req.body.get("employee_id", "")).strip()
    if not employee_id:
        raise ApiError(400, "validation_error", "employee_id is required")
    return Result(engagement.add_cohort_member(int(cohort_id), employee_id, req.actor), 201)


def remove_cohort_member(req: Request, cohort_id: str, employee_id: str) -> Result:
    _require_manage_engagement(req)
    return Result(engagement.remove_cohort_member(int(cohort_id), employee_id, req.actor))


def list_paths(req: Request) -> Result:
    req.require("read")
    include_unpublished = req.can("manage_content") or req.can("manage_learning")
    return Result(engagement.list_paths(
        actor=req.actor,
        include_unpublished=include_unpublished,
        q=req.query.get("q"),
    ))


def get_path(req: Request, path_id: str) -> Result:
    req.require("read")
    include_unpublished = req.can("manage_content") or req.can("manage_learning")
    data = engagement.get_path(int(path_id), actor=req.actor,
                               include_unpublished=include_unpublished)
    if not data:
        raise ApiError(404, "not_found", "Learning path not found")
    return Result(data)


def create_path(req: Request) -> Result:
    if not (req.can("manage_content") or req.can("manage_learning")):
        req.require("manage_learning")
    return Result(engagement.create_path(req.body, req.actor), 201)


def update_path(req: Request, path_id: str) -> Result:
    if not (req.can("manage_content") or req.can("manage_learning")):
        req.require("manage_learning")
    return Result(engagement.update_path(int(path_id), req.body, req.actor))


def delete_path(req: Request, path_id: str) -> Result:
    if not (req.can("manage_content") or req.can("manage_learning")):
        req.require("manage_learning")
    return Result(engagement.delete_path(int(path_id), req.actor))


def add_path_course(req: Request, path_id: str) -> Result:
    if not (req.can("manage_content") or req.can("manage_learning")):
        req.require("manage_learning")
    return Result(engagement.add_path_course(int(path_id), req.body, req.actor), 201)


def remove_path_course(req: Request, path_id: str, course_code: str) -> Result:
    if not (req.can("manage_content") or req.can("manage_learning")):
        req.require("manage_learning")
    return Result(engagement.remove_path_course(int(path_id), course_code, req.actor))


def enroll_path(req: Request, path_id: str) -> Result:
    req.require("read")
    employee_id = str(req.body.get("employee_id") or req.actor).strip()
    if employee_id != req.actor and not req.can("manage_learning"):
        raise ApiError(403, "forbidden", "You may only enroll yourself in a learning path")
    return Result(engagement.enroll_path(employee_id, int(path_id), req.actor), 201)


def gradebook(req: Request) -> Result:
    req.require("read")
    employee_id = _engagement_learner(req)
    return Result({
        "employee_id": employee_id,
        "grades": engagement.gradebook(employee_id),
        "badges": engagement.earned_badges(employee_id),
        "paths": engagement.list_paths(actor=employee_id),
    })


def course_grade(req: Request, course_code: str) -> Result:
    req.require("read")
    employee_id = _engagement_learner(req)
    data = engagement.get_course_grade(employee_id, course_code)
    if not data:
        raise ApiError(404, "not_found", "Course not found")
    return Result(data)


def list_badges(req: Request) -> Result:
    req.require("read")
    include_inactive = req.can("manage_learning") and _bool_param(req, "include_inactive")
    return Result(engagement.list_badges(include_inactive=include_inactive,
                                         employee_id=req.actor))


def get_badge(req: Request, badge_id: str) -> Result:
    req.require("read")
    data = engagement.get_badge(int(badge_id), employee_id=req.actor)
    if not data:
        raise ApiError(404, "not_found", "Badge not found")
    return Result(data)


def create_badge(req: Request) -> Result:
    _require_manage_engagement(req)
    return Result(engagement.create_badge(req.body, req.actor), 201)


def update_badge(req: Request, badge_id: str) -> Result:
    _require_manage_engagement(req)
    return Result(engagement.update_badge(int(badge_id), req.body, req.actor))


def delete_badge(req: Request, badge_id: str) -> Result:
    _require_manage_engagement(req)
    return Result(engagement.delete_badge(int(badge_id), req.actor))


def award_badge(req: Request, badge_id: str) -> Result:
    _require_manage_engagement(req)
    employee_id = str(req.body.get("employee_id", "")).strip()
    if not employee_id:
        raise ApiError(400, "validation_error", "employee_id is required")
    return Result(engagement.award_badge(
        int(badge_id), employee_id, req.actor, req.body.get("score")
    ), 201)


def earned_badges(req: Request) -> Result:
    req.require("read")
    employee_id = _engagement_learner(req)
    return Result(engagement.earned_badges(employee_id))


def verify_certificate(req: Request, code: str) -> Result:
    data = engagement.verify_certificate(code)
    if not data:
        raise ApiError(404, "not_found", "Certificate could not be verified")
    return Result(data)


def list_discussions(req: Request, course_code: str) -> Result:
    req.require("read")
    _ensure_course_discussion_access(req, course_code)
    return Result(engagement.list_discussions(course_code))


def get_discussion(req: Request, discussion_id: str) -> Result:
    req.require("read")
    return Result(_discussion_for_request(req, discussion_id))


def create_discussion(req: Request, course_code: str) -> Result:
    req.require("read")
    _ensure_course_discussion_access(req, course_code)
    return Result(engagement.create_discussion(course_code, req.body, req.actor), 201)


def update_discussion(req: Request, discussion_id: str) -> Result:
    req.require("read")
    data = _discussion_for_request(req, discussion_id)
    if not (req.can("manage_content") or data["created_by"] == req.actor):
        raise ApiError(403, "forbidden", "You may not edit this discussion")
    return Result(engagement.update_discussion(int(discussion_id), req.body, req.actor))


def delete_discussion(req: Request, discussion_id: str) -> Result:
    req.require("read")
    data = _discussion_for_request(req, discussion_id)
    if not (req.can("manage_content") or data["created_by"] == req.actor):
        raise ApiError(403, "forbidden", "You may not delete this discussion")
    return Result(engagement.delete_discussion(int(discussion_id), req.actor))


def create_post(req: Request, discussion_id: str) -> Result:
    req.require("read")
    _discussion_for_request(req, discussion_id)
    return Result(engagement.create_post(int(discussion_id), req.body, req.actor), 201)


def update_post(req: Request, discussion_id: str, post_id: str) -> Result:
    req.require("read")
    _discussion_for_request(req, discussion_id)
    post = engagement.get_post(int(post_id))
    if not post or int(post["discussion_id"]) != int(discussion_id):
        raise ApiError(404, "not_found", "Post not found")
    if not (req.can("manage_content") or post["author_id"] == req.actor):
        raise ApiError(403, "forbidden", "You may not edit this post")
    return Result(engagement.update_post(int(post_id), req.body, req.actor))


def delete_post(req: Request, discussion_id: str, post_id: str) -> Result:
    req.require("read")
    _discussion_for_request(req, discussion_id)
    post = engagement.get_post(int(post_id))
    if not post or int(post["discussion_id"]) != int(discussion_id):
        raise ApiError(404, "not_found", "Post not found")
    if not (req.can("manage_content") or post["author_id"] == req.actor):
        raise ApiError(403, "forbidden", "You may not delete this post")
    return Result(engagement.delete_post(int(post_id), req.actor))


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

Route = tuple[str, re.Pattern, callable]

ROUTES: list[Route] = [
    ("GET", re.compile(r"^/health$"), health),
    ("GET", re.compile(r"^/meta$"), meta),
    ("POST", re.compile(r"^/session$"), create_session),
    ("POST", re.compile(r"^/session/logout$"), logout),
    ("POST", re.compile(r"^/session/rotate$"), rotate_sessions),
    ("GET", re.compile(r"^/me$"), me),
    ("GET", re.compile(r"^/dashboard$"), dashboard),
    ("GET", re.compile(r"^/employees$"), list_employees),
    ("GET", re.compile(r"^/learners$"), list_learners),
    ("POST", re.compile(r"^/employees$"), create_employee),
    ("GET", re.compile(r"^/employees/(?P<employee_id>[^/]+)$"), get_employee),
    ("GET", re.compile(r"^/courses$"), list_courses),
    ("POST", re.compile(r"^/courses$"), create_course),
    ("GET", re.compile(r"^/courses/(?P<course_code>[^/]+)$"), get_course),
    ("PATCH", re.compile(r"^/courses/(?P<course_code>[^/]+)$"), update_course),
    ("POST", re.compile(r"^/courses/(?P<course_code>[^/]+)/enroll$"), enroll_course),
    ("GET", re.compile(r"^/courses/(?P<course_code>[^/]+)/progress$"), course_progress),
    ("POST", re.compile(r"^/courses/(?P<course_code>[^/]+)/modules$"), create_module),
    ("PATCH", re.compile(r"^/modules/(?P<module_id>\d+)$"), update_module),
    ("DELETE", re.compile(r"^/modules/(?P<module_id>\d+)$"), delete_module),
    ("POST", re.compile(r"^/modules/(?P<module_id>\d+)/lessons$"), create_lesson),
    ("PATCH", re.compile(r"^/lessons/(?P<lesson_id>\d+)$"), update_lesson),
    ("DELETE", re.compile(r"^/lessons/(?P<lesson_id>\d+)$"), delete_lesson),
    ("POST", re.compile(r"^/lessons/(?P<lesson_id>\d+)/progress$"), record_progress),
    ("GET", re.compile(r"^/lessons/(?P<lesson_id>\d+)/quiz$"), lesson_quiz),
    ("POST", re.compile(r"^/lessons/(?P<lesson_id>\d+)/quiz/attempt$"), submit_quiz),
    ("POST", re.compile(r"^/lessons/(?P<lesson_id>\d+)/questions$"), create_question),
    ("PATCH", re.compile(r"^/questions/(?P<question_id>\d+)$"), update_question),
    ("DELETE", re.compile(r"^/questions/(?P<question_id>\d+)$"), delete_question),
    ("GET", re.compile(r"^/cohorts$"), list_cohorts),
    ("POST", re.compile(r"^/cohorts$"), create_cohort),
    ("GET", re.compile(r"^/cohorts/(?P<cohort_id>\d+)$"), get_cohort),
    ("PATCH", re.compile(r"^/cohorts/(?P<cohort_id>\d+)$"), update_cohort),
    ("DELETE", re.compile(r"^/cohorts/(?P<cohort_id>\d+)$"), delete_cohort),
    ("POST", re.compile(r"^/cohorts/(?P<cohort_id>\d+)/members$"), add_cohort_member),
    ("DELETE", re.compile(r"^/cohorts/(?P<cohort_id>\d+)/members/(?P<employee_id>[^/]+)$"), remove_cohort_member),
    ("GET", re.compile(r"^/me/cohorts$"), my_cohorts),
    ("GET", re.compile(r"^/learning-paths$"), list_paths),
    ("POST", re.compile(r"^/learning-paths$"), create_path),
    ("GET", re.compile(r"^/learning-paths/(?P<path_id>\d+)$"), get_path),
    ("PATCH", re.compile(r"^/learning-paths/(?P<path_id>\d+)$"), update_path),
    ("DELETE", re.compile(r"^/learning-paths/(?P<path_id>\d+)$"), delete_path),
    ("POST", re.compile(r"^/learning-paths/(?P<path_id>\d+)/courses$"), add_path_course),
    ("DELETE", re.compile(r"^/learning-paths/(?P<path_id>\d+)/courses/(?P<course_code>[^/]+)$"), remove_path_course),
    ("POST", re.compile(r"^/learning-paths/(?P<path_id>\d+)/enroll$"), enroll_path),
    ("GET", re.compile(r"^/gradebook$"), gradebook),
    ("GET", re.compile(r"^/gradebook/courses/(?P<course_code>[^/]+)$"), course_grade),
    ("GET", re.compile(r"^/badges$"), list_badges),
    ("POST", re.compile(r"^/badges$"), create_badge),
    ("GET", re.compile(r"^/badges/(?P<badge_id>\d+)$"), get_badge),
    ("PATCH", re.compile(r"^/badges/(?P<badge_id>\d+)$"), update_badge),
    ("DELETE", re.compile(r"^/badges/(?P<badge_id>\d+)$"), delete_badge),
    ("POST", re.compile(r"^/badges/(?P<badge_id>\d+)/award$"), award_badge),
    ("GET", re.compile(r"^/credentials/earned$"), earned_badges),
    ("GET", re.compile(r"^/credentials/verify/(?P<code>[^/]+)$"), verify_certificate),
    ("GET", re.compile(r"^/courses/(?P<course_code>[^/]+)/discussions$"), list_discussions),
    ("POST", re.compile(r"^/courses/(?P<course_code>[^/]+)/discussions$"), create_discussion),
    ("GET", re.compile(r"^/discussions/(?P<discussion_id>\d+)$"), get_discussion),
    ("PATCH", re.compile(r"^/discussions/(?P<discussion_id>\d+)$"), update_discussion),
    ("DELETE", re.compile(r"^/discussions/(?P<discussion_id>\d+)$"), delete_discussion),
    ("POST", re.compile(r"^/discussions/(?P<discussion_id>\d+)/posts$"), create_post),
    ("PATCH", re.compile(r"^/discussions/(?P<discussion_id>\d+)/posts/(?P<post_id>\d+)$"), update_post),
    ("DELETE", re.compile(r"^/discussions/(?P<discussion_id>\d+)/posts/(?P<post_id>\d+)$"), delete_post),
    ("GET", re.compile(r"^/policies$"), list_policies),
    ("POST", re.compile(r"^/policies$"), create_policy),
    ("POST", re.compile(r"^/policies/apply$"), apply_policies),
    ("GET", re.compile(r"^/assignments$"), list_assignments),
    ("POST", re.compile(r"^/assignments$"), create_assignment),
    ("POST", re.compile(r"^/assignments/start$"), start_assignment),
    ("POST", re.compile(r"^/assignments/complete$"), complete_assignment),
    ("POST", re.compile(r"^/assignments/exempt$"), exempt_assignment),
    ("POST", re.compile(r"^/assignments/extend$"), extend_assignment),
    ("POST", re.compile(r"^/assignments/reassign$"), reassign_course),
    ("GET", re.compile(r"^/team$"), team_dashboard),
    ("GET", re.compile(r"^/team/assignments$"), team_assignments),
    ("GET", re.compile(r"^/notifications$"), list_notifications),
    ("POST", re.compile(r"^/notifications/read-all$"), mark_all_notifications_read),
    ("POST", re.compile(r"^/notifications/(?P<notification_id>\d+)/read$"), mark_notification_read),
    ("POST", re.compile(r"^/notifications/sweep$"), notification_sweep),
    ("GET", re.compile(r"^/jobs$"), list_jobs),
    ("POST", re.compile(r"^/jobs$"), queue_job),
    ("GET", re.compile(r"^/jobs/(?P<job_id>\d+)$"), get_job),
    ("POST", re.compile(r"^/jobs/(?P<job_id>\d+)/retry$"), retry_job),
    ("GET", re.compile(r"^/outbox$"), list_deliveries),
    ("GET", re.compile(r"^/reports/compliance\.csv$"), compliance_csv),
    ("GET", re.compile(r"^/audit$"), audit),
    ("GET", re.compile(r"^/import-jobs$"), import_jobs),
    ("POST", re.compile(r"^/hris/analyze$"), hris_analyze),
    ("POST", re.compile(r"^/hris/apply$"), hris_apply),
]


def _normalise(path: str) -> tuple[str, bool]:
    """Return ``(api_path, deprecated)`` for a request path."""
    if path.startswith("/api/v1/"):
        return path[len("/api/v1"):], False
    if path == "/api/v1":
        return "/", False
    if path.startswith("/api/"):
        return path[len("/api"):], True
    if path == "/api":
        return "/", True
    return path, False


def dispatch(method: str, path: str, query: dict, body: dict,
             identity: dict | None, request_id: str = "", remote: str = "",
             fingerprint: str = "") -> Result:
    api_path, deprecated = _normalise(path)
    request = Request(method, api_path, query, body, identity, request_id,
                      remote, fingerprint)
    for route_method, pattern, handler in ROUTES:
        if route_method != method:
            continue
        match = pattern.match(api_path)
        if not match:
            continue
        result = handler(request, **match.groupdict())
        if deprecated:
            result.headers.setdefault("Deprecation", "true")
            result.headers.setdefault(
                "Link", '</api/v1%s>; rel="successor-version"' % api_path
            )
        return result
    raise ApiError(404, "not_found", "No such endpoint")


def handle(method: str, path: str, query: dict, body: dict,
           identity: dict | None, request_id: str = "", remote: str = "",
           fingerprint: str = "") -> Result:
    """Dispatch and translate domain errors into API responses."""
    try:
        return dispatch(method, path, query, body, identity, request_id,
                        remote, fingerprint)
    except ApiError as exc:
        return Result(exc.payload(), exc.status)
    except sqlite3.IntegrityError as exc:
        message = str(exc)
        if "UNIQUE" in message.upper():
            message = "A record with these identifiers already exists"
        return Result({"error": "conflict", "message": message}, 409)
    except (ValueError, KeyError, TypeError, AttributeError,
            sqlite3.InterfaceError) as exc:
        return Result({"error": "validation_error", "message": str(exc)}, 400)
