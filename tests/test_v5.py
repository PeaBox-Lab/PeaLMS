"""v0.5.0 tests: proxy identity, durable jobs, outbox delivery and operations."""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lms import api, config, email, identity, jobs, notifications, services, worker
from lms.db import connection, init_db, schema_version
from lms.seed import seed


MANAGED_ENV = (
    "LMS_DB_PATH", "LMS_DATA_DIR", "LMS_MEDIA_DIR", "LMS_DEMO_MODE",
    "LMS_RATE_LIMIT_PER_MINUTE", "LMS_SESSION_SECRET", "LMS_SEED_ON_START",
    "LMS_AUTH_MODE", "LMS_PROXY_SECRET", "LMS_PROXY_ROLE_GROUPS",
    "LMS_TRUSTED_PROXIES", "LMS_SMTP_HOST", "LMS_SMTP_FROM",
)


class IsolatedV5Db(unittest.TestCase):
    def setUp(self):
        self._saved = {key: os.environ.get(key) for key in MANAGED_ENV}
        self.tmp = Path(tempfile.mkdtemp(prefix="lms-v5-"))
        for key in ("LMS_AUTH_MODE", "LMS_PROXY_SECRET", "LMS_PROXY_ROLE_GROUPS",
                    "LMS_TRUSTED_PROXIES", "LMS_SMTP_HOST", "LMS_SMTP_FROM"):
            os.environ.pop(key, None)
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
        identity.reset_nonce_cache()
        init_db()
        seed()

    def tearDown(self):
        identity.reset_nonce_cache()
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


class SchemaTests(IsolatedV5Db):
    def test_schema_version_is_six(self):
        self.assertEqual(schema_version(), 6)

    def test_worker_tables_exist(self):
        with connection() as conn:
            names = {item[0] for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertTrue({"jobs", "job_events", "notification_deliveries"} <= names)

    def test_meta_exposes_identity_and_transport(self):
        result = self.call("GET", "/meta")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["auth_mode"], "demo")
        self.assertFalse(result.payload["smtp_enabled"])


class ConfigurationTests(unittest.TestCase):
    def test_proxy_mode_requires_shared_secret(self):
        with self.assertRaises(ValueError):
            config.load_settings({"LMS_AUTH_MODE": "proxy"})

    def test_proxy_group_mapping_is_parsed_and_validated(self):
        settings = config.load_settings({
            "LMS_AUTH_MODE": "proxy",
            "LMS_PROXY_SECRET": "shared-secret",
            "LMS_PROXY_ROLE_GROUPS": "admins=learning_admin,trainers=instructor",
        })
        self.assertEqual(settings.proxy_role_groups, {
            "admins": "learning_admin", "trainers": "instructor",
        })
        with self.assertRaises(ValueError):
            config.load_settings({
                "LMS_AUTH_MODE": "proxy", "LMS_PROXY_SECRET": "s",
                "LMS_PROXY_ROLE_GROUPS": "admins=superuser",
            })

    def test_smtp_transport_is_detected_from_host_and_sender(self):
        settings = config.load_settings({
            "LMS_SMTP_HOST": "smtp.example.test", "LMS_SMTP_FROM": "lms@example.test",
        })
        self.assertTrue(settings.smtp_enabled)

    def test_unknown_auth_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            config.load_settings({"LMS_AUTH_MODE": "anything"})


class ProxyIdentityTests(IsolatedV5Db):
    def enable_proxy(self, groups="admins=learning_admin,managers=manager"):
        os.environ.update({
            "LMS_AUTH_MODE": "proxy",
            "LMS_PROXY_SECRET": "proxy-shared-secret",
            "LMS_PROXY_ROLE_GROUPS": groups,
            "LMS_TRUSTED_PROXIES": "127.0.0.1/32",
        })
        config.reset_settings()
        identity.reset_nonce_cache()

    def assertion(self, **overrides):
        now = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "sub": "E1003", "groups": ["learners"], "iat": now,
            "exp": now + 300, "nonce": "nonce-123456",
        }
        payload.update(overrides)
        return identity.sign_assertion(payload, "proxy-shared-secret")

    def test_signed_assertion_maps_server_group_to_role(self):
        self.enable_proxy()
        result = identity.verify_header(
            self.assertion(groups=["admins"]), remote="127.0.0.1"
        )
        self.assertEqual(result["actor"], "E1003")
        self.assertEqual(result["role"], "learning_admin")
        self.assertEqual(result["source"], "proxy")

    def test_client_supplied_role_is_ignored(self):
        self.enable_proxy()
        result = identity.verify_header(
            self.assertion(groups=["managers"], role="platform_admin"),
            remote="127.0.0.1",
        )
        self.assertEqual(result["role"], "manager")

    def test_highest_priority_group_wins(self):
        self.enable_proxy()
        result = identity.verify_header(
            self.assertion(groups=["managers", "admins"]), remote="127.0.0.1"
        )
        self.assertEqual(result["role"], "learning_admin")

    def test_tampered_assertion_is_rejected(self):
        self.enable_proxy()
        token = self.assertion()[:-1] + ("a" if self.assertion()[-1] != "a" else "b")
        with self.assertRaises(identity.ProxyIdentityError):
            identity.verify_header(token, remote="127.0.0.1")

    def test_expired_and_replayed_assertions_are_rejected(self):
        self.enable_proxy()
        now = int(datetime.now(timezone.utc).timestamp())
        expired = identity.sign_assertion({
            "sub": "E1003", "groups": [], "iat": now - 600,
            "exp": now - 300, "nonce": "expired-123456",
        }, "proxy-shared-secret")
        with self.assertRaises(identity.ProxyIdentityError):
            identity.verify_header(expired, remote="127.0.0.1")
        identity.verify_header(self.assertion(), remote="127.0.0.1")
        with self.assertRaises(identity.ProxyIdentityError):
            identity.verify_header(self.assertion(), remote="127.0.0.1")

    def test_untrusted_remote_is_rejected(self):
        self.enable_proxy()
        with self.assertRaises(identity.ProxyIdentityError):
            identity.verify_header(self.assertion(), remote="10.0.0.9")

    def test_unknown_subject_is_rejected(self):
        self.enable_proxy()
        with self.assertRaises(identity.ProxyIdentityError):
            identity.verify_header(
                self.assertion(sub="E9999", nonce="unknown-123456"),
                remote="127.0.0.1",
            )

    def test_from_headers_ignores_unsigned_role_header(self):
        self.enable_proxy()
        headers = {
            "X-LMS-Identity": self.assertion(groups=["admins"]),
            "X-Role": "platform_admin",
        }
        result = identity.from_headers(headers, remote="127.0.0.1")
        self.assertEqual(result["role"], "learning_admin")


class JobLedgerTests(IsolatedV5Db):
    def test_enqueue_claim_and_complete(self):
        job_id = jobs.enqueue("notification_sweep", {"days_soon": 7}, actor="E1002")
        claimed = jobs.claim("worker-1")
        self.assertEqual([item["id"] for item in claimed], [job_id])
        self.assertEqual(claimed[0]["attempts"], 1)
        self.assertTrue(jobs.complete(job_id, {"due_soon": 0}))
        job = jobs.get_job(job_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertIsNone(job["locked_by"])

    def test_failure_retries_then_dead_letters_and_can_be_retried(self):
        job_id = jobs.enqueue("policy_apply", actor="E1002")
        jobs.claim("worker-1")
        self.assertTrue(jobs.fail(job_id, "temporary", attempts=1))
        self.assertEqual(jobs.get_job(job_id)["status"], "queued")
        with connection() as conn:
            conn.execute("UPDATE jobs SET run_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
        jobs.claim("worker-1")
        self.assertTrue(jobs.fail(job_id, "permanent", attempts=3))
        self.assertEqual(jobs.get_job(job_id)["status"], "failed")
        self.assertTrue(jobs.retry(job_id, "E1002"))
        retried = jobs.get_job(job_id)
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["attempts"], 0)

    def test_worker_runs_a_queued_sweep(self):
        job_id = jobs.enqueue("notification_sweep", actor="E1002")
        results = worker.run_once(1)
        self.assertEqual(results[0]["status"], "succeeded")
        self.assertIsNotNone(results[0]["result"]["delivered"])
        self.assertEqual(jobs.get_job(job_id)["status"], "succeeded")

    def test_job_event_history_is_recorded(self):
        job_id = jobs.enqueue("outbox_delivery", actor="E1002")
        with connection() as conn:
            events = [item[0] for item in conn.execute(
                "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
            )]
        self.assertIn("queued", events)


class OperationsApiTests(IsolatedV5Db):
    def test_operations_evidence_requires_permission(self):
        self.assertEqual(self.call("GET", "/jobs", role="learner", actor="E1003").status, 403)
        self.assertEqual(self.call("GET", "/outbox", role="learner", actor="E1003").status, 403)
        self.assertEqual(self.call("GET", "/jobs", role="auditor").status, 200)

    def test_sweep_endpoint_queues_a_job(self):
        result = self.call("POST", "/notifications/sweep", {}, role="learning_admin")
        self.assertEqual(result.status, 202)
        self.assertTrue(result.payload["queued"])
        self.assertEqual(jobs.get_job(result.payload["job_id"])["job_type"],
                         "notification_sweep")

    def test_generic_queue_and_retry_flow(self):
        queued = self.call("POST", "/jobs", {"job_type": "outbox_delivery", "payload": {}})
        self.assertEqual(queued.status, 202)
        self.assertEqual(self.call("POST", "/jobs", {"job_type": "unknown"}).status, 400)
        jobs.claim("worker-1")
        jobs.fail(queued.payload["job_id"], "broken", attempts=3)
        result = self.call("POST", f"/jobs/{queued.payload['job_id']}/retry", {})
        self.assertEqual(result.status, 200)
        self.assertEqual(jobs.get_job(queued.payload["job_id"])["status"], "queued")


class OutboxTests(IsolatedV5Db):
    def setUp(self):
        super().setUp()
        with connection() as conn:
            conn.execute("DELETE FROM notifications")

    def queue_notification(self):
        with connection() as conn:
            notification_id = notifications.queue(
                conn, "E1003", "assignment_created",
                {
                    "first_name": "Meera", "course_title": "Security Basics",
                    "course_code": "SEC-101", "due_date": "2026-12-01",
                },
                entity_type="assignment", entity_id="E1003:SEC-101:1",
            )
        return notification_id

    def test_failed_delivery_is_bounded_and_dead_lettered(self):
        notification_id = self.queue_notification()

        def failing(item):
            raise RuntimeError("smtp unavailable")

        self.assertEqual(notifications.deliver(failing, max_attempts=2), 0)
        self.assertEqual(notifications.deliver(failing, max_attempts=2), 0)
        with connection() as conn:
            status = conn.execute(
                "SELECT status, attempts FROM notifications WHERE id=?",
                (notification_id,),
            ).fetchone()
            deliveries = [dict(item) for item in conn.execute(
                "SELECT status FROM notification_deliveries"
            )]
        self.assertEqual(status["status"], "queued")
        self.assertEqual(status["attempts"], 1)
        self.assertEqual([item["status"] for item in deliveries], ["failed"])
        # Force the next attempt past its backoff to exercise the dead letter.
        with connection() as conn:
            conn.execute(
                "UPDATE notifications SET next_attempt_at=NULL WHERE id=?",
                (notification_id,),
            )
        self.assertEqual(notifications.deliver(failing, max_attempts=2), 0)
        with connection() as conn:
            statuses = [item[0] for item in conn.execute(
                "SELECT status FROM notification_deliveries ORDER BY id"
            )]
        self.assertEqual(statuses, ["failed", "dead"])
        self.assertEqual(notifications.list_deliveries()["total"], 2)

    def test_successful_delivery_marks_notification_sent(self):
        notification_id = self.queue_notification()
        self.assertEqual(notifications.deliver(lambda item: None), 1)
        with connection() as conn:
            status = conn.execute(
                "SELECT status FROM notifications WHERE id=?", (notification_id,)
            ).fetchone()[0]
        self.assertEqual(status, "sent")

    def test_unconfigured_smtp_transport_fails_permanently(self):
        with self.assertRaises(email.PermanentDeliveryFailure):
            email.smtp_transport({"recipient_employee_id": "E1003"})

    def test_service_sweeps_still_queue_in_app_notifications(self):
        result = services.run_notification_sweeps()
        self.assertIn("due_soon", result)
        self.assertIn("delivered", result)


if __name__ == "__main__":
    unittest.main()
