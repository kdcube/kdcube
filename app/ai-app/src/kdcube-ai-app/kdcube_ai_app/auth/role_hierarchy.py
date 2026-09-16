# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Canonical platform-role sufficiency checks for authorization gates."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


REGISTERED_ROLE = "kdcube:role:registered"
PAID_ROLE = "kdcube:role:paid"
PRIVILEGED_ROLE = "kdcube:role:privileged"
ADMIN_ROLE = "kdcube:role:admin"
SUPER_ADMIN_ROLE = "kdcube:role:super-admin"

PAID_ROLES = frozenset({PAID_ROLE})
PRIVILEGED_ROLES = frozenset(
    {
        PRIVILEGED_ROLE,
        ADMIN_ROLE,
        SUPER_ADMIN_ROLE,
    }
)

# ``kdcube:role:admin`` is the legacy name for the privileged tier. Roles not
# named here are independent capabilities and are comparable only by identity.
_PLATFORM_ROLE_RANK = {
    REGISTERED_ROLE: 0,
    PAID_ROLE: 1,
    PRIVILEGED_ROLE: 2,
    ADMIN_ROLE: 2,
    SUPER_ADMIN_ROLE: 3,
}


def _role_text(value: Any) -> str:
    return str(value or "").strip()


def _roles(values: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            text
            for value in (values or ())
            if (text := _role_text(value))
        )
    )


def platform_role_rank(role: Any) -> int | None:
    """Return the platform authority tier, or ``None`` for an exact role."""

    return _PLATFORM_ROLE_RANK.get(_role_text(role))


def strongest_platform_role(roles: Iterable[Any] | None) -> str:
    """Return one strongest platform role from a role collection."""

    candidates = tuple(
        role for role in _roles(roles) if role in _PLATFORM_ROLE_RANK
    )
    if not candidates:
        return ""
    return max(candidates, key=lambda role: (_PLATFORM_ROLE_RANK[role], role))


def role_satisfies(actual_role: Any, required_role: Any) -> bool:
    """Return whether one held role satisfies one admission requirement.

    Platform roles use the ordered authority tiers. Custom, IdP, and service
    roles remain exact capabilities: a higher platform tier does not imply
    them.
    """

    actual = _role_text(actual_role)
    required = _role_text(required_role)
    if not actual or not required:
        return False
    if actual == required:
        return True
    actual_rank = _PLATFORM_ROLE_RANK.get(actual)
    required_rank = _PLATFORM_ROLE_RANK.get(required)
    return (
        actual_rank is not None
        and required_rank is not None
        and actual_rank >= required_rank
    )


def roles_satisfy_any(
    actual_roles: Iterable[Any] | None,
    required_roles: Iterable[Any] | None,
) -> bool:
    """Return whether any held role satisfies any required role.

    An empty requirement is an open role gate, matching the provider-surface
    contract used by REST, bundle operations, and Data Bus.
    """

    required = _roles(required_roles)
    if not required:
        return True
    actual = _roles(actual_roles)
    return any(
        role_satisfies(held, wanted)
        for wanted in required
        for held in actual
    )


def missing_required_roles(
    actual_roles: Iterable[Any] | None,
    required_roles: Iterable[Any] | None,
) -> tuple[str, ...]:
    """Return required roles not satisfied by the held role set."""

    actual = _roles(actual_roles)
    return tuple(
        wanted
        for wanted in _roles(required_roles)
        if not any(role_satisfies(held, wanted) for held in actual)
    )


def roles_satisfy_all(
    actual_roles: Iterable[Any] | None,
    required_roles: Iterable[Any] | None,
) -> bool:
    """Return whether every required role is satisfied by a held role."""

    return not missing_required_roles(actual_roles, required_roles)


__all__ = [
    "ADMIN_ROLE",
    "PAID_ROLE",
    "PAID_ROLES",
    "PRIVILEGED_ROLE",
    "PRIVILEGED_ROLES",
    "REGISTERED_ROLE",
    "SUPER_ADMIN_ROLE",
    "missing_required_roles",
    "platform_role_rank",
    "role_satisfies",
    "roles_satisfy_all",
    "roles_satisfy_any",
    "strongest_platform_role",
]
