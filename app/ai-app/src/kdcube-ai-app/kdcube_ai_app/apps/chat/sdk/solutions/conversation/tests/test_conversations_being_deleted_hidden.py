"""A conversation with an open deletion is hidden from the conversation list."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import ContextRAGClient


class _Index:
    def __init__(self, deleting):
        self.deleting = set(deleting)
        self.asked = []

    async def fetch_recent(self, **kwargs):
        return [{"conversation_id": cid, "ts": f"2026-11-2{n}T10:00:00Z",
                 "text": json.dumps({"conversation_title": cid})} for n, cid in enumerate(("c1", "c2", "c3"))]

    async def conversations_being_deleted(self, *, user_id, conversation_ids):
        self.asked.append((user_id, sorted(conversation_ids)))
        return self.deleting & set(conversation_ids)


@pytest.mark.asyncio
async def test_the_list_leaves_out_conversations_being_deleted():
    index = _Index({"c2"})
    client = SimpleNamespace(idx=index)
    client._conversations_being_deleted = lambda user_id, ids: ContextRAGClient._conversations_being_deleted(
        client, user_id, ids)
    listed = await ContextRAGClient.list_conversations(client, "u1")
    assert [i["conversation_id"] for i in listed["items"]] == ["c3", "c1"]
    assert index.asked == [("u1", ["c1", "c2", "c3"])]


@pytest.mark.asyncio
async def test_an_index_without_the_ledger_check_lists_everything():
    class _Plain(_Index):
        conversations_being_deleted = None

    client = SimpleNamespace(idx=_Plain(()))
    client._conversations_being_deleted = lambda user_id, ids: ContextRAGClient._conversations_being_deleted(
        client, user_id, ids)
    listed = await ContextRAGClient.list_conversations(client, "u1")
    assert len(listed["items"]) == 3
