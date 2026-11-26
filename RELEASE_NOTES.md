# PeaLMS v0.4.0 Release Notes

## Release theme

Make it like Moodle, but better: real learning content plus cohorts, paths,
grades, credentials and discussions — without plugins, trackers, WYSIWYG HTML or
a second database.

## Highlights

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

Migration 4 adds engagement tables; migration 5 adds session revocations and the
session epoch. Both are forward-only. Roll back by restoring the backup.

If you used a demo database from v0.3.0, delete it and let v0.4.0 reseed it to
see the new cohorts, paths, badges and discussions. Production data is migrated
in place.

## Known limitations

- Session tokens are still bearer tokens for a pilot; production identity must
  be OIDC/SAML with server-side scope derivation.
- Media links are short-lived capabilities, not per-user access tokens. Do not
  put highly sensitive media behind them without an identity-aware proxy.
- Multi-process deployments need a shared secret and a shared rate limiter; the
  SQLite database must remain single-writer.
- Email delivery is a structured-log transport; SMTP is still a roadmap item.
- Offline course packs, SCORM, xAPI, LTI, tenant isolation and WCAG 2.2 AA
  conformance evidence are not yet implemented.
- The browser UI has been checked manually but has not had an independent
  screen-reader audit.

## Upgrade policy

Future releases are cumulative: preserve working capabilities, supply database
migrations, document breaking changes, and update the changelog, release notes,
security guidance and roadmap.
