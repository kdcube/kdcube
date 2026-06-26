# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for the conversations_export tool logic: source attribution, the Phase-0
normalizer, and the cross-tenant/project sweep with the `since` watermark.
The data layer is faked; the real adapter wraps conversations_browser.
"""
from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.ingress.oauth_mcp.export_tool import (
    export_conversations,
    normalize_conversation,
    source_for_user,
)


def test_source_attribution():
    assert source_for_user("telegram:123") == "telegram"
    assert source_for_user("oauth:google:abc") == "web"
    assert source_for_user("") == "web"


def test_normalize_conversation_shape():
    raw = {
        "conversation_id": "c1",
        "user_id": "telegram:42",
        "started_at": "2026-06-22T10:30:00Z",
        "title": "Anchor alarm?",
        "turns": [
            {"turn_id": "t1", "ts": "2026-06-22T10:30:01Z", "user": "hi", "assistant": "hello",
             "attachments": [{"k": "v"}], "citations": ["s1"]},
        ],
    }
    rec = normalize_conversation(raw, tenant="home", project="demo")
    assert rec["conversation_id"] == "c1"
    assert rec["tenant"] == "home" and rec["project"] == "demo"
    assert rec["source"] == "telegram"
    assert rec["title"] == "Anchor alarm?"
    assert rec["turns"][0]["user"] == "hi"
    assert rec["turns"][0]["assistant"] == "hello"
    assert rec["turns"][0]["attachments"] == [{"k": "v"}]
    assert rec["turns"][0]["citations"] == ["s1"]


class _FakeDataSource:
    def __init__(self, data):
        # data: {(tenant, project): [raw_conversation, ...]}
        self._data = data

    async def list_tenant_projects(self):
        return list(self._data.keys())

    async def list_conversations(self, tenant, project, since):
        rows = self._data.get((tenant, project), [])
        if since:
            rows = [r for r in rows if r["started_at"] >= since]
        return rows


def _raw(cid, uid, started):
    return {"conversation_id": cid, "user_id": uid, "started_at": started, "title": cid, "turns": []}


@pytest.mark.asyncio
async def test_export_sweeps_all_tenant_projects_by_default():
    ds = _FakeDataSource({
        ("home", "demo"): [_raw("c1", "telegram:1", "2026-06-20T00:00:00Z")],
        ("acme", "prod"): [_raw("c2", "oauth:google:x", "2026-06-21T00:00:00Z")],
    })
    out = await export_conversations(ds)
    ids = {r["conversation_id"] for r in out}
    assert ids == {"c1", "c2"}
    # tenant/project stamped per record.
    by_id = {r["conversation_id"]: r for r in out}
    assert by_id["c1"]["tenant"] == "home" and by_id["c2"]["project"] == "prod"


@pytest.mark.asyncio
async def test_export_narrows_to_one_tenant_project():
    ds = _FakeDataSource({
        ("home", "demo"): [_raw("c1", "telegram:1", "2026-06-20T00:00:00Z")],
        ("acme", "prod"): [_raw("c2", "oauth:google:x", "2026-06-21T00:00:00Z")],
    })
    out = await export_conversations(ds, tenant="home", project="demo")
    assert {r["conversation_id"] for r in out} == {"c1"}


@pytest.mark.asyncio
async def test_export_honors_since_watermark():
    ds = _FakeDataSource({
        ("home", "demo"): [
            _raw("old", "telegram:1", "2026-06-01T00:00:00Z"),
            _raw("new", "telegram:1", "2026-06-24T00:00:00Z"),
        ],
    })
    out = await export_conversations(ds, since="2026-06-10T00:00:00Z")
    assert {r["conversation_id"] for r in out} == {"new"}
