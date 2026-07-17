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

import copy
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
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
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.boundary_policy import (
    NamedServiceBoundaryCatalog,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import (
    RedisNamedServiceDiscovery,
)
from kdcube_ai_app.auth.bundle.sessions import BUNDLE_SESSION_MAX_TTL_SECONDS


AUTOMATION_ACCESS_SCHEMA = "connection_hub.automation_access.v1"
AUTOMATION_CLIENT_PREFIX = "automation"
AUTOMATION_ACCESS_DEFAULT_TTL_SECONDS = ACCESS_TOKEN_TTL_SECONDS
ALL_RESOURCES_RESOURCE = "*"

# Live delivery: registry mutations (an OAuth consent lands a grant, a manual
# token is created, anything is revoked) are pushed to the user's OPEN
# Connection Hub widgets over the Data Bus. The widget registers its federated
# data-bus session at claim time; mutations fan out to every live session of
# the grantor. Event type consumed by the widget:
DELEGATED_ACCESS_CHANGED_EVENT = "connection_hub.delegated_access.changed"

_LOGGER = __import__("logging").getLogger("connection_hub.delegated_access")


def _live_sessions_key(tenant: str, project: str, grantor_subject: str) -> str:
    return (
        f"{_clean(tenant)}:{_clean(project)}:kdcube:delegated-access:"
        f"live-sessions:{_subject_key(_clean(grantor_subject))}"
    )


async def register_delegated_access_live_session(
    redis: Any,
    *,
    tenant: str,
    project: str,
    grantor_subject: str,
    session_id: str,
    expires_at: int | float | None = None,
) -> None:
    """Remember a user's live Connection Hub data-bus session so registry
    mutations can be pushed to it. Members expire with the session token."""
    subject = _clean(grantor_subject)
    sid = _clean(session_id)
    if not subject or not sid:
        return
    now = int(time.time())
    score = int(expires_at or 0) or (now + 3600)
    key = _live_sessions_key(tenant, project, subject)
    await redis.zadd(key, {sid: score})
    await redis.zremrangebyscore(key, "-inf", now)
    await redis.expire(key, BUNDLE_SESSION_MAX_TTL_SECONDS)


async def notify_delegated_access_changed(
    redis: Any,
    *,
    tenant: str,
    project: str,
    grantor_subject: str,
    action: str,
    access: Mapping[str, Any] | None = None,
    access_id: str = "",
    relay: Any = None,
) -> None:
    """Fan a registry mutation out to the grantor's live hub sessions.

    Fire-and-forget by contract: a delivery failure must never fail the
    mutation that triggered it.
    """
    subject = _clean(grantor_subject)
    if not subject:
        return
    try:
        key = _live_sessions_key(tenant, project, subject)
        now = int(time.time())
        await redis.zremrangebyscore(key, "-inf", now)
        session_ids = [
            sid.decode("utf-8") if isinstance(sid, (bytes, bytearray)) else str(sid)
            for sid in await redis.zrange(key, 0, -1)
        ]
        if not session_ids:
            return
        if relay is None:
            from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator

            relay = ChatRelayCommunicator()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for sid in session_ids:
            payload = {
                "type": DELEGATED_ACCESS_CHANGED_EVENT,
                "timestamp": timestamp,
                "service": {
                    "request_id": f"delegated-access-{_clean(access_id) or action}",
                    "tenant": _clean(tenant),
                    "project": _clean(project),
                    "user": subject,
                },
                "conversation": {"session_id": sid, "conversation_id": sid, "turn_id": ""},
                "event": {
                    "agent": "connection-hub",
                    "title": "Delegated Access Changed",
                    "status": "completed",
                    "step": "connection.delegated_access",
                },
                "data": {
                    "action": action,
                    "access_id": _clean(access_id) or str((access or {}).get("access_id") or ""),
                    "access": dict(access or {}),
                },
                "route": "chat_service",
            }
            await relay.emit(
                event="chat_service",
                data=payload,
                tenant=tenant,
                project=project,
                session_id=sid,
            )
    except Exception:
        _LOGGER.exception(
            "[connection-hub.delegated_access] live notify failed action=%s", action
        )


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
# A per-agent delegated grant: the consenting user grants a hosted agent
# (a "Delegated By KDCube" entity, keyed by a deterministic client_id) access to
# a resource. Unlike a MANUAL automation (which mints its own random client), the
# client_id is caller-supplied and stable, so re-consent updates one record.
ACCESS_SOURCE_AGENT = "agent"


@dataclass(frozen=True)
class AutomationAccessRecord:
    access_id: str
    label: str
    client_id: str
    grantor_subject: str
    delegate_subject: str
    operations: tuple[str, ...]
    resource_grants: Mapping[str, tuple[str, ...]]
    named_service_operations: Mapping[str, Mapping[str, tuple[str, ...]]] = field(
        default_factory=dict
    )
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
            named_service_operations={
                _clean(resource): {
                    _clean(namespace).lower().rstrip(":"): tuple(_as_list(operations))
                    for namespace, operations in dict(namespaces or {}).items()
                    if _clean(namespace)
                }
                for resource, namespaces in dict(
                    value.get("named_service_operations") or {}
                ).items()
                if _clean(resource) and isinstance(namespaces, Mapping)
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
            "named_service_operations": {
                resource: {
                    namespace: list(operations)
                    for namespace, operations in namespaces.items()
                }
                for resource, namespaces in self.named_service_operations.items()
            },
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
        named_service_discovery: Any | None = None,
    ) -> None:
        self._redis = redis
        self._tenant = _clean(tenant)
        self._project = _clean(project)
        self._config = config
        self._store = grant_store or GrantStore(redis, self._tenant, self._project)
        self._authority = authority
        self._minter = minter
        self._named_service_discovery = named_service_discovery

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

    async def _named_service_options(
        self,
        config: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Project the configured namespace boundary and provider requirements.

        The namespace/tool tree comes directly from the same descriptor-backed
        catalog used by OAuth consent. Connected-account requirements are
        copied verbatim from each live provider's discovery metadata. On
        credential creation, the selected operation subset narrows this same
        ``named_services`` policy object; no parallel policy model is created.
        """

        namespaces = NamedServiceBoundaryCatalog(config).list_public()
        if not namespaces:
            return []

        discovery = self._named_service_discovery or RedisNamedServiceDiscovery(
            self._redis,
            tenant=self._tenant,
            project=self._project,
        )
        for namespace in namespaces:
            namespace_name = _clean(namespace.get("namespace"))
            if not namespace_name:
                continue
            try:
                entries = await discovery.entries_for_namespace(namespace_name)
            except Exception:
                _LOGGER.debug(
                    "[connection-hub.delegated_access] named-service provider requirements unavailable namespace=%s",
                    namespace_name,
                    exc_info=True,
                )
                continue

            requirements: list[dict[str, Any]] = []
            seen: set[str] = set()
            for entry in entries or ():
                spec = getattr(entry, "spec", None)
                metadata = getattr(spec, "metadata", None)
                raw_requirements = (
                    metadata.get("connected_accounts")
                    if isinstance(metadata, Mapping)
                    else None
                )
                if not isinstance(raw_requirements, (list, tuple)):
                    continue
                for raw_requirement in raw_requirements:
                    if not isinstance(raw_requirement, Mapping):
                        continue
                    requirement = copy.deepcopy(dict(raw_requirement))
                    signature = json.dumps(
                        requirement,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                    if signature in seen:
                        continue
                    seen.add(signature)
                    requirements.append(requirement)
            if requirements:
                namespace["connected_accounts"] = requirements
        return namespaces

    async def resource_options(self, user: Mapping[str, Any]) -> list[dict[str, Any]]:
        platform_admin = _is_platform_admin(user)
        out: list[dict[str, Any]] = []
        for resource in self._config.resources:
            if resource.admin_only and not platform_admin:
                continue
            option = {
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
            if isinstance(resource.named_services, Mapping):
                named_services = await self._named_service_options(resource.named_services)
                if named_services:
                    option["named_services"] = named_services
            out.append(option)
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

    def _named_service_operation_selection(
        self,
        value: Mapping[str, Any] | None,
    ) -> dict[str, dict[str, list[str]]] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("named_service_operations must be an object")
        out: dict[str, dict[str, list[str]]] = {}
        for resource, raw_namespaces in value.items():
            resource_value = _clean(resource)
            if not resource_value:
                continue
            if not isinstance(raw_namespaces, Mapping):
                raise ValueError(
                    f"named_service_operations[{resource_value!r}] must be an object"
                )
            namespaces: dict[str, list[str]] = {}
            for namespace, raw_operations in raw_namespaces.items():
                namespace_value = _clean(namespace).lower().rstrip(":")
                if not namespace_value:
                    continue
                namespaces[namespace_value] = _as_list(raw_operations)
            out[resource_value] = namespaces
        return out

    @staticmethod
    def _operation_grants(policy: Mapping[str, Any], fallback: Mapping[str, Any]) -> set[str]:
        return set(
            _as_list(
                policy.get("grants")
                or policy.get("scopes")
                or fallback.get("grants")
                or fallback.get("scopes")
            )
        )

    def _narrow_named_service_config(
        self,
        *,
        config: Mapping[str, Any],
        selected: Mapping[str, list[str]],
        grants: Iterable[str],
        resource: str,
    ) -> dict[str, Any]:
        """Keep only explicitly selected, grant-authorized namespace operations.

        The returned object retains the existing descriptor schema. The named-
        service bridge therefore enforces this selection through its normal
        ``NamedServiceBoundaryCatalog`` path.
        """

        raw_namespaces = config.get("namespaces")
        if not isinstance(raw_namespaces, Mapping):
            if any(selected.values()):
                raise ValueError(
                    f"resource {resource!r} does not configure named-service namespaces"
                )
            narrowed = copy.deepcopy(dict(config))
            narrowed["namespaces"] = {}
            return narrowed

        configured_by_name = {
            _clean(namespace).lower().rstrip(":"): (namespace, raw_policy)
            for namespace, raw_policy in raw_namespaces.items()
            if _clean(namespace) and isinstance(raw_policy, Mapping)
        }
        unknown_namespaces = sorted(set(selected) - set(configured_by_name))
        if unknown_namespaces:
            raise ValueError(
                f"unknown named-service namespace(s) for {resource!r}: "
                + ", ".join(unknown_namespaces)
            )

        available_grants = set(_as_list(list(grants)))
        narrowed_namespaces: dict[str, Any] = {}
        for namespace, requested_operations in selected.items():
            requested = set(_as_list(requested_operations))
            if not requested:
                continue
            raw_namespace, raw_policy = configured_by_name[namespace]
            raw_tools = raw_policy.get("tools")
            raw_tools = dict(raw_tools or {}) if isinstance(raw_tools, Mapping) else {}
            configured_operations: set[str] = set()
            authorized_operations: set[str] = set()
            narrowed_tools: dict[str, Any] = {}

            for tool_name, raw_tool_policy in raw_tools.items():
                if not isinstance(raw_tool_policy, Mapping):
                    continue
                tool_policy = dict(raw_tool_policy)
                operation_policies = tool_policy.get("operations")
                if isinstance(operation_policies, Mapping) and operation_policies:
                    narrowed_operations: dict[str, Any] = {}
                    for operation, raw_operation_policy in operation_policies.items():
                        operation_value = _clean(operation)
                        if not operation_value:
                            continue
                        configured_operations.add(operation_value)
                        operation_policy = (
                            dict(raw_operation_policy)
                            if isinstance(raw_operation_policy, Mapping)
                            else {}
                        )
                        required = self._operation_grants(operation_policy, tool_policy)
                        if operation_value in requested and required.issubset(available_grants):
                            narrowed_operations[operation] = copy.deepcopy(raw_operation_policy)
                            authorized_operations.add(operation_value)
                    if narrowed_operations:
                        narrowed_tool = copy.deepcopy(tool_policy)
                        narrowed_tool["operations"] = narrowed_operations
                        narrowed_tools[tool_name] = narrowed_tool
                    continue

                operation_value = _clean(tool_policy.get("operation") or tool_name)
                if not operation_value:
                    continue
                configured_operations.add(operation_value)
                required = self._operation_grants(tool_policy, {})
                if operation_value in requested and required.issubset(available_grants):
                    narrowed_tools[tool_name] = copy.deepcopy(raw_tool_policy)
                    authorized_operations.add(operation_value)

            unknown_operations = sorted(requested - configured_operations)
            if unknown_operations:
                raise ValueError(
                    f"unknown named-service operation(s) for {resource!r}/{namespace}: "
                    + ", ".join(unknown_operations)
                )
            unauthorized_operations = sorted(requested - authorized_operations)
            if unauthorized_operations:
                raise ValueError(
                    f"named-service operation(s) lack selected grants for "
                    f"{resource!r}/{namespace}: " + ", ".join(unauthorized_operations)
                )
            if narrowed_tools:
                narrowed_policy = copy.deepcopy(dict(raw_policy))
                narrowed_policy["tools"] = narrowed_tools
                narrowed_namespaces[raw_namespace] = narrowed_policy

        narrowed = copy.deepcopy(dict(config))
        narrowed["namespaces"] = narrowed_namespaces
        return narrowed

    @staticmethod
    def _merge_named_service_configs(
        target: dict[str, Any],
        source: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not target:
            return copy.deepcopy(dict(source))
        merged = copy.deepcopy(target)
        for key, value in source.items():
            if key == "namespaces" and isinstance(value, Mapping):
                namespaces = merged.setdefault("namespaces", {})
                if isinstance(namespaces, dict):
                    namespaces.update(copy.deepcopy(dict(value)))
                continue
            merged.setdefault(key, copy.deepcopy(value))
        return merged

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
            "resources": await self.resource_options(user),
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
        named_service_operations: Mapping[str, Any] | None = None,
        ttl_seconds: Any = None,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a delegated-access grant the current user grants to a client.

        ``client_id`` is normally omitted — a fresh random ``automation:…`` client
        is minted per grant. When a caller passes a DETERMINISTIC client_id (a
        hosted agent's ``kdcube-agent:<app>:<agent>`` identity), the grant is keyed
        to it and DEDUPLICATED: one record per (grantor, client, resources), so
        re-consent updates the same record instead of piling up. The credential is
        built + bound identically either way, so the minted token passes the @mcp
        guard the same as any Delegated-By-KDCube grant."""
        grantor_subject = _subject_from_user(user)
        if not grantor_subject:
            return {"ok": False, "error": "delegated_access_requires_authenticated_user"}

        selected_resource_grants = self._resource_grants(resource_grants)
        try:
            selected_named_service_operations = self._named_service_operation_selection(
                named_service_operations
            )
        except ValueError as exc:
            return {
                "ok": False,
                "error": "invalid_named_service_operation_selection",
                "message": str(exc),
            }
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
        if selected_named_service_operations is not None:
            unknown_selection_resources = sorted(
                set(selected_named_service_operations) - set(selected_resources)
            )
            if unknown_selection_resources:
                return {
                    "ok": False,
                    "error": "delegated_access_unknown_named_service_resources",
                    "resources": unknown_selection_resources,
                }
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
                if selected_named_service_operations is None:
                    selected_policy = copy.deepcopy(dict(cfg.named_services))
                else:
                    try:
                        selected_policy = self._narrow_named_service_config(
                            config=cfg.named_services,
                            selected=selected_named_service_operations.get(cfg.resource, {}),
                            grants=selected_resource_grants.get(cfg.resource, []),
                            resource=cfg.resource,
                        )
                    except ValueError as exc:
                        return {
                            "ok": False,
                            "error": "invalid_named_service_operation_selection",
                            "message": str(exc),
                        }
                named_services = self._merge_named_service_configs(
                    named_services,
                    selected_policy,
                )
        selected_operations = self._resolve_operations(
            grants=selected_grants,
            operations=_as_list(list(operations)),
            resources=selected_resources,
        )

        requested_client_id = _clean(client_id)
        access_source = ACCESS_SOURCE_MANUAL
        if requested_client_id:
            # Deterministic per-agent grant: one record per (grantor, client,
            # resources) so re-consent updates it instead of accumulating rows.
            client_id = requested_client_id
            digest = hashlib.sha256(
                f"{grantor_subject}|{client_id}|{'+'.join(sorted(selected_resources))}".encode("utf-8")
            ).hexdigest()[:16]
            access_id = f"agent-{digest}"
            access_source = ACCESS_SOURCE_AGENT
        else:
            access_id = "aut_" + secrets.token_urlsafe(10)
            client_id = f"{AUTOMATION_CLIENT_PREFIX}:{access_id}"
        ttl = _bounded_ttl(ttl_seconds)
        now = int(time.time())
        created_at = now
        if access_source == ACCESS_SOURCE_AGENT:
            existing_raw = await self._redis.get(self._record_key(access_id))
            if existing_raw is not None:
                try:
                    created_at = int(json.loads(existing_raw).get("created_at") or now)
                except Exception:
                    created_at = now
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
            named_service_operations={
                resource: {
                    namespace: tuple(operations_for_namespace)
                    for namespace, operations_for_namespace in namespaces.items()
                }
                for resource, namespaces in (
                    selected_named_service_operations or {}
                ).items()
            },
            identity_scope=identity_scope,
            session_id=session_id,
            created_at=created_at,
            expires_at=expires_at,
            last_four=access_token[-4:] if access_token else "",
            source=access_source,
            # A per-agent grant persists its token so each turn REUSES the
            # consented bearer (looked up by the resolver) rather than minting an
            # unbound one; a manual automation keeps the token client-side only.
            access_token=access_token if access_source == ACCESS_SOURCE_AGENT else "",
        )
        await self._redis.setex(self._record_key(access_id), expires_in, json.dumps(record.to_dict()))
        await self._redis.sadd(self._index_key(grantor_subject), access_id)
        await self._redis.expire(self._index_key(grantor_subject), BUNDLE_SESSION_MAX_TTL_SECONDS)
        await self.notify_change(grantor_subject, action="created", access=record.to_public_dict())

        return {
            "ok": True,
            "access": record.to_public_dict(),
            "access_token": access_token,
            "authorization_header": f"Bearer {access_token}" if access_token else "",
        }

    async def agent_access_token(
        self,
        *,
        grantor_subject: str,
        client_id: str,
        resources: Iterable[str],
    ) -> dict[str, Any] | None:
        """The consented bearer for a per-agent grant, or ``None`` when the user
        has not granted THIS agent access to these resources (consent pending) or
        the grant has expired. Keyed by the SAME deterministic access_id
        `create_access(client_id=…)` writes, so the per-turn resolver reuses the
        stored, already-bound token instead of minting an unbound one."""
        grantor = _clean(grantor_subject)
        client = _clean(client_id)
        if not grantor or not client:
            return None
        selected = sorted({_clean(r) for r in (resources or ()) if _clean(r)})
        digest = hashlib.sha256(
            f"{grantor}|{client}|{'+'.join(selected)}".encode("utf-8")
        ).hexdigest()[:16]
        raw = await self._redis.get(self._record_key(f"agent-{digest}"))
        if raw is None:
            return None
        try:
            record = AutomationAccessRecord.from_mapping(json.loads(raw))
        except Exception:
            return None
        if record.source != ACCESS_SOURCE_AGENT or not record.access_token:
            return None
        if record.expires_at and record.expires_at <= int(time.time()):
            return None
        return {
            "access_token": record.access_token,
            "authorization_header": f"Bearer {record.access_token}",
            "expires_at": record.expires_at,
            "resource_grants": {key: list(value) for key, value in record.resource_grants.items()},
            "client_id": record.client_id,
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
        await self.notify_change(grantor, action="granted", access=record.to_public_dict())
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
        await self.notify_change(grantor_subject, action="revoked", access_id=access_id_value)
        return {
            "ok": True,
            "removed": True,
            "session_removed": removed_session,
            "refresh_token_revoked": refresh_revoked,
        }

    # ------------------------- live-session delivery -------------------------

    async def register_live_session(
        self, grantor_subject: str, session_id: str, expires_at: int | float | None = None
    ) -> None:
        await register_delegated_access_live_session(
            self._redis,
            tenant=self._tenant,
            project=self._project,
            grantor_subject=grantor_subject,
            session_id=session_id,
            expires_at=expires_at,
        )

    async def notify_change(
        self,
        grantor_subject: str,
        *,
        action: str,
        access: Mapping[str, Any] | None = None,
        access_id: str = "",
    ) -> None:
        await notify_delegated_access_changed(
            self._redis,
            tenant=self._tenant,
            project=self._project,
            grantor_subject=grantor_subject,
            action=action,
            access=access,
            access_id=access_id,
        )


__all__ = [
    "ALL_RESOURCES_RESOURCE",
    "AUTOMATION_ACCESS_DEFAULT_TTL_SECONDS",
    "AUTOMATION_ACCESS_SCHEMA",
    "DELEGATED_ACCESS_CHANGED_EVENT",
    "AutomationAccessRecord",
    "AutomationAccessService",
    "notify_delegated_access_changed",
    "register_delegated_access_live_session",
]
