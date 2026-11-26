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
import logging
import uuid
from typing import Any, AsyncIterator

from kdcube_ai_app.apps.chat.sdk.event_identity import safe_event_lane_part
from kdcube_ai_app.infra.namespaces import REDIS, ns_key

logger = logging.getLogger(__name__)

CONVERSATION_LOCK_TTL_SECONDS = 300
# Owners of the lock other than a turn: they hold it while deleting.
DELETION_LOCK_OWNERS = ("conversation-delete", "conversation-deletion-resume")
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


class ConversationLockHeld(RuntimeError):
    """The conversation's lock is held by someone else."""

    code = "conversation_busy"


class ConversationTurnRunning(ConversationLockHeld):
    """A turn of the conversation is running; the conversation cannot be changed now."""

    code = "conversation_turn_running"

    def __init__(self, message: str = "A turn of this conversation is still running.") -> None:
        super().__init__(message)


class ConversationDeleteInProgress(ConversationLockHeld):
    """The conversation is already being deleted."""

    code = "conversation_delete_in_progress"

    def __init__(self, message: str = "This conversation is already being deleted.") -> None:
        super().__init__(message)


async def _lock_held_error(redis: Any, key: str) -> ConversationLockHeld:
    try:
        holder = await redis.get(key)
    except Exception:
        holder = None
    if isinstance(holder, bytes):
        holder = holder.decode("utf-8", "replace")
    if isinstance(holder, str) and holder.split(":", 1)[0] in DELETION_LOCK_OWNERS:
        return ConversationDeleteInProgress()
    return ConversationTurnRunning()


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
    """Hold the conversation lock for the block, or raise ``ConversationLockHeld`` at once.

    ``ConversationTurnRunning`` when a turn holds it, ``ConversationDeleteInProgress``
    when a deletion does. The lock is renewed while the block runs and
    released only by its holder, so a lock that expired and was taken by a
    turn is never released here.
    """
    token = f"{owner}:{uuid.uuid4().hex}"
    if not await redis.set(key, token, nx=True, ex=int(ttl_seconds)):
        raise await _lock_held_error(redis, key)
    stop = asyncio.Event()

    async def renew() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=renew_seconds)
            except asyncio.TimeoutError:
                # A failed renewal is retried at the next interval; one error
                # must not leave the lock to expire under the holder.
                try:
                    if not await redis.eval(_RENEW, 1, key, token, str(int(ttl_seconds))):
                        logger.warning("[conversation-lock] %s lost the lock %s", owner, key)
                except Exception:
                    logger.warning("[conversation-lock] renewing %s failed; retrying", key, exc_info=True)

    renewer = asyncio.create_task(renew())
    try:
        yield token
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await renewer
        await redis.eval(_RELEASE, 1, key, token)


__all__ = ["CONVERSATION_LOCK_TTL_SECONDS", "ConversationDeleteInProgress", "ConversationLockHeld",
           "ConversationTurnRunning", "conversation_lock_key", "hold_conversation_lock"]
