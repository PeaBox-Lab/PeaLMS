import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path(os.environ.get('LMS_DATA_DIR', Path.home() / '.corporate-lms'))
DB_PATH = Path(os.environ.get("LMS_DB_PATH", DEFAULT_DATA_DIR / "lms.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;
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
);
CREATE TABLE IF NOT EXISTS employee_roles (
  employee_id TEXT NOT NULL,
  role TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'self',
  assigned_by TEXT NOT NULL DEFAULT 'system',
  assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(employee_id, role, scope),
  FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
);
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
);
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
);
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
);
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
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  correlation_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_assignments_employee ON assignments(employee_id);
CREATE INDEX IF NOT EXISTS idx_assignments_status ON assignments(status);
CREATE INDEX IF NOT EXISTS idx_employees_department ON employees(department);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
"""

@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with connection() as conn:
        conn.executescript(SCHEMA)

def rows(query, params=()):
    with connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]

def row(query, params=()):
    with connection() as conn:
        found = conn.execute(query, params).fetchone()
        return dict(found) if found else None
