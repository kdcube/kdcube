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
    assert revoked == {"ok": True, "removed": True, "session_removed": True}
    assert authority.logged_out == ["session-1"]
    assert await service.list_access(user) == {
        "ok": True,
        "platform_user_id": "platform-user-1",
        "grant_options": listed["grant_options"],
        "resources": listed["resources"],
        "items": [],
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
