# Architecture

## Release architecture

v0.4.0 remains deliberately dependency-light: a Python standard-library HTTP
service, SQLite transactional store, and a static HTML/CSS/JavaScript client.
Configuration, transport, routing, content, engagement, media, notifications and
reporting have explicit boundaries, and the schema is versioned through v5.

```
browser (CSP-safe vanilla JS)
        │  JSON over HTTP, bearer session
        ▼
lms/server.py        transport: parsing, rate limit, headers, request ids
        │
        ▼
lms/api.py           versioned routes, permission checks, error mapping
        │
        ├── lms/services.py      org rules, assignments, policies, HRIS-facing rules
        ├── lms/content.py       modules, lessons, quizzes, progress, completion
        ├── lms/engagement.py    cohorts, paths, gradebook, credentials, discussions
        ├── lms/media.py         streaming uploads, validation, signed URLs
        ├── lms/hris.py          import validation, diff, guarded application
        ├── lms/notifications.py outbox, templates, inbox, delivery
        └── lms/reports.py       compliance export
        │
        ▼
lms/db.py  →  SQLite  ←  lms/migrations.py
```

Cross-cutting modules:

- `lms/config.py` — one validated `Settings` object, lazily cached.
- `lms/auth.py` — fingerprint-bound HMAC tokens with revocation and epoch rotation.
- `lms/permissions.py` — role → permission catalogue.
- `lms/security.py` — rate limiter, security headers, structured access logs.
- `lms/audit.py` — transactional audit event writer.
- `lms/content.py` and `lms/engagement.py` — learning and engagement domains.

## Request lifecycle

1. `server.Handler` assigns a request id and resolves the bearer token to an
   identity, including fingerprint and revocation checks.
2. It applies a rate limit keyed by client address and identity. Media uploads
   stream to a spooled temporary file instead of being buffered in memory.
3. `api.handle` normalises `/api` to `/api/v1`, matches a route, and dispatches.
4. The handler checks permissions and calls a domain service.
5. Services validate state, write data and audit events in one transaction.
6. Errors are mapped: `ApiError` keeps its status, `ValueError`/`KeyError`
   become 400, unique violations become 409, bad media ranges become 416.
7. The transport writes JSON, CSV or streamed media with security headers and
   the request id.

## Data model

Core entities:

- `employees` — HRIS-authoritative identity and organization attributes.
- `employee_roles` — role and scope assignments, with provenance (`seed`,
  `hris`, `system`).
- `courses` — catalog metadata, version, mandatory flag, passing score,
  recertification period, content URL.
- `assignment_policies` — attribute/operator/value rules with a due window.
- `assignments` — per employee, course and **cycle**; status, attempts,
  exemption, score, completion, recertification due date and source.
- `course_modules`, `lessons`, `quiz_questions`, `quiz_attempts`,
  `lesson_progress`, `media` — learning content and evidence.
- `cohorts`, `cohort_members`, `learning_paths`, `learning_path_courses` —
  grouping and sequencing.
- `badges`, `badge_awards` — criteria and verifiable credentials.
- `discussions`, `discussion_posts` — threaded course conversations.
- `notifications` — durable outbox and inbox.
- `import_jobs` — checksum, counts and reconciliation summary.
- `audit_events` — append-only event history.
- `session_revocations`, `app_state` — token revocation and session epoch.
- `schema_migrations` — applied migration ledger.

### Assignment state machine

```
assigned ──start──▶ in_progress ──complete(pass)──▶ completed
   │                    │
   │                    └──complete(fail)──▶ failed ──complete(pass)──▶ completed
   ├──exempt──▶ exempted
   └──complete(pass)──▶ completed
completed ──reassign──▶ assigned (cycle + 1)
```

Rules:

- Completion is rejected for `completed` and `exempted` assignments.
- A score below the course passing score records a `failed` attempt and an audit
  event, but does not schedule recertification.
- Completion of a course with `recertification_days > 0` sets
  `recert_due_date`.
- Reassignment is only allowed when the latest cycle is closed.
- Audit entity ids stay stable (`employee:course`); the cycle is recorded in the
  event details.

## Learning and engagement flow

`content.py` owns modules, lessons, quiz attempts and progress. Passing a
required quiz marks the lesson complete; when every required lesson is complete,
`_maybe_complete()` calls `services.complete_assignment()` so the canonical
assignment lifecycle, notifications and audit events stay in one place.
Learners cannot call completion directly, and failed quizzes do not expose
explanations.

`engagement.on_course_completed()` runs from that same completion hook. It:

1. awards course badges whose criteria match the completed course;
2. recomputes learning paths and assigns the next required course; and
3. awards path certificates once every required course is done.

Gradebook reads are derived: assignment scores take precedence, otherwise quiz
attempts are averaged. Credentials carry a verification code that resolves
without authentication, but the award record itself is only writable through
privileged endpoints. Discussion posting checks course enrolment or a management
role; moderation is separate from authorship.

## Organization scoping

`manager_subtree` uses a recursive CTE over `manager_employee_id`. Authorization
combines the role permission with a relationship check:

- `manage_people` / `complete_any` — organization-wide.
- `read_team` / `complete_team` — only employees in the caller's subtree.
- Otherwise — self only.

The dashboard endpoint chooses a scope explicitly: `personal`, `team`, or
`organization`, so the client never infers authority from data.

## Migration strategy

`lms/migrations.py` holds an ordered tuple of immutable migrations. On start:

1. Create `schema_migrations` if needed.
2. If the ledger is empty but `employees` exists, adopt the database as v1.
3. Apply each pending migration in its own explicit transaction.
4. Record the version only after all statements succeed.

Migrations must be append-only and forward-only. Rolling back means restoring a
backup; the release notes state this explicitly.

## Notifications

Business transactions write notifications to the outbox in the same database
transaction. This guarantees that an assignment and its notification cannot
diverge. Rendering uses `string.Template` with safe substitution. Delivery is a
separate step (`deliver`) so a transport failure cannot roll back a business
operation. Sweeps deduplicate per assignment cycle by checking
`(template, entity_id)`.

## Security posture

- Strict CSP (`script-src 'self'`, `style-src 'self'`, `media-src 'self'`, narrow
  YouTube frame origins, `frame-ancestors 'none'`).
- No inline event handlers or inline styles in the client; external video URLs
  are rejected rather than silently blocked.
- `X-Frame-Options: DENY`, `nosniff`, no-referrer, restrictive permissions
  policy, and HSTS when TLS is terminated upstream.
- Fingerprint-bound bearer tokens in the `Authorization` header, with revocation
  and epoch rotation. Browser requests are not ambient credentials, so classic
  CSRF does not apply; cookies would require CSRF tokens.
- Parameterized SQL, foreign keys enabled, transaction rollback on error.
- Streaming uploads with MIME/magic-byte validation; short-lived signed media
  URLs with lesson-aware lifetimes, client refresh and safe range semantics.
- JSON body limit plus per-client/identity rate limiting.
- Structured access logs with request ids; no secrets or personal payloads are
  logged.

See `docs/SECURITY.md` for the remaining production requirements.

## Production target

| Pilot component | Production target |
|---|---|
| Python built-in server | Supported application framework behind an API gateway |
| SQLite | PostgreSQL with high availability and point-in-time recovery |
| In-process rate limiter | Shared limiter (Redis or gateway) |
| Demo bearer sessions | OIDC/SAML federation with server-side claims |
| Synchronous assignment updates | Workflow orchestration and asynchronous workers |
| In-process sweeps | Scheduled worker with a job ledger |
| Log-only notification transport | SMTP/chat transport with retry and dead letters |
| Basic table lookup | Permission-aware search service |
| Operational SQL metrics | Governed analytics store and semantic metrics layer |
| Local execution | Containers, Kubernetes, GitOps, secrets management, autoscaling |
| Console logs | OpenTelemetry traces, Prometheus metrics, centralized logs |

## Data ownership

- HRIS remains authoritative for workforce identity and employment attributes.
- The identity provider remains authoritative for authentication.
- The LMS is authoritative for learning definitions, assignments, progress,
  evidence, completions and credentials.
- Audit events preserve why and how material changes occurred.

## HRIS pipeline

receipt → checksum → schema validation → normalization → identity resolution →
diff → anomaly controls → manager/role derivation → transactional apply →
policy evaluation → notification queueing → reconciliation → audit.

Full extracts may suspend active accounts missing from the incoming population,
but only after the population-change gate succeeds. Replayed files are detected
by checksum. Unknown manager references and date inconsistencies are surfaced as
warnings or row errors respectively.

## Next architectural increments

1. Federated identity and shared session/rate-limit infrastructure for pilots.
2. PostgreSQL repositories and tenant context on every query.
3. Scheduled worker entry point with a durable job ledger.
4. Declarative course packages and offline signed sync.
5. Transactional outbox to an event bus and signed webhooks.
6. Permission-aware search and governed analytics.
7. Formal SLOs, backups, restore exercises and disaster recovery.
