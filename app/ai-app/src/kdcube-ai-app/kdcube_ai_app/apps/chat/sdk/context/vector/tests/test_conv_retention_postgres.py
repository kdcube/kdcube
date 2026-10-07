"""Conversation cold-tier rewrites against real PostgreSQL and files (W536).

A legacy move and a deletion that overlap must never record deleted rows
again, and the legacy move stays inside the run's budget. Runs only with
KDCUBE_TEST_POSTGRES_DSN (a disposable database with pgvector).
"""

from __future__ import annotations

import asyncio
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
            # Both entry points take the retention lock, so only a deletion that
            # does not (a process of an older version, say) can land here.
            done = await retention._delete_messages_locked(actor="operator", user_id="u1", conversation_id="c1")
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


@pytest.mark.asyncio
async def test_a_stuck_batch_holds_back_only_its_own_utc_day(env, tmp_path):
    pool, schema, backend, archive, retention = env
    # Ids 1..3 of one conversation: the stuck batch is the 09-10 day (ids 1 and 3);
    # the 09-17 row (id 2) lies inside that id range but on another UTC day.
    first = await _add(pool, schema, "u1", "c1", datetime(2026, 9, 10, 9, tzinfo=UTC))
    other_day = await _add(pool, schema, "u1", "c1", datetime(2026, 9, 17, 9, tzinfo=UTC))
    last = await _add(pool, schema, "u1", "c1", datetime(2026, 9, 10, 10, tzinfo=UTC))

    original = archive.write_batch

    async def corrupt(**kw):
        manifest = await original(**kw)
        await backend.write_bytes_a(manifest["part_key"], b"tampered")
        return manifest

    archive.write_batch = corrupt
    with pytest.raises(ColdArchiveIntegrityError):
        await retention.archive_before(_night(1))  # cutoff 09-17 02:20: only the 09-10 rows
    archive.write_batch = original

    summary = await retention.archive_before(_night(3))  # cutoff 09-19 02:20
    assert summary["stuck"] == 1 and (summary["batches"], summary["rows"]) == (1, 1)
    assert await _hot_ids(pool, schema) == [first, last]
    assert await _cold_ids(retention) == [other_day]


# ---------- W536 D1/D2: a hard delete covers the cold tier and stays deleted ----------


class _RecordingStore:
    """Bodies for retention, plus the ConversationStore surface the app's hard delete uses."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete_message(self, uri: str) -> bool:
        self.deleted.append(uri)
        return True

    def _who_and_id(self, user_id, fingerprint):
        return "registered", user_id

    async def delete_conversation(self, **scope):
        return {"messages": 0, "attachments": 0, "executions": 0}


async def _msg(pool, schema, user, conv, ts, bundle="b1") -> int:
    async with pool.acquire() as con:
        return await con.fetchval(
            f"INSERT INTO {schema}.conv_messages (user_id, bundle_id, conversation_id, role, text, hosted_uri, ts, tags) "
            f"VALUES ($1, $2, $3, 'user', 'x', $4, $5, ARRAY['conv.start', 'artifact:turn.fingerprint.v1']) RETURNING id",
            user, bundle, conv, f"cb/{user}/{conv}/{uuid.uuid4().hex}.json", ts,
        )


async def _scope_ids(pool, schema, retention, user, conv) -> tuple[list[int], list[int]]:
    async with pool.acquire() as con:
        hot = sorted(r["id"] for r in await con.fetch(
            f"SELECT id FROM {schema}.conv_messages WHERE user_id = $1 AND conversation_id = $2", user, conv))
    cold = sorted(int(r["id"]) for r in await retention.fetch_cold(
        from_ts=NOW - timedelta(days=400), user_id=user, conversation_id=conv))
    return hot, cold


def _orphan_parts(backend, live_part_keys) -> set:
    on_disk = {str(p.relative_to(backend.base_path)) for p in Path(backend.base_path).rglob("*.jsonl.gz")}
    return {k.split("conversation-cold/")[1] for k in on_disk} - {k.split("conversation-cold/")[1] for k in live_part_keys}


async def _live_parts(pool, schema) -> set:
    async with pool.acquire() as con:
        return {r["part_key"] for r in await con.fetch(
            f"SELECT part_key FROM {schema}.conv_archive_batches WHERE state = 'pruned'")}


@pytest.mark.asyncio
async def test_the_app_hard_delete_removes_archived_rows_and_unlists_the_conversation(env):
    from types import SimpleNamespace

    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
    from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import ContextRAGClient

    pool, schema, backend, archive, retention = env
    store = _RecordingStore()
    retention.store = store
    old, later = datetime(2026, 9, 10, 9, tzinfo=UTC), datetime(2026, 9, 11, 9, tzinfo=UTC)
    target_cold = [await _msg(pool, schema, "u1", "c1", old), await _msg(pool, schema, "u1", "c1", later)]
    other_bundle = await _msg(pool, schema, "u1", "c1", old, bundle="b2")  # outside the caller's bundles
    other_conv = await _msg(pool, schema, "u1", "c2", old)
    other_user = await _msg(pool, schema, "u2", "c1", old)
    assert (await retention.archive_before(_night(7)))["rows"] == 5
    target_hot = await _msg(pool, schema, "u1", "c1", datetime(2026, 10, 5, 9, tzinfo=UTC))
    async with pool.acquire() as con:
        target_uris = {r["hosted_uri"] for r in await con.fetch(
            f"SELECT hosted_uri FROM {schema}.conv_messages WHERE id = $1", target_hot)}
    target_uris |= {r["hosted_uri"] for r in await retention.fetch_cold(from_ts=NOW - timedelta(days=400))
                    if r["id"] in target_cold}

    idx = ConvIndex(pool=pool, schema=schema)
    idx.cold_retention = retention
    client = SimpleNamespace(idx=idx, store=store)
    result = await ContextRAGClient.delete_conversation(
        client, tenant="t", project="p", user_id="u1", conversation_id="c1", user_type="registered",
        bundle_id=None, bundle_ids=["b1"],
    )

    assert result["deleted_messages"] == 3  # one hot row and two archived ones
    assert set(store.deleted) == target_uris
    assert await _scope_ids(pool, schema, retention, "u1", "c1") == ([], [other_bundle])
    assert await retention.fetch_cold_conversation(
        user_id="u1", conversation_id="c1", from_ts=NOW - timedelta(days=400), bundle_ids=["b1"]) == []
    listed = await retention.list_archived_conversations(
        user_id="u1", from_ts=NOW - timedelta(days=400), bundle_id="b1")
    assert [r["conversation_id"] for r in listed] == ["c2"]
    assert await _scope_ids(pool, schema, retention, "u1", "c2") == ([], [other_conv])
    assert await _scope_ids(pool, schema, retention, "u2", "c1") == ([], [other_user])
    assert _orphan_parts(backend, await _live_parts(pool, schema)) == set()
    async with pool.acquire() as con:
        audit = await con.fetchrow(
            f"SELECT actor, reason, state, hot_rows, cold_rows, scope::text AS scope FROM {schema}.conv_archive_deletions")
    assert (audit["actor"], audit["reason"], audit["state"], audit["hot_rows"], audit["cold_rows"]) == (
        "u1", "user delete", "completed", 1, 2)
    assert json.loads(audit["scope"])["bundle_ids"] == ["b1"]


@pytest.mark.asyncio
async def test_a_deletion_after_a_crashed_run_stays_deleted_on_the_next_night(env):
    pool, schema, backend, archive, retention = env
    kept = await _msg(pool, schema, "u1", "c1", datetime(2026, 9, 10, 9, tzinfo=UTC))
    await _msg(pool, schema, "u2", "c2", datetime(2026, 9, 10, 10, tzinfo=UTC))
    original, calls = retention._verify_and_prune, {"n": 0}

    async def crash_on_second(manifest_key, batch_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("crash after write")
        return await original(manifest_key, batch_id)

    retention._verify_and_prune = crash_on_second
    with pytest.raises(RuntimeError):
        await retention.archive_before(_night(7))
    retention._verify_and_prune = original
    assert [s for _, s, _, _ in await _ledger(pool, schema)] == ["pruned", "written"]

    done = await retention.delete_messages(actor="operator", user_id="u2", conversation_id="c2", reason="test")
    assert done["unfinished_batches"] == {"finished": 1, "retired": 0}
    assert (done["hot_rows"], done["cold_rows"]) == (0, 1)
    summary = await retention.archive_before(_night(8))

    assert (summary["resumed"], summary["stuck"], summary["rows"]) == (0, 0, 0)
    assert await _scope_ids(pool, schema, retention, "u2", "c2") == ([], [])
    assert await retention.list_archived_conversations(user_id="u2", from_ts=NOW - timedelta(days=400)) == []
    assert await _scope_ids(pool, schema, retention, "u1", "c1") == ([], [kept])


@pytest.mark.asyncio
async def test_a_deletion_retires_a_stuck_batch_of_its_conversation_and_nothing_comes_back(env):
    pool, schema, backend, archive, retention = env
    await _msg(pool, schema, "u1", "c1", datetime(2026, 9, 10, 9, tzinfo=UTC))
    original = archive.write_batch

    async def corrupt(**kw):
        manifest = await original(**kw)
        await backend.write_bytes_a(manifest["part_key"], b"tampered")
        return manifest

    archive.write_batch = corrupt
    with pytest.raises(ColdArchiveIntegrityError):
        await retention.archive_before(_night(7))
    archive.write_batch = original
    other = await _msg(pool, schema, "u2", "c1", datetime(2026, 9, 10, 10, tzinfo=UTC))

    done = await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
    assert done["unfinished_batches"] == {"finished": 0, "retired": 1}
    assert done["hot_rows"] == 1
    (batch, state, error, _), = await _ledger(pool, schema)
    assert state == "retired" and done["deletion_id"] in error and "sha256 mismatch" in error
    assert list(Path(backend.base_path).rglob("*.jsonl.gz")) == []  # the stuck part is gone

    summary = await retention.archive_before(_night(8))
    assert (summary["stuck"], summary["rows"]) == (0, 1)
    assert await _scope_ids(pool, schema, retention, "u1", "c1") == ([], [])
    assert await _scope_ids(pool, schema, retention, "u2", "c1") == ([], [other])


@pytest.mark.asyncio
async def test_a_deletion_waits_for_a_running_archive_and_nothing_comes_back(env):
    pool, schema, backend, archive, retention = env
    await _msg(pool, schema, "u1", "c1", datetime(2026, 9, 10, 9, tzinfo=UTC))
    other = await _msg(pool, schema, "u2", "c1", datetime(2026, 9, 10, 10, tzinfo=UTC))
    original, entered, gate = archive.write_batch, asyncio.Event(), asyncio.Event()

    async def paused(**kw):
        manifest = await original(**kw)  # the part is written, the ledger does not know it yet
        entered.set()
        await gate.wait()
        return manifest

    archive.write_batch = paused
    run = asyncio.create_task(retention.archive_before(_night(7)))
    deletion = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        deletion = asyncio.create_task(retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1"))
        await asyncio.sleep(0.5)
        waited = not deletion.done()
    finally:
        gate.set()
        await run
        done = await deletion if deletion else None
    archive.write_batch = original
    assert waited, "the deletion ran while the archive run was between writing a part and recording it"
    assert (done["hot_rows"], done["cold_rows"]) == (0, 1)

    await retention.archive_before(_night(8))
    assert await _scope_ids(pool, schema, retention, "u1", "c1") == ([], [])
    assert await _scope_ids(pool, schema, retention, "u2", "c1") == ([], [other])


# ---------- W536 review: hot bundle scope, a per-batch lock, a bounded deletion wait ----------


@pytest.mark.asyncio
async def test_a_deletion_scoped_to_some_bundles_keeps_the_other_bundles_hot_rows(env):
    pool, schema, backend, archive, retention = env
    recent = datetime(2026, 10, 5, 9, tzinfo=UTC)
    in_a = [await _msg(pool, schema, "u1", "c1", recent, bundle="bA"),
            await _msg(pool, schema, "u1", "c1", recent + timedelta(minutes=1), bundle="bA")]
    in_b = [await _msg(pool, schema, "u1", "c1", recent, bundle="bB"),
            await _msg(pool, schema, "u1", "c1", recent + timedelta(minutes=1), bundle="bB")]

    done = await retention.delete_messages(actor="u1", user_id="u1", conversation_id="c1", bundle_ids=["bA"])

    assert done["hot_rows"] == len(in_a)
    assert await _scope_ids(pool, schema, retention, "u1", "c1") == (sorted(in_b), [])


async def _until_a_lock_waiter(pool, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with pool.acquire() as con:
            if await con.fetchval(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
            ):
                return
        assert asyncio.get_running_loop().time() < deadline, "the deletion never queued for the retention lock"
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_a_deletion_issued_mid_run_waits_for_at_most_one_batch(env):
    pool, schema, backend, archive, retention = env
    night = datetime(2026, 9, 10, 9, tzinfo=UTC)
    ids = [await _msg(pool, schema, f"u{i}", "c1", night + timedelta(minutes=i)) for i in range(5)]
    events: list = []
    entered, gate = asyncio.Event(), asyncio.Event()
    write, delete_locked = archive.write_batch, retention._delete_messages_locked

    async def tracked_write(**kw):
        events.append(("write", kw["batch_id"]))
        manifest = await write(**kw)
        if len(events) == 2:  # the second batch is in flight
            entered.set()
            await gate.wait()
        return manifest

    async def tracked_delete(**kw):
        done = await delete_locked(**kw)
        events.append(("deleted", done["hot_rows"], done["cold_rows"]))  # still under the lock
        return done

    archive.write_batch = tracked_write
    retention._delete_messages_locked = tracked_delete
    run = asyncio.create_task(retention.archive_before(_night(7), batch_size=1))
    deletion = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        # u4's only row would be the fifth batch: the deletion finds it hot.
        deletion = asyncio.create_task(retention.delete_messages(actor="operator", user_id="u4", conversation_id="c1"))
        await _until_a_lock_waiter(pool)
    finally:
        gate.set()
        summary = await asyncio.wait_for(run, 30)
        if deletion:
            await asyncio.wait_for(deletion, 30)
    archive.write_batch, retention._delete_messages_locked = write, delete_locked

    deleted_at = [i for i, e in enumerate(events) if e[0] == "deleted"]
    assert deleted_at == [2], f"the deletion waited for more than the batch in flight: {events}"
    assert events[2] == ("deleted", 1, 0)
    assert (summary["batches"], summary["rows"]) == (4, 4)
    assert await _scope_ids(pool, schema, retention, "u4", "c1") == ([], [])
    for i in range(4):
        assert await _scope_ids(pool, schema, retention, f"u{i}", "c1") == ([], [ids[i]])
    summary = await retention.archive_before(_night(8), batch_size=1)
    assert summary["rows"] == 0 and await _scope_ids(pool, schema, retention, "u4", "c1") == ([], [])


@pytest.mark.asyncio
async def test_the_ingress_delete_answers_503_when_the_retention_lock_stays_held(env, monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from kdcube_ai_app.apps.chat.ingress.conversations import conversations
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
    from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import ContextRAGClient

    pool, schema, backend, archive, retention = env
    store = _RecordingStore()
    retention.store = store
    kept = await _msg(pool, schema, "u1", "c1", datetime(2026, 10, 5, 9, tzinfo=UTC))
    idx = ConvIndex(pool=pool, schema=schema)
    idx.cold_retention = retention
    client = SimpleNamespace(idx=idx, store=store)

    class _Browser:
        async def get_conversation_details(self, **kw):
            return {"bundle_id": "b1", "turns": [{"turn_id": "t1"}]}

        async def delete_conversation(self, **kw):
            return await ContextRAGClient.delete_conversation(client, lock_wait_s=0.5, **kw)

    class _Comm:
        async def emit_conversation_status(self, **kw):
            raise AssertionError("a refused delete reports no deleted status")

    async def _registry(runtime_ctx, tenant, project):
        return SimpleNamespace(bundles={"b1": object()})

    monkeypatch.setattr(conversations, "load_persisted_registry_from_runtime_ctx", _registry)
    monkeypatch.setattr(conversations.router, "state", SimpleNamespace(conversation_browser=_Browser(), chat_comm=_Comm()),
                        raising=False)
    session = SimpleNamespace(user_id="u1", session_id="s1", user_type="registered", fingerprint="fp")
    async with pool.acquire() as holder:  # an archive step in another process holds the lock
        await holder.fetchval("SELECT pg_advisory_lock($1)", retention._lock_key())
        try:
            with pytest.raises(HTTPException) as refused:
                await asyncio.wait_for(conversations.delete_conversation(
                    tenant="t", project="p", conversation_id="c1", session=session), 10)
        finally:
            await holder.fetchval("SELECT pg_advisory_unlock($1)", retention._lock_key())

    assert refused.value.status_code == 503
    assert refused.value.detail["code"] == "conversation_delete_busy"
    assert await _scope_ids(pool, schema, retention, "u1", "c1") == ([kept], [])
    async with pool.acquire() as con:
        assert await con.fetchval(f"SELECT count(*) FROM {schema}.conv_archive_deletions") == 0
    done = await ContextRAGClient.delete_conversation(
        client, tenant="t", project="p", user_id="u1", conversation_id="c1", user_type="registered",
        bundle_ids=["b1"], lock_wait_s=0.5)
    assert done["deleted_messages"] == 1
