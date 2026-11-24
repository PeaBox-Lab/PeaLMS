# Architecture

## Release architecture

v0.2.0 remains deliberately dependency-light: a Python standard-library HTTP
service, SQLite transactional store, and a static HTML/CSS/JavaScript client.
The difference from v0.1.0 is structure: configuration, transport, routing,
domain rules, notifications and reporting now have explicit boundaries, and the
schema is versioned.

```
browser (CSP-safe vanilla JS)
        │  JSON over HTTP, bearer session
        ▼
lms/server.py        transport: parsing, rate limit, headers, request ids
        │
        ▼
lms/api.py           versioned routes, permission checks, error mapping
        │
        ├── lms/services.py      domain rules, read models, lifecycle, org scoping
        ├── lms/hris.py          import validation, diff, guarded application
        ├── lms/notifications.py outbox, templates, inbox, delivery
        └── lms/reports.py       compliance export
        │
        ▼
lms/db.py  →  SQLite  ←  lms/migrations.py
```

Cross-cutting modules:

- `lms/config.py` — one validated `Settings` object, lazily cached.
- `lms/auth.py` — HMAC-signed session tokens with issuer, subject, issued-at,
  expiry and token id.
- `lms/permissions.py` — role → permission catalogue.
- `lms/security.py` — rate limiter, security headers, structured access logs.
- `lms/audit.py` — transactional audit event writer.

## Request lifecycle

1. `server.Handler` assigns a request id and applies the per-address rate limit.
2. It parses the body with the configured size limit and resolves the bearer
   token to an identity.
3. `api.handle` normalises `/api` to `/api/v1`, matches a route, and dispatches.
4. The handler checks permissions and calls a service.
5. Services validate state, write data and audit events in one transaction.
6. Errors are mapped: `ApiError` keeps its status, `ValueError`/`KeyError`
   become 400, unique violations become 409.
7. The transport writes JSON or CSV with security headers and the request id.

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
- `notifications` — durable outbox and inbox.
- `import_jobs` — checksum, counts and reconciliation summary.
- `audit_events` — append-only event history.
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

- Strict CSP (`script-src 'self'`, `style-src 'self'`, `frame-ancestors 'none'`).
- No inline event handlers or inline styles in the client.
- `X-Frame-Options: DENY`, `nosniff`, no-referrer, restrictive permissions
  policy, and HSTS when TLS is terminated upstream.
- Bearer tokens in the `Authorization` header, so browser requests are not
  ambient credentials and classic CSRF does not apply. If the deployment moves
  to cookies, CSRF tokens become mandatory.
- Parameterized SQL, foreign keys enabled, transaction rollback on error.
- Request body size limit and per-address rate limiting.
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

1. Federated identity and token/session hardening for pilot use.
2. Scheduled worker entry point with a durable job ledger.
3. Object storage and versioned content publishing.
4. PostgreSQL repositories and tenant context on every query.
5. Transactional outbox to an event bus and signed webhooks.
6. Permission-aware search and governed analytics.
7. Formal SLOs, backups, restore exercises and disaster recovery.
