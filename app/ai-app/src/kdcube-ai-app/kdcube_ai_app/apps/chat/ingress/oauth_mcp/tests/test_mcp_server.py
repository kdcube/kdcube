# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for the MCP JSON-RPC endpoint at /mcp: the bearer -> feedback-reader gate,
the unauthenticated RFC 9728 challenge, and initialize/tools/list/tools/call
dispatch. The tool runner is injected so no data layer is needed.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.ingress.oauth_mcp import mount_oauth_mcp
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.store import GrantStore
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.helpers import enable_oauth_mcp

ISSUER = "https://yey.boats"


async def _authenticate(token):
    table = {
        "reader-tok": {"sub": "integration:claude:admin", "roles": ["kdcube:role:feedback-reader"]},
        "admin-tok": {"sub": "google:admin", "roles": ["kdcube:role:super-admin"]},
        "user-tok": {"sub": "google:user", "roles": ["kdcube:role:chat-user"]},
    }
    return table.get(token)


async def _export_runner(arguments, user):
    # Echo the args so the test can assert pass-through; tag the caller.
    return {"echo_args": dict(arguments), "called_by": user["sub"], "records": []}


def _seed_grant(store, token, tools):
    """Seed an access-grant record (sync) for a bearer token -> consented tools."""
    import hashlib
    h = hashlib.sha256(token.encode("utf-8")).hexdigest()
    store._r.values[store._key("agrant", h)] = json.dumps({"tools": tools})


@pytest.fixture
def client():
    app = FastAPI()
    enable_oauth_mcp(app, issuer=ISSUER)
    mount_oauth_mcp(app)
    app.state.oauth_authenticate = _authenticate
    app.state.mcp_tools = {"conversations_export": _export_runner}
    store = GrantStore(FakeRedis(), tenant="home", project="demo")
    app.state.oauth_grant_store = store
    # reader-tok consented to conversations_export (the normal happy path).
    _seed_grant(store, "reader-tok", ["conversations_export"])
    return TestClient(app)


def _rpc(method, params=None, id=1):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


def test_mcp_requires_bearer(client):
    r = client.post("/mcp", json=_rpc("tools/list"))
    assert r.status_code == 401
    assert 'resource_metadata="' in r.headers.get("WWW-Authenticate", "")


def test_mcp_rejects_invalid_bearer(client):
    r = client.post("/mcp", json=_rpc("tools/list"), headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_mcp_rejects_non_reader_role(client):
    r = client.post("/mcp", json=_rpc("tools/list"), headers={"Authorization": "Bearer user-tok"})
    assert r.status_code == 403


def test_initialize_returns_server_info(client):
    r = client.post("/mcp", json=_rpc("initialize"), headers={"Authorization": "Bearer reader-tok"})
    assert r.status_code == 200
    res = r.json()["result"]
    assert "protocolVersion" in res
    assert "tools" in res["capabilities"]
    assert res["serverInfo"]["name"]


def test_tools_list_advertises_export(client):
    r = client.post("/mcp", json=_rpc("tools/list"), headers={"Authorization": "Bearer reader-tok"})
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "conversations_export" in names
    export = next(t for t in tools if t["name"] == "conversations_export")
    assert "inputSchema" in export
    assert export["inputSchema"]["type"] == "object"


def test_tools_call_runs_export_and_passes_args(client):
    params = {"name": "conversations_export", "arguments": {"since": "2026-06-10T00:00:00Z"}}
    r = client.post("/mcp", json=_rpc("tools/call", params), headers={"Authorization": "Bearer reader-tok"})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result.get("isError") in (False, None)
    payload = json.loads(result["content"][0]["text"])
    assert payload["echo_args"]["since"] == "2026-06-10T00:00:00Z"
    assert payload["called_by"] == "integration:claude:admin"


def test_tools_call_unknown_tool_errors(client):
    params = {"name": "conversations_delete", "arguments": {}}
    r = client.post("/mcp", json=_rpc("tools/call", params), headers={"Authorization": "Bearer reader-tok"})
    # Either a JSON-RPC error or an MCP tool error, but never a success result.
    body = r.json()
    assert "error" in body or body.get("result", {}).get("isError") is True


def test_unknown_method_is_jsonrpc_error(client):
    r = client.post("/mcp", json=_rpc("frobnicate"), headers={"Authorization": "Bearer reader-tok"})
    body = r.json()
    assert body["error"]["code"] == -32601


# ---- consent enforcement (reviewer fix: granted tools must be enforced) ----

def test_tools_call_denied_when_tool_not_consented(client):
    # The reviewer's scenario: admin unchecked the tool -> grant carries NO tools.
    store = client.app.state.oauth_grant_store
    _seed_grant(store, "reader-tok", [])  # consented to nothing
    params = {"name": "conversations_export", "arguments": {}}
    r = client.post("/mcp", json=_rpc("tools/call", params), headers={"Authorization": "Bearer reader-tok"})
    result = r.json()["result"]
    assert result["isError"] is True
    assert "not consented" in result["content"][0]["text"].lower()


def test_reader_token_without_grant_is_denied(client):
    # Fail closed: a feedback-reader token with no grant record grants nothing.
    import hashlib
    store = client.app.state.oauth_grant_store
    store._r.values.pop(store._key("agrant", hashlib.sha256(b"reader-tok").hexdigest()), None)
    r = client.post("/mcp", json=_rpc("tools/call", {"name": "conversations_export", "arguments": {}}),
                    headers={"Authorization": "Bearer reader-tok"})
    assert r.json()["result"]["isError"] is True


def test_admin_token_bypasses_tool_consent(client):
    # Admins have no grant record but retain access via role (superset).
    r = client.post("/mcp", json=_rpc("tools/call", {"name": "conversations_export", "arguments": {}}),
                    headers={"Authorization": "Bearer admin-tok"})
    assert r.json()["result"].get("isError") in (False, None)
