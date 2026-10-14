# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, replace
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.actor_compaction import compact_card_actor
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DataBusMessage,
    DataBusResult,
    data_bus_group_name,
    data_bus_stream_key,
)

logger = logging.getLogger("kdcube.data_bus.stream")

DATA_BUS_AUTOCLAIM_IDLE_MS = max(1000, int(os.getenv("DATA_BUS_AUTOCLAIM_IDLE_MS", "60000") or "60000"))

# Results and DLQ have no consumer group: their existing count caps stay (a changed window is a policy decision).
DATA_BUS_RESULT_STREAM_MAXLEN = max(128, int(os.getenv("DATA_BUS_RESULT_STREAM_MAXLEN", "10000") or "10000"))
DATA_BUS_DLQ_STREAM_MAXLEN = max(128, int(os.getenv("DATA_BUS_DLQ_STREAM_MAXLEN", "10000") or "10000"))

# W827 S2d: result data above this many UTF-8 bytes is stored once under its own key and the results-stream entry
# keeps a digest- and size-bound reference (live: p50 574 B, p90 158 KB). The body lives at least
# DATA_BUS_RESULT_BODY_TTL_SECONDS, and at least the polling caller's declared wait plus a margin, capped at a day.
DATA_BUS_RESULT_INLINE_BYTES = max(1024, int(os.getenv("DATA_BUS_RESULT_INLINE_BYTES", "16384") or "16384"))
DATA_BUS_RESULT_BODY_TTL_SECONDS = max(60, int(os.getenv("DATA_BUS_RESULT_BODY_TTL_SECONDS", "3600") or "3600"))
RESULT_BODY_WAIT_MARGIN_SECONDS = 300
RESULT_BODY_TTL_CAP_SECONDS = 86400
RESULT_BODY_SCHEMA = "kdcube.data_bus.result_body.v1"


class DataBusResultBodyUnavailable(RuntimeError):
    """A result's stored body is missing or does not match its reference: the outcome is unknown, never empty.

    Retryable for reads; a mutation is not re-sent automatically because of it (its durable receipt, where the
    application keeps one, is the authority).
    """

    code = "data_bus_result_body_unavailable"
    outcome_unknown = True


def result_body_ttl_seconds(wait_ms: Any = None) -> int:
    """The body's lifetime: the default, or the caller's declared wait plus a margin, capped at one day."""
    ttl = DATA_BUS_RESULT_BODY_TTL_SECONDS
    try:
        wait_seconds = int(wait_ms) // 1000 if wait_ms is not None else 0
    except (TypeError, ValueError):
        wait_seconds = 0
    if wait_seconds > 0:
        ttl = max(ttl, wait_seconds + RESULT_BODY_WAIT_MARGIN_SECONDS)
    return min(ttl, RESULT_BODY_TTL_CAP_SECONDS)


def result_body_key(results_key: str, key_suffix: str) -> str:
    """The body key sits next to its own results stream: same tenant, project and bundle scope."""
    prefix = results_key[: -len(":results")] if results_key.endswith(":results") else results_key
    return f"{prefix}:result-body:{key_suffix}"

# W827: the message stream is bounded by age, never by count on insert. A count cap (XADD MAXLEN) removed the
# oldest entries whether or not every consumer group had read and acknowledged them, so a pending message lost its
# body while still listed as pending; when the client lacked the argument the cap silently disappeared instead.
# Messages keep the six-hour transport window of the data-bus README; an entry older than it is removed only once
# every group has acknowledged it.
DATA_BUS_MESSAGE_RETENTION_SECONDS = max(60, int(os.getenv("DATA_BUS_MESSAGE_RETENTION_SECONDS", "21600") or "21600"))
# One trim removes at most this many entries (XTRIM ... LIMIT), so no single call blocks Redis for long.
DATA_BUS_TRIM_LIMIT = max(1, int(os.getenv("DATA_BUS_TRIM_LIMIT", "1000") or "1000"))
# A process trims one stream at most this often; the trim follows acknowledgements and appends, not a timer.
DATA_BUS_TRIM_INTERVAL_SECONDS = max(0.0, float(os.getenv("DATA_BUS_TRIM_INTERVAL_SECONDS", "5") or "5"))

if os.getenv("DATA_BUS_STREAM_MAXLEN"):
    logger.warning("[data_bus.retention] DATA_BUS_STREAM_MAXLEN is no longer applied: the message stream is bounded "
                   "by DATA_BUS_MESSAGE_RETENTION_SECONDS over acknowledged entries, never by count")

# Atomic: the age boundary comes from the server clock and the group state is read in the same call, so no
# group can be created, read or acknowledged between the check and the trim.
#   KEYS[1]  stream
#   ARGV[1]  retention in milliseconds
#   ARGV[2]  per-call entry limit
# Every consumer group must have acknowledged an entry; a stream with no group is kept. The boundary is computed
# here on every Redis version: XTRIM's own ACKED option removed nothing with "~ ... LIMIT" on Redis 8.4.0
# (Mint, W827 review), and LIMIT requires "~".
# Returns the number of entries removed, or -1 when the stream is kept because no group is known.
_TRIM_SCRIPT = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 0 then return 0 end
local now = redis.call('TIME')
local age_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000) - tonumber(ARGV[1])
if age_ms <= 0 then return 0 end
local boundary = tostring(age_ms) .. '-0'
local limit = ARGV[2]
local groups = redis.call('XINFO', 'GROUPS', key)
if #groups == 0 then return -1 end
local function before(a, b)
  local ams, aseq = string.match(a, '^(%d+)-(%d+)$')
  local bms, bseq = string.match(b, '^(%d+)-(%d+)$')
  ams, aseq, bms, bseq = tonumber(ams), tonumber(aseq), tonumber(bms), tonumber(bseq)
  return ams < bms or (ams == bms and aseq < bseq)
end
for _, group in ipairs(groups) do
  local info = {}
  for i = 1, #group, 2 do info[group[i]] = group[i + 1] end
  local delivered = info['last-delivered-id']
  if type(delivered) ~= 'string' or not string.match(delivered, '^%d+-%d+$') then return -1 end
  -- Entries after last-delivered-id are unread by this group: keep them (and the last delivered one).
  if before(delivered, boundary) then boundary = delivered end
  if tonumber(info['pending'] or 0) > 0 then
    local summary = redis.call('XPENDING', key, info['name'])
    local oldest = summary[2]
    if type(oldest) ~= 'string' then return -1 end
    if before(oldest, boundary) then boundary = oldest end
  end
end
return redis.call('XTRIM', key, 'MINID', '~', boundary, 'LIMIT', limit)
"""

_LAST_TRIM: dict[tuple[int, str], float] = {}
# Groups this process has created or found, so publish checks a stream's group once, not on every message.
_GROUP_READY: set[tuple[int, str, str]] = set()


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
        result_inline_bytes: int = DATA_BUS_RESULT_INLINE_BYTES,
        message_retention_seconds: int = DATA_BUS_MESSAGE_RETENTION_SECONDS,
        result_stream_maxlen: int = DATA_BUS_RESULT_STREAM_MAXLEN,
        dlq_stream_maxlen: int = DATA_BUS_DLQ_STREAM_MAXLEN,
        trim_limit: int = DATA_BUS_TRIM_LIMIT,
        trim_interval_seconds: float = DATA_BUS_TRIM_INTERVAL_SECONDS,
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
        self.message_retention_ms = max(1, int(message_retention_seconds)) * 1000
        self.result_stream_maxlen = max(0, int(result_stream_maxlen or 0))
        self.result_inline_bytes = max(1, int(result_inline_bytes))
        self.dlq_stream_maxlen = max(0, int(dlq_stream_maxlen or 0))
        self.trim_limit = max(1, int(trim_limit))
        self.trim_interval_seconds = max(0.0, float(trim_interval_seconds))

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
        # The handler group exists before the first entry, so retention always has a group that must read and
        # acknowledge it; a stream without a group is never trimmed.
        marker = (id(self.redis), self.messages_key, self.group_name)
        if marker not in _GROUP_READY:
            await self.ensure_group()
            _GROUP_READY.add(marker)
        # W827 S2c: a Card-bound actor's Card-rebuilt copies are not stored (the worker re-reads the Card).
        if message.actor:
            message = replace(message, actor=compact_card_actor(message.actor))
        stream_id = await self._xadd(self.messages_key, {"json": message.to_json()})
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

    async def write_result(self, result: DataBusResult, *, stream_id: str | None = None, wait_ms: Any = None) -> str:
        payload = result.to_dict()
        if stream_id:
            payload["source_stream_id"] = stream_id
        data = payload.get("data")
        if data:
            body = _json_dump(data).encode("utf-8")
            if len(body) > self.result_inline_bytes:
                digest = hashlib.sha256(body).hexdigest()
                key_suffix = f"{result.message_id}:{digest[:16]}"
                try:
                    # Immutable: the key names the content digest, so a retry writes nothing new.
                    await self.redis.set(result_body_key(self.results_key, key_suffix), body, nx=True,
                                         ex=result_body_ttl_seconds(wait_ms))
                except Exception:
                    # Fail toward lossless: without its body the reference would be useless, so keep data inline.
                    logger.warning("[data_bus.result] body write failed; result kept inline message_id=%s",
                                   result.message_id, exc_info=True)
                else:
                    payload["data"] = {}
                    payload["data_ref"] = {"schema": RESULT_BODY_SCHEMA, "key_suffix": key_suffix,
                                           "sha256": digest, "bytes": len(body)}
        return await self._xadd(self.results_key, {"json": _json_dump(payload)}, maxlen=self.result_stream_maxlen)

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
        return await self._xadd(self.dlq_key, {"json": _json_dump(payload)}, maxlen=self.dlq_stream_maxlen)

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
        return await self._xadd(self.dlq_key, {"json": _json_dump(payload)}, maxlen=self.dlq_stream_maxlen)

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
            return
        await self.trim()

    async def trim(self, *, force: bool = False) -> int | None:
        """Remove message entries older than the retention window that every consumer group acknowledged.

        Returns the number removed, -1 when the stream is kept because its group state is unknown, or None when
        this process trimmed the stream too recently or Redis refused. A failed trim never fails the caller.
        """
        clock = time.monotonic()
        stream_key = self.messages_key
        marker = (id(self.redis), stream_key)
        if not force and clock - _LAST_TRIM.get(marker, float("-inf")) < self.trim_interval_seconds:
            return None
        _LAST_TRIM[marker] = clock
        evaluate = getattr(self.redis, "eval", None)
        if not callable(evaluate):
            return None
        try:
            removed = await evaluate(_TRIM_SCRIPT, 1, stream_key, self.message_retention_ms, self.trim_limit)
        except Exception:
            logger.warning("[data_bus.retention] trim refused stream=%s", stream_key, exc_info=True)
            return None
        return int(removed or 0)

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(self.messages_key, self.group_name, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _xadd(self, stream_key: str, fields: dict[str, str], *, maxlen: int = 0) -> str:
        # The message stream passes no maxlen: a count cap would drop unread or pending entries (W827); its
        # retention is trim(). Results and DLQ keep their existing approximate count caps.
        if not maxlen:
            stream_id = await self.redis.xadd(stream_key, fields)
        else:
            try:
                stream_id = await self.redis.xadd(stream_key, fields, maxlen=maxlen, approximate=True)
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
