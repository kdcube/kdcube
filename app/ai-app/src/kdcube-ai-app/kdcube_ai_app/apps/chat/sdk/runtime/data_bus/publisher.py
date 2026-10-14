# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import uuid
import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import (
    RESULT_BODY_SCHEMA,
    DataBusPublishResult,
    DataBusResultBodyUnavailable,
    DataBusResultWaitTooLong,
    RESULT_WAIT_MAX_MS,
    RedisDataBusStream,
    result_body_key,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DataBusMessage,
    ensure_json_object,
    ensure_json_serializable,
    timestamp_message_id,
    utc_now_iso,
)
from kdcube_ai_app.infra.redis.client import get_async_redis_client


@dataclass(frozen=True)
class DataBusPublishAck:
    message_id: str
    stream_key: str
    stream_id: str
    subject: str
    object_ref: str | None


class DataBusPublisher:
    """Producer-side facade for durable bundle-scoped Data Bus messages.

    This is intentionally separate from ``ChatCommunicator.service_event``.
    Service events are live UI/relay messages; Data Bus publishes are durable
    messages consumed by ``@data_bus_handler`` workers.
    """

    def __init__(
        self,
        *,
        redis: Any | None = None,
        redis_url: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        bundle_id: str | None = None,
        actor_provider: Callable[[], Mapping[str, Any]] | None = None,
        reply_provider: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self._redis = redis
        self._redis_url = redis_url
        self._tenant = tenant
        self._project = project
        self._bundle_id = bundle_id
        self._actor_provider = actor_provider
        self._reply_provider = reply_provider

    def _redis_client(self) -> Any:
        if self._redis is not None:
            return self._redis
        redis_url = self._redis_url or get_settings().REDIS_URL
        self._redis = get_async_redis_client(redis_url)
        return self._redis

    def _default_actor(self) -> dict[str, Any]:
        if self._actor_provider is None:
            return {}
        value = self._actor_provider()
        return dict(value or {}) if isinstance(value, Mapping) else {}

    def _default_reply(self) -> dict[str, Any]:
        if self._reply_provider is None:
            return {}
        value = self._reply_provider()
        return dict(value or {}) if isinstance(value, Mapping) else {}

    async def publish(
        self,
        *,
        subject: str,
        payload: Mapping[str, Any] | None = None,
        bundle_id: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        object_ref: str | None = None,
        idempotency_key: str | None = None,
        actor: Mapping[str, Any] | None = None,
        reply: Mapping[str, Any] | bool | None = None,
        trace: Mapping[str, Any] | None = None,
        message_id: str | None = None,
    ) -> DataBusPublishAck:
        tenant_value = str(tenant or self._tenant or "").strip()
        project_value = str(project or self._project or "").strip()
        bundle_value = str(bundle_id or self._bundle_id or "").strip()
        if not tenant_value:
            raise ValueError("Data Bus publish requires tenant")
        if not project_value:
            raise ValueError("Data Bus publish requires project")
        if not bundle_value:
            raise ValueError("Data Bus publish requires bundle_id")

        payload_obj = ensure_json_object(payload, field_name="payload")
        ensure_json_serializable(payload_obj, field_name="payload")
        trace_obj = ensure_json_object(trace, field_name="trace")
        trace_obj.setdefault("request_id", str(uuid.uuid4()))

        if reply is True:
            reply_obj = self._default_reply()
        elif isinstance(reply, Mapping):
            reply_obj = dict(reply)
        else:
            reply_obj = None

        actor_obj = dict(actor or self._default_actor())
        message = DataBusMessage(
            message_id=str(message_id or timestamp_message_id()),
            tenant=tenant_value,
            project=project_value,
            bundle_id=bundle_value,
            subject=subject,
            object_ref=str(object_ref).strip() if object_ref else None,
            idempotency_key=str(idempotency_key).strip() if idempotency_key else None,
            actor=actor_obj,
            payload=payload_obj,
            reply=reply_obj,
            trace=trace_obj,
            created_at=utc_now_iso(),
        )
        stream = RedisDataBusStream(
            self._redis_client(),
            tenant=tenant_value,
            project=project_value,
            bundle_id=bundle_value,
        )
        published: DataBusPublishResult = await stream.publish(message)
        return DataBusPublishAck(
            message_id=published.message_id,
            stream_key=published.stream_key,
            stream_id=published.stream_id,
            subject=message.subject,
            object_ref=message.object_ref,
        )

    async def publish_and_wait(
        self,
        *,
        subject: str,
        payload: Mapping[str, Any] | None = None,
        bundle_id: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        object_ref: str | None = None,
        idempotency_key: str | None = None,
        actor: Mapping[str, Any] | None = None,
        reply: Mapping[str, Any] | bool | None = None,
        trace: Mapping[str, Any] | None = None,
        message_id: str | None = None,
        timeout_ms: int = 20000,
        poll_interval_ms: int = 100,
        scan_count: int = 200,
    ) -> dict[str, Any]:
        if int(timeout_ms) > RESULT_WAIT_MAX_MS:
            raise DataBusResultWaitTooLong(
                f"timeout_ms {int(timeout_ms)} exceeds the {RESULT_WAIT_MAX_MS} ms a result body is kept")
        ack = await self.publish(
            subject=subject,
            payload=payload,
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            object_ref=object_ref,
            idempotency_key=idempotency_key,
            actor=actor,
            reply=reply,
            # W827 S2d: the worker keeps a large result's body at least as long as this caller waits for it.
            trace={**dict(trace or {}),
                   "result_wait_ms": max(0, int(timeout_ms))},
            message_id=message_id,
        )
        stream = RedisDataBusStream(
            self._redis_client(),
            tenant=str(tenant or self._tenant or "").strip(),
            project=str(project or self._project or "").strip(),
            bundle_id=str(bundle_id or self._bundle_id or "").strip(),
        )
        deadline = asyncio.get_running_loop().time() + max(0.1, timeout_ms / 1000.0)
        interval = max(0.025, poll_interval_ms / 1000.0)
        while True:
            result = await self._find_result(stream.results_key, ack.message_id, count=scan_count)
            if result is not None:
                return result
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for Data Bus result subject={ack.subject} message_id={ack.message_id}"
                )
            await asyncio.sleep(interval)

    async def _find_result(self, results_key: str, message_id: str, *, count: int) -> dict[str, Any] | None:
        raw_items = await self._redis_client().xrevrange(results_key, count=max(1, int(count or 1)))
        for _stream_id, fields in raw_items or []:
            raw_json = None
            for key, value in dict(fields or {}).items():
                key_text = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                if key_text == "json":
                    raw_json = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                    break
            if not raw_json:
                continue
            try:
                payload = json.loads(raw_json)
            except Exception:
                continue
            if isinstance(payload, Mapping) and str(payload.get("message_id") or "") == message_id:
                found = dict(payload)
                if "data_ref" in found:
                    found["data"] = await self._result_body(results_key, message_id, found.pop("data_ref"))
                return found
        return None

    async def _result_body(self, results_key: str, message_id: str, ref: Any) -> Any:
        """The whole original ``data`` of a large result, verified against its reference; never empty data."""
        if not isinstance(ref, Mapping) or ref.get("schema") != RESULT_BODY_SCHEMA:
            raise DataBusResultBodyUnavailable("unknown result body reference")
        suffix = str(ref.get("key_suffix") or "")
        digest = str(ref.get("sha256") or "")
        # Only this caller's own result: the suffix must name this message and the digest it carries.
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or suffix != f"{message_id}:{digest[:16]}":
            raise DataBusResultBodyUnavailable("result body reference does not belong to this message")
        body = await self._redis_client().get(result_body_key(results_key, suffix))
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not isinstance(body, bytes):
            raise DataBusResultBodyUnavailable("result body expired or missing")
        if len(body) != int(ref.get("bytes") or -1) or hashlib.sha256(body).hexdigest() != digest:
            raise DataBusResultBodyUnavailable("result body does not match its reference")
        return json.loads(body.decode("utf-8"))
