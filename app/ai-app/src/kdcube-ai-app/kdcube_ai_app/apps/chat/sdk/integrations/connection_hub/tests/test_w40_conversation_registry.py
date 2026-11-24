"""W40: the conversation registry the Hub reads, over KDCube's own records."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.conversation_registry import (
    KDCubeConversationRegistry, conversation_lock_key,
)
from kdcube_ai_app.infra.namespaces import REDIS, ns_key

TENANT, PROJECT, USER, CONV = "t-1", "p-1", "user-1", "conv-1"


def test_the_lock_key_is_the_processors_conversation_lock():
    processor = SimpleNamespace(middleware=SimpleNamespace(
        LOCK_PREFIX=ns_key(REDIS.SYNCHRONIZATION.LOCK, tenant=TENANT, project=PROJECT)))
    task = {"routing": {"conversation_id": CONV}, "actor": {"tenant_id": TENANT, "project_id": PROJECT},
            "user": {"user_id": USER}}
    assert EnhancedChatRequestProcessor._task_conversation_lock_key(processor, task) == conversation_lock_key(
        tenant=TENANT, project=PROJECT, user_id=USER, conversation_id=CONV)


class _Index:
    def __init__(self, row):
        self.row = row

    async def get_conversation_state_row(self, *, user_id, conversation_id):
        return self.row if (user_id, conversation_id) == (USER, CONV) else None


class _Redis:
    def __init__(self, keys=()):
        self.keys = set(keys)

    async def exists(self, key):
        return int(key in self.keys)


def _registry(row, keys=()):
    return KDCubeConversationRegistry(conv_index=_Index(row), redis=_Redis(keys), tenant=TENANT, project=PROJECT)


@pytest.mark.asyncio
async def test_owner_and_application_come_from_the_state_row():
    row = {"bundle_id": "app@1-0", "ts": datetime(2026, 11, 20, tzinfo=timezone.utc), "tags": []}
    found = await _registry(row).conversation(owner_subject=USER, conversation_id=CONV)
    assert (found.owner_subject, found.application) == (USER, "app@1-0")
    assert await _registry(row).conversation(owner_subject="user-2", conversation_id=CONV) is None


@pytest.mark.asyncio
async def test_a_turn_is_alive_only_in_progress_for_it_and_with_the_lock_held():
    lock = conversation_lock_key(tenant=TENANT, project=PROJECT, user_id=USER, conversation_id=CONV)
    running = {"bundle_id": "app@1-0", "tags": ["conv.state:in_progress", "conv.turn:t1"]}
    assert await _registry(running, [lock]).turn_alive(owner_subject=USER, conversation_id=CONV, turn_id="t1")
    # The processor died: its lock lapsed although the row still says in_progress.
    assert not await _registry(running).turn_alive(owner_subject=USER, conversation_id=CONV, turn_id="t1")
    assert not await _registry(running, [lock]).turn_alive(owner_subject=USER, conversation_id=CONV, turn_id="t2")
    idle = {"bundle_id": "app@1-0", "tags": ["conv.state:idle", "conv.turn:t1"]}
    assert not await _registry(idle, [lock]).turn_alive(owner_subject=USER, conversation_id=CONV, turn_id="t1")
