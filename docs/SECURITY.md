# Security and Production Readiness

## Implemented safeguards

### Authentication and authorization
- Server-side permission checks on every mutating endpoint and every read that
  exposes people data.
- Short-lived HMAC-signed sessions with issuer, subject, issued-at, expiry and
  unique token id claims. Arbitrary role headers are never trusted.
- Tampered, expired, wrong-issuer and unknown-role tokens are rejected.
- Learner completion is bound to the signed employee identity; privileged
  completion requires a server-validated role.
- Manager access is additionally checked against the recursive reporting
  subtree, not just the role name.

### Data integrity
- Completed evidence cannot be silently overwritten; a second completion is a
  conflict.
- Scores are validated from 0 to 100 and compared with the course passing score.
- Exemptions require a reason and are audited.
- Assignment cycles preserve history across recertification.
- Parameterized SQL, foreign keys enabled, and rollback on any error.
- Transactional import application with checksum idempotency and anomaly
  blocking.
- No destructive change when a dry run has blockers.
- Audit events for employees, courses, policies, assignments, starts,
  completions, failures, exemptions, extensions, imports and suspensions.

### Transport and browser
- Strict Content Security Policy: `default-src 'self'`, `script-src 'self'`,
  `style-src 'self'`, `object-src 'none'`, `base-uri 'none'`,
  `frame-ancestors 'none'`.
- No inline event handlers or inline `style` attributes in the client.
- `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, no-referrer,
  restrictive permissions policy, cross-origin isolation headers.
- HSTS when TLS is terminated upstream (`X-Forwarded-Proto: https`).
- Request body size limit and per-address sliding-window rate limiting.
- Static-path traversal protection.
- Request ids and structured JSON access logs.
- HTML escaping before displaying returned values.
- Bearer tokens are not ambient credentials, which removes classic CSRF for the
  current client. Moving to cookies requires CSRF tokens.

## Required before production

### Identity
- Replace demo sessions with OIDC/SAML authentication and trusted server-side
  role and scope claims.
- If cookies replace bearer tokens: secure, HttpOnly, SameSite cookies plus CSRF
  tokens and origin checks.
- Privileged-access review, MFA, break-glass procedures and session revocation.

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
HRIS as authoritative and reconcile rather than overwrite blindly.

## Responsible use

The sample employee records are fictional. Do not replace them with real
employee data in an uncontrolled environment. The pilot should not be exposed
directly to the public internet.
