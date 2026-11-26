# Security and Production Readiness

## Implemented safeguards

### Authentication and authorization
- Server-side permission checks on every mutating endpoint and every read that
  exposes people data.
- Short-lived HMAC-signed sessions with issuer, subject, issued-at, expiry,
  unique token id, browser fingerprint and persisted epoch claims. Arbitrary
  role headers are never trusted.
- Tampered, expired, wrong-issuer, unknown-role, revoked and stale-epoch tokens
  are rejected. `POST /session/logout` revokes one token; `POST /session/rotate`
  invalidates all tokens.
- Demo sessions are restricted to loopback clients and are disabled in the
  shipped Docker stack; demo impersonation is never a production identity.
- Learner completion is bound to the signed employee identity; privileged
  completion requires a server-validated role. Learners cannot self-report
  scores or skip required lessons.
- Manager access is additionally checked against the recursive reporting
  subtree, not just the role name, including unfiltered assignment lists.
- Course discussions require enrollment for readers as well as posters;
  unauthorized course identifiers do not expose threads or replies.

### Data integrity
- Completed evidence cannot be silently overwritten; a second completion is a
  conflict.
- Scores are validated from 0 to 100 and compared with the course passing score.
- Exemptions require a reason and are audited.
- Assignment cycles preserve history across recertification.
- Parameterized SQL, foreign keys enabled, and rollback on any error.
- Transactional import application with checksum idempotency and anomaly
  blocking; authoritative full extracts replace stale derived roles.
- No destructive change when a dry run has blockers.
- Assignment due dates are bounded and draft curricula cannot be bypassed by
  self-completion.
- Audit events for employees, courses, policies, assignments, starts,
  completions, failures, exemptions, extensions, imports, suspensions, cohorts,
  paths, badges, awards, discussions and media uploads.
- Gradebook grades derive from assignment scores and quiz attempts; failed quiz
  attempts do not reveal explanations.
- Badge criteria are validated against known courses and paths; verification
  codes are unguessable and resolve to a signed award record without exposing
  the recipient's work email.
- Discussion posting requires course access; moderators alone may delete threads.

### Transport and browser
- Strict Content Security Policy: `default-src 'self'`, `script-src 'self'`,
  `style-src 'self'`, `media-src 'self'`, narrow YouTube frame origins,
  `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'`.
- No inline event handlers or inline `style` attributes in the client. External
  video URLs are rejected; uploaded videos play from this origin only.
- `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, no-referrer,
  restrictive permissions policy, cross-origin isolation headers.
- HSTS when TLS is terminated upstream (`X-Forwarded-Proto: https`).
- JSON body size limit plus streaming uploads validated by declared MIME type
  and magic bytes; unvalidated container formats such as MKV are rejected.
- Signed upload links expire after five minutes; lesson stream URLs are signed
  for the declared lesson duration (at least fifteen minutes) and refreshed by
  the client before expiry. Byte ranges are validated, and unsatisfiable ranges
  return 416 rather than leaking partial content.
- Per-client/identity sliding-window rate limiting; static-path traversal
  protection; request ids and structured JSON access logs.
- HTML escaping before displaying returned values; CSV exports neutralise
  formula-injection prefixes.
- Session switches cancel in-flight view and import work so stale identities
  cannot repopulate the interface; raw media/export requests re-authenticate on
  401 like JSON requests.
- Bearer tokens are not ambient credentials, which removes classic CSRF for the
  current client. Moving to cookies requires CSRF tokens.

## Required before production

### Identity
- Replace demo sessions with OIDC/SAML authentication and trusted server-side
  role and scope claims.
- If cookies replace bearer tokens: secure, HttpOnly, SameSite cookies plus CSRF
  tokens and origin checks.
- Privileged-access review, MFA, break-glass procedures and IdP-driven session
  lifecycle (the built-in revocation and epoch rotation are pilot primitives).

### Platform
- HTTPS-only operation with a managed certificate and strict transport policy.
- Move rate limiting to a shared store or the edge gateway.
- Move secrets into a managed secrets platform; never ship a fixed
  `LMS_SESSION_SECRET`.
- Encrypt databases, backups, employee extracts and object storage.
- Malware scanning and sandboxing before accepting uploaded learning packages.
- Immutable, centralized logging with retention and alerting.
- Dependency, container, static analysis, dynamic analysis and penetration
  testing.

### Privacy and governance
- Scoped tenant and organization authorization tests for every endpoint.
- Privacy retention, data-subject requests, export controls and non-production
  masking.
- Documented incident response, backup restoration and disaster recovery.
- Accessibility conformance evidence (WCAG 2.2 AA) and an accessibility
  statement.

## HRIS considerations

Employee extracts contain personal information. Production SFTP accounts must
use key authentication and least privilege. Inbound files should use controlled
directories, checksums, encryption or signatures where required, malware
scanning, retention limits and restricted operator access.

The pipeline validates required fields, controlled values, dates and manager
references, but it is not a substitute for source-system controls. Treat the
HRIS as authoritative and reconcile rather than overwrite blindly. The browser
client stores the reviewed dry-run snapshot and disables **Apply** if the file
or options change before confirmation.

## Responsible use

The sample employee records are fictional. Do not replace them with real
employee data in an uncontrolled environment. The pilot should not be exposed
directly to the public internet.
