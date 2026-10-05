"""Conversation cold-tier rewrites against real PostgreSQL and files (W536).

A legacy move and a deletion that overlap must never record deleted rows
again, and the legacy move stays inside the run's budget. Runs only with
KDCUBE_TEST_POSTGRES_DSN (a disposable database with pgvector).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_cold import (
    ConversationColdArchive,
    encode_part,
    row_to_record,
    sha256_hex,
)
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import ConversationRetention
from kdcube_ai_app.storage.storage import LocalFileSystemBackend

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
SHAPES = {1: ("u1", "c1"), 2: ("u1", "c2"), 3: ("u2", "c1"), 4: ("u1", "c3"), 5: ("u3", "c1")}


class _Bodies:
    async def delete_message(self, uri: str) -> bool:
        return True


def _record(i: int) -> dict:
    user, conv = SHAPES[i]
    return row_to_record({
        "id": i, "user_id": user, "bundle_id": "b1", "agent_id": "codex", "conversation_id": conv,
        "message_id": f"m{i}", "role": "user", "text": f"text {i}", "hosted_uri": f"cb/x/{i}.json",
        "ts": NOW - timedelta(days=200, minutes=i), "ttl_days": 3650, "user_type": "registered",
        "tags": [], "turn_id": "t1", "anchors_text": None, "embedding": [0.25 * i, 0.5, -1.0],
    })


@pytest.fixture
async def env(tmp_path):
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")
    import asyncpg

    schema = f"w536_{uuid.uuid4().hex[:8]}"
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    sql = (Path(__file__).parents[1] / "conv_index.sql").read_text().replace("<SCHEMA>", schema)
    async with pool.acquire() as con:
        await con.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await con.execute(f"CREATE SCHEMA {schema}")
        await con.execute(sql)
    backend = LocalFileSystemBackend(str(tmp_path))
    archive = ConversationColdArchive(backend, tenant="t", project="p")
    retention = ConversationRetention(pool=pool, schema=schema, archive=archive, store=_Bodies())
    try:
        yield pool, schema, backend, archive, retention
    finally:
        async with pool.acquire() as con:
            await con.execute(f"DROP SCHEMA {schema} CASCADE")
        await pool.close()


async def _legacy_part(pool, schema, backend, archive, retention, batch_id="legacy", ids=(1, 2, 3)):
    day = (NOW - timedelta(days=200)).date()
    records = [_record(i) for i in ids]
    data = encode_part(records)
    part_key, manifest_key = archive.part_key(day, batch_id), archive.manifest_key(day, batch_id)
    manifest = {"batch_id": batch_id, "day": day.isoformat(), "row_count": len(records), "ids": list(ids),
                "sha256": sha256_hex(data), "part_key": part_key,
                "min_ts": min(r["ts"] for r in records), "max_ts": max(r["ts"] for r in records)}
    await backend.write_bytes_a(part_key, data)
    await backend.write_bytes_a(manifest_key, json.dumps(manifest).encode())
    async with pool.acquire() as con:
        await con.execute(
            f"INSERT INTO {schema}.conv_archive_batches (batch_id, day, row_count, min_id, max_id, min_ts, max_ts,"
            f" part_key, manifest_key, sha256, state) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'pruned')",
            batch_id, day, len(ids), min(ids), max(ids), datetime.fromisoformat(manifest["min_ts"]),
            datetime.fromisoformat(manifest["max_ts"]), part_key, manifest_key, manifest["sha256"],
        )
        await retention._index_batch(con, batch_id, records)


async def _cold_ids(retention) -> list[int]:
    return sorted(int(r["id"]) for r in await retention.fetch_cold(from_ts=NOW - timedelta(days=400)))


@pytest.mark.asyncio
async def test_a_deletion_completed_during_a_legacy_move_never_comes_back(env):
    pool, schema, backend, archive, retention = env
    await _legacy_part(pool, schema, backend, archive, retention)
    original = archive.read_part
    armed = {"race": True}

    async def read_then_delete(manifest):
        records = await original(manifest)
        if armed.pop("race", False):
            done = await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
            assert done["cold_rows"] == 1
        return records

    archive.read_part = read_then_delete
    summary = await retention.archive_before(NOW - timedelta(days=90))

    assert summary["relaid"] == 0
    assert await _cold_ids(retention) == [2, 3]
    async with pool.acquire() as con:
        live = {r["part_key"] for r in await con.fetch(f"SELECT part_key FROM {schema}.conv_archive_batches WHERE state='pruned'")}
        states = await con.fetch(f"SELECT state FROM {schema}.conv_archive_deletions")
    assert [s["state"] for s in states] == ["completed"]
    # Only live parts are on disk: the losing move removed what it wrote.
    on_disk = {str(p.relative_to(backend.base_path)) for p in Path(backend.base_path).rglob("*.jsonl.gz")}
    assert {k.split("conversation-cold/")[1] for k in on_disk} == {k.split("conversation-cold/")[1] for k in live}


@pytest.mark.asyncio
async def test_a_legacy_move_during_a_deletion_makes_the_deletion_plan_again(env):
    pool, schema, backend, archive, retention = env
    await _legacy_part(pool, schema, backend, archive, retention)
    original = retention._cold_matches
    armed = {"race": True}

    async def plan_then_move(**kwargs):
        plan = await original(**kwargs)
        if armed.pop("race", False):
            assert await retention._relayout_legacy_batches() == 1
        return plan

    retention._cold_matches = plan_then_move
    done = await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")

    assert done["cold_rows"] == 1
    assert await _cold_ids(retention) == [2, 3]


@pytest.mark.asyncio
async def test_moving_legacy_parts_counts_toward_the_run_budget(env):
    pool, schema, backend, archive, retention = env
    await _legacy_part(pool, schema, backend, archive, retention, "legacy-a", ids=(1, 2))
    await _legacy_part(pool, schema, backend, archive, retention, "legacy-b", ids=(4, 5))

    assert (await retention.archive_before(NOW - timedelta(days=90), max_batches=0))["relaid"] == 0
    assert (await retention.archive_before(NOW - timedelta(days=90), max_batches=1))["relaid"] == 1
    async with pool.acquire() as con:
        rows = await con.fetch(
            f"SELECT batch_id, state FROM {schema}.conv_archive_batches WHERE batch_id IN ('legacy-a','legacy-b') ORDER BY batch_id"
        )
    assert [r["state"] for r in rows] == ["retired", "pruned"]
    assert await _cold_ids(retention) == [1, 2, 4, 5]
