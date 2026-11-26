"""A queued message must not bring a deleted conversation back."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor


class _Index:
    def __init__(self, answer=None, fails=False):
        self.answer, self.fails, self.asked = answer, fails, []

    async def conversation_deletion_since(self, *, user_id, conversation_id, since):
        self.asked.append((user_id, conversation_id, since))
        if self.fails:
            raise RuntimeError("pg down")
        return self.answer


class _Comm:
    def __init__(self):
        self.errors = []

    async def error(self, *, message, data):
        self.errors.append(data)


def _processor(index):
    comm = _Comm()
    events = []

    async def ack(task_data):
        events.append("ack")

    async def release_lock(key, token=None):
        events.append(("release", key))
        return True

    async def release_unstarted(task_data, *, reason):
        events.append(("requeue", reason))
        return True

    proc = SimpleNamespace(
        conversation_ctx=SimpleNamespace(idx=index), _current_load=1, _ack_claimed_task=ack,
        _release_redis_lock=release_lock, _release_unstarted_claimed_work=release_unstarted,
        _build_runtime_context=lambda payload: ("req", None, None, comm),
        _task_logical_id=lambda task_data: "task-1",
    )
    return proc, comm, events


def _payload(created_at=1764150000.0, operation="chat"):
    return SimpleNamespace(request=SimpleNamespace(operation=operation),
                           meta=SimpleNamespace(created_at=created_at, task_id="task-1"),
                           user=SimpleNamespace(user_id="u1"), routing=SimpleNamespace(conversation_id="c1"))


async def _drop(proc, payload):
    return await EnhancedChatRequestProcessor._drop_task_of_deleted_conversation(
        proc, {}, payload, conversation_lock_key="lock-c1", conversation_lock_token="tok")


@pytest.mark.asyncio
async def test_a_message_to_a_deleted_conversation_is_dropped_and_reported():
    proc, comm, events = _processor(_Index({"deletion_id": "del_1", "state": "completed"}))
    assert await _drop(proc, _payload()) is True
    assert events == ["ack", ("release", "lock-c1")] and proc._current_load == 0
    assert comm.errors == [{"task_id": "task-1", "error_type": "conversation_deleted", "deletion_id": "del_1"}]
    assert proc.conversation_ctx.idx.asked == [("u1", "c1", datetime.fromtimestamp(1764150000.0, tz=timezone.utc))]


@pytest.mark.asyncio
async def test_a_message_to_a_live_conversation_runs():
    proc, comm, events = _processor(_Index(None))
    assert await _drop(proc, _payload()) is False
    assert events == [] and comm.errors == [] and proc._current_load == 1


@pytest.mark.asyncio
async def test_an_unreadable_ledger_puts_the_task_back_in_the_queue():
    proc, comm, events = _processor(_Index(fails=True))
    assert await _drop(proc, _payload()) is True
    assert events == [("requeue", "conversation-deletion-unreadable")] and comm.errors == []


@pytest.mark.asyncio
async def test_background_jobs_are_not_checked():
    from kdcube_ai_app.apps.chat.processor import BACKGROUND_JOB_OPERATION

    index = _Index({"deletion_id": "del_1", "state": "started"})
    proc, comm, events = _processor(index)
    assert await _drop(proc, _payload(operation=BACKGROUND_JOB_OPERATION)) is False
    assert index.asked == []
