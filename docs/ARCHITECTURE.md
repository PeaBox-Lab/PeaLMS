# Architecture

## Release architecture

v0.1.0 is deliberately dependency-light: a Python standard-library HTTP service, SQLite transactional store, and static HTML/CSS/JavaScript experience. It is suitable for local evaluation, product validation, demos, and a controlled pilot where the operating team accepts the documented limitations.

The application retains clear domain boundaries:

- identity and organization;
- catalog and content metadata;
- assignment policies and enrollments;
- completion evidence;
- HRIS integration and reconciliation;
- audit events;
- reporting read models.

## Production target

The product blueprint's enterprise target should evolve the MVP as follows:

| MVP component | Production target |
|---|---|
| Python built-in server | Supported application framework behind an API gateway |
| SQLite | PostgreSQL with high availability and point-in-time recovery |
| Local files | S3-compatible object storage with malware scanning and signed access |
| Demo role header | OIDC/SAML federation and server-side claims using Keycloak or the enterprise identity provider |
| Synchronous assignment updates | Workflow orchestration and asynchronous workers |
| In-process actions | Kafka-compatible event backbone with transactional outbox |
| Basic table lookup | OpenSearch-backed discovery and filtering |
| Operational SQL metrics | Governed analytics store and semantic metrics layer |
| Local execution | Containers, Kubernetes, GitOps, secrets management and autoscaling |
| Console logs | OpenTelemetry traces, Prometheus metrics and centralized logs |

## Data ownership

- HRIS remains authoritative for workforce identity and employment attributes.
- The identity provider remains authoritative for authentication.
- The LMS is authoritative for learning definitions, assignments, progress, evidence, completions and credentials.
- Audit events preserve why and how material changes occurred.

## HRIS pipeline

The pipeline follows: receipt → checksum → schema validation → normalization → identity resolution → diff → anomaly controls → role derivation → transactional apply → policy evaluation → reconciliation → audit.

Full extracts may suspend active accounts missing from the incoming population, but only after the configured population-change gate succeeds. Replayed files are detected by checksum.

## API surface

- `GET /api/health`
- `POST /api/session` (demo mode only)
- `GET /api/dashboard`
- `GET /api/me`
- `GET/POST /api/employees`
- `GET/POST /api/courses`
- `GET/POST /api/policies`
- `POST /api/policies/apply`
- `GET/POST /api/assignments`
- `POST /api/assignments/complete`
- `POST /api/hris/analyze`
- `POST /api/hris/apply`
- `GET /api/import-jobs`
- `GET /api/audit`

## Next architectural increments

1. Replace demo authorization with trusted identity federation.
2. Move persistence to PostgreSQL and introduce schema migrations.
3. Separate background jobs for imports, assignments, reports and notifications.
4. Add content storage, SCORM runtime support and versioned publishing workflow.
5. Introduce signed webhooks, integration service accounts and event schemas.
6. Add tenant context to every entity, query, cache key and audit event.
7. Add formal SLOs, backups, restore exercises and disaster recovery.
