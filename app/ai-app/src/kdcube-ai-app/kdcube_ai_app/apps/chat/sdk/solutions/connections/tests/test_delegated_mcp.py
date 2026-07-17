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


def test_consent_gate_blocks_the_mint_when_pending():
    minter, calls = _fake_minter_calls()

    async def deny(scopes):
        return False  # consent pending

    servers = asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub="u1", minter=minter, consent_gate=deny))
    assert servers == {}            # not bound — consent pending
    assert calls == []              # never minted without consent


def test_consent_gate_allows_the_mint_when_given():
    minter, calls = _fake_minter_calls()

    seen = {}
    async def allow(scopes):
        seen["scopes"] = list(scopes)
        return True

    servers = asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub="u1", minter=minter, consent_gate=allow))
    assert set(servers) == {"memory"}                       # bound
    assert seen["scopes"] == ["memories:read"]              # gate saw the connection's claims
    assert calls and calls[0]["scopes"] == ["memories:read"]


def test_delegated_client_id_is_per_agent():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_mcp import (
        delegated_client_id_for_agent,
    )
    cid = delegated_client_id_for_agent("ported-langgraph-agents@2026-07-13", "lg-react")
    assert cid == "kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react"
    # distinct per agent (per-agent consent) and per application
    assert delegated_client_id_for_agent("app", "a") != delegated_client_id_for_agent("app", "b")
    assert delegated_client_id_for_agent("", "") == "kdcube-agent"  # fallback


def test_client_id_is_passed_to_the_minter():
    minter, calls = _fake_minter_calls()
    asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub="u1", minter=minter, client_id="kdcube-agent:app:lg-react"))
    assert calls and calls[0]["client_id"] == "kdcube-agent:app:lg-react"


def test_bearer_provider_injects_the_consented_token_and_replaces_the_mint():
    minter, calls = _fake_minter_calls()
    seen = {}

    async def provider(conn, user_sub):
        seen["conn_url"] = conn.get("url")
        seen["user"] = user_sub
        return "consented-tok"

    servers = asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub="u1", minter=minter, bearer_provider=provider))
    assert servers["memory"]["headers"]["Authorization"] == "Bearer consented-tok"
    assert seen == {"conn_url": "https://h/api/mcp/mem", "user": "u1"}
    assert calls == []  # provider REPLACES the mint — never minted


def test_bearer_provider_none_drops_the_connection_consent_pending():
    minter, calls = _fake_minter_calls()

    async def provider(conn, user_sub):
        return None  # consent pending for this agent

    servers = asyncio.run(resolve_mcp_server_map(
        [_DELEGATED, _STATIC], user_sub="u1", minter=minter, bearer_provider=provider))
    # Delegated dropped (consent pending); static still resolves.
    assert set(servers) == {"docs"}
    assert calls == []


def test_drop_sink_records_why_each_connection_was_omitted():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_mcp import (
        DROP_CONSENT_PENDING, DROP_NO_USER, DROP_PROVIDER_ERROR,
    )
    minter, _ = _fake_minter_calls()

    async def pending(conn, user_sub):
        return None

    # Consent pending: the drop happens BEFORE any server contact, so the sink —
    # not a transport error — is how the caller learns to raise the demand.
    sink: dict = {}
    asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub="u1", minter=minter, bearer_provider=pending, drop_sink=sink))
    assert sink == {"memory": DROP_CONSENT_PENDING}

    # No user bound: dropped, but NOT consent-pending (nothing to grant).
    sink = {}
    asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub=None, minter=minter, drop_sink=sink))
    assert sink == {"memory": DROP_NO_USER}

    # Provider error: operational, not consentable.
    async def broken(conn, user_sub):
        raise RuntimeError("hub unreachable")

    sink = {}
    asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub="u1", minter=minter, bearer_provider=broken, drop_sink=sink))
    assert sink == {"memory": DROP_PROVIDER_ERROR}

    # A bound connection leaves no drop record.
    async def granted(conn, user_sub):
        return "tok"

    sink = {}
    servers = asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub="u1", minter=minter, bearer_provider=granted, drop_sink=sink))
    assert set(servers) == {"memory"} and sink == {}


def test_agent_bearer_provider_over_automation_access_service():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_mcp import (
        agent_bearer_provider, resolve_mcp_server_map,
    )

    class _FakeService:
        def __init__(self):
            self.calls = []

        async def agent_access_token(self, *, grantor_subject, client_id, resources):
            self.calls.append({"sub": grantor_subject, "client_id": client_id, "resources": list(resources)})
            # Consent given only for the exact resource the connection points at.
            if resources == ["https://h/api/mcp/mem"] and client_id == "kdcube-agent:app:lg-react":
                return {"access_token": "granted-tok", "resource_grants": {}}
            return None

    svc = _FakeService()
    provider = agent_bearer_provider(svc, client_id="kdcube-agent:app:lg-react")

    servers = asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub="u9", bearer_provider=provider))
    assert servers["memory"]["headers"]["Authorization"] == "Bearer granted-tok"
    assert svc.calls == [{"sub": "u9", "client_id": "kdcube-agent:app:lg-react", "resources": ["https://h/api/mcp/mem"]}]

    # A different agent has no grant -> connection dropped (consent pending).
    provider_other = agent_bearer_provider(svc, client_id="kdcube-agent:app:other")
    assert asyncio.run(resolve_mcp_server_map(
        [_DELEGATED], user_sub="u9", bearer_provider=provider_other)) == {}


def test_claim_requirements_from_connection():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.consent_state import (
        claim_requirements_from_connection, SOURCE_DELEGATED, SOURCE_CONNECTED,
    )
    reqs = claim_requirements_from_connection(_DELEGATED)
    assert len(reqs) == 1 and reqs[0].claim == "memories:read" and reqs[0].source == SOURCE_DELEGATED

    conn_slack = {"kind": "python", "connected_accounts": [
        {"provider_id": "slack", "connector_app_id": "app1", "claims": ["slack:read", "slack:write"]}]}
    reqs2 = claim_requirements_from_connection(conn_slack)
    assert [r.claim for r in reqs2] == ["slack:read", "slack:write"]
    assert all(r.source == SOURCE_CONNECTED and r.provider_id == "slack" for r in reqs2)
