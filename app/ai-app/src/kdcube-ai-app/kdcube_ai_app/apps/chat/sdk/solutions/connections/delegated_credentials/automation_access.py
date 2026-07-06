# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""User-created delegated access credentials for automations.

This module is the SDK-owned backend for the Connection Hub "Delegated Access"
surface. It deliberately reuses the delegated-client credential model used by
OAuth/MCP connectors:

- the approving platform subject remains the grantor;
- the issued bearer belongs to an ``integration:automation:*`` subject;
- grants are narrowed through the platform authority inventory;
- token metadata is bound in ``GrantStore`` so managed surfaces can enforce the
  selected grants/operations.

The Connection Hub bundle should only adapt UI operations to this service.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory import (
    AuthorityGrantInventory,
    PlatformAuthorityInventoryProvider,
    platform_identity_from_user,
    selected_delegation_edge,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    authority_has_platform_privilege,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.authority import (
    build_delegated_client_credential,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    OAuthDelegatedClientConfig,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import (
    ACCESS_TOKEN_TTL_SECONDS,
    integration_subject,
    mint_delegated_client_access_token,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import (
    GrantStore,
)
from kdcube_ai_app.auth.bundle.sessions import BUNDLE_SESSION_MAX_TTL_SECONDS


AUTOMATION_ACCESS_SCHEMA = "connection_hub.automation_access.v1"
AUTOMATION_CLIENT_PREFIX = "automation"
AUTOMATION_ACCESS_DEFAULT_TTL_SECONDS = ACCESS_TOKEN_TTL_SECONDS
ALL_RESOURCES_RESOURCE = "*"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _clean(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _subject_from_user(user: Mapping[str, Any]) -> str:
    for key in ("user_id", "sub", "id"):
        value = _clean(user.get(key))
        if value and value != "anonymous":
            return value
    return ""


def _subject_key(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


def _is_platform_admin(user: Mapping[str, Any]) -> bool:
    return authority_has_platform_privilege(_as_list(user.get("roles")))


def _bounded_ttl(value: Any) -> int:
    try:
        ttl = int(value or AUTOMATION_ACCESS_DEFAULT_TTL_SECONDS)
    except Exception:
        ttl = AUTOMATION_ACCESS_DEFAULT_TTL_SECONDS
    return max(60, min(ttl, BUNDLE_SESSION_MAX_TTL_SECONDS))


def _grantor_authority(
    user: Mapping[str, Any],
    *,
    grants: Iterable[str],
    inventory: AuthorityGrantInventory,
) -> dict[str, Any]:
    roles = sorted(set(_as_list(user.get("roles"))))
    has_privilege = authority_has_platform_privilege(roles)
    edge = selected_delegation_edge(
        inventory,
        grants,
        economics_budget_bypass=has_privilege,
    )
    edges = [edge.to_dict()] if edge is not None else []
    permissions = sorted(set(edge.permissions if edge is not None else ()))
    out: dict[str, Any] = {
        "schema": "connection_hub.grantor_authority.v1",
        "economics_budget_bypass": has_privilege,
    }
    if roles:
        out["grantor_roles"] = roles
    if permissions:
        out["grantor_permissions"] = permissions
    if edges:
        out["delegation_edges"] = edges
    return out


ACCESS_SOURCE_MANUAL = "manual"
ACCESS_SOURCE_OAUTH = "oauth"


@dataclass(frozen=True)
class AutomationAccessRecord:
    access_id: str
    label: str
    client_id: str
    grantor_subject: str
    delegate_subject: str
    operations: tuple[str, ...]
    resource_grants: Mapping[str, tuple[str, ...]]
    identity_scope: str = ""
    session_id: str = ""
    created_at: int = 0
    expires_at: int = 0
    last_four: str = ""
    source: str = ACCESS_SOURCE_MANUAL
    # OAuth-flow grants keep their live token material so revoke can kill the
    # refresh token and the current access-grant binding. Never public.
    refresh_token: str = ""
    access_token: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AutomationAccessRecord":
        return cls(
            access_id=_clean(value.get("access_id")),
            label=_clean(value.get("label")),
            client_id=_clean(value.get("client_id")),
            grantor_subject=_clean(value.get("grantor_subject")),
            delegate_subject=_clean(value.get("delegate_subject")),
            operations=tuple(_as_list(value.get("operations"))),
            resource_grants={
                _clean(key): tuple(_as_list(grants))
                for key, grants in dict(value.get("resource_grants") or {}).items()
                if _clean(key)
            },
            identity_scope=_clean(value.get("identity_scope")),
            session_id=_clean(value.get("session_id")),
            created_at=int(value.get("created_at") or 0),
            expires_at=int(value.get("expires_at") or 0),
            last_four=_clean(value.get("last_four")),
            source=_clean(value.get("source")) or ACCESS_SOURCE_MANUAL,
            refresh_token=_clean(value.get("refresh_token")),
            access_token=_clean(value.get("access_token")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": AUTOMATION_ACCESS_SCHEMA,
            "access_id": self.access_id,
            "label": self.label,
            "client_id": self.client_id,
            "grantor_subject": self.grantor_subject,
            "delegate_subject": self.delegate_subject,
            "operations": list(self.operations),
            "resource_grants": {key: list(value) for key, value in self.resource_grants.items()},
            "identity_scope": self.identity_scope,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_four": self.last_four,
            "source": self.source,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
        }

    def to_public_dict(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("session_id", None)
        payload.pop("refresh_token", None)
        payload.pop("access_token", None)
        return {key: value for key, value in payload.items() if value not in ("", [], {})}


class AutomationAccessService:
    """Create/list/revoke user-created delegated automation credentials."""

    def __init__(
        self,
        *,
        redis: Any,
        tenant: str,
        project: str,
        config: OAuthDelegatedClientConfig,
        grant_store: GrantStore | None = None,
        authority: Any | None = None,
        minter: Any | None = None,
    ) -> None:
        self._redis = redis
        self._tenant = _clean(tenant)
        self._project = _clean(project)
        self._config = config
        self._store = grant_store or GrantStore(redis, self._tenant, self._project)
        self._authority = authority
        self._minter = minter

    def _key(self, suffix: str) -> str:
        return f"{self._tenant}:{self._project}:kdcube:delegated-access:{suffix}"

    def _record_key(self, access_id: str) -> str:
        return self._key(f"automation:{access_id}")

    def _index_key(self, grantor_subject: str) -> str:
        return self._key(f"automation-by-grantor:{_subject_key(grantor_subject)}")

    async def _available_inventory(
        self,
        user: Mapping[str, Any],
        *,
        requested_grants: Iterable[str] = (),
    ) -> AuthorityGrantInventory:
        provider = PlatformAuthorityInventoryProvider(self._config.capabilities)
        return await provider.list_delegable_grants(
            platform_identity_from_user(user),
            requested_grants=requested_grants,
        )

    async def grant_options(self, user: Mapping[str, Any]) -> list[dict[str, Any]]:
        inventory = await self._available_inventory(user)
        return [item.to_dict() for item in inventory.grants]

    def resource_options(self, user: Mapping[str, Any]) -> list[dict[str, Any]]:
        platform_admin = _is_platform_admin(user)
        out: list[dict[str, Any]] = []
        for resource in self._config.resources:
            if resource.admin_only and not platform_admin:
                continue
            out.append(
                {
                    "resource": resource.resource,
                    "label": resource.label or resource.resource,
                    "identity_scope": resource.identity_scope,
                    "grants": list(resource.grants),
                    "admin_only": bool(resource.admin_only),
                    "operations": [
                        {
                            "name": tool.name,
                            "label": tool.label,
                            "description": tool.description,
                            "grants": list(tool.grants),
                        }
                        for tool in resource.tools
                    ],
                }
            )
        return out

    def _configured_resource(self, resource: str) -> Any | None:
        text = _clean(resource).rstrip("/")
        if not text:
            return None
        for item in self._config.resources:
            if str(item.resource or "").strip().rstrip("/") == text:
                return item
        return None

    def _configured_resources(self, resources: Iterable[str]) -> tuple[Any, ...]:
        selected = _as_list(list(resources))
        configs: list[Any] = []
        missing: list[str] = []
        for resource in selected:
            cfg = self._configured_resource(resource)
            if cfg is None:
                missing.append(resource)
            else:
                configs.append(cfg)
        if missing:
            raise ValueError("unknown delegated resource(s): " + ", ".join(missing))
        return tuple(configs)

    def _resource_grants(self, resource_grants: Mapping[str, Any]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for resource, grants in dict(resource_grants or {}).items():
            resource_value = _clean(resource)
            selected = _as_list(grants)
            if resource_value and selected:
                out[resource_value] = selected
        return out

    async def list_access(self, user: Mapping[str, Any]) -> dict[str, Any]:
        grantor_subject = _subject_from_user(user)
        if not grantor_subject:
            return {"ok": False, "error": "delegated_access_requires_authenticated_user"}

        now = int(time.time())
        raw_ids = await self._redis.smembers(self._index_key(grantor_subject))
        access_ids = [
            item.decode("utf-8") if isinstance(item, (bytes, bytearray)) else str(item)
            for item in (raw_ids or [])
        ]
        records: list[dict[str, Any]] = []
        stale: list[str] = []
        for access_id in access_ids:
            raw = await self._redis.get(self._record_key(access_id))
            if raw is None:
                stale.append(access_id)
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                stale.append(access_id)
                continue
            record = AutomationAccessRecord.from_mapping(payload)
            if record.expires_at and record.expires_at < now:
                stale.append(access_id)
                continue
            records.append(record.to_public_dict())
        if stale and hasattr(self._redis, "srem"):
            await self._redis.srem(self._index_key(grantor_subject), *stale)

        records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {
            "ok": True,
            "platform_user_id": grantor_subject,
            "grant_options": await self.grant_options(user),
            "resources": self.resource_options(user),
            "items": records,
        }

    def _resolve_operations(self, *, grants: list[str], operations: list[str], resources: list[str]) -> list[str]:
        available_by_name: dict[str, Any] = {}
        for resource in resources:
            for operation in self._config.tools_for_scopes(grants, resource=resource or None):
                available_by_name.setdefault(operation.name, operation)
        available_names = set(available_by_name)
        if operations:
            unknown = sorted(set(operations) - available_names)
            if unknown:
                raise ValueError(f"unknown or unauthorized operation(s): {', '.join(unknown)}")
            return sorted(operations)
        return sorted(available_names)

    async def create_access(
        self,
        user: Mapping[str, Any],
        *,
        label: str,
        resource_grants: Mapping[str, Any],
        operations: Iterable[str] = (),
        ttl_seconds: Any = None,
    ) -> dict[str, Any]:
        grantor_subject = _subject_from_user(user)
        if not grantor_subject:
            return {"ok": False, "error": "delegated_access_requires_authenticated_user"}

        selected_resource_grants = self._resource_grants(resource_grants)
        selected_resources = list(selected_resource_grants)
        if self._config.resources and not selected_resources:
            return {"ok": False, "error": "delegated_access_requires_resource_grants"}

        selected_grants = _as_list([
            grant
            for grants_for_resource in selected_resource_grants.values()
            for grant in grants_for_resource
        ])
        if not selected_grants:
            return {"ok": False, "error": "delegated_access_requires_resource_grants"}

        inventory = await self._available_inventory(user, requested_grants=selected_grants)
        available = set(inventory.grant_names())
        denied = [grant for grant in selected_grants if grant not in available]
        if denied:
            return {
                "ok": False,
                "error": "delegated_access_grants_not_delegable",
                "grants": denied,
            }

        try:
            resource_configs = self._configured_resources(selected_resources) if self._config.resources else ()
        except ValueError:
            return {"ok": False, "error": "delegated_access_unknown_resources", "resources": selected_resources}
        admin_required = [cfg.resource for cfg in resource_configs if cfg.admin_only]
        if admin_required and not _is_platform_admin(user):
            return {
                "ok": False,
                "error": "delegated_access_resource_requires_admin",
                "resources": admin_required,
            }
        cfg_by_resource = {cfg.resource: cfg for cfg in resource_configs}
        for resource_value, grants_for_resource in selected_resource_grants.items():
            cfg = cfg_by_resource.get(resource_value)
            if cfg is None:
                continue
            allowed_for_resource = set(self._config.supported_scopes(resource_value))
            disallowed = [grant for grant in grants_for_resource if grant not in allowed_for_resource]
            if disallowed:
                return {
                    "ok": False,
                    "error": "delegated_access_grants_not_allowed_for_resources",
                    "grants": disallowed,
                    "resource": resource_value,
                }
        identity_scopes = {
            _clean(getattr(cfg, "identity_scope", "") or "grantor")
            for cfg in resource_configs
        }
        if len(identity_scopes) > 1:
            return {
                "ok": False,
                "error": "delegated_access_resources_have_conflicting_identity_scopes",
                "resources": selected_resources,
            }
        identity_scope = next(iter(identity_scopes), "grantor")
        named_services: dict[str, Any] = {}
        for cfg in resource_configs:
            if isinstance(cfg.named_services, Mapping):
                named_services.update(dict(cfg.named_services))
        selected_operations = self._resolve_operations(
            grants=selected_grants,
            operations=_as_list(list(operations)),
            resources=selected_resources,
        )

        access_id = "aut_" + secrets.token_urlsafe(10)
        client_id = f"{AUTOMATION_CLIENT_PREFIX}:{access_id}"
        ttl = _bounded_ttl(ttl_seconds)
        now = int(time.time())
        credential = build_delegated_client_credential(
            grantor_subject=grantor_subject,
            client_id=client_id,
            scopes=selected_grants,
            operations=selected_operations,
            tenant=self._tenant,
            project=self._project,
            resource_grants=selected_resource_grants,
            identity_scope=identity_scope,
            expires_in=ttl,
            issued_at=now,
        )
        minter = self._minter or mint_delegated_client_access_token
        authority = self._authority
        if authority is None:
            from kdcube_ai_app.auth.bundle import get_bundle_session_authority

            authority = get_bundle_session_authority(tenant=self._tenant, project=self._project)
        minted = await minter(
            grantor_subject,
            selected_grants,
            authority=authority,
            client_id=client_id,
            operations=selected_operations,
            credential=credential.to_dict(),
            ttl_seconds=ttl,
        )
        access_token = _clean(minted.get("access_token"))
        expires_in = int(minted.get("expires_in") or ttl)
        expires_at = now + expires_in
        session_id = _clean(minted.get("session_id"))

        grantor_authority = _grantor_authority(user, grants=selected_grants, inventory=inventory)
        delegation_edges = list(grantor_authority.get("delegation_edges") or [])
        await self._store.bind_access_grant(
            access_token,
            selected_operations,
            expires_in,
            credential=credential.to_dict(),
            grantor_authority=grantor_authority,
            delegation_edges=delegation_edges,
            named_services=named_services,
        )

        record = AutomationAccessRecord(
            access_id=access_id,
            label=_clean(label) or "Automation access",
            client_id=client_id,
            grantor_subject=grantor_subject,
            delegate_subject=integration_subject(grantor_subject, client_id=client_id),
            operations=tuple(selected_operations),
            resource_grants={key: tuple(value) for key, value in selected_resource_grants.items()},
            identity_scope=identity_scope,
            session_id=session_id,
            created_at=now,
            expires_at=expires_at,
            last_four=access_token[-4:] if access_token else "",
        )
        await self._redis.setex(self._record_key(access_id), expires_in, json.dumps(record.to_dict()))
        await self._redis.sadd(self._index_key(grantor_subject), access_id)
        await self._redis.expire(self._index_key(grantor_subject), BUNDLE_SESSION_MAX_TTL_SECONDS)

        return {
            "ok": True,
            "access": record.to_public_dict(),
            "access_token": access_token,
            "authorization_header": f"Bearer {access_token}" if access_token else "",
        }

    async def record_oauth_grant(
        self,
        *,
        grantor_subject: str,
        client_id: str,
        client_label: str = "",
        scopes: Iterable[str] = (),
        operations: Iterable[str] = (),
        resource: str = "",
        identity_scope: str = "",
        access_token: str = "",
        refresh_token: str = "",
    ) -> AutomationAccessRecord | None:
        """Register (or update) an OAuth-flow delegated grant in the registry.

        Called on every token issuance for an external client (initial consent
        and refresh rotations), so the user sees the connection in Connection
        Hub and revoking it invalidates the CURRENT refresh token and access
        grant. One record per (grantor, client, resource): reconsent updates
        it instead of piling up rows.
        """
        grantor = _clean(grantor_subject)
        client = _clean(client_id)
        if not grantor or not client:
            return None
        resource_value = _clean(resource)
        digest = hashlib.sha256(f"{grantor}|{client}|{resource_value}".encode("utf-8")).hexdigest()[:16]
        access_id = f"oauth-{digest}"
        now = int(time.time())
        created_at = now
        existing_raw = await self._redis.get(self._record_key(access_id))
        if existing_raw is not None:
            try:
                created_at = int(json.loads(existing_raw).get("created_at") or now)
            except Exception:
                created_at = now
        ttl = max(60, int(self._store.refresh_ttl))
        scope_list = _as_list(list(scopes))
        record = AutomationAccessRecord(
            access_id=access_id,
            label=_clean(client_label) or client,
            client_id=client,
            grantor_subject=grantor,
            delegate_subject=integration_subject(grantor, client_id=client),
            operations=tuple(_as_list(list(operations))),
            resource_grants={resource_value or "*": tuple(scope_list)},
            identity_scope=_clean(identity_scope),
            created_at=created_at,
            expires_at=now + ttl,
            source=ACCESS_SOURCE_OAUTH,
            refresh_token=_clean(refresh_token),
            access_token=_clean(access_token),
        )
        await self._redis.setex(self._record_key(access_id), ttl, json.dumps(record.to_dict()))
        await self._redis.sadd(self._index_key(grantor), access_id)
        await self._redis.expire(self._index_key(grantor), BUNDLE_SESSION_MAX_TTL_SECONDS)
        return record

    async def revoke_access(self, user: Mapping[str, Any], *, access_id: str) -> dict[str, Any]:
        grantor_subject = _subject_from_user(user)
        if not grantor_subject:
            return {"ok": False, "error": "delegated_access_requires_authenticated_user"}
        access_id_value = _clean(access_id)
        if not access_id_value:
            return {"ok": False, "error": "delegated_access_id_required"}
        raw = await self._redis.get(self._record_key(access_id_value))
        if raw is None:
            return {"ok": True, "removed": False}
        record = AutomationAccessRecord.from_mapping(json.loads(raw))
        if record.grantor_subject != grantor_subject:
            return {"ok": False, "error": "delegated_access_cross_user_access_denied"}
        removed_session = False
        if record.session_id:
            from kdcube_ai_app.auth.bundle import get_bundle_session_authority

            authority = self._authority or get_bundle_session_authority(tenant=self._tenant, project=self._project)
            removed_session = bool(await authority.logout(session_id=record.session_id))
        # OAuth-flow grants: kill the refresh token (no new access tokens) and
        # the current access-grant binding (managed guards reject the bearer
        # immediately).
        refresh_revoked = False
        if record.refresh_token:
            refresh_revoked = bool(await self._store.revoke_refresh_token(record.refresh_token))
        if record.access_token:
            await self._store.revoke_access_grant(record.access_token)
        await self._redis.delete(self._record_key(access_id_value))
        if hasattr(self._redis, "srem"):
            await self._redis.srem(self._index_key(grantor_subject), access_id_value)
        return {
            "ok": True,
            "removed": True,
            "session_removed": removed_session,
            "refresh_token_revoked": refresh_revoked,
        }


__all__ = [
    "ALL_RESOURCES_RESOURCE",
    "AUTOMATION_ACCESS_DEFAULT_TTL_SECONDS",
    "AUTOMATION_ACCESS_SCHEMA",
    "AutomationAccessRecord",
    "AutomationAccessService",
]
