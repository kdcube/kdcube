# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Tests for user-created delegated automation access."""

from __future__ import annotations

import copy
import json
import os
import uuid
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.persistence import (
    DurableCardPersistence,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.resolver import (
    CatalogUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
    AutomationAccessService,
    agent_grant_access_id,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    oauth_delegated_config,
)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.values[key] = value
        self.ttls[key] = ttl

    async def delete(self, key: str):
        self.values.pop(key, None)

    async def sadd(self, key: str, value: str):
        self.sets.setdefault(key, set()).add(value)

    async def smembers(self, key: str):
        return set(self.sets.get(key, set()))

    async def srem(self, key: str, *values: str):
        current = self.sets.setdefault(key, set())
        for value in values:
            current.discard(value)

    async def expire(self, key: str, ttl: int):
        self.ttls[key] = ttl


class _Store:
    def __init__(self) -> None:
        self.bound: list[dict] = []

    async def bind_access_grant(self, access_token, operations, expires_in, **kwargs):
        self.bound.append(
            {
                "access_token": access_token,
                "operations": list(operations),
                "expires_in": expires_in,
                **kwargs,
            }
        )


class _Authority:
    def __init__(self) -> None:
        self.logged_out: list[str] = []

    async def logout(self, *, session_id: str):
        self.logged_out.append(session_id)
        return True



async def stored_card(
    service: AutomationAccessService, access_id: str, *, grantor: str = "platform-user-1"
) -> dict:
    """The committed card, read through the persistence port."""
    record = await service._load_record(access_id, grantor_subject=grantor)
    assert record is not None, f"card {access_id} is not committed"
    return record.to_dict()


async def only_stored_card(
    service: AutomationAccessService, *, grantor: str = "platform-user-1"
) -> dict:
    """The single committed card for a grantor."""
    records = await service._list_active_records(grantor)
    assert len(records) == 1, f"expected one card, found {len(records)}"
    return records[0].to_dict()


REDIS_URL = os.environ.get("REDIS_URL") or ""

pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="REDIS_URL is not set; delegated-card persistence needs a real Redis",
)


@pytest.fixture
async def card_persistence(tmp_path):
    """Production card persistence: durable revisions plus a Redis projection."""
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Redis at REDIS_URL is unreachable: {exc}")
    yield DurableCardPersistence(
        redis=client,
        tenant=f"t-{uuid.uuid4().hex[:8]}",
        project=f"p-{uuid.uuid4().hex[:8]}",
        card_store=BundleStorageDelegatedCardStore(tmp_path),
    )
    await client.aclose()

TEST_CATALOG_VERSION = "delegated_catalog_2026-08-11-10-30-00-123_d4e5f6a7b8c9"


class _CatalogResolver:
    """Resolves a fixed active catalog generation; `unavailable` fails closed.

    ``connections`` is the catalog body a governed decision reads; tests that
    only need a version to stamp leave it empty.
    """

    def __init__(
        self,
        version: str = TEST_CATALOG_VERSION,
        unavailable: bool = False,
        connections: dict | None = None,
        versions: dict[str, dict] | None = None,
    ) -> None:
        self.version = version
        self.unavailable = unavailable
        self.connections = connections or {}
        # Registered historical generations, for drift comparison.
        self.versions = dict(versions or {})

    async def resolve_active(self):
        if self.unavailable:
            raise CatalogUnavailable("active_catalog_not_registered")
        return SimpleNamespace(version=self.version, connections=self.connections)

    async def resolve_version(self, version: str):
        if self.unavailable:
            raise CatalogUnavailable("cache_unavailable")
        if version == self.version:
            return SimpleNamespace(version=self.version, connections=self.connections)
        if version in self.versions:
            return SimpleNamespace(version=version, connections=self.versions[version])
        return None

    def advance(self, *, version: str, connections: dict) -> None:
        """Publish a new generation, keeping the previous one resolvable."""
        self.versions[self.version] = self.connections
        self.version = version
        self.connections = connections


class _NamedServiceDiscovery:
    def __init__(self, requirements_by_namespace: dict[str, list[dict]]) -> None:
        self.requirements_by_namespace = requirements_by_namespace
        self.requested: list[str] = []

    async def entries_for_namespace(self, namespace: str):
        self.requested.append(namespace)
        requirements = self.requirements_by_namespace.get(namespace, [])
        if not requirements:
            return []
        return [
            SimpleNamespace(
                spec=SimpleNamespace(
                    metadata={"connected_accounts": requirements},
                )
            )
        ]


async def _minter(_grantor_subject, _scopes, **kwargs):
    return {
        "access_token": "kst1.test.abcdef",
        "expires_in": kwargs.get("ttl_seconds") or 3600,
        "session_id": "session-1",
    }


PLAIN_OAUTH = {
        "enabled": True,
        "tenant": "demo-tenant",
        "project": "demo-project",
        "capabilities": [
            {
                "grant": "kdcube:role:super-admin",
                "label": "Use all platform and application APIs",
                "delegable_roles": ["kdcube:role:super-admin"],
            },
            {
                "grant": "records:read",
                "label": "Read records",
                "delegable_roles": ["kdcube:role:registered"],
            },
            {
                "grant": "records:write",
                "label": "Write records",
                "delegable_permissions": ["records:write"],
            },
        ],
        "resources": [
            {
                "resource": "*",
                "label": "All platform and application APIs",
                "admin_only": True,
                "grants": ["kdcube:role:super-admin"],
            },
            {
                "resource": "https://example.test/mcp",
                "label": "Example MCP",
                "identity_scope": "grantor",
                "tools": {
                    "records_export": {
                        "label": "Export records",
                        "grants": ["records:read"],
                    },
                    "records_upsert": {
                        "label": "Upsert records",
                        "grants": ["records:write"],
                    },
                },
            },
        ],
}


def _config():
    return oauth_delegated_config(
        SimpleNamespace(state=SimpleNamespace(oauth_delegated_config=PLAIN_OAUTH))
    )


def _connections() -> dict:
    """The registered catalog body matching `_config`."""
    return {"delegated_credentials": {"oauth": PLAIN_OAUTH}}


NAMED_SERVICES_OAUTH = {
        "enabled": True,
        "tenant": "demo-tenant",
        "project": "demo-project",
        "capabilities": [
            {
                "grant": grant,
                "label": grant,
                "delegable_roles": ["kdcube:role:registered"],
            }
            for grant in (
                "named_services:use",
                "mail:read",
                "mail:send",
                "slack:read",
                "slack:write",
            )
        ],
        "resources": [
            {
                "resource": "https://example.test/mcp/named-services",
                "label": "Named services MCP",
                "tools": {
                    "named_services_call": {
                        "label": "Named service call",
                        "grants": ["named_services:use"],
                    }
                },
                "named_services": {
                    "namespaces": {
                        "mail": {
                            "label": "Mail",
                            "description": "Connected mail accounts.",
                            "authority_id": "delegated_client",
                            "tools": {
                                "search": {
                                    "operation": "object.search",
                                    "label": "Search mail",
                                    "grants": ["mail:read"],
                                },
                                "action": {
                                    "operation": "object.action",
                                    "label": "Mail action",
                                    "operations": {
                                        "object.action": {
                                            "label": "Mail action",
                                            "grants": ["mail:read", "mail:send"],
                                        }
                                    },
                                },
                            },
                        },
                        "slack": {
                            "label": "Slack",
                            "description": "Connected Slack workspaces.",
                            "authority_id": "delegated_client",
                            "tools": {
                                "search": {
                                    "operation": "object.search",
                                    "label": "Search Slack",
                                    "grants": ["slack:read"],
                                },
                                "action": {
                                    "operation": "object.action",
                                    "label": "Slack action",
                                    "operations": {
                                        "object.action": {
                                            "label": "Slack action",
                                            "grants": ["slack:read", "slack:write"],
                                        }
                                    },
                                },
                                "call": {
                                    "label": "Generic Slack call",
                                    "operations": {
                                        "object.search": {
                                            "label": "Search Slack",
                                            "grants": ["slack:read"],
                                        },
                                        "object.action": {
                                            "label": "Slack action",
                                            "grants": ["slack:read", "slack:write"],
                                        },
                                    },
                                },
                            },
                        },
                    }
                },
            }
        ],
}


def _named_services_config():
    return oauth_delegated_config(
        SimpleNamespace(state=SimpleNamespace(oauth_delegated_config=NAMED_SERVICES_OAUTH))
    )


def _named_services_connections() -> dict:
    """The registered catalog body matching `_named_services_config`."""
    return {"delegated_credentials": {"oauth": NAMED_SERVICES_OAUTH}}


@pytest.mark.asyncio
async def test_automation_access_create_list_and_revoke(card_persistence):
    redis = _Redis()
    store = _Store()
    authority = _Authority()
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=redis,
        tenant="demo-tenant",
        project="demo-project",
        config=_config(),
        grant_store=store,
        authority=authority,
        minter=_minter,
    )
    user = {
        "user_id": "platform-user-1",
        "roles": ["kdcube:role:registered"],
        "permissions": [],
    }

    created = await service.create_access(
        user,
        label="Nightly automation",
        resource_grants={"https://example.test/mcp": ["records:read"]},
        ttl_seconds=3600,
    )

    assert created["ok"] is True
    assert created["authorization_header"] == "Bearer kst1.test.abcdef"
    assert created["access"]["label"] == "Nightly automation"
    assert created["access"]["operations"] == ["records_export"]
    assert "session_id" not in created["access"]

    assert store.bound[0]["operations"] == ["records_export"]
    assert store.bound[0]["grantor_authority"]["delegation_edges"][0]["grants"] == ["records:read"]
    assert store.bound[0]["credential"]["attrs"]["grantor_subject"] == "platform-user-1"
    assert "resources" not in store.bound[0]["credential"]["attrs"]
    assert store.bound[0]["credential"]["attrs"]["resource_grants"] == {
        "https://example.test/mcp": ["records:read"],
    }

    listed = await service.list_access(user)
    assert listed["ok"] is True
    assert listed["platform_user_id"] == "platform-user-1"
    assert listed["items"][0]["access_id"] == created["access"]["access_id"]
    assert [item["grant"] for item in listed["grant_options"]] == ["records:read"]
    assert listed["resources"][0]["operations"][0]["name"] == "records_export"

    assert (await stored_card(service, created["access"]["access_id"]))["session_id"] == "session-1"

    revoked = await service.revoke_access(user, access_id=created["access"]["access_id"])
    assert revoked == {
        "ok": True,
        "removed": True,
        "session_removed": True,
        # Manual tokens carry no OAuth refresh token; only oauth-flow grants do.
        "refresh_token_revoked": False,
    }
    assert authority.logged_out == ["session-1"]
    assert await service.list_access(user) == {
        "ok": True,
        "platform_user_id": "platform-user-1",
        "grant_options": listed["grant_options"],
        "resources": listed["resources"],
        "items": [],
    }


@pytest.mark.asyncio
async def test_resource_options_project_exact_named_service_and_provider_catalogs(card_persistence):
    mail_requirement = {
        "provider_id": "google",
        "connector_app_id": "gmail",
        "provider_label": "Google",
        "claims": ["gmail:read", "gmail:send"],
        "claim_labels": {
            "gmail:read": "read mail",
            "gmail:send": "send mail",
        },
        "claims_by_operation": {
            "object.search": ["gmail:read"],
            "object.action.send": ["gmail:send"],
        },
    }
    slack_requirement = {
        "provider_id": "slack",
        "connector_app_id": "slack-oauth",
        "provider_label": "Slack",
        "claims": ["slack:history", "slack:chat:write"],
        "claim_labels": {
            "slack:history": "read history",
            "slack:chat:write": "post messages",
        },
    }
    discovery = _NamedServiceDiscovery(
        {
            "mail": [mail_requirement],
            "slack": [slack_requirement, slack_requirement],
        }
    )
    store = _Store()
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_named_services_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_named_services_config(),
        grant_store=store,
        authority=_Authority(),
        minter=_minter,
        named_service_discovery=discovery,
    )
    user = {
        "user_id": "platform-user-1",
        "roles": ["kdcube:role:registered"],
        "permissions": [],
    }

    listed = await service.list_access(user)
    resource = listed["resources"][0]
    namespaces = {item["namespace"]: item for item in resource["named_services"]}

    assert discovery.requested == ["mail", "slack"]
    assert namespaces["mail"]["connected_accounts"] == [mail_requirement]
    assert namespaces["slack"]["connected_accounts"] == [slack_requirement]
    assert namespaces["slack"]["tools"]["action"]["operation"] == "object.action"
    assert set(namespaces["slack"]["tools"]["action"]["operations"]) == {
        "object.action"
    }
    assert "object.action.post_message" not in json.dumps(
        namespaces["slack"]["tools"],
        sort_keys=True,
    )

    created = await service.create_access(
        user,
        label="Slack automation",
        resource_grants={
            "https://example.test/mcp/named-services": [
                "named_services:use",
                "slack:read",
                "slack:write",
            ]
        },
        named_service_operations="*",
    )
    assert created["ok"] is True
    persisted_policy = store.bound[0]["named_services"]
    assert persisted_policy["namespaces"]["slack"]["tools"]["action"]["operation"] == "object.action"
    assert "connected_accounts" not in persisted_policy["namespaces"]["slack"]


@pytest.mark.asyncio
async def test_automation_access_persists_only_selected_named_service_operations(card_persistence):
    store = _Store()
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_named_services_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_named_services_config(),
        grant_store=store,
        authority=_Authority(),
        minter=_minter,
        named_service_discovery=_NamedServiceDiscovery({}),
    )
    user = {
        "user_id": "platform-user-1",
        "roles": ["kdcube:role:registered"],
        "permissions": [],
    }
    resource = "https://example.test/mcp/named-services"

    created = await service.create_access(
        user,
        label="Slack search only",
        resource_grants={resource: ["named_services:use", "slack:read"]},
        named_service_operations={
            resource: {"slack": ["object.search"]},
        },
    )

    assert created["ok"] is True
    assert created["access"]["named_service_operations"] == {
        resource: {"slack": ["object.search"]},
    }
    persisted = store.bound[0]["named_services"]
    assert set(persisted["namespaces"]) == {"slack"}
    assert set(persisted["namespaces"]["slack"]["tools"]) == {"search", "call"}
    assert set(
        persisted["namespaces"]["slack"]["tools"]["call"]["operations"]
    ) == {"object.search"}


@pytest.mark.asyncio
async def test_automation_access_rejects_named_service_operation_without_its_grants(card_persistence):
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_named_services_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_named_services_config(),
        grant_store=_Store(),
        authority=_Authority(),
        minter=_minter,
        named_service_discovery=_NamedServiceDiscovery({}),
    )
    resource = "https://example.test/mcp/named-services"

    denied = await service.create_access(
        {
            "user_id": "platform-user-1",
            "roles": ["kdcube:role:registered"],
            "permissions": [],
        },
        label="Missing Slack write",
        resource_grants={resource: ["named_services:use", "slack:read"]},
        named_service_operations={
            resource: {"slack": ["object.action"]},
        },
    )

    assert denied == {
        "ok": False,
        "error": "invalid_named_service_operation_selection",
        "message": (
            "named-service operation(s) lack selected grants for "
            "'https://example.test/mcp/named-services'/slack: object.action"
        ),
    }


@pytest.mark.asyncio
async def test_automation_access_rejects_non_delegable_grant(card_persistence):
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_config(),
        grant_store=_Store(),
        authority=_Authority(),
        minter=_minter,
    )

    denied = await service.create_access(
        {"user_id": "platform-user-1", "roles": [], "permissions": []},
        label="No grants",
        resource_grants={"https://example.test/mcp": ["records:read"]},
    )

    assert denied["ok"] is False
    assert denied["error"] == "delegated_access_grants_not_delegable"
    assert denied["grants"] == ["records:read"]
    # The refusal says WHERE this deployment decides — the hub catalog is
    # the subset of what apps expose that may be asked for here.
    assert "connection-hub@1-0" in denied["message"]


@pytest.mark.asyncio
async def test_automation_access_requires_configured_resource_when_catalog_exists(card_persistence):
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_config(),
        grant_store=_Store(),
        authority=_Authority(),
        minter=_minter,
    )
    user = {
        "user_id": "platform-user-1",
        "roles": ["kdcube:role:registered"],
        "permissions": [],
    }

    missing = await service.create_access(
        user,
        label="No resource",
        resource_grants={},
    )
    assert missing == {"ok": False, "error": "delegated_access_requires_resource_grants"}

    unknown = await service.create_access(
        user,
        label="Unknown resource",
        resource_grants={"https://example.test/other": ["records:read"]},
    )
    assert unknown["ok"] is False
    assert unknown["error"] == "delegated_access_unknown_resources"
    assert unknown["resources"] == ["https://example.test/other"]
    # An endpoint nobody configured is NOT "a permission you may not have":
    # it is one this deployment never decided to expose, and the answer says so.
    assert "connection-hub@1-0" in unknown["message"]


@pytest.mark.asyncio
async def test_automation_access_all_resources_is_admin_only(card_persistence):
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_config(),
        grant_store=_Store(),
        authority=_Authority(),
        minter=_minter,
    )

    non_admin = {
        "user_id": "platform-user-1",
        "roles": ["kdcube:role:registered"],
        "permissions": [],
    }
    listed = await service.list_access(non_admin)
    assert [item["resource"] for item in listed["resources"]] == ["https://example.test/mcp"]

    denied = await service.create_access(
        non_admin,
        label="All APIs",
        resource_grants={"*": ["kdcube:role:super-admin"]},
    )
    assert denied["ok"] is False
    assert denied["error"] == "delegated_access_grants_not_delegable"
    assert denied["grants"] == ["kdcube:role:super-admin"]
    # The refusal says WHERE this deployment decides — the hub catalog is
    # the subset of what apps expose that may be asked for here.
    assert "connection-hub@1-0" in denied["message"]

    admin = {
        "user_id": "platform-admin-1",
        "roles": ["kdcube:role:super-admin"],
        "permissions": [],
    }
    listed_admin = await service.list_access(admin)
    assert listed_admin["resources"][0]["resource"] == "*"
    assert listed_admin["resources"][0]["admin_only"] is True

    created = await service.create_access(
        admin,
        label="All APIs",
        resource_grants={"*": ["kdcube:role:super-admin"]},
    )
    assert created["ok"] is True
    assert created["access"]["resource_grants"] == {"*": ["kdcube:role:super-admin"]}
    assert created["access"].get("operations", []) == []


@pytest.mark.asyncio
async def test_automation_access_can_select_multiple_resources(card_persistence):
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_config(),
        grant_store=_Store(),
        authority=_Authority(),
        minter=_minter,
    )
    admin = {
        "user_id": "platform-admin-1",
        "roles": ["kdcube:role:super-admin", "kdcube:role:registered"],
        "permissions": [],
    }

    created = await service.create_access(
        admin,
        label="All and MCP",
        resource_grants={
            "*": ["kdcube:role:super-admin"],
            "https://example.test/mcp": ["records:read"],
        },
    )

    assert created["ok"] is True
    assert created["access"]["resource_grants"] == {
        "*": ["kdcube:role:super-admin"],
        "https://example.test/mcp": ["records:read"],
    }
    assert created["access"]["operations"] == ["records_export"]


class _OAuthStore(_Store):
    def __init__(self) -> None:
        super().__init__()
        self.refresh_ttl = 3600 * 24
        self.revoked_refresh: list[str] = []
        self.revoked_access: list[str] = []

    async def revoke_refresh_token(self, refresh_token: str) -> bool:
        self.revoked_refresh.append(refresh_token)
        return True

    async def revoke_access_grant(self, access_token: str) -> bool:
        self.revoked_access.append(access_token)
        return True


@pytest.mark.asyncio
async def test_oauth_grant_registers_lists_and_revokes(card_persistence):
    """An external client connecting via OAuth becomes a visible, revocable grant."""
    redis = _Redis()
    store = _OAuthStore()
    user = {
        "sub": "platform-user-1",
        "roles": ["kdcube:role:registered"],
        "permissions": [],
    }
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=redis,
        tenant="demo-tenant",
        project="demo-project",
        config=_config(),
        grant_store=store,
        authority=_Authority(),
        minter=_minter,
    )

    record = await service.record_oauth_grant(
        grantor_subject="platform-user-1",
        client_id="dcr-claude",
        client_label="Claude",
        scopes=["records:read"],
        operations=["records_export"],
        resource="https://example.test/mcp",
        access_token="kst1.oauth.token",
        refresh_token="refresh-1",
    )
    assert record is not None

    listed = await service.list_access(user)
    items = listed["items"]
    assert len(items) == 1
    assert items[0]["source"] == "oauth"
    assert items[0]["label"] == "Claude"
    assert items[0]["resource_grants"] == {"https://example.test/mcp": ["records:read"]}
    assert "refresh_token" not in items[0]
    assert "access_token" not in items[0]

    # Reconsent with wider scope updates the SAME row (no pile-up) and keeps created_at.
    updated = await service.record_oauth_grant(
        grantor_subject="platform-user-1",
        client_id="dcr-claude",
        client_label="Claude",
        scopes=["records:read", "records:write"],
        resource="https://example.test/mcp",
        access_token="kst1.oauth.token2",
        refresh_token="refresh-2",
    )
    assert updated is not None
    assert updated.access_id == record.access_id
    assert updated.created_at == record.created_at
    assert len((await service.list_access(user))["items"]) == 1

    revoked = await service.revoke_access(user, access_id=record.access_id)
    assert revoked["ok"] is True and revoked["removed"] is True
    assert revoked["refresh_token_revoked"] is True
    # The CURRENT tokens die, not the ones rotated away earlier.
    assert store.revoked_refresh == ["refresh-2"]
    assert store.revoked_access == ["kst1.oauth.token2"]
    assert (await service.list_access(user))["items"] == []


async def test_live_sessions_receive_delegated_access_changes():
    """A registered live hub session is notified on grant record and revoke;
    expired sessions are pruned and receive nothing."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
        DELEGATED_ACCESS_CHANGED_EVENT,
        notify_delegated_access_changed,
        register_delegated_access_live_session,
    )

    class _ZRedis(_Redis):
        def __init__(self) -> None:
            super().__init__()
            self.zsets: dict[str, dict[str, float]] = {}

        async def zadd(self, key: str, mapping: dict[str, float]):
            self.zsets.setdefault(key, {}).update(mapping)

        async def zremrangebyscore(self, key: str, low, high):
            members = self.zsets.get(key, {})
            low_v = float("-inf") if low == "-inf" else float(low)
            high_v = float("inf") if high == "+inf" else float(high)
            for member in [m for m, s in members.items() if low_v <= s <= high_v]:
                members.pop(member, None)

        async def zrange(self, key: str, start: int, end: int):
            members = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
            stop = len(members) if end == -1 else end + 1
            return [m for m, _ in members[start:stop]]

    class _Relay:
        def __init__(self) -> None:
            self.emitted: list[dict] = []

        async def emit(self, *, event, data, tenant, project, session_id):
            self.emitted.append(
                {"event": event, "type": data.get("type"), "session_id": session_id,
                 "action": (data.get("data") or {}).get("action")}
            )

    redis = _ZRedis()
    relay = _Relay()
    import time as _time
    now = int(_time.time())

    await register_delegated_access_live_session(
        redis, tenant="demo-tenant", project="demo-project",
        grantor_subject="platform-user-1", session_id="live-1", expires_at=now + 600,
    )
    # an expired session must be pruned, never notified
    await register_delegated_access_live_session(
        redis, tenant="demo-tenant", project="demo-project",
        grantor_subject="platform-user-1", session_id="stale-1", expires_at=now - 5,
    )

    await notify_delegated_access_changed(
        redis, tenant="demo-tenant", project="demo-project",
        grantor_subject="platform-user-1", action="granted",
        access={"access_id": "oauth-abc"}, relay=relay,
    )
    await notify_delegated_access_changed(
        redis, tenant="demo-tenant", project="demo-project",
        grantor_subject="platform-user-1", action="revoked",
        access_id="oauth-abc", relay=relay,
    )
    # a different user's mutation reaches nobody here
    await notify_delegated_access_changed(
        redis, tenant="demo-tenant", project="demo-project",
        grantor_subject="platform-user-2", action="granted", relay=relay,
    )

    assert [e["session_id"] for e in relay.emitted] == ["live-1", "live-1"]
    assert {e["type"] for e in relay.emitted} == {DELEGATED_ACCESS_CHANGED_EVENT}
    assert [e["action"] for e in relay.emitted] == ["granted", "revoked"]


def _agent_service(card_persistence):
    return AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_config(),
        grant_store=_Store(),
        authority=_Authority(),
        minter=_minter,
    )


_AGENT_CLIENT = "kdcube-agent:app@v1:lg-react"
_AGENT_USER = {"user_id": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}


@pytest.mark.asyncio
async def test_create_access_with_agent_client_id_is_deterministic_and_stores_token(card_persistence):
    service = _agent_service(card_persistence)
    created = await service.create_access(
        _AGENT_USER,
        label="lg-react (memories)",
        resource_grants={"https://example.test/mcp": ["records:read"]},
        client_id=_AGENT_CLIENT,
    )
    assert created["ok"] is True
    access = created["access"]
    # Keyed to the agent's deterministic client_id (not a random automation:… one),
    # with a stable agent-… access_id and source=agent.
    assert access["client_id"] == _AGENT_CLIENT
    assert access["access_id"].startswith("agent-")
    assert access["source"] == "agent"
    # The public view never leaks the token; the internal record persists it for reuse.
    assert "access_token" not in access
    token = await service.agent_access_token(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        resources=["https://example.test/mcp"],
    )
    assert token is not None
    assert token["access_token"] == "kst1.test.abcdef"
    assert token["authorization_header"] == "Bearer kst1.test.abcdef"
    assert token["resource_grants"] == {"https://example.test/mcp": ["records:read"]}


@pytest.mark.asyncio
async def test_reconsent_updates_one_record_and_preserves_created_at(card_persistence):
    service = _agent_service(card_persistence)
    first = await service.create_access(
        _AGENT_USER, label="lg-react", client_id=_AGENT_CLIENT,
        resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    listed = await service.list_access(_AGENT_USER)
    assert len(listed["items"]) == 1
    created_at = (await only_stored_card(service))["created_at"]

    again = await service.create_access(
        _AGENT_USER, label="lg-react (relabeled)", client_id=_AGENT_CLIENT,
        resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    # Same deterministic access_id -> re-consent updates the SAME record, not a pile-up.
    assert again["access"]["access_id"] == first["access"]["access_id"]
    assert len((await service.list_access(_AGENT_USER))["items"]) == 1
    assert (await only_stored_card(service))["created_at"] == created_at


@pytest.mark.asyncio
async def test_agent_access_token_none_when_no_grant_or_scope_mismatch(card_persistence):
    service = _agent_service(card_persistence)
    await service.create_access(
        _AGENT_USER, label="lg-react", client_id=_AGENT_CLIENT,
        resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    # Consent pending for a DIFFERENT agent -> no token.
    assert await service.agent_access_token(
        grantor_subject="platform-user-1", client_id="kdcube-agent:app@v1:other",
        resources=["https://example.test/mcp"],
    ) is None
    # Grant exists but for a different resource key -> no token (the resolver must
    # ask for the exact resource the connection points at).
    assert await service.agent_access_token(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        resources=["https://example.test/other"],
    ) is None


@pytest.mark.asyncio
async def test_agent_regrant_MERGES_claims_never_replaces(card_persistence):
    # Sequential one-click grants on the SAME resource must accumulate:
    # granting write after read keeps read (a replace would silently revoke it).
    service = _agent_service(card_persistence)
    writer = {**_AGENT_USER, "permissions": ["records:write"]}
    await service.create_access(
        writer, label="a", client_id=_AGENT_CLIENT,
        resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    await service.create_access(
        writer, label="a", client_id=_AGENT_CLIENT,
        resource_grants={"https://example.test/mcp": ["records:write"]},
    )
    token = await service.agent_access_token(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        resources=["https://example.test/mcp"],
    )
    assert sorted(token["resource_grants"]["https://example.test/mcp"]) == ["records:read", "records:write"]
    assert len((await service.list_access(_AGENT_USER))["items"]) == 1


@pytest.mark.asyncio
async def test_agent_regrant_with_merge_existing_false_REPLACES_the_record(card_persistence):
    # The EDIT semantics: the user unchecked a claim; the submitted set becomes
    # the record exactly — the merge default would have kept the removed claim.
    service = _agent_service(card_persistence)
    writer = {**_AGENT_USER, "permissions": ["records:write"]}
    await service.create_access(
        writer, label="a", client_id=_AGENT_CLIENT,
        resource_grants={"https://example.test/mcp": ["records:read", "records:write"]},
    )
    await service.create_access(
        writer, label="a", client_id=_AGENT_CLIENT,
        resource_grants={"https://example.test/mcp": ["records:read"]},
        merge_existing=False,
    )
    token = await service.agent_access_token(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        resources=["https://example.test/mcp"],
    )
    assert token["resource_grants"]["https://example.test/mcp"] == ["records:read"]
    assert len((await service.list_access(writer))["items"]) == 1


def _named_services_agent_service(card_persistence):
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
        oauth_delegated_config,
    )
    state = SimpleNamespace(
        oauth_delegated_config={
            "enabled": True,
            "tenant": "demo-tenant",
            "project": "demo-project",
            "capabilities": [
                {"grant": g, "label": g, "delegable_roles": ["kdcube:role:registered"]}
                for g in ("named_services:use", "mail:read", "mail:send")
            ],
            "resources": [
                {
                    "resource": "*/kdcube-services@1-0/public/mcp/named_services*",
                    "label": "Named services MCP",
                    # The resource's scope ceiling: the entry grant AND every
                    # namespace grant it publishes (the deployment contract).
                    "grants": ["named_services:use", "mail:read", "mail:send"],
                    # The generic entry tools carry the common MCP entry grant.
                    "tools": {
                        "named_services_call": {"label": "Named service call", "grants": ["named_services:use"]},
                    },
                    "named_services": {
                        "namespaces": {
                            "mail": {
                                "label": "Mail",
                                "tools": {
                                    "search": {"operation": "object.search", "grants": ["mail:read"]},
                                    "action": {
                                        "operation": "object.action",
                                        "operations": {
                                            "object.action": {"grants": ["mail:read", "mail:send"]},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            ],
        }
    )
    # The native gate reads the registered catalog, so the fixture publishes the
    # same block it configures.
    connections = {"delegated_credentials": {"oauth": state.oauth_delegated_config}}
    return AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=connections),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=oauth_delegated_config(SimpleNamespace(state=state)),
        grant_store=_Store(), authority=_Authority(), minter=_minter,
    )


@pytest.mark.asyncio
async def test_agent_namespace_grant_state_governs_and_grants(card_persistence):
    # The NATIVE named-service gate's answer: which resource publishes the
    # namespace, the operation's required claims, and whether THIS agent holds
    # them — pending before the grant, granted after, ungoverned namespaces
    # impose no gate.
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"

    pending = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )
    assert pending["governed"] is True and pending["granted"] is False
    assert pending["resource"] == ns_resource
    assert pending["claims"] == ["mail:read", "named_services:use"]

    created = await service.create_access(
        _AGENT_USER, label="agent", client_id=_AGENT_CLIENT,
        resource_grants={ns_resource: pending["claims"]},
        named_service_operations={ns_resource: {"mail": ["object.search"]}},
    )
    assert created["ok"] is True

    granted = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )
    assert granted["granted"] is True

    # A costlier operation needs MORE claims -> still pending until re-grant.
    action = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.action",
    )
    assert action["governed"] is True and action["granted"] is False
    assert action["claims"] == ["mail:read", "mail:send", "named_services:use"]

    # An unpublished namespace imposes no gate.
    assert (await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="calendar", operation="object.search",
    )) == {"governed": False}


@pytest.mark.asyncio
async def test_the_native_gate_reads_the_boundary_not_the_intent(card_persistence):
    """A wildcard card is bounded by what it expanded to, on the native path too.

    The gate used to answer from the stored SELECTION and handled only `none`
    and `exact`, so a wildcard fell through to a claims-only decision and
    collected operations added after the card was issued — while the
    named-services door, reading the materialized boundary, refused them. One
    question, two answers. Both now read the boundary.
    """
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    claims = ["mail:read", "named_services:use"]

    created = await service.create_access(
        _AGENT_USER, label="agent", client_id=_AGENT_CLIENT,
        resource_grants={ns_resource: claims},
        named_service_operations="*",
    )
    assert created["ok"] is True
    assert created["access"]["named_service_operations"] == "*"

    granted = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )
    assert granted["granted"] is True

    # The card's boundary is the expansion of the generation it was saved
    # against. An operation the deployment adds later is outside it, and the
    # claims the card already holds must not be enough on their own.
    record = await service._load_record(
        agent_grant_access_id("platform-user-1", _AGENT_CLIENT, [ns_resource]),
        grantor_subject="platform-user-1",
    )
    namespaces = record.named_services["namespaces"]
    namespaces["mail"]["tools"].pop("search")

    await service._persist_record(record, expected_revision=record.card_revision)

    after = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )
    assert after["governed"] is True
    assert after["granted"] is False


@pytest.mark.asyncio
async def test_a_boundary_refusal_says_the_card_refused_not_the_claims(card_persistence):
    """The two refusals are different questions and need different answers.

    Live run 2026-08-19: the gate refused an operation added after the card was
    issued — correctly — but reported it as `missing=[memories:read,
    named_services:use]`, claims the card already held. The caller raised a
    consent demand for them, the demand deduplicated against the existing
    grant, and the agent told the user to wait for a permission they had
    already given. The design's denial table separates the two: a capability
    the catalog offers but the card lacks may follow the consent path; the
    caller has to be able to tell which case it is.
    """
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    assert (await service.create_access(
        _AGENT_USER, label="agent", client_id=_AGENT_CLIENT,
        resource_grants={ns_resource: ["mail:read", "named_services:use"]},
        named_service_operations="*",
    ))["ok"] is True

    record = await service._load_record(
        agent_grant_access_id("platform-user-1", _AGENT_CLIENT, [ns_resource]),
        grantor_subject="platform-user-1",
    )
    record.named_services["namespaces"]["mail"]["tools"].pop("search")
    await service._persist_record(record, expected_revision=record.card_revision)

    state = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )

    assert state["granted"] is False
    # The claims are held, so nothing about them is missing.
    assert state["missing_claims"] == []
    denial = state["not_granted"]
    assert denial["error"]["code"] == "delegated_capability_not_granted"
    assert denial["error"]["retryable"] is False
    path = denial["ret"]["requested_capability"]
    assert path["namespace"] == "mail" and path["operation"] == "object.search"
    # A remedy exists and its owner is the grantor.
    assert denial["ret"]["recovery"] == {
        "action": "grant_capability_in_delegated_access",
        "retry_same_request": False,
        "request_user_consent": True,
    }
    assert denial["ret"]["access_id"] == record.access_id


@pytest.mark.asyncio
async def test_a_claim_refusal_names_only_the_claims_the_card_lacks(card_persistence):
    """A demand that re-asks for held claims deduplicates away and teaches the
    caller nothing, so the missing set is the difference, not the requirement."""
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    assert (await service.create_access(
        _AGENT_USER, label="agent", client_id=_AGENT_CLIENT,
        resource_grants={ns_resource: ["named_services:use"]},
    ))["ok"] is True

    state = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )

    assert state["granted"] is False
    assert state["not_granted"] is None
    assert state["claims"] == ["mail:read", "named_services:use"]
    assert state["missing_claims"] == ["mail:read"]


@pytest.mark.asyncio
async def test_a_removal_denial_tells_the_caller_consent_will_not_help(card_persistence):
    """The mirror flag. The design's retry meaning for a withdrawn capability is
    "do not blindly retry and do not request more user consent"; the live run
    measured an agent advising a revoke-and-re-grant instead."""
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    assert (await service.create_access(
        _AGENT_USER, label="agent", client_id=_AGENT_CLIENT,
        resource_grants={ns_resource: ["mail:read", "named_services:use"]},
    ))["ok"] is True

    trimmed = copy.deepcopy(service._catalog_resolver.connections)
    trimmed["delegated_credentials"]["oauth"]["resources"][0]["named_services"][
        "namespaces"]["mail"]["tools"].pop("search")
    service._catalog_resolver.connections = trimmed

    state = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )

    recovery = state["removed"]["ret"]["recovery"]
    assert recovery["request_user_consent"] is False
    assert recovery["retry_same_request"] is False
    assert "no longer offers" in state["removed"]["error"]["message"]


@pytest.mark.asyncio
async def test_the_native_gate_refuses_an_operation_the_catalog_dropped(card_persistence):
    """The card holds it; the deployment no longer offers it. Consent cannot
    restore that, so the answer is a removal, not a pending grant."""
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    claims = ["mail:read", "named_services:use"]
    assert (await service.create_access(
        _AGENT_USER, label="agent", client_id=_AGENT_CLIENT,
        resource_grants={ns_resource: claims},
    ))["ok"] is True

    trimmed = copy.deepcopy(service._catalog_resolver.connections)
    namespaces = trimmed["delegated_credentials"]["oauth"]["resources"][0]["named_services"]["namespaces"]
    namespaces["mail"]["tools"].pop("search")
    service._catalog_resolver.connections = trimmed

    state = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )

    assert state["governed"] is True and state["granted"] is False
    removed = state["removed"]
    assert removed["error"]["code"] == "delegated_capability_no_longer_available"
    path = removed["ret"]["requested_capability"]
    assert path["namespace"] == "mail" and path["operation"] == "object.search"
    assert path["surface"] == "named_service"
    assert removed["ret"]["card_revision"] >= 1


@pytest.mark.asyncio
async def test_the_native_gate_refuses_a_namespace_the_catalog_dropped(card_persistence):
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    assert (await service.create_access(
        _AGENT_USER, label="agent", client_id=_AGENT_CLIENT,
        resource_grants={ns_resource: ["mail:read", "named_services:use"]},
        named_service_operations="*",
    ))["ok"] is True

    trimmed = copy.deepcopy(service._catalog_resolver.connections)
    trimmed["delegated_credentials"]["oauth"]["resources"][0]["named_services"]["namespaces"].pop("mail")
    service._catalog_resolver.connections = trimmed

    state = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )

    assert state["governed"] is True and state["granted"] is False
    assert state["removed"]["ret"]["requested_capability"]["namespace"] == "mail"


@pytest.mark.asyncio
async def test_save_refuses_when_the_card_moved_under_the_editor(card_persistence):
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    created = await service.create_access(
        _AGENT_USER, label="manual",
        resource_grants={ns_resource: ["named_services:use", "mail:read"]},
    )
    access_id = created["access"]["access_id"]

    conflict = await service.update_access(
        _AGENT_USER, access_id=access_id,
        resource_grants={ns_resource: ["named_services:use"]},
        expected_card_revision=int(created["access"]["card_revision"]) + 5,
    )

    assert conflict["ok"] is False
    assert conflict["error"] == "delegated_access_precondition_failed"
    assert conflict["status"] == 409
    assert conflict["mismatched"]["card_revision"]["actual"] == created["access"]["card_revision"]
    # The refreshed projection is what the editor reloads.
    assert conflict["access"]["access_id"] == access_id
    assert conflict["access"]["catalog_drift"]["status"] == "current"


@pytest.mark.asyncio
async def test_save_refuses_when_the_catalog_moved_under_the_editor(card_persistence):
    service = _named_services_agent_service(card_persistence)
    resolver = service._catalog_resolver
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    created = await service.create_access(
        _AGENT_USER, label="manual",
        resource_grants={ns_resource: ["named_services:use", "mail:read"]},
    )

    conflict = await service.update_access(
        _AGENT_USER, access_id=created["access"]["access_id"],
        resource_grants={ns_resource: ["named_services:use"]},
        expected_catalog_version="delegated_catalog_2026-01-01-00-00-00-000_000000000000",
    )

    assert conflict["status"] == 409
    assert conflict["mismatched"]["catalog_version"]["actual"] == resolver.version


@pytest.mark.asyncio
async def test_save_matching_the_expectations_proceeds(card_persistence):
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    created = await service.create_access(
        _AGENT_USER, label="manual",
        resource_grants={ns_resource: ["named_services:use", "mail:read"]},
    )

    saved = await service.update_access(
        _AGENT_USER, access_id=created["access"]["access_id"],
        resource_grants={ns_resource: ["named_services:use", "mail:read"]},
        label="Renamed",
        expected_card_revision=int(created["access"]["card_revision"]),
        expected_catalog_version=service._catalog_resolver.version,
    )

    assert saved["ok"] is True
    assert saved["access"]["label"] == "Renamed"
    assert saved["access"]["catalog_drift"]["status"] == "current"
    assert saved["pruned"] == {"resources": [], "claims": [], "named_service_operations": []}


@pytest.mark.asyncio
async def test_save_prunes_a_withdrawn_operation_instead_of_refusing(card_persistence):
    """The picker cannot render a withdrawn row, so Save removes it."""
    service = _named_services_agent_service(card_persistence)
    resolver = service._catalog_resolver
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    created = await service.create_access(
        _AGENT_USER, label="manual",
        resource_grants={ns_resource: ["named_services:use", "mail:read", "mail:send"]},
        named_service_operations={ns_resource: {"mail": ["object.search", "object.action"]}},
    )
    assert created["ok"] is True

    trimmed = copy.deepcopy(resolver.connections)
    namespaces = trimmed["delegated_credentials"]["oauth"]["resources"][0]["named_services"]["namespaces"]
    namespaces["mail"]["tools"].pop("action")
    resolver.advance(version="delegated_catalog_2026-08-14-11-00-00-000_aaaaaaaaaaaa", connections=trimmed)

    saved = await service.update_access(
        _AGENT_USER, access_id=created["access"]["access_id"],
        resource_grants={ns_resource: ["named_services:use", "mail:read", "mail:send"]},
        named_service_operations={ns_resource: {"mail": ["object.search", "object.action"]}},
    )

    assert saved["ok"] is True
    assert saved["pruned"]["named_service_operations"] == [
        {"resource": ns_resource, "namespace": "mail", "operation": "object.action"}
    ]
    assert saved["access"]["named_service_operations"] == {ns_resource: {"mail": ["object.search"]}}
    assert saved["access"]["catalog_drift"]["status"] == "current"


@pytest.mark.asyncio
async def test_save_revokes_a_card_pruning_leaves_without_authority(card_persistence):
    service = _named_services_agent_service(card_persistence)
    resolver = service._catalog_resolver
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    created = await service.create_access(
        _AGENT_USER, label="manual",
        resource_grants={ns_resource: ["named_services:use", "mail:read"]},
    )
    access_id = created["access"]["access_id"]

    emptied = copy.deepcopy(resolver.connections)
    emptied["delegated_credentials"]["oauth"]["resources"] = []
    resolver.advance(version="delegated_catalog_2026-08-14-12-00-00-000_bbbbbbbbbbbb", connections=emptied)

    saved = await service.update_access(
        _AGENT_USER, access_id=access_id,
        resource_grants={ns_resource: ["named_services:use", "mail:read"]},
    )

    assert saved["ok"] is True and saved["revoked"] is True
    assert saved["pruned"]["resources"] == [ns_resource]
    assert await service._load_record(access_id, grantor_subject="platform-user-1") is None


@pytest.mark.asyncio
async def test_listing_reports_drift_against_the_card_baseline(card_persistence):
    """Listing explains a card against the generation it was saved under."""
    service = _named_services_agent_service(card_persistence)
    resolver = service._catalog_resolver
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    created = await service.create_access(
        _AGENT_USER, label="agent", client_id=_AGENT_CLIENT,
        resource_grants={ns_resource: ["mail:read", "named_services:use"]},
        named_service_operations="*",
    )
    assert created["ok"] is True

    listed = await service.list_access(_AGENT_USER)
    assert listed["items"][0]["catalog_drift"]["status"] == "current"

    trimmed = copy.deepcopy(resolver.connections)
    namespaces = trimmed["delegated_credentials"]["oauth"]["resources"][0]["named_services"]["namespaces"]
    namespaces["mail"]["tools"].pop("search")
    resolver.advance(version="delegated_catalog_2026-08-14-10-00-00-000_ffffffffffff", connections=trimmed)

    listed = await service.list_access(_AGENT_USER)
    drift = listed["items"][0]["catalog_drift"]

    assert drift["status"] == "changed"
    assert drift["saved_version"] == TEST_CATALOG_VERSION
    assert drift["current_version"] != TEST_CATALOG_VERSION
    removed = drift["removed"]["named_service_operations"]
    assert [row["operation"] for row in removed] == ["object.search"]
    assert removed[0]["was_selected"] is True
    assert removed[0]["effect"] == "denied_immediately"


@pytest.mark.asyncio
async def test_listing_disables_editing_when_the_comparison_cannot_be_made(card_persistence):
    service = _named_services_agent_service(card_persistence)
    ns_resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    assert (await service.create_access(
        _AGENT_USER, label="agent", client_id=_AGENT_CLIENT,
        resource_grants={ns_resource: ["mail:read", "named_services:use"]},
    ))["ok"] is True
    service._catalog_resolver.unavailable = True

    listed = await service.list_access(_AGENT_USER)

    assert listed["ok"] is True                      # the card is not hidden
    assert listed["items"][0]["catalog_drift"] == {
        "status": "unavailable",
        "reason": "active_catalog_not_registered",
    }


@pytest.mark.asyncio
async def test_a_namespace_no_card_ever_held_stays_ungoverned(card_persistence):
    """Nothing was delegated here, so there is nothing to refuse."""
    service = _named_services_agent_service(card_persistence)

    assert (await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="calendar", operation="object.search",
    )) == {"governed": False}


@pytest.mark.asyncio
async def test_the_native_gate_fails_closed_when_the_catalog_is_unavailable(card_persistence):
    service = _named_services_agent_service(card_persistence)
    service._catalog_resolver.unavailable = True

    with pytest.raises(CatalogUnavailable):
        await service.agent_namespace_grant_state(
            grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
            namespace="mail", operation="object.search",
        )


@pytest.mark.asyncio
async def test_manual_automation_keeps_random_client_and_no_stored_token(card_persistence):
    service = _agent_service(card_persistence)
    created = await service.create_access(
        _AGENT_USER, label="Nightly", resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    # No client_id -> unchanged manual behavior: random automation:… client, source=manual,
    # token returned to the caller but NOT persisted in the record for reuse.
    assert created["access"]["client_id"].startswith("automation:")
    assert created["access"]["source"] == "manual"
    assert (await only_stored_card(service)).get("access_token", "") == ""


@pytest.mark.asyncio
async def test_external_client_card_extends_and_refresh_registration_merges(card_persistence):
    # The card is the authority (the guard resolves it live): a hub-side
    # extension merges claims into an EXTERNAL client's existing card, an
    # unknown client is never created here, and the refresh-time
    # re-registration (record_oauth_grant on every issuance) MERGES with the
    # card instead of clobbering the extension back to the frozen token scopes.
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
        oauth_access_id,
    )

    service = _agent_service(card_persistence)
    resource = "https://example.test/mcp"

    # Extension before any consent: nothing to extend.
    missing = await service.extend_client_access(
        _AGENT_USER, client_id="claude", resource=resource, claims=["records:read"],
    )
    assert missing["ok"] is False and missing["error"] == "delegated_access_unknown_client"

    # The card is born at OAuth consent (token issuance registers it).
    record = await service.record_oauth_grant(
        grantor_subject="platform-user-1", client_id="claude",
        scopes=["records:read"], resource=resource, access_token="tokA",
    )
    assert record is not None
    access_id = oauth_access_id("platform-user-1", "claude", resource)
    assert record.access_id == access_id

    # Hub-side extension merges the new claim into the card.
    writer = {**_AGENT_USER, "permissions": ["records:write"]}
    extended = await service.extend_client_access(
        writer, client_id="claude", resource=resource, claims=["records:write"],
    )
    assert extended["ok"] is True
    assert sorted(extended["resource_grants"][resource]) == ["records:read", "records:write"]

    # A refresh rotation re-registers with the token's OLD scopes — the card
    # keeps the extension (merge, not overwrite).
    again = await service.record_oauth_grant(
        grantor_subject="platform-user-1", client_id="claude",
        scopes=["records:read"], resource=resource, access_token="tokB",
    )
    assert sorted(again.resource_grants[resource]) == ["records:read", "records:write"]


@pytest.mark.asyncio
async def test_external_client_edit_replaces_and_narrows(card_persistence):
    # The pointer/card design: editing an external client's card REPLACES its
    # resource grants exactly (narrowing allowed, read+write -> read). The
    # guard resolves the card live, so it applies on the client's next call.
    service = _agent_service(card_persistence)
    resource = "https://example.test/mcp"

    await service.record_oauth_grant(
        grantor_subject="platform-user-1", client_id="claude",
        scopes=["records:read", "records:write"], resource=resource, access_token="tokA",
    )

    writer = {**_AGENT_USER, "permissions": ["records:write"]}
    # Merge (extension) — default: adds without dropping.
    merged = await service.extend_client_access(
        writer, client_id="claude", resource=resource, claims=["records:read"],
    )
    assert sorted(merged["resource_grants"][resource]) == ["records:read", "records:write"]

    # Replace (edit) — narrow to read only.
    narrowed = await service.extend_client_access(
        writer, client_id="claude", resource=resource, claims=["records:read"], replace=True,
    )
    assert narrowed["ok"] is True
    assert narrowed["resource_grants"][resource] == ["records:read"]  # write dropped


@pytest.mark.asyncio
async def test_agent_grant_carries_account_scope_and_merges(card_persistence):
    # The agent card carries a per-account claim binding
    # {provider: {account_id: [claims]}}; a re-grant MERGES (union) claims per
    # account, replace overwrites — "read+write on one, read-only on another".
    service = _agent_service(card_persistence)
    resource = "https://example.test/mcp"

    created = await service.create_access(
        _AGENT_USER, label="a", client_id=_AGENT_CLIENT,
        resource_grants={resource: ["records:read"]},
        account_scope={"google": {"acct-2": ["gmail:read"]}},
    )
    assert created["access"]["account_scope"] == {"google": {"acct-2": ["gmail:read"]}}

    # Merge: add gmail:send on acct-2 and a new acct-3 -> union per account.
    merged = await service.create_access(
        _AGENT_USER, label="a", client_id=_AGENT_CLIENT,
        resource_grants={resource: ["records:read"]},
        account_scope={"google": {"acct-2": ["gmail:send"], "acct-3": ["gmail:read"]}},
    )
    assert sorted(merged["access"]["account_scope"]["google"]["acct-2"]) == ["gmail:read", "gmail:send"]
    assert merged["access"]["account_scope"]["google"]["acct-3"] == ["gmail:read"]

    # Replace (edit): narrow back to acct-2 read-only.
    replaced = await service.create_access(
        _AGENT_USER, label="a", client_id=_AGENT_CLIENT,
        resource_grants={resource: ["records:read"]},
        account_scope={"google": {"acct-2": ["gmail:read"]}},
        merge_existing=False,
    )
    assert replaced["access"]["account_scope"] == {"google": {"acct-2": ["gmail:read"]}}


@pytest.mark.asyncio
async def test_agent_replace_distinguishes_omitted_scope_from_explicit_clear(card_persistence):
    service = _agent_service(card_persistence)
    resource = "https://example.test/mcp"
    await service.create_access(
        _AGENT_USER,
        label="a",
        client_id=_AGENT_CLIENT,
        resource_grants={resource: ["records:read"]},
        account_scope={"google": {"acct-2": ["gmail:read"]}},
    )

    preserved = await service.create_access(
        _AGENT_USER,
        label="a",
        client_id=_AGENT_CLIENT,
        resource_grants={resource: ["records:read"]},
        merge_existing=False,
    )
    assert preserved["access"]["account_scope"] == {
        "google": {"acct-2": ["gmail:read"]}
    }

    cleared = await service.create_access(
        _AGENT_USER,
        label="a",
        client_id=_AGENT_CLIENT,
        resource_grants={resource: ["records:read"]},
        account_scope={},
        merge_existing=False,
    )
    assert cleared["access"].get("account_scope", {}) == {}


@pytest.mark.asyncio
async def test_agent_grant_account_scope_accepts_legacy_list_form(card_persistence):
    # Backward compat: the old {provider: [account_ids]} form migrates to
    # {account_id: ["*"]} (bound to those accounts, any claim) — no breakage.
    service = _agent_service(card_persistence)
    resource = "https://example.test/mcp"
    created = await service.create_access(
        _AGENT_USER, label="a", client_id=_AGENT_CLIENT,
        resource_grants={resource: ["records:read"]},
        account_scope={"google": ["acct-2"]},
    )
    assert created["access"]["account_scope"] == {"google": {"acct-2": ["*"]}}


def test_credential_view_reads_account_scope_and_resolves_allowed():
    # The one canonical reader exposes the nested account_scope and the
    # account_claim_scope accessor; a delegated caller is default-closed for
    # an absent provider.
    from types import SimpleNamespace
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.credential_view import (
        delegated_credential_view,
    )
    req = SimpleNamespace(state=SimpleNamespace(delegated_credential={
        "credential": {"attrs": {"account_scope": {
            "google": {"acct-2": ["gmail:read"]},
            "slack": {"*": ["*"]},
        }}},
        "grant_record": {},
    }))
    view = delegated_credential_view(req)
    assert view.account_scope["google"] == {"acct-2": ("gmail:read",)}
    assert view.account_claim_scope("google") == {"acct-2": ("gmail:read",)}
    assert view.account_claim_scope("slack") == {"*": ("*",)}  # any account, any claim
    assert view.account_claim_scope("icloud") == {}


def test_credential_view_without_delegated_credential_has_no_account_restriction():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.credential_view import (
        DelegatedCredentialView,
    )

    assert DelegatedCredentialView().account_claim_scope("google") is None


@pytest.mark.asyncio
async def test_external_client_edit_account_scope_merge_and_replace(card_persistence):
    # Gap #1 closed: an external client's card account binding is editable
    # (merge extends, replace narrows), same as its claims.
    service = _agent_service(card_persistence)
    resource = "https://example.test/mcp"
    await service.record_oauth_grant(
        grantor_subject="platform-user-1", client_id="claude",
        scopes=["records:read"], resource=resource, access_token="tokA",
    )
    # Add per-account claim binding (merge).
    added = await service.extend_client_access(
        _AGENT_USER, client_id="claude", resource=resource,
        claims=[], account_scope={"google": {"acct-1": ["gmail:read"]}},
    )
    assert added["account_scope"] == {"google": {"acct-1": ["gmail:read"]}}
    # Merge a claim on acct-1 and a second account.
    both = await service.extend_client_access(
        _AGENT_USER, client_id="claude", resource=resource,
        claims=[], account_scope={"google": {"acct-1": ["gmail:send"], "acct-2": ["gmail:read"]}},
    )
    assert sorted(both["account_scope"]["google"]["acct-1"]) == ["gmail:read", "gmail:send"]
    assert both["account_scope"]["google"]["acct-2"] == ["gmail:read"]
    # Replace (narrow) to acct-2 read-only.
    narrowed = await service.extend_client_access(
        _AGENT_USER, client_id="claude", resource=resource,
        claims=[], account_scope={"google": {"acct-2": ["gmail:read"]}}, replace=True,
    )
    assert narrowed["account_scope"] == {"google": {"acct-2": ["gmail:read"]}}

    # An explicit empty map is a legitimate replacement: clear all account
    # bindings while keeping the client's existing door grants.
    cleared = await service.extend_client_access(
        _AGENT_USER,
        client_id="claude",
        resource=resource,
        claims=[],
        account_scope={},
        replace=True,
    )
    assert cleared["account_scope"] == {}


@pytest.mark.asyncio
async def test_agent_grant_state_exposes_account_scope_for_native_gate(card_persistence):
    # Gap #3 source: the native gate reads account_scope off AGENT_GRANT_CHECK.
    service = _named_services_agent_service(card_persistence)
    door = "*/kdcube-services@1-0/public/mcp/named_services*"
    await service.create_access(
        _AGENT_USER, label="a", client_id=_AGENT_CLIENT,
        resource_grants={door: ["named_services:use", "mail:read"]},
        account_scope={"google": {"acct-2": ["gmail:read"]}},
    )
    state = await service.agent_namespace_grant_state(
        grantor_subject="platform-user-1", client_id=_AGENT_CLIENT,
        namespace="mail", operation="object.search",
    )
    assert state["governed"] is True
    assert state["account_scope"] == {"google": {"acct-2": ["gmail:read"]}}


@pytest.mark.asyncio
async def test_agent_binding_carries_the_card_pointer_for_live_resolution(card_persistence):
    # Gap #2 fix: an agent bearer's binding is a POINTER onto its card
    # (registry_access_id = the card access_id), so the guard resolves the card
    # live and an edit (claims OR account_scope) applies to the reused agent
    # bearer on its next call — not only after a re-mint, matching OAuth.
    service = _agent_service(card_persistence)
    resource = "https://example.test/mcp"
    result = await service.create_access(
        _AGENT_USER, label="a", client_id=_AGENT_CLIENT,
        resource_grants={resource: ["records:read"]},
        account_scope={"google": ["acct-2"]},
    )
    access_id = result["access"]["access_id"]
    assert access_id  # deterministic agent card id
    # The last bind carries the pointer to that exact card.
    assert store_bound_pointer(card_persistence, service) == access_id


def store_bound_pointer(card_persistence, service):
    # The _agent_service(card_persistence)'s store is a fresh _Store; its last bind records
    # registry_access_id when the pointer was passed.
    bound = getattr(service._store, "bound", [])
    return bound[-1].get("registry_access_id") if bound else None


@pytest.mark.asyncio
async def test_disconnecting_an_account_clears_its_agent_bindings(card_persistence):
    """Disconnecting a connected account must drop it from every grant that
    binds it. Account ids are deterministic, so a binding left behind would
    silently revive - re-granting access nobody ticked - if the same account
    were reconnected later."""
    redis = _Redis()
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=redis,
        tenant="demo-tenant",
        project="demo-project",
        config=_config(),
        grant_store=_Store(),
        authority=_Authority(),
        minter=_minter,
    )
    user = {"user_id": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}
    created = await service.create_access(
        user,
        label="Agent grant",
        resource_grants={"https://example.test/mcp": ["records:read"]},
        ttl_seconds=3600,
        account_scope={
            "google": {"google_aaa": ["gmail:read"], "google_bbb": ["gmail:read"]},
            "slack": {"slack_zzz": ["slack:search"]},
        },
    )
    access_id = created["access"]["access_id"]

    result = await service.prune_account_from_grants(
        grantor_subject="platform-user-1", provider_id="google", account_id="google_aaa",
    )
    assert result["pruned"] == 1 and access_id in result["grants"]

    record = await stored_card(service, access_id)
    scope = record["account_scope"]
    # The disconnected account is gone; its provider survives with the sibling.
    assert "google_aaa" not in scope.get("google", {})
    assert scope["google"]["google_bbb"] == ["gmail:read"]
    # An untouched provider is left exactly as it was.
    assert scope["slack"] == {"slack_zzz": ["slack:search"]}

    # Removing the LAST bound account drops the provider entirely — the runtime
    # is default-closed, so no provider entry means no access there.
    await service.prune_account_from_grants(
        grantor_subject="platform-user-1", provider_id="google", account_id="google_bbb",
    )
    record = await stored_card(service, access_id)
    assert "google" not in record["account_scope"]

    # An account nobody bound is a no-op.
    noop = await service.prune_account_from_grants(
        grantor_subject="platform-user-1", provider_id="google", account_id="google_never",
    )
    assert noop["pruned"] == 0


@pytest.mark.asyncio
async def test_automation_access_update_replaces_grants_in_place_and_keeps_identity(card_persistence):
    """Edit a manual automation IN PLACE: the grant set is replaced, the card
    (access_id/client_id) and its client-side token are kept, and no re-mint or
    re-bind happens — the guard resolves the card live, so the new scope applies
    to the existing bearer on its next call."""
    redis = _Redis()
    store = _Store()
    authority = _Authority()
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=redis, tenant="demo-tenant", project="demo-project",
        config=_config(), grant_store=store, authority=authority, minter=_minter,
    )
    user = {
        "user_id": "platform-user-1",
        "roles": ["kdcube:role:super-admin"],
        "permissions": ["records:read", "records:write"],
    }
    created = await service.create_access(
        user, label="Nightly", resource_grants={"https://example.test/mcp": ["records:read"]},
        ttl_seconds=3600,
    )
    assert created["ok"] is True
    access_id = created["access"]["access_id"]
    client_id = created["access"]["client_id"]
    created_at = created["access"]["created_at"]
    bound_before = len(store.bound)

    updated = await service.update_access(
        user, access_id=access_id,
        resource_grants={"https://example.test/mcp": ["records:write"]},
        label="Renamed",
    )
    assert updated["ok"] is True
    assert updated["access"]["access_id"] == access_id      # same card
    assert updated["access"]["client_id"] == client_id      # same client => same token
    assert updated["access"]["created_at"] == created_at    # not re-created
    assert updated["access"]["label"] == "Renamed"
    assert updated["access"]["resource_grants"] == {"https://example.test/mcp": ["records:write"]}
    assert "access_token" not in updated                    # manual token never re-issued
    assert len(store.bound) == bound_before                 # NO re-mint / re-bind

    # The live card the guard resolves now carries the new grant.
    assert (await stored_card(service, access_id))["resource_grants"] == {
        "https://example.test/mcp": ["records:write"]
    }


@pytest.mark.asyncio
async def test_automation_access_update_explicit_empty_scope_clears_binding(card_persistence):
    service = _agent_service(card_persistence)
    user = {
        "user_id": "platform-user-1",
        "roles": ["kdcube:role:super-admin"],
        "permissions": ["records:read"],
    }
    created = await service.create_access(
        user,
        label="Nightly",
        resource_grants={"https://example.test/mcp": ["records:read"]},
        account_scope={"google": {"acct-2": ["gmail:read"]}},
    )

    updated = await service.update_access(
        user,
        access_id=created["access"]["access_id"],
        resource_grants={"https://example.test/mcp": ["records:read"]},
        account_scope={},
    )

    assert updated["ok"] is True
    assert updated["access"].get("account_scope", {}) == {}


@pytest.mark.asyncio
async def test_automation_access_update_guards_ownership_existence_and_empty(card_persistence):
    redis = _Redis()
    store = _Store()
    authority = _Authority()
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=redis, tenant="demo-tenant", project="demo-project",
        config=_config(), grant_store=store, authority=authority, minter=_minter,
    )
    owner = {"user_id": "owner", "roles": ["kdcube:role:registered"], "permissions": []}
    created = await service.create_access(
        owner, label="x", resource_grants={"https://example.test/mcp": ["records:read"]}, ttl_seconds=3600,
    )
    access_id = created["access"]["access_id"]

    missing = await service.update_access(
        owner, access_id="aut_missing", resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    assert missing["ok"] is False and missing["error"] == "delegated_access_not_found"

    intruder = {"user_id": "intruder", "roles": ["kdcube:role:registered"], "permissions": []}
    # Loads are grantor-scoped, so another user's card is simply not visible.
    # The response no longer distinguishes "exists but yours" from "absent".
    not_owned = await service.update_access(
        intruder, access_id=access_id, resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    assert not_owned["ok"] is False and not_owned["error"] == "delegated_access_not_found"

    empty = await service.update_access(owner, access_id=access_id, resource_grants={})
    assert empty["ok"] is False and empty["error"] == "delegated_access_requires_resource_grants"


@pytest.mark.asyncio
async def test_automation_access_update_replaces_named_service_operations_without_rebinding(card_persistence):
    """Editing the namespace selection rewrites the card only; no binding is
    written."""
    store = _Store()
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_named_services_connections()),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=_named_services_config(), grant_store=store, authority=_Authority(),
        minter=_minter, named_service_discovery=_NamedServiceDiscovery({}),
    )
    user = {"user_id": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}
    resource = "https://example.test/mcp/named-services"
    grants = ["named_services:use", "slack:read", "slack:write"]

    created = await service.create_access(
        user, label="Slack search only", resource_grants={resource: grants},
        named_service_operations={resource: {"slack": ["object.search"]}},
    )
    assert created["ok"] is True
    access_id = created["access"]["access_id"]
    bound_before = len(store.bound)

    widened = await service.update_access(
        user, access_id=created["access"]["access_id"],
        resource_grants={resource: grants},
        named_service_operations={resource: {"slack": ["object.search", "object.action"]}},
    )

    assert widened["ok"] is True
    assert widened["access"]["named_service_operations"] == {
        resource: {"slack": ["object.search", "object.action"]},
    }
    assert len(store.bound) == bound_before      # no re-mint, no re-bind

    # The tree the guard copies onto the request is recomputed from the
    # descriptor and now carries the added operation.
    card = await stored_card(service, access_id)
    tools = card["named_services"]["namespaces"]["slack"]["tools"]
    assert set(tools["call"]["operations"]) == {"object.search", "object.action"}

    cleared = await service.update_access(
        user, access_id=created["access"]["access_id"],
        resource_grants={resource: grants},
        named_service_operations={resource: {}},
    )

    assert cleared["access"]["named_service_operations"] == {resource: {}}
    assert len(store.bound) == bound_before
    card = await stored_card(service, access_id)
    assert card["named_services"]["namespaces"] == {}


@pytest.mark.asyncio
async def test_automation_access_update_rejects_an_operation_its_grants_do_not_cover(card_persistence):
    """The selection is validated at save time."""
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_named_services_connections()),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=_named_services_config(), grant_store=_Store(), authority=_Authority(),
        minter=_minter, named_service_discovery=_NamedServiceDiscovery({}),
    )
    user = {"user_id": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}
    resource = "https://example.test/mcp/named-services"

    created = await service.create_access(
        user, label="Slack search only",
        resource_grants={resource: ["named_services:use", "slack:read"]},
        named_service_operations={resource: {"slack": ["object.search"]}},
    )

    denied = await service.update_access(
        user, access_id=created["access"]["access_id"],
        resource_grants={resource: ["named_services:use", "slack:read"]},
        named_service_operations={resource: {"slack": ["object.action"]}},
    )

    assert denied["ok"] is False
    assert denied["error"] == "invalid_named_service_operation_selection"


@pytest.mark.asyncio
async def test_automation_access_update_omitting_the_narrowing_keeps_it(card_persistence):
    """Absent vs empty, same rule as account_scope: omitting the narrowing keeps
    the record's, an explicit {} widens to the full policy."""
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_named_services_connections()),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=_named_services_config(), grant_store=_Store(), authority=_Authority(),
        minter=_minter, named_service_discovery=_NamedServiceDiscovery({}),
    )
    user = {"user_id": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}
    resource = "https://example.test/mcp/named-services"
    grants = ["named_services:use", "slack:read", "slack:write"]
    created = await service.create_access(
        user, label="Slack search only", resource_grants={resource: grants},
        named_service_operations={resource: {"slack": ["object.search"]}},
    )
    access_id = created["access"]["access_id"]

    async def _card():
        raw = await stored_card(service, access_id)
        namespaces = raw["named_services"]["namespaces"]
        return raw.get("named_service_operations"), sorted(
            namespaces.get("slack", {}).get("tools", {}).get("call", {}).get("operations", {})
        )

    renamed = await service.update_access(
        user, access_id=access_id, resource_grants={resource: grants}, label="Renamed",
    )
    assert renamed["ok"] is True
    assert await _card() == ({resource: {"slack": ["object.search"]}}, ["object.search"])

    await service.update_access(
        user, access_id=access_id, resource_grants={resource: grants},
        named_service_operations={},
    )
    selection, operations = await _card()
    assert selection == {}
    assert operations == []


@pytest.mark.asyncio
async def test_agent_grant_replace_without_the_narrowing_keeps_it(card_persistence):
    """A replace edit that submits no narrowing (a rename) must not widen the
    agent's boundary."""
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_named_services_connections()),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=_named_services_config(), grant_store=_Store(), authority=_Authority(),
        minter=_minter, named_service_discovery=_NamedServiceDiscovery({}),
    )
    user = {"user_id": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}
    resource = "https://example.test/mcp/named-services"
    grants = ["named_services:use", "slack:read", "slack:write"]
    await service.create_access(
        user, label="agent", client_id="kdcube-agent:app:a1",
        resource_grants={resource: grants},
        named_service_operations={resource: {"slack": ["object.search"]}},
    )

    renamed = await service.create_access(
        user, label="agent renamed", client_id="kdcube-agent:app:a1",
        resource_grants={resource: grants}, merge_existing=False,
    )

    assert renamed["access"]["named_service_operations"] == {
        resource: {"slack": ["object.search"]},
    }


@pytest.mark.asyncio
async def test_automation_access_update_empty_narrowing_clears_every_resource(card_persistence):
    """An explicit {} is the clear, and it reaches resources the caller did not
    name — the widget therefore omits the field when it has no selection."""
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_named_services_connections()),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=_named_services_config(), grant_store=_Store(), authority=_Authority(),
        minter=_minter, named_service_discovery=_NamedServiceDiscovery({}),
    )
    user = {"user_id": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}
    resource = "https://example.test/mcp/named-services"
    grants = ["named_services:use", "slack:read"]
    created = await service.create_access(
        user, label="all ops", resource_grants={resource: grants},
        named_service_operations="*",
    )
    access_id = created["access"]["access_id"]

    async def _namespaces():
        raw = await stored_card(service, access_id)
        return sorted(raw["named_services"]["namespaces"])

    # Every operation the catalog offered, which the bridge still gates per
    # operation against the card's grants.
    assert await _namespaces() == ["mail", "slack"]

    # Omitted -> the wildcard is frozen into what it already meant, so it can
    # no longer be re-pinned to a later catalog. Only namespaces this card's
    # claims authorize survive, because an exact selection may name nothing
    # else; `mail` needs `mail:read`, which this card never held, so it was
    # unusable before and is unusable now.
    await service.update_access(
        user, access_id=access_id, resource_grants={resource: grants}, label="Renamed",
    )
    assert await _namespaces() == ["slack"]

    await service.update_access(
        user, access_id=access_id, resource_grants={resource: grants},
        named_service_operations={},
    )
    assert await _namespaces() == []               # explicit {} -> cleared


def test_a_catalog_tool_whose_claim_was_never_declared_is_reported():
    """The defect that shipped: a resource entry naming claims the deployment
    never declared under `capabilities`. Both halves live in the same config
    block and both are this deployment's decision, but nothing compared them —
    so the access card rendered, the grant failed, and the refusal talked about
    the claim instead of the missing declaration."""
    state = SimpleNamespace(
        oauth_delegated_config={
            "enabled": True,
            "tenant": "demo-tenant",
            "project": "demo-project",
            "capabilities": [
                {"grant": "records:read", "label": "Read records",
                 "delegable_roles": ["kdcube:role:registered"]},
            ],
            "resources": [
                {
                    "resource": "https://example.test/mcp",
                    "label": "Example MCP",
                    "tools": {
                        "records_export": {"label": "Export", "grants": ["records:read"]},
                        "records_purge": {"label": "Purge", "grants": ["records:purge"]},
                    },
                },
            ],
        }
    )
    cfg = oauth_delegated_config(SimpleNamespace(state=state))
    assert cfg.ungrantable_resource_tools() == (
        ("https://example.test/mcp", "records_purge", "records:purge"),
    )


def test_a_coherent_catalog_reports_nothing():
    assert _config().ungrantable_resource_tools() == ()


async def test_consent_seeds_the_account_binding_from_the_clients_own_card(card_persistence):
    """The pre-check a re-consent screen shows comes from durable storage.

    The client's own card is one of two seed sources; the other is a superseded
    DCR sibling. Reading the card through anything but the durable store finds
    nothing, and the screen silently drops the binding it should pre-check.
    """
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_config(),
        grant_store=_OAuthStore(),
        authority=_Authority(),
        minter=_minter,
    )
    record = await service.record_oauth_grant(
        grantor_subject="platform-user-1",
        client_id="claude",
        client_label="Claude",
        scopes=["records:read"],
        resource="https://example.test/mcp",
        access_token="kst1.oauth.token",
        refresh_token="refresh-1",
        account_scope={"slack": {"acct-1": ["chat:write"]}},
    )
    assert record is not None
    assert record.account_scope == {"slack": {"acct-1": ("chat:write",)}}

    seeded = await service.oauth_seed_account_scope(
        grantor_subject="platform-user-1",
        client_id="claude",
        resource="https://example.test/mcp",
    )
    assert seeded == {"slack": {"acct-1": ["chat:write"]}}


async def test_a_card_keyed_by_a_concrete_url_resolves_to_its_catalog_row(card_persistence):
    """An OAuth card names the URL the client asked for; the catalog names a
    pattern. The row is matched, not compared, so the card stays editable and
    the listing tells a surface which row governs it."""
    pattern = "https://example.test/mcp/named-services*"
    concrete = "https://example.test/mcp/named-services/instance-7"
    oauth = copy.deepcopy(NAMED_SERVICES_OAUTH)
    for row in oauth["resources"]:
        if row["resource"] == "https://example.test/mcp/named-services":
            row["resource"] = pattern
    connections = {"delegated_credentials": {"oauth": oauth}}
    config = oauth_delegated_config(
        SimpleNamespace(state=SimpleNamespace(oauth_delegated_config=oauth))
    )
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=connections),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=config,
        grant_store=_OAuthStore(),
        authority=_Authority(),
        minter=_minter,
    )
    user = {"sub": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}
    record = await service.record_oauth_grant(
        grantor_subject="platform-user-1",
        client_id="dcr-claude",
        client_label="Claude",
        scopes=["named_services:use", "mail:read"],
        resource=concrete,
        access_token="kst1.oauth.token",
        refresh_token="refresh-1",
    )
    assert record is not None

    listed = await service.list_access(user)
    item = next(row for row in listed["items"] if row["access_id"] == record.access_id)
    # The surface is told which row governs the card, so it never compares.
    assert item["catalog_row_by_resource"] == {concrete: pattern}

    # And the same key is editable: unknown_resources would mean the save path
    # judged the card by string equality too.
    updated = await service.update_access(
        user,
        access_id=record.access_id,
        resource_grants={concrete: ["named_services:use", "mail:read"]},
        named_service_operations={concrete: {"mail": ["object.search"]}},
    )
    assert updated.get("ok") is True, updated
    assert updated["access"]["named_service_operations"] == {
        concrete: {"mail": ["object.search"]}
    }
    # The boundary comes from the row's subtree but the grants and the
    # selection are read under the card's key, so the tree is not empty.
    stored = await only_stored_card(service)
    assert stored["named_services"], stored["named_services"]


async def test_a_revoked_card_can_be_granted_again(card_persistence):
    """Revoke does not poison a deterministic access_id.

    `oauth_access_id` and `agent_grant_access_id` are derived from
    (grantor, client, resource), so the same client reconnecting reuses the id.
    The revoked revision keeps the counter; the new consent continues it.
    """
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_connections()),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=_config(), grant_store=_OAuthStore(),
        authority=_Authority(), minter=_minter,
    )
    user = {"sub": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}
    first = await service.record_oauth_grant(
        grantor_subject="platform-user-1", client_id="dcr-claude", client_label="Claude",
        scopes=["records:read"], resource="https://example.test/mcp",
        access_token="kst1.a", refresh_token="r-a",
    )
    assert first is not None and first.card_revision == 1

    revoked = await service.revoke_access(user, access_id=first.access_id)
    assert revoked.get("ok") is True, revoked
    assert (await service.list_access(user))["items"] == []

    again = await service.record_oauth_grant(
        grantor_subject="platform-user-1", client_id="dcr-claude", client_label="Claude",
        scopes=["records:read"], resource="https://example.test/mcp",
        access_token="kst1.b", refresh_token="r-b",
    )
    assert again is not None, "reconnect after revoke produced no card"
    assert again.access_id == first.access_id
    # The counter continues past the revoked revision rather than restarting.
    assert again.card_revision > first.card_revision
    assert len((await service.list_access(user))["items"]) == 1


async def test_the_catalog_is_offered_through_what_the_grantor_may_delegate(card_persistence):
    """A claim outside the grantor's delegable set is not offered anywhere.

    Offering it produces a form whose save cannot succeed, and the picker
    auto-ticks an operation's claims, so dropping a claim from one list while
    its operation survives in another moves the wall rather than removing it.
    """
    oauth = copy.deepcopy(NAMED_SERVICES_OAUTH)
    for capability in oauth["capabilities"]:
        if capability["grant"] == "mail:send":
            capability["delegable_roles"] = ["kdcube:role:super-admin"]
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(
            connections={"delegated_credentials": {"oauth": oauth}}
        ),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=oauth_delegated_config(
            SimpleNamespace(state=SimpleNamespace(oauth_delegated_config=oauth))
        ),
        grant_store=_OAuthStore(), authority=_Authority(), minter=_minter,
    )
    admin = {"sub": "u-admin", "roles": ["kdcube:role:super-admin"], "permissions": []}
    plain = {"sub": "u-plain", "roles": ["kdcube:role:registered"], "permissions": []}

    def claims(options):
        return {grant for option in options for grant in option.get("grants") or []}

    def operations(options):
        out = set()
        for option in options:
            for namespace in option.get("named_services") or []:
                for tool_name, tool in (namespace.get("tools") or {}).items():
                    ops = tool.get("operations")
                    if isinstance(ops, dict) and ops:
                        out.update(f"{namespace['namespace']}:{op}" for op in ops)
                    else:
                        out.add(f"{namespace['namespace']}:{tool.get('operation') or tool_name}")
        return out

    wide = await service.resource_options(admin)
    narrow = await service.resource_options(plain)

    # The restricted claim is offered to the grantor who may delegate it.
    assert "mail:send" in claims(wide)
    plain_claims = claims(narrow)
    assert "mail:send" not in plain_claims, plain_claims
    assert "mail:read" in plain_claims

    # And the operation that costs it disappears with it, instead of staying
    # tickable against a claim the form no longer offers.
    assert "mail:object.action" in operations(wide)
    assert "mail:object.action" not in operations(narrow)
    assert "mail:object.search" in operations(narrow)

    # A save of what is still offered succeeds; the removed claim is refused
    # with the reason that actually applies.
    refused = await service.create_access(
        plain,
        label="Nope",
        resource_grants={"https://example.test/mcp/named-services": ["mail:send"]},
    )
    assert refused["ok"] is False
    assert refused["error"] == "delegated_access_grants_not_delegable"
    assert "not yours to delegate" in refused["message"]
    assert "capabilities" in refused["message"]


async def test_mixed_identity_scopes_are_refused_with_their_reason(card_persistence):
    """One card issues one credential, so its doors share one identity scope."""
    oauth = copy.deepcopy(NAMED_SERVICES_OAUTH)
    oauth["resources"] = list(oauth["resources"]) + [{
        "resource": "https://example.test/mcp/family",
        "label": "Family-scoped door",
        "identity_scope": "grantor_identity_family",
        "tools": {"family_call": {"label": "Call", "grants": ["named_services:use"]}},
    }]
    service = AutomationAccessService(
        catalog_resolver=_CatalogResolver(
            connections={"delegated_credentials": {"oauth": oauth}}
        ),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=oauth_delegated_config(
            SimpleNamespace(state=SimpleNamespace(oauth_delegated_config=oauth))
        ),
        grant_store=_OAuthStore(), authority=_Authority(), minter=_minter,
    )
    user = {"sub": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}
    refused = await service.create_access(
        user,
        label="Mixed",
        resource_grants={
            "https://example.test/mcp/named-services": ["named_services:use"],
            "https://example.test/mcp/family": ["named_services:use"],
        },
        named_service_operations={},
    )
    assert refused["ok"] is False
    assert refused["error"] == "delegated_access_resources_have_conflicting_identity_scopes"
    # The refusal names the conflict rather than only its code.
    assert refused["identity_scopes"] == ["grantor", "grantor_identity_family"]
    assert "identity_scope" in refused["message"]
    assert "separate cards" in refused["message"]
