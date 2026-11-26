"""Finishing conversation deletions that were recorded but did not complete."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import (
    ConversationDeleteBusy,
    ConversationDeleteIncomplete,
)
from kdcube_ai_app.apps.chat.sdk.runtime.conversation_lock import conversation_lock_key
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.deletion_resume import (
    RESUME_ACTOR,
    finish_unfinished_conversation_deletions,
)


class _Redis:
    def __init__(self, held=()):
        self.values = {key: "turn" for key in held}
        self.seen_held = []

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def eval(self, script, numkeys, key, token, *args):
        if self.values.get(key) == token and "DEL" in script:
            del self.values[key]
        return 1


class _Retention:
    def __init__(self, pending):
        self.pending = pending

    async def unfinished_conversation_deletions(self, *, limit):
        return self.pending[:limit]


def _client(pending, outcomes, redis):
    calls = []

    async def delete_conversation(**kwargs):
        key = conversation_lock_key(tenant="t", project="p", user_id=kwargs["user_id"],
                                    conversation_id=kwargs["conversation_id"])
        redis.seen_held.append(key in redis.values)
        calls.append(kwargs)
        outcome = outcomes.get(kwargs["conversation_id"])
        if outcome is not None:
            raise outcome
        return {"deletion_id": "del_new"}

    retention = _Retention(pending)
    client = SimpleNamespace(store=object(), delete_conversation=delete_conversation,
                             idx=SimpleNamespace(retention=lambda store: retention))
    return client, calls


def _item(conversation, attempts=1, bundle_ids=("app@1-0",)):
    return {"user_id": "u1", "conversation_id": conversation, "bundle_id": None,
            "bundle_ids": list(bundle_ids), "attempts": attempts}


@pytest.mark.asyncio
async def test_each_unfinished_deletion_runs_again_with_its_scope_under_the_conversation_lock():
    redis = _Redis()
    client, calls = _client([_item("c1"), _item("c2")], {}, redis)
    summary = await finish_unfinished_conversation_deletions(ctx_client=client, redis=redis, tenant="t", project="p")
    assert summary == {"found": 2, "finished": 2, "incomplete": 0, "turn_running": 0, "stopped": 0}
    assert [c["conversation_id"] for c in calls] == ["c1", "c2"]
    assert all(c["bundle_ids"] == ["app@1-0"] and c["actor"] == RESUME_ACTOR and c["lock_wait_s"] == 0
               for c in calls)
    assert redis.seen_held == [True, True] and redis.values == {}


@pytest.mark.asyncio
async def test_a_conversation_with_a_running_turn_is_left_for_the_next_run():
    held = conversation_lock_key(tenant="t", project="p", user_id="u1", conversation_id="c1")
    redis = _Redis(held=[held])
    client, calls = _client([_item("c1"), _item("c2")], {}, redis)
    summary = await finish_unfinished_conversation_deletions(ctx_client=client, redis=redis, tenant="t", project="p")
    assert summary["turn_running"] == 1 and summary["finished"] == 1
    assert [c["conversation_id"] for c in calls] == ["c2"]


@pytest.mark.asyncio
async def test_a_held_retention_lock_stops_the_run():
    redis = _Redis()
    client, calls = _client([_item("c1"), _item("c2")], {"c1": ConversationDeleteBusy("held")}, redis)
    summary = await finish_unfinished_conversation_deletions(ctx_client=client, redis=redis, tenant="t", project="p")
    assert summary["stopped"] == 1 and [c["conversation_id"] for c in calls] == ["c1"] and redis.values == {}


@pytest.mark.asyncio
async def test_a_deletion_that_keeps_failing_is_reported_as_a_warning(caplog):
    redis = _Redis()
    failing = ConversationDeleteIncomplete("del_9", "RuntimeError: storage unavailable")
    client, _ = _client([_item("c1", attempts=4), _item("c2", attempts=1)], {"c1": failing, "c2": failing}, redis)
    with caplog.at_level(logging.INFO):
        summary = await finish_unfinished_conversation_deletions(ctx_client=client, redis=redis, tenant="t",
                                                                 project="p", warn_after=5)
    assert summary["incomplete"] == 2
    levels = {r.getMessage().split("conversation=")[1].split()[0]: r.levelname for r in caplog.records
              if "did not finish" in r.getMessage()}
    assert levels == {"c1": "WARNING", "c2": "INFO"}


@pytest.mark.asyncio
async def test_without_the_cold_tier_nothing_runs():
    client = SimpleNamespace(store=object(), idx=SimpleNamespace(retention=lambda store: None))
    summary = await finish_unfinished_conversation_deletions(ctx_client=client, redis=_Redis(), tenant="t",
                                                             project="p")
    assert summary["stopped"] == 1 and summary["found"] == 0
