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

## Current state — v0.2.0

v0.2.0 turns the v0.1.0 MVP into a coherent enterprise-controls foundation:

- Versioned schema migrations with legacy database adoption.
- Central validated configuration and structured access logs with request IDs.
- Versioned API (`/api/v1`) with a deprecated `/api` alias.
- Recursive manager hierarchy and team-scoped authorization and dashboards.
- Assignment lifecycle: start, complete, fail, exempt, extend, recertify.
- Certification cycles (`cycle`) with recertification due dates.
- Durable notification outbox with templates, in-app inbox and delivery sweep.
- Pagination, filtering and scoped CSV compliance export.
- Strict Content Security Policy, security headers, rate limiting and a
  CSP-safe browser client with no inline handlers or styles.
- 56 automated tests covering migrations, permissions, lifecycle, notifications,
  reporting and the HTTP transport.

## Guiding principles

1. **Fail closed.** Invalid imports, ambiguous identity, and missing evidence
   block the operation instead of silently degrading.
2. **Evidence over assertion.** Material changes produce an audit event, and
   completion evidence cannot be overwritten.
3. **Least privilege by default.** New endpoints start with an explicit
   permission and a scoping rule, never an implicit "authenticated means allowed".
4. **Boring, reversible infrastructure.** Small dependency surface, forward-only
   migrations, documented rollback by restore.
5. **Accessible and fast by default.** Keyboard operable, screen-reader sane,
   responsive, and no client framework required for the core experience.

## v0.2.x — Stabilization (next)

Goal: make v0.2.0 safe to pilot with real people data.

- **OIDC/SAML pilot integration.** Replace demo sessions behind a feature flag;
  map provider groups to roles; keep server-side scope derivation. Acceptance:
  no endpoint accepts a client-supplied role.
- **CSRF and cookie hardening** if browser sessions replace bearer tokens;
  short-lived access tokens plus refresh rotation if bearer stays.
- **Import mapping profiles.** Named column mappings, per-profile validation
  rules, and a saved profile selector in the HRIS screen.
- **Scheduled jobs.** A single worker entry point for import pickup, due-date
  sweeps, recertification sweeps and outbox delivery, with a job ledger.
- **Email transport.** SMTP adapter for the notification outbox, with retry and
  dead-letter handling; keep the in-app inbox as the source of truth.
- **Accessibility regression pass.** Automated axe checks in CI plus a manual
  keyboard and screen-reader checklist for every view.
- **Operational runbook.** Backup/restore rehearsal, retention policy, and
  on-call triage for failed imports and notification backlogs.
- **API contract tests.** Freeze the v1 response shapes and reject accidental
  breaking changes.

## v0.3.0 — Learning delivery and assessment

Goal: move from course metadata to actual learning experiences.

- **Content model.** Versioned course versions, modules, lessons, assets and a
  publishing workflow (draft → review → published → retired).
- **Object storage adapter.** S3-compatible storage with signed URLs, checksum
  verification and malware-scanning hook.
- **SCORM 1.2 / 2004 runtime.** Launch, attempt tracking, completion and score
  ingestion, with a sandboxed player origin.
- **Assessments.** Question banks, attempts, time limits, randomized order,
  passing rules and item-level analytics.
- **Certificates and credentials.** PDF generation, verification identifiers,
  expiry and revocation.
- **Instructor-led training.** Sessions, capacity, waitlists, attendance,
  calendars and instructor scheduling.
- **Observation checklists.** Evidence attachments and assessor sign-off for
  practical competencies.
- **Learning paths.** Ordered collections with prerequisites and completion
  rules.

## v0.4.0 — Enterprise identity and scale

Goal: operate multi-team, multi-region deployments without bespoke engineering.

- **PostgreSQL persistence.** Repository abstraction, connection pooling,
  read replicas and point-in-time recovery.
- **Tenant and legal-entity context.** Tenant id on every entity, query, cache
  key, export and audit event; cross-tenant access tests.
- **Delegated administration.** Scoped admin roles per business unit, department
  or manager subtree, with an explicit grant/revoke audit trail.
- **Workflow orchestration.** Approval chains for exemptions, content
  publishing, and bulk assignment, with escalation and SLA timers.
- **Event backbone.** Transactional outbox to a Kafka-compatible bus; signed
  webhooks with replay protection.
- **Search.** Indexed discovery for people, courses and audit with permission-
  aware filtering.
- **Observability.** OpenTelemetry traces, Prometheus metrics, SLOs, and
  centralized log retention.
- **Rate limiting and quotas** moved to a shared store for multi-replica
  deployments.

## v0.5.0 — Skills and experience

Goal: connect learning to capability and career.

- **Skills taxonomy.** Skill graph, proficiency levels, role profiles and
  learning-to-skill mapping.
- **Gap analysis.** Required vs. demonstrated skills per role and team.
- **Personalized paths and recommendations** with explainable rationale.
- **Communities and social learning.** Cohorts, discussions, peer recognition.
- **Mobile and offline.** Progressive web app, offline completion capture and
  conflict-safe sync.
- **Digital badges and portable credentials** (Open Badges compatible).
- **Manager coaching insights** grounded in completion and skill evidence, not
  surveillance metrics.

## v0.6.0 — Ecosystem and interoperability

Goal: fit into the surrounding enterprise stack.

- **xAPI / LRS** statements for rich learning activity.
- **LTI 1.3** tool launch and deep linking.
- **HRIS connectors** beyond CSV: Workday, SuccessFactors and generic SCIM 2.0.
- **Virtual classroom connectors** (Teams, Zoom, Webex) with attendance sync.
- **Report builder and BI datasets.** Governed semantic metrics layer with
  scheduled exports.
- **Service accounts and API keys** with scoped permissions and rotation.
- **Localization.** Externalized strings, locale-aware dates and right-to-left
  layout.

## v1.0.0 — Production enterprise release

Goal: certifiable, supportable, and resilient.

- High availability, disaster recovery, and published service objectives.
- WCAG 2.2 AA conformance evidence and an accessibility statement.
- Security and privacy certification readiness (ISO 27001 / SOC 2 evidence).
- Global localization and data-residency options.
- Audited upgrade, backup, restore and support processes.
- Documented deprecation policy and long-term support window.

## Beyond 1.0 — Governed AI

AI is optional, human-reviewed, and gated on data governance.

- Course and assessment drafting with reviewer approval and provenance.
- Grounded conversational discovery and tutoring over approved content only.
- Administrator assistance for exception triage and safe draft generation.
- Skills inference and risk analytics only after privacy review, bias testing
  and explicit opt-in.

## Sequencing and dependencies

1. Identity and transport hardening (v0.2.x) must precede any real employee data.
2. Notifications and scheduled jobs are prerequisites for escalations, reminders
   and certification workflows.
3. Content storage and the SCORM runtime are prerequisites for assessments,
   certificates and learning paths.
4. Tenant context must land before delegated administration and cross-entity
   reporting.
5. Search, events and analytics depend on a stable domain model and audit
   contract.

## Explicit non-goals

- Building a general-purpose video conferencing or authoring suite.
- Replacing the HRIS as the system of record for employment attributes.
- Surveillance-style productivity scoring of learners.
- Shipping AI features that cannot explain their sources or be turned off.
