# SPDX-License-Identifier: MIT
"""Opt-in, bounded process-memory timing; no request-side diagnostic IO."""

from __future__ import annotations

import contextvars
import asyncio
import json
import logging
import math
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from kdcube_ai_app.apps.chat.sdk.config_scopes import DataBusTimingConfig


class Phase(str, Enum):
    CLAIM = "server.claim.start"
    READINESS_START = "server.readiness.start"
    READINESS_END = "server.readiness.end"
    AUTHORITY_START = "server.live_authority.start"
    AUTHORITY_END = "server.live_authority.end"
    LOCK_START = "server.lock.start"
    LOCK_END = "server.lock.end"
    INVOKE_START = "server.invoke.start"
    INVOKE_END = "server.invoke.end"
    PREPARATION_START = "server.preparation.start"
    PREPARATION_END = "server.preparation.end"
    HANDLER_START = "server.handler.start"
    HANDLER_END = "server.handler.end"
    REPLY_START = "server.reply.start"
    REPLY_END = "server.reply.end"
    FINISH = "server.claim.finish"


class Outcome(str, Enum):
    OK = "ok"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEFERRED = "deferred"
    REJECTED = "rejected"
    RETRY = "retry"
    NO_ROUTE = "no_route"
    ACCEPTED = "accepted"
    RESULT = "result"
    CONFLICT = "conflict"
    ERROR = "error"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Span:
    message_id: str
    attempt: int
    started: float


CURRENT: contextvars.ContextVar[Span | None] = contextvars.ContextVar("data_bus_timing", default=None)


class MemoryCapture:
    MAX_REQUESTS = 100
    MAX_RECORD_BYTES = 512
    MAX_BYTES = 256 * 1024

    def __init__(self):
        self.config = DataBusTimingConfig()
        self._expiry = 0.0
        self._deadline = 0.0
        self._lock = threading.Lock()
        self._records: deque[str] = deque()
        self.sampled_claims = 0
        self.buffer_bytes = 0
        self.admitted_bytes = 0
        self.dropped_records = 0
        self.dropped_bytes = 0

    def configure(self, config: DataBusTimingConfig) -> None:
        if config == self.config:
            return
        self.config = config
        self._expiry = datetime.fromisoformat(config.expires_at.replace("Z", "+00:00")).timestamp() if config.enabled else 0.0
        self._deadline = time.monotonic() + max(0.0, min(900, self._expiry - time.time()))

    def active(self) -> bool:
        return self.config.enabled and time.time() < self._expiry and time.monotonic() < self._deadline

    def begin(self, message_id: str, attempt: int = 0) -> Span | None:
        if not self.config.enabled:
            return None
        if not self.active():
            return None
        try:
            request_id = str(uuid.UUID(message_id))
            if type(attempt) is not int or not 0 <= attempt <= 1_000_000:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            self.dropped_records += 1
            return None
        if not self._lock.acquire(blocking=False):
            self.dropped_records += 1
            return None
        try:
            if self.sampled_claims >= self.MAX_REQUESTS:
                self.dropped_records += 1
                return None
            self.sampled_claims += 1
            return Span(request_id, attempt, time.monotonic())
        finally:
            self._lock.release()

    def record(self, span: Span | None, phase: Phase, outcome: Outcome = Outcome.OK) -> None:
        if span is None or not self.active():
            return
        if not self._lock.acquire(blocking=False):
            self.dropped_records += 1
            return
        try:
            if type(phase) is not Phase or type(outcome) is not Outcome or type(span) is not Span:
                self.dropped_records += 1
                return
            # Recheck the immutable span before serialization: no arbitrary
            # identifiers or metadata may pass even via an incorrect caller.
            if str(uuid.UUID(span.message_id)) != span.message_id or type(span.attempt) is not int or not 0 <= span.attempt <= 1_000_000 or type(span.started) not in (int, float) or not math.isfinite(span.started):
                self.dropped_records += 1
                return
            line = json.dumps({
                "schema": "data_bus.timing.v1",
                "phase": phase.value,
                "outcome": outcome.value,
                "message_id": span.message_id,
                "attempt": span.attempt,
                "utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                "elapsed_seconds": round(max(0.0, time.monotonic() - span.started), 6),
                "dropped_records": self.dropped_records,
                "dropped_bytes": self.dropped_bytes,
            }, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            # Reserve the existing logger formatter's prefix too.
            size = len(line.encode("utf-8")) + 128
            if size > self.MAX_RECORD_BYTES or self.admitted_bytes + size > self.MAX_BYTES:
                self.dropped_records += 1
                self.dropped_bytes += size
                return
            self._records.append(line)
            self.buffer_bytes += size
            self.admitted_bytes += size
        except Exception:
            self.dropped_records += 1
        finally:
            self._lock.release()

    def drain(self, *, max_records: int = 100) -> tuple[str, ...]:
        """Explicit local collection only, outside a captured claim context.

        This method does no IO. Logging returned lines may block; callers must
        arrange that outside request handling in a separately approved window.
        Drain never resets the process sample/cumulative-byte budget.
        """
        if CURRENT.get() is not None:
            raise RuntimeError("Timing drain is prohibited in request handling")
        if type(max_records) is not int or not 1 <= max_records <= 1000:
            raise ValueError("max_records must be between 1 and 1000")
        with self._lock:
            rows = tuple(self._records.popleft() for _ in range(min(max_records, len(self._records))))
            self.buffer_bytes -= sum(len(row.encode("utf-8")) + 128 for row in rows)
        return rows


CAPTURE = MemoryCapture()


def observe(phase: Phase, outcome: Outcome = Outcome.OK, *, message_id: str | None = None) -> None:
    span = CURRENT.get()
    if span is None or (message_id is not None and span.message_id != message_id):
        return
    try:
        CAPTURE.record(span, phase, outcome)
    except (Exception, asyncio.CancelledError):
        # An observation must never change an application result.
        pass


def drain_to_logger(*, max_records: int = 100) -> int:
    """Explicitly drain to existing synchronous logging, which may block."""
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_bundle_id, get_current_request_context

    if get_current_request_context() is not None or get_current_bundle_id():
        raise RuntimeError("Timing drain requires an unbound, outside-request context")
    rows = CAPTURE.drain(max_records=max_records)
    logger = logging.getLogger("kdcube.data_bus.timing")
    for row in rows:
        logger.info("%s", row)
    return len(rows)
