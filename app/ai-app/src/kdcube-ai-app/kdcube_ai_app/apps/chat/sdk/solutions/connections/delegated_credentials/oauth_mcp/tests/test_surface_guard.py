# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp import (
    surface_guard,
)


class _GrantStore:
    def __init__(self, record=None):
        self.record = record

    async def get_access_grant_record(self, access_token: str):
        return self.record


def _authority(scopes=None):
    return {
        "schema": "kdcube.credential.v1",
        "credential_kind": "delegated_client_access",
        "issuer_authority_id": "oauth_mcp",
        "issuer_authenticator_id": "oauth_mcp.bearer",
        "subject": "integration:claude:admin",
        "audience": "kdcube:mcp",
        "attrs": {
            "scopes": list(scopes or ["conversations:read"]),
        },
    }


def _rpc_tool_call(name="conversations_export", rpc_id=1):
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


def test_managed_policy_parses_endpoint_grants_and_tools():
    policy = surface_guard.managed_mcp_auth_policy({
        "mode": "managed",
        "authority_id": "oauth_mcp",
        "grants": ["conversations:read"],
        "tools": ["conversations_export"],
    })

    assert policy is not None
    assert policy.authority_id == "oauth_mcp"
    assert policy.grants == ("conversations:read",)
    assert policy.tools == ("conversations_export",)


def test_extract_mcp_tool_calls_handles_batch():
    calls = surface_guard.extract_mcp_tool_calls(
        b"""[
          {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}},
          {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"conversations_export"}}
        ]"""
    )

    assert calls == [(2, "conversations_export")]


def _client(monkeypatch, *, grant_record):
    async def fake_authenticate(token: str):
        if token != "reader":
            return None
        return {
            "sub": "integration:claude:admin",
            "roles": ["kdcube:role:feedback-reader"],
            "permissions": ["kdcube:*:conversations:*;read"],
        }

    monkeypatch.setattr(
        surface_guard,
        "_authenticate_oauth_mcp_access_token",
        fake_authenticate,
    )

    app = FastAPI()
    app.state.oauth_grant_store = _GrantStore(grant_record)
    auth = {
        "mode": "managed",
        "authority_id": "oauth_mcp",
        "grants": ["conversations:read"],
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
        return denial or JSONResponse({"ok": True})

    return TestClient(app)


def test_managed_guard_allows_consented_tool(monkeypatch):
    client = _client(
        monkeypatch,
        grant_record={
            "tools": ["conversations_export"],
            "authority": _authority(),
        },
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_managed_guard_fails_closed_when_tool_not_consented(monkeypatch):
    client = _client(
        monkeypatch,
        grant_record={
            "tools": [],
            "authority": _authority(),
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


def test_managed_guard_requires_bearer(monkeypatch):
    client = _client(
        monkeypatch,
        grant_record={
            "tools": ["conversations_export"],
            "authority": _authority(),
        },
    )

    response = client.post("/guard", json=_rpc_tool_call())

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
