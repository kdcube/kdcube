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
    ColdArchiveIntegrityError,
    ConversationColdArchive,
    encode_part,
    row_to_record,
    sha256_hex,
)
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import ConversationRetention, hot_cutoff
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


@pytest.mark.asyncio
async def test_a_dated_search_finds_archived_turns_and_names_each_hits_storage(env):
    """W536: a date filter past the watermark reaches cold turns; each hit says hot or cold."""

    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
    from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import search_context

    pool, schema, _, _, retention = env
    now = datetime.now(timezone.utc)
    rows = [
        ("t-old", "assistant", "The index is consistent and the receipt table is renamed.", now - timedelta(days=200)),
        ("t-old-other", "assistant", "Nothing about that here.", now - timedelta(days=199)),
        ("t-new", "assistant", "The receipt table rename is live.", now - timedelta(days=2)),
    ]
    async with pool.acquire() as con:
        for turn_id, role, text, ts in rows:
            await con.execute(
                f"INSERT INTO {schema}.conv_messages (user_id, bundle_id, conversation_id, message_id, role, text,"
                f" hosted_uri, ts, ttl_days, tags, turn_id) VALUES ('u1','b1','c1',$1,$2,$3,$4,$5,3650,'{{}}',$6)",
                f"m-{turn_id}", role, text, f"cb/x/{turn_id}.json", ts, turn_id,
            )
    summary = await retention.archive_before(now - timedelta(days=90))
    assert summary["rows"] == 2

    index = ConvIndex(pool, schema=schema)
    index.cold_retention = retention

    async def search(filters):
        _, hits = await search_context(
            index, object(), None,
            targets=[{"where": "assistant", "query": "receipt table renamed"}],
            user="u1", conv="c1", scope="user", top_k=10, days=3650,
            scoring_mode="rrf_hybrid", timestamp_filters=filters,
        )
        return {h["turn_id"]: h["storage"] for h in hits}

    dated = await search([{"op": ">=", "value": (now - timedelta(days=400)).isoformat()}])
    assert dated == {"t-old": "cold", "t-new": "hot"}
    # Without a date filter the archive is not read: only the hot turn.
    assert await search(None) == {"t-new": "hot"}
    # A date range that ends before the hot row returns only the archived turn.
    old_only = await search([
        {"op": ">=", "value": (now - timedelta(days=400)).isoformat()},
        {"op": "<=", "value": (now - timedelta(days=150)).isoformat()},
    ])
    assert old_only == {"t-old": "cold"}

    # Opening the conversation pages through its hot row and then its
    # archived rows on one newest-first keyset (W536), so a cold match opens.
    first = await index.fetch_message_page(user_id="u1", conversation_id="c1", limit=2, days=3650)
    assert [(r["turn_id"], r.get("storage", "hot")) for r in first] == [("t-new", "hot"), ("t-old-other", "cold")]
    last = first[-1]
    second = await index.fetch_message_page(
        user_id="u1", conversation_id="c1", limit=2, days=3650,
        before_ts=last["ts"], before_id=int(last["id"]),
    )
    assert [(r["turn_id"], r["storage"]) for r in second] == [("t-old", "cold")]


# Nightly runs (ported from scratch/W536 sim_conv_archive.py / sim_conv_poison.py):
# hot_days=14 at 02:20 UTC, as the admin-bundle cron computes the cutoff.

UTC = timezone.utc


def _night(day: int) -> datetime:
    return hot_cutoff(14, now=datetime(2026, 10, day, 2, 20, tzinfo=UTC))


async def _add(pool, schema, user, conv, ts) -> int:
    async with pool.acquire() as con:
        return await con.fetchval(
            f"INSERT INTO {schema}.conv_messages (user_id, conversation_id, role, text, hosted_uri, ts) "
            f"VALUES ($1, $2, 'user', 'x', 'cb/x', $3) RETURNING id",
            user, conv, ts,
        )


async def _hot_ids(pool, schema) -> list[int]:
    async with pool.acquire() as con:
        return sorted(r["id"] for r in await con.fetch(f"SELECT id FROM {schema}.conv_messages"))


async def _ledger(pool, schema) -> list[tuple]:
    async with pool.acquire() as con:
        return [tuple(r) for r in await con.fetch(
            f"SELECT batch_id, state, error, updated_at FROM {schema}.conv_archive_batches ORDER BY batch_id"
        )]


def _files(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.asyncio
async def test_a_corrupt_written_batch_does_not_stop_later_nights(env, tmp_path):
    pool, schema, backend, archive, retention = env
    stuck_row = await _add(pool, schema, "u1", "c1", datetime(2026, 9, 10, 9, tzinfo=UTC))

    # Night N (10-01): the part is corrupted right after it is written.
    original, good = archive.write_batch, {}

    async def corrupt(**kw):
        manifest = await original(**kw)
        good["part"] = (manifest["part_key"], await backend.read_bytes_a(manifest["part_key"]))
        await backend.write_bytes_a(manifest["part_key"], b"tampered")
        return manifest

    archive.write_batch = corrupt
    with pytest.raises(ColdArchiveIntegrityError):
        await retention.archive_before(_night(1))
    archive.write_batch = original
    (stuck_batch, state, error, _), = await _ledger(pool, schema)
    assert state == "written" and "sha256 mismatch" in error
    assert await _hot_ids(pool, schema) == [stuck_row]  # nothing deleted before a verified readback

    # Rows that become eligible later, including one of the same user,
    # conversation and day as the stuck batch, plus one that stays hot.
    other_scope = await _add(pool, schema, "u2", "c1", datetime(2026, 9, 10, 11, tzinfo=UTC))
    same_scope_later = await _add(pool, schema, "u1", "c1", datetime(2026, 9, 10, 12, tzinfo=UTC))
    next_day = await _add(pool, schema, "u1", "c1", datetime(2026, 9, 18, 9, tzinfo=UTC))
    recent = await _add(pool, schema, "u1", "c1", datetime(2026, 10, 1, 12, tzinfo=UTC))
    total = 5

    # Night N+1 (10-03): the stuck batch is reported, later rows still move.
    summary = await retention.archive_before(_night(3))
    assert summary["stuck"] == 1 and summary["stuck_batches"] == [stuck_batch]
    assert summary["resumed"] == 0 and summary["batches"] == 3 and summary["rows"] == 3
    hot, cold = await _hot_ids(pool, schema), await _cold_ids(retention)
    assert hot == [stuck_row, recent]  # the stuck batch's hot row was not deleted
    assert cold == sorted([other_scope, same_scope_later, next_day])
    assert len(hot) + len(cold) == total and not set(hot) & set(cold)
    ledger = {b: (s, e) for b, s, e, _ in await _ledger(pool, schema)}
    assert ledger[stuck_batch][0] == "written" and "sha256 mismatch" in ledger[stuck_batch][1]
    assert [s for b, (s, _) in ledger.items() if b != stuck_batch] == ["pruned"] * 3

    # Night N+2 (10-04): nothing new is eligible; still reported, nothing moves.
    files_before = _files(tmp_path)
    summary = await retention.archive_before(_night(4))
    assert (summary["stuck"], summary["batches"], summary["rows"]) == (1, 0, 0)
    assert _files(tmp_path) == files_before
    assert await _hot_ids(pool, schema) == [stuck_row, recent]

    # Once the part is repaired the next night finishes the batch, with no duplicate.
    await backend.write_bytes_a(*good["part"])
    summary = await retention.archive_before(_night(5))
    assert (summary["resumed"], summary["stuck"], summary["batches"]) == (1, 0, 0)
    hot, cold = await _hot_ids(pool, schema), await _cold_ids(retention)
    assert hot == [recent] and cold == sorted([stuck_row, other_scope, same_scope_later, next_day])
    assert len(hot) + len(cold) == total


@pytest.mark.asyncio
async def test_a_quiet_night_moves_and_writes_nothing(env, tmp_path):
    pool, schema, backend, archive, retention = env
    old = await _add(pool, schema, "u1", "c1", datetime(2026, 9, 10, 9, tzinfo=UTC))
    # 09-23 05:00 is past every cutoff up to night 10-07 (09-23 02:20).
    recent = await _add(pool, schema, "u2", "c4", datetime(2026, 9, 23, 5, tzinfo=UTC))
    first = await retention.archive_before(_night(6))
    assert (first["batches"], first["rows"]) == (1, 1)
    files, ledger, mark = _files(tmp_path), await _ledger(pool, schema), await retention.watermark()

    summary = await retention.archive_before(_night(7))
    assert summary == {"resumed": 0, "stuck": 0, "stuck_batches": [], "batches": 0, "rows": 0,
                       "indexed": 0, "relaid": 0}
    assert _files(tmp_path) == files
    assert await _ledger(pool, schema) == ledger
    assert await retention.watermark() == mark
    assert await _hot_ids(pool, schema) == [recent] and await _cold_ids(retention) == [old]
