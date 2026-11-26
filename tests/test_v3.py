"""v0.3.0 tests: learning content, curriculum, media, progress and quizzes."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lms import api, config, content, media, services
from lms.db import connection, init_db, schema_version
from lms.security import security_headers
from lms.seed import seed

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64


class IsolatedContentDb(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "LMS_DB_PATH", "LMS_DATA_DIR", "LMS_MEDIA_DIR", "LMS_DEMO_MODE",
            "LMS_RATE_LIMIT_PER_MINUTE", "LMS_SESSION_SECRET", "LMS_SEED_ON_START",
        )}
        self.tmp = Path(tempfile.mkdtemp(prefix="lms-v3-"))
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


class ContentSchemaTests(IsolatedContentDb):
    def test_schema_version_is_five(self):
        self.assertEqual(schema_version(), 5)

    def test_seeded_catalog_has_curriculum(self):
        courses = content.list_courses("E1003")
        codes = {course["course_code"] for course in courses}
        self.assertEqual(codes, {"SEC-101", "ETH-101", "ENG-201", "MGR-101"})
        detail = content.get_course_detail("SEC-101", actor="E1003")
        self.assertGreaterEqual(detail["module_count"], 2)
        types = {lesson["type"] for module in detail["modules"] for lesson in module["lessons"]}
        self.assertTrue({"text", "youtube", "quiz"} <= types)
        self.assertTrue(detail["objectives_list"])
        self.assertTrue(detail["tags_list"])

    def test_unpublished_courses_hidden_from_learners(self):
        content.create_course({"course_code": "DRAFT-1", "title": "Draft", "status": "draft"},
                              "E1004")
        self.assertIsNone(content.get_course_detail("DRAFT-1", actor="E1003"))
        self.assertIsNotNone(
            content.get_course_detail("DRAFT-1", actor="E1004", include_unpublished=True)
        )


class YoutubeTests(unittest.TestCase):
    def test_normalises_common_urls(self):
        cases = {
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ": "aqz-KE-bpKQ",
            "https://youtu.be/aqz-KE-bpKQ?t=12": "aqz-KE-bpKQ",
            "https://www.youtube.com/embed/aqz-KE-bpKQ": "aqz-KE-bpKQ",
            "https://www.youtube.com/shorts/aqz-KE-bpKQ": "aqz-KE-bpKQ",
            "https://m.youtube.com/watch?feature=share&v=aqz-KE-bpKQ": "aqz-KE-bpKQ",
            "aqz-KE-bpKQ": "aqz-KE-bpKQ",
        }
        for value, expected in cases.items():
            self.assertEqual(content.normalize_youtube(value), expected)

    def test_rejects_invalid_input(self):
        with self.assertRaises(ValueError):
            content.normalize_youtube("https://example.com/not-a-video")
        with self.assertRaises(ValueError):
            content.normalize_youtube("too-short")


class ProgressAndQuizTests(IsolatedContentDb):
    def _security_lessons(self):
        detail = content.get_course_detail("SEC-101", actor="E1003")
        return [lesson for module in detail["modules"] for lesson in module["lessons"]]

    def test_lesson_progress_auto_completes_assignment(self):
        lessons = self._security_lessons()
        for lesson in lessons:
            if lesson["type"] != "quiz":
                result = content.record_lesson_progress(
                    "E1003", lesson["id"], completed=True, actor="E1003"
                )
                self.assertNotEqual(
                    (result["assignment"] or {}).get("status"), "completed"
                )
        quiz = next(lesson for lesson in lessons if lesson["type"] == "quiz")
        outcome = content.submit_quiz("E1003", quiz["id"], [1, 1, 1], "E1003")
        self.assertTrue(outcome["passed"])
        self.assertEqual(outcome["score"], 100.0)
        self.assertEqual(outcome["assignment"]["status"], "completed")
        self.assertEqual(outcome["assignment"]["score"], 100.0)
        self.assertTrue(outcome["progress"]["progress"]["is_complete"])

    def test_failed_quiz_does_not_complete_assignment(self):
        lessons = self._security_lessons()
        quiz = next(lesson for lesson in lessons if lesson["type"] == "quiz")
        outcome = content.submit_quiz("E1003", quiz["id"], [0, 0, 0], "E1003")
        self.assertFalse(outcome["passed"])
        self.assertLess(outcome["score"], outcome["pass_score"])
        self.assertNotEqual((outcome["assignment"] or {}).get("status"), "completed")

    def test_quiz_answers_hidden_from_learners(self):
        lessons = self._security_lessons()
        quiz = next(lesson for lesson in lessons if lesson["type"] == "quiz")
        learner_view = content.list_questions(quiz["id"])
        self.assertTrue(learner_view["questions"])
        for question in learner_view["questions"]:
            self.assertNotIn("correct_index", question)
            self.assertNotIn("explanation", question)
        admin_view = content.list_questions(quiz["id"], reveal=True)
        self.assertIn("correct_index", admin_view["questions"][0])

    def test_quiz_lesson_cannot_be_completed_manually(self):
        lessons = self._security_lessons()
        quiz = next(lesson for lesson in lessons if lesson["type"] == "quiz")
        with self.assertRaises(ValueError):
            content.record_lesson_progress("E1003", quiz["id"], completed=True)

    def test_reassignment_clears_lesson_progress(self):
        lessons = self._security_lessons()
        for lesson in lessons:
            if lesson["type"] != "quiz":
                content.record_lesson_progress("E1003", lesson["id"], completed=True)
        quiz = next(lesson for lesson in lessons if lesson["type"] == "quiz")
        content.submit_quiz("E1003", quiz["id"], [1, 1, 1], "E1003")
        assignment = services.assign_course("E1003", "SEC-101", 30, "E1004")
        self.assertEqual(assignment["cycle"], 2)
        self.assertEqual(assignment["status"], "assigned")
        progress = content.get_progress("E1003", "SEC-101")
        self.assertEqual(progress["progress"]["completed_lessons"], 0)


class AuthoringTests(IsolatedContentDb):
    def test_full_authoring_lifecycle(self):
        course = content.create_course({
            "course_code": "AUTH-1", "title": "Authored course", "summary": "Summary",
            "level": "Advanced", "author": "Instructor", "tags": "alpha,beta",
            "objectives": "one\ntwo", "status": "draft", "mandatory": False,
        }, "E1004")
        self.assertEqual(course["status"], "draft")
        self.assertEqual(course["tags_list"], ["alpha", "beta"])
        self.assertEqual(course["objectives_list"], ["one", "two"])

        module = content.create_module("AUTH-1", {"title": "Module one"}, "E1004")
        text = content.create_lesson(module["id"], {
            "title": "Read this", "lesson_type": "text", "content": "Body text",
            "duration_minutes": 5,
        }, "E1004")
        self.assertEqual(text["type"], "text")

        youtube = content.create_lesson(module["id"], {
            "title": "Watch this", "lesson_type": "youtube",
            "content": "https://youtu.be/aqz-KE-bpKQ",
        }, "E1004")
        self.assertEqual(youtube["youtube_id"], "aqz-KE-bpKQ")

        video = media.store("clip.mp4", "video/mp4", MP4, "E1004")
        uploaded = content.create_lesson(module["id"], {
            "title": "Uploaded", "lesson_type": "video", "media_id": video["id"],
        }, "E1004")
        self.assertTrue(uploaded["stream_url"].startswith("/media/"))

        quiz = content.create_lesson(module["id"], {
            "title": "Check", "lesson_type": "quiz", "content": "Answer all",
            "quiz_pass_score": 60,
        }, "E1004")
        question = content.add_question(quiz["id"], {
            "prompt": "Pick B", "options": ["A", "B", "C"], "correct_index": 1,
            "explanation": "B is correct",
        }, "E1004")
        self.assertEqual(question["options"], ["A", "B", "C"])
        self.assertEqual(question["correct_index"], 1)

        updated = content.update_lesson(quiz["id"], {"title": "Check your knowledge"}, "E1004")
        self.assertEqual(updated["title"], "Check your knowledge")
        self.assertEqual(updated["question_count"], 1)

        patched = content.update_course("AUTH-1", {"status": "published"}, "E1004")
        self.assertEqual(patched["status"], "published")
        self.assertEqual(patched["lesson_count"], 4)

        content.delete_question(question["id"], "E1004")
        content.delete_lesson(uploaded["id"], "E1004")
        content.delete_module(module["id"], "E1004")
        refreshed = content.get_course_detail("AUTH-1", actor="E1004", include_unpublished=True)
        self.assertEqual(refreshed["module_count"], 0)

    def test_invalid_lesson_payloads_rejected(self):
        module = content.create_module("SEC-101", {"title": "Extra"}, "E1004")
        with self.assertRaises(ValueError):
            content.create_lesson(module["id"], {"title": "Bad", "lesson_type": "video"}, "E1004")
        with self.assertRaises(ValueError):
            content.create_lesson(module["id"], {"title": "Bad", "lesson_type": "link",
                                                "content": "javascript:alert(1)"}, "E1004")
        with self.assertRaises(ValueError):
            content.create_lesson(module["id"], {"title": "Bad", "lesson_type": "nope"}, "E1004")


class MediaTests(IsolatedContentDb):
    def test_store_and_verify(self):
        item = media.store("logo.png", "image/png", PNG, "E1004")
        self.assertEqual(item["kind"], "image")
        self.assertIn("/media/", item["url"])
        expires = item["url"].split("exp=")[1].split("&")[0]
        signature = item["url"].split("sig=")[1]
        self.assertTrue(media.verify(item["id"], expires, signature))
        self.assertFalse(media.verify(item["id"], expires, signature + "0"))
        self.assertFalse(media.verify(item["id"], "0", signature))

    def test_rejects_mismatched_content(self):
        with self.assertRaises(ValueError):
            media.store("fake.png", "image/png", MP4, "E1004")
        with self.assertRaises(ValueError):
            media.store("script.html", "text/html", b"<script>", "E1004")
        with self.assertRaises(ValueError):
            media.store("empty.png", "image/png", b"", "E1004")


class EnrollmentAndApiTests(IsolatedContentDb):
    def test_self_enrollment(self):
        assignment = content.enroll("E1001", "MGR-101", "E1001")
        self.assertEqual(assignment["assignment_source"], "self")
        self.assertEqual(assignment["status"], "assigned")
        again = content.enroll("E1001", "MGR-101", "E1001")
        self.assertEqual(again["id"], assignment["id"])

    def test_learner_cannot_author_content(self):
        learner = {"actor": "E1003", "role": "learner"}
        result = api.handle("POST", "/api/v1/courses", {}, {
            "course_code": "NOPE-1", "title": "Nope",
        }, learner)
        self.assertEqual(result.status, 403)

        instructor = {"actor": "E1004", "role": "instructor"}
        created = api.handle("POST", "/api/v1/courses", {}, {
            "course_code": "API-1", "title": "Via API", "status": "draft",
        }, instructor)
        self.assertEqual(created.status, 201)
        self.assertEqual(created.payload["course_code"], "API-1")

        detail = api.handle("GET", "/api/v1/courses/API-1", {}, {}, instructor)
        self.assertEqual(detail.status, 200)
        self.assertEqual(detail.payload["title"], "Via API")

    def test_media_upload_requires_content_permission(self):
        learner = {"actor": "E1003", "role": "learner"}
        denied = api.handle_media_upload(learner, "image/png", "x.png", PNG)
        self.assertEqual(denied.status, 403)

        instructor = {"actor": "E1004", "role": "instructor"}
        allowed = api.handle_media_upload(instructor, "image/png", "x.png", PNG)
        self.assertEqual(allowed.status, 201)
        self.assertEqual(allowed.payload["kind"], "image")

    def test_progress_api_scopes_to_actor(self):
        learner = {"actor": "E1003", "role": "learner"}
        detail = api.handle("GET", "/api/v1/courses/SEC-101", {}, {}, learner)
        lesson_id = detail.payload["modules"][0]["lessons"][0]["id"]
        result = api.handle("POST", f"/api/v1/lessons/{lesson_id}/progress", {},
                            {"completed": True}, learner)
        self.assertEqual(result.status, 200)
        self.assertTrue(result.payload["progress"]["progress"]["completed_lessons"] >= 1)

        other = api.handle("POST", f"/api/v1/lessons/{lesson_id}/progress", {},
                           {"employee_id": "E1001", "completed": True}, learner)
        self.assertEqual(other.status, 403)

    def test_csp_allows_media_and_youtube(self):
        csp = security_headers()["Content-Security-Policy"]
        self.assertIn("media-src 'self'", csp)
        self.assertIn("frame-src https://www.youtube-nocookie.com", csp)
        self.assertIn("img-src 'self' data: https:", csp)


if __name__ == "__main__":
    unittest.main()
