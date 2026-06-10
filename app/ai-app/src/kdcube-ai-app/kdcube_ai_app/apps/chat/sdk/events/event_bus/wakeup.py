# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from kdcube_ai_app.apps.chat.sdk.event_identity import DEFAULT_REACT_AGENT_ID, normalize_agent_id
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventLaneRef, ExternalEventLaneWakeup, ExternalEventPayload
from kdcube_ai_app.infra.namespaces import REDIS, ns_key


WakeEnqueue = Callable[[ExternalEventLaneWakeup], Awaitable[Any]]


@dataclass(frozen=True)
class EventLaneWakePublishResult:
    success: bool
    wakeup: ExternalEventLaneWakeup
    reason: str = ""
    stats: dict[str, Any] = field(default_factory=dict)


def build_event_lane_ref(
    *,
    tenant: Optional[str],
    project: Optional[str],
    user_id: Optional[str],
    conversation_id: str,
    agent_id: str,
    event: Any,
) -> ExternalEventLaneRef:
    return ExternalEventLaneRef(
        tenant=tenant,
        project=project,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_id=normalize_agent_id(agent_id, default=DEFAULT_REACT_AGENT_ID),
        event_id=str(getattr(event, "message_id", "") or "") or None,
        sequence=int(getattr(event, "sequence", 0) or 0) or None,
        stream_id=str(getattr(event, "stream_id", "") or "") or None,
    )


def build_event_lane_wakeup(
    *,
    payload: ExternalEventPayload,
    event: Any,
    tenant: Optional[str],
    project: Optional[str],
    user_id: Optional[str],
    conversation_id: str,
    agent_id: str,
    reason: str,
) -> ExternalEventLaneWakeup:
    return ExternalEventLaneWakeup(
        meta=payload.meta,
        routing=payload.routing,
        actor=payload.actor,
        user=payload.user,
        config=payload.config,
        accounting=payload.accounting,
        continuation=payload.continuation,
        event=payload.event,
        bundle_call_context=dict(getattr(payload, "bundle_call_context", {}) or {}),
        event_lane=build_event_lane_ref(
            tenant=tenant,
            project=project,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            event=event,
        ),
        reason=reason,
    )


class EventLaneWakePublisher:
    """Send conversation external-event lane wakeups through an injected queue."""

    def __init__(self, enqueue: WakeEnqueue) -> None:
        self._enqueue = enqueue

    async def publish_wakeup(self, wakeup: ExternalEventLaneWakeup) -> EventLaneWakePublishResult:
        raw = await self._enqueue(wakeup)
        if isinstance(raw, EventLaneWakePublishResult):
            return raw
        if isinstance(raw, tuple):
            success = bool(raw[0]) if len(raw) >= 1 else False
            reason = str(raw[1] or "") if len(raw) >= 2 else ""
            stats = raw[2] if len(raw) >= 3 and isinstance(raw[2], dict) else {}
            return EventLaneWakePublishResult(success=success, reason=reason, stats=stats, wakeup=wakeup)
        if isinstance(raw, dict):
            return EventLaneWakePublishResult(
                success=bool(raw.get("success", raw.get("ok", False))),
                reason=str(raw.get("reason") or ""),
                stats=dict(raw.get("stats") or {}),
                wakeup=wakeup,
            )
        return EventLaneWakePublishResult(success=bool(raw), wakeup=wakeup)

    async def publish_for_event(
        self,
        *,
        payload: ExternalEventPayload,
        event: Any,
        tenant: Optional[str],
        project: Optional[str],
        user_id: Optional[str],
        conversation_id: str,
        agent_id: str,
        reason: str,
    ) -> EventLaneWakePublishResult:
        wakeup = build_event_lane_wakeup(
            payload=payload,
            event=event,
            tenant=tenant,
            project=project,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            reason=reason,
        )
        return await self.publish_wakeup(wakeup)


def event_lane_wakeup_queue_key(*, tenant: str, project: str, user_type: str) -> str:
    queue_prefix = ns_key(REDIS.CHAT.PROMPT_QUEUE_PREFIX, tenant=tenant, project=project)
    return f"{queue_prefix}:{str(user_type or 'registered').lower()}"


class RedisEventLaneWakeEnqueuer:
    """Queue sender for lane wakeups that run through the normal processor queue."""

    def __init__(self, *, redis: Any, tenant: str, project: str) -> None:
        self.redis = redis
        self.tenant = str(tenant or "")
        self.project = str(project or "")

    async def __call__(self, wakeup: ExternalEventLaneWakeup) -> EventLaneWakePublishResult:
        if self.redis is None:
            return EventLaneWakePublishResult(
                success=False,
                wakeup=wakeup,
                reason="redis_unavailable",
            )
        raw_user_type = getattr(getattr(wakeup, "user", None), "user_type", None) or "registered"
        user_type = str(getattr(raw_user_type, "value", raw_user_type) or "registered").lower()
        queue_key = event_lane_wakeup_queue_key(
            tenant=self.tenant or str(getattr(getattr(wakeup, "actor", None), "tenant_id", "") or ""),
            project=self.project or str(getattr(getattr(wakeup, "actor", None), "project_id", "") or ""),
            user_type=user_type,
        )
        await self.redis.rpush(queue_key, json.dumps(wakeup.model_dump(mode="json"), ensure_ascii=False))
        return EventLaneWakePublishResult(
            success=True,
            wakeup=wakeup,
            reason="queued",
            stats={"queue_key": queue_key},
        )
