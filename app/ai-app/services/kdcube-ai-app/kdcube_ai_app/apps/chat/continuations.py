# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from kdcube_ai_app.apps.chat.sdk.continuations import ContinuationEnvelope, ContinuationKind
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.namespaces import REDIS, ns_key

logger = logging.getLogger(__name__)


class RedisConversationContinuationSource:
    """
    Ordered per-conversation mailbox stored in Redis Lists.

    Ordering model:
    - publish via LPUSH
    - peek/take oldest via right side
    """

    def __init__(self, *, redis, tenant: str, project: str, conversation_id: str):
        self.redis = redis
        self.tenant = tenant
        self.project = project
        self.conversation_id = conversation_id

    @property
    def mailbox_key(self) -> str:
        base = ns_key(REDIS.CHAT.CONVERSATION_MAILBOX_PREFIX, tenant=self.tenant, project=self.project)
        return f"{base}:{self.conversation_id}"

    @property
    def sequence_key(self) -> str:
        base = ns_key(REDIS.CHAT.CONVERSATION_MAILBOX_SEQ_PREFIX, tenant=self.tenant, project=self.project)
        return f"{base}:{self.conversation_id}"

    def _count_key(self, user_type: str) -> str:
        base = ns_key(REDIS.CHAT.CONVERSATION_MAILBOX_COUNT_PREFIX, tenant=self.tenant, project=self.project)
        return f"{base}:{str(user_type).lower()}"

    async def publish(
        self,
        task_payload: ChatTaskPayload | Dict[str, Any],
        *,
        kind: ContinuationKind,
        explicit: bool = False,
        target_turn_id: Optional[str] = None,
        active_turn_id: Optional[str] = None,
    ) -> ContinuationEnvelope:
        payload_dict = (
            task_payload.model_dump()
            if hasattr(task_payload, "model_dump")
            else dict(task_payload or {})
        )
        sequence = int(await self.redis.incr(self.sequence_key))
        payload_user_type = (
            ((payload_dict.get("user") or {}).get("user_type") or "anonymous")
            if isinstance(payload_dict, dict)
            else "anonymous"
        )
        envelope = ContinuationEnvelope(
            message_id=f"cont_{uuid.uuid4().hex[:10]}",
            kind=kind,
            created_at=time.time(),
            sequence=sequence,
            explicit=explicit,
            target_turn_id=target_turn_id,
            active_turn_id=active_turn_id,
            payload=payload_dict,
        )
        await self.redis.lpush(
            self.mailbox_key,
            json.dumps(envelope.to_dict(), ensure_ascii=False),
        )
        await self.redis.incr(self._count_key(payload_user_type))
        return envelope

    async def has_pending(self) -> bool:
        return (await self.pending_count()) > 0

    async def pending_count(self) -> int:
        return int(await self.redis.llen(self.mailbox_key))

    async def peek_next(self) -> Optional[ContinuationEnvelope]:
        raw_items = await self.redis.lrange(self.mailbox_key, -1, -1)
        if not raw_items:
            return None
        return self._decode(raw_items[0])

    async def take_next(self) -> Optional[ContinuationEnvelope]:
        raw = await self.redis.rpop(self.mailbox_key)
        if raw is None:
            return None
        envelope = self._decode(raw)
        await self._decr_count_for_envelope(envelope)
        return envelope

    async def restore_taken(self, envelope: ContinuationEnvelope) -> None:
        await self.redis.rpush(
            self.mailbox_key,
            json.dumps(envelope.to_dict(), ensure_ascii=False),
        )
        await self.redis.incr(self._count_key(self._user_type_from_envelope(envelope)))

    def _decode(self, raw: Any) -> ContinuationEnvelope:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            raw = json.loads(raw)
        return ContinuationEnvelope.from_any(raw)

    def _user_type_from_envelope(self, envelope: ContinuationEnvelope) -> str:
        payload = envelope.payload or {}
        return str(((payload.get("user") or {}).get("user_type") or "anonymous")).lower()

    async def _decr_count_for_envelope(self, envelope: ContinuationEnvelope) -> None:
        try:
            await self.redis.decr(self._count_key(self._user_type_from_envelope(envelope)))
        except Exception:
            logger.debug("Failed to decrement continuation count", exc_info=True)


def build_conversation_continuation_source(*, redis, payload: ChatTaskPayload) -> RedisConversationContinuationSource:
    return RedisConversationContinuationSource(
        redis=redis,
        tenant=payload.actor.tenant_id,
        project=payload.actor.project_id,
        conversation_id=payload.routing.conversation_id or payload.routing.session_id,
    )
