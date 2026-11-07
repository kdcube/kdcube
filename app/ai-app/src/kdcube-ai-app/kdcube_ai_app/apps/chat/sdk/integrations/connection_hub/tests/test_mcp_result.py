# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The shared MCP-result post-processor: consent banners + file cards for the
chat surface, driven ENTIRELY by the surface's self-describing result.

This is the ONE place the behavior lives (applied by the SDK loader), so no
bundle re-implements it. The tests pin: both consent kinds announced from their
self-describing blocks (no namespace/tool_id parsing), file delivery through the
real adapter tuple shape, the turn-less passthrough, and the loud sentinel when
a consent-carrying result slips through unhandled."""

from __future__ import annotations

import asyncio
import json

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub import mcp_result


def _tool(result):
    class _T:
        name = "named_services_search"

        async def _run(self):
            return result

    t = _T()
    t.coroutine = t._run
    return t


def test_agent_grant_consent_announced_from_self_describing_block(monkeypatch):
    # The block carries agent_client_id + resource + claims + namespace +
    # operation — the post-processor reads them directly; NO fallback resource,
    # NO tool-name parsing.
    client = "kdcube-agent:app@v1:main"
    resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    denial = json.dumps({
        "ok": False, "error": "delegated_consent_required",
        "namespace": "slack", "operation": "object.search",
        "missing_grants": ["slack:read"],
        "consent": {
            "kind": "delegated_agent_grant", "agent_client_id": client,
            "resource": resource, "claims": ["slack:read"],
            "tool_name": "slack", "namespace": "slack",
            "operation": "object.search",
        },
    })
    announced = []

    async def fake_announce(consent):
        announced.append(consent)

    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub import mcp_consent as consent_mod
    monkeypatch.setattr(consent_mod, "announce_agent_consent", fake_announce)

    tool = _tool((denial, {"structured_content": None}))
    mcp_result.bind_chat_result_handling([tool])
    content, artifact = asyncio.run(tool.coroutine())

    assert artifact == {"structured_content": None}  # preserved
    assert len(announced) == 1 and announced[0].claims == ["slack:read"]
    out = json.loads(content)
    assert out["consent"]["agent_client_id"] == client
    assert out["consent"]["resource"] == resource
    assert out["consent"]["claims"] == ["slack:read"]
    assert out["consent"]["agent_card_consent"] is True
    assert "grant" not in out["consent"]
    assert out["consent"]["namespace"] == "slack"
    assert out["consent"]["operation"] == "object.search"


def test_agent_grant_consent_announced_from_normalized_mcp_content_envelope(monkeypatch, caplog):
    # Live regression (2026-08-17): normalize_mcp_tool_result returns a dict
    # envelope with content blocks. The old post-processor looked only at the
    # top-level dict, logged NOT post-processed, and raised no chat banner.
    import logging

    client = "kdcube-agent:app@v1:main"
    resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    denial = json.dumps({
        "ok": False, "error": "delegated_consent_required",
        "namespace": "slack", "missing_grants": ["slack:channels"],
        "consent": {
            "kind": "delegated_agent_grant", "agent_client_id": client,
            "resource": resource, "claims": ["slack:channels"],
            "tool_name": "slack", "namespace": "slack",
        },
    })
    announced = []

    async def fake_announce(consent):
        announced.append(consent)

    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub import mcp_consent as consent_mod
    monkeypatch.setattr(consent_mod, "announce_agent_consent", fake_announce)

    tool = _tool({"content": [{"type": "text", "text": denial}], "isError": False})
    mcp_result.bind_chat_result_handling([tool])
    with caplog.at_level(logging.WARNING):
        result = asyncio.run(tool.coroutine())

    assert len(announced) == 1 and announced[0].claims == ["slack:channels"]
    out = json.loads(result["content"][0]["text"])
    assert out["consent"]["agent_client_id"] == client
    assert out["consent"]["resource"] == resource
    assert out["consent"]["claims"] == ["slack:channels"]
    assert out["consent"]["agent_card_consent"] is True
    assert "grant" not in out["consent"]
    assert not any("NOT post-processed" in r.message for r in caplog.records)


def test_connected_account_consent_reads_namespace_from_block(monkeypatch):
    # REGRESSION: the namespace comes from the block's `namespace` field — NOT
    # parsed from a tool_id. An empty namespace used to make the announce bail.
    hub_url = "https://h/…/connections_settings?tab=delegated_to_kdcube&provider_id=google"
    door = {
        "ok": False,
        "error": {
            "code": "needs_connected_account_consent",
            "message": "Connect Google.",
            "details": {
                "reason": "connect_required", "provider_id": "google",
                "connector_app_id": "gmail", "claims": ["gmail:read"],
                "connection_hub_url": hub_url,
                "consent": {
                    "kind": "delegated_to_kdcube.connected_account",
                    "reason": "connect_required", "provider_id": "google",
                    "connector_app_id": "gmail", "claims": ["gmail:read"],
                    "url": hub_url, "action_label": "Connect account",
                    "namespace": "mail",  # self-describing — no tool_id parsing
                },
            },
        },
    }
    announced = []

    async def fake_raise(payload, *, namespace, tool_name):
        announced.append({"namespace": namespace, "tool_name": tool_name})

    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import consent as consent_mod
    monkeypatch.setattr(consent_mod, "raise_named_service_consent_demand", fake_raise)

    tool = _tool((json.dumps(door), None))
    mcp_result.bind_chat_result_handling([tool])
    content, _ = asyncio.run(tool.coroutine())

    assert announced == [{"namespace": "mail", "tool_name": "mail"}]
    # the original result (with the link) flows unchanged to the model / an
    # external client.
    assert json.loads(content)["error"]["code"] == "needs_connected_account_consent"
    assert hub_url in content


def test_file_delivery_from_tuple_result(monkeypatch):
    raw = json.dumps({
        "ok": True,
        "object": {"ref": "slack:a:file:F9", "object_ref": "slack:a:file:F9",
                   "name": "pic.png", "mimetype": "image/png", "size": 10,
                   "download": {"encoding": "url", "url": "http://h/dl?download_token=S"}},
    })
    events = []

    class _Comm:
        async def event(self, **kwargs):
            events.append(kwargs)

    from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx
    monkeypatch.setattr(comm_ctx, "get_comm", lambda: _Comm())

    tool = _tool(([{"type": "text", "text": raw}], "artifact-slot"))
    mcp_result.bind_chat_result_handling([tool])
    content, artifact = asyncio.run(tool.coroutine())

    assert artifact == "artifact-slot"
    assert len(events) == 1 and events[0]["type"] == "chat.files"
    assert events[0]["data"]["items"][0]["object_ref"] == "slack:a:file:F9"
    assert "download_token" not in content[0]["text"]


def test_turn_less_keeps_url(monkeypatch):
    raw = json.dumps({
        "ok": True,
        "object": {"ref": "slack:a:file:F1", "name": "f.bin",
                   "download": {"encoding": "url", "url": "http://h/dl?t=S"}},
    })
    from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx
    monkeypatch.setattr(comm_ctx, "get_comm", lambda: None)

    tool = _tool((raw, None))
    mcp_result.bind_chat_result_handling([tool])
    content, _ = asyncio.run(tool.coroutine())
    assert json.loads(content)["object"]["download"]["encoding"] == "url"


def test_sentinel_warns_when_consent_slips_through(monkeypatch, caplog):
    # A consent-carrying result in an UNHANDLED shape (not str/dict/list) must
    # log loudly, never silently reach the model.
    import logging
    tool = _tool(12345)  # int — no processing path
    # Inject a marker into repr by wrapping — use a shape the processor ignores.
    class _Weird:
        def __repr__(self):
            return '{"error": {"code": "needs_connected_account_consent"}}'
    tool2 = _tool(_Weird())
    mcp_result.bind_chat_result_handling([tool2])
    with caplog.at_level(logging.WARNING):
        asyncio.run(tool2.coroutine())
    assert any("NOT post-processed" in r.message for r in caplog.records)
