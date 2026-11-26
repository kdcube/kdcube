"""A queued message must not bring a deleted conversation back."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor


class _Index:
    def __init__(self, answer=None, fails=False, state_turn=None):
        self.answer, self.fails, self.asked, self.state_turn = answer, fails, [], state_turn

    async def get_conversation_state_row(self, *, user_id, conversation_id):
        if self.state_turn is None:
            return None
        return {"tags": ["conv.state:in_progress", f"conv.turn:{self.state_turn}"]}

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

    deletes = []

    async def delete_conversation(**kwargs):
        deletes.append(kwargs)
        return {"deletion_id": "del_again"}

    proc = SimpleNamespace(
        conversation_ctx=SimpleNamespace(idx=index, delete_conversation=delete_conversation, deletes=deletes),
        _current_load=1, _ack_claimed_task=ack,
        _release_redis_lock=release_lock, _release_unstarted_claimed_work=release_unstarted,
        _build_runtime_context=lambda payload: ("req", None, None, comm),
        _task_logical_id=lambda task_data: "task-1",
    )
    proc._remove_state_left_by_dropped_task = (
        lambda payload: EnhancedChatRequestProcessor._remove_state_left_by_dropped_task(proc, payload))
    return proc, comm, events


def _payload(created_at=1764150000.0, operation="chat"):
    return SimpleNamespace(request=SimpleNamespace(operation=operation),
                           meta=SimpleNamespace(created_at=created_at, task_id="task-1"),
                           user=SimpleNamespace(user_id="u1", user_type="registered"),
                           actor=SimpleNamespace(tenant_id="t", project_id="p"),
                           routing=SimpleNamespace(conversation_id="c1", turn_id="turn-9", bundle_id="app@1-0"))


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



@pytest.mark.asyncio
async def test_state_a_dropped_message_wrote_after_the_deletion_is_removed():
    """Accepted while the deletion ran, the message recreated the conversation's state; it goes again."""
    proc, comm, events = _processor(_Index({"deletion_id": "del_1", "state": "completed"}, state_turn="turn-9"))
    assert await _drop(proc, _payload()) is True
    [deleted] = proc.conversation_ctx.deletes
    assert (deleted["conversation_id"], deleted["bundle_id"], deleted["lock_wait_s"]) == ("c1", "app@1-0", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("state_turn", [None, "turn-of-a-newer-conversation"])
async def test_a_newer_conversation_under_the_id_is_never_removed(state_turn):
    proc, comm, events = _processor(_Index({"deletion_id": "del_1", "state": "completed"}, state_turn=state_turn))
    assert await _drop(proc, _payload()) is True
    assert proc.conversation_ctx.deletes == []
