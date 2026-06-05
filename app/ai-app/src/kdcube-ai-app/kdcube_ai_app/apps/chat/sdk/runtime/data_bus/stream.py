# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DataBusMessage,
    DataBusResult,
    data_bus_group_name,
    data_bus_stream_key,
)

logger = logging.getLogger("kdcube.data_bus.stream")

DATA_BUS_STREAM_MAXLEN = max(128, int(os.getenv("DATA_BUS_STREAM_MAXLEN", "50000") or "50000"))
DATA_BUS_RESULT_STREAM_MAXLEN = max(128, int(os.getenv("DATA_BUS_RESULT_STREAM_MAXLEN", "10000") or "10000"))
DATA_BUS_DLQ_STREAM_MAXLEN = max(128, int(os.getenv("DATA_BUS_DLQ_STREAM_MAXLEN", "10000") or "10000"))
DATA_BUS_AUTOCLAIM_IDLE_MS = max(1000, int(os.getenv("DATA_BUS_AUTOCLAIM_IDLE_MS", "60000") or "60000"))


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value or "")


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_load(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        return value
    return json.loads(value)


@dataclass(frozen=True)
class DataBusClaim:
    stream_key: str
    stream_id: str
    message: DataBusMessage
    fields: dict[str, Any]
    consumer_name: str


@dataclass(frozen=True)
class DataBusPublishResult:
    message_id: str
    stream_key: str
    stream_id: str


class RedisDataBusStream:
    def __init__(
        self,
        redis: Any,
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        group_name: str | None = None,
        stream_maxlen: int = DATA_BUS_STREAM_MAXLEN,
        result_stream_maxlen: int = DATA_BUS_RESULT_STREAM_MAXLEN,
        dlq_stream_maxlen: int = DATA_BUS_DLQ_STREAM_MAXLEN,
    ) -> None:
        self.redis = redis
        self.tenant = tenant
        self.project = project
        self.bundle_id = bundle_id
        self.group_name = group_name or data_bus_group_name(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
        self.stream_maxlen = max(0, int(stream_maxlen or 0))
        self.result_stream_maxlen = max(0, int(result_stream_maxlen or 0))
        self.dlq_stream_maxlen = max(0, int(dlq_stream_maxlen or 0))

    @property
    def messages_key(self) -> str:
        return data_bus_stream_key(
            tenant=self.tenant,
            project=self.project,
            bundle_id=self.bundle_id,
            kind="messages",
        )

    @property
    def results_key(self) -> str:
        return data_bus_stream_key(
            tenant=self.tenant,
            project=self.project,
            bundle_id=self.bundle_id,
            kind="results",
        )

    @property
    def dlq_key(self) -> str:
        return data_bus_stream_key(
            tenant=self.tenant,
            project=self.project,
            bundle_id=self.bundle_id,
            kind="dlq",
        )

    async def publish(self, message: DataBusMessage) -> DataBusPublishResult:
        stream_id = await self._xadd(
            self.messages_key,
            {"json": message.to_json()},
            maxlen=self.stream_maxlen,
        )
        return DataBusPublishResult(
            message_id=message.message_id,
            stream_key=self.messages_key,
            stream_id=stream_id,
        )

    async def claim_next(
        self,
        *,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 1000,
        autoclaim_idle_ms: int = DATA_BUS_AUTOCLAIM_IDLE_MS,
    ) -> DataBusClaim | None:
        await self.ensure_group()
        raw_items = await self._xreadgroup(
            consumer_name=consumer_name,
            count=count,
            block_ms=block_ms,
        )
        if not raw_items:
            raw_items = await self._xautoclaim(
                consumer_name=consumer_name,
                min_idle_ms=autoclaim_idle_ms,
                count=count,
            )
        if not raw_items:
            return None
        stream_id, fields = raw_items[0]
        try:
            message = self.message_from_fields(fields)
        except Exception:
            logger.warning(
                "[data_bus.claim] Invalid Data Bus payload stream=%s id=%s",
                self.messages_key,
                stream_id,
                exc_info=True,
            )
            await self.write_dlq_raw(
                stream_id=stream_id,
                fields=fields,
                reason="invalid_payload",
            )
            await self.ack_stream_id(stream_id=stream_id)
            return None
        return DataBusClaim(
            stream_key=self.messages_key,
            stream_id=stream_id,
            message=message,
            fields=dict(fields or {}),
            consumer_name=consumer_name,
        )

    def message_from_fields(self, fields: Mapping[Any, Any]) -> DataBusMessage:
        data = {_decode(k): v for k, v in dict(fields or {}).items()}
        raw_json = data.get("json")
        if raw_json is None:
            raise ValueError("Data Bus stream record is missing json field")
        return DataBusMessage.from_json(raw_json)

    async def requeue(
        self,
        claim: DataBusClaim,
        *,
        reason: str,
        max_retries: int,
    ) -> bool:
        message = claim.message
        trace = dict(message.trace or {})
        retries = int(trace.get("retry_count") or 0) + 1
        if retries > max(0, int(max_retries or 0)):
            await self.write_dlq(
                message,
                reason=reason,
                details={"retry_count": retries, "source_stream_id": claim.stream_id},
            )
            await self.ack(claim)
            return False
        trace.update({
            "retry_count": retries,
            "retry_reason": reason,
            "source_stream_id": claim.stream_id,
        })
        retry_message = DataBusMessage(
            message_id=message.message_id,
            tenant=message.tenant,
            project=message.project,
            bundle_id=message.bundle_id,
            subject=message.subject,
            object_ref=message.object_ref,
            idempotency_key=message.idempotency_key,
            actor=dict(message.actor or {}),
            payload=dict(message.payload or {}),
            reply=dict(message.reply or {}) if message.reply is not None else None,
            trace=trace,
            created_at=message.created_at,
            schema=message.schema,
        )
        await self.publish(retry_message)
        await self.ack(claim)
        return True

    async def write_result(self, result: DataBusResult, *, stream_id: str | None = None) -> str:
        payload = result.to_dict()
        if stream_id:
            payload["source_stream_id"] = stream_id
        return await self._xadd(
            self.results_key,
            {"json": _json_dump(payload)},
            maxlen=self.result_stream_maxlen,
        )

    async def write_dlq(
        self,
        message: DataBusMessage,
        *,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        payload = {
            "schema": "kdcube.data_bus.dlq.v1",
            "reason": str(reason or "failed"),
            "details": dict(details or {}),
            "message": message.to_dict(),
        }
        return await self._xadd(
            self.dlq_key,
            {"json": _json_dump(payload)},
            maxlen=self.dlq_stream_maxlen,
        )

    async def write_dlq_raw(
        self,
        *,
        stream_id: str,
        fields: Mapping[Any, Any],
        reason: str,
    ) -> str:
        safe_fields = {_decode(k): _decode(v) for k, v in dict(fields or {}).items()}
        payload = {
            "schema": "kdcube.data_bus.dlq.v1",
            "reason": str(reason or "failed"),
            "stream_id": stream_id,
            "fields": safe_fields,
        }
        return await self._xadd(
            self.dlq_key,
            {"json": _json_dump(payload)},
            maxlen=self.dlq_stream_maxlen,
        )

    async def ack(self, claim: DataBusClaim) -> None:
        await self.ack_stream_id(stream_id=claim.stream_id)

    async def ack_stream_id(self, *, stream_id: str) -> None:
        stream_id = str(stream_id or "").strip()
        if not stream_id:
            return
        await self.ensure_group()
        try:
            await self.redis.xack(self.messages_key, self.group_name, stream_id)
        except Exception:
            logger.debug(
                "[data_bus.ack] Failed to ack stream=%s id=%s",
                self.messages_key,
                stream_id,
                exc_info=True,
            )

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(self.messages_key, self.group_name, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _xadd(self, stream_key: str, fields: dict[str, str], *, maxlen: int) -> str:
        try:
            stream_id = await self.redis.xadd(
                stream_key,
                fields,
                maxlen=maxlen or None,
                approximate=True,
            )
        except TypeError:
            stream_id = await self.redis.xadd(stream_key, fields)
        if isinstance(stream_id, bytes):
            stream_id = stream_id.decode("utf-8")
        return str(stream_id or "")

    async def _xreadgroup(
        self,
        *,
        consumer_name: str,
        count: int,
        block_ms: int,
    ) -> list[tuple[str, dict[Any, Any]]]:
        response = await self.redis.xreadgroup(
            self.group_name,
            consumer_name,
            {self.messages_key: ">"},
            count=max(1, int(count or 1)),
            block=max(0, int(block_ms or 0)),
        )
        return self._flatten_xread_response(response)

    async def _xautoclaim(
        self,
        *,
        consumer_name: str,
        min_idle_ms: int,
        count: int,
    ) -> list[tuple[str, dict[Any, Any]]]:
        claimer = getattr(self.redis, "xautoclaim", None)
        if not callable(claimer):
            return []
        response = await claimer(
            self.messages_key,
            self.group_name,
            consumer_name,
            max(1, int(min_idle_ms or 1)),
            "0-0",
            count=max(1, int(count or 1)),
        )
        if isinstance(response, (tuple, list)) and len(response) >= 2:
            return self._flatten_claim_items(response[1] or [])
        return []

    @staticmethod
    def _flatten_xread_response(response: Any) -> list[tuple[str, dict[Any, Any]]]:
        out: list[tuple[str, dict[Any, Any]]] = []
        for stream_entry in response or []:
            if not isinstance(stream_entry, (tuple, list)) or len(stream_entry) < 2:
                continue
            for item in stream_entry[1] or []:
                if not isinstance(item, (tuple, list)) or len(item) < 2:
                    continue
                stream_id = item[0].decode("utf-8") if isinstance(item[0], bytes) else str(item[0] or "")
                fields = item[1] if isinstance(item[1], dict) else {}
                out.append((stream_id, fields))
        return out

    @staticmethod
    def _flatten_claim_items(items: Any) -> list[tuple[str, dict[Any, Any]]]:
        out: list[tuple[str, dict[Any, Any]]] = []
        for item in items or []:
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue
            stream_id = item[0].decode("utf-8") if isinstance(item[0], bytes) else str(item[0] or "")
            fields = item[1] if isinstance(item[1], dict) else {}
            out.append((stream_id, fields))
        return out
