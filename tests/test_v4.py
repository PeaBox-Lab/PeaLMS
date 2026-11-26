"""v0.4.0 tests: cohorts, learning paths, gradebook, credentials and discussions."""

import csv
import io
import os
import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lms import api, auth, config, content, engagement, hris, media, services
from lms.db import connection, init_db, schema_version
from lms.seed import seed


class IsolatedEngagementDb(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "LMS_DB_PATH", "LMS_DATA_DIR", "LMS_MEDIA_DIR", "LMS_DEMO_MODE",
            "LMS_RATE_LIMIT_PER_MINUTE", "LMS_SESSION_SECRET", "LMS_SEED_ON_START",
        )}
        self.tmp = Path(tempfile.mkdtemp(prefix="lms-v4-"))
        os.environ.update({
            "LMS_DB_PATH": str(self.tmp / "lms.db"),
            "LMS_DATA_DIR": str(self.tmp),
            "LMS_MEDIA_DIR": str(self.tmp / "media"),
            "LMS_DEMO_MODE": "true",
            "LMS_RATE_LIMIT_PER_MINUTE": "0",
            "LMS_SESSION_SECRET": "test-secret-not-for-production",
            "LMS_SEED_ON_START": "true",
        })
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

    def call(self, method, path, body=None, role="platform_admin", actor="demo-admin"):
        parsed = urlparse(path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        return api.handle(
            method, "/api/v1" + parsed.path, query, body or {},
            {"role": role, "actor": actor},
        )


class SchemaAndSeedTests(IsolatedEngagementDb):
    def test_schema_version_is_five(self):
        self.assertEqual(schema_version(), 5)

    def test_engagement_seed_data_exists(self):
        self.assertTrue(engagement.list_cohorts())
        self.assertTrue(engagement.list_paths())
        badges = {badge["code"] for badge in engagement.list_badges(include_inactive=True)}
        self.assertTrue({"SEC-FOUNDATION", "ETH-FOUNDATION", "CORE-COMPLETE"} <= badges)
        discussions = engagement.list_discussions("SEC-101")
        self.assertTrue(discussions)
        self.assertTrue(discussions[0]["post_count"] >= 1)

    def test_meta_exposes_new_options(self):
        result = self.call("GET", "/meta")
        self.assertEqual(result.status, 200)
        self.assertIn("cohort", result.payload["policy_attributes"])
        self.assertIn("published", result.payload["learning_path_statuses"])
        self.assertIn("shield", result.payload["badge_icons"])


class CohortTests(IsolatedEngagementDb):
    def test_cohort_crud(self):
        created = engagement.create_cohort({
            "code": "PILOT", "name": "Pilot group", "description": "Pilot learners",
            "employees": ["E1003"],
        }, "E1002")
        self.assertEqual(created["code"], "PILOT")
        self.assertEqual(len(created["members"]), 1)
        updated = engagement.update_cohort(created["id"], {"name": "Pilot cohort"}, "E1002")
        self.assertEqual(updated["name"], "Pilot cohort")
        engagement.add_cohort_member(created["id"], "E1004", "E1002")
        self.assertEqual(len(engagement.get_cohort(created["id"])["members"]), 2)
        engagement.remove_cohort_member(created["id"], "E1004", "E1002")
        self.assertEqual(len(engagement.get_cohort(created["id"])["members"]), 1)
        engagement.delete_cohort(created["id"], "E1002")
        self.assertIsNone(engagement.get_cohort(created["id"]))

    def test_cohort_policy_attribute_matches_membership(self):
        cohort = engagement.create_cohort({
            "code": "PILOT", "name": "Pilot cohort", "employees": ["E1003"],
        })
        services.create_policy({
            "name": "Pilot management course", "course_code": "MGR-101",
            "attribute": "cohort", "operator": "equals", "value": "PILOT", "due_days": 7,
        }, "demo-admin")
        services.apply_policies()
        assignments = services.list_assignments("E1003")
        self.assertTrue(any(item["course_code"] == "MGR-101" for item in assignments))
        assignments_e1004 = services.list_assignments("E1004")
        self.assertFalse(any(item["course_code"] == "MGR-101" for item in assignments_e1004))
        pilot_codes = [item["code"] for item in engagement.cohorts_for_employee("E1004")]
        self.assertNotIn("PILOT", pilot_codes)
        self.assertEqual(cohort["code"], "PILOT")

    def test_unknown_policy_attribute_is_rejected(self):
        with self.assertRaises(ValueError):
            services.create_policy({
                "name": "Bad rule", "course_code": "MGR-101",
                "attribute": "favourite_colour", "value": "blue",
            })

    def test_learner_cannot_list_cohorts_but_can_see_own(self):
        self.assertEqual(self.call("GET", "/cohorts", role="learner", actor="E1003").status, 403)
        result = self.call("GET", "/me/cohorts", role="learner", actor="E1003")
        self.assertEqual(result.status, 200)
        self.assertTrue(result.payload)
        self.assertEqual(self.call("GET", "/learners", role="learner", actor="E1003").status, 200)


class LearningPathTests(IsolatedEngagementDb):
    def test_path_creation_and_progress(self):
        path = engagement.create_path({
            "code": "TEST-PATH", "title": "Test path",
            "description": "Two steps", "courses": ["SEC-101", "MGR-101"],
        }, "demo-admin")
        self.assertEqual(len(path["courses"]), 2)
        progress = engagement.get_path(path["id"], actor="E1003")["progress"]
        self.assertEqual(progress["required_courses"], 2)
        self.assertEqual(progress["percent"], 0)
        self.assertEqual(progress["next_course"], "SEC-101")

    def test_completion_awards_badge_and_advances_path(self):
        engagement.create_path({
            "code": "TEST-PATH", "title": "Test path", "courses": ["SEC-101", "MGR-101"],
        }, "demo-admin")
        result = self.call("POST", "/assignments/complete", {
            "employee_id": "E1003", "course_code": "SEC-101", "score": 95,
        })
        self.assertEqual(result.status, 200)
        progression = result.payload["progression"]
        self.assertTrue(progression["awarded"])
        self.assertTrue(any(item["course_code"] == "MGR-101" for item in progression["advanced"]))
        earned = engagement.earned_badges("E1003")
        self.assertTrue(any(item["title"] == "Security foundation" for item in earned))
        verification = engagement.verify_certificate(progression["awarded"][0])
        self.assertEqual(verification["badge_title"], "Security foundation")
        self.assertEqual(verification["employee_id"], "E1003")

    def test_enroll_path_assigns_next_course(self):
        path = engagement.create_path({
            "code": "ENROLL-PATH", "title": "Enroll path", "courses": ["MGR-101"],
        })
        result = self.call("POST", f"/learning-paths/{path['id']}/enroll",
                           role="learner", actor="E1003")
        self.assertEqual(result.status, 201)
        self.assertTrue(any(item["course_code"] == "MGR-101"
                            for item in services.list_assignments("E1003")))

    def test_instructor_cannot_enroll_other_employee_in_path(self):
        path = engagement.create_path({"code": "ENROLL-PATH", "title": "Enroll path",
                                       "courses": ["MGR-101"]})
        result = self.call("POST", f"/learning-paths/{path['id']}/enroll",
                           {"employee_id": "E1003"}, role="instructor", actor="E1004")
        self.assertEqual(result.status, 403)


class GradebookAndCredentialTests(IsolatedEngagementDb):
    def test_gradebook_uses_assignment_score_and_quiz_evidence(self):
        services.complete_assignment("E1003", "SEC-101", 95, "E1004")
        grade = engagement.get_course_grade("E1003", "SEC-101")
        self.assertEqual(grade["score"], 95)
        self.assertEqual(grade["letter"], "A")
        self.assertEqual(grade["source"], "assignment")
        self.assertTrue(grade["components"])
        result = self.call("GET", "/gradebook?employee_id=E1003")
        self.assertEqual(result.status, 200)
        self.assertTrue(any(item["course_code"] == "SEC-101" for item in result.payload["grades"]))

    def test_gradebook_requires_permission_for_other_learner(self):
        self.assertEqual(
            self.call("GET", "/gradebook?employee_id=E1001", role="learner", actor="E1003").status,
            403,
        )
        self.assertEqual(
            self.call("GET", "/gradebook?employee_id=E1003", role="learner", actor="E1003").status,
            200,
        )

    def test_badge_award_and_verification(self):
        badge = engagement.create_badge({
            "code": "MANUAL-1", "title": "Manual badge", "icon": "trophy",
        }, "demo-admin")
        result = self.call("POST", f"/badges/{badge['id']}/award",
                           {"employee_id": "E1003"})
        self.assertEqual(result.status, 201)
        code = result.payload["verification_code"]
        verified = self.call("GET", "/credentials/verify/" + code)
        self.assertEqual(verified.status, 200)
        self.assertEqual(verified.payload["badge_title"], "Manual badge")
        self.assertEqual(self.call("GET", "/credentials/verify/not-a-code").status, 404)

    def test_learner_cannot_create_badge(self):
        self.assertEqual(self.call("POST", "/badges", {
            "code": "BAD", "title": "Bad",
        }, role="learner", actor="E1003").status, 403)


class DiscussionTests(IsolatedEngagementDb):
    def test_discussion_posting_and_moderation(self):
        created = self.call("POST", "/courses/SEC-101/discussions", {
            "title": "Study group", "body": "Who is joining?",
        }, role="instructor", actor="E1004")
        self.assertEqual(created.status, 201)
        discussion_id = created.payload["id"]
        posted = self.call("POST", f"/discussions/{discussion_id}/posts", {
            "body": "I am in.",
        }, role="learner", actor="E1003")
        self.assertEqual(posted.status, 201)
        detail = self.call("GET", f"/discussions/{discussion_id}",
                           role="learner", actor="E1003")
        self.assertEqual(len(detail.payload["posts"]), 1)
        self.assertEqual(self.call("DELETE", f"/discussions/{discussion_id}",
                                   role="learner", actor="E1003").status, 403)
        self.assertEqual(self.call("DELETE", f"/discussions/{discussion_id}",
                                   role="instructor", actor="E1004").status, 200)

    def test_unenrolled_learner_cannot_post(self):
        # MGR-101 has no seed policy, so E1001 is genuinely unenrolled.
        created = self.call("POST", "/courses/MGR-101/discussions", {
            "title": "Open thread",
        }, role="instructor", actor="E1004")
        discussion_id = created.payload["id"]
        self.assertEqual(self.call("POST", f"/discussions/{discussion_id}/posts", {
            "body": "Hello",
        }, role="learner", actor="E1001").status, 403)


class CompletionGuardTests(IsolatedEngagementDb):
    def test_learner_cannot_self_report_score(self):
        result = self.call("POST", "/assignments/complete", {
            "employee_id": "E1003", "course_code": "SEC-101", "score": 100,
        }, role="learner", actor="E1003")
        self.assertEqual(result.status, 403)

    def test_learner_cannot_skip_required_lessons(self):
        result = self.call("POST", "/assignments/complete", {
            "employee_id": "E1003", "course_code": "SEC-101",
        }, role="learner", actor="E1003")
        self.assertEqual(result.status, 403)

    def test_failed_quiz_hides_explanations(self):
        quiz = next(lesson for lesson in content.get_course_detail(
            "SEC-101", actor="E1003")["modules"][1]["lessons"]
            if lesson["type"] == "quiz")
        questions = content.list_questions(quiz["id"], reveal=True)["questions"]
        correct = [item["correct_index"] for item in questions]
        wrong = [(item["correct_index"] + 1) % len(item["options"])
                 for item in questions]
        failed = content.submit_quiz("E1003", quiz["id"], wrong, "E1003")
        self.assertFalse(failed["passed"])
        self.assertTrue(all("explanation" not in item for item in failed["results"]))
        passed = content.submit_quiz("E1003", quiz["id"], correct, "E1003")
        self.assertTrue(passed["passed"])
        self.assertEqual(passed["correct"], passed["total"])
        self.assertTrue(all("explanation" in item for item in passed["results"]))

    def test_manager_can_start_team_assignment(self):
        result = self.call("POST", "/assignments/start", {
            "employee_id": "E1003", "course_code": "SEC-101",
        }, role="manager", actor="E1002")
        self.assertEqual(result.status, 200)


class SecurityHardeningTests(IsolatedEngagementDb):
    def test_token_is_fingerprint_bound_and_revocable(self):
        token = auth.issue_token("learner", "E1003", fingerprint="browser-A")
        identity = auth.verify_token(token, fingerprint="browser-A")
        self.assertIsNotNone(identity)
        self.assertIsNone(auth.verify_token(token, fingerprint="browser-B"))
        result = api.handle("POST", "/api/v1/session/logout", {}, {}, identity)
        self.assertEqual(result.status, 200)
        self.assertIsNone(auth.verify_token(token, fingerprint="browser-A"))

    def test_epoch_rotation_revokes_all_sessions(self):
        token = auth.issue_token("platform_admin", "demo-admin", fingerprint="browser-A")
        identity = auth.verify_token(token, fingerprint="browser-A")
        before = auth.session_epoch()
        result = api.handle("POST", "/api/v1/session/rotate", {}, {}, identity)
        self.assertEqual(result.status, 200)
        self.assertGreater(auth.session_epoch(), before)
        self.assertIsNone(auth.verify_token(token, fingerprint="browser-A"))

    def test_demo_session_is_loopback_only(self):
        body = {"role": "platform_admin", "actor": "demo-admin"}
        remote = api.handle("POST", "/api/v1/session", {}, body, None,
                            remote="203.0.113.9")
        self.assertEqual(remote.status, 403)
        local = api.handle("POST", "/api/v1/session", {}, body, None,
                           remote="127.0.0.1")
        self.assertEqual(local.status, 200)
        self.assertIn("token", local.payload)

    def test_media_stream_validation_and_unsupported_mkv(self):
        item = media.store_stream(
            "clip.mov", "video/quicktime",
            BytesIO(b"\x00\x00\x00\x18ftypqt  "), "E1004",
        )
        self.assertEqual(item["content_type"], "video/quicktime")
        self.assertEqual(item["kind"], "video")
        self.assertTrue(item["url"].startswith("/media/"))
        with self.assertRaises(ValueError):
            media.store_stream(
                "clip.mkv", "video/x-matroska",
                BytesIO(b"\x1aE\xdf\xa3invalid"), "E1004",
            )


class LearnerDirectoryShapeTests(IsolatedEngagementDb):
    def test_admin_gets_learner_directory_items(self):
        result = self.call("GET", "/learners")
        self.assertEqual(result.status, 200)
        self.assertIsInstance(result.payload, list)
        self.assertTrue(result.payload)
        self.assertTrue(all("employee_id" in item for item in result.payload))
        self.assertTrue(all("display_name" in item for item in result.payload))

    def test_manager_gets_learner_directory_items(self):
        result = self.call("GET", "/learners", role="manager", actor="E1002")
        self.assertEqual(result.status, 200)
        self.assertIsInstance(result.payload, list)
        self.assertTrue(any(item["employee_id"] == "E1003" for item in result.payload))

    def test_learner_gets_own_directory_item(self):
        result = self.call("GET", "/learners", role="learner", actor="E1003")
        self.assertEqual(result.status, 200)
        self.assertEqual([item["employee_id"] for item in result.payload], ["E1003"])


class AuditRegressionTests(IsolatedEngagementDb):
    def test_manager_without_filter_sees_only_own_subtree(self):
        services.assign_course("E1001", "MGR-101", actor="demo-admin")
        services.assign_course("E1003", "MGR-101", actor="demo-admin")
        result = self.call("GET", "/assignments", role="manager", actor="E1002")
        self.assertEqual(result.status, 200)
        employees = {item["employee_id"] for item in result.payload["items"]}
        self.assertTrue(employees)
        self.assertNotIn("E1001", employees)
        self.assertTrue(employees <= {"E1002", "E1003", "E1004"})

    def test_learner_cannot_use_status_filter_to_see_drafts(self):
        content.create_course({
            "course_code": "DRAFT-X", "title": "Draft course", "status": "draft",
        }, "demo-admin")
        result = self.call("GET", "/courses?status=draft", role="learner", actor="E1003")
        self.assertEqual(result.status, 200)
        self.assertNotIn("DRAFT-X", [item["course_code"] for item in result.payload])

    def test_learner_cannot_read_unenrolled_discussion(self):
        created = self.call("POST", "/courses/MGR-101/discussions", {
            "title": "Private thread", "body": "For enrolled learners",
        }, role="instructor", actor="E1004")
        self.assertEqual(created.status, 201)
        discussion_id = created.payload["id"]
        self.assertEqual(
            self.call("GET", f"/discussions/{discussion_id}",
                      role="learner", actor="E1001").status,
            403,
        )
        self.assertEqual(
            self.call("GET", "/courses/MGR-101/discussions",
                      role="learner", actor="E1001").status,
            403,
        )

    def test_hris_rejects_malformed_body_types(self):
        result = self.call("POST", "/hris/analyze", {
            "csv": [1, 2], "filename": {"bad": "value"},
        }, role="learning_admin")
        self.assertEqual(result.status, 400)
        self.assertEqual(result.payload["error"], "validation_error")

    def test_hris_full_extract_revokes_stale_manager_role(self):
        with connection() as conn:
            employees = [dict(item) for item in conn.execute("SELECT * FROM employees")]
        output = io.StringIO()
        fields = list(services.EMPLOYEE_FIELDS)
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for employee in employees:
            row_data = {field: employee.get(field, "") or "" for field in fields}
            row_data["manager_employee_id"] = ""
            writer.writerow(row_data)
        result = hris.apply(output.getvalue(), mode="full", actor="demo-admin")
        self.assertEqual(result["status"], "applied")
        with connection() as conn:
            roles = {item["role"] for item in conn.execute(
                "SELECT role FROM employee_roles WHERE employee_id='E1002'"
            )}
        self.assertIn("learner", roles)
        self.assertNotIn("manager", roles)

    def test_negative_due_days_is_rejected(self):
        result = self.call("POST", "/assignments", {
            "employee_id": "E1003", "course_code": "MGR-101", "due_days": -5,
        })
        self.assertEqual(result.status, 400)

    def test_updating_path_courses_replaces_the_sequence(self):
        path = engagement.create_path({
            "code": "EDIT-PATH", "title": "Editable path", "courses": ["SEC-101"],
        }, "demo-admin")
        result = self.call("PATCH", f"/learning-paths/{path['id']}", {
            "courses": ["ETH-101", "MGR-101"],
        })
        self.assertEqual(result.status, 200)
        updated = engagement.get_path(path["id"], actor="demo-admin",
                                      include_unpublished=True)
        self.assertEqual(
            [item["course_code"] for item in updated["courses"]],
            ["ETH-101", "MGR-101"],
        )

    def test_public_verification_does_not_expose_work_email(self):
        result = self.call("POST", "/badges", {
            "code": "PUBLIC-1", "title": "Public badge",
        }, role="learning_admin")
        self.assertEqual(result.status, 201)
        awarded = self.call("POST", f"/badges/{result.payload['id']}/award", {
            "employee_id": "E1003",
        }, role="learning_admin")
        verified = self.call("GET",
                             "/credentials/verify/" + awarded.payload["verification_code"])
        self.assertEqual(verified.status, 200)
        self.assertNotIn("work_email", verified.payload)

    def test_learner_cannot_self_complete_draft_course(self):
        content.create_course({
            "course_code": "DRAFT-Y", "title": "Draft with lessons", "status": "draft",
        }, "demo-admin")
        module = content.create_module("DRAFT-Y", {"title": "Module one"}, "demo-admin")
        content.create_lesson(module["id"], {
            "title": "Required reading", "lesson_type": "text", "content": "Read me",
        }, "demo-admin")
        services.assign_course("E1003", "DRAFT-Y", actor="demo-admin")
        result = self.call("POST", "/assignments/complete", {
            "employee_id": "E1003", "course_code": "DRAFT-Y",
        }, role="learner", actor="E1003")
        self.assertEqual(result.status, 403)


if __name__ == "__main__":
    unittest.main()
