"""The conversation lock held by work that must not overlap a turn."""

from __future__ import annotations

import asyncio

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.conversation_lock import (
    ConversationDeleteInProgress,
    ConversationTurnRunning,
    hold_conversation_lock,
)


class _Redis:
    def __init__(self, fail_renewals=0):
        self.values, self.renewals, self.fail_renewals = {}, 0, fail_renewals

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def get(self, key):
        value = self.values.get(key)
        return value.encode() if isinstance(value, str) else value

    async def eval(self, script, numkeys, key, token, *args):
        if "EXPIRE" in script:
            self.renewals += 1
            if self.renewals <= self.fail_renewals:
                raise ConnectionError("redis blip")
        if self.values.get(key) != token:
            return 0
        if "DEL" in script:
            del self.values[key]
        return 1


@pytest.mark.asyncio
async def test_a_failed_renewal_is_retried_and_the_lock_is_still_released_by_its_holder():
    redis = _Redis(fail_renewals=1)
    async with hold_conversation_lock(redis, "k", owner="conversation-delete", renew_seconds=0.01):
        await asyncio.sleep(0.06)
    assert redis.renewals >= 2 and "k" not in redis.values


@pytest.mark.asyncio
@pytest.mark.parametrize("holder,error", [
    ("worker-1:proc-1:task-1", ConversationTurnRunning),
    ("conversation-delete:abc", ConversationDeleteInProgress),
    ("conversation-deletion-resume:abc", ConversationDeleteInProgress),
])
async def test_who_holds_the_lock_decides_the_refusal(holder, error):
    redis = _Redis()
    redis.values["k"] = holder
    with pytest.raises(error):
        async with hold_conversation_lock(redis, "k", owner="conversation-delete"):
            pass
    assert redis.values["k"] == holder



@pytest.mark.asyncio
async def test_a_failed_release_does_not_fail_the_work_done_under_the_lock():
    class _FailingRelease(_Redis):
        async def eval(self, script, numkeys, key, token, *args):
            if "DEL" in script:
                raise ConnectionError("redis went away")
            return await super().eval(script, numkeys, key, token, *args)

    redis = _FailingRelease()
    async with hold_conversation_lock(redis, "k", owner="conversation-delete"):
        done = True
    assert done
