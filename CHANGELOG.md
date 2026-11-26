# Changelog

All notable product changes are documented here.

## 0.4.0 — 2026-11-26

### Added

- Cohorts with members, management screens and cohort-based assignment policies.
- Learning paths with ordered courses, required steps, progress and enrolment.
- Gradebook combining assignment scores with quiz attempts and letter grades.
- Verifiable badges and completion certificates with public verification codes.
- Course discussions with threaded replies, pinning, closing and moderation.
- Role-native navigation and views for the new engagement surfaces.
- Schema migration 5: session revocations and a persisted session epoch.
- Session logout, epoch rotation, browser-fingerprint binding and loopback-only
  demo sessions.
- Streaming media uploads with magic-byte validation and short-lived signed URLs.
- HTTP 416 handling for unsatisfiable media byte ranges.

### Changed

- Demo mode is disabled in the shipped Docker stack.
- Rate limiting is scoped by client address and authenticated identity.
- Uploaded videos must be local files; external video URLs are blocked by CSP.
- Badge criteria are validated against known courses and paths.
- Learner options now follow the same visibility rules as API reads.
- Verify/apply HRIS snapshots are invalidated when the file or options change.
- Stale asynchronous renders and quiz loads are discarded with generation tokens,
  and session switches now cancel in-flight view loads and HRIS snapshots.
- Lesson stream URLs are signed for the lesson duration and refreshed before
  expiry so long videos can finish without a stale capability.
- Notification badge counts come from the authoritative unread count.
- Dialogs have accessible labels, scroll containment and column headers.
- Discussions and learning-path actions follow the same role rules as the API.

### Fixed

- Quiz result counts were double-incrementing correct answers.
- Gradebook queries used tuple indexing against dict rows.
- Cohort policy matching now handles negative operators across memberships.
- Failed quiz attempts no longer reveal explanations.
- Managers can start team assignments while learners cannot self-report scores.
- CSV formula-injection values are neutralised on export.
- Unsatisfiable and malformed media ranges return 416 instead of a partial 206.
- MKV uploads are rejected instead of being accepted but unvalidated.
- Manager assignment lists without an explicit filter are scoped to the
  reporting subtree instead of the whole organization.
- A supplied `status` filter can no longer expose draft courses to learners.
- Discussion listing and detail reads require course access, not just `read`.
- HRIS full extracts revoke stale derived roles and reject malformed field types.
- Assignment due dates are bounded; draft courses cannot be self-completed.
- Editing a learning path now replaces its course sequence as displayed.
- Public credential verification no longer exposes the recipient's work email.
- Failed media inserts do not leave orphaned files; 405 responses close the
  connection instead of corrupting the next keep-alive request.

### Tests

- 112 automated tests, including 37 v0.4 engagement, security and regression
  tests.

## 0.3.0 — 2026-11-25

### Added

- Course modules, ordered lessons and a lesson player.
- Lesson types for text, uploaded video, YouTube, links and quizzes.
- Question banks, attempts, pass scores and delayed explanations.
- Automatic course completion when all required lessons are complete.
- Media upload, signed URLs and byte-range streaming.
- Strict CSP extensions for same-origin media and privacy-enhanced YouTube embeds.

### Changed

- Course detail responses include modules, lessons and progress.
- The browser client became a multi-view application with authoring dialogs.

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
