# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth import (
    surface_guard,
)

GUARD_RESOURCE = "http://testserver/guard"


class _GrantStore:
    def __init__(self, record=None):
        self.record = record

    async def get_access_grant_record(self, access_token: str):
        return self.record


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
            "resource": resource,
            "grantor_subject": grantor_subject,
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


def _client(monkeypatch, *, grant_record, auth=None, user=None, return_projection=False):
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
    app.state.oauth_grant_store = _GrantStore(grant_record)
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
        return denial or JSONResponse({"ok": True})

    return TestClient(app)


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
            "tools": ["records_export"],
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
            "tools": ["records_export"],
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
            "tools": ["records_export"],
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
            "tools": ["memory_search"],
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
            "tools": ["memory_search"],
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
            "tools": ["memory_search", "memory_delete"],
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
            "tools": [],
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
            "tools": ["records_export"],
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
    authority["attrs"].pop("resource")
    client = _client(
        monkeypatch,
        grant_record={
            "tools": ["records_export"],
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
    client = _client(
        monkeypatch,
        grant_record={
            "tools": ["records_export"],
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
            "tools": ["records_export"],
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
