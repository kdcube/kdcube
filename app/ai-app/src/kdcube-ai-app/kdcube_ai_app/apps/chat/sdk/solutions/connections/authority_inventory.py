# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Authority grant inventory and delegation-edge primitives.

An authority owns identities and grants. Before Connection Hub can let an
external delegate represent an identity, the authority must say which grants are
available for that identity and which subset is delegable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Iterable, Mapping, Protocol

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    PLATFORM_PRIVILEGED_ROLE_GRANTS,
)


DELEGATION_EDGE_SCHEMA = "connection_hub.delegation_edge.v1"
PLATFORM_AUTHORITY_ID = "platform"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item for item in (_clean(part) for part in value.replace(",", " ").split()) if item)
    if isinstance(value, (list, tuple, set)):
        return tuple(item for item in (_clean(part) for part in value) if item)
    return ()


def _as_set(value: Any) -> set[str]:
    return set(_as_tuple(value))


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    text = _clean(value)
    if not text:
        return False
    return any(text == pattern or fnmatch(text, pattern) or fnmatch(pattern, text) for pattern in patterns)


@dataclass(frozen=True)
class AuthorityGrantDefinition:
    """Configured grant policy exposed by an authority."""

    grant: str
    label: str = ""
    description: str = ""
    delegable_roles: tuple[str, ...] = ()
    delegable_permissions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def coerce(cls, value: Any) -> "AuthorityGrantDefinition":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(grant=_clean(value), label=_clean(value))
        if isinstance(value, Mapping):
            grant = _clean(value.get("grant") or value.get("scope") or value.get("name"))
            return cls(
                grant=grant,
                label=_clean(value.get("label")) or grant,
                description=_clean(value.get("description")),
                delegable_roles=_as_tuple(value.get("delegable_roles") or value.get("roles")),
                delegable_permissions=_as_tuple(value.get("delegable_permissions") or value.get("permissions")),
                metadata=dict(value.get("metadata") or {}) if isinstance(value.get("metadata"), Mapping) else {},
            )
        grant = _clean(getattr(value, "grant", ""))
        return cls(
            grant=grant,
            label=_clean(getattr(value, "label", "")) or grant,
            description=_clean(getattr(value, "description", "")),
            delegable_roles=_as_tuple(getattr(value, "delegable_roles", ())),
            delegable_permissions=_as_tuple(getattr(value, "delegable_permissions", ())),
        )


@dataclass(frozen=True)
class AuthorityIdentity:
    """An identity inside an authority for which grants can be inventoried."""

    authority_id: str
    identity_ref: str
    user_id: str = ""
    provider: str = ""
    integration_id: str = ""
    label: str = ""
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DelegableAuthorityGrant:
    authority_id: str
    identity_ref: str
    grant: str
    label: str = ""
    description: str = ""
    source: str = ""
    matched_permissions: tuple[str, ...] = ()
    matched_roles: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "authority_id": self.authority_id,
                "identity_ref": self.identity_ref,
                "grant": self.grant,
                "label": self.label,
                "description": self.description,
                "source": self.source,
                "matched_permissions": list(self.matched_permissions),
                "matched_roles": list(self.matched_roles),
                "metadata": dict(self.metadata),
            }.items()
            if value not in ("", [], {})
        }


@dataclass(frozen=True)
class AuthorityGrantInventory:
    authority_id: str
    identity: AuthorityIdentity
    grants: tuple[DelegableAuthorityGrant, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def grant_names(self) -> tuple[str, ...]:
        return tuple(item.grant for item in self.grants)

    def grant_map(self) -> dict[str, DelegableAuthorityGrant]:
        return {item.grant: item for item in self.grants}

    def to_dict(self) -> dict[str, Any]:
        return {
            "authority_id": self.authority_id,
            "identity": {
                "authority_id": self.identity.authority_id,
                "identity_ref": self.identity.identity_ref,
                "user_id": self.identity.user_id,
                "provider": self.identity.provider,
                "integration_id": self.identity.integration_id,
                "label": self.identity.label,
                "roles": list(self.identity.roles),
                "permissions": list(self.identity.permissions),
                "metadata": dict(self.identity.metadata),
            },
            "grants": [grant.to_dict() for grant in self.grants],
            "metadata": dict(self.metadata),
        }


class AuthorityGrantInventoryProvider(Protocol):
    authority_id: str

    async def list_delegable_grants(
        self,
        identity: AuthorityIdentity,
        *,
        requested_grants: Iterable[str] = (),
        context: Mapping[str, Any] | None = None,
    ) -> AuthorityGrantInventory:
        ...


@dataclass(frozen=True)
class AuthorityDelegationEdge:
    authority_id: str
    identity_ref: str
    user_id: str = ""
    grants: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    provider: str = ""
    integration_id: str = ""
    label: str = ""
    economics_budget_bypass: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": DELEGATION_EDGE_SCHEMA,
            "authority_id": self.authority_id,
            "identity_ref": self.identity_ref,
            "user_id": self.user_id,
            "grants": list(self.grants),
            "roles": list(self.roles),
            "permissions": list(self.permissions),
            "provider": self.provider,
            "integration_id": self.integration_id,
            "label": self.label,
            "metadata": dict(self.metadata),
        }
        if self.economics_budget_bypass is not None:
            payload["economics_budget_bypass"] = bool(self.economics_budget_bypass)
        return {key: value for key, value in payload.items() if value not in ("", [], {})}


class PlatformAuthorityInventoryProvider:
    """Grant inventory provider for the KDCube platform authority."""

    authority_id = PLATFORM_AUTHORITY_ID

    def __init__(
        self,
        grant_definitions: Iterable[Any],
        *,
        admin_roles: Iterable[str] = PLATFORM_PRIVILEGED_ROLE_GRANTS,
    ) -> None:
        self._definitions = {
            item.grant: item
            for item in (AuthorityGrantDefinition.coerce(row) for row in grant_definitions)
            if item.grant
        }
        self._admin_roles = set(_as_tuple(admin_roles))

    def _delegable(self, identity: AuthorityIdentity, definition: AuthorityGrantDefinition) -> DelegableAuthorityGrant | None:
        roles = set(identity.roles)
        permissions = set(identity.permissions)
        matched_roles: set[str] = set()
        matched_permissions: set[str] = set()
        if roles.intersection(self._admin_roles):
            matched_roles.update(sorted(roles.intersection(self._admin_roles)))
        matched_roles.update(sorted(roles.intersection(definition.delegable_roles)))
        for permission in permissions:
            if permission == definition.grant or _matches_any(definition.grant, [permission]):
                matched_permissions.add(permission)
            if definition.delegable_permissions and _matches_any(permission, definition.delegable_permissions):
                matched_permissions.add(permission)
        if not matched_roles and not matched_permissions:
            return None
        return DelegableAuthorityGrant(
            authority_id=identity.authority_id,
            identity_ref=identity.identity_ref,
            grant=definition.grant,
            label=definition.label or definition.grant,
            description=definition.description,
            source="authority_inventory",
            matched_permissions=tuple(sorted(matched_permissions)),
            matched_roles=tuple(sorted(matched_roles)),
        )

    async def list_delegable_grants(
        self,
        identity: AuthorityIdentity,
        *,
        requested_grants: Iterable[str] = (),
        context: Mapping[str, Any] | None = None,
    ) -> AuthorityGrantInventory:
        requested = _as_set(requested_grants)
        grants: list[DelegableAuthorityGrant] = []
        for definition in self._definitions.values():
            if requested and definition.grant not in requested:
                continue
            grant = self._delegable(identity, definition)
            if grant is not None:
                grants.append(grant)
        grants.sort(key=lambda item: item.grant)
        return AuthorityGrantInventory(
            authority_id=self.authority_id,
            identity=identity,
            grants=tuple(grants),
            metadata=dict(context or {}),
        )


def platform_identity_from_user(user: Mapping[str, Any]) -> AuthorityIdentity:
    user_id = ""
    for key in ("user_id", "sub", "id"):
        if user.get(key):
            user_id = _clean(user.get(key))
            break
    return AuthorityIdentity(
        authority_id=PLATFORM_AUTHORITY_ID,
        identity_ref=f"{PLATFORM_AUTHORITY_ID}:{user_id}" if user_id else "",
        user_id=user_id,
        provider=PLATFORM_AUTHORITY_ID,
        label="KDCube platform user",
        roles=tuple(sorted(_as_set(user.get("roles")))),
        permissions=tuple(sorted(_as_set(user.get("permissions")))),
    )


def selected_delegation_edge(
    inventory: AuthorityGrantInventory,
    selected_grants: Iterable[str],
    *,
    economics_budget_bypass: bool | None = None,
) -> AuthorityDelegationEdge | None:
    selected = _as_set(selected_grants)
    available = inventory.grant_map()
    grants = tuple(sorted(grant for grant in selected if grant in available))
    if not grants:
        return None
    permissions: set[str] = set()
    roles: set[str] = set()
    for grant in grants:
        item = available[grant]
        permissions.update(item.matched_permissions)
        roles.update(item.matched_roles)
    # Preserve the authority identity's roles because economics/legacy guards may
    # still derive bypass from roles; keep permissions narrowed to selected grants.
    roles.update(inventory.identity.roles)
    return AuthorityDelegationEdge(
        authority_id=inventory.authority_id,
        identity_ref=inventory.identity.identity_ref,
        user_id=inventory.identity.user_id,
        grants=grants,
        roles=tuple(sorted(roles)),
        permissions=tuple(sorted(permissions)),
        provider=inventory.identity.provider,
        integration_id=inventory.identity.integration_id,
        label=inventory.identity.label,
        economics_budget_bypass=economics_budget_bypass,
    )


__all__ = [
    "DELEGATION_EDGE_SCHEMA",
    "PLATFORM_AUTHORITY_ID",
    "AuthorityDelegationEdge",
    "AuthorityGrantDefinition",
    "AuthorityGrantInventory",
    "AuthorityGrantInventoryProvider",
    "AuthorityIdentity",
    "DelegableAuthorityGrant",
    "PlatformAuthorityInventoryProvider",
    "platform_identity_from_user",
    "selected_delegation_edge",
]
