"""
Centralized permission definitions — scope taxonomy, role-to-scope mapping,
and pure helper functions for team-level permission checks.

Design: RBAC (role → scopes) for team-level + resource_permissions table for
per-resource sharing (Google Docs pattern). See ARCHITECTURE.md for details.
"""

import json
from typing import FrozenSet, Optional, Set

# ─── Scope Taxonomy ──────────────────────────────────────────────
# Format: resource:action (Clerk convention)

SCOPES: list[str] = [
    # Calls
    "calls:read",
    "calls:write",
    "calls:delete",
    "calls:share",
    # Intelligence
    "intelligence:read",
    "intelligence:write",
    # Team
    "team:read",
    "team:manage",
    "team:members:invite",
    "team:members:remove",
    "team:members:role",
    "team:billing:read",
    "team:billing:manage",
]

_SCOPES_SET: frozenset[str] = frozenset(SCOPES)

# ─── Role → Scope Mapping ────────────────────────────────────────
# Defined in code as a constant (not DB). Simple, auditable.
# Can be moved to DB later if custom roles are needed.

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "member": frozenset([
        "calls:read",
        "calls:write",
        "calls:share",
        "intelligence:read",
        "team:read",
    ]),
    "admin": frozenset([
        "calls:read",
        "calls:write",
        "calls:delete",
        "calls:share",
        "intelligence:read",
        "intelligence:write",
        "team:read",
        "team:manage",
        "team:members:invite",
        "team:members:remove",
        "team:billing:read",
    ]),
    "owner": frozenset(SCOPES),  # Owner gets everything
}

# ─── Resource Actions ────────────────────────────────────────────
# Valid actions per resource type (for resource-level permissions)

RESOURCE_ACTIONS: dict[str, frozenset[str]] = {
    "call": frozenset(["read", "write", "delete", "share"]),
}

# ─── Convenience Presets ─────────────────────────────────────────
# Used in the API — expand to multiple action rows in resource_permissions

PRESETS: dict[str, frozenset[str]] = {
    "viewer": frozenset(["read"]),
    "editor": frozenset(["read", "write"]),
    "owner": frozenset(["read", "write", "delete", "share"]),
}

# ─── Pure Helper Functions ───────────────────────────────────────


def has_scope(role: Optional[str], scope: str, scope_overrides: Optional[str] = None) -> bool:
    """Check if a team role has a given scope, with optional overrides. Pure function, no DB call."""
    if not role:
        return False
    if scope_overrides:
        return scope in get_effective_scopes(role, scope_overrides)
    return scope in ROLE_SCOPES.get(role, frozenset())


def get_scopes_for_role(role: Optional[str]) -> frozenset[str]:
    """Get all scopes for a team role."""
    if not role:
        return frozenset()
    return ROLE_SCOPES.get(role, frozenset())


def get_effective_scopes(role: str, scope_overrides_json: Optional[str] = None) -> frozenset[str]:
    """Compute effective scopes by applying overrides to role defaults."""
    base = set(get_scopes_for_role(role))
    if not scope_overrides_json:
        return frozenset(base)
    try:
        overrides = json.loads(scope_overrides_json)
    except (json.JSONDecodeError, TypeError):
        return frozenset(base)
    for scope, granted in overrides.items():
        if granted:
            base.add(scope)
        else:
            base.discard(scope)
    return frozenset(base)


def expand_preset(preset: str) -> frozenset[str]:
    """Expand a convenience preset name to a set of actions.

    Raises ValueError if the preset is not recognized.
    """
    actions = PRESETS.get(preset)
    if actions is None:
        raise ValueError(f"Unknown preset: {preset}. Valid: {', '.join(PRESETS)}")
    return actions


def is_valid_scope(scope: str) -> bool:
    """Check if a scope string is in the defined taxonomy."""
    return scope in _SCOPES_SET


def validate_resource_actions(resource_type: str, actions: set[str]) -> None:
    """Validate that actions are valid for a resource type.

    Raises ValueError if resource_type is unknown or actions are invalid.
    """
    valid = RESOURCE_ACTIONS.get(resource_type)
    if valid is None:
        raise ValueError(
            f"Unknown resource type: {resource_type}. "
            f"Valid: {', '.join(RESOURCE_ACTIONS)}"
        )
    invalid = actions - valid
    if invalid:
        raise ValueError(
            f"Invalid actions for {resource_type}: {', '.join(invalid)}. "
            f"Valid: {', '.join(valid)}"
        )
