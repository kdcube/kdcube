# SPDX-License-Identifier: MIT

"""KDCube's conversation registry, as Connection Hub reads it.

The Hub asks two things, both answered from KDCube's own records:

- ``conversation``: who owns a conversation and in which application. The
  conversation-state row (``artifact:conversation.state`` in
  ``conv_messages``) is keyed by ``(user_id, conversation_id)`` and carries the
  ``bundle_id``; a conversation has no row until its first turn or its first
  picker write registers it.
- Both answer "no" while the conversation has an open deletion in the
  deletion ledger: it is being deleted.
- ``turn_alive``: the state row says ``in_progress`` for exactly this turn
  AND the processor still holds the conversation's Redis lock. The lock is
  renewed while the turn runs (``processor._lock_renewer``) and lapses within
  its TTL after the processor dies; the long-lived "started" marker is not a
  liveness signal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from kdcube_ai_app.apps.chat.sdk.runtime.conversation_lock import conversation_lock_key


def _tags(row: Any) -> list[str]:
    return [str(tag) for tag in (row or {}).get("tags") or ()]


class KDCubeConversationRegistry:
    """Read-only view over the conversation index and the processor's locks."""

    def __init__(self, *, conv_index: Any, redis: Any, tenant: str, project: str) -> None:
        self._index, self._redis = conv_index, redis
        self._tenant, self._project = tenant, project

    async def conversation(self, *, owner_subject: str, conversation_id: str) -> Optional[Any]:
        from connection_hub.delegated_credentials.slices.turns import RegisteredConversation

        deletion = await self._index.conversation_deletion_status(user_id=owner_subject,
                                                                  conversation_id=conversation_id)
        if deletion.get("open_deletion_id"):
            # A conversation being deleted does not exist for the Hub, so its
            # conversation capabilities are never seeded or reseeded.
            return None
        row = await self._index.get_conversation_state_row(user_id=owner_subject, conversation_id=conversation_id)
        if not row:
            return None
        ts = row.get("ts")
        created = int(ts.timestamp()) if isinstance(ts, datetime) else 0
        return RegisteredConversation(owner_subject=owner_subject, application=str(row.get("bundle_id") or ""),
                                      created_at=created, epoch=int(deletion.get("epoch") or 0))

    async def turn_alive(self, *, owner_subject: str, conversation_id: str, turn_id: str) -> bool:
        deletion = await self._index.conversation_deletion_status(user_id=owner_subject,
                                                                  conversation_id=conversation_id)
        if deletion.get("open_deletion_id"):
            return False
        row = await self._index.get_conversation_state_row(user_id=owner_subject, conversation_id=conversation_id)
        tags = _tags(row)
        if "conv.state:in_progress" not in tags or f"conv.turn:{turn_id}" not in tags:
            return False
        key = conversation_lock_key(tenant=self._tenant, project=self._project, user_id=owner_subject,
                                    conversation_id=conversation_id)
        return bool(await self._redis.exists(key))


__all__ = ["KDCubeConversationRegistry", "conversation_lock_key"]
