# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional


_STATE_TTL_SECONDS = 7 * 24 * 3600
_LOCK_TTL_SECONDS = 10


def utc_timestamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _decode(raw: Any) -> Any:
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return raw


def _parse_json(raw: Any) -> dict[str, Any]:
    raw = _decode(raw)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def normalize_timestamp(value: Any) -> str:
    if value is None:
        return ""
    text = str(value or "").strip()
    if not text:
        return ""
    return text


def _timestamp_epoch(value: Any) -> float:
    text = normalize_timestamp(value)
    if not text:
        return 0.0
    try:
        return float(text)
    except Exception:
        pass
    try:
        parse_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = _dt.datetime.fromisoformat(parse_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return float(dt.timestamp())
    except Exception:
        return 0.0


def later_timestamp(left: Any, right: Any) -> str:
    left_text = normalize_timestamp(left)
    right_text = normalize_timestamp(right)
    if not left_text:
        return right_text
    if not right_text:
        return left_text
    if _timestamp_epoch(right_text) >= _timestamp_epoch(left_text):
        return right_text
    return left_text


def timestamp_lte(left: Any, right: Any) -> bool:
    left_text = normalize_timestamp(left)
    right_text = normalize_timestamp(right)
    if not left_text or not right_text:
        return False
    return _timestamp_epoch(left_text) <= _timestamp_epoch(right_text)


def timestamp_lt(left: Any, right: Any) -> bool:
    left_text = normalize_timestamp(left)
    right_text = normalize_timestamp(right)
    if not right_text:
        return False
    if not left_text:
        return True
    return _timestamp_epoch(left_text) < _timestamp_epoch(right_text)


def timestamp_age_ms(*, now: Any, since: Any) -> float:
    now_text = normalize_timestamp(now)
    since_text = normalize_timestamp(since)
    if not now_text or not since_text:
        return float("inf")
    return max(0.0, (_timestamp_epoch(now_text) - _timestamp_epoch(since_text)) * 1000.0)


def timestamp_is_fresh(*, now: Any, since: Any, ttl_ms: int) -> bool:
    ttl = max(0, int(ttl_ms or 0))
    if ttl <= 0:
        return False
    return timestamp_age_ms(now=now, since=since) <= ttl


def event_timestamp(event: Any) -> str:
    payload = getattr(event, "payload", None)
    if isinstance(payload, dict):
        accepted = payload.get("event")
        if isinstance(accepted, dict):
            ts = normalize_timestamp(accepted.get("timestamp") or accepted.get("ts"))
            if ts:
                return ts
    task_payload = getattr(event, "task_payload", None)
    if isinstance(task_payload, dict):
        request = task_payload.get("request")
        if isinstance(request, dict):
            for item in request.get("external_events") or []:
                if isinstance(item, dict):
                    ts = normalize_timestamp(item.get("timestamp") or item.get("ts"))
                    if ts:
                        return ts
    return normalize_timestamp(getattr(event, "created_at", None))


def event_is_reactive(event: Any) -> bool:
    payload = getattr(event, "payload", None)
    if isinstance(payload, dict):
        accepted = payload.get("event")
        if isinstance(accepted, dict) and accepted.get("reactive") is not None:
            return bool(accepted.get("reactive"))
    task_payload = getattr(event, "task_payload", None)
    if isinstance(task_payload, dict):
        request = task_payload.get("request")
        if isinstance(request, dict):
            for item in request.get("external_events") or []:
                if isinstance(item, dict) and item.get("reactive") is not None:
                    return bool(item.get("reactive"))
    return bool(getattr(event, "is_continuation", False))


@dataclass
class EventLaneState:
    handler_turn_id: str = ""
    handler_status: str = ""
    handler_status_at: str = ""
    last_processed_reactive_event_timestamp: str = ""
    last_processed_event_timestamp: str = ""
    consumer_status: str = ""
    consumer_status_at: str = ""

    @classmethod
    def from_any(cls, raw: Any) -> "EventLaneState":
        data = _parse_json(raw)
        if not data:
            return cls()
        fields = {name: data.get(name) for name in cls.__dataclass_fields__.keys()}
        return cls(**fields)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_present(self) -> bool:
        return self.handler_status in {"open", "closed"}

    def is_open_for(self, turn_id: str) -> bool:
        return self.handler_status == "open" and bool(turn_id) and self.handler_turn_id == str(turn_id or "")

    def event_was_processed(self, event: Any) -> bool:
        ts = event_timestamp(event)
        if event_is_reactive(event):
            return timestamp_lte(ts, self.last_processed_reactive_event_timestamp)
        return timestamp_lte(ts, self.last_processed_event_timestamp)


class RedisEventLaneStateTable:
    """Redis-backed event-lane coordination record.

    The logical table row is stored as one JSON value under ``state_key``. The
    short-lived lock key serializes updates between ingress, proc, and runtime
    readers.
    """

    def __init__(
        self,
        *,
        redis: Any,
        state_key: str,
        lock_key: Optional[str] = None,
        ttl_seconds: int = _STATE_TTL_SECONDS,
        lock_ttl_seconds: int = _LOCK_TTL_SECONDS,
    ) -> None:
        self.redis = redis
        self.state_key = str(state_key or "")
        self.lock_key = str(lock_key or f"{self.state_key}:lock")
        self.ttl_seconds = max(1, int(ttl_seconds or _STATE_TTL_SECONDS))
        self.lock_ttl_seconds = max(1, int(lock_ttl_seconds or _LOCK_TTL_SECONDS))

    @classmethod
    def for_source(cls, source: Any) -> "RedisEventLaneStateTable":
        return cls(
            redis=getattr(source, "redis"),
            state_key=f"{getattr(source, 'log_key')}:state",
            lock_key=f"{getattr(source, 'log_key')}:state:lock",
        )

    async def get(self) -> EventLaneState:
        raw = await self.redis.get(self.state_key)
        return EventLaneState.from_any(raw)

    async def put(self, state: EventLaneState) -> EventLaneState:
        payload = json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True)
        setter = getattr(self.redis, "set", None)
        if callable(setter):
            try:
                await setter(self.state_key, payload, ex=self.ttl_seconds)
                return state
            except TypeError:
                await setter(self.state_key, payload)
                return state
        await self.redis.setex(self.state_key, self.ttl_seconds, payload)
        return state

    @contextlib.asynccontextmanager
    async def lock(self, *, timeout_seconds: float = 2.0):
        token = f"lock_{uuid.uuid4().hex}"
        deadline = time.monotonic() + max(0.05, float(timeout_seconds or 2.0))
        acquired = False
        while time.monotonic() < deadline:
            setter = getattr(self.redis, "set", None)
            if callable(setter):
                try:
                    acquired = bool(await setter(self.lock_key, token, nx=True, ex=self.lock_ttl_seconds))
                except TypeError:
                    if await self.redis.get(self.lock_key) is None:
                        await setter(self.lock_key, token)
                        acquired = True
                if acquired:
                    break
            await asyncio.sleep(0.01)
        if not acquired:
            raise TimeoutError(f"event lane state lock timed out: {self.lock_key}")
        renew_task = asyncio.create_task(self._renew_lock(token), name=f"event-lane-lock-renew:{self.lock_key}")
        try:
            yield token
        finally:
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass
            try:
                await self._release_lock(token)
            except Exception:
                pass

    async def update(self, mutator: Callable[[EventLaneState], EventLaneState | None]) -> EventLaneState:
        async with self.lock():
            state = await self.get()
            new_state = mutator(state) or state
            await self.put(new_state)
            return new_state

    async def _renew_lock(self, token: str) -> None:
        interval = max(0.1, min(1.0, float(self.lock_ttl_seconds) / 3.0))
        while True:
            await asyncio.sleep(interval)
            renewed = await self._renew_lock_once(token)
            if not renewed:
                return

    async def _renew_lock_once(self, token: str) -> bool:
        evaluator = getattr(self.redis, "eval", None)
        if callable(evaluator):
            result = await evaluator(
                """
                if redis.call('GET', KEYS[1]) == ARGV[1] then
                    return redis.call('EXPIRE', KEYS[1], ARGV[2])
                end
                return 0
                """,
                1,
                self.lock_key,
                token,
                str(self.lock_ttl_seconds),
            )
            return bool(result)
        current = _decode(await self.redis.get(self.lock_key))
        if str(current or "") != token:
            return False
        expirer = getattr(self.redis, "expire", None)
        if callable(expirer):
            return bool(await expirer(self.lock_key, self.lock_ttl_seconds))
        return True

    async def _release_lock(self, token: str) -> bool:
        evaluator = getattr(self.redis, "eval", None)
        if callable(evaluator):
            result = await evaluator(
                """
                if redis.call('GET', KEYS[1]) == ARGV[1] then
                    return redis.call('DEL', KEYS[1])
                end
                return 0
                """,
                1,
                self.lock_key,
                token,
            )
            return bool(result)
        current = _decode(await self.redis.get(self.lock_key))
        if str(current or "") == token:
            await self.redis.delete(self.lock_key)
            return True
        return False
