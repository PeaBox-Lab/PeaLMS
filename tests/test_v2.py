"""v0.2.0 tests: migrations, org scoping, assignment lifecycle, notifications,
reporting, API permissions and HTTP transport controls.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from lms import api, config, hris, notifications, reports, services
from lms.auth import issue_token, verify_token
from lms.db import SCHEMA_VERSION, connection, init_db, schema_version
from lms.migrations import BASELINE, migrate
from lms.security import RateLimiter
from lms.seed import seed
from lms.server import create_server


def _csv(manager="E1002"):
    header = (
        "employee_id,user_name,first_name,last_name,display_name,work_email,"
        "employment_status,hire_date,termination_date,manager_employee_id,"
        "legal_entity,business_unit,department,location,job_code,job_title,"
        "job_family,grade,worker_type,country,time_zone,locale,cost_center,"
        "source_last_modified_at"
    )
    body = [
        f"E1001,asha.sharma,Asha,Sharma,Asha Sharma,asha.sharma@example.com,"
        f"active,2026-01-10,,,Nagarro,Technology,Engineering,Gurgaon,ENG-SSE,"
        f"Senior Engineer,Engineering,G6,employee,India,Asia/Kolkata,en-IN,CC100,2026-11-16T00:00:00Z",
        f"E1002,vikram.singh,Vikram,Singh,Vikram Singh,vikram.singh@example.com,"
        f"active,2024-05-01,,,Nagarro,Technology,Engineering,Gurgaon,ENG-MGR,"
        f"Engineering Manager,Engineering,G8,employee,India,Asia/Kolkata,en-IN,CC100,2026-11-16T00:00:00Z",
        f"E1003,meera.iyer,Meera,Iyer,Meera Iyer,meera.iyer@example.com,"
        f"active,2025-03-18,,E1002,Nagarro,Technology,Engineering,Bengaluru,ENG-SE,"
        f"Engineer,Engineering,G5,employee,India,Asia/Kolkata,en-IN,CC101,2026-11-16T00:00:00Z",
        f"E1004,daniel.kim,Daniel,Kim,Daniel Kim,daniel.kim@example.com,"
        f"active,2023-07-12,,{manager},Nagarro,People,Learning,Remote,TRN-001,"
        f"Learning Instructor,Learning,G7,employee,India,Asia/Kolkata,en-IN,CC200,2026-11-16T00:00:00Z",
    ]
    return "\n".join([header] + body) + "\n"


class IsolatedDb(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "LMS_DB_PATH", "LMS_DATA_DIR", "LMS_DEMO_MODE", "LMS_RATE_LIMIT_PER_MINUTE",
            "LMS_SESSION_SECRET", "LMS_HOST", "LMS_PORT", "LMS_SEED_ON_START",
        )}
        self.tmp = Path(tempfile.mkdtemp(prefix="lms-test-"))
        os.environ["LMS_DB_PATH"] = str(self.tmp / "lms.db")
        os.environ["LMS_DATA_DIR"] = str(self.tmp)
        os.environ["LMS_DEMO_MODE"] = "true"
        os.environ["LMS_RATE_LIMIT_PER_MINUTE"] = "0"
        os.environ["LMS_SESSION_SECRET"] = "test-secret-not-for-production"
        os.environ["LMS_SEED_ON_START"] = "true"
        config.reset_settings()
        init_db()
        seed()

    def tearDown(self):
        config.reset_settings()
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config.reset_settings()
        shutil.rmtree(self.tmp, ignore_errors=True)


class MigrationTests(IsolatedDb):
    def test_schema_version_and_idempotency(self):
        self.assertEqual(schema_version(), SCHEMA_VERSION)
        with connection() as conn:
            self.assertEqual(migrate(conn), [])

    def test_v1_database_is_adopted_and_upgraded(self):
        legacy = self.tmp / "legacy.db"
        conn = sqlite3.connect(legacy)
        for statement in BASELINE:
            conn.execute(statement)
        conn.commit()
        applied = migrate(conn)
        self.assertEqual(applied, [2, 3])
        columns = {row[1] for row in conn.execute("PRAGMA table_info(assignments)")}
        self.assertIn("cycle", columns)
        self.assertIn("recert_due_date", columns)
        conn.close()

    def test_new_tables_exist(self):
        with connection() as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertTrue({"notifications", "schema_migrations"} <= tables)


class OrganizationTests(IsolatedDb):
    def test_manager_subtree_is_recursive(self):
        self.assertEqual(set(services.manager_subtree("E1002")), {"E1003", "E1004"})
        self.assertEqual(services.manager_subtree("E1001"), [])

    def test_is_manager_of(self):
        self.assertTrue(services.is_manager_of("E1002", "E1003"))
        self.assertFalse(services.is_manager_of("E1001", "E1003"))
        self.assertTrue(services.is_manager_of("E1002", "E1002"))

    def test_team_dashboard_is_scoped(self):
        data = services.team_dashboard("E1002")
        self.assertEqual(data["team_size"], 2)
        self.assertGreaterEqual(data["total_assignments"], 4)
        self.assertEqual({m["employee_id"] for m in data["members"]}, {"E1003", "E1004"})

    def test_search_pagination(self):
        page = services.search_employees(limit=2, offset=0)
        self.assertEqual(page["total"], 4)
        self.assertEqual(len(page["items"]), 2)
        second = services.search_employees(limit=2, offset=2)
        self.assertEqual(len(second["items"]), 2)
        self.assertNotEqual(
            {e["employee_id"] for e in page["items"]},
            {e["employee_id"] for e in second["items"]},
        )

    def test_search_filter_by_department(self):
        page = services.search_employees(department="Engineering")
        self.assertEqual(page["total"], 3)


class AssignmentLifecycleTests(IsolatedDb):
    def _course(self, code="CRT-1", **overrides):
        payload = {
            "course_code": code, "title": "Certification " + code,
            "duration_minutes": 30, "recertification_days": 365, "passing_score": 80,
        }
        payload.update(overrides)
        return services.create_course(payload)

    def test_start_complete_and_recertify(self):
        self._course()
        assignment = services.assign_course("E1001", "CRT-1", 10, "tester")
        self.assertEqual(assignment["cycle"], 1)
        started = services.start_assignment("E1001", "CRT-1", "E1001")
        self.assertEqual(started["status"], "in_progress")
        completed = services.complete_assignment("E1001", "CRT-1", 90, "E1001")
        self.assertEqual(completed["status"], "completed")
        self.assertIsNotNone(completed["recert_due_date"])
        renewed = services.reassign_course("E1001", "CRT-1", 30, "admin")
        self.assertEqual(renewed["cycle"], 2)
        self.assertEqual(renewed["status"], "assigned")

    def test_score_below_passing_marks_failed(self):
        self._course()
        services.assign_course("E1001", "CRT-1", 10, "tester")
        result = services.complete_assignment("E1001", "CRT-1", 50, "E1001")
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["recert_due_date"])
        events = [e["event_type"] for e in services.audit_events(50)]
        self.assertIn("LearningFailed", events)

    def test_cannot_complete_twice(self):
        self._course()
        services.assign_course("E1001", "CRT-1", 10, "tester")
        services.complete_assignment("E1001", "CRT-1", 90, "E1001")
        with self.assertRaises(ValueError):
            services.complete_assignment("E1001", "CRT-1", 95, "E1001")

    def test_exemption_requires_reason_and_blocks_completion(self):
        self._course()
        services.assign_course("E1001", "CRT-1", 10, "tester")
        with self.assertRaises(ValueError):
            services.exempt_assignment("E1001", "CRT-1", "  ", "admin")
        result = services.exempt_assignment("E1001", "CRT-1", "Prior certification", "admin")
        self.assertEqual(result["status"], "exempted")
        with self.assertRaises(ValueError):
            services.complete_assignment("E1001", "CRT-1", 90, "E1001")

    def test_due_date_extension_is_audited(self):
        self._course()
        services.assign_course("E1001", "CRT-1", 10, "tester")
        result = services.extend_due_date("E1001", "CRT-1", "2030-01-31", "admin")
        self.assertEqual(result["due_date"], "2030-01-31")
        events = [e["event_type"] for e in services.audit_events(50)]
        self.assertIn("AssignmentDueDateExtended", events)

    def test_duplicate_active_assignment_rejected(self):
        self._course()
        services.assign_course("E1001", "CRT-1", 10, "tester")
        with self.assertRaises(ValueError):
            services.assign_course("E1001", "CRT-1", 10, "tester")


class NotificationTests(IsolatedDb):
    def test_seed_queues_notifications_and_mark_read(self):
        page = notifications.list_for("E1003")
        self.assertGreater(page["total"], 0)
        before = notifications.unread_count("E1003")
        first = page["items"][0]
        self.assertTrue(notifications.mark_read(first["id"], "E1003"))
        self.assertEqual(notifications.unread_count("E1003"), before - 1)

    def test_mark_read_is_scoped_to_recipient(self):
        page = notifications.list_for("E1003")
        self.assertFalse(notifications.mark_read(page["items"][0]["id"], "E1001"))

    def test_overdue_sweep_is_deduplicated(self):
        with connection() as conn:
            conn.execute(
                "INSERT INTO assignments(employee_id,course_code,cycle,status,due_date) "
                "VALUES('E1001','SEC-101',2,'assigned',date('now','-3 day'))"
            )
        self.assertEqual(services.notify_overdue(), 1)
        self.assertEqual(services.notify_overdue(), 0)

    def test_completion_notifies_learner_and_manager(self):
        with connection() as conn:
            before_manager = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE recipient_employee_id='E1002'"
            ).fetchone()[0]
        services.complete_assignment("E1003", "SEC-101", 95, "E1003")
        with connection() as conn:
            after_manager = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE recipient_employee_id='E1002'"
            ).fetchone()[0]
            learner_templates = [r[0] for r in conn.execute(
                "SELECT template FROM notifications WHERE recipient_employee_id='E1003'"
            )]
        self.assertGreater(after_manager, before_manager)
        self.assertIn("completion_recorded", learner_templates)

    def test_delivery_marks_sent(self):
        delivered = notifications.deliver()
        self.assertGreater(delivered, 0)
        with connection() as conn:
            queued = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE status='queued'"
            ).fetchone()[0]
        self.assertEqual(queued, 0)


class ReportingTests(IsolatedDb):
    def test_compliance_csv_has_header_and_rows(self):
        text = reports.compliance_csv()
        lines = text.strip().splitlines()
        self.assertGreater(len(lines), 1)
        self.assertTrue(lines[0].startswith("employee_id,display_name,department"))

    def test_compliance_csv_scoped_to_manager_subtree(self):
        ids = services.manager_subtree("E1002")
        text = reports.compliance_csv(employee_ids=ids)
        lines = text.strip().splitlines()[1:]
        self.assertTrue(lines)
        for line in lines:
            self.assertIn(line.split(",")[0], ids)


class HrisWarningTests(IsolatedDb):
    def test_unknown_manager_produces_warning(self):
        result = hris.analyze(_csv(manager="E9999"), mode="delta")
        self.assertTrue(any("unknown manager" in w for w in result["warnings"]))

    def test_termination_before_hire_is_rejected(self):
        csv_text = _csv().replace(
            "active,2026-01-10,,,", "active,2026-01-10,2020-01-01,,", 1
        )
        result = hris.analyze(csv_text, mode="delta")
        self.assertFalse(result["can_apply"])
        self.assertTrue(any("termination_date precedes" in " ".join(e["errors"])
                            for e in result["errors"]))


class ApiPermissionTests(IsolatedDb):
    def _call(self, method, path, body=None, identity=None, query=None):
        return api.handle(method, path, query or {}, body or {}, identity)

    def test_unauthenticated_read_is_rejected(self):
        result = self._call("GET", "/api/v1/dashboard")
        self.assertEqual(result.status, 401)

    def test_learner_cannot_list_employees(self):
        result = self._call("GET", "/api/v1/employees",
                            identity={"role": "learner", "actor": "E1003"})
        self.assertEqual(result.status, 403)

    def test_learner_can_read_own_dashboard(self):
        result = self._call("GET", "/api/v1/dashboard",
                            identity={"role": "learner", "actor": "E1003"})
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["scope"], "personal")

    def test_manager_dashboard_is_team_scoped(self):
        result = self._call("GET", "/api/v1/dashboard",
                            identity={"role": "manager", "actor": "E1002"})
        self.assertEqual(result.payload["scope"], "team")

    def test_manager_cannot_read_other_employee_assignments(self):
        result = self._call("GET", "/api/v1/assignments",
                            query={"employee_id": "E1001"},
                            identity={"role": "manager", "actor": "E1002"})
        self.assertEqual(result.status, 403)

    def test_manager_can_read_report_assignments(self):
        result = self._call("GET", "/api/v1/assignments",
                            query={"employee_id": "E1003"},
                            identity={"role": "manager", "actor": "E1002"})
        self.assertEqual(result.status, 200)

    def test_learner_cannot_complete_other_assignment(self):
        result = self._call(
            "POST", "/api/v1/assignments/complete",
            body={"employee_id": "E1001", "course_code": "SEC-101", "score": 100},
            identity={"role": "learner", "actor": "E1003"},
        )
        self.assertEqual(result.status, 403)

    def test_unknown_endpoint_is_404(self):
        result = self._call("GET", "/api/v1/nope",
                            identity={"role": "platform_admin", "actor": "x"})
        self.assertEqual(result.status, 404)

    def test_deprecated_alias_is_marked(self):
        result = self._call("GET", "/api/dashboard",
                            identity={"role": "platform_admin", "actor": "x"})
        self.assertEqual(result.status, 200)
        self.assertEqual(result.headers.get("Deprecation"), "true")

    def test_session_requires_demo_mode(self):
        result = self._call("POST", "/api/v1/session",
                            body={"role": "learner", "actor": "E1003"})
        self.assertEqual(result.status, 200)
        self.assertIn("token", result.payload)

    def test_auditor_can_read_all_assignments(self):
        result = self._call("GET", "/api/v1/assignments",
                            identity={"role": "auditor", "actor": "demo-admin"})
        self.assertEqual(result.status, 200)
        self.assertGreater(result.payload["total"], 0)

    def test_export_requires_export_permission(self):
        denied = self._call("GET", "/api/v1/reports/compliance.csv",
                            identity={"role": "learner", "actor": "E1003"})
        allowed = self._call("GET", "/api/v1/reports/compliance.csv",
                             identity={"role": "manager", "actor": "E1002"})
        self.assertEqual(denied.status, 403)
        self.assertEqual(allowed.status, 200)
        self.assertIn("text/csv", allowed.content_type)


class TokenTests(IsolatedDb):
    def test_round_trip_and_tamper(self):
        token = issue_token("learner", "E1003")
        identity = verify_token(token)
        self.assertEqual(identity["role"], "learner")
        self.assertEqual(identity["actor"], "E1003")
        self.assertIsNone(verify_token(token + "x"))

    def test_unknown_role_rejected(self):
        token = issue_token("not_a_role", "E1003")
        self.assertIsNone(verify_token(token))


class RateLimiterTests(unittest.TestCase):
    def test_window_limit(self):
        limiter = RateLimiter(2)
        self.assertEqual(limiter.allow("a"), (True, 0))
        self.assertEqual(limiter.allow("a"), (True, 0))
        allowed, retry = limiter.allow("a")
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)
        self.assertTrue(limiter.allow("b")[0])

    def test_zero_disables(self):
        limiter = RateLimiter(0)
        for _ in range(10):
            self.assertTrue(limiter.allow("a")[0])


class HttpTransportTests(IsolatedDb):
    def setUp(self):
        super().setUp()
        self.server = create_server("127.0.0.1", 0, rate_limit_per_minute=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def _get(self, path, token=None):
        request = urllib.request.Request(self.base + path)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_health_and_security_headers(self):
        status, headers, body = self._get("/api/v1/health")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertTrue(headers.get("X-Request-ID"))

    def test_index_is_served_without_auth(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn(b"Corporate LMS", body)

    def test_protected_endpoint_requires_token(self):
        status, _, _ = self._get("/api/v1/dashboard")
        self.assertEqual(status, 401)

    def test_session_then_dashboard(self):
        request = urllib.request.Request(
            self.base + "/api/v1/session",
            data=json.dumps({"role": "learner", "actor": "E1003"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            token = json.loads(response.read())["token"]
        status, _, body = self._get("/api/v1/dashboard", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["scope"], "personal")

    def test_static_traversal_is_blocked(self):
        status, _, _ = self._get("/../lms/server.py")
        self.assertIn(status, (400, 404))

    def test_malformed_json_returns_400(self):
        request = urllib.request.Request(
            self.base + "/api/v1/session", data=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            self.fail("expected an error")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


class HttpRateLimitTests(IsolatedDb):
    def test_second_request_is_throttled_with_retry_after(self):
        server = create_server("127.0.0.1", 0, rate_limit_per_minute=1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/v1/health", timeout=5) as response:
                self.assertEqual(response.status, 200)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(base + "/api/v1/health", timeout=5)
            self.assertEqual(raised.exception.code, 429)
            self.assertTrue(raised.exception.headers.get("Retry-After"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
