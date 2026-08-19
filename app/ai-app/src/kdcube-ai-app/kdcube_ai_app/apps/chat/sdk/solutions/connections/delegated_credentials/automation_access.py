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
from dataclasses import replace as replace_fields
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

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
    oauth_delegated_config_from_connections,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import (
    ACCESS_TOKEN_TTL_SECONDS,
    integration_subject,
    mint_delegated_client_access_token,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import (
    GrantStore,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.named_service_policy import (
    as_string_list,
    boundary_permits_operation,
    clean_text,
    configured_named_service_operations,
    merge_named_service_configs,
    named_service_policy_for_resource,
    narrow_named_service_config,
    operation_grants as _named_service_operation_grants,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CARD_STATE_ACTIVE,
    NAMED_SERVICE_OPERATIONS_ALL,
    CardAuthority,
    CardCredentialHandles,
    CardRecordError,
    NamedServiceSelection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
    DelegatedCardRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    subject_hash_for,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.resolver import (
    CardUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.service import (
    CardCommitFailed,
    CardConflict,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.resolver import (
    CatalogUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.drift import (
    card_drift,
    drift_unavailable,
    selected_named_service_operations,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.reconcile import (
    reconcile_selection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.authorization import (
    CAPABILITY_NAMED_SERVICE_OPERATION,
    ActiveCatalogCapabilities,
    CapabilityRequest,
    CardProvenance,
    authorize_current_capability,
    capability_denial,
    card_boundary_denial,
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


# Shared with the managed guard, which re-derives the same narrowing from the
# live card on every call.
_clean = clean_text
_as_list = as_string_list


def normalize_account_scope(value: Any) -> dict[str, dict[str, tuple[str, ...]]]:
    """Nested per-account claim binding: ``{provider: {account_id: (claims...)}}``.

    For each provider the agent may reach, this names the exact connected
    account(s) it may use AND, per account, the claims it may use on that
    account — so "read+write from account 1, read-only from account 2" is
    expressible independent of what each account is itself capable of.

    - account key ``"*"`` = any account; a claim entry ``"*"`` (or an empty
      claim list) = any claim the account supports.
    - Accepts the legacy list form ``{provider: [account_ids]}`` and migrates
      each account to ``("*",)`` (bound to those accounts for every claim), so
      existing grants keep working unchanged.
    - An absent provider key remains absent. Enforcement interprets that shape
      together with the caller identity: it is default-closed for a delegated
      caller and unrestricted only for the user's own non-delegated turn.
    """
    out: dict[str, dict[str, tuple[str, ...]]] = {}
    for provider, entry in dict(value or {}).items():
        pkey = _clean(provider)
        if not pkey:
            continue
        accounts: dict[str, tuple[str, ...]] = {}
        if isinstance(entry, Mapping):
            for account_id, claims in entry.items():
                akey = _clean(account_id)
                if not akey:
                    continue
                cl = tuple(_as_list(claims))
                accounts[akey] = cl or ("*",)
        else:
            for account_id in _as_list(entry):
                akey = _clean(account_id)
                if akey:
                    accounts[akey] = ("*",)
        if accounts:
            out[pkey] = accounts
    return out


def _subject_from_user(user: Mapping[str, Any]) -> str:
    for key in ("user_id", "sub", "id"):
        value = _clean(user.get(key))
        if value and value != "anonymous":
            return value
    return ""


def automation_record_key(tenant: str, project: str, access_id: str) -> str:
    """The registry card's Redis key — the guard resolves live against it."""
    return f"{tenant}:{project}:kdcube:delegated-access:automation:{access_id}"


def oauth_access_id(grantor_subject: str, client_id: str, resource: str = "") -> str:
    """Deterministic card id for an OAuth-flow delegated client — one card per
    (grantor, client, resource), stable across token refreshes."""
    digest = hashlib.sha256(
        f"{_clean(grantor_subject)}|{_clean(client_id)}|{_clean(resource)}".encode("utf-8")
    ).hexdigest()[:16]
    return f"oauth-{digest}"


def _subject_key(subject: str) -> str:
    return subject_hash_for(subject)


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


def agent_grant_access_id(grantor_subject: str, client_id: str, resources: Iterable[str]) -> str:
    """The deterministic record id of a per-agent grant — one record per
    (grantor, client, resources), shared by the write (`create_access`) and every
    read, so re-consent updates in place and lookups always hit the same key."""
    selected = sorted({_clean(r) for r in (resources or ()) if _clean(r)})
    digest = hashlib.sha256(
        f"{_clean(grantor_subject)}|{_clean(client_id)}|{'+'.join(selected)}".encode("utf-8")
    ).hexdigest()[:16]
    return f"agent-{digest}"


async def read_agent_grant_record(
    redis: Any,
    *,
    tenant: str,
    project: str,
    grantor_subject: str,
    client_id: str,
    resources: Iterable[str],
) -> "AutomationAccessRecord | None":
    """Read-only probe of a per-agent grant record: the parsed, unexpired record,
    or ``None`` while consent is pending. Needs only Redis + the scope — no
    delegated config — so a picker/menu enrichment can show given/pending without
    constructing the full service. The token stays server-side with the caller."""
    grantor = _clean(grantor_subject)
    client = _clean(client_id)
    if not grantor or not client:
        return None
    access_id = agent_grant_access_id(grantor, client, resources)
    cache = DelegatedCardRuntimeCache(redis, tenant=_clean(tenant), project=_clean(project))
    try:
        entry = await cache.read(access_id)
    except Exception:
        # A probe enriches a picker; it never denies on its own.
        return None
    if entry is None or not entry.is_card or entry.authority is None:
        return None
    record = record_from_card(entry.authority)
    if record.source != ACCESS_SOURCE_AGENT:
        return None
    if record.expires_at and record.expires_at <= int(time.time()):
        return None
    return record


def _selection_policy_argument(
    selection: NamedServiceSelection,
) -> dict[str, dict[str, list[str]]] | None:
    """The narrowing argument ``named_service_policy_for_resource`` expects.

    ``None`` keeps the full descriptor policy, an empty map narrows every
    resource to nothing, and an exact map narrows to its entries. A legacy
    record without an explicit selection keeps its prior full-policy meaning
    until its next successful save writes one.
    """
    if selection.is_all or selection.is_unknown:
        return None
    if selection.is_none:
        return {}
    return {
        resource: {namespace: list(values) for namespace, values in namespaces.items()}
        for resource, namespaces in selection.operations.items()
    }


def _inherited_selection(
    existing: "AutomationAccessRecord",
    grants_by_resource: Mapping[str, Iterable[str]] | None = None,
) -> NamedServiceSelection:
    """The selection an edit that never mentioned it carries forward.

    A wildcard is bound to the catalog version the card was saved against, so
    an unrelated edit must not re-pin it to the current one: it is frozen into
    the exact set it already means. The materialized boundary is that expansion
    by construction, so freezing needs no historical catalog document and works
    even when the referenced version is gone.

    An explicitly submitted ``"*"`` does not come through here — that one is
    consent to everything the current catalog shows.
    """
    selection = existing.named_service_operations
    if not selection.is_all:
        return selection
    # Filtered by the claims THIS save persists, not the ones the record used to
    # hold: a wildcard boundary is the descriptor tree unfiltered, an exact
    # selection may only name what the card can actually invoke, and an edit
    # that widens claims must not lose the namespaces those claims just opened.
    held = dict(grants_by_resource) if grants_by_resource is not None else dict(existing.resource_grants)
    frozen: dict[str, dict[str, list[str]]] = {}
    for resource, grants in held.items():
        offered = configured_named_service_operations(
            existing.named_services, grants=list(grants or ())
        )
        per_namespace = {
            namespace: sorted(operations)
            for namespace, operations in offered.items()
            if operations
        }
        if per_namespace:
            frozen[resource] = per_namespace
    if not frozen:
        return NamedServiceSelection.none()
    return NamedServiceSelection.exact(frozen)


def _materialized_has_namespaces(named_services: Any) -> bool:
    if not isinstance(named_services, Mapping):
        return False
    namespaces = named_services.get("namespaces")
    return isinstance(namespaces, Mapping) and bool(namespaces)


def _parse_named_service_selection(value: Mapping[str, Any]) -> NamedServiceSelection:
    """Read the stored selection.

    A stored ``{}`` is an explicit empty policy only when the materialized
    boundary carries no namespaces; otherwise the record predates the encoding.
    """
    try:
        selection = NamedServiceSelection.from_stored(
            value.get("named_service_operations"),
            present="named_service_operations" in value,
        )
    except CardRecordError:
        return NamedServiceSelection.unknown()
    if selection.is_none and _materialized_has_namespaces(value.get("named_services")):
        return NamedServiceSelection.unknown()
    return selection


@dataclass(frozen=True)
class AutomationAccessRecord:
    access_id: str
    label: str
    client_id: str
    grantor_subject: str
    delegate_subject: str
    operations: tuple[str, ...]
    resource_grants: Mapping[str, tuple[str, ...]]
    # Exact user-visible selection in one of four states. A newly written card
    # always carries "*", {}, or an exact map; `unknown` is a legacy-record
    # condition only.
    named_service_operations: NamedServiceSelection = field(
        default_factory=NamedServiceSelection.unknown
    )
    # Boundary tree for the named-service bridge, narrowed from the descriptor
    # by `named_service_operations`. Empty on cards written before this field;
    # the guard then keeps the bound snapshot.
    named_services: Mapping[str, Any] = field(default_factory=dict)
    # Per-agent, per-account claim binding:
    # {provider_id: {account_id: (claims...)}}. For a provider, which connected
    # account(s) this client may use AND, per account, the exact claims it may
    # use there. account "*" = any account; claim "*" (or empty) = any claim.
    # An absent provider key is default-closed for delegated callers. The
    # request boundary carries the delegated identity alongside this map so
    # enforcement can distinguish it from a non-delegated user turn.
    account_scope: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)
    identity_scope: str = ""
    # The catalog generation this card was last saved against. "*" and every
    # exact selection are defined relative to it.
    catalog_version: str = ""
    # Monotonic per-card revision; rejects concurrent lost updates.
    card_revision: int = 0
    session_id: str = ""
    created_at: int = 0
    expires_at: int = 0
    last_four: str = ""
    source: str = ACCESS_SOURCE_MANUAL
    # OAuth-flow grants keep their live token material so revoke can kill the
    # refresh token and the current access-grant binding. Never public.
    refresh_token: str = ""
    access_token: str = ""
    # Last token issuance (initial consent or refresh rotation) — staleness
    # signal: a card whose last_issued_at is old is likely an orphan (the
    # client disconnected without revoking).
    last_issued_at: int = 0

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
            named_service_operations=_parse_named_service_selection(value),
            named_services=(
                dict(value.get("named_services"))
                if isinstance(value.get("named_services"), Mapping)
                else {}
            ),
            account_scope=normalize_account_scope(value.get("account_scope")),
            identity_scope=_clean(value.get("identity_scope")),
            catalog_version=_clean(value.get("catalog_version")),
            card_revision=int(value.get("card_revision") or 0),
            session_id=_clean(value.get("session_id")),
            created_at=int(value.get("created_at") or 0),
            expires_at=int(value.get("expires_at") or 0),
            last_four=_clean(value.get("last_four")),
            source=_clean(value.get("source")) or ACCESS_SOURCE_MANUAL,
            refresh_token=_clean(value.get("refresh_token")),
            access_token=_clean(value.get("access_token")),
            last_issued_at=int(value.get("last_issued_at") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": AUTOMATION_ACCESS_SCHEMA,
            "access_id": self.access_id,
            "label": self.label,
            "client_id": self.client_id,
            "grantor_subject": self.grantor_subject,
            "delegate_subject": self.delegate_subject,
            "operations": list(self.operations),
            "resource_grants": {key: list(value) for key, value in self.resource_grants.items()},
            "named_services": dict(self.named_services or {}),
            "account_scope": {
                provider: {account_id: list(claims) for account_id, claims in accounts.items()}
                for provider, accounts in self.account_scope.items()
            },
            "identity_scope": self.identity_scope,
            "catalog_version": self.catalog_version,
            "card_revision": self.card_revision,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_four": self.last_four,
            "source": self.source,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "last_issued_at": self.last_issued_at,
        }
        stored_selection = self.named_service_operations.to_stored()
        if stored_selection is not None:
            payload["named_service_operations"] = stored_selection
        return payload

    def to_public_dict(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("session_id", None)
        payload.pop("refresh_token", None)
        payload.pop("access_token", None)
        # Derived from the descriptor and only consumed by the guard; the
        # selection (`named_service_operations`) is what surfaces render.
        payload.pop("named_services", None)
        # The selection is retained verbatim: an explicit {} must not render as
        # unrestricted, and "*" must survive a refetch.
        selection = payload.pop("named_service_operations", None)
        public = {key: value for key, value in payload.items() if value not in ("", [], {})}
        if selection is not None:
            public["named_service_operations"] = selection
        # Derived, never authority: the selection expanded under the version the
        # card was saved against. "*" and a pre-encoding record name no
        # operations, so without this a surface can only render them as an empty
        # picker — indistinguishable from an explicit {}.
        effective = {
            resource: {
                namespace: sorted(operations)
                for namespace, operations in namespaces.items()
                if operations
            }
            for resource, namespaces in selected_named_service_operations(self).items()
        }
        effective = {resource: rows for resource, rows in effective.items() if rows}
        if effective:
            public["effective_named_service_operations"] = effective
        return public


def card_authority_from_record(record: AutomationAccessRecord) -> CardAuthority:
    """The record's non-secret authorization decision."""
    return CardAuthority(
        access_id=record.access_id,
        client_id=record.client_id,
        grantor_subject=record.grantor_subject,
        delegate_subject=record.delegate_subject,
        source=record.source,
        label=record.label,
        card_revision=record.card_revision,
        catalog_version=record.catalog_version,
        state=CARD_STATE_ACTIVE,
        operations=tuple(record.operations),
        resource_grants={key: tuple(value) for key, value in record.resource_grants.items()},
        named_service_operations=record.named_service_operations,
        named_services=copy.deepcopy(dict(record.named_services or {})),
        account_scope={
            provider: {account_id: tuple(claims) for account_id, claims in accounts.items()}
            for provider, accounts in record.account_scope.items()
        },
        identity_scope=record.identity_scope,
        created_at=record.created_at,
        expires_at=record.expires_at,
        last_issued_at=record.last_issued_at,
        last_four=record.last_four,
    )


def card_handles_from_record(record: AutomationAccessRecord) -> CardCredentialHandles:
    """The record's live, reusable credential material."""
    return CardCredentialHandles(
        access_id=record.access_id,
        access_token=record.access_token,
        refresh_token=record.refresh_token,
        session_id=record.session_id,
    )


def record_from_card(
    authority: CardAuthority,
    handles: CardCredentialHandles | None = None,
) -> AutomationAccessRecord:
    """Recombine authority and handles into the record shape callers render."""
    held = handles or CardCredentialHandles(access_id=authority.access_id)
    return AutomationAccessRecord(
        access_id=authority.access_id,
        label=authority.label,
        client_id=authority.client_id,
        grantor_subject=authority.grantor_subject,
        delegate_subject=authority.delegate_subject,
        operations=tuple(authority.operations),
        resource_grants={key: tuple(value) for key, value in authority.resource_grants.items()},
        named_service_operations=authority.named_service_operations,
        named_services=copy.deepcopy(dict(authority.named_services or {})),
        account_scope={
            provider: {account_id: tuple(claims) for account_id, claims in accounts.items()}
            for provider, accounts in authority.account_scope.items()
        },
        identity_scope=authority.identity_scope,
        catalog_version=authority.catalog_version,
        card_revision=authority.card_revision,
        session_id=held.session_id,
        created_at=authority.created_at,
        expires_at=authority.expires_at,
        last_four=authority.last_four,
        source=authority.source,
        refresh_token=held.refresh_token,
        access_token=held.access_token,
        last_issued_at=authority.last_issued_at,
    )


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
        catalog_resolver: Any | None = None,
        card_persistence: Any | None = None,
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
        # Required by the operations that stamp a card. Read-only and
        # credential-lifecycle operations do not need it.
        self._catalog_resolver = catalog_resolver
        # Policy depends on the persistence contract, not on how a card is
        # stored. Composition of the durable implementation belongs to the
        # caller that owns storage.
        self._persistence = card_persistence

    # -- card persistence -----------------------------------------------------
    #
    # Durable revisions are the source of truth; Redis holds the live
    # projection and the bounded credential handles. Every raw record access
    # goes through these three operations.

    def _cards(self) -> Any:
        if self._persistence is None:
            raise CardUnavailable("card_persistence_not_configured")
        return self._persistence

    async def _load_record(
        self, access_id: str, *, grantor_subject: str
    ) -> AutomationAccessRecord | None:
        loaded = await self._cards().load(
            access_id, subject_hash=_subject_key(grantor_subject)
        )
        if loaded is None:
            return None
        authority, handles = loaded
        return record_from_card(authority, handles)

    async def _persist_record(
        self, record: AutomationAccessRecord, *, expected_revision: int
    ) -> None:
        await self._cards().persist(
            card_authority_from_record(record),
            card_handles_from_record(record),
            subject_hash=_subject_key(record.grantor_subject),
            expected_revision=expected_revision,
        )

    async def _forget_record(self, record: AutomationAccessRecord) -> None:
        await self._cards().forget(
            card_authority_from_record(record),
            subject_hash=_subject_key(record.grantor_subject),
        )

    async def _list_active_records(
        self, grantor_subject: str, *, now: int | None = None
    ) -> list[AutomationAccessRecord]:
        authorities = await self._cards().list_active(
            subject_hash=_subject_key(grantor_subject), now=now
        )
        return [record_from_card(authority) for authority in authorities]

    async def _save_precondition_conflict(
        self,
        *,
        existing: AutomationAccessRecord,
        active: Any,
        expected_card_revision: int | None,
        expected_catalog_version: str | None,
    ) -> dict[str, Any] | None:
        """``409`` with a refreshed projection when the editor's inputs moved.

        Both preconditions are optional; a caller that sends neither keeps the
        previous last-writer-wins behaviour.
        """
        mismatched: dict[str, Any] = {}
        if expected_card_revision is not None and int(expected_card_revision) != int(
            existing.card_revision
        ):
            mismatched["card_revision"] = {
                "expected": int(expected_card_revision),
                "actual": int(existing.card_revision),
            }
        expected_version = _clean(expected_catalog_version)
        if expected_version and expected_version != _clean(getattr(active, "version", "")):
            mismatched["catalog_version"] = {
                "expected": expected_version,
                "actual": _clean(getattr(active, "version", "")),
            }
        if not mismatched:
            return None

        refreshed = existing.to_public_dict()
        try:
            baseline, reason = await self._baseline_document(_clean(existing.catalog_version))
            refreshed["catalog_drift"] = (
                drift_unavailable(reason)
                if reason
                else card_drift(
                    card=existing,
                    active=active,
                    baseline=baseline,
                    baseline_confirmed_absent=baseline is None,
                )
            )
        except Exception:
            _LOGGER.warning(
                "[automation-access] drift for conflict response failed card=%s",
                existing.access_id,
                exc_info=True,
            )
            refreshed["catalog_drift"] = drift_unavailable("catalog_unavailable")
        return {
            "ok": False,
            "error": "delegated_access_precondition_failed",
            "status": 409,
            "mismatched": mismatched,
            "access": refreshed,
        }

    async def _catalog_drift(
        self, records: Iterable[AutomationAccessRecord]
    ) -> dict[str, dict[str, Any]]:
        """Drift per card: one active read, and one read per distinct baseline.

        Listing explains cards; it never rewrites them, and an unreadable
        comparison disables editing rather than hiding the card.
        """
        rows = list(records)
        try:
            active = await self._active_catalog()
        except CatalogUnavailable as exc:
            return {record.access_id: drift_unavailable(exc.reason) for record in rows}
        except Exception:
            _LOGGER.warning("[automation-access] active catalog unreadable", exc_info=True)
            return {
                record.access_id: drift_unavailable("catalog_unavailable") for record in rows
            }

        baselines: dict[str, tuple[Any, str]] = {}
        out: dict[str, dict[str, Any]] = {}
        for record in rows:
            version = _clean(record.catalog_version)
            if version and version == active.version:
                out[record.access_id] = card_drift(
                    card=record, active=active, baseline=active
                )
                continue
            if version not in baselines:
                baselines[version] = await self._baseline_document(version)
            document, reason = baselines[version]
            if reason:
                out[record.access_id] = drift_unavailable(reason)
                continue
            out[record.access_id] = card_drift(
                card=record,
                active=active,
                baseline=document,
                baseline_confirmed_absent=document is None,
            )
        return out

    async def _baseline_document(self, version: str) -> tuple[Any, str]:
        """``(document, reason)``. No document and no reason is confirmed absence."""
        if not version:
            return None, ""
        try:
            return await self._catalog_resolver.resolve_version(version), ""
        except CatalogUnavailable as exc:
            return None, (exc.reason or "catalog_unavailable")
        except Exception:
            _LOGGER.warning(
                "[automation-access] baseline unreadable version=%s", version, exc_info=True
            )
            return None, "catalog_unavailable"

    async def _active_catalog(self):
        """The registered catalog a governed decision is taken against."""
        if self._catalog_resolver is None:
            raise CatalogUnavailable("catalog_resolver_not_configured")
        return await self._catalog_resolver.resolve_active()

    async def _active_catalog_version(self) -> str:
        """The generation a save is stamped with.

        A card may not be written without naming the catalog its selection is
        defined against, so an absent resolver fails closed rather than
        producing an unstamped record.
        """
        if self._catalog_resolver is None:
            raise CatalogUnavailable("catalog_resolver_not_configured")
        active = await self._catalog_resolver.resolve_active()
        version = _clean(getattr(active, "version", ""))
        if not version:
            raise CatalogUnavailable("active_catalog_version_missing")
        return version

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
        value: Any,
    ) -> NamedServiceSelection | None:
        """Parse a submitted selection. ``None`` means the field was omitted."""
        if value is None:
            return None
        if isinstance(value, str):
            if _clean(value) != NAMED_SERVICE_OPERATIONS_ALL:
                raise ValueError("named_service_operations must be '*' or an object")
            return NamedServiceSelection.all()
        if not isinstance(value, Mapping):
            raise ValueError("named_service_operations must be an object")
        for resource, raw_namespaces in value.items():
            if _clean(resource) and not isinstance(raw_namespaces, Mapping):
                raise ValueError(
                    f"named_service_operations[{_clean(resource)!r}] must be an object"
                )
        try:
            return NamedServiceSelection.exact(value)
        except CardRecordError as exc:
            raise ValueError(str(exc)) from exc

    # Delegators; the managed guard calls the same functions per request.
    @staticmethod
    def _operation_grants(policy: Mapping[str, Any], fallback: Mapping[str, Any]) -> set[str]:
        return _named_service_operation_grants(policy, fallback)

    def _narrow_named_service_config(
        self,
        *,
        config: Mapping[str, Any],
        selected: Mapping[str, list[str]],
        grants: Iterable[str],
        resource: str,
    ) -> dict[str, Any]:
        return narrow_named_service_config(
            config=config, selected=selected, grants=grants, resource=resource,
        )

    @staticmethod
    def _merge_named_service_configs(
        target: dict[str, Any],
        source: Mapping[str, Any],
    ) -> dict[str, Any]:
        return merge_named_service_configs(target, source)

    async def list_access(self, user: Mapping[str, Any]) -> dict[str, Any]:
        grantor_subject = _subject_from_user(user)
        if not grantor_subject:
            return {"ok": False, "error": "delegated_access_requires_authenticated_user"}

        now = int(time.time())
        try:
            records_found = await self._list_active_records(grantor_subject, now=now)
        except CardUnavailable as exc:
            return {
                "ok": False,
                "error": "delegated_cards_unavailable",
                "reason": exc.reason,
                "retryable": True,
                "status": 503,
            }
        drift_by_card = await self._catalog_drift(records_found)
        records = []
        for record in records_found:
            item = record.to_public_dict()
            item["catalog_drift"] = drift_by_card[record.access_id]
            records.append(item)

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
        named_service_operations: Mapping[str, Any] | str | None = None,
        account_scope: Mapping[str, Any] | None = None,
        ttl_seconds: Any = None,
        client_id: str | None = None,
        merge_existing: bool = True,
    ) -> dict[str, Any]:
        """Create a delegated-access grant the current user grants to a client.

        ``client_id`` is normally omitted — a fresh random ``automation:…`` client
        is minted per grant. When a caller passes a DETERMINISTIC client_id (a
        hosted agent's ``kdcube-agent:<app>:<agent>`` identity), the grant is keyed
        to it and DEDUPLICATED: one record per (grantor, client, resources).
        With ``merge_existing`` (the default) a re-grant MERGES claims and
        narrowing into the record — sequential one-click grants accumulate.
        ``merge_existing=False`` is the EDIT semantics: the submitted selection
        REPLACES the record exactly (the user unchecked something). The
        credential is built + bound identically either way, so the minted token
        passes the @mcp guard the same as any Delegated-By-KDCube grant."""
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

        # The resource FIRST: a claim for an endpoint this deployment never put
        # in the hub's catalog is not "a claim you may not delegate" — it is an
        # endpoint nobody here has decided to expose, and the two need different
        # answers. The hub's catalog is a SUBSET of what apps declare: an app
        # states what its surface can delegate, this deployment decides how much
        # of that may be asked for here, and until it says so the answer is no.
        try:
            resource_configs = self._configured_resources(selected_resources) if self._config.resources else ()
        except ValueError:
            return {
                "ok": False,
                "error": "delegated_access_unknown_resources",
                "resources": selected_resources,
                "message": (
                    "This deployment has not made that endpoint delegable. Add it to "
                    "connection-hub@1-0 `connections.delegated_credentials.oauth.resources`, "
                    "with the tools it may grant, before access to it can be asked for."
                ),
            }

        inventory = await self._available_inventory(user, requested_grants=selected_grants)
        available = set(inventory.grant_names())
        denied = [grant for grant in selected_grants if grant not in available]
        if denied:
            return {
                "ok": False,
                "error": "delegated_access_grants_not_delegable",
                "grants": denied,
                "message": (
                    "These permissions are not among the ones this deployment allows for "
                    "the endpoint. They are configured in connection-hub@1-0 "
                    "`connections.delegated_credentials.oauth.resources`, per tool."
                ),
            }
        admin_required = [cfg.resource for cfg in resource_configs if cfg.admin_only]
        if admin_required and not _is_platform_admin(user):
            return {
                "ok": False,
                "error": "delegated_access_resource_requires_admin",
                "resources": admin_required,
            }
        cfg_by_resource = {cfg.resource: cfg for cfg in resource_configs}
        if selected_named_service_operations is not None and selected_named_service_operations.is_exact:
            unknown_selection_resources = sorted(
                set(selected_named_service_operations.operations) - set(selected_resources)
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

        # Per-agent, per-account claim binding: {provider: {account_id: [claims]}}.
        account_scope_provided = account_scope is not None
        selected_account_scope: dict[str, dict[str, list[str]]] = {
            provider: {account_id: list(claims) for account_id, claims in accounts.items()}
            for provider, accounts in normalize_account_scope(account_scope).items()
        }

        requested_client_id = _clean(client_id)
        access_source = ACCESS_SOURCE_MANUAL
        created_at_override: int | None = None
        existing: AutomationAccessRecord | None = None
        if requested_client_id:
            # Deterministic per-agent grant: one record per (grantor, client,
            # resources). Re-consent MERGES into it — sequential one-click
            # grants on the same resource (memories today, slack tomorrow)
            # accumulate; a replace would silently revoke the earlier consent.
            client_id = requested_client_id
            access_id = agent_grant_access_id(grantor_subject, client_id, selected_resources)
            access_source = ACCESS_SOURCE_AGENT
            try:
                existing = await self._load_record(
                    access_id, grantor_subject=grantor_subject
                )
            except CardUnavailable as exc:
                return {
                    "ok": False,
                    "error": "delegated_cards_unavailable",
                    "reason": exc.reason,
                    "retryable": True,
                    "status": 503,
                }
            if existing is not None:
                created_at_override = existing.created_at or None
                if not merge_existing and not account_scope_provided:
                    # Replace only dimensions the caller actually submitted.
                    # Omitted account_scope preserves the current binding;
                    # an explicit {} below clears it.
                    selected_account_scope = {
                        provider: {
                            account_id: list(claims)
                            for account_id, claims in accounts.items()
                        }
                        for provider, accounts in existing.account_scope.items()
                    }
                if not merge_existing and selected_named_service_operations is None:
                    # Same rule for the namespace narrowing: omitting it keeps
                    # the record's own state, including an explicit empty one.
                    selected_named_service_operations = _inherited_selection(
                        existing, selected_resource_grants
                    )
            if existing is not None and merge_existing:
                for resource_key, held in existing.resource_grants.items():
                    merged = list(selected_resource_grants.get(resource_key, []))
                    for grant in held:
                        if grant not in merged:
                            merged.append(grant)
                    selected_resource_grants[resource_key] = merged
                selected_grants = _as_list([
                    grant
                    for grants_for_resource in selected_resource_grants.values()
                    for grant in grants_for_resource
                ])
                # The card's own side is frozen before it is merged into: a
                # consent screen names a door and its claims, never the inner
                # namespaces, so it is not the reviewed explicit "*" that may
                # re-pin. Computed after the claim merge above, because the
                # freeze is filtered by the claims THIS save persists.
                inherited = _inherited_selection(existing, selected_resource_grants)
                if selected_named_service_operations is None:
                    selected_named_service_operations = inherited
                else:
                    # A one-click extension accumulates: the merged boundary is
                    # the wider of what the card holds and what was submitted.
                    selected_named_service_operations = (
                        selected_named_service_operations.union(inherited)
                    )
                # Merge the account binding per provider AND per account: union
                # the claim lists (a one-click grant accumulates; a REPLACE edit
                # sends the full desired scope and overwrites, same as
                # resource_grants).
                for provider, held_accounts in existing.account_scope.items():
                    target_accounts = selected_account_scope.setdefault(provider, {})
                    for account_id, held_claims in held_accounts.items():
                        merged_claims = list(target_accounts.get(account_id, []))
                        for claim in held_claims:
                            if claim not in merged_claims:
                                merged_claims.append(claim)
                        target_accounts[account_id] = merged_claims
        else:
            access_id = "aut_" + secrets.token_urlsafe(10)
            client_id = f"{AUTOMATION_CLIENT_PREFIX}:{access_id}"

        # A create call that names no selection grants the full policy of the
        # catalog it is saved against, and a legacy card resolves to the same
        # explicit state on its next save.
        if (
            selected_named_service_operations is None
            or selected_named_service_operations.is_unknown
        ):
            selected_named_service_operations = NamedServiceSelection.all()
        try:
            catalog_version = await self._active_catalog_version()
        except CatalogUnavailable as exc:
            return {
                "ok": False,
                "error": "delegated_catalog_unavailable",
                "reason": exc.reason,
                "retryable": True,
                "status": 503,
            }

        named_services: dict[str, Any] = {}
        for cfg in resource_configs:
            if isinstance(cfg.named_services, Mapping):
                try:
                    selected_policy = named_service_policy_for_resource(
                        named_services=cfg.named_services,
                        resource=cfg.resource,
                        selection=_selection_policy_argument(selected_named_service_operations),
                        grants=selected_resource_grants.get(cfg.resource, []),
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

        ttl = _bounded_ttl(ttl_seconds)
        now = int(time.time())
        created_at = created_at_override or now
        credential = build_delegated_client_credential(
            grantor_subject=grantor_subject,
            client_id=client_id,
            scopes=selected_grants,
            operations=selected_operations,
            tenant=self._tenant,
            project=self._project,
            resource_grants=selected_resource_grants,
            account_scope=selected_account_scope,
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
            # The card is the authority: this binding is a POINTER onto it, so
            # the guard resolves the card live (grants, resource_grants,
            # account_scope) and an edit applies to the reused agent bearer on
            # its very next call — not only after a re-mint. Same mechanism
            # OAuth clients use; makes card-authority universal.
            registry_access_id=access_id,
        )

        record = AutomationAccessRecord(
            access_id=access_id,
            label=_clean(label) or "Automation access",
            client_id=client_id,
            grantor_subject=grantor_subject,
            delegate_subject=integration_subject(grantor_subject, client_id=client_id),
            operations=tuple(selected_operations),
            resource_grants={key: tuple(value) for key, value in selected_resource_grants.items()},
            named_service_operations=selected_named_service_operations,
            named_services=copy.deepcopy(named_services),
            account_scope={
                provider: {account_id: tuple(claims) for account_id, claims in accounts.items()}
                for provider, accounts in selected_account_scope.items()
            },
            identity_scope=identity_scope,
            catalog_version=catalog_version,
            card_revision=(existing.card_revision + 1) if existing is not None else 1,
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
        try:
            await self._persist_record(
                record, expected_revision=existing.card_revision if existing is not None else 0
            )
        except (CardUnavailable, CardConflict, CardCommitFailed) as exc:
            return {
                "ok": False,
                "error": "delegated_card_not_committed",
                "reason": getattr(exc, "reason", ""),
                "retryable": True,
                "status": 503,
            }
        await self.notify_change(grantor_subject, action="created", access=record.to_public_dict())

        return {
            "ok": True,
            "access": record.to_public_dict(),
            "access_token": access_token,
            "authorization_header": f"Bearer {access_token}" if access_token else "",
        }

    async def update_access(
        self,
        user: Mapping[str, Any],
        *,
        access_id: str,
        resource_grants: Mapping[str, Any],
        named_service_operations: Mapping[str, Any] | str | None = None,
        account_scope: Mapping[str, Any] | None = None,
        label: str | None = None,
        expected_card_revision: int | None = None,
        expected_catalog_version: str | None = None,
    ) -> dict[str, Any]:
        """Edit a MANUAL automation access IN PLACE: replace its grants while
        keeping the same access_id and token. The card is the guard's authority
        (resolved live via resolve_live_grant_card), so the change applies to the
        client's existing bearer on its very next call — no re-mint. A manual
        token is client-side only, so it is never touched; only the card record
        is rewritten. The submitted selection REPLACES the record exactly."""
        grantor_subject = _subject_from_user(user)
        if not grantor_subject:
            return {"ok": False, "error": "delegated_access_requires_authenticated_user"}
        access_id = _clean(access_id)
        if not access_id:
            return {"ok": False, "error": "delegated_access_requires_access_id"}
        try:
            existing = await self._load_record(access_id, grantor_subject=grantor_subject)
        except CardUnavailable as exc:
            return {
                "ok": False,
                "error": "delegated_cards_unavailable",
                "reason": exc.reason,
                "retryable": True,
                "status": 503,
            }
        if existing is None:
            return {"ok": False, "error": "delegated_access_not_found"}
        if existing.grantor_subject != grantor_subject:
            return {"ok": False, "error": "delegated_access_not_owned"}
        # Only a manual automation card is edited here. Agent/OAuth cards edit
        # through their own consent flows (delegated_agent_grant_create).
        if existing.source != ACCESS_SOURCE_MANUAL:
            return {"ok": False, "error": "delegated_access_not_editable"}

        try:
            active = await self._active_catalog()
        except CatalogUnavailable as exc:
            return {
                "ok": False,
                "error": "delegated_catalog_unavailable",
                "reason": exc.reason,
                "retryable": True,
                "status": 503,
            }
        catalog_version = _clean(getattr(active, "version", ""))
        if not catalog_version:
            return {
                "ok": False,
                "error": "delegated_catalog_unavailable",
                "reason": "active_catalog_version_missing",
                "retryable": True,
                "status": 503,
            }
        conflict = await self._save_precondition_conflict(
            existing=existing,
            active=active,
            expected_card_revision=expected_card_revision,
            expected_catalog_version=expected_catalog_version,
        )
        if conflict is not None:
            return conflict

        # Validate the new grants with the SAME rules as create_access.
        selected_resource_grants = self._resource_grants(resource_grants)
        try:
            selected_named_service_operations = self._named_service_operation_selection(
                named_service_operations
            )
        except ValueError as exc:
            return {"ok": False, "error": "invalid_named_service_operation_selection", "message": str(exc)}
        # Resolved before pruning: pruning acts on the selection about to be
        # persisted. Omitted keeps the record's own state; legacy becomes "*".
        if selected_named_service_operations is None:
            selected_named_service_operations = _inherited_selection(
                existing, selected_resource_grants
            )
        if selected_named_service_operations.is_unknown:
            selected_named_service_operations = NamedServiceSelection.all()

        # Submitting nothing is a client error; pruning to nothing is a revoke.
        if not any(selected_resource_grants.values()):
            return {"ok": False, "error": "delegated_access_requires_resource_grants"}

        # Values absent from the active catalog are pruned, not rejected.
        reconciled = reconcile_selection(
            resource_grants=selected_resource_grants,
            named_service_operations=selected_named_service_operations,
            active=active,
        )
        if reconciled.empty:
            # No authority survives: revoke rather than keep an empty card.
            revoked = await self.revoke_access(user, access_id=access_id)
            if not revoked.get("ok"):
                return revoked
            return {
                "ok": True,
                "revoked": True,
                "access_id": access_id,
                "pruned": reconciled.to_public_dict(),
                "message": (
                    "Every selection on this card was withdrawn from the delegated-service "
                    "catalog, so the card was revoked instead of saved."
                ),
            }
        selected_resource_grants = reconciled.resource_grants
        selected_named_service_operations = reconciled.named_service_operations
        selected_resources = list(selected_resource_grants)
        if self._config.resources and not selected_resources:
            return {"ok": False, "error": "delegated_access_requires_resource_grants"}
        selected_grants = _as_list([
            grant
            for grants_for_resource in selected_resource_grants.values()
            for grant in grants_for_resource
        ])
        if not selected_grants:
            # Removing everything is a revoke, not an edit.
            return {"ok": False, "error": "delegated_access_requires_resource_grants"}
        # Same order as create_access: an endpoint this deployment never made
        # delegable is a different answer from a permission it will not delegate.
        try:
            resource_configs = self._configured_resources(selected_resources) if self._config.resources else ()
        except ValueError:
            return {
                "ok": False,
                "error": "delegated_access_unknown_resources",
                "resources": selected_resources,
                "message": (
                    "This deployment has not made that endpoint delegable. Add it to "
                    "connection-hub@1-0 `connections.delegated_credentials.oauth.resources`, "
                    "with the tools it may grant, before access to it can be asked for."
                ),
            }
        inventory = await self._available_inventory(user, requested_grants=selected_grants)
        denied = [grant for grant in selected_grants if grant not in set(inventory.grant_names())]
        if denied:
            return {
                "ok": False,
                "error": "delegated_access_grants_not_delegable",
                "grants": denied,
                "message": (
                    "These permissions are not among the ones this deployment allows for "
                    "the endpoint. They are configured in connection-hub@1-0 "
                    "`connections.delegated_credentials.oauth.resources`, per tool."
                ),
            }
        admin_required = [cfg.resource for cfg in resource_configs if cfg.admin_only]
        if admin_required and not _is_platform_admin(user):
            return {"ok": False, "error": "delegated_access_resource_requires_admin", "resources": admin_required}
        cfg_by_resource = {cfg.resource: cfg for cfg in resource_configs}
        if selected_named_service_operations is not None and selected_named_service_operations.is_exact:
            unknown = sorted(
                set(selected_named_service_operations.operations) - set(selected_resources)
            )
            if unknown:
                return {"ok": False, "error": "delegated_access_unknown_named_service_resources", "resources": unknown}
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
            _clean(getattr(cfg, "identity_scope", "") or "grantor") for cfg in resource_configs
        }
        if len(identity_scopes) > 1:
            return {
                "ok": False,
                "error": "delegated_access_resources_have_conflicting_identity_scopes",
                "resources": selected_resources,
            }
        # Recompute the boundary tree here: the descriptor is available in this
        # process, the guard's is not.
        named_services: dict[str, Any] = {}
        for cfg in resource_configs:
            if not isinstance(cfg.named_services, Mapping):
                continue
            try:
                selected_policy = named_service_policy_for_resource(
                    named_services=cfg.named_services,
                    resource=cfg.resource,
                    # Same value the record stores below, so the tree and the
                    # selection it was derived from cannot disagree.
                    selection=_selection_policy_argument(selected_named_service_operations),
                    grants=selected_resource_grants.get(cfg.resource, []),
                )
            except ValueError as exc:
                return {"ok": False, "error": "invalid_named_service_operation_selection", "message": str(exc)}
            named_services = self._merge_named_service_configs(named_services, selected_policy)
        selected_operations = self._resolve_operations(
            grants=selected_grants, operations=(), resources=selected_resources,
        )
        if account_scope is None:
            selected_account_scope = {
                provider: {account_id: list(claims) for account_id, claims in accounts.items()}
                for provider, accounts in existing.account_scope.items()
            }
        else:
            selected_account_scope = {
                provider: {account_id: list(claims) for account_id, claims in accounts.items()}
                for provider, accounts in normalize_account_scope(account_scope).items()
            }

        now = int(time.time())
        if existing.expires_at <= now:
            return {"ok": False, "error": "delegated_access_expired"}
        remaining = max(1, int(existing.expires_at) - now)
        updated = AutomationAccessRecord(
            access_id=existing.access_id,
            label=_clean(label) if label else existing.label,
            client_id=existing.client_id,
            grantor_subject=existing.grantor_subject,
            delegate_subject=existing.delegate_subject,
            operations=tuple(selected_operations),
            resource_grants={key: tuple(value) for key, value in selected_resource_grants.items()},
            named_service_operations=selected_named_service_operations,
            named_services=copy.deepcopy(named_services),
            account_scope={
                provider: {account_id: tuple(claims) for account_id, claims in accounts.items()}
                for provider, accounts in selected_account_scope.items()
            },
            identity_scope=next(iter(identity_scopes), existing.identity_scope or "grantor"),
            catalog_version=catalog_version,
            card_revision=existing.card_revision + 1,
            session_id=existing.session_id,
            created_at=existing.created_at,
            expires_at=existing.expires_at,
            last_four=existing.last_four,
            source=existing.source,
            # Manual token stays client-side only; the record never holds it.
            access_token="",
        )
        del remaining
        try:
            await self._persist_record(updated, expected_revision=existing.card_revision)
        except (CardUnavailable, CardConflict, CardCommitFailed) as exc:
            return {
                "ok": False,
                "error": "delegated_card_not_committed",
                "reason": getattr(exc, "reason", ""),
                "retryable": True,
                "status": 503,
            }
        await self.notify_change(grantor_subject, action="updated", access=updated.to_public_dict())
        saved = updated.to_public_dict()
        saved["catalog_drift"] = card_drift(card=updated, active=active, baseline=active)
        return {"ok": True, "access": saved, "pruned": reconciled.to_public_dict()}

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
        record = await self._load_record(
            agent_grant_access_id(grantor_subject, client_id, resources),
            grantor_subject=grantor_subject,
        )
        if record is None or record.source != ACCESS_SOURCE_AGENT:
            return None
        if not record.access_token:
            return None
        return {
            "access_token": record.access_token,
            "authorization_header": f"Bearer {record.access_token}",
            "expires_at": record.expires_at,
            "resource_grants": {key: list(value) for key, value in record.resource_grants.items()},
            "client_id": record.client_id,
        }

    async def agent_namespace_grant_state(
        self,
        *,
        grantor_subject: str,
        client_id: str,
        namespace: str,
        operation: str,
    ) -> dict[str, Any]:
        """Whether an agent client holds the delegated-by grant a NATIVE
        named-service call needs.

        The ceiling is the registered catalog, not live props, so this answers
        the same question the managed guard answers for the MCP door: the
        catalog resource that publishes ``namespace``, the operation's declared
        grants plus the resource's entry grants, intersected with the agent's
        card.

        Outcomes:

            {"governed": False}                nothing publishes the namespace
            {"governed": True, "granted": …}   the catalog offers it
            {"removed": <structured denial>}   the card holds it, the catalog
                                               no longer offers it
            CatalogUnavailable                 current authority is unknown

        A refusal says WHICH side refused: ``missing_claims`` when the card
        lacks the claims, ``not_granted`` (a structured card-side denial) when
        it holds them and only its own boundary excludes the operation. Without
        that, a caller reads every refusal as "ask for consent" and loops on
        claims the card already carries.
        """
        ns = _clean(namespace).lower().rstrip(":")
        op = _clean(operation)
        if not ns or not op:
            return {"governed": False}
        active = await self._active_catalog()
        catalog = ActiveCatalogCapabilities(active)
        for cfg in oauth_delegated_config_from_connections(active.connections).resources:
            named = cfg.named_services if isinstance(cfg.named_services, Mapping) else None
            raw_namespaces = named.get("namespaces") if named else None
            if not isinstance(raw_namespaces, Mapping):
                continue
            policy = None
            for raw_ns, raw_policy in raw_namespaces.items():
                if _clean(raw_ns).lower().rstrip(":") == ns and isinstance(raw_policy, Mapping):
                    policy = raw_policy
                    break
            if policy is None:
                continue
            # The common MCP entry requirement = the grants of the resource's
            # generic tools (e.g. named_services:use) — NOT `cfg.grants`, which
            # is the resource's full scope ceiling.
            required: set[str] = set()
            for tool_cfg in cfg.tools or ():
                required |= set(_as_list(list(getattr(tool_cfg, "grants", ()) or ())))
            raw_tools = policy.get("tools")
            for tool_policy in (raw_tools or {}).values() if isinstance(raw_tools, Mapping) else ():
                if not isinstance(tool_policy, Mapping):
                    continue
                operation_policies = tool_policy.get("operations")
                if isinstance(operation_policies, Mapping) and operation_policies:
                    for op_name, op_policy in operation_policies.items():
                        if _clean(op_name) == op:
                            required |= self._operation_grants(
                                dict(op_policy) if isinstance(op_policy, Mapping) else {},
                                dict(tool_policy),
                            )
                    continue
                if _clean(tool_policy.get("operation") or "") == op:
                    required |= self._operation_grants({}, dict(tool_policy))
            # A service method reads through its own persistence port; the
            # standalone probe exists for callers that have no service.
            record = await self._load_record(
                agent_grant_access_id(grantor_subject, client_id, [cfg.resource]),
                grantor_subject=grantor_subject,
            )
            if record is not None and record.source != ACCESS_SOURCE_AGENT:
                record = None
            # The namespace survives, but the operation under it may not. A
            # capability the catalog no longer offers is refused outright —
            # consent cannot restore it.
            removed = authorize_current_capability(
                catalog=catalog,
                provenance=CardProvenance(
                    access_id=record.access_id if record is not None else "",
                    card_revision=record.card_revision if record is not None else 0,
                    catalog_version=record.catalog_version if record is not None else "",
                ),
                request=CapabilityRequest(
                    kind=CAPABILITY_NAMED_SERVICE_OPERATION,
                    resource=cfg.resource,
                    surface="named_service",
                    namespace=ns,
                    operation=op,
                ),
            )
            if removed is not None:
                return {"governed": True, "granted": False, "removed": removed}
            granted = False
            missing_claims: list[str] = sorted(required)
            not_granted: dict[str, Any] | None = None
            if record is not None:
                held = set(record.resource_grants.get(cfg.resource, ()))
                missing_claims = sorted(required - held)
                granted = not missing_claims
                if granted:
                    # The materialized boundary is the card's answer, the same
                    # tree the named-services door enforces. Reading the
                    # selection instead loses the wildcard, whose meaning is
                    # "everything the acknowledged catalog offered" — an
                    # expansion that lives in the boundary, not in the intent.
                    # A pre-encoding record carries no boundary and keeps its
                    # claims-only compatibility answer.
                    if not record.named_service_operations.is_unknown:
                        granted = boundary_permits_operation(
                            record.named_services, namespace=ns, operation=op
                        )
                        if not granted:
                            # The claims are held and the catalog offers this;
                            # only the card's own boundary excludes it. Saying
                            # so is what keeps the caller out of a consent loop
                            # for claims it already has.
                            not_granted = card_boundary_denial(
                                provenance=CardProvenance(
                                    access_id=record.access_id,
                                    card_revision=record.card_revision,
                                    catalog_version=record.catalog_version,
                                ),
                                request=CapabilityRequest(
                                    kind=CAPABILITY_NAMED_SERVICE_OPERATION,
                                    resource=cfg.resource,
                                    surface="named_service",
                                    namespace=ns,
                                    operation=op,
                                ),
                            )
            return {
                "governed": True,
                "granted": granted,
                "not_granted": not_granted,
                "missing_claims": missing_claims,
                "resource": cfg.resource,
                "claims": sorted(required),
                "client_id": client_id,
                # The agent's per-account claim binding, so the native gate can
                # bind it for the connected-account resolver (which account +
                # which claims this agent may use per provider). Empty => any.
                "account_scope": {
                    provider: {account_id: list(claims) for account_id, claims in accounts.items()}
                    for provider, accounts in (record.account_scope.items() if record is not None else ())
                },
            }
        # Nothing in the active catalog publishes the namespace. A card that
        # still carries it is a removed capability, not an ungoverned call: the
        # user cannot consent their way back to something the deployment no
        # longer offers.
        removed = await self._removed_namespace_denial(
            catalog=catalog,
            grantor_subject=grantor_subject,
            client_id=client_id,
            namespace=ns,
            operation=op,
        )
        if removed is not None:
            return {"governed": True, "granted": False, "removed": removed}
        return {"governed": False}

    async def _removed_namespace_denial(
        self,
        *,
        catalog: "ActiveCatalogCapabilities",
        grantor_subject: str,
        client_id: str,
        namespace: str,
        operation: str,
    ) -> dict[str, Any] | None:
        """The structured denial for a namespace this agent's card still holds.

        The card's materialized boundary is the expansion of its selection under
        the catalog version it was saved against, so membership there is what
        proves the capability was granted.
        """
        client = _clean(client_id)
        for record in await self._list_active_records(grantor_subject):
            if record.source != ACCESS_SOURCE_AGENT or _clean(record.client_id) != client:
                continue
            namespaces = (record.named_services or {}).get("namespaces")
            if not isinstance(namespaces, Mapping):
                continue
            if not any(
                _clean(name).lower().rstrip(":") == namespace for name in namespaces
            ):
                continue
            resource = next(iter(record.resource_grants), "")
            return capability_denial(
                catalog=catalog,
                provenance=CardProvenance(
                    access_id=record.access_id,
                    card_revision=record.card_revision,
                    catalog_version=record.catalog_version,
                ),
                request=CapabilityRequest(
                    kind=CAPABILITY_NAMED_SERVICE_OPERATION,
                    resource=str(resource),
                    surface="named_service",
                    namespace=namespace,
                    operation=operation,
                ),
            )
        return None

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
        account_scope: Mapping[str, Any] | None = None,
    ) -> AutomationAccessRecord | None:
        """Register (or update) an OAuth-flow delegated grant in the registry.

        Called on every token issuance for an external client (initial consent
        and refresh rotations), so the user sees the connection in Connection
        Hub and revoking it invalidates the CURRENT refresh token and access
        grant. One record per (grantor, client, resource): reconsent updates
        it instead of piling up rows.

        ``account_scope`` carries the per-account claim picks from the consent
        screen (initial consent). The card's EXISTING binding is always
        preserved and merged — a refresh rotation (no picks) must never wipe
        the user's per-account ticks, and a re-consent unions with them.

        A fresh consent also SUPERSEDES sibling cards: a DCR client gets a new
        ``dcr-…`` id on every reconnect, so the old card can never be used
        again. Siblings (same grantor + resource, different dcr client whose
        registered redirect origin matches this client's) donate their account
        binding to the new card and are then revoked.
        """
        grantor = _clean(grantor_subject)
        client = _clean(client_id)
        if not grantor or not client:
            return None
        resource_value = _clean(resource)
        access_id = oauth_access_id(grantor, client, resource_value)
        now = int(time.time())
        created_at = now
        existing_grants: list[str] = []
        existing_account_scope: dict[str, dict[str, list[str]]] = {}
        # A refresh rotation must not widen the card: the named-service
        # selection and its materialized boundary carry forward untouched.
        existing_selection = NamedServiceSelection.unknown()
        existing_named_services: dict[str, Any] = {}
        # Token rotation is not an authority change: the card keeps the catalog
        # generation it was last saved against and only advances its revision.
        existing_catalog_version = ""
        existing_card_revision = 0
        try:
            existing_card = await self._load_record(access_id, grantor_subject=grantor)
        except CardUnavailable:
            existing_card = None
        if existing_card is not None:
            created_at = existing_card.created_at or now
            existing_grants = list(
                existing_card.resource_grants.get(resource_value or "*", ())
            )
            existing_account_scope = {
                provider: {account_id: list(claims) for account_id, claims in accounts.items()}
                for provider, accounts in existing_card.account_scope.items()
            }
            existing_selection = existing_card.named_service_operations
            existing_catalog_version = existing_card.catalog_version
            existing_card_revision = existing_card.card_revision
            existing_named_services = copy.deepcopy(dict(existing_card.named_services or {}))
        # Initial consent (not a refresh rotation): absorb superseded sibling
        # cards BEFORE composing this card, so their binding carries over.
        is_initial_consent = existing_card is None
        inherited_account_scope: dict[str, dict[str, list[str]]] = {}
        superseded: list[AutomationAccessRecord] = []
        if is_initial_consent:
            try:
                inherited_account_scope, superseded = await self._collect_oauth_siblings(
                    grantor=grantor, client_id=client, resource=resource_value,
                )
            except Exception:
                _LOGGER.exception(
                    "[automation-access] sibling scan failed grantor=%s client=%s", grantor, client
                )
        ttl = max(60, int(getattr(self._store, "refresh_ttl", None) or 86400))
        # MERGE with the card's current grants: the card is the authority the
        # guard resolves live, and a hub-side extension must survive token
        # refresh rotations (which re-register on every issuance).
        scope_list = _as_list(list(scopes))
        for grant in existing_grants:
            if grant not in scope_list:
                scope_list.append(grant)
        # Account binding: consent picks ∪ this card's existing binding ∪ what a
        # superseded sibling donated. Union per account claim list.
        merged_account_scope: dict[str, dict[str, list[str]]] = {
            provider: {account_id: list(claims) for account_id, claims in accounts.items()}
            for provider, accounts in normalize_account_scope(account_scope).items()
        }
        for source_scope in (existing_account_scope, inherited_account_scope):
            for provider, accounts in source_scope.items():
                target = merged_account_scope.setdefault(provider, {})
                for account_id, claims in accounts.items():
                    held = target.setdefault(account_id, [])
                    for claim in claims:
                        if claim not in held:
                            held.append(claim)
        record = AutomationAccessRecord(
            access_id=access_id,
            label=_clean(client_label) or client,
            client_id=client,
            grantor_subject=grantor,
            delegate_subject=integration_subject(grantor, client_id=client),
            operations=tuple(_as_list(list(operations))),
            resource_grants={resource_value or "*": tuple(scope_list)},
            named_service_operations=existing_selection,
            named_services=existing_named_services,
            catalog_version=existing_catalog_version,
            card_revision=existing_card_revision + 1,
            account_scope=normalize_account_scope(merged_account_scope),
            identity_scope=_clean(identity_scope),
            created_at=created_at,
            expires_at=now + ttl,
            source=ACCESS_SOURCE_OAUTH,
            refresh_token=_clean(refresh_token),
            access_token=_clean(access_token),
            last_issued_at=now,
        )
        await self._persist_record(record, expected_revision=existing_card_revision)
        _LOGGER.info(
            "[automation-access] oauth grant recorded card=%s client=%s initial=%s "
            "account_scope_providers=%s siblings_superseded=%d",
            access_id, client, is_initial_consent,
            sorted(merged_account_scope.keys()) or "-", len(superseded),
        )
        await self.notify_change(grantor, action="granted", access=record.to_public_dict())
        # Retire the superseded siblings AFTER the new card exists (revoke kills
        # their refresh/access tokens and removes the card).
        for old in superseded:
            try:
                await self.revoke_access(
                    {"user_id": grantor}, access_id=old.access_id,
                )
                _LOGGER.info(
                    "[automation-access] superseded oauth card %s (client=%s) by %s (client=%s)",
                    old.access_id, old.client_id, access_id, client,
                )
            except Exception:
                _LOGGER.exception(
                    "[automation-access] failed to supersede card %s", old.access_id
                )
        return record

    async def oauth_seed_account_scope(
        self,
        *,
        grantor_subject: str,
        client_id: str,
        resource: str,
    ) -> dict[str, dict[str, list[str]]]:
        """The account binding a consent screen should pre-check for this
        client: the client's own existing card (re-consent) merged with what a
        superseded DCR sibling would donate. Read-only — nothing is retired
        here; supersession happens at token issuance."""
        grantor = _clean(grantor_subject)
        client = _clean(client_id)
        if not grantor or not client:
            return {}
        seed: dict[str, dict[str, list[str]]] = {}
        own_raw = await self._redis.get(
            self._record_key(oauth_access_id(grantor, client, _clean(resource)))
        )
        sources: list[Mapping[str, Mapping[str, Any]]] = []
        if own_raw is not None:
            try:
                own = AutomationAccessRecord.from_mapping(json.loads(own_raw))
                sources.append(own.account_scope)
            except Exception:
                pass
        try:
            donated, _retire = await self._collect_oauth_siblings(
                grantor=grantor, client_id=client, resource=_clean(resource),
            )
            sources.append(donated)
        except Exception:
            pass
        for source_scope in sources:
            for provider, accounts in source_scope.items():
                target = seed.setdefault(str(provider), {})
                for account_id, claims in dict(accounts).items():
                    held = target.setdefault(str(account_id), [])
                    for claim in claims:
                        if claim not in held:
                            held.append(str(claim))
        return seed

    async def _collect_oauth_siblings(
        self,
        *,
        grantor: str,
        client_id: str,
        resource: str,
    ) -> tuple[dict[str, dict[str, list[str]]], list[AutomationAccessRecord]]:
        """Sibling cards a fresh OAuth consent supersedes: same grantor and
        resource, a DIFFERENT dcr-registered client whose registered redirect
        origin matches this client's. Returns (donated account binding, cards
        to retire). Non-DCR clients (static ids like ``claude``) are keyed
        stably and never pile up, so only ``dcr-…`` siblings are considered."""
        if not client_id.startswith("dcr-"):
            return {}, []
        own_origins = await self._client_redirect_origins(client_id)
        if not own_origins:
            return {}, []
        try:
            candidates = await self._list_active_records(grantor)
        except CardUnavailable:
            return {}, []
        donated: dict[str, dict[str, list[str]]] = {}
        retire: list[AutomationAccessRecord] = []
        for candidate in candidates:
            if candidate.source != ACCESS_SOURCE_OAUTH:
                continue
            if candidate.client_id == client_id or not candidate.client_id.startswith("dcr-"):
                continue
            if (resource or "*") not in candidate.resource_grants:
                continue
            their_origins = await self._client_redirect_origins(candidate.client_id)
            if not (own_origins & their_origins):
                continue
            for provider, accounts in candidate.account_scope.items():
                target = donated.setdefault(provider, {})
                for account_id, claims in accounts.items():
                    held = target.setdefault(account_id, [])
                    for claim in claims:
                        if claim not in held:
                            held.append(claim)
            retire.append(candidate)
        return donated, retire

    async def _client_redirect_origins(self, client_id: str) -> set[str]:
        """The scheme+host origins of a registered client's redirect URIs —
        the stable identity of the app across DCR re-registrations."""
        store = self._store
        if store is None or not hasattr(store, "get_client_record"):
            return set()
        try:
            record = await store.get_client_record(client_id) or {}
        except Exception:
            return set()
        origins: set[str] = set()
        for uri in record.get("redirect_uris") or []:
            try:
                parts = urlsplit(str(uri))
            except Exception:
                continue
            if parts.scheme and parts.hostname:
                origins.add(f"{parts.scheme}://{parts.hostname}".lower())
        return origins

    async def prune_account_from_grants(
        self, *, grantor_subject: str, provider_id: str, account_id: str
    ) -> dict[str, Any]:
        """Drop a connected account from every grant of this user that binds it.

        Called when the user DISCONNECTS the account. Account ids are
        deterministic (provider + connector app + subject), so a binding left
        behind would silently come back to life if the same account were
        reconnected later - re-granting access nobody ticked again. Disconnect
        therefore closes the bindings too; ``Reconnect`` (re-approval without
        disconnecting) is the action that preserves them.

        Never raises: a pruning failure must not fail the disconnect itself.
        """
        subject = _clean(grantor_subject)
        provider = _clean(provider_id)
        account = _clean(account_id)
        if not subject or not provider or not account:
            return {"pruned": 0, "grants": []}
        try:
            candidates = await self._list_active_records(subject)
        except Exception:
            return {"pruned": 0, "grants": []}
        pruned: list[str] = []
        for record in candidates:
            access_id = record.access_id
            try:
                accounts = dict(record.account_scope.get(provider) or {})
                if account not in accounts:
                    continue
                accounts.pop(account, None)
                scope = {
                    p: {a: list(cl) for a, cl in bound.items()}
                    for p, bound in record.account_scope.items()
                }
                # A provider with no bound accounts drops out entirely - the
                # runtime is default-closed for delegated callers.
                if accounts:
                    scope[provider] = {a: list(cl) for a, cl in accounts.items()}
                else:
                    scope.pop(provider, None)
                pruned_record = replace_fields(
                    record,
                    account_scope={
                        p: {a: tuple(cl) for a, cl in bound.items()}
                        for p, bound in scope.items()
                    },
                    card_revision=record.card_revision + 1,
                )
                await self._persist_record(
                    pruned_record, expected_revision=record.card_revision
                )
                pruned.append(access_id)
                await self.notify_change(
                    subject, action="edited", access=pruned_record.to_public_dict()
                )
            except Exception:
                _LOGGER.warning(
                    "[connection_hub.disconnect] pruning account binding failed "
                    "(non-fatal): access_id=%s provider=%s account=%s",
                    access_id, provider, account, exc_info=True,
                )
                continue
        if pruned:
            _LOGGER.info(
                "[connection_hub.disconnect] cleared account binding from %d grant(s): "
                "provider=%s account=%s grants=%s",
                len(pruned), provider, account, pruned,
            )
        return {"pruned": len(pruned), "grants": pruned}


    async def extend_client_access(
        self,
        user: Mapping[str, Any],
        *,
        client_id: str,
        resource: str,
        claims: Iterable[str],
        account_scope: Mapping[str, Any] | None = None,
        replace: bool = False,
        label: str | None = None,
    ) -> dict[str, Any]:
        """Edit an EXISTING external client's card (an unknown client is never
        created here; its card is born at OAuth consent). ``replace=False``
        MERGES the claims in (a one-click extension); ``replace=True`` makes the
        submitted claim set the resource's grants EXACTLY (the edit-in-place
        path — allowing narrowing, e.g. read+write -> read). ``account_scope``
        ({provider_id: [account_ids or "*"]}) edits the client's per-provider
        account binding the same way (merge or replace). The card is the
        authority the guard resolves live, so either takes effect on the
        client's very next call, on the bearer it already holds; a
        pointer-carrying refresh re-derives from the card, so a narrowing
        sticks across token rotations."""
        grantor_subject = _subject_from_user(user)
        if not grantor_subject:
            return {"ok": False, "error": "delegated_access_requires_authenticated_user"}
        client = _clean(client_id)
        resource_value = _clean(resource)
        claim_list = _as_list(list(claims))
        account_scope_provided = account_scope is not None
        scope_update: dict[str, dict[str, list[str]]] = {
            provider: {account_id: list(cl) for account_id, cl in accounts.items()}
            for provider, accounts in normalize_account_scope(account_scope).items()
        }
        if not client or (
            not claim_list
            and not scope_update
            and not (account_scope_provided and replace)
        ):
            return {"ok": False, "error": "delegated_access_requires_client_and_claims"}
        access_id = oauth_access_id(grantor_subject, client, resource_value)
        try:
            record = await self._load_record(access_id, grantor_subject=grantor_subject)
        except CardUnavailable as exc:
            return {
                "ok": False,
                "error": "delegated_cards_unavailable",
                "reason": exc.reason,
                "retryable": True,
                "status": 503,
            }
        if record is None:
            return {"ok": False, "error": "delegated_access_unknown_client",
                    "message": "This client has no existing grant to extend; it connects via its own consent flow first."}
        # Claims must stay inside the deployment's delegable ceiling for the
        # resource when the catalog knows it.
        cfg = self._config.resource_config(resource_value) if resource_value else None
        ceiling = set(_as_list(list(getattr(cfg, "grants", ()) or ()))) if cfg is not None else set()
        if ceiling:
            outside = sorted(set(claim_list) - ceiling)
            if outside:
                return {"ok": False, "error": "delegated_access_grants_not_delegable", "grants": outside}
        key = resource_value or "*"
        resource_grants = {res: tuple(vals) for res, vals in record.resource_grants.items()}
        if claim_list:
            if replace:
                # Edit: the submitted set becomes the resource's grants exactly.
                merged = list(claim_list)
            else:
                merged = list(record.resource_grants.get(key, ()))
                for claim in claim_list:
                    if claim not in merged:
                        merged.append(claim)
            resource_grants[key] = tuple(merged)
        # Account binding edit, same merge/replace semantics per provider AND
        # per account.
        account_scope_out: dict[str, dict[str, tuple[str, ...]]] = {
            provider: {account_id: tuple(cl) for account_id, cl in accounts.items()}
            for provider, accounts in record.account_scope.items()
        }
        if replace and account_scope_provided:
            # The submitted map is the full desired binding. {} intentionally
            # clears every account; omission preserves the existing binding.
            account_scope_out = {
                provider: {account_id: tuple(cl) for account_id, cl in accounts.items()}
                for provider, accounts in scope_update.items()
            }
        elif account_scope_provided:
            for provider, accounts in scope_update.items():
                target = dict(account_scope_out.get(provider, {}))
                for account_id, cl in accounts.items():
                    current = list(target.get(account_id, ()))
                    for claim in cl:
                        if claim not in current:
                            current.append(claim)
                    target[account_id] = tuple(current)
                account_scope_out[provider] = target
        # A DCR client registers one fixed name (every Claude connector arrives
        # as "Claude"), so the user may rename the card to tell connections
        # apart. Empty/absent label leaves the current one untouched.
        new_label = _clean(label)
        updated = replace_fields(
            record,
            resource_grants=resource_grants,
            account_scope=account_scope_out,
            label=new_label or record.label,
            card_revision=record.card_revision + 1,
        )
        try:
            await self._persist_record(updated, expected_revision=record.card_revision)
        except (CardUnavailable, CardConflict, CardCommitFailed) as exc:
            return {
                "ok": False,
                "error": "delegated_card_not_committed",
                "reason": getattr(exc, "reason", ""),
                "retryable": True,
                "status": 503,
            }
        await self.notify_change(
            grantor_subject,
            action="edited" if replace else "extended",
            access=updated.to_public_dict(),
        )
        return {
            "ok": True,
            "access_id": access_id,
            "resource_grants": {res: list(vals) for res, vals in resource_grants.items()},
            "account_scope": {
                p: {a: list(cl) for a, cl in accounts.items()} for p, accounts in account_scope_out.items()
            },
        }

    async def revoke_access(self, user: Mapping[str, Any], *, access_id: str) -> dict[str, Any]:
        grantor_subject = _subject_from_user(user)
        if not grantor_subject:
            return {"ok": False, "error": "delegated_access_requires_authenticated_user"}
        access_id_value = _clean(access_id)
        if not access_id_value:
            return {"ok": False, "error": "delegated_access_id_required"}
        try:
            record = await self._load_record(
                access_id_value, grantor_subject=grantor_subject
            )
        except CardUnavailable as exc:
            return {
                "ok": False,
                "error": "delegated_cards_unavailable",
                "reason": exc.reason,
                "retryable": True,
                "status": 503,
            }
        if record is None:
            return {"ok": True, "removed": False}
        if record.grantor_subject != grantor_subject:
            return {"ok": False, "error": "delegated_access_cross_user_access_denied"}
        # The revoked revision commits before any credential cleanup, so a
        # failure below cannot leave the card usable.
        await self._forget_record(record)
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
