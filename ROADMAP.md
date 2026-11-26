# Product Roadmap

This roadmap is a living plan. Each release is cumulative: existing behaviour is
preserved, schema changes ship with migrations, and every release updates the
changelog, release notes, security guidance and this document.

## How to read this roadmap

- **Shipped** means the capability exists in the tagged release.
- **Next** is the committed scope for the following release.
- **Candidate** is directionally agreed but may move.
- Priorities are ordered by *risk reduction first, learner value second, scale
  third*. A capability that protects people data or makes compliance evidence
  trustworthy outranks a feature that only looks good in a demo.

## Current state — v0.5.0

- Enterprise controls from v0.2.0: migrations, manager scoping, assignment
  lifecycle, certification cycles, notifications, reporting and HRIS import.
- Learning delivery from v0.3.0: modules, lessons, uploaded video, YouTube,
  links, quizzes, attempts and automatic completion.
- Engagement from v0.4.0: cohorts, learning paths, gradebook, verifiable badges
  and certificates, course discussions and role-native views.
- Pilot foundation from v0.5.0: signed proxy identity, durable job ledger and
  worker, SMTP outbox delivery with dead-letter evidence and an operations view.
- Session revocation, fingerprint binding, loopback-only demo sessions,
  streaming media validation and RFC-correct media ranges.
- Strict CSP with no inline handlers or styles and no third-party runtime
  dependencies.
- 138 automated tests.

## Market study — what we copy and what we refuse

Reviewed against Moodle, Canvas, TalentLMS, Docebo, Coursera, Blackboard and
Udemy using 2026 sources.

### Copy

- Canvas: explicit module requirements and a SpeedGrader-style evidence view.
- TalentLMS/Docebo: rule-based enrolment with cohort/attribute policies.
- Coursera: stackable, verifiable credentials tied to real completion evidence.
- Blackboard: one "+" authoring entry point instead of scattered tools.
- Udemy: a role-native next-best-action dashboard instead of a generic admin grid.

### Refuse

- Moodle settings sprawl and plugin marketplaces as the default extension model.
- Canvas-style arbitrary HTML/WYSIWYG and third-party CDN scripts under CSP.
- Tier gating that hides basic learning from smaller organisations.
- Passive-video-only completion with no retrieval practice or evidence.
- Surveillance-style productivity scoring.

## Ten differentiators for a better Moodle

1. **Strict CSP by construction.** No inline handlers, inline styles or CDN code.
2. **One SQLite/file codebase.** Self-hostable without a service mesh.
3. **Git-friendly course packages.** Modules and lessons are structured data,
   ready to export as declarative packs.
4. **Replayable gradebook.** Grades derive from assignments and attempts with an
   audit trail, not self-reported scores.
5. **Mastery paths and spaced retrieval.** Planned next increment beyond simple
   ordered paths.
6. **Author-time accessibility gate.** Planned validation before publish, not a
   retrofitted checker.
7. **Role-native home.** Learner, instructor, manager, admin and auditor see
   different, purposeful views.
8. **Privacy-first zero outbound network.** YouTube is opt-in and framed from a
   privacy-enhanced origin; everything else stays local.
9. **Offline static course packs with signed sync.** Planned for low-connectivity
   learners.
10. **Simple roles and tenant isolation with CSS-only branding.** No plugin
    architecture required to customise the experience.

## v0.5.0 — Enterprise identity and scale (shipped pilot slice)

Goal: make v0.4.0 safe to pilot with real people data and multiple teams.

### Shipped in v0.5.0

- **Signed proxy identity pilot.** An authenticating reverse proxy can assert a
  short HMAC-signed identity. PeaLMS verifies trusted source networks, freshness,
  replay nonces and a server-side group-to-role map. No endpoint accepts a
  client-supplied role; demo impersonation controls are hidden in proxy mode.
- **Scheduled worker.** Durable job ledger with atomic claims, bounded retries,
  backoff, event history and manual retry of failed jobs. `python -m lms.worker`
  supports cron or a long-running process.
- **SMTP outbox delivery.** Optional SMTP mirror with per-attempt evidence and
  dead-lettering after the retry budget. The in-app inbox remains authoritative.
- **Operations evidence.** Admins and auditors can inspect jobs, retries, job
  events and delivery attempts from one view.

### Carried forward to later increments

- **Full OIDC/SAML relying party.** Provider discovery, key rotation, logout and
  MFA remain the identity provider's responsibility in this signed-header pilot.
- **Cookie/session hardening** if browser sessions replace bearer tokens:
  secure, HttpOnly, SameSite cookies, CSRF tokens, origin checks and refresh
  rotation.
- **PostgreSQL repository abstraction.** Connection pooling, read replicas and
  point-in-time recovery while keeping SQLite for local development.
- **Tenant and legal-entity context.** Tenant id on every entity, query, cache
  key, export and audit event; cross-tenant access tests.
- **Delegated administration.** Scoped admin roles per business unit, department
  or manager subtree with an explicit grant/revoke audit trail.
- **Shared rate limiting and quotas** for multi-replica deployments.
- **Accessibility regression pass.** Automated checks plus a manual keyboard and
  screen-reader checklist for every view.

## v0.6.0 — Skills, mastery and offline (candidate)

Goal: connect learning to capability and support learners without reliable
network access.

- Skills taxonomy, proficiency levels and learning-to-skill mapping.
- Gap analysis per role and team, with explainable recommendations.
- Mastery paths with spaced retrieval and re-assessment rules.
- Author-time accessibility gate: alternative text, transcript and contrast
  checks before publishing.
- Offline static course packs with signed sync and conflict-safe progress merge.
- Hash-chained gradebook audit export with replay verification.
- Manager coaching insights grounded in completion and skill evidence, not
  surveillance metrics.

## v0.7.0 — Ecosystem and interoperability (candidate)

Goal: fit into the surrounding enterprise stack without becoming it.

- SCORM 1.2/2004 launch and attempt tracking in a sandboxed origin.
- xAPI statements and an LRS bridge.
- LTI 1.3 tool launch and deep linking.
- HRIS connectors beyond CSV: Workday, SuccessFactors and generic SCIM 2.0.
- Virtual classroom connectors with attendance sync.
- Report builder and governed BI datasets.
- Service accounts and API keys with scoped permissions and rotation.
- Localization with externalized strings and right-to-left layout.

## v1.0.0 — Production enterprise release

Goal: certifiable, supportable and resilient.

- High availability, disaster recovery and published service objectives.
- WCAG 2.2 AA conformance evidence and an accessibility statement.
- Security and privacy certification readiness (ISO 27001 / SOC 2 evidence).
- Global localization and data-residency options.
- Audited upgrade, backup, restore and support processes.
- Documented deprecation policy and long-term support window.

## Beyond 1.0 — Governed AI

AI is optional, human-reviewed and gated on data governance.

- Course and assessment drafting with reviewer approval and provenance.
- Grounded conversational discovery over approved content only.
- Administrator assistance for exception triage and safe draft generation.
- Skills inference and risk analytics only after privacy review, bias testing
  and explicit opt-in.

## Sequencing and dependencies

1. Identity and transport hardening (v0.5.0 pilot slice, then the full federated
   relying party) must precede real employee data.
2. Tenant context must land before delegated administration and cross-entity
   reporting.
3. Content packages and access controls precede offline sync and SCORM.
4. Search, events and analytics depend on a stable domain model and audit
   contract.
5. Formal conformance evidence comes after the accessibility and operational
   gates are enforced in CI.

## Explicit non-goals

- Building a general-purpose video conferencing or authoring suite.
- Replacing the HRIS as the system of record for employment attributes.
- Surveillance-style productivity scoring of learners.
- Shipping AI features that cannot explain their sources or be turned off.
