"""Role-based permission catalogue.

Permissions are intentionally coarse-grained today. Fine-grained scoping (the
``team`` checks) is enforced by services because it depends on the organization
graph rather than the role alone.
"""

from __future__ import annotations

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "learner": frozenset({"read", "complete_self", "notifications"}),
    "manager": frozenset({
        "read", "complete_self", "read_team", "complete_team",
        "notifications", "export",
    }),
    "instructor": frozenset({
        "read", "complete_any", "manage_content", "notifications",
    }),
    "auditor": frozenset({"read", "audit", "export", "notifications"}),
    "compliance_officer": frozenset({
        "read", "audit", "export", "manage_learning", "notifications",
    }),
    "learning_admin": frozenset({
        "read", "manage_learning", "manage_content", "manage_people", "hris", "audit",
        "complete_any", "export", "notifications",
    }),
    "platform_admin": frozenset({
        "read", "manage_learning", "manage_content", "manage_people", "hris", "audit",
        "complete_any", "export", "notifications",
    }),
}

ALL_ROLES = tuple(sorted(ROLE_PERMISSIONS))


def has_permission(role: str | None, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role or "", frozenset())


def permissions_for(role: str | None) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(role or "", frozenset()))
