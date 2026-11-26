# PeaLMS v0.4.0

A dependency-free corporate learning management system that is deliberately
Moodle-like in capability and stricter than Moodle by construction: one SQLite
file, no runtime packages, no inline scripts or styles, no outbound network, and
a single auditable codebase.

v0.2.0 established enterprise controls. v0.3.0 added real learning content.
v0.4.0 adds the engagement layer that makes the product feel like an LMS rather
than a compliance spreadsheet: cohorts, learning paths, gradebook, verifiable
badges and certificates, and course discussions.

## What works

### People and organization
- Employee directory with organizational attributes and derived roles.
- Recursive manager hierarchy with team-scoped dashboards and assignments.
- Attribute-based assignment policies with seven match operators, including
  cohort membership.
- Search, filter and pagination for people, assignments and audit events.

### Learning content (v0.3.0)
- Course catalog with modules, ordered lessons, publishing state, duration,
  mandatory flag, passing score, recertification period and version.
- Lesson types: `text`, `video`, `youtube`, `link` and `quiz`.
- Uploaded videos and images are stored outside the static tree and served
  through short-lived signed URLs with byte-range support.
- YouTube lessons use privacy-enhanced `youtube-nocookie.com` embeds; external
  video URLs are rejected by the strict content security policy.
- Question banks, attempts, per-question explanations revealed only after a
  pass, and automatic course completion when all required lessons are done.
- Learners cannot self-report scores or skip required lessons.

### Engagement (v0.4.0)
- Cohorts with members, targeted assignments and policy matching.
- Learning paths that unlock the next course when the current one is complete.
- Gradebook combining assignment scores with quiz evidence and letter grades.
- Badges and completion certificates with verification codes and a public
  verification endpoint.
- Course discussions with threaded replies, pinning, closing and moderation.
- Role-native home views: learner, instructor, manager, admin and auditor.

### Notifications
- Durable outbox with templates for assignment, due-soon, overdue,
  completion (learner and manager), recertification and exemption.
- In-app inbox with read state and an accurate unread badge.
- Sweep job for due-soon, overdue and recertification messages, with
  deduplication per assignment cycle.

### Reporting and audit
- Organization, team and personal dashboards.
- Compliance by department and CSV export scoped to the caller's permissions;
  formula-like cell values are neutralised.
- Hash-chained-style audit history for every material action, with cycle
  context and request ids.

### HRIS integration
- CSV dry run and guarded application; the reviewed snapshot is invalidated if
  the file or options change before **Apply**.
- Required-field, controlled-value, date and manager-reference validation.
- Duplicate detection, active-population anomaly threshold, checksum
  idempotency, create/update/suspend/rehire handling, role derivation and
  policy reassessment.

### Platform
- Versioned schema migrations through v5, including adoption of v0.1.0
  databases.
- HMAC session tokens bound to browser fingerprint, with revocation and
  server-side epoch rotation.
- Demo sessions restricted to loopback clients; shipped Docker stack does not
  enable demo impersonation.
- Versioned API (`/api/v1`) with a deprecated `/api` compatibility alias.
- Strict CSP with `media-src 'self'` and narrow YouTube frame origins.
- Streaming, magic-byte-validated media uploads with size limits.
- Rate limiting scoped to client and identity, security headers, request-size
  limits and structured JSON access logs.
- Responsive, keyboard-operable interface with no inline handlers or styles.
- Zero third-party runtime dependencies.

## Quick start

```bash
LMS_DEMO_MODE=true python -m lms.server
```

On PowerShell:

```powershell
$env:LMS_DEMO_MODE = "true"; python -m lms.server
```

Open `http://127.0.0.1:8080`.

The application initializes `~/.corporate-lms/lms.db` and adds representative
sample employees, courses, modules, lessons, cohorts, paths, badges and
discussions on first run. Override the location with `LMS_DB_PATH`.

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
| `LMS_MEDIA_DIR` | `~/.corporate-lms/media` | Uploaded media directory |
| `LMS_DEMO_MODE` | `false` | Enable signed demo sessions |
| `LMS_ALLOW_REMOTE_DEMO` | `false` | Allow demo mode on a non-loopback bind |
| `LMS_SESSION_SECRET` | random per process | HMAC secret for sessions and media links |
| `LMS_SESSION_TTL_SECONDS` | `3600` | Session lifetime |
| `LMS_MAX_BODY_BYTES` | `5000000` | JSON request body limit |
| `LMS_MAX_UPLOAD_BYTES` | `200000000` | Media upload limit |
| `LMS_RATE_LIMIT_PER_MINUTE` | `240` | Per client/identity limit; `0` disables |
| `LMS_LOG_LEVEL` | `INFO` | Log level |
| `LMS_SEED_ON_START` | `true` | Seed sample data on an empty database |

## Demo authorization

The interface has a **View as** selector. In demo mode the server issues
short-lived HMAC-signed sessions for the selected identity and binds them to the
browser's user agent. Direct role headers are not trusted. Production must set
`LMS_DEMO_MODE=false`, integrate an OIDC/SAML identity provider, and derive
authorization from trusted server-side claims.

| Role | User | What to look at |
|---|---|---|
| Platform admin | `demo-admin` | Dashboards, people, HRIS, audit, cohorts, paths |
| Manager | `E1002` | Team dashboard, team assignments, gradebook |
| Learner | `E1003` | My learning, lessons, quizzes, credentials, discussions |
| Instructor | `E1004` | Course authoring, quiz management, discussions |
| Auditor | `demo-admin` | Audit and compliance export |

## API

Canonical endpoints are under `/api/v1`. The old `/api/...` paths still work and
return a `Deprecation: true` header.

```text
GET    /api/v1/health
GET    /api/v1/meta
POST   /api/v1/session                     (demo mode only)
POST   /api/v1/session/logout
POST   /api/v1/session/rotate              (manage_learning)
GET    /api/v1/me
GET    /api/v1/dashboard
GET    /api/v1/learners
GET    /api/v1/employees?q=&department=&status=&role=&limit=&offset=
POST   /api/v1/employees
GET    /api/v1/employees/{id}
GET    /api/v1/courses?active_only=
POST   /api/v1/courses
GET    /api/v1/courses/{code}
GET    /api/v1/courses/{code}/modules
POST   /api/v1/modules
POST   /api/v1/lessons
GET    /api/v1/lessons/{id}/quiz
POST   /api/v1/lessons/{id}/quiz/attempt
POST   /api/v1/media                       (raw binary upload)
GET    /api/v1/learning-paths
POST   /api/v1/learning-paths
POST   /api/v1/learning-paths/{id}/enroll
GET    /api/v1/gradebook?employee_id=
GET    /api/v1/cohorts
GET    /api/v1/badges
POST   /api/v1/badges/{id}/award
GET    /api/v1/credentials/verify/{code}
GET    /api/v1/courses/{code}/discussions
POST   /api/v1/discussions/{id}/posts
GET    /api/v1/policies
POST   /api/v1/policies/apply
GET    /api/v1/assignments?employee_id=&status=&overdue=&limit=&offset=
POST   /api/v1/assignments/start
POST   /api/v1/assignments/complete
POST   /api/v1/assignments/exempt
POST   /api/v1/assignments/extend
POST   /api/v1/assignments/reassign
GET    /api/v1/team
GET    /api/v1/notifications?unread_only=
POST   /api/v1/notifications/{id}/read
GET    /api/v1/reports/compliance.csv
GET    /api/v1/audit?event_type=&actor=&limit=&offset=
POST   /api/v1/hris/analyze
POST   /api/v1/hris/apply
```

## Use the HRIS import

1. Open **HRIS import**.
2. Select `samples/hris_employee_extract.csv`.
3. Choose **Full extract** and analyze it.
4. Review creates, updates, suspensions, rejected rows, warnings and blockers.
5. Apply only when the dry run reports **Ready to apply**. Changing the file or
   options disables Apply until you analyze again.

A full extract treats active employees missing from the file as suspension
candidates. Population-change safeguards prevent a small or corrupt file from
causing a mass suspension.

## Run tests

```bash
python -m unittest discover -s tests -v
```

The 112 tests cover migrations, organization scoping, the assignment lifecycle,
content and quizzes, cohorts, paths, gradebook, credentials, discussions,
notifications, reporting, API permissions, session revocation, media validation
and the HTTP transport.

## Project layout

- `lms/config.py` — validated configuration and version.
- `lms/db.py` — connection handling and database entry points.
- `lms/migrations.py` — versioned, forward-only schema migrations.
- `lms/auth.py` — signed, fingerprint-bound, revocable session tokens.
- `lms/permissions.py` — role/permission catalogue.
- `lms/api.py` — versioned route table and permission checks.
- `lms/server.py` — HTTP transport, media streaming, rate limiting and headers.
- `lms/services.py` — domain rules and read models.
- `lms/content.py` — courses, modules, lessons, quizzes and progress.
- `lms/engagement.py` — cohorts, paths, gradebook, credentials and discussions.
- `lms/media.py` — streaming uploads, validation and signed URLs.
- `lms/notifications.py` — templates, outbox, inbox and delivery.
- `lms/reports.py` — compliance export.
- `lms/hris.py` — HRIS validation, diff and guarded application.
- `lms/security.py` — rate limiter, headers and structured logging.
- `lms/static/` — CSP-safe browser interface.
- `tests/` — automated unit, integration and HTTP tests.
- `docs/` — architecture, security and operational documentation.

## Production evolution

See `ROADMAP.md` for the sequenced plan and `docs/ARCHITECTURE.md` for the
migration path to PostgreSQL, OIDC/SAML, object storage, workflow orchestration,
event streaming, search, analytics, Kubernetes, SCIM and enterprise
observability.

## License

Apache License 2.0. See `LICENSE`.
