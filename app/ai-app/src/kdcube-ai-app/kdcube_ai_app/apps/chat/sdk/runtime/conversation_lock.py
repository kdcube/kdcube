# SPDX-License-Identifier: MIT

"""The per-conversation lock the processor holds while a turn runs.

The processor takes ``conversation_lock_key`` (``SET NX`` with a TTL, renewed
while the turn runs) before it starts a turn, and puts the task back in the
queue when the lock is held. Other work that must not overlap a turn, such as
deleting the conversation, takes the same lock with
``hold_conversation_lock``: it fails at once with ``ConversationTurnRunning``
while a turn runs, and while it holds the lock no turn of that conversation
starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any, AsyncIterator

from kdcube_ai_app.apps.chat.sdk.event_identity import safe_event_lane_part
from kdcube_ai_app.infra.namespaces import REDIS, ns_key

CONVERSATION_LOCK_TTL_SECONDS = 300
CONVERSATION_LOCK_RENEW_SECONDS = 60

_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class ConversationTurnRunning(RuntimeError):
    """A turn of the conversation is running; the conversation cannot be changed now."""

    code = "conversation_turn_running"

    def __init__(self, message: str = "A turn of this conversation is still running.") -> None:
        super().__init__(message)


def conversation_lock_key(*, tenant: str, project: str, user_id: str, conversation_id: str) -> str:
    """The processor's per-conversation lock (``processor._task_conversation_lock_key``)."""
    parts = (safe_event_lane_part(tenant, default="_"), safe_event_lane_part(project, default="_"),
             safe_event_lane_part(user_id, default="_"), safe_event_lane_part(conversation_id, default="_"))
    prefix = ns_key(REDIS.SYNCHRONIZATION.LOCK, tenant=tenant, project=project)
    return f"{prefix}:conversation:{':'.join(parts)}"


@contextlib.asynccontextmanager
async def hold_conversation_lock(
    redis: Any,
    key: str,
    *,
    owner: str,
    ttl_seconds: int = CONVERSATION_LOCK_TTL_SECONDS,
    renew_seconds: float = CONVERSATION_LOCK_RENEW_SECONDS,
) -> AsyncIterator[str]:
    """Hold the conversation lock for the block, or raise ``ConversationTurnRunning`` at once.

    The lock is renewed while the block runs and released only by its holder,
    so a lock that expired and was taken by a turn is never released here.
    """
    token = f"{owner}:{uuid.uuid4().hex}"
    if not await redis.set(key, token, nx=True, ex=int(ttl_seconds)):
        raise ConversationTurnRunning()
    stop = asyncio.Event()

    async def renew() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=renew_seconds)
            except asyncio.TimeoutError:
                await redis.eval(_RENEW, 1, key, token, str(int(ttl_seconds)))

    renewer = asyncio.create_task(renew())
    try:
        yield token
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await renewer
        await redis.eval(_RELEASE, 1, key, token)


__all__ = ["CONVERSATION_LOCK_TTL_SECONDS", "ConversationTurnRunning", "conversation_lock_key",
           "hold_conversation_lock"]
