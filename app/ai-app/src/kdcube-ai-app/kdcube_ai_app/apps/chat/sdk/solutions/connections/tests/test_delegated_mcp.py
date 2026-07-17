# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The per-user MCP server-map resolver (`solutions/connections/delegated_mcp.py`).

The ONE place a delegated KDCube @mcp surface gets a minted per-user bearer.
The minter is faked so no OAuth stack is exercised; the assertions pin the
governance-critical behaviors: a delegated connection injects the user's bearer;
a delegated connection with NO user is dropped (never an unauthenticated call);
a static connection keeps its declared headers untouched.
"""

from __future__ import annotations

import asyncio

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_mcp import (
    resolve_mcp_server_map,
)


def _fake_minter_calls():
    calls = []

    async def minter(sub, scopes, *, client_id, ttl_seconds):
        calls.append({"sub": sub, "scopes": list(scopes), "client_id": client_id, "ttl": ttl_seconds})
        return {"access_token": f"tok-{sub}-{'+'.join(scopes)}"}

    return minter, calls


_STATIC = {"kind": "mcp", "name": "docs", "url": "https://h/api/mcp/x", "headers": {"Authorization": "Bearer static"}}
_DELEGATED = {"kind": "mcp", "name": "memory", "url": "https://h/api/mcp/mem", "delegated": True, "scopes": ["memories:read"]}


def test_delegated_connection_injects_minted_per_user_bearer():
    minter, calls = _fake_minter_calls()
    servers = asyncio.run(resolve_mcp_server_map([_DELEGATED], user_sub="user-1", minter=minter))

    assert servers["memory"]["url"] == "https://h/api/mcp/mem"
    assert servers["memory"]["transport"] == "streamable_http"
    assert servers["memory"]["headers"]["Authorization"] == "Bearer tok-user-1-memories:read"
    # Minted for THIS user, scoped to the connection's declared grants.
    assert calls == [{"sub": "user-1", "scopes": ["memories:read"], "client_id": "kdcube-agent", "ttl": None}]


def test_delegated_connection_dropped_when_no_user_bound():
    minter, calls = _fake_minter_calls()
    servers = asyncio.run(resolve_mcp_server_map([_DELEGATED], user_sub=None, minter=minter))

    # No blind, unauthenticated call: the delegated server is omitted entirely.
    assert servers == {}
    assert calls == []


def test_static_connection_keeps_declared_headers_and_never_mints():
    minter, calls = _fake_minter_calls()
    servers = asyncio.run(resolve_mcp_server_map([_STATIC], user_sub="user-1", minter=minter))

    assert servers["docs"]["headers"] == {"Authorization": "Bearer static"}
    assert calls == []  # static never mints


def test_mixed_list_resolves_each_by_kind():
    minter, _ = _fake_minter_calls()
    servers = asyncio.run(resolve_mcp_server_map(
        [_STATIC, _DELEGATED, {"kind": "python", "name": "calc"}], user_sub="u", minter=minter,
    ))
    assert set(servers) == {"docs", "memory"}  # the python tool is not an MCP server
    assert servers["memory"]["headers"]["Authorization"].startswith("Bearer tok-u-")


def test_minter_failure_drops_the_server_not_the_build():
    async def boom(sub, scopes, *, client_id, ttl_seconds):
        raise RuntimeError("mint down")

    servers = asyncio.run(resolve_mcp_server_map([_DELEGATED, _STATIC], user_sub="u", minter=boom))
    # Delegated dropped on mint failure; static still resolves — the build survives.
    assert set(servers) == {"docs"}
