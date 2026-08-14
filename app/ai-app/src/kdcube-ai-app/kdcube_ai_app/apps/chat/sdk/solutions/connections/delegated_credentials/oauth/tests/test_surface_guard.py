# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth import (
    surface_guard,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import (
    bind_delegated_catalog,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import (
    GrantStoreUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
    ACCESS_SOURCE_OAUTH,
    AutomationAccessRecord,
    card_authority_from_record,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_io import (
    encode_cache_value,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
    DelegatedCardRuntimeCache,
)

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CARD_STATE_REVOKED,
    NamedServiceSelection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.service import (
    replace_state,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
    subject_hash_for,
)

GUARD_RESOURCE = "http://testserver/guard"

# The catalog generation these tests enforce against. Drift cases trim it.
GUARD_CONNECTIONS = {
    "delegated_credentials": {
        "oauth": {
            "enabled": True,
            "resources": [
                {
                    "resource": GUARD_RESOURCE,
                    "grants": [
                        "records:read",
                        "records:write",
                        "memories:read",
                        "memories:write",
                    ],
                    "tools": {
                        "records_export": {"grants": ["records:read"]},
                        "records_update": {"grants": ["records:write"]},
                        "memory_search": {"grants": ["memories:read"]},
                        "memory_delete": {"grants": ["memories:write"]},
                    },
                },
            ],
        },
    },
}


def _connections_without(*, tool: str = "", claim: str = "", resource: bool = True) -> dict:
    import copy as _copy

    trimmed = _copy.deepcopy(GUARD_CONNECTIONS)
    resources = trimmed["delegated_credentials"]["oauth"]["resources"]
    if not resource:
        resources.clear()
        return trimmed
    if tool:
        resources[0]["tools"].pop(tool, None)
    if claim:
        resources[0]["grants"] = [
            grant for grant in resources[0]["grants"] if grant != claim
        ]
    return trimmed


def _card_key(access_id: str) -> str:
    return DelegatedCardRuntimeCache(None, tenant="home", project="demo").card_key(access_id)


class _GrantStore:
    def __init__(self, record=None, redis=None):
        self.record = record
        self.redis = redis

    async def get_access_grant_record(self, access_token: str):
        if isinstance(self.record, Exception):
            raise self.record
        return self.record


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail_get = False

    async def get(self, key: str):
        if self.fail_get:
            raise RuntimeError("redis unavailable")
        return self.values.get(key)


def _authority(
    scopes=None,
    *,
    resource=GUARD_RESOURCE,
    grantor_subject="a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    identity_scope="grantor",
    subject="",
):
    subject = subject or f"integration:claude:{grantor_subject}"
    return {
        "schema": "kdcube.credential.v1",
        "credential_kind": "delegated_client_access",
        "issuer_authority_id": "delegated_client",
        "issuer_authenticator_id": "delegated_client.bearer",
        "subject": subject,
        "audience": "kdcube:delegated_client",
        "attrs": {
            "scopes": list(scopes or ["records:read"]),
            "resource_grants": {
                resource: list(scopes or ["records:read"]),
            },
            "grantor_subject": grantor_subject,
            "client_id": "claude",
            "identity_scope": identity_scope,
        },
    }


def _memory_authority():
    return _authority(scopes=["memories:read"])


def _rpc_tool_call(name="records_export", rpc_id=1):
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }


def test_mcp_auth_mode_keeps_bundle_owned_header_metadata_unmanaged():
    auth = {"header_name": "X-Knowledge-MCP-Token"}

    assert surface_guard.mcp_auth_mode(auth) == ""
    assert surface_guard.managed_mcp_auth_policy(auth) is None


def test_managed_policy_parses_per_tool_grants():
    policy = surface_guard.managed_mcp_auth_policy({
        "mode": "managed",
        "authority_id": "delegated_client",
        "tools": {
            "records_export": {
                "grants": ["records:read"],
            },
        },
    })

    assert policy is not None
    assert policy.authority_id == "delegated_client"
    assert policy.tool_policies is not None
    assert policy.tool_policies["records_export"].grants == ("records:read",)


def test_extract_mcp_tool_calls_handles_batch():
    calls = surface_guard.extract_mcp_tool_calls(
        b"""[
          {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}},
          {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"records_export"}}
        ]"""
    )

    assert calls == [(2, "records_export")]


def _client(
    monkeypatch,
    *,
    grant_record,
    auth=None,
    user=None,
    return_projection=False,
    return_named_services=False,
    redis=None,
    connections=None,
    catalog_unavailable="",
    cards=None,
):
    async def fake_authenticate(token: str):
        if token != "reader":
            return None
        return user or {
            "sub": "integration:claude:admin",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["kdcube:*:records:*;read"],
        }

    monkeypatch.setattr(
        surface_guard,
        "_authenticate_delegated_client_access_token",
        fake_authenticate,
    )

    app = FastAPI()
    app.state.oauth_grant_store = _GrantStore(grant_record, redis=redis)
    app.state.oauth_delegated_config = {"tenant": "home", "project": "demo"}
    bind_delegated_catalog(
        app,
        GUARD_CONNECTIONS if connections is None else connections,
        unavailable=catalog_unavailable,
        cards=cards,
    )
    auth = auth or {
        "mode": "managed",
        "authority_id": "delegated_client",
        "tools": {
            "records_export": {
                "grants": ["records:read"],
            },
        },
        "selected_tool_grants": True,
    }

    @app.post("/guard")
    async def guard(request: Request):
        body = await request.body()
        denial = await surface_guard.authorize_delegated_mcp_request(
            request=request,
            body=body,
            auth=auth,
        )
        if return_projection:
            projection = surface_guard.delegated_mcp_runtime_projection(request)
            return denial or JSONResponse({"ok": True, "projection": projection})
        if return_named_services:
            # What the named-service bridge reads off the request.
            delegated = getattr(request.state, "delegated_credential", None) or {}
            record = delegated.get("grant_record") or {}
            return denial or JSONResponse({"ok": True, "named_services": record.get("named_services")})
        return denial or JSONResponse({"ok": True})

    return TestClient(app)


def _rest_client(
    monkeypatch,
    *,
    grant_record,
    auth=None,
    user=None,
    operation="records_export",
    redis=None,
    connections=None,
    catalog_unavailable="",
    cards=None,
):
    async def fake_authenticate(token: str):
        if token != "reader":
            return None
        return user or {
            "sub": "integration:automation:admin",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["records:read"],
        }

    monkeypatch.setattr(
        surface_guard,
        "_authenticate_delegated_client_access_token",
        fake_authenticate,
    )

    app = FastAPI()
    app.state.oauth_grant_store = _GrantStore(grant_record, redis=redis)
    app.state.oauth_delegated_config = {"tenant": "home", "project": "demo"}
    bind_delegated_catalog(
        app,
        GUARD_CONNECTIONS if connections is None else connections,
        unavailable=catalog_unavailable,
        cards=cards,
    )
    auth = auth or {
        "mode": "managed",
        "authority_id": "delegated_client",
        "operations": {
            "records_export": {
                "grants": ["records:read"],
            },
        },
        "selected_operation_grants": True,
    }

    @app.post("/guard")
    async def guard(request: Request):
        denial = await surface_guard.authorize_delegated_rest_request(
            request=request,
            auth=auth,
            operation=operation,
            method="POST",
        )
        projection = surface_guard.delegated_rest_runtime_projection(request)
        return denial or JSONResponse({"ok": True, "projection": projection})

    return TestClient(app)


def _pointer_grant(access_id: str = "oauth-access-1") -> dict:
    return {
        "registry_access_id": access_id,
        "operations": ["records_export"],
        "credential": _authority(),
    }


def _live_card(
    *,
    access_id: str = "oauth-access-1",
    operations=("records_export",),
    resource_grants=None,
    named_service_operations=None,
    named_services=None,
    expires_at=None,
) -> AutomationAccessRecord:
    return AutomationAccessRecord(
        access_id=access_id,
        label="Claude records",
        client_id="claude",
        grantor_subject="a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
        delegate_subject=(
            "integration:claude:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
        ),
        operations=tuple(operations),
        resource_grants=(
            resource_grants
            if resource_grants is not None
            else {GUARD_RESOURCE: ("records:read",)}
        ),
        named_service_operations=(
            NamedServiceSelection.exact(named_service_operations)
            if named_service_operations
            else NamedServiceSelection.unknown()
        ),
        named_services=named_services or {},
        expires_at=int(time.time()) + 3600 if expires_at is None else int(expires_at),
        source=ACCESS_SOURCE_OAUTH,
    )


def _store_live_card(redis: _Redis, card: AutomationAccessRecord) -> None:
    authority = card_authority_from_record(card)
    redis.values[_card_key(card.access_id)] = encode_cache_value(
        {
            "kind": "card",
            "card_revision": authority.card_revision,
            "authority": authority.to_dict(),
        }
    )


def test_managed_mcp_guard_fails_closed_when_live_card_is_malformed(monkeypatch):
    redis = _Redis()
    redis.values[_card_key("oauth-access-1")] = "{"
    client = _client(
        monkeypatch,
        grant_record=_pointer_grant(),
        redis=redis,
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"


def test_managed_rest_guard_fails_closed_when_live_lookup_is_unavailable(monkeypatch):
    redis = _Redis()
    redis.fail_get = True
    client = _rest_client(
        monkeypatch,
        grant_record=_pointer_grant(),
        redis=redis,
    )

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"


def test_managed_mcp_guard_reports_grant_store_unavailable(monkeypatch, caplog):
    client = _client(
        monkeypatch,
        grant_record=GrantStoreUnavailable("access_grant.get"),
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": "temporarily_unavailable",
        "error_description": "Current delegated authorization state is unavailable",
    }
    assert "operation=access_grant.get" in caplog.text


def test_managed_rest_guard_reports_grant_store_unavailable(monkeypatch, caplog):
    client = _rest_client(
        monkeypatch,
        grant_record=GrantStoreUnavailable("access_grant.get"),
    )

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 503
    assert response.json() == {
        "error": "temporarily_unavailable",
        "error_description": "Current delegated authorization state is unavailable",
    }
    assert "operation=access_grant.get" in caplog.text


def test_managed_mcp_guard_applies_live_operation_narrowing(monkeypatch):
    redis = _Redis()
    _store_live_card(redis, _live_card(operations=()))
    client = _client(
        monkeypatch,
        grant_record=_pointer_grant(),
        redis=redis,
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "not consented" in result["content"][0]["text"]


def test_managed_guard_uses_connection_hub_resource_policy(monkeypatch):
    config = {
        "enabled": True,
        "capabilities": [{"grant": "records:read"}],
        "resources": [
            {
                "resource": GUARD_RESOURCE,
                "tools": {
                    "records_export": {
                        "grants": ["records:read"],
                    },
                },
            }
        ],
    }
    client = _client(
        monkeypatch,
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
            "selected_tool_grants": True,
        },
        user={
            "sub": "integration:claude:user",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["records:read"],
        },
        grant_record={
            "operations": ["records_export"],
            "credential": _authority(),
        },
    )
    client.app.state.oauth_delegated_config = config

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_managed_rest_guard_accepts_consented_operation(monkeypatch):
    client = _rest_client(
        monkeypatch,
        grant_record={
            "operations": ["records_export"],
            "credential": _authority(),
        },
    )

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["projection"]["schema"] == "connection_hub.delegated_rest_runtime_projection.v1"
    assert payload["projection"]["grantor_user_id"] == "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"


def test_managed_rest_guard_accepts_configured_resource_pattern(monkeypatch):
    client = _rest_client(
        monkeypatch,
        grant_record={
            "operations": ["records_export"],
            "credential": _authority(resource="http://testserver/*"),
        },
    )

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_managed_rest_guard_scopes_grants_to_matching_resource(monkeypatch):
    authority = _authority(scopes=["records:read"], resource=GUARD_RESOURCE)
    authority["attrs"]["resource_grants"] = {
        GUARD_RESOURCE: ["records:read"],
        "http://testserver/other": ["records:write"],
    }
    client = _rest_client(
        monkeypatch,
        operation="records_update",
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
            "operations": {
                "records_update": {
                    "grants": ["records:write"],
                },
            },
            "selected_operation_grants": True,
        },
        grant_record={
            "operations": ["records_update"],
            "credential": authority,
        },
    )

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 403
    assert response.json()["error_description"] == (
        "required delegated grant is missing for operation: records_update"
    )


def test_managed_rest_guard_accepts_wildcard_resource_grant(monkeypatch):
    authority = _authority(scopes=["records:read"], resource=GUARD_RESOURCE)
    authority["attrs"]["resource_grants"] = {
        "*": ["records:write"],
        GUARD_RESOURCE: ["records:read"],
    }
    client = _rest_client(
        monkeypatch,
        operation="records_update",
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
            "operations": {
                "records_update": {
                    "grants": ["records:write"],
                },
            },
            "selected_operation_grants": True,
        },
        grant_record={
            "operations": ["records_update"],
            "credential": authority,
        },
    )

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_managed_rest_guard_rejects_unconsented_operation(monkeypatch):
    client = _rest_client(
        monkeypatch,
        grant_record={
            "operations": ["records_list"],
            "credential": _authority(),
        },
    )

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 403
    assert response.json()["error_description"] == (
        "operation not consented for this connection: records_export"
    )


def test_managed_rest_guard_uses_connection_hub_resource_policy(monkeypatch):
    config = {
        "enabled": True,
        "capabilities": [{"grant": "records:read"}],
        "resources": [
            {
                "resource": GUARD_RESOURCE,
                "operations": {
                    "records_export": {
                        "grants": ["records:read"],
                    },
                },
            }
        ],
    }
    client = _rest_client(
        monkeypatch,
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
            "selected_operation_grants": True,
        },
        grant_record={
            "operations": ["records_export"],
            "credential": _authority(),
        },
    )
    client.app.state.oauth_delegated_config = config

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_managed_rest_guard_resource_policy_requires_selected_operation(monkeypatch):
    config = {
        "enabled": True,
        "capabilities": [{"grant": "records:read"}],
        "resources": [
            {
                "resource": GUARD_RESOURCE,
                "operations": {
                    "records_export": {
                        "grants": ["records:read"],
                    },
                },
            }
        ],
    }
    client = _rest_client(
        monkeypatch,
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
        },
        grant_record={
            "operations": [],
            "credential": _authority(),
        },
    )
    client.app.state.oauth_delegated_config = config

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 403
    assert response.json()["error_description"] == (
        "operation not consented for this connection: records_export"
    )


def test_managed_guard_prefers_connection_hub_resource_policy_over_surface_tools(monkeypatch):
    config = {
        "enabled": True,
        "capabilities": [{"grant": "records:read"}],
        "resources": [
            {
                "resource": GUARD_RESOURCE,
                "tools": {
                    "records_export": {
                        "grants": ["records:read"],
                    },
                },
            }
        ],
    }
    client = _client(
        monkeypatch,
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
            "tools": {
                "records_export": {
                    "grants": ["wrong:grant"],
                },
            },
            "selected_tool_grants": True,
        },
        user={
            "sub": "integration:claude:user",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["records:read"],
        },
        grant_record={
            "operations": ["records_export"],
            "credential": _authority(),
        },
    )
    client.app.state.oauth_delegated_config = config

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_managed_guard_allows_consented_tool(monkeypatch):
    client = _client(
        monkeypatch,
        grant_record={
            "operations": ["records_export"],
            "credential": _authority(),
        },
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_managed_guard_allows_configured_non_feedback_tool(monkeypatch):
    client = _client(
        monkeypatch,
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
            "tools": {
                "memory_search": {
                    "grants": ["memories:read"],
                },
            },
            "selected_tool_grants": True,
        },
        user={
            "sub": "integration:claude:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["memories:read"],
        },
        grant_record={
            "operations": ["memory_search"],
            "credential": _memory_authority(),
        },
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(name="memory_search"),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_managed_guard_exposes_runtime_projection_for_proc_bridge(monkeypatch):
    client = _client(
        monkeypatch,
        return_projection=True,
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
            "tools": {
                "memory_search": {
                    "grants": ["memories:read"],
                },
            },
            "selected_tool_grants": True,
        },
        user={
            "sub": "integration:claude:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["memories:read"],
        },
        grant_record={
            "operations": ["memory_search"],
            "credential": _authority(
                scopes=["memories:read"],
                grantor_subject="a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
                identity_scope="grantor_identity_family",
            ),
            "grantor_authority": {
                "grantor_roles": ["kdcube:role:super-admin"],
                "grantor_permissions": ["memories:read"],
                "economics_budget_bypass": True,
            },
        },
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(name="memory_search"),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    projection = response.json()["projection"]
    authority = projection["identity_authority"]
    assert projection["user_id"] == "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    assert projection["user_type"] == "external"
    assert projection["delegate_identity"] == "integration:claude:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    assert projection["grantor_user_id"] == "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    assert projection["identity_scope"] == "grantor_identity_family"
    assert "memories:read" in projection["grants"]
    assert "kdcube:role:super-admin" in projection["roles"]
    assert authority["economics_user_id"] == "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    assert authority["budget_bypass"] is True
    assert authority["actor_identity"] == "integration:claude:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"


def test_managed_guard_enforces_grants_per_called_tool(monkeypatch):
    client = _client(
        monkeypatch,
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
            "tools": {
                "memory_search": {"grants": ["memories:read"]},
                "memory_delete": {"grants": ["memories:write"]},
            },
            "selected_tool_grants": True,
        },
        user={
            "sub": "integration:claude:user",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["memories:read"],
        },
        grant_record={
            "operations": ["memory_search", "memory_delete"],
            "credential": _memory_authority(),
        },
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(name="memory_delete"),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "required delegated grant is missing for tool: memory_delete" in result["content"][0]["text"]


def test_managed_guard_fails_closed_when_tool_not_consented(monkeypatch):
    client = _client(
        monkeypatch,
        grant_record={
            "operations": [],
            "credential": _authority(),
        },
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "not consented" in result["content"][0]["text"]


def test_managed_guard_rejects_resource_mismatch(monkeypatch):
    client = _client(
        monkeypatch,
        grant_record={
            "operations": ["records_export"],
            "credential": _authority(resource="http://testserver/other"),
        },
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 403
    assert response.json()["error_description"] == "delegated credential resource mismatch"


def test_managed_guard_rejects_missing_resource(monkeypatch):
    authority = _authority()
    authority["attrs"].pop("resource_grants")
    client = _client(
        monkeypatch,
        grant_record={
            "operations": ["records_export"],
            "credential": authority,
        },
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 403
    assert response.json()["error_description"] == "delegated credential resource is missing"


def test_managed_guard_compares_forwarded_public_resource(monkeypatch):
    # The catalog selector is a pattern, as a deployed descriptor writes it.
    forwarded_catalog = {
        "delegated_credentials": {
            "oauth": {
                "enabled": True,
                "resources": [
                    {
                        "resource": "*/guard",
                        "grants": ["records:read"],
                        "tools": {"records_export": {"grants": ["records:read"]}},
                    },
                ],
            },
        },
    }
    client = _client(
        monkeypatch,
        connections=forwarded_catalog,
        grant_record={
            "operations": ["records_export"],
            "credential": _authority(
                resource=(
                    "https://broodier-maxie-uninferrably.ngrok-free.dev"
                    "/guard"
                )
            ),
        },
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={
            "Authorization": "Bearer reader",
            "Host": "chat-proc:8020",
            "X-Forwarded-Proto": "http",
            "X-Forwarded-Host": "broodier-maxie-uninferrably.ngrok-free.dev",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_managed_guard_requires_bearer(monkeypatch):
    client = _client(
        monkeypatch,
        grant_record={
            "operations": ["records_export"],
            "credential": _authority(),
        },
    )

    response = client.post("/guard", json=_rpc_tool_call())

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_oauth_challenge_uses_forwarded_public_origin():
    request = StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/integrations/bundles/demo-tenant/demo-project/kdcube-services@1-0/public/mcp/example",
            "query_string": b"",
            "server": ("chat-proc", 8020),
            "headers": [
                (b"host", b"chat-proc:8020"),
                (b"x-forwarded-proto", b"http"),
                (b"x-forwarded-host", b"broodier-maxie-uninferrably.ngrok-free.dev"),
            ],
            "path_params": {
                "tenant": "demo-tenant",
                "project": "demo-project",
            },
        }
    )

    headers = surface_guard._oauth_challenge_headers(request, {"mode": "managed"})

    challenge = headers["WWW-Authenticate"]
    assert (
        "https://broodier-maxie-uninferrably.ngrok-free.dev/"
        "api/integrations/bundles/demo-tenant/demo-project/connection-hub@1-0/public/oauth"
    ) in challenge
    assert "resource=https%3A%2F%2Fbroodier-maxie-uninferrably.ngrok-free.dev" in challenge
    assert "http%3A%2F%2Fchat-proc%3A8020" not in challenge


# ── the named-service boundary rides the card ─────────────────────────────────

NAMED_SERVICES_POLICY = {
    "namespaces": {
        "records": {
            "authority_id": "delegated_client",
            "tools": {"search": {"operation": "object.search", "grants": ["records:read"]}},
        },
    },
}


def _boundary(monkeypatch, *, named_services):
    redis = _Redis()
    _store_live_card(redis, _live_card(named_services=named_services))
    client = _client(
        monkeypatch,
        grant_record={**_pointer_grant(), "named_services": {"namespaces": {"stale": {}}}},
        redis=redis,
        return_named_services=True,
    )
    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )
    assert response.status_code == 200, response.text
    return response.json()["named_services"]


def test_named_service_boundary_comes_from_the_card(monkeypatch):
    boundary = _boundary(monkeypatch, named_services=NAMED_SERVICES_POLICY)

    assert set(boundary["namespaces"]) == {"records"}


def test_named_service_boundary_narrowed_to_nothing_reaches_the_bridge(monkeypatch):
    """An empty namespace map is a real boundary, not a missing one."""
    boundary = _boundary(monkeypatch, named_services={"namespaces": {}})

    assert boundary == {"namespaces": {}}


def test_named_service_boundary_falls_back_to_the_snapshot_on_a_legacy_card(monkeypatch):
    """Cards written before the field keep the bound snapshot."""
    boundary = _boundary(monkeypatch, named_services={})

    assert set(boundary["namespaces"]) == {"stale"}


# -- current-catalog intersection ----------------------------------------------


def _denial_path(payload):
    return payload["ret"]["requested_capability"]


def test_a_resource_removed_from_the_catalog_denies_before_the_tool_runs(monkeypatch):
    client = _client(
        monkeypatch,
        connections=_connections_without(resource=False),
        grant_record={"operations": ["records_export"], "credential": _authority()},
    )

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "delegated_capability_no_longer_available"
    assert payload["error"]["retryable"] is False
    assert _denial_path(payload)["kind"] == "resource"


def test_a_tool_removed_from_the_catalog_denies_with_its_whole_path(monkeypatch):
    client = _client(
        monkeypatch,
        connections=_connections_without(tool="records_export"),
        grant_record={"operations": ["records_export"], "credential": _authority()},
    )

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    # A tool call is refused inside the RPC result, carrying the full path.
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "delegated_capability_no_longer_available"
    path = _denial_path(payload)
    assert path["kind"] == "outer_operation"
    assert path["outer_operation"] == "records_export"
    assert path["resource"] == GUARD_RESOURCE
    assert path["surface"] == "mcp"


def test_a_claim_removed_from_the_catalog_denies_the_rest_operation(monkeypatch):
    client = _rest_client(
        monkeypatch,
        connections=_connections_without(claim="records:read"),
        auth={
            "mode": "managed",
            "authority_id": "delegated_client",
            "grants": ["records:read"],
            "operations": {"records_export": {"grants": ["records:read"]}},
            "selected_operation_grants": True,
        },
        grant_record={"operations": ["records_export"], "credential": _authority()},
    )

    response = client.post("/guard", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "delegated_capability_no_longer_available"
    path = _denial_path(payload)
    assert path["kind"] == "resource_claim"
    assert path["claim"] == "records:read"


def test_a_card_that_still_matches_the_catalog_is_admitted(monkeypatch):
    client = _client(
        monkeypatch,
        grant_record={"operations": ["records_export"], "credential": _authority()},
    )

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_an_unreadable_catalog_is_retryable_unavailability_not_a_denial(monkeypatch):
    client = _client(
        monkeypatch,
        catalog_unavailable="durable_active_unreadable",
        grant_record={"operations": ["records_export"], "credential": _authority()},
    )

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "temporarily_unavailable"
    assert payload["error"]["retryable"] is True
    assert payload["ret"]["reason"] == "durable_active_unreadable"


def test_uninstalled_serving_resolvers_fail_closed(monkeypatch):
    """Absent readers are a composition failure, never "no requirement"."""
    client = _client(
        monkeypatch,
        grant_record={"operations": ["records_export"], "credential": _authority()},
    )
    delattr(client.app.state, "delegated_serving_resolvers")

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 503
    assert response.json()["ret"]["reason"] == "delegated_serving_resolvers_absent"


# -- durable card read-through --------------------------------------------------


async def _commit_card(store, card: AutomationAccessRecord, *, revoked: bool = False) -> None:
    from datetime import datetime, timezone

    authority = card_authority_from_record(card)
    if revoked:
        authority = replace_state(authority, CARD_STATE_REVOKED)
    subject = subject_hash_for(card.grantor_subject)
    pointer = await store.write_revision(
        subject_hash=subject, authority=authority, updated_at=datetime.now(timezone.utc)
    )
    await store.advance_current(subject_hash=subject, pointer=pointer)


def _durable_client(monkeypatch, store, *, redis=None):
    return _client(
        monkeypatch,
        grant_record=_pointer_grant(),
        redis=redis if redis is not None else _Redis(),
        cards=store,
    )


@pytest.mark.asyncio
async def test_an_evicted_projection_is_restored_from_the_committed_revision(
    monkeypatch, tmp_path
):
    """A lost projection is not a revoked card: the durable revision stands."""
    store = BundleStorageDelegatedCardStore(tmp_path)
    await _commit_card(store, _live_card())
    client = _durable_client(monkeypatch, store)

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_an_expired_durable_card_denies(monkeypatch, tmp_path):
    store = BundleStorageDelegatedCardStore(tmp_path)
    await _commit_card(store, _live_card(expires_at=int(time.time()) - 60))
    client = _durable_client(monkeypatch, store)

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_revoked_durable_card_denies(monkeypatch, tmp_path):
    store = BundleStorageDelegatedCardStore(tmp_path)
    await _commit_card(store, _live_card(), revoked=True)
    client = _durable_client(monkeypatch, store)

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unreadable_durable_state_is_unavailability_not_a_denial(
    monkeypatch, tmp_path
):
    store = BundleStorageDelegatedCardStore(tmp_path)
    card = _live_card()
    await _commit_card(store, card)
    # The pointer names a revision that is gone: corruption, not absence.
    subject = subject_hash_for(card.grantor_subject)
    current = await store.read_current(subject_hash=subject, access_id=card.access_id)
    store.revision_path(
        subject_hash=subject,
        access_id=card.access_id,
        revision_name=current.revision_name,
    ).unlink()
    client = _durable_client(monkeypatch, store)

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"
