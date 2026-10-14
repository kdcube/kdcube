"""W827 S2a: the size report returns aggregates only, never a value."""
from __future__ import annotations

import json
import os

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import size_report
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusMessage, DataBusResult, data_bus_stream_key

SECRET = "w827-must-never-appear"


def _message(subject: str, payload_bytes: int) -> str:
    return DataBusMessage(
        message_id="m", tenant="t", project="p", bundle_id="b@1", subject=subject,
        actor={"user_id": SECRET, "identity_authority": {"grants": [SECRET] * 3, "access_id": SECRET}},
        payload={"text": "x" * payload_bytes, "card": SECRET},
    ).to_json()


def test_percentiles_use_nearest_rank():
    assert size_report.percentiles(range(1, 101)) == {
        "count": 100, "sum": 5050, "p50": 50, "p90": 90, "p99": 99, "max": 100}
    assert size_report.percentiles([]) == {"count": 0, "sum": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0}


def test_messages_summary_reports_sizes_by_component_and_allowed_subject_only():
    raw = [_message("board.get", 100), _message("board.get", 1000), _message(SECRET, 10), b"not json"]

    report = size_report.summarize_entries("messages", raw, allowed_subjects=["board.get"])

    assert set(report["by_subject"]) == {"board.get", "other", "unparsable"}
    assert report["by_subject"]["board.get"]["count"] == 2
    assert report["unparsable"] == 1
    assert report["components"]["payload"]["count"] == 3
    assert report["components"]["actor.identity_authority"]["max"] > 0
    assert report["components"]["payload"]["max"] > 1000
    assert SECRET not in json.dumps(report)


def test_entry_bytes_are_the_stored_utf8_bytes():
    raw = [json.dumps({"subject": "s", "payload": {"t": "ü" * 10}}, ensure_ascii=False)]
    report = size_report.summarize_entries("messages", raw)
    assert report["entry_bytes"]["max"] == len(raw[0].encode("utf-8"))


def test_dlq_entries_are_labelled_by_their_message_subject():
    raw = [json.dumps({"schema": "kdcube.data_bus.dlq.v1", "reason": "r", "details": {},
                       "message": {"subject": "board.get", "actor": {"user_id": SECRET}, "payload": {}}})]
    report = size_report.summarize_entries("dlq", raw, allowed_subjects=["board.get"])
    assert list(report["by_subject"]) == ["board.get"]
    assert report["components"]["message.actor"]["count"] == 1
    assert SECRET not in json.dumps(report)


REDIS_URL = os.getenv("DATA_BUS_TEST_REDIS_URL", "")


@pytest.mark.asyncio
@pytest.mark.skipif(not REDIS_URL, reason="requires a disposable Redis (DATA_BUS_TEST_REDIS_URL)")
async def test_the_report_reads_real_streams_without_writing_or_returning_values():
    from redis.asyncio import Redis

    redis = Redis.from_url(REDIS_URL)
    await redis.flushdb()
    try:
        keys = {kind: data_bus_stream_key(tenant="t", project="p", bundle_id="b@1", kind=kind)
                for kind in ("messages", "results", "dlq")}
        for n in range(30):
            await redis.xadd(keys["messages"], {"json": _message("board.get", n * 10)})
        result = DataBusResult(message_id="m", subject="board.get", data={"rows": [SECRET] * 5})
        await redis.xadd(keys["results"], {"json": json.dumps(result.to_dict())})
        before = {kind: await redis.xlen(key) for kind, key in keys.items()}

        report = await size_report.stream_size_report(redis, tenant="t", project="p", bundle_id="b@1",
                                                      sample=10, allowed_subjects=["board.get"])

        assert {kind: await redis.xlen(key) for kind, key in keys.items()} == before
        assert report["streams"]["messages"]["xlen"] == 30 and report["streams"]["messages"]["sampled"] == 10
        assert report["streams"]["messages"]["memory_usage_bytes"] > 0
        assert report["streams"]["results"]["components"]["data"]["count"] == 1
        assert report["streams"]["dlq"] == {"xlen": 0, "memory_usage_bytes": 0, "sampled": 0,
                                            **size_report.summarize_entries("dlq", [])}
        assert SECRET not in json.dumps(report)
    finally:
        await redis.flushdb()
        await redis.aclose()
