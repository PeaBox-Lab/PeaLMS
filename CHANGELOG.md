# Changelog

All notable product changes are documented here.

## 0.2.0 — 2026-11-24

### Added

- Versioned schema migrations with `schema_migrations`, legacy v0.1.0 database
  adoption, and a `--migrate-only` command.
- Central validated configuration module with environment parsing, defaults and
  binding safety checks.
- Versioned API under `/api/v1`; `/api` remains as a deprecated alias.
- Recursive manager hierarchy, team-scoped dashboards and team assignment views.
- Assignment lifecycle: start, complete, fail, exempt and extend, with audit
  events for each transition.
- Certification cycles with recertification due dates and reassignment.
- Course passing scores, recertification periods and content URLs.
- Durable notification outbox, in-app inbox, templates, delivery transport and
  due/overdue/recertification sweeps with deduplication.
- Pagination and filtering for people, assignments and audit.
- Scoped compliance CSV export.
- Strict Content Security Policy, security headers, request-size limits,
  per-address rate limiting, request IDs and structured JSON access logs.
- `--sweep-notifications` operational command.
- Expanded test suite (56 tests) including HTTP transport and permission tests.

### Changed

- The browser client no longer uses inline event handlers or inline styles.
- Manager dashboards and assignment reads are scoped to the reporting subtree.
- Demo session tokens now include issuer, issued-at and unique token id claims.
- Course seeds include certification periods and passing scores.
- HRIS analysis validates dates and manager references and reports warnings.
- Manager role derivation now considers the existing active population, not
  only rows present in the current file.

### Fixed

- Personal dashboard binding count mismatch introduced by the new metrics.
- Assignment audit entity identifiers remain stable across cycles; cycle is
  recorded in event details.
- Static file 404 responses now report the correct status.

## 0.1.0 — 2026-11-16

### Added

- Employee, role and organization data model.
- Course, assignment policy and enrollment data model.
- Learner, people, course, policy, HRIS and audit interfaces.
- Dashboard metrics and basic visual summaries.
- HRIS CSV validation, normalization, dry run, checksums, create/update/suspend,
  role derivation and policy assignment.
- Import job reconciliation and audit events.
- Sample employee, course, policy and assignment data.
- Automated unit/integration tests.
- Architecture, security, release and roadmap documentation.
- Container and local run support.
