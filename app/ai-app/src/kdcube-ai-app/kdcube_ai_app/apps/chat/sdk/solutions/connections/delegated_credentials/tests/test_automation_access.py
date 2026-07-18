# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Tests for user-created delegated automation access."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
    AutomationAccessService,
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


def _config():
    state = SimpleNamespace(
        oauth_delegated_config={
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
    )
    return oauth_delegated_config(SimpleNamespace(state=state))


def _named_services_config():
    state = SimpleNamespace(
        oauth_delegated_config={
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
    )
    return oauth_delegated_config(SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_automation_access_create_list_and_revoke():
    redis = _Redis()
    store = _Store()
    authority = _Authority()
    service = AutomationAccessService(
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

    raw_record = next(iter(redis.values.values()))
    assert json.loads(raw_record)["session_id"] == "session-1"

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
async def test_resource_options_project_exact_named_service_and_provider_catalogs():
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
    )
    assert created["ok"] is True
    persisted_policy = store.bound[0]["named_services"]
    assert persisted_policy["namespaces"]["slack"]["tools"]["action"]["operation"] == "object.action"
    assert "connected_accounts" not in persisted_policy["namespaces"]["slack"]


@pytest.mark.asyncio
async def test_automation_access_persists_only_selected_named_service_operations():
    store = _Store()
    service = AutomationAccessService(
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
async def test_automation_access_rejects_named_service_operation_without_its_grants():
    service = AutomationAccessService(
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
async def test_automation_access_rejects_non_delegable_grant():
    service = AutomationAccessService(
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

    assert denied == {
        "ok": False,
        "error": "delegated_access_grants_not_delegable",
        "grants": ["records:read"],
    }


@pytest.mark.asyncio
async def test_automation_access_requires_configured_resource_when_catalog_exists():
    service = AutomationAccessService(
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
    assert unknown == {
        "ok": False,
        "error": "delegated_access_unknown_resources",
        "resources": ["https://example.test/other"],
    }


@pytest.mark.asyncio
async def test_automation_access_all_resources_is_admin_only():
    service = AutomationAccessService(
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
    assert denied == {
        "ok": False,
        "error": "delegated_access_grants_not_delegable",
        "grants": ["kdcube:role:super-admin"],
    }

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
async def test_automation_access_can_select_multiple_resources():
    service = AutomationAccessService(
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
async def test_oauth_grant_registers_lists_and_revokes():
    """An external client connecting via OAuth becomes a visible, revocable grant."""
    redis = _Redis()
    store = _OAuthStore()
    user = {
        "sub": "platform-user-1",
        "roles": ["kdcube:role:registered"],
        "permissions": [],
    }
    service = AutomationAccessService(
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


def _agent_service():
    return AutomationAccessService(
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
async def test_create_access_with_agent_client_id_is_deterministic_and_stores_token():
    service = _agent_service()
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
async def test_reconsent_updates_one_record_and_preserves_created_at():
    service = _agent_service()
    first = await service.create_access(
        _AGENT_USER, label="lg-react", client_id=_AGENT_CLIENT,
        resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    listed = await service.list_access(_AGENT_USER)
    assert len(listed["items"]) == 1
    created_at = json.loads(next(iter(service._redis.values.values())))["created_at"]

    again = await service.create_access(
        _AGENT_USER, label="lg-react (relabeled)", client_id=_AGENT_CLIENT,
        resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    # Same deterministic access_id -> re-consent updates the SAME record, not a pile-up.
    assert again["access"]["access_id"] == first["access"]["access_id"]
    assert len((await service.list_access(_AGENT_USER))["items"]) == 1
    assert json.loads(next(iter(service._redis.values.values())))["created_at"] == created_at


@pytest.mark.asyncio
async def test_agent_access_token_none_when_no_grant_or_scope_mismatch():
    service = _agent_service()
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
async def test_agent_regrant_MERGES_claims_never_replaces():
    # Sequential one-click grants on the SAME resource must accumulate:
    # granting write after read keeps read (a replace would silently revoke it).
    service = _agent_service()
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
async def test_agent_regrant_with_merge_existing_false_REPLACES_the_record():
    # The EDIT semantics: the user unchecked a claim; the submitted set becomes
    # the record exactly — the merge default would have kept the removed claim.
    service = _agent_service()
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


def _named_services_agent_service():
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
    return AutomationAccessService(
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=oauth_delegated_config(SimpleNamespace(state=state)),
        grant_store=_Store(), authority=_Authority(), minter=_minter,
    )


@pytest.mark.asyncio
async def test_agent_namespace_grant_state_governs_and_grants():
    # The NATIVE named-service gate's answer: which resource publishes the
    # namespace, the operation's required claims, and whether THIS agent holds
    # them — pending before the grant, granted after, ungoverned namespaces
    # impose no gate.
    service = _named_services_agent_service()
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
async def test_manual_automation_keeps_random_client_and_no_stored_token():
    service = _agent_service()
    created = await service.create_access(
        _AGENT_USER, label="Nightly", resource_grants={"https://example.test/mcp": ["records:read"]},
    )
    # No client_id -> unchanged manual behavior: random automation:… client, source=manual,
    # token returned to the caller but NOT persisted in the record for reuse.
    assert created["access"]["client_id"].startswith("automation:")
    assert created["access"]["source"] == "manual"
    assert json.loads(next(iter(service._redis.values.values()))).get("access_token", "") == ""
