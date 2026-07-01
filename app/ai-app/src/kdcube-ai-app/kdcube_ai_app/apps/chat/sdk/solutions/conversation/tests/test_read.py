# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Tests for the SDK-owned conversation read/export facade.

The provider depends on `ConversationReadService` + `ConversationReadScope`, not
on control-plane internals. These drive the facade with a fake materialization
port — no `_build_ctx`, no database — and assert scope resolution, summary/record
shaping (reusing the SDK-owned `collapse_turn`/`normalize_conversation`), and that
selected-user scope routes reads to the selected user.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import (
    SCOPE_SELF,
    SCOPE_USER,
    ConversationExportScope,
    ConversationGetRequest,
    ConversationListRequest,
    ConversationReadScope,
    ConversationReadService,
    ConversationScopeError,
)


class FakePort:
    """Records the user_id it was asked for; returns canned materialization."""

    def __init__(self, convs: List[Dict[str, Any]], artifacts: Dict[str, Dict[str, Any]]):
        self._convs = convs
        self._artifacts = artifacts
        self.list_calls: List[str] = []
        self.fetch_calls: List[tuple] = []

    async def list_conversations(self, *, user_id, started_after=None, days=3650, last_n=None, include_titles=True):
        self.list_calls.append(user_id)
        return list(self._convs)

    async def fetch_conversation_artifacts(self, *, user_id, conversation_id, turn_ids=None, materialize=True, days=3650):
        self.fetch_calls.append((user_id, conversation_id))
        return self._artifacts.get(conversation_id, {})


def _turn(turn_id: str, user: str, assistant: str) -> Dict[str, Any]:
    return {
        "turn_id": turn_id,
        "artifacts": [
            {"type": "chat:user", "data": {"payload": {"text": user}}},
            {"type": "chat:assistant", "data": {"payload": {"text": assistant}}},
        ],
    }


def _service(convs=None, artifacts=None, *, tenant="t1", project="p1"):
    port = FakePort(convs or [], artifacts or {})
    return ConversationReadService(port, tenant=tenant, project=project), port


# --- scope resolution -------------------------------------------------------

def test_self_scope_requires_current_user():
    with pytest.raises(ConversationScopeError):
        ConversationReadScope(mode=SCOPE_SELF).resolve()


def test_user_scope_requires_user_id():
    with pytest.raises(ConversationScopeError):
        ConversationReadScope(mode=SCOPE_USER).resolve()


def test_scope_resolution():
    assert ConversationReadScope(mode=SCOPE_SELF, current_user_id="u-self").resolve() == "u-self"
    assert ConversationReadScope(mode=SCOPE_USER, user_id="u-target").resolve() == "u-target"
    # Unknown mode degrades to self.
    assert ConversationReadScope(mode="bogus", current_user_id="u-self").resolve() == "u-self"


# --- list -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_returns_cleaned_summaries():
    convs = [{"conversation_id": "c1", "title": "First", "started_at": "2026-01-01T00:00:00Z", "turn_count": 3}]
    service, port = _service(convs=convs)
    scope = ConversationReadScope(mode=SCOPE_SELF, current_user_id="u1")
    out = await service.list_user_conversations(ConversationListRequest(scope=scope))
    assert port.list_calls == ["u1"]
    assert out == [{
        "conversation_id": "c1", "user_id": "u1", "tenant": "t1", "project": "p1",
        "title": "First", "started_at": "2026-01-01T00:00:00Z", "turn_count": 3,
    }]


# --- get --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_conversation_normalizes_record():
    artifacts = {"c1": {"title": "First", "turns": [_turn("t1", "hi", "hello")]}}
    service, port = _service(artifacts=artifacts)
    scope = ConversationReadScope(mode=SCOPE_SELF, current_user_id="u1")
    rec = await service.get_conversation(ConversationGetRequest(scope=scope, conversation_id="c1"))
    assert port.fetch_calls == [("u1", "c1")]
    assert rec["conversation_id"] == "c1"
    assert rec["tenant"] == "t1" and rec["project"] == "p1" and rec["user_id"] == "u1"
    assert rec["turns"] == [{
        "turn_id": "t1", "ts": None, "user": "hi", "assistant": "hello",
        "attachments": [], "citations": [],
    }]


@pytest.mark.asyncio
async def test_get_conversation_missing_returns_none():
    service, _ = _service(artifacts={})
    scope = ConversationReadScope(mode=SCOPE_SELF, current_user_id="u1")
    assert await service.get_conversation(ConversationGetRequest(scope=scope, conversation_id="nope")) is None


# --- export -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_limits_and_counts():
    convs = [{"conversation_id": f"c{i}", "title": f"T{i}"} for i in range(4)]
    artifacts = {f"c{i}": {"turns": [_turn(f"t{i}", "u", "a")]} for i in range(4)}
    service, port = _service(convs=convs, artifacts=artifacts)
    scope = ConversationReadScope(mode=SCOPE_SELF, current_user_id="u1")
    result = await service.export_conversations(ConversationExportScope(scope=scope, limit=2))
    assert result["ok"] is True
    assert result["total_available"] == 4
    assert result["count"] == 2
    assert result["limited"] is True
    assert [c["conversation_id"] for c in result["conversations"]] == ["c0", "c1"]
    # Every conversation was materialized for the requesting user.
    assert port.list_calls == ["u1"]
    assert {c for (c, _) in port.fetch_calls} == {"u1"}


@pytest.mark.asyncio
async def test_selected_user_scope_routes_to_selected_user():
    convs = [{"conversation_id": "c1", "title": "First"}]
    artifacts = {"c1": {"turns": [_turn("t1", "hi", "hello")]}}
    service, port = _service(convs=convs, artifacts=artifacts)
    # An admin acting on behalf of "victim" (boundary must have granted :any_user).
    scope = ConversationReadScope(mode=SCOPE_USER, current_user_id="admin-1", user_id="other-user")
    await service.export_conversations(ConversationExportScope(scope=scope))
    assert port.list_calls == ["other-user"]
    assert port.fetch_calls == [("other-user", "c1")]


@pytest.mark.asyncio
async def test_pooled_port_unwraps_list_conversations_items():
    # Regression: ContextRAGClient.list_conversations returns a dict
    # {"user_id", "items": [...]}, NOT a bare list. The port must unwrap `items`;
    # `list(dict)` would yield the keys ("user_id","items") and silently drop
    # every conversation (the "object.list returns empty" bug).
    from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import _PooledMaterializationPort

    class _FakeCtx:
        async def list_conversations(self, **kwargs):
            return {"user_id": "u", "items": [{"conversation_id": "c1"}, {"conversation_id": "c2"}]}

    port = _PooledMaterializationPort(pg_pool=None, tenant="t", project="p", model_service=None, store=None)
    port._ctx = _FakeCtx()  # bypass real ctx-client construction
    convs = await port.list_conversations(user_id="u")
    assert [c["conversation_id"] for c in convs] == ["c1", "c2"]
