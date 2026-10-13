from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import uuid

import pytest

from kdcube_ai_app.apps.chat.sdk.config_scopes import DataBusTimingConfig
from kdcube_ai_app.apps.chat.sdk import config_scopes
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import timing, worker as workers
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import DataBusClaim
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusHandlerSpec, DataBusMessage, DataBusReply


def enabled():
    return DataBusTimingConfig(enabled=True, expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())


@pytest.fixture
def capture(monkeypatch):
    value = timing.MemoryCapture()
    value.configure(enabled())
    monkeypatch.setattr(timing, "CAPTURE", value)
    monkeypatch.setattr(workers, "CAPTURE", value)
    return value


def message():
    return DataBusMessage(message_id=str(uuid.uuid4()), tenant="tenant-marker", project="project-marker", bundle_id="bundle-marker", subject="subject-marker", object_ref="object-marker", payload={"secret": "payload-marker"}, actor={"user_id": "actor-marker"}, reply={"session_id": "session-marker"})


def test_default_off_performs_no_clock_or_record_io(monkeypatch):
    value = timing.MemoryCapture()
    monkeypatch.setattr(timing.time, "time", lambda: pytest.fail("disabled clock"))
    assert value.begin(str(uuid.uuid4())) is None
    value.record(None, timing.Phase.CLAIM)
    assert not value.drain()


@pytest.mark.parametrize("fields", [{"enabled": "yes"}, {"enabled": True}, {"extra": "marker"}, {"enabled": True, "expires_at": "2030-01-01T00:00:00Z"}, {"enabled": True, "expires_at": "2026-01-01T00:00:00"}])
def test_typed_gate_and_expiry_fail_closed(fields):
    with pytest.raises(ValueError):
        DataBusTimingConfig.model_validate(fields)


def test_supported_descriptor_reader_ignores_hidden_environment(monkeypatch):
    path = "platform.services.proc.service.data_bus_timing"
    reads = []

    def read(requested):
        reads.append(requested)
        return {"enabled": False}

    monkeypatch.setenv("DATA_BUS_TIMING_ENABLED", "true")
    monkeypatch.setattr(config_scopes, "_load_assembly_plain", read)
    assert not DataBusTimingConfig.from_descriptor(path).enabled
    assert reads == [path]
    monkeypatch.setattr(config_scopes, "_load_assembly_plain", lambda _: False)
    with pytest.raises(ValueError):
        DataBusTimingConfig.from_descriptor(path)


def test_allowlist_limits_drops_and_process_budget(capture):
    span = capture.begin(str(uuid.uuid4()), 2)
    capture.record(span, timing.Phase.CLAIM)
    row = json.loads(capture.drain()[0])
    assert set(row) == {"schema", "phase", "outcome", "message_id", "attempt", "utc", "elapsed_seconds", "dropped_records", "dropped_bytes"}
    assert row["attempt"] == 2
    for _ in range(1000):
        capture.record(span, timing.Phase.HANDLER_START)
    assert capture.buffer_bytes <= 256 * 1024
    assert capture.admitted_bytes <= 256 * 1024
    assert capture.dropped_records > 0
    assert all(len(row.encode()) + 128 <= 512 for row in capture.drain(max_records=1000))
    for _ in range(120):
        capture.begin(str(uuid.uuid4()))
    assert capture.sampled_claims == 100
    capture.configure(DataBusTimingConfig())
    capture.configure(enabled())
    assert capture.begin(str(uuid.uuid4())) is None


@pytest.mark.parametrize("field,value", [("message_id", "marker"), ("attempt", "marker"), ("started", float("nan"))])
def test_negative_span_never_serializes(capture, field, value):
    span = replace(capture.begin(str(uuid.uuid4())), **{field: value})
    capture.record(span, timing.Phase.CLAIM)
    capture.record(span, "arbitrary-marker")
    assert not capture.drain()
    assert capture.dropped_records == 2


def test_capture_busy_or_expired_never_waits(capture, monkeypatch):
    span = capture.begin(str(uuid.uuid4()))
    with capture._lock:
        capture.record(span, timing.Phase.CLAIM)
        assert capture.begin(str(uuid.uuid4())) is None
    assert capture.dropped_records == 2
    monkeypatch.setattr(timing.time, "monotonic", lambda: capture._deadline + 1)
    capture.record(span, timing.Phase.CLAIM)
    assert capture.begin(str(uuid.uuid4())) is None
    assert not capture.drain()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,outcome", [("accepted", "accepted"), ("ok", "result"), ("conflict", "conflict"), ("error", "error"), ("event", "other")])
async def test_actual_reply_boundary_and_redaction(capture, method, outcome, monkeypatch):
    msg = message()
    span = capture.begin(msg.message_id)
    token = timing.CURRENT.set(span)

    class Comm:
        async def service_event(self, **kwargs):
            assert len(capture._records) == 1  # start before actual awaited delivery
            await asyncio.sleep(0)

    def no_log(*args, **kwargs):
        pytest.fail("request-side diagnostic logger IO")

    # Replace this module's diagnostic facade, not global logging used by
    # pytest and unrelated service code.
    monkeypatch.setattr(timing, "logging", SimpleNamespace(getLogger=no_log))
    try:
        reply = DataBusReply(message=msg, comm=Comm())
        if method == "error":
            await reply.error("code-marker", "error-marker", {"secret": "details-marker"})
        elif method == "event":
            await reply.event("event-marker", {"secret": "event-payload-marker"}, title="title-marker")
        else:
            await getattr(reply, method)({"secret": "reply-marker"})
        assert reply.sent_count == 1
        with pytest.raises(RuntimeError):
            capture.drain()
    finally:
        timing.CURRENT.reset(token)
    rows = capture.drain()
    assert "marker" not in "".join(rows)
    assert [json.loads(row)["phase"] for row in rows] == ["server.reply.start", "server.reply.end"]
    assert json.loads(rows[-1])["outcome"] == outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,outcome", [(RuntimeError("raw-error-marker"), "failed"), (asyncio.CancelledError(), "cancelled"), (None, "no_route")])
async def test_reply_failure_no_route_and_cancellation_unchanged(capture, failure, outcome):
    msg = message()
    token = timing.CURRENT.set(capture.begin(msg.message_id))

    class Comm:
        def service_event(self, **kwargs):
            raise failure

    reply = DataBusReply(message=msg, comm=Comm() if failure is not None else None)
    try:
        if failure is not None:
            with pytest.raises(type(failure)):
                await reply.ok({})
        else:
            await reply.ok({})
        assert reply.sent_count == 0
    finally:
        timing.CURRENT.reset(token)
    rows = capture.drain()
    assert "marker" not in "".join(rows)
    assert json.loads(rows[-1])["outcome"] == outcome


@pytest.mark.asyncio
async def test_uuid_normalization_is_diagnostic_only(capture):
    msg = replace(message(), message_id=str(uuid.uuid4()).upper())
    sent = []
    token = timing.CURRENT.set(capture.begin(msg.message_id))
    try:
        await DataBusReply(message=msg, comm=SimpleNamespace(service_event=lambda **kwargs: sent.append(kwargs))).ok({})
    finally:
        timing.CURRENT.reset(token)
    rows = [json.loads(row) for row in capture.drain()]
    assert len(rows) == 2
    assert all(row["message_id"] == msg.message_id.lower() for row in rows)
    assert sent[0]["data"]["message_id"] == msg.message_id


@pytest.mark.asyncio
async def test_real_handler_preparation_direct_reply_partial_order(capture, monkeypatch):
    msg = message()

    class Comm:
        def service_event(self, **kwargs):
            return None

    class Bundle:
        async def handle(self, ctx, value):
            await ctx.reply.ok({"secret": "direct-marker"})
            return {"status": "ok"}

    async def workflow(*args, **kwargs):
        return Bundle(), None

    async def refresh(**kwargs):
        return None

    async def pool():
        return None

    monkeypatch.setattr(workers, "get_workflow_instance_async", workflow)
    monkeypatch.setattr(workers, "_refresh_bundle_props", refresh)
    monkeypatch.setattr(workers, "_make_reply_comm", lambda **kwargs: Comm())
    for name in ("make_local_bundle_operation_caller", "make_local_bundle_operation_stream_caller", "make_local_bundle_named_service_caller"):
        monkeypatch.setattr(workers, name, lambda **kwargs: object())
    monkeypatch.setattr("kdcube_ai_app.apps.chat.ingress.resolvers.get_pg_pool", pool)
    worker = object.__new__(workers.DataBusBundleWorker)
    worker.bundle_spec = object()
    worker.bundle_config = object()
    worker.bundle_id = msg.bundle_id
    worker.redis = object()
    worker.relay = None
    claim = DataBusClaim("stream-marker", "1-0", msg, {}, "consumer-marker")
    token = timing.CURRENT.set(capture.begin(msg.message_id))
    try:
        result, sent = await worker._invoke_handler(claim, DataBusHandlerSpec(method_name="handle", subject=msg.subject))
        assert result.status == "ok" and sent
    finally:
        timing.CURRENT.reset(token)
    rows = capture.drain()
    phases = [json.loads(row)["phase"] for row in rows]
    assert phases == ["server.preparation.start", "server.preparation.end", "server.handler.start", "server.reply.start", "server.reply.end", "server.handler.end"]
    assert "marker" not in "".join(rows)


@pytest.mark.asyncio
async def test_concurrent_claim_context_and_real_cancellation(capture):
    worker = object.__new__(workers.DataBusBundleWorker)

    async def process(claim):
        await asyncio.sleep(0)
        timing.observe(timing.Phase.HANDLER_START)
        if claim.message.trace.get("cancel"):
            raise asyncio.CancelledError()

    worker._process_claim_without_timing = process
    messages = [message() for _ in range(8)]
    messages[-1] = replace(messages[-1], trace={"cancel": True})
    results = await asyncio.gather(*[worker._process_claim(DataBusClaim("s", "1-0", msg, {}, "c")) for msg in messages], return_exceptions=True)
    assert isinstance(results[-1], asyncio.CancelledError)
    assert timing.CURRENT.get() is None
    rows = [json.loads(row) for row in capture.drain()]
    for msg in messages:
        selected = [row for row in rows if row["message_id"] == msg.message_id]
        assert [row["phase"] for row in selected] == ["server.claim.start", "server.handler.start", "server.claim.finish"]
    assert rows[-1]["outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_actual_readiness_live_authority_and_no_route_boundaries(capture, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusResult

    msg = replace(message(), actor={}, reply={})
    calls = []

    async def result(*args, **kwargs):
        calls.append("result")

    async def ack(*args):
        calls.append("ack")

    async def invoke(claim, spec):
        return DataBusResult.ok(claim.message), False

    worker = object.__new__(workers.DataBusBundleWorker)
    worker.redis = None
    worker.relay = None
    worker.bundle_allowed_roles = ()
    worker.handler_specs = {msg.subject: DataBusHandlerSpec(method_name="handle", subject=msg.subject)}
    worker.stream = SimpleNamespace(write_result=result, ack=ack)
    worker._invoke_handler = invoke
    monkeypatch.setattr(workers.application_readiness_registry, "require_ready", lambda **kwargs: None)
    await worker._process_claim(DataBusClaim("s", "1-0", msg, {}, "c"))
    assert calls == ["result", "ack"]
    rows = [json.loads(row) for row in capture.drain()]
    assert [row["phase"] for row in rows] == ["server.claim.start", "server.readiness.start", "server.readiness.end", "server.live_authority.start", "server.live_authority.end", "server.invoke.start", "server.invoke.end", "server.reply.end", "server.claim.finish"]
    assert rows[-2]["outcome"] == "no_route"


@pytest.mark.asyncio
async def test_actual_serial_lock_retry_is_not_reordered(capture, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DATA_BUS_ORDERING_SERIAL_PER_PARTITION, DATA_BUS_PARTITION_OBJECT_REF

    msg = replace(message(), actor={}, reply={})
    calls = []

    async def acquire(*args):
        calls.append("lock")
        return None

    async def requeue(*args, **kwargs):
        calls.append("requeue")

    worker = object.__new__(workers.DataBusBundleWorker)
    worker.redis = None
    worker.bundle_allowed_roles = ()
    worker.handler_specs = {msg.subject: DataBusHandlerSpec(method_name="handle", subject=msg.subject, ordering=DATA_BUS_ORDERING_SERIAL_PER_PARTITION, partition_by=DATA_BUS_PARTITION_OBJECT_REF)}
    worker.locker = SimpleNamespace(acquire=acquire)
    worker.stream = SimpleNamespace(requeue=requeue)
    monkeypatch.setattr(workers.application_readiness_registry, "require_ready", lambda **kwargs: None)
    monkeypatch.setattr(workers, "DATA_BUS_LOCK_RETRY_SLEEP_SECONDS", 0)
    await worker._process_claim(DataBusClaim("s", "1-0", msg, {}, "c"))
    assert calls == ["lock", "requeue"]
    rows = [json.loads(row) for row in capture.drain()]
    selected = [row for row in rows if row["phase"].startswith("server.lock")]
    assert [row["phase"] for row in selected] == ["server.lock.start", "server.lock.end"]
    assert selected[-1]["outcome"] == "retry"
