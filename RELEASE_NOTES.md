# Corporate LMS v0.1.0 Release Notes

## Release theme

Trusted corporate learning foundation with guarded HRIS provisioning.

## Highlights

- Runnable learner and administration web experience.
- Employee and organization directory with derived roles.
- Course catalog, assignment policies, enrollments and completion tracking.
- Management dashboard and audit evidence browser.
- HRIS employee extract dry run, validation, anomaly protection, idempotent application and reconciliation.
- Sample data, sample employee extract, automated tests, container definitions and implementation documentation.

## Known limitations

- Signed demo sessions are disabled by default and are not production identity federation. They must be explicitly enabled for local evaluation.
- SQLite is intended for evaluation and controlled pilots, not global scale.
- Course content delivery is metadata-only; SCORM runtime and object storage are roadmap items.
- Notifications, recurring certification, e-signatures, observation checklists and custom reporting are not yet implemented.
- The browser UI is responsive and keyboard-oriented but has not completed an independent WCAG conformance audit.

## Upgrade policy

Future releases should be cumulative: preserve working capabilities, supply database migrations, document breaking changes, and update the changelog, release notes, security guidance and roadmap.
