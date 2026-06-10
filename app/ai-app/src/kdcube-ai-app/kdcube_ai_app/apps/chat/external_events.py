# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.event_identity import (
    DEFAULT_REACT_AGENT_ID,
    normalize_agent_id,
    safe_event_lane_part,
)
from kdcube_ai_app.infra.namespaces import REDIS, ns_key

logger = logging.getLogger(__name__)

_DEFAULT_OWNER_TTL_SECONDS = 600
_PROMOTION_CONSUMER_GROUP = "react-external-promoter.v1"
_DEFAULT_STREAM_MAX_ENTRIES = max(64, int(os.getenv("CHAT_EXTERNAL_EVENTS_STREAM_MAX_ENTRIES") or "1024"))
_DEFAULT_STREAM_RETENTION_SECONDS = max(0, int(os.getenv("CHAT_EXTERNAL_EVENTS_STREAM_RETENTION_SECONDS") or str(7 * 24 * 3600)))
_DEFAULT_STREAM_TRIM_BATCH = max(16, int(os.getenv("CHAT_EXTERNAL_EVENTS_STREAM_TRIM_BATCH") or "256"))


def _base36(num: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    n = max(0, int(num or 0))
    if n == 0:
        return "0"
    out = []
    while n:
        n, rem = divmod(n, 36)
        out.append(chars[rem])
    return "".join(reversed(out))


def _short_event_message_id(*, created_at: float, sequence: int) -> str:
    millis = int(float(created_at or 0.0) * 1000)
    return f"m{_base36(millis)}{_base36(sequence)}"


@dataclass
class ConversationExternalEvent:
    message_id: str
    batch_id: str
    kind: str
    created_at: float
    sequence: int
    user_id: str = ""
    agent_id: str = DEFAULT_REACT_AGENT_ID
    event_source_id: str = ""
    stream_id: Optional[str] = None
    explicit: bool = False
    is_continuation: bool = False
    target_turn_id: Optional[str] = None
    active_turn_id_at_ingress: Optional[str] = None
    owner_turn_id: Optional[str] = None
    source: str = ""
    text: str = ""
    payload: Optional[Dict[str, Any]] = None
    task_payload: Optional[Dict[str, Any]] = None
    consumed_at: Optional[float] = None
    consumed_by_turn_id: Optional[str] = None
    promoted_at: Optional[float] = None
    promoted_task_id: Optional[str] = None
    failed_at: Optional[float] = None
    failed_reason: Optional[str] = None

    @property
    def event_id(self) -> str:
        return self.message_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "event_id": self.message_id,
            "batch_id": self.batch_id or "",
            "kind": str(self.kind or "external"),
            "created_at": float(self.created_at or 0.0),
            "sequence": int(self.sequence or 0),
            "user_id": self.user_id or "",
            "agent_id": self.agent_id or DEFAULT_REACT_AGENT_ID,
            "event_source_id": self.event_source_id or "",
            "stream_id": self.stream_id,
            "explicit": bool(self.explicit),
            "is_continuation": bool(self.is_continuation),
            "target_turn_id": self.target_turn_id,
            "active_turn_id_at_ingress": self.active_turn_id_at_ingress,
            "owner_turn_id": self.owner_turn_id,
            "source": self.source or "",
            "text": self.text or "",
            "payload": dict(self.payload or {}),
            "task_payload": dict(self.task_payload or {}),
            "consumed_at": float(self.consumed_at) if self.consumed_at is not None else None,
            "consumed_by_turn_id": self.consumed_by_turn_id,
            "promoted_at": float(self.promoted_at) if self.promoted_at is not None else None,
            "promoted_task_id": self.promoted_task_id,
            "failed_at": float(self.failed_at) if self.failed_at is not None else None,
            "failed_reason": self.failed_reason,
        }

    @classmethod
    def from_any(cls, raw: Any) -> "ConversationExternalEvent":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise TypeError(f"Unsupported external event payload: {type(raw)!r}")
        active_turn_id_at_ingress = raw.get("active_turn_id_at_ingress")
        owner_turn_id = raw.get("owner_turn_id")
        task_payload = dict(raw.get("task_payload") or {})
        continuation = task_payload.get("continuation") if isinstance(task_payload.get("continuation"), dict) else {}
        is_continuation = raw.get("is_continuation")
        if is_continuation is None:
            is_continuation = bool(
                continuation.get("is_continuation")
                or active_turn_id_at_ingress
                or owner_turn_id
            )
        return cls(
            message_id=str(raw.get("message_id") or raw.get("event_id") or ""),
            batch_id=str(raw.get("batch_id") or ""),
            kind=str(raw.get("kind") or "external"),
            created_at=float(raw.get("created_at") or 0.0),
            sequence=int(raw.get("sequence") or 0),
            user_id=str(raw.get("user_id") or ""),
            agent_id=normalize_agent_id(raw.get("agent_id")),
            event_source_id=str(raw.get("event_source_id") or ""),
            stream_id=raw.get("stream_id"),
            explicit=bool(raw.get("explicit")),
            is_continuation=bool(is_continuation),
            target_turn_id=raw.get("target_turn_id"),
            active_turn_id_at_ingress=active_turn_id_at_ingress,
            owner_turn_id=owner_turn_id,
            source=str(raw.get("source") or ""),
            text=str(raw.get("text") or ""),
            payload=dict(raw.get("payload") or {}),
            task_payload=task_payload,
            consumed_at=(float(raw.get("consumed_at")) if raw.get("consumed_at") is not None else None),
            consumed_by_turn_id=raw.get("consumed_by_turn_id"),
            promoted_at=(float(raw.get("promoted_at")) if raw.get("promoted_at") is not None else None),
            promoted_task_id=raw.get("promoted_task_id"),
            failed_at=(float(raw.get("failed_at")) if raw.get("failed_at") is not None else None),
            failed_reason=raw.get("failed_reason"),
        )

    def task_payload_model(self):
        from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload

        return ExternalEventPayload.model_validate(self.task_payload or {})


@dataclass
class TimelineOwnerLease:
    turn_id: str
    bundle_id: str = ""
    instance_id: str = ""
    process_id: int = 0
    listener_id: str = ""
    lease_token: str = ""
    lease_epoch: int = 0
    started_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id or "",
            "bundle_id": self.bundle_id or "",
            "instance_id": self.instance_id or "",
            "process_id": int(self.process_id or 0),
            "listener_id": self.listener_id or "",
            "lease_token": self.lease_token or "",
            "lease_epoch": int(self.lease_epoch or 0),
            "started_at": self.started_at or "",
            "updated_at": self.updated_at or "",
        }

    @classmethod
    def from_any(cls, raw: Any) -> Optional["TimelineOwnerLease"]:
        if raw is None:
            return None
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            return None
        turn_id = str(raw.get("turn_id") or "").strip()
        if not turn_id:
            return None
        return cls(
            turn_id=turn_id,
            bundle_id=str(raw.get("bundle_id") or ""),
            instance_id=str(raw.get("instance_id") or ""),
            process_id=int(raw.get("process_id") or 0),
            listener_id=str(raw.get("listener_id") or ""),
            lease_token=str(raw.get("lease_token") or ""),
            lease_epoch=int(raw.get("lease_epoch") or 0),
            started_at=str(raw.get("started_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )


class RedisConversationExternalEventSource:
    """
    Durable conversation event lane.

    The lane is partitioned by tenant/project/user/conversation/agent_id. Each
    agent inside a bundle gets its own ordered retained event lane for the same
    conversation, while the bundle entrypoint still receives the event through a
    single ``@on_reactive_event`` method and dispatches internally.
    """

    def __init__(
        self,
        *,
        redis,
        tenant: str,
        project: str,
        conversation_id: str,
        user_id: str = "",
        agent_id: str = DEFAULT_REACT_AGENT_ID,
        stream_max_entries: Optional[int] = None,
        stream_retention_seconds: Optional[int] = None,
        stream_trim_batch: Optional[int] = None,
    ):
        self.redis = redis
        self.tenant = tenant
        self.project = project
        self.conversation_id = conversation_id
        self.user_id = str(user_id or "").strip()
        self.agent_id = normalize_agent_id(agent_id)
        self.stream_max_entries = max(0, int(stream_max_entries if stream_max_entries is not None else _DEFAULT_STREAM_MAX_ENTRIES))
        self.stream_retention_seconds = max(0, int(stream_retention_seconds if stream_retention_seconds is not None else _DEFAULT_STREAM_RETENTION_SECONDS))
        self.stream_trim_batch = max(1, int(stream_trim_batch if stream_trim_batch is not None else _DEFAULT_STREAM_TRIM_BATCH))

    @property
    def lane_id(self) -> str:
        if not self.user_id and self.agent_id == DEFAULT_REACT_AGENT_ID:
            return safe_event_lane_part(self.conversation_id)
        return (
            f"{safe_event_lane_part(self.conversation_id)}"
            f":user:{safe_event_lane_part(self.user_id)}"
            f":agent:{safe_event_lane_part(self.agent_id, default=DEFAULT_REACT_AGENT_ID)}"
        )

    @property
    def log_key(self) -> str:
        base = ns_key(REDIS.CHAT.CONVERSATION_EXTERNAL_EVENTS_PREFIX, tenant=self.tenant, project=self.project)
        return f"{base}:{self.lane_id}"

    @property
    def sequence_key(self) -> str:
        base = ns_key(REDIS.CHAT.CONVERSATION_EXTERNAL_EVENTS_SEQ_PREFIX, tenant=self.tenant, project=self.project)
        return f"{base}:{self.lane_id}"

    def event_key(self, message_id: str) -> str:
        return f"{self.log_key}:event:{str(message_id or '').strip()}"

    def claim_key(self, message_id: str) -> str:
        return f"{self.log_key}:claim:{str(message_id or '').strip()}"

    @property
    def promotion_cursor_key(self) -> str:
        return f"{self.log_key}:promotion-cursor"

    @property
    def promotion_group(self) -> str:
        return _PROMOTION_CONSUMER_GROUP

    @property
    def owner_key(self) -> str:
        base = ns_key(REDIS.CHAT.CONVERSATION_TIMELINE_OWNER_PREFIX, tenant=self.tenant, project=self.project)
        return f"{base}:{self.lane_id}"

    @property
    def owner_token_key(self) -> str:
        return f"{self.owner_key}:token"

    @property
    def owner_epoch_key(self) -> str:
        return f"{self.owner_key}:epoch"

    async def publish(
        self,
        *,
        kind: str,
        event_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        explicit: bool = False,
        is_continuation: Optional[bool] = None,
        target_turn_id: Optional[str] = None,
        active_turn_id_at_ingress: Optional[str] = None,
        owner_turn_id: Optional[str] = None,
        source: str = "",
        event_source_id: str = "",
        text: str = "",
        payload: Optional[Dict[str, Any]] = None,
        task_payload: Optional[Dict[str, Any]] = None,
    ) -> ConversationExternalEvent:
        sequence = int(await self.redis.incr(self.sequence_key))
        created_at = time.time()
        message_id = safe_event_lane_part(event_id, default="") if event_id else ""
        if not message_id:
            message_id = _short_event_message_id(created_at=created_at, sequence=sequence)
        if not event_source_id and isinstance(task_payload, dict):
            task_event = task_payload.get("event")
            if isinstance(task_event, dict):
                event_source_id = str(task_event.get("event_source_id") or "").strip()
        continuation_flag = bool(is_continuation) if is_continuation is not None else bool(
            active_turn_id_at_ingress or owner_turn_id
        )
        event = ConversationExternalEvent(
            message_id=message_id,
            batch_id=str(batch_id or ""),
            kind=str(kind or "external"),
            created_at=created_at,
            sequence=sequence,
            user_id=self.user_id,
            agent_id=self.agent_id,
            event_source_id=str(event_source_id or ""),
            explicit=explicit,
            is_continuation=continuation_flag,
            target_turn_id=target_turn_id,
            active_turn_id_at_ingress=active_turn_id_at_ingress,
            owner_turn_id=owner_turn_id,
            source=source or "",
            text=text or "",
            payload=dict(payload or {}),
            task_payload=self._patch_task_payload_event(
                task_payload,
                kind=str(kind or "external"),
                event_id="",
                batch_id=str(batch_id or ""),
                sequence=sequence,
                event_source_id=str(event_source_id or ""),
                is_continuation=continuation_flag,
            ),
        )
        event.task_payload = self._patch_task_payload_event(
            event.task_payload,
            kind=str(event.kind or "external"),
            event_id=event.message_id,
            batch_id=event.batch_id,
            sequence=event.sequence,
            event_source_id=event.event_source_id,
            is_continuation=event.is_continuation,
        )
        stream_id = await self._append_to_stream(event)
        event.stream_id = str(stream_id or "")
        await self._write_event(event)
        logger.info(
            "[external_events.publish] conversation=%s agent_id=%s kind=%s event_source_id=%s event_id=%s batch_id=%s seq=%s stream_id=%s target_turn=%s active_turn=%s owner_turn=%s explicit=%s is_continuation=%s text=%r",
            self.conversation_id,
            self.agent_id,
            event.kind,
            event.event_source_id,
            event.message_id,
            event.batch_id,
            event.sequence,
            event.stream_id,
            event.target_turn_id,
            event.active_turn_id_at_ingress,
            event.owner_turn_id,
            event.explicit,
            event.is_continuation,
            (event.text or "")[:160],
        )
        await self._maybe_cleanup_retention()
        return event

    def _patch_task_payload_event(
        self,
        task_payload: Optional[Dict[str, Any]],
        *,
        kind: str,
        event_id: str,
        batch_id: str,
        sequence: int,
        event_source_id: str,
        is_continuation: bool,
    ) -> Dict[str, Any]:
        payload = dict(task_payload or {})
        event = payload.get("event")
        if not isinstance(event, dict):
            event = {}
        event.setdefault("kind", str(kind or "external"))
        event["agent_id"] = self.agent_id
        event["event_source_id"] = str(event_source_id or event.get("event_source_id") or "")
        event["is_continuation"] = bool(is_continuation)
        if event_id:
            event["event_id"] = str(event_id)
        if batch_id:
            event["batch_id"] = str(batch_id)
        if sequence:
            event["sequence"] = int(sequence)
        payload["event"] = event
        continuation = payload.get("continuation")
        if not isinstance(continuation, dict):
            continuation = {}
        continuation["is_continuation"] = bool(is_continuation)
        payload["continuation"] = continuation
        return payload

    async def read_since(self, cursor: str | int | None, *, limit: Optional[int] = None) -> List[ConversationExternalEvent]:
        raw_items = await self._stream_read_since(cursor, limit=limit)
        out: List[ConversationExternalEvent] = []
        for raw in raw_items or []:
            try:
                item = await self._read_event_ref(raw)
            except Exception:
                continue
            if item is None:
                continue
            out.append(item)
            if limit is not None and len(out) >= int(limit):
                break
        return out

    async def wait_for_events_after(
        self,
        cursor: str | int | None,
        *,
        block_ms: int = 3000,
        limit: Optional[int] = None,
    ) -> List[ConversationExternalEvent]:
        raw_items = await self._stream_wait_for_after(cursor, block_ms=block_ms, limit=limit)
        out: List[ConversationExternalEvent] = []
        for raw in raw_items or []:
            try:
                item = await self._read_event_ref(raw)
            except Exception:
                continue
            if item is None:
                continue
            out.append(item)
            if limit is not None and len(out) >= int(limit):
                break
        return out

    async def claim_next_promotable(
        self,
        *,
        claimant_id: str,
        ttl_seconds: int = 120,
        min_idle_ms: int = 15_000,
    ) -> Optional[ConversationExternalEvent]:
        consumer_name = str(claimant_id or "")
        for _ in range(200):
            raw_items = await self._promotion_group_read(consumer_name=consumer_name, count=1)
            if not raw_items:
                raw_items = await self._promotion_group_autoclaim(
                    consumer_name=consumer_name,
                    min_idle_ms=max(1, int(min_idle_ms or 1)),
                    count=1,
                )
            if not raw_items:
                break
            raw = raw_items[0]
            item = await self._read_event_ref(raw)
            if item is None:
                continue
            stream_id = str(item.stream_id or "")
            if item.failed_at is not None or item.task_payload is None or item.promoted_at is not None or item.consumed_at is not None:
                if stream_id:
                    await self._ack_stream_event(stream_id)
                continue
            latest = await self.get_event(item.message_id)
            if latest is None:
                if stream_id:
                    await self._ack_stream_event(stream_id)
                continue
            if latest.failed_at is not None or latest.promoted_at is not None or latest.consumed_at is not None:
                if latest.stream_id:
                    await self._ack_stream_event(str(latest.stream_id))
                continue
            if await self._has_live_owner_for_event(latest):
                if latest.stream_id:
                    await self._ack_stream_event(str(latest.stream_id))
                logger.info(
                    "[external_events.claim] deferred live-owned event conversation=%s claimant=%s event_id=%s kind=%s seq=%s active_turn=%s owner_turn=%s",
                    self.conversation_id,
                    claimant_id,
                    latest.message_id,
                    latest.kind,
                    latest.sequence,
                    latest.active_turn_id_at_ingress,
                    latest.owner_turn_id,
                )
                return None
            logger.info(
                "[external_events.claim] conversation=%s claimant=%s event_id=%s kind=%s seq=%s target_turn=%s active_turn=%s owner_turn=%s",
                self.conversation_id,
                claimant_id,
                latest.message_id,
                latest.kind,
                latest.sequence,
                latest.target_turn_id,
                latest.active_turn_id_at_ingress,
                latest.owner_turn_id,
            )
            return latest

        cursor = await self._get_promotion_cursor()
        raw_items = await self._stream_read_since(cursor, limit=200)
        if not raw_items:
            return None
        last_terminal_stream_id = str(cursor or "")
        for raw in raw_items or []:
            item = await self._read_event_ref(raw)
            if item is None:
                continue
            stream_id = str(item.stream_id or "")
            if item.failed_at is not None:
                if stream_id:
                    last_terminal_stream_id = stream_id
                continue
            if item.task_payload is None:
                if stream_id:
                    last_terminal_stream_id = stream_id
                continue
            if item.promoted_at is not None:
                if stream_id:
                    last_terminal_stream_id = stream_id
                continue
            if item.consumed_at is not None:
                if stream_id:
                    last_terminal_stream_id = stream_id
                continue
            if last_terminal_stream_id:
                await self._advance_promotion_cursor(last_terminal_stream_id)
            if not await self._claim_event(item.message_id, claimant_id=claimant_id, ttl_seconds=ttl_seconds):
                continue
            latest = await self.get_event(item.message_id)
            if latest is None:
                await self.release_claim(message_id=item.message_id, claimant_id=claimant_id)
                continue
            if latest.failed_at is not None or latest.promoted_at is not None or latest.consumed_at is not None:
                if latest.stream_id:
                    await self._advance_promotion_cursor(str(latest.stream_id))
                await self.release_claim(message_id=item.message_id, claimant_id=claimant_id)
                continue
            if await self._has_live_owner_for_event(latest):
                await self.release_claim(message_id=item.message_id, claimant_id=claimant_id)
                logger.info(
                    "[external_events.claim] deferred live-owned event conversation=%s claimant=%s event_id=%s kind=%s seq=%s active_turn=%s owner_turn=%s",
                    self.conversation_id,
                    claimant_id,
                    latest.message_id,
                    latest.kind,
                    latest.sequence,
                    latest.active_turn_id_at_ingress,
                    latest.owner_turn_id,
                )
                return None
            logger.info(
                "[external_events.claim] conversation=%s claimant=%s event_id=%s kind=%s seq=%s target_turn=%s active_turn=%s owner_turn=%s",
                self.conversation_id,
                claimant_id,
                latest.message_id,
                latest.kind,
                latest.sequence,
                latest.target_turn_id,
                latest.active_turn_id_at_ingress,
                latest.owner_turn_id,
            )
            return latest
        if raw_items:
            tail = await self._read_event_ref(raw_items[-1])
            if tail is not None and tail.stream_id:
                await self._advance_promotion_cursor(str(tail.stream_id))
        return None

    async def _has_live_owner_for_event(self, event: ConversationExternalEvent) -> bool:
        try:
            owner = await self.get_owner()
        except Exception:
            return False
        owner_turn_id = str(getattr(owner, "turn_id", "") or "").strip() if owner is not None else ""
        if not owner_turn_id:
            return False
        event_turn_ids = {
            str(getattr(event, "owner_turn_id", "") or "").strip(),
            str(getattr(event, "active_turn_id_at_ingress", "") or "").strip(),
            str(getattr(event, "target_turn_id", "") or "").strip(),
        }
        return owner_turn_id in event_turn_ids

    async def get_event(self, message_id: str) -> Optional[ConversationExternalEvent]:
        raw = await self.redis.get(self.event_key(message_id))
        if raw is None:
            return None
        try:
            return ConversationExternalEvent.from_any(raw)
        except Exception:
            return None

    async def release_claim(self, *, message_id: str, claimant_id: str) -> None:
        raw = await self.redis.get(self.claim_key(message_id))
        if raw is None:
            return
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if claimant_id and str(raw or "") != str(claimant_id):
            return
        await self.redis.delete(self.claim_key(message_id))

    async def mark_promoted(self, *, message_id: str, claimant_id: str, task_id: str) -> Optional[ConversationExternalEvent]:
        event = await self.get_event(message_id)
        if event is None:
            await self.release_claim(message_id=message_id, claimant_id=claimant_id)
            return None
        event.promoted_at = time.time()
        event.promoted_task_id = str(task_id or "")
        await self._write_event(event)
        if event.stream_id:
            await self._ack_stream_event(str(event.stream_id))
            await self._advance_promotion_cursor(str(event.stream_id))
        await self.release_claim(message_id=message_id, claimant_id=claimant_id)
        logger.info(
            "[external_events.promoted] conversation=%s claimant=%s event_id=%s kind=%s seq=%s promoted_task_id=%s target_turn=%s active_turn=%s owner_turn=%s",
            self.conversation_id,
            claimant_id,
            event.message_id,
            event.kind,
            event.sequence,
            event.promoted_task_id,
            event.target_turn_id,
            event.active_turn_id_at_ingress,
            event.owner_turn_id,
        )
        await self._maybe_cleanup_retention()
        return event

    async def mark_failed(self, *, message_id: str, claimant_id: str, reason: str) -> Optional[ConversationExternalEvent]:
        event = await self.get_event(message_id)
        if event is None:
            await self.release_claim(message_id=message_id, claimant_id=claimant_id)
            return None
        event.failed_at = time.time()
        event.failed_reason = str(reason or "failed")
        await self._write_event(event)
        if event.stream_id:
            await self._ack_stream_event(str(event.stream_id))
            await self._advance_promotion_cursor(str(event.stream_id))
        await self.release_claim(message_id=message_id, claimant_id=claimant_id)
        await self._maybe_cleanup_retention()
        return event

    async def mark_consumed_up_to(self, *, max_sequence: int, turn_id: str) -> int:
        max_sequence = int(max_sequence or 0)
        if max_sequence <= 0:
            return 0
        updated = 0
        raw_items = await self._stream_read_all()
        max_stream_id = ""
        for raw in raw_items or []:
            item = await self._read_event_ref(raw)
            if item is None:
                continue
            if int(item.sequence or 0) > max_sequence:
                continue
            if item.consumed_at is not None:
                continue
            if item.failed_at is not None:
                continue
            item.consumed_at = time.time()
            item.consumed_by_turn_id = str(turn_id or "")
            await self._write_event(item)
            if item.stream_id:
                await self._ack_stream_event(str(item.stream_id))
                max_stream_id = str(item.stream_id)
            updated += 1
        if max_stream_id:
            await self._advance_promotion_cursor(max_stream_id)
        if updated:
            logger.info(
                "[external_events.consumed] conversation=%s turn_id=%s max_sequence=%s updated=%s",
                self.conversation_id,
                turn_id,
                max_sequence,
                updated,
            )
            await self._maybe_cleanup_retention()
        return updated

    async def get_owner(self) -> Optional[TimelineOwnerLease]:
        raw = await self.redis.get(self.owner_key)
        return TimelineOwnerLease.from_any(raw)

    async def acquire_owner(
        self,
        *,
        turn_id: str,
        bundle_id: str = "",
        listener_id: str = "",
        ttl_seconds: int = _DEFAULT_OWNER_TTL_SECONDS,
    ) -> TimelineOwnerLease:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        lease_token = f"lease_{uuid.uuid4().hex[:16]}"
        listener_id = listener_id or f"listener_{uuid.uuid4().hex[:8]}"
        lease_epoch = int(
            await self._acquire_owner_epoch_and_write(
                turn_id=str(turn_id or ""),
                bundle_id=str(bundle_id or ""),
                listener_id=str(listener_id or ""),
                lease_token=str(lease_token or ""),
                started_at=now_iso,
                updated_at=now_iso,
                ttl_seconds=max(1, int(ttl_seconds or _DEFAULT_OWNER_TTL_SECONDS)),
            )
        )
        lease = TimelineOwnerLease(
            turn_id=turn_id or "",
            bundle_id=bundle_id or "",
            instance_id=get_settings().INSTANCE_ID,
            process_id=os.getpid(),
            listener_id=listener_id,
            lease_token=lease_token,
            lease_epoch=lease_epoch,
            started_at=now_iso,
            updated_at=now_iso,
        )
        logger.info(
            "[external_events.owner.acquire] conversation=%s turn_id=%s listener_id=%s lease_epoch=%s",
            self.conversation_id,
            lease.turn_id,
            lease.listener_id,
            lease.lease_epoch,
        )
        return lease

    async def refresh_owner(
        self,
        *,
        listener_id: str,
        turn_id: str,
        bundle_id: str = "",
        lease_token: str = "",
        ttl_seconds: int = _DEFAULT_OWNER_TTL_SECONDS,
    ) -> Optional[TimelineOwnerLease]:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        existing = await self.get_owner()
        if existing is None:
            return None
        if listener_id and existing.listener_id != listener_id:
            return None
        if lease_token and existing.lease_token != str(lease_token or ""):
            return None
        lease = TimelineOwnerLease(
            turn_id=turn_id or "",
            bundle_id=bundle_id or existing.bundle_id,
            instance_id=get_settings().INSTANCE_ID,
            process_id=os.getpid(),
            listener_id=listener_id or existing.listener_id,
            lease_token=existing.lease_token,
            lease_epoch=int(existing.lease_epoch or 0),
            started_at=existing.started_at or now_iso,
            updated_at=now_iso,
        )
        updated = await self._compare_and_set_owner_lease(
            expected_token=str(existing.lease_token or ""),
            lease=lease,
            ttl_seconds=max(1, int(ttl_seconds or _DEFAULT_OWNER_TTL_SECONDS)),
        )
        if not updated:
            return None
        logger.info(
            "[external_events.owner.refresh] conversation=%s turn_id=%s listener_id=%s lease_epoch=%s",
            self.conversation_id,
            lease.turn_id,
            lease.listener_id,
            lease.lease_epoch,
        )
        return lease

    async def release_owner(self, *, listener_id: str, lease_token: str = "") -> bool:
        existing = await self.get_owner()
        if existing is None:
            return False
        if listener_id and existing.listener_id and existing.listener_id != listener_id:
            return False
        if lease_token and existing.lease_token and existing.lease_token != str(lease_token or ""):
            return False
        deleted = bool(await self._delete_owner_lease(expected_token=str(existing.lease_token or "")))
        if deleted:
            logger.info(
                "[external_events.owner.release] conversation=%s turn_id=%s listener_id=%s lease_epoch=%s",
                self.conversation_id,
                existing.turn_id,
                existing.listener_id,
                existing.lease_epoch,
            )
        return deleted

    async def _read_event_ref(self, raw: Any) -> Optional[ConversationExternalEvent]:
        stream_id = None
        fields: Any = raw
        if isinstance(raw, (tuple, list)) and len(raw) == 2:
            stream_id, fields = raw[0], raw[1]
        if isinstance(stream_id, bytes):
            stream_id = stream_id.decode("utf-8")
        if isinstance(fields, bytes):
            fields = fields.decode("utf-8")
        if isinstance(fields, str):
            stripped = fields.strip()
            if stripped.startswith("{"):
                item = ConversationExternalEvent.from_any(stripped)
                if item.stream_id is None and stream_id:
                    item.stream_id = str(stream_id)
                return item
            item = await self.get_event(stripped)
            if item is not None and item.stream_id is None and stream_id:
                item.stream_id = str(stream_id)
            return item
        if isinstance(fields, dict):
            message_id = None
            for key in ("message_id", b"message_id"):
                value = fields.get(key)
                if value is not None:
                    message_id = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                    break
            if message_id:
                item = await self.get_event(message_id)
                if item is not None and item.stream_id is None and stream_id:
                    item.stream_id = str(stream_id)
                return item
            item = ConversationExternalEvent.from_any(fields)
            if item.stream_id is None and stream_id:
                item.stream_id = str(stream_id)
            return item
        return None

    async def _write_event(self, event: ConversationExternalEvent) -> None:
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        setter = getattr(self.redis, "set", None)
        if callable(setter):
            try:
                await setter(self.event_key(event.message_id), payload)
                return
            except TypeError:
                pass
        await self.redis.setex(self.event_key(event.message_id), 315360000, payload)

    async def _acquire_owner_epoch_and_write(
        self,
        *,
        turn_id: str,
        bundle_id: str,
        listener_id: str,
        lease_token: str,
        started_at: str,
        updated_at: str,
        ttl_seconds: int,
    ) -> int:
        script = """
local epoch_key = KEYS[1]
local token_key = KEYS[2]
local owner_key = KEYS[3]
local ttl = tonumber(ARGV[1])
local epoch = redis.call('INCR', epoch_key)
local owner = cjson.encode({
  turn_id = ARGV[2],
  bundle_id = ARGV[3],
  instance_id = ARGV[4],
  process_id = tonumber(ARGV[5]),
  listener_id = ARGV[6],
  lease_token = ARGV[7],
  lease_epoch = epoch,
  started_at = ARGV[8],
  updated_at = ARGV[9]
})
redis.call('SETEX', owner_key, ttl, owner)
redis.call('SETEX', token_key, ttl, ARGV[7])
return epoch
"""
        evaluator = getattr(self.redis, "eval", None)
        instance_id = get_settings().INSTANCE_ID
        process_id = os.getpid()
        if callable(evaluator):
            try:
                res = await evaluator(
                    script,
                    3,
                    self.owner_epoch_key,
                    self.owner_token_key,
                    self.owner_key,
                    str(max(1, int(ttl_seconds or 1))),
                    str(turn_id or ""),
                    str(bundle_id or ""),
                    str(instance_id or ""),
                    str(process_id),
                    str(listener_id or ""),
                    str(lease_token or ""),
                    str(started_at or ""),
                    str(updated_at or ""),
                )
                return int(res or 0)
            except Exception:
                pass
        epoch = int(await self.redis.incr(self.owner_epoch_key))
        lease = TimelineOwnerLease(
            turn_id=str(turn_id or ""),
            bundle_id=str(bundle_id or ""),
            instance_id=str(instance_id or ""),
            process_id=int(process_id or 0),
            listener_id=str(listener_id or ""),
            lease_token=str(lease_token or ""),
            lease_epoch=epoch,
            started_at=str(started_at or ""),
            updated_at=str(updated_at or ""),
        )
        await self._write_owner_lease(lease, ttl_seconds=max(1, int(ttl_seconds or 1)))
        return epoch

    async def _write_owner_lease(self, lease: TimelineOwnerLease, *, ttl_seconds: int) -> None:
        payload = json.dumps(lease.to_dict(), ensure_ascii=False)
        setter = getattr(self.redis, "set", None)
        if callable(setter):
            try:
                await setter(self.owner_key, payload, ex=ttl_seconds)
                await setter(self.owner_token_key, str(lease.lease_token or ""), ex=ttl_seconds)
                return
            except TypeError:
                pass
        await self.redis.setex(self.owner_key, ttl_seconds, payload)
        await self.redis.setex(self.owner_token_key, ttl_seconds, str(lease.lease_token or ""))

    async def _compare_and_set_owner_lease(self, *, expected_token: str, lease: TimelineOwnerLease, ttl_seconds: int) -> bool:
        payload = json.dumps(lease.to_dict(), ensure_ascii=False)
        script = """
local token_key = KEYS[1]
local owner_key = KEYS[2]
local expected = ARGV[1]
local ttl = tonumber(ARGV[2])
local owner_json = ARGV[3]
local lease_token = ARGV[4]
local current = redis.call('GET', token_key)
if not current then return 0 end
if current ~= expected then return 0 end
redis.call('SETEX', owner_key, ttl, owner_json)
redis.call('SETEX', token_key, ttl, lease_token)
return 1
"""
        evaluator = getattr(self.redis, "eval", None)
        if callable(evaluator):
            try:
                res = await evaluator(
                    script,
                    2,
                    self.owner_token_key,
                    self.owner_key,
                    str(expected_token or ""),
                    str(max(1, int(ttl_seconds or 1))),
                    payload,
                    str(lease.lease_token or ""),
                )
                return bool(int(res or 0))
            except Exception:
                pass
        current = await self.redis.get(self.owner_token_key)
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        if str(current or "") != str(expected_token or ""):
            return False
        await self._write_owner_lease(lease, ttl_seconds=ttl_seconds)
        return True

    async def _delete_owner_lease(self, *, expected_token: str) -> bool:
        script = """
local token_key = KEYS[1]
local owner_key = KEYS[2]
local expected = ARGV[1]
local current = redis.call('GET', token_key)
if not current then return 0 end
if current ~= expected then return 0 end
redis.call('DEL', owner_key)
redis.call('DEL', token_key)
return 1
"""
        evaluator = getattr(self.redis, "eval", None)
        if callable(evaluator):
            try:
                res = await evaluator(
                    script,
                    2,
                    self.owner_token_key,
                    self.owner_key,
                    str(expected_token or ""),
                )
                return bool(int(res or 0))
            except Exception:
                pass
        current = await self.redis.get(self.owner_token_key)
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        if str(current or "") != str(expected_token or ""):
            return False
        await self.redis.delete(self.owner_key)
        await self.redis.delete(self.owner_token_key)
        return True

    async def _claim_event(self, message_id: str, *, claimant_id: str, ttl_seconds: int) -> bool:
        setter = getattr(self.redis, "set", None)
        if callable(setter):
            try:
                res = await setter(
                    self.claim_key(message_id),
                    str(claimant_id or ""),
                    ex=max(1, int(ttl_seconds or 120)),
                    nx=True,
                )
                return bool(res)
            except TypeError:
                pass
        raw = await self.redis.get(self.claim_key(message_id))
        if raw is not None:
            return False
        await self.redis.setex(
            self.claim_key(message_id),
            max(1, int(ttl_seconds or 120)),
            str(claimant_id or ""),
        )
        return True

    async def _get_promotion_cursor(self) -> str:
        raw = await self.redis.get(self.promotion_cursor_key)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return str(raw or "").strip()

    async def _advance_promotion_cursor(self, stream_id: str) -> None:
        stream_id = str(stream_id or "").strip()
        if not stream_id:
            return
        current = await self._get_promotion_cursor()
        if current and self._compare_stream_ids(stream_id, current) <= 0:
            return
        setter = getattr(self.redis, "set", None)
        if callable(setter):
            try:
                await setter(self.promotion_cursor_key, stream_id)
                return
            except TypeError:
                pass
        await self.redis.setex(self.promotion_cursor_key, 315360000, stream_id)

    async def _append_to_stream(self, event: ConversationExternalEvent) -> str:
        fields = {"message_id": str(event.message_id or "")}
        xadd = getattr(self.redis, "xadd", None)
        if callable(xadd):
            stream_id = await xadd(self.log_key, fields)
            if isinstance(stream_id, bytes):
                stream_id = stream_id.decode("utf-8")
            return str(stream_id or "")
        await self.redis.rpush(self.log_key, event.message_id)
        return ""

    async def _delete_stream_event(self, stream_id: str) -> None:
        stream_id = str(stream_id or "").strip()
        if not stream_id:
            return
        await self._ack_stream_event(stream_id)
        deleter = getattr(self.redis, "xdel", None)
        if callable(deleter):
            try:
                await deleter(self.log_key, stream_id)
            except Exception:
                pass

    def _terminal_timestamp(self, event: Optional[ConversationExternalEvent]) -> Optional[float]:
        if event is None:
            return None
        for value in (event.consumed_at, event.promoted_at, event.failed_at):
            if value is not None:
                try:
                    return float(value)
                except Exception:
                    continue
        return None

    def _raw_stream_id(self, raw: Any) -> str:
        if isinstance(raw, (tuple, list)) and len(raw) == 2:
            stream_id = raw[0]
            if isinstance(stream_id, bytes):
                stream_id = stream_id.decode("utf-8")
            return str(stream_id or "")
        return ""

    async def _maybe_cleanup_retention(self) -> int:
        max_entries = int(self.stream_max_entries or 0)
        retention_seconds = int(self.stream_retention_seconds or 0)
        if max_entries <= 0 and retention_seconds <= 0:
            return 0
        raw_items = list(await self._stream_read_all() or [])
        if not raw_items:
            return 0
        overflow = max(0, len(raw_items) - max_entries) if max_entries > 0 else 0
        if overflow <= 0 and self.stream_trim_batch > 0 and len(raw_items) > self.stream_trim_batch:
            raw_items = raw_items[: self.stream_trim_batch]
        now = time.time()
        removed = 0
        for raw in raw_items:
            item = await self._read_event_ref(raw)
            stream_id = str(getattr(item, "stream_id", "") or "") if item is not None else self._raw_stream_id(raw)
            if not stream_id:
                continue
            terminal_at = self._terminal_timestamp(item)
            should_trim_for_age = bool(
                retention_seconds > 0
                and terminal_at is not None
                and (now - float(terminal_at or 0.0)) >= retention_seconds
            )
            should_trim_for_overflow = bool(overflow > 0 and (terminal_at is not None or item is None))
            if not should_trim_for_age and not should_trim_for_overflow:
                continue
            await self._delete_stream_event(stream_id)
            if item is not None:
                await self.redis.delete(self.event_key(item.message_id))
                await self.redis.delete(self.claim_key(item.message_id))
            removed += 1
            if should_trim_for_overflow and overflow > 0:
                overflow -= 1
        return removed

    async def _ensure_promotion_group(self) -> None:
        creator = getattr(self.redis, "xgroup_create", None)
        if not callable(creator):
            return
        try:
            await creator(self.log_key, self.promotion_group, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _promotion_group_read(self, *, consumer_name: str, count: int) -> List[Any]:
        reader = getattr(self.redis, "xreadgroup", None)
        if not callable(reader):
            return []
        await self._ensure_promotion_group()
        response = await reader(
            self.promotion_group,
            str(consumer_name or "promoter"),
            {self.log_key: ">"},
            count=max(1, int(count or 1)),
            block=1,
        )
        return self._flatten_xread_response(response)

    async def _promotion_group_autoclaim(self, *, consumer_name: str, min_idle_ms: int, count: int) -> List[Any]:
        claimer = getattr(self.redis, "xautoclaim", None)
        if not callable(claimer):
            return []
        await self._ensure_promotion_group()
        response = await claimer(
            self.log_key,
            self.promotion_group,
            str(consumer_name or "promoter"),
            max(1, int(min_idle_ms or 1)),
            "0-0",
            count=max(1, int(count or 1)),
        )
        if isinstance(response, (tuple, list)) and len(response) >= 2:
            return list(response[1] or [])
        return []

    async def _ack_stream_event(self, stream_id: str) -> None:
        stream_id = str(stream_id or "").strip()
        if not stream_id:
            return
        acker = getattr(self.redis, "xack", None)
        if not callable(acker):
            return
        await self._ensure_promotion_group()
        try:
            await acker(self.log_key, self.promotion_group, stream_id)
        except Exception:
            pass

    async def _stream_read_all(self) -> List[Any]:
        xrange = getattr(self.redis, "xrange", None)
        if callable(xrange):
            return list(await xrange(self.log_key, min="-", max="+"))
        return list(await self.redis.lrange(self.log_key, 0, -1))

    async def _stream_read_since(self, cursor: str | int | None, *, limit: Optional[int] = None) -> List[Any]:
        xrange = getattr(self.redis, "xrange", None)
        if callable(xrange):
            start = "-"
            if cursor not in (None, "", 0, "0"):
                if isinstance(cursor, str) and "-" in cursor:
                    start = f"({cursor}"
                else:
                    start = "-"
            items = list(await xrange(self.log_key, min=start, max="+", count=limit))
            if isinstance(cursor, int) and cursor > 0:
                out: List[Any] = []
                for raw in items:
                    item = await self._read_event_ref(raw)
                    if item is None:
                        continue
                    if int(item.sequence or 0) <= int(cursor):
                        continue
                    out.append(raw)
                    if limit is not None and len(out) >= int(limit):
                        break
                return out
            return items
        raw_items = await self.redis.lrange(self.log_key, 0, -1)
        if isinstance(cursor, int):
            seq_floor = int(cursor or 0)
            out = []
            for raw in raw_items or []:
                item = await self._read_event_ref(raw)
                if item is None or int(item.sequence or 0) <= seq_floor:
                    continue
                out.append(raw)
                if limit is not None and len(out) >= int(limit):
                    break
            return out
        return list(raw_items or [])

    async def _stream_wait_for_after(self, cursor: str | int | None, *, block_ms: int, limit: Optional[int] = None) -> List[Any]:
        xread = getattr(self.redis, "xread", None)
        if callable(xread):
            if isinstance(cursor, str) and "-" in cursor:
                start_id = cursor
            elif cursor not in (None, "", 0, "0"):
                return await self._stream_read_since(cursor, limit=limit)
            else:
                start_id = "$"
            response = await xread(
                {self.log_key: start_id},
                count=limit,
                block=max(1, int(block_ms or 1)),
            )
            return self._flatten_xread_response(response)
        sleep_seconds = max(0.05, float(block_ms or 0) / 1000.0)
        await _sleep_async(sleep_seconds)
        return await self._stream_read_since(cursor, limit=limit)

    def _flatten_xread_response(self, response: Any) -> List[Any]:
        if not response:
            return []
        out: List[Any] = []
        if isinstance(response, dict):
            for items in response.values():
                if isinstance(items, list):
                    out.extend(items)
            return out
        if isinstance(response, list):
            for entry in response:
                if isinstance(entry, (tuple, list)) and len(entry) == 2:
                    items = entry[1]
                    if isinstance(items, list):
                        out.extend(items)
            return out
        return []

    def _compare_stream_ids(self, left: str, right: str) -> int:
        def _parts(value: str) -> tuple[int, int]:
            try:
                first, second = str(value or "").split("-", 1)
                return int(first), int(second)
            except Exception:
                return 0, 0

        l = _parts(left)
        r = _parts(right)
        if l < r:
            return -1
        if l > r:
            return 1
        return 0


async def _sleep_async(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def build_conversation_external_event_source(
    *,
    redis,
    tenant: str,
    project: str,
    conversation_id: str,
    user_id: str = "",
    agent_id: str = DEFAULT_REACT_AGENT_ID,
    stream_max_entries: Optional[int] = None,
    stream_retention_seconds: Optional[int] = None,
    stream_trim_batch: Optional[int] = None,
) -> RedisConversationExternalEventSource:
    return RedisConversationExternalEventSource(
        redis=redis,
        tenant=tenant,
        project=project,
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=agent_id,
        stream_max_entries=stream_max_entries,
        stream_retention_seconds=stream_retention_seconds,
        stream_trim_batch=stream_trim_batch,
    )
