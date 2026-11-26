# PeaLMS v0.5.0 Release Notes

## Release theme

Make the v0.4.0 LMS safe to pilot with real people data: trusted identity from
an authenticating proxy, durable background work, auditable delivery attempts,
and no new runtime dependency.

## v0.4.0 highlights

- **Learning content.** Courses now contain modules and ordered lessons of type
  text, uploaded video, YouTube, link and quiz. The player tracks progress and
  completes the assignment when required lessons are done.
- **Assessment integrity.** Learners cannot self-report scores or skip required
  lessons. Explanations appear only after a passing attempt.
- **Cohorts.** Group people for targeted assignments and policies, including
  `cohort` and `cohort_name` rule attributes.
- **Learning paths.** Ordered courses with required steps, progress, and a safe
  "start next course" action. Completion of one course can advance the path.
- **Gradebook.** Assignment scores and quiz evidence are combined into a final
  grade with a letter and an explicit source.
- **Verifiable credentials.** Badges and completion certificates carry
  verification codes that can be checked from the Credentials view.
- **Discussions.** Course threads with replies, pinning, closing and moderator
  controls, available only to people who can access the course.
- **Role-native home.** Dashboards and navigation adapt to learner, instructor,
  manager, admin and auditor roles.
- **Security hardening.** Sessions are fingerprint-bound and revocable, demo
  sessions are loopback-only, Docker demo impersonation is off, uploads stream to
  disk with magic-byte checks, and media ranges are RFC-correct.
- **Strict CSP preserved.** No inline handlers, no inline styles, same-origin
  media, narrow YouTube frame origins, and zero outbound requests.

## v0.5.0 highlights

- **Trusted identity.** A reverse proxy that already performed OIDC/SAML
  authentication signs a short assertion. PeaLMS verifies its HMAC, freshness,
  replay nonce and source network, then derives the role from a server-side
  group map. No request field can supply a role.
- **Durable work.** Notification sweeps, policy reassessment and outbox
  delivery are jobs in a SQLite ledger. Claims are atomic; failures retry with
  backoff and terminal failures are retained for inspection.
- **Worker CLI.** `python -m lms.worker --once` suits cron;
  `--poll-seconds` runs a persistent worker. Multiple workers can share the
  database safely.
- **SMTP mirror.** When SMTP is configured, queued notifications are delivered
  by email after the outbox record exists. The in-app inbox remains authoritative.
- **Operations evidence.** Admins and auditors can inspect jobs, retries, job
  events and delivery attempts from the browser.
- **Pilot-safe UI.** Demo impersonation controls are hidden when the server runs
  in proxy identity mode.

## Market study applied

The roadmap was informed by Canvas module requirements and SpeedGrader-style
grading, TalentLMS/Docebo rule-based enrolment, Coursera stackable verified
credentials, Blackboard's single "+" authoring pattern, and Udemy's
next-best-action dashboards. We deliberately avoided Moodle settings sprawl,
Canvas XSS/CDN exposure, plugin marketplaces, arbitrary WYSIWYG HTML, tier
gating and passive-video-only learning.

## Upgrade notes

1. Back up the SQLite database.
2. Run `python -m lms.server --migrate-only`.
3. Start the application normally.

Migrations 4, 5 and 6 add engagement tables, session controls, and the job and
delivery ledger respectively. All are forward-only. Roll back by restoring the
backup.

If you used a demo database from v0.3.0 or v0.4.0, delete it and let v0.5.0
reseed it to see the current sample data. Production data is migrated in place.

## Known limitations

- Proxy identity is a signed-header pilot, not a full OIDC relying party with
  discovery, JWKS rotation or SAML POST binding. The reverse proxy must perform
  the real federation and be the only network source allowed to assert identity.
- Browser sessions remain bearer tokens; cookies with CSRF and origin checks are
  deferred with the multi-tenant scale increment.
- Media links are short-lived capabilities, not per-user access tokens. Do not
  put highly sensitive media behind them without an identity-aware proxy.
- Multi-process deployments need a shared secret and a shared rate limiter; the
  SQLite database must remain single-writer.
- PostgreSQL, tenant context, delegated administration and shared quota storage
  are not yet implemented.
- Offline course packs, SCORM, xAPI and LTI are not yet implemented.
- The browser UI has been checked manually but has not had an independent
  screen-reader audit or formal WCAG 2.2 AA conformance testing.

## Upgrade policy

Future releases are cumulative: preserve working capabilities, supply database
migrations, document breaking changes, and update the changelog, release notes,
security guidance and roadmap.
