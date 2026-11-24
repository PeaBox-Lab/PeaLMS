# Corporate LMS v0.2.0

A runnable corporate learning management system derived from the Corporate LMS
Product Blueprint. v0.2.0 is an enterprise-controls foundation: guarded HRIS
provisioning, manager-scoped views, a full assignment lifecycle, certification
cycles, a notification outbox, reporting, and a hardened HTTP surface.

## What works

### People and organization
- Employee directory with organizational attributes and derived roles.
- Recursive manager hierarchy with team-scoped dashboards and assignments.
- Attribute-based assignment policies with seven match operators.
- Search, filter and pagination for people, assignments and audit events.

### Learning
- Course catalog with versions, duration, mandatory flag, passing score,
  recertification period and content URL.
- Assignment lifecycle: `assigned` → `in_progress` → `completed` or `failed`,
  plus `exempted` with a required reason and `expired`.
- Due-date extension, exemption, and certification cycles. Completing a course
  with a recertification period schedules the next due date; reassignment opens
  a new cycle without losing history.
- Scores are validated 0–100 and compared with the course passing score.
- Completion evidence cannot be silently overwritten.

### Notifications
- Durable outbox with templates for assignment, due-soon, overdue,
  completion (learner and manager), recertification and exemption.
- In-app inbox with read state and an unread badge.
- Sweep job for due-soon, overdue and recertification messages, with
  deduplication per assignment cycle.
- Pluggable delivery; the default transport writes structured log entries.

### Reporting and audit
- Organization, team and personal dashboards.
- Compliance by department and CSV export scoped to the caller's permissions.
- Immutable-style audit history for every material action, with cycle context.

### HRIS integration
- CSV dry run and guarded application.
- Required-field, controlled-value, date and manager-reference validation.
- Duplicate detection, active-population anomaly threshold, checksum
  idempotency, create/update/suspend/rehire handling, role derivation and
  policy reassessment.
- Import job evidence, reconciliation summary and non-blocking warnings.

### Platform
- Versioned schema migrations with adoption of v0.1.0 databases.
- Central validated configuration; no scattered environment reads.
- Versioned API (`/api/v1`) with a deprecated `/api` compatibility alias.
- Structured JSON access logs with request IDs.
- Strict Content Security Policy, security headers, request-size limits and
  in-process rate limiting.
- Responsive, keyboard-operable interface with no inline handlers or styles.
- Zero third-party runtime dependencies.

## Quick start

Run directly from the repository:

```bash
LMS_DEMO_MODE=true python -m lms.server
```

On PowerShell:

```powershell
$env:LMS_DEMO_MODE = "true"; python -m lms.server
```

Open `http://127.0.0.1:8080`.

The application initializes `~/.corporate-lms/lms.db` and adds representative
sample employees, courses, policies and assignments on first run. Override the
location with `LMS_DB_PATH`.

Install as a package:

```bash
python -m pip install .
LMS_DEMO_MODE=true corporate-lms
```

### Useful commands

```bash
python -m lms.server --migrate-only          # apply migrations and exit
python -m lms.server --sweep-notifications   # queue due/overdue/recert messages
python -m lms.server --no-seed               # start without sample data
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LMS_HOST` | `127.0.0.1` | Bind address |
| `LMS_PORT` | `8080` | Bind port |
| `LMS_DB_PATH` | `~/.corporate-lms/lms.db` | SQLite database file |
| `LMS_DATA_DIR` | `~/.corporate-lms` | Default data directory |
| `LMS_DEMO_MODE` | `false` | Enable signed demo sessions |
| `LMS_ALLOW_REMOTE_DEMO` | `false` | Allow demo mode on a non-loopback bind |
| `LMS_SESSION_SECRET` | random per process | HMAC secret for demo sessions |
| `LMS_SESSION_TTL_SECONDS` | `3600` | Demo session lifetime |
| `LMS_RATE_LIMIT_PER_MINUTE` | `240` | Per-address request limit; `0` disables |
| `LMS_MAX_BODY_BYTES` | `5000000` | JSON request body limit |
| `LMS_LOG_LEVEL` | `INFO` | Log level |
| `LMS_SEED_ON_START` | `true` | Seed sample data on an empty database |

## Demo authorization

The interface has a **View as** selector. In demo mode the server issues
short-lived HMAC-signed sessions for the selected identity. Direct role headers
are not trusted. This is demonstration authentication: production deployment
must set `LMS_DEMO_MODE=false`, integrate an OIDC/SAML identity provider, and
derive authorization from trusted server-side claims.

Suggested demo identities:

| Role | User | What to look at |
|---|---|---|
| Platform admin | `demo-admin` | Organization dashboard, people, HRIS, audit |
| Manager | `E1002` | Team dashboard and team assignments |
| Learner | `E1003` | Own learning, start/complete, notifications |
| Instructor | `E1004` | Catalog and completion |
| Auditor | `demo-admin` | Audit and compliance export |

## API

Canonical endpoints are under `/api/v1`. The old `/api/...` paths still work and
return a `Deprecation: true` header.

```
GET  /api/v1/health
GET  /api/v1/meta
POST /api/v1/session                     (demo mode only)
GET  /api/v1/me
GET  /api/v1/dashboard                   (personal / team / organization)
GET  /api/v1/employees?q=&department=&status=&role=&limit=&offset=
POST /api/v1/employees
GET  /api/v1/employees/{id}
GET  /api/v1/courses?active_only=
POST /api/v1/courses
GET  /api/v1/courses/{code}
GET  /api/v1/policies
POST /api/v1/policies
POST /api/v1/policies/apply
GET  /api/v1/assignments?employee_id=&status=&overdue=&q=&limit=&offset=
POST /api/v1/assignments
POST /api/v1/assignments/start
POST /api/v1/assignments/complete
POST /api/v1/assignments/exempt
POST /api/v1/assignments/extend
POST /api/v1/assignments/reassign
GET  /api/v1/team
GET  /api/v1/team/assignments
GET  /api/v1/notifications?unread_only=
POST /api/v1/notifications/{id}/read
POST /api/v1/notifications/read-all
POST /api/v1/notifications/sweep
GET  /api/v1/reports/compliance.csv
GET  /api/v1/audit?event_type=&actor=&entity_type=&limit=&offset=
GET  /api/v1/import-jobs
POST /api/v1/hris/analyze
POST /api/v1/hris/apply
```

## Use the HRIS import

1. Open **HRIS import**.
2. Select `samples/hris_employee_extract.csv`.
3. Choose **Full extract** and analyze it.
4. Review creates, updates, suspensions, rejected rows, warnings and blockers.
5. Apply only when the dry run reports **Ready to apply**.

A full extract treats active employees missing from the file as suspension
candidates. Population-change safeguards prevent a small or corrupt file from
causing a mass suspension.

## Run tests

```bash
python -m unittest discover -s tests -v
```

The suite covers migrations (including v0.1.0 database adoption), organization
scoping, the assignment lifecycle, certification cycles, exemptions,
notifications and deduplication, reporting, API permissions, token handling,
rate limiting and the HTTP transport.

## Project layout

- `lms/config.py` — validated configuration and version.
- `lms/db.py` — connection handling and database entry points.
- `lms/migrations.py` — versioned, forward-only schema migrations.
- `lms/auth.py` — HMAC session tokens.
- `lms/permissions.py` — role/permission catalogue.
- `lms/api.py` — versioned route table and permission checks.
- `lms/server.py` — HTTP transport, static files, rate limiting, security headers.
- `lms/services.py` — domain rules and read models.
- `lms/notifications.py` — templates, outbox, inbox and delivery.
- `lms/reports.py` — compliance export.
- `lms/hris.py` — HRIS validation, diff and guarded application.
- `lms/security.py` — rate limiter, headers and structured logging.
- `lms/static/` — CSP-safe browser interface.
- `tests/` — automated unit, integration and HTTP tests.
- `docs/` — architecture, security and operational documentation.

## Production evolution

This is a deployable pilot foundation, not the full global enterprise platform.
See `ROADMAP.md` for the sequenced plan and `docs/ARCHITECTURE.md` for the
migration path to PostgreSQL, OIDC/SAML, object storage, workflow orchestration,
event streaming, search, analytics, Kubernetes, SCIM and enterprise
observability.

## License

Apache License 2.0. See `LICENSE`.
