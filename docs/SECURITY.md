# Security and Production Readiness

## Implemented safeguards

- Server-side permission checks for management, HRIS, completion and audit endpoints.
- Short-lived HMAC-signed sessions in explicit demo mode; arbitrary role headers are not trusted.
- Learner completion is bound to the signed employee identity, while privileged completion requires a server-validated role.
- Completed evidence cannot be silently overwritten, and scores are validated from 0 to 100.
- Request-size limit for JSON payloads.
- Parameterized SQL and database foreign keys.
- Transactional import application and rollback on errors.
- Import checksum/idempotency and anomaly blocking.
- No destructive change when the dry run has blockers.
- Audit events for employees, courses, policies, assignments, completions, imports and suspensions.
- Static-path traversal protection and `nosniff` response headers.
- HTML escaping in the browser before displaying returned values.

## Required before production

- Replace demo role headers with OIDC/SAML authentication and trusted server-side role/scope claims.
- Add CSRF protection, secure cookies, strict Content Security Policy, HTTPS-only operation, rate limiting and security headers.
- Move secrets out of configuration and into a managed secrets platform.
- Encrypt databases, backups, employee extracts and object storage.
- Add malware scanning and sandboxing before supporting uploaded learning packages.
- Implement scoped tenant and organization authorization tests for every endpoint.
- Add privileged-access review, MFA, break-glass procedures and immutable centralized logging.
- Add privacy retention, data-subject requests, export controls and non-production masking.
- Run dependency, container, static analysis, dynamic analysis and penetration testing.
- Document incident response, backup restoration and disaster recovery.

## HRIS considerations

Employee extracts contain personal information. Production SFTP accounts must use key authentication and least privilege. Inbound files should use controlled directories, checksums, encryption or signatures where required, malware scanning, retention limits and restricted operator access.

## Responsible use

The sample employee records are fictional. Do not replace them with real employee data in an uncontrolled environment. The MVP should not be exposed directly to the public internet.
