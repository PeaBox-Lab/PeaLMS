# Corporate LMS v0.1.0

A runnable corporate learning management system MVP derived from the Corporate LMS Product Blueprint.

## What works

- Workforce dashboard with active employees, courses, assignments, completion rate, overdue counts, department distribution, and assignment status.
- Employee directory with organizational attributes and derived roles.
- Course catalog and course creation.
- Attribute-based assignment policies with due dates.
- Learner assignment view and completion recording.
- Immutable-style audit event history for key actions.
- HRIS CSV dry run and guarded application:
  - required-field and controlled-value validation;
  - duplicate employee detection;
  - active-population anomaly threshold;
  - idempotent file checksums;
  - create, update, suspend, and rehire-compatible account handling;
  - learner, manager, and instructor role derivation;
  - policy reassessment after import;
  - import job evidence and reconciliation summary.
- Responsive, keyboard-operable web interface.
- Zero third-party runtime dependencies for the MVP.

## Quick start

Run directly from the extracted release:

```bash
LMS_DEMO_MODE=true python -m lms.server
```

Demo mode is disabled by default and may bind only to loopback unless remote demo access is explicitly allowed. Or install it into a Python environment:

```bash
python -m pip install .
LMS_DEMO_MODE=true corporate-lms
```

Open `http://127.0.0.1:8080`.

The application initializes `~/.corporate-lms/lms.db` by default and adds representative sample employees, courses, policies, assignments, and audit events on first run. Override the location with `LMS_DB_PATH`.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Use the HRIS import

1. Open **HRIS import**.
2. Select `samples/hris_employee_extract.csv`.
3. Choose **Full extract** and analyze it.
4. Review creates, updates, suspensions, rejected rows, and blockers.
5. Apply only when the dry run reports **Ready to apply**.

A full extract treats active employees missing from the file as suspension candidates. Population-change safeguards prevent a small or corrupt file from causing a mass suspension.

## Demo authorization

The interface has a **View as** selector. In the default demo mode, the server issues short-lived signed sessions for the selected demo identity. Direct role headers are not trusted. This remains demonstration authentication: production deployment must set `LMS_DEMO_MODE=false`, integrate an OIDC/SAML identity provider, and derive authorization from trusted server-side claims.

## Project layout

- `lms/` — database, services, HRIS pipeline, audit and HTTP server.
- `lms/static/` — responsive administration and learner interface packaged with the application.
- `tests/` — automated provisioning, policy, role and audit tests.
- `samples/` — representative employee extract.
- `docs/` — architecture, security and operational documentation.
- `data/` — runtime SQLite database; excluded from the packaged clean state.

## Production evolution

This is a deployable MVP and reference implementation, not the full global enterprise platform. The architecture documentation explains the migration path to PostgreSQL, Keycloak, object storage, workflow orchestration, event streaming, search, analytics, Kubernetes, SSO, SCIM and enterprise observability.

## License

Apache License 2.0. See `LICENSE`.
