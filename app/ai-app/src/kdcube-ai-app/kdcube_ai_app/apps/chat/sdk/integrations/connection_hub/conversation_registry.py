# SPDX-License-Identifier: MIT

"""W40: KDCube's conversation registry, as Connection Hub reads it.

The Hub asks two things, both answered from KDCube's own records:

- ``conversation``: who owns a conversation and in which application. The
  conversation-state row (``artifact:conversation.state`` in
  ``conv_messages``) is keyed by ``(user_id, conversation_id)`` and carries the
  ``bundle_id``; a conversation has no row until its first turn or its first
  picker write registers it.
- ``turn_alive``: the state row says ``in_progress`` for exactly this turn
  AND the processor still holds the conversation's Redis lock. The lock is
  renewed while the turn runs (``processor._lock_renewer``) and lapses within
  its TTL after the processor dies; the long-lived "started" marker is not a
  liveness signal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from kdcube_ai_app.apps.chat.sdk.event_identity import safe_event_lane_part
from kdcube_ai_app.infra.namespaces import REDIS, ns_key


def conversation_lock_key(*, tenant: str, project: str, user_id: str, conversation_id: str) -> str:
    """The processor's per-conversation lock (``processor._task_conversation_lock_key``)."""
    parts = (safe_event_lane_part(tenant, default="_"), safe_event_lane_part(project, default="_"),
             safe_event_lane_part(user_id, default="_"), safe_event_lane_part(conversation_id, default="_"))
    prefix = ns_key(REDIS.SYNCHRONIZATION.LOCK, tenant=tenant, project=project)
    return f"{prefix}:conversation:{':'.join(parts)}"


def _tags(row: Any) -> list[str]:
    return [str(tag) for tag in (row or {}).get("tags") or ()]


class KDCubeConversationRegistry:
    """Read-only view over the conversation index and the processor's locks."""

    def __init__(self, *, conv_index: Any, redis: Any, tenant: str, project: str) -> None:
        self._index, self._redis = conv_index, redis
        self._tenant, self._project = tenant, project

    async def conversation(self, *, owner_subject: str, conversation_id: str) -> Optional[Any]:
        from connection_hub.delegated_credentials.slices.turns import RegisteredConversation

        row = await self._index.get_conversation_state_row(user_id=owner_subject, conversation_id=conversation_id)
        if not row:
            return None
        ts = row.get("ts")
        created = int(ts.timestamp()) if isinstance(ts, datetime) else 0
        return RegisteredConversation(owner_subject=owner_subject, application=str(row.get("bundle_id") or ""),
                                      created_at=created)

    async def turn_alive(self, *, owner_subject: str, conversation_id: str, turn_id: str) -> bool:
        row = await self._index.get_conversation_state_row(user_id=owner_subject, conversation_id=conversation_id)
        tags = _tags(row)
        if "conv.state:in_progress" not in tags or f"conv.turn:{turn_id}" not in tags:
            return False
        key = conversation_lock_key(tenant=self._tenant, project=self._project, user_id=owner_subject,
                                    conversation_id=conversation_id)
        return bool(await self._redis.exists(key))


__all__ = ["KDCubeConversationRegistry", "conversation_lock_key"]
