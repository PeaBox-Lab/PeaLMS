"""Versioned, forward-only schema migrations.

The LMS started as a single ``CREATE TABLE IF NOT EXISTS`` script. That is fine
for an empty database but cannot evolve an existing pilot installation. This
module records applied versions in ``schema_migrations`` and applies each
pending migration inside a transaction.

Rules for adding a migration:
* append a new ``Migration`` with the next integer version;
* never edit a migration that has shipped;
* keep every statement idempotent where SQLite allows it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


BASELINE = (
    """
    CREATE TABLE IF NOT EXISTS employees (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      employee_id TEXT NOT NULL UNIQUE,
      user_name TEXT NOT NULL UNIQUE,
      first_name TEXT NOT NULL,
      last_name TEXT NOT NULL,
      display_name TEXT NOT NULL,
      work_email TEXT,
      employment_status TEXT NOT NULL DEFAULT 'active',
      hire_date TEXT,
      termination_date TEXT,
      manager_employee_id TEXT,
      legal_entity TEXT,
      business_unit TEXT,
      department TEXT,
      location TEXT,
      job_code TEXT,
      job_title TEXT,
      job_family TEXT,
      grade TEXT,
      worker_type TEXT,
      country TEXT,
      time_zone TEXT,
      locale TEXT,
      cost_center TEXT,
      source_last_modified_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS employee_roles (
      employee_id TEXT NOT NULL,
      role TEXT NOT NULL,
      scope TEXT NOT NULL DEFAULT 'self',
      assigned_by TEXT NOT NULL DEFAULT 'system',
      assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(employee_id, role, scope),
      FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS courses (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      course_code TEXT NOT NULL UNIQUE,
      title TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      category TEXT NOT NULL DEFAULT 'General',
      duration_minutes INTEGER NOT NULL DEFAULT 30,
      mandatory INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1,
      version TEXT NOT NULL DEFAULT '1.0',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assignment_policies (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      course_code TEXT NOT NULL,
      attribute TEXT NOT NULL,
      operator TEXT NOT NULL DEFAULT 'equals',
      value TEXT NOT NULL,
      due_days INTEGER NOT NULL DEFAULT 30,
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(course_code) REFERENCES courses(course_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assignments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      employee_id TEXT NOT NULL,
      course_code TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'assigned',
      assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      due_date TEXT,
      completed_at TEXT,
      score REAL,
      assignment_source TEXT NOT NULL DEFAULT 'manual',
      policy_id INTEGER,
      UNIQUE(employee_id, course_code),
      FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE,
      FOREIGN KEY(course_code) REFERENCES courses(course_code),
      FOREIGN KEY(policy_id) REFERENCES assignment_policies(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      filename TEXT NOT NULL,
      checksum TEXT NOT NULL,
      mode TEXT NOT NULL,
      status TEXT NOT NULL,
      row_count INTEGER NOT NULL DEFAULT 0,
      accepted_count INTEGER NOT NULL DEFAULT 0,
      rejected_count INTEGER NOT NULL DEFAULT 0,
      create_count INTEGER NOT NULL DEFAULT 0,
      update_count INTEGER NOT NULL DEFAULT 0,
      suspend_count INTEGER NOT NULL DEFAULT 0,
      summary_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_type TEXT NOT NULL,
      actor TEXT NOT NULL,
      entity_type TEXT NOT NULL,
      entity_id TEXT NOT NULL,
      details_json TEXT NOT NULL DEFAULT '{}',
      correlation_id TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_assignments_employee ON assignments(employee_id)",
    "CREATE INDEX IF NOT EXISTS idx_assignments_status ON assignments(status)",
    "CREATE INDEX IF NOT EXISTS idx_employees_department ON employees(department)",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at)",
)

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline", BASELINE),
    Migration(
        2,
        "enterprise_controls",
        (
            # Course delivery metadata and certification rules.
            "ALTER TABLE courses ADD COLUMN recertification_days INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE courses ADD COLUMN passing_score REAL",
            "ALTER TABLE courses ADD COLUMN content_url TEXT NOT NULL DEFAULT ''",
            # Assignments gain cycles so a completed course can be reassigned
            # for annual recertification without losing history.
            """
            CREATE TABLE assignments_v2 (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              employee_id TEXT NOT NULL,
              course_code TEXT NOT NULL,
              cycle INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL DEFAULT 'assigned',
              assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              started_at TEXT,
              due_date TEXT,
              completed_at TEXT,
              score REAL,
              attempts INTEGER NOT NULL DEFAULT 0,
              exempted INTEGER NOT NULL DEFAULT 0,
              exemption_reason TEXT,
              recert_due_date TEXT,
              completed_by TEXT,
              assignment_source TEXT NOT NULL DEFAULT 'manual',
              policy_id INTEGER,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(employee_id, course_code, cycle),
              FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE,
              FOREIGN KEY(course_code) REFERENCES courses(course_code),
              FOREIGN KEY(policy_id) REFERENCES assignment_policies(id)
            )
            """,
            """
            INSERT INTO assignments_v2
              (id, employee_id, course_code, cycle, status, assigned_at, due_date,
               completed_at, score, assignment_source, policy_id)
            SELECT id, employee_id, course_code, 1, status, assigned_at, due_date,
                   completed_at, score, assignment_source, policy_id
            FROM assignments
            """,
            "DROP TABLE assignments",
            "ALTER TABLE assignments_v2 RENAME TO assignments",
            "CREATE INDEX IF NOT EXISTS idx_assignments_employee ON assignments(employee_id)",
            "CREATE INDEX IF NOT EXISTS idx_assignments_status ON assignments(status)",
            "CREATE INDEX IF NOT EXISTS idx_assignments_due ON assignments(due_date)",
            "CREATE INDEX IF NOT EXISTS idx_assignments_recert ON assignments(recert_due_date)",
            "CREATE INDEX IF NOT EXISTS idx_employees_manager ON employees(manager_employee_id)",
            # Durable notification outbox. Delivery is pluggable; the default
            # transport writes to the structured log so no email dependency is
            # required for evaluation or tests.
            """
            CREATE TABLE notifications (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              recipient_employee_id TEXT NOT NULL,
              template TEXT NOT NULL,
              channel TEXT NOT NULL DEFAULT 'in_app',
              subject TEXT NOT NULL,
              body TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued',
              entity_type TEXT,
              entity_id TEXT,
              correlation_id TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              sent_at TEXT,
              read_at TEXT,
              FOREIGN KEY(recipient_employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_employee_id, read_at)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status)",
        ),
    ),
    Migration(
        3,
        "learning_content",
        (
            # Rich catalog metadata. Existing rows keep sensible defaults so the
            # upgrade is additive for a v0.2.0 pilot database.
            "ALTER TABLE courses ADD COLUMN summary TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE courses ADD COLUMN level TEXT NOT NULL DEFAULT 'All levels'",
            "ALTER TABLE courses ADD COLUMN language TEXT NOT NULL DEFAULT 'en'",
            "ALTER TABLE courses ADD COLUMN thumbnail_url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE courses ADD COLUMN thumbnail_media_id INTEGER",
            "ALTER TABLE courses ADD COLUMN author TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE courses ADD COLUMN objectives TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE courses ADD COLUMN tags TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE courses ADD COLUMN status TEXT NOT NULL DEFAULT 'published'",
            "ALTER TABLE courses ADD COLUMN prerequisite_codes TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE courses ADD COLUMN updated_by TEXT",
            # Uploaded media (videos, images, documents). Files live on disk and
            # are served through signed, expiring URLs with HTTP range support.
            """
            CREATE TABLE media (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              filename TEXT NOT NULL,
              stored_name TEXT NOT NULL UNIQUE,
              content_type TEXT NOT NULL,
              size_bytes INTEGER NOT NULL DEFAULT 0,
              kind TEXT NOT NULL DEFAULT 'file',
              checksum TEXT NOT NULL DEFAULT '',
              uploaded_by TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # Curriculum: a course has ordered modules, a module has ordered
            # lessons. Lessons are text, uploaded video, YouTube, link or quiz.
            """
            CREATE TABLE course_modules (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              course_code TEXT NOT NULL,
              title TEXT NOT NULL,
              summary TEXT NOT NULL DEFAULT '',
              position INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(course_code) REFERENCES courses(course_code) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE lessons (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              module_id INTEGER NOT NULL,
              course_code TEXT NOT NULL,
              title TEXT NOT NULL,
              lesson_type TEXT NOT NULL DEFAULT 'text',
              content TEXT NOT NULL DEFAULT '',
              media_id INTEGER,
              duration_minutes INTEGER NOT NULL DEFAULT 0,
              position INTEGER NOT NULL DEFAULT 0,
              is_required INTEGER NOT NULL DEFAULT 1,
              quiz_pass_score REAL NOT NULL DEFAULT 70,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(module_id) REFERENCES course_modules(id) ON DELETE CASCADE,
              FOREIGN KEY(course_code) REFERENCES courses(course_code) ON DELETE CASCADE,
              FOREIGN KEY(media_id) REFERENCES media(id)
            )
            """,
            """
            CREATE TABLE lesson_progress (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              employee_id TEXT NOT NULL,
              lesson_id INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'in_progress',
              position_seconds REAL NOT NULL DEFAULT 0,
              completed_at TEXT,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(employee_id, lesson_id),
              FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE,
              FOREIGN KEY(lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE quiz_questions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              lesson_id INTEGER NOT NULL,
              prompt TEXT NOT NULL,
              options_json TEXT NOT NULL DEFAULT '[]',
              correct_index INTEGER NOT NULL DEFAULT 0,
              explanation TEXT NOT NULL DEFAULT '',
              position INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY(lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE quiz_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              employee_id TEXT NOT NULL,
              lesson_id INTEGER NOT NULL,
              score REAL NOT NULL DEFAULT 0,
              passed INTEGER NOT NULL DEFAULT 0,
              answers_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE,
              FOREIGN KEY(lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_modules_course ON course_modules(course_code, position)",
            "CREATE INDEX IF NOT EXISTS idx_lessons_module ON lessons(module_id, position)",
            "CREATE INDEX IF NOT EXISTS idx_lessons_course ON lessons(course_code)",
            "CREATE INDEX IF NOT EXISTS idx_progress_employee ON lesson_progress(employee_id)",
            "CREATE INDEX IF NOT EXISTS idx_questions_lesson ON quiz_questions(lesson_id, position)",
            "CREATE INDEX IF NOT EXISTS idx_attempts_employee ON quiz_attempts(employee_id, lesson_id)",
            "CREATE INDEX IF NOT EXISTS idx_courses_status ON courses(status, active)",
        ),
    ),
    Migration(
        4,
        "engagement_and_credentials",
        (
            # Cohorts group people for targeting, reporting and enrolment.
            """
            CREATE TABLE cohorts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE cohort_members (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              cohort_id INTEGER NOT NULL,
              employee_id TEXT NOT NULL,
              added_by TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(cohort_id, employee_id),
              FOREIGN KEY(cohort_id) REFERENCES cohorts(id) ON DELETE CASCADE,
              FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
            )
            """,
            # Learning paths are ordered course sequences with auto-advancement.
            """
            CREATE TABLE learning_paths (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'published',
              created_by TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE learning_path_courses (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path_id INTEGER NOT NULL,
              course_code TEXT NOT NULL,
              position INTEGER NOT NULL DEFAULT 0,
              is_required INTEGER NOT NULL DEFAULT 1,
              UNIQUE(path_id, course_code),
              FOREIGN KEY(path_id) REFERENCES learning_paths(id) ON DELETE CASCADE,
              FOREIGN KEY(course_code) REFERENCES courses(course_code) ON DELETE CASCADE
            )
            """,
            # Badges and verifiable completion certificates.
            """
            CREATE TABLE badges (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              icon TEXT NOT NULL DEFAULT 'star',
              criteria_course_code TEXT,
              criteria_path_id INTEGER,
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(criteria_course_code) REFERENCES courses(course_code) ON DELETE CASCADE,
              FOREIGN KEY(criteria_path_id) REFERENCES learning_paths(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE badge_awards (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              badge_id INTEGER NOT NULL,
              employee_id TEXT NOT NULL,
              verification_code TEXT NOT NULL UNIQUE,
              source TEXT NOT NULL DEFAULT 'course_completion',
              score REAL,
              awarded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(badge_id, employee_id),
              FOREIGN KEY(badge_id) REFERENCES badges(id) ON DELETE CASCADE,
              FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
            )
            """,
            # Course discussions: lightweight, moderated forums.
            """
            CREATE TABLE discussions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              course_code TEXT NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',
              created_by TEXT NOT NULL,
              pinned INTEGER NOT NULL DEFAULT 0,
              closed INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(course_code) REFERENCES courses(course_code) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE discussion_posts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              discussion_id INTEGER NOT NULL,
              parent_id INTEGER,
              author_id TEXT NOT NULL,
              body TEXT NOT NULL,
              edited_at TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(discussion_id) REFERENCES discussions(id) ON DELETE CASCADE,
              FOREIGN KEY(parent_id) REFERENCES discussion_posts(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_cohort_members_employee ON cohort_members(employee_id, cohort_id)",
            "CREATE INDEX IF NOT EXISTS idx_cohort_members_cohort ON cohort_members(cohort_id)",
            "CREATE INDEX IF NOT EXISTS idx_path_courses_path ON learning_path_courses(path_id, position)",
            "CREATE INDEX IF NOT EXISTS idx_badges_criteria ON badges(criteria_course_code, criteria_path_id)",
            "CREATE INDEX IF NOT EXISTS idx_badge_awards_employee ON badge_awards(employee_id, awarded_at)",
            "CREATE INDEX IF NOT EXISTS idx_discussions_course ON discussions(course_code, pinned DESC, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_posts_discussion ON discussion_posts(discussion_id, created_at)",
        ),
    ),
    Migration(
        5,
        "session_controls",
        (
            """
            CREATE TABLE IF NOT EXISTS session_revocations (
              jti TEXT PRIMARY KEY,
              actor TEXT NOT NULL,
              revoked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS app_state (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "INSERT OR IGNORE INTO app_state(key, value) VALUES('session_epoch', '0')",
            "CREATE INDEX IF NOT EXISTS idx_session_revocations_actor ON session_revocations(actor)",
        ),
    ),
)

SCHEMA_VERSION = max(m.version for m in MIGRATIONS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return found is not None


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    if not _table_exists(conn, "schema_migrations"):
        return set()
    return {int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations")}


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply all pending migrations. Returns the versions applied this call."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )
    done = applied_versions(conn)
    applied: list[int] = []

    # Adopt databases created by v0.1.0, which had no migration ledger.
    if not done and _table_exists(conn, "employees"):
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
            (1, "baseline (adopted legacy database)", _now()),
        )
        done = {1}

    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version in done:
            continue
        if conn.in_transaction:
            conn.commit()
        try:
            conn.execute("BEGIN")
            for statement in migration.statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                (migration.version, migration.name, _now()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        applied.append(migration.version)
    return applied
