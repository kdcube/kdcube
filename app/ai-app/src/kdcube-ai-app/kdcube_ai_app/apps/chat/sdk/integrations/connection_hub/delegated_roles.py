# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Project explicit delegated Card role grants into runtime identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


PLATFORM_ROLE_PREFIX = "kdcube:role:"
REGISTERED_ROLE = "kdcube:role:registered"
PAID_ROLE = "kdcube:role:paid"
PRIVILEGED_ROLES = frozenset(
    {
        "kdcube:role:admin",
        "kdcube:role:privileged",
        "kdcube:role:super-admin",
    }
)


def _values(items: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(
        sorted({str(item).strip() for item in (items or ()) if str(item).strip()})
    )


def platform_role_grants(grants: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(
        role for role in _values(grants) if role.startswith(PLATFORM_ROLE_PREFIX)
    )


def user_type_for_roles(roles: Iterable[Any] | None) -> str:
    selected = set(_values(roles))
    if selected & PRIVILEGED_ROLES:
        return "privileged"
    if PAID_ROLE in selected:
        return "paid"
    if selected:
        return "registered"
    return "external"


@dataclass(frozen=True)
class DelegatedRoleProjection:
    roles: tuple[str, ...]
    user_type: str
    selected_on_card: bool


def delegated_role_projection(
    grants: Iterable[Any] | None,
    *,
    fallback_roles: Iterable[Any] | None = None,
) -> DelegatedRoleProjection:
    """Use Card-selected roles when present; otherwise preserve legacy roles."""

    selected = platform_role_grants(grants)
    roles = selected or _values(fallback_roles)
    return DelegatedRoleProjection(
        roles=roles,
        # Older Cards projected grantor roles for endpoint checks while the
        # delegated actor remained external. Only an explicit Card role opts
        # into a platform user type.
        user_type=user_type_for_roles(selected) if selected else "external",
        selected_on_card=bool(selected),
    )


__all__ = [
    "DelegatedRoleProjection",
    "PAID_ROLE",
    "PLATFORM_ROLE_PREFIX",
    "PRIVILEGED_ROLES",
    "REGISTERED_ROLE",
    "delegated_role_projection",
    "platform_role_grants",
    "user_type_for_roles",
]
