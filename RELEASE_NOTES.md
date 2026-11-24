# Corporate LMS v0.2.0 Release Notes

## Release theme

Enterprise controls on a dependable foundation: scoped authority, auditable
learning lifecycle, durable notifications and a hardened HTTP surface.

## Highlights

- **Manager hierarchy.** Team dashboards and assignment access follow the
  recursive reporting line. A manager sees their subtree, never the whole
  organization, unless their role grants it.
- **Full assignment lifecycle.** Start, complete, fail, exempt and extend. Each
  transition is audited. Completion evidence cannot be overwritten.
- **Certification cycles.** Courses can define a passing score and a
  recertification period. Completing a course schedules recertification, and
  reassignment opens a new cycle without losing history.
- **Notification outbox.** Assignment, due-soon, overdue, completion,
  recertification and exemption messages are persisted, rendered from templates,
  shown in an in-app inbox, and delivered through a pluggable transport.
- **Schema migrations.** Existing v0.1.0 databases are adopted and upgraded in
  place. Fresh installs start at the current schema version.
- **Versioned API.** `/api/v1` is canonical. `/api` keeps working and advertises
  its successor.
- **Security hardening.** Strict CSP with no inline handlers or styles, frame
  denial, nosniff, referrer and permissions policies, request-size limits,
  per-address rate limiting and request IDs in structured logs.
- **Reporting.** Compliance by department and a permission-scoped CSV export.

## Upgrade notes

1. Back up the SQLite database.
2. Run `python -m lms.server --migrate-only`.
3. Start the application normally.

The migration adds course certification columns, rebuilds the assignment table
to add cycles and lifecycle fields, and creates the notification outbox. It is
forward-only; roll back by restoring the backup.

## Known limitations

- Signed demo sessions are disabled by default and are not production identity
  federation. They must be explicitly enabled for local evaluation.
- Demo sessions are bearer tokens held in memory by the browser client. Do not
  expose demo mode to untrusted networks.
- Rate limiting is in-process; a multi-replica deployment needs a shared store.
- SQLite is intended for evaluation and controlled pilots, not global scale.
- Course content delivery is metadata-only; SCORM runtime, assessments and
  object storage are roadmap items.
- Email delivery is a structured-log transport by default; SMTP is a v0.2.x
  roadmap item.
- The browser UI is responsive and keyboard-oriented but has not completed an
  independent WCAG conformance audit.
- Multi-tenancy, SCIM, xAPI and LTI are not implemented.

## Upgrade policy

Future releases are cumulative: preserve working capabilities, supply database
migrations, document breaking changes, and update the changelog, release notes,
security guidance and roadmap.
