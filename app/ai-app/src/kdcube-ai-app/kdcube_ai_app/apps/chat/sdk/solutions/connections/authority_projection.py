# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Authority projection helpers owned by Connection Hub.

Execution surfaces should not know how to interpret platform roles, which
identity pays economics, or which authority field carries budget bypass. They
receive an authority envelope from Connection Hub and ask this module to project
the fields needed at a boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from kdcube_ai_app.auth.AuthManager import PRIVILEGED_ROLES


PLATFORM_PRIVILEGED_ROLE_GRANTS: tuple[str, ...] = tuple(
    sorted(str(role or "").strip() for role in PRIVILEGED_ROLES if str(role or "").strip())
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_authority_values(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = values.replace(",", " ").split()
    if isinstance(values, (list, tuple, set)):
        return tuple(item for item in (_clean(value) for value in values) if item)
    item = _clean(values)
    return (item,) if item else ()


def _authority_mapping(source: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return source if isinstance(source, Mapping) else {}


def authority_roles(
    source: Mapping[str, Any] | None,
    *,
    fallback: Iterable[Any] | None = None,
) -> tuple[str, ...]:
    authority = _authority_mapping(source)
    values = (
        authority.get("platform_roles")
        or authority.get("grantor_roles")
        or authority.get("roles")
        or fallback
    )
    return normalize_authority_values(values)


def authority_permissions(
    source: Mapping[str, Any] | None,
    *,
    fallback: Iterable[Any] | None = None,
) -> tuple[str, ...]:
    authority = _authority_mapping(source)
    values = (
        authority.get("platform_permissions")
        or authority.get("grantor_permissions")
        or authority.get("permissions")
        or fallback
    )
    return normalize_authority_values(values)


def authority_has_platform_privilege(roles: Iterable[Any] | None) -> bool:
    return bool(set(normalize_authority_values(roles)) & set(PLATFORM_PRIVILEGED_ROLE_GRANTS))


def authority_explicit_budget_bypass(source: Mapping[str, Any] | None) -> bool | None:
    authority = _authority_mapping(source)
    for key in ("economics_budget_bypass", "budget_bypass"):
        value = authority.get(key)
        if isinstance(value, bool):
            return value
    return None


def authority_budget_bypass(
    source: Mapping[str, Any] | None,
    *,
    roles: Iterable[Any] | None = None,
) -> bool | None:
    explicit = authority_explicit_budget_bypass(source)
    if explicit is not None:
        return explicit
    resolved_roles = tuple(roles or ()) or authority_roles(source)
    if authority_has_platform_privilege(resolved_roles):
        return True
    return None


def authority_actor_user_id(
    source: Mapping[str, Any] | None,
    *,
    fallback_user_id: str = "",
) -> str:
    authority = _authority_mapping(source)
    return _clean(
        authority.get("actor_user_id")
        or authority.get("storage_user_id")
        or authority.get("delegate_user_id")
        or authority.get("delegate_identity")
        or fallback_user_id
    )


def authority_economics_user_id(
    source: Mapping[str, Any] | None,
    *,
    actor_user_id: str = "",
    fallback_user_id: str = "",
) -> str:
    authority = _authority_mapping(source)
    actor = _clean(actor_user_id) or authority_actor_user_id(authority, fallback_user_id=fallback_user_id)
    return _clean(
        authority.get("economics_user_id")
        or authority.get("platform_user_id")
        or authority.get("grantor_user_id")
        or authority.get("subject_user_id")
        or authority.get("user_id")
        or fallback_user_id
        or actor
    )


def authority_has_platform_economics(
    source: Mapping[str, Any] | None,
    *,
    economics_user_id: str = "",
) -> bool:
    """Return true when economics should be owned by a platform authority user.

    A verified external actor can have an actor session without being a
    registered platform user. Free-user quota, subscriptions, and admin bypass
    belong only to a platform/grantor projection.
    """

    authority = _authority_mapping(source)
    if not authority:
        return False

    economics_user = _clean(economics_user_id)
    if _clean(authority.get("economics_projection")) == "platform_user":
        return True

    platform_user = _clean(authority.get("platform_user_id"))
    if platform_user and (not economics_user or economics_user == platform_user):
        return True

    grantor_user = _clean(authority.get("grantor_user_id") or authority.get("subject_user_id"))
    if grantor_user and (not economics_user or economics_user == grantor_user):
        authority_id = _clean(authority.get("authority_id"))
        charge_to = _clean(authority.get("charge_to"))
        if authority_id in {"platform", "kdcube.platform", "kdcube-front-shell"} or charge_to == "grantor":
            return True

    return False


@dataclass(frozen=True)
class AuthorityExecutionProjection:
    actor_user_id: str
    economics_user_id: str
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    budget_bypass: bool | None = None
    is_anonymous: bool = False
    source: dict[str, Any] = field(default_factory=dict)

    def to_provenance(self) -> dict[str, Any]:
        return {
            "actor_user_id": self.actor_user_id,
            "economics_user_id": self.economics_user_id,
            "identity_authority": dict(self.source),
        }


def project_execution_authority(
    source: Mapping[str, Any] | None,
    *,
    actor_user_id: str = "",
    economics_user_id: str = "",
    fallback_user_id: str = "",
    fallback_roles: Iterable[Any] | None = None,
    fallback_permissions: Iterable[Any] | None = None,
) -> AuthorityExecutionProjection:
    authority = dict(source or {}) if isinstance(source, Mapping) else {}
    actor = authority_actor_user_id(authority, fallback_user_id=actor_user_id or fallback_user_id)
    economics_user = _clean(economics_user_id) or authority_economics_user_id(
        authority,
        actor_user_id=actor,
        fallback_user_id=fallback_user_id,
    )
    roles = authority_roles(authority)
    permissions = authority_permissions(authority)
    has_platform_economics = authority_has_platform_economics(authority, economics_user_id=economics_user)
    budget_bypass = authority_budget_bypass(authority, roles=roles) if has_platform_economics else None
    del fallback_roles, fallback_permissions
    is_anonymous = (
        (not economics_user)
        or economics_user == "anonymous"
        or not has_platform_economics
    )
    return AuthorityExecutionProjection(
        actor_user_id=actor,
        economics_user_id=economics_user,
        roles=roles,
        permissions=permissions,
        budget_bypass=budget_bypass,
        is_anonymous=is_anonymous,
        source=authority,
    )


__all__ = [
    "PLATFORM_PRIVILEGED_ROLE_GRANTS",
    "AuthorityExecutionProjection",
    "authority_actor_user_id",
    "authority_budget_bypass",
    "authority_economics_user_id",
    "authority_explicit_budget_bypass",
    "authority_has_platform_economics",
    "authority_has_platform_privilege",
    "authority_permissions",
    "authority_roles",
    "normalize_authority_values",
    "project_execution_authority",
]
