"""W619 (W536 part A): message bodies move to the cold tier with their index rows.

Real PostgreSQL and a real file-backed ConversationStore; runs only with
KDCUBE_TEST_POSTGRES_DSN (a disposable database with pgvector). Nights are
the admin cron's: hot_days=14 at 02:20 UTC.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_cold import ConversationColdArchive
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import ConversationRetention, hot_cutoff
from kdcube_ai_app.apps.chat.sdk.storage import conversation_body_cold as cold
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

UTC = timezone.utc


class _Crash(BaseException):
    """A process death: not caught by `except Exception`."""


def _night(month: int, day: int) -> datetime:
    return hot_cutoff(14, now=datetime(2026, month, day, 2, 20, tzinfo=UTC))


@pytest.fixture
async def env(tmp_path):
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")
    import asyncpg

    schema = f"w619_{uuid.uuid4().hex[:8]}"
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6)
    sql = (Path(__file__).parents[1] / "conv_index.sql").read_text().replace("<SCHEMA>", schema)
    async with pool.acquire() as con:
        await con.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await con.execute(f"CREATE SCHEMA {schema}")
        await con.execute(sql)
    store = ConversationStore(storage_uri=tmp_path.as_uri())
    archive = ConversationColdArchive(store.backend, tenant="t", project="p")
    retention = ConversationRetention(pool=pool, schema=schema, archive=archive, store=store)
    try:
        yield pool, schema, store, archive, retention
    finally:
        async with pool.acquire() as con:
            await con.execute(f"DROP SCHEMA {schema} CASCADE")
        await pool.close()


async def _put(pool, schema, store, user, conv, ts, *, turn="t1", index_only=False, bundle="b1",
               agent=None) -> tuple[int, str]:
    """A stored body and its index row, created at `ts` (the body's id carries its creation time)."""

    if index_only:
        uri = "index_only"
    else:
        uri, _, _ = await store.put_message(
            tenant="t", project="p", user=user, fingerprint=None, conversation_id=conv, turn_id=turn,
            role="user", text=f"body of {user}/{conv} at {ts.isoformat()}",
            msg_ts=ts.strftime("%Y-%m-%dT%H-%M-%S"),
        )
    async with pool.acquire() as con:
        row_id = await con.fetchval(
            f"INSERT INTO {schema}.conv_messages (user_id, bundle_id, agent_id, conversation_id, role, text, hosted_uri,"
            f" ts, turn_id) VALUES ($1, $2, $3, $4, 'user', 'x', $5, $6, $7) RETURNING id",
            user, bundle, agent, conv, uri, ts, turn,
        )
    return row_id, uri


async def _hot_ids(pool, schema) -> list[int]:
    async with pool.acquire() as con:
        return sorted(r["id"] for r in await con.fetch(f"SELECT id FROM {schema}.conv_messages"))


async def _cold_ids(retention) -> list[int]:
    return sorted(int(r["id"]) for r in await retention.fetch_cold(from_ts=datetime(2025, 1, 1, tzinfo=UTC)))


def _rel(store, uri) -> str:
    return store._rel_from_uri_or_path(uri)


def _raw(store, uri):
    path = Path(store.backend.base_path) / _rel(store, uri)
    return json.loads(path.read_bytes()) if path.exists() else None


def _is_pointer(store, uri) -> bool:
    raw = _raw(store, uri)
    return isinstance(raw, dict) and raw.get("schema") == cold.POINTER_SCHEMA


def _cold_path(store, uri) -> Path:
    return Path(store.backend.base_path) / cold.cold_key_for(_rel(store, uri))


async def _assert_conserved(pool, schema, retention, store, rows: dict) -> None:
    """Every row is in exactly one tier; an archived row's body is cold, a hot row's body is hot."""

    hot, cold_ids = await _hot_ids(pool, schema), await _cold_ids(retention)
    assert not set(hot) & set(cold_ids)
    assert sorted(hot + cold_ids) == sorted(rows)
    for row_id, uri in rows.items():
        if uri == "index_only":
            continue
        body = await store.get_message(uri)
        assert body["text"].startswith("body of ")
        if row_id in cold_ids:
            assert body["storage"] == "cold" and _is_pointer(store, uri) and _cold_path(store, uri).exists()
            assert "text" not in _raw(store, uri)  # hot keeps the pointer only
        else:
            assert body["storage"] == "hot" and not _is_pointer(store, uri)
            assert not _cold_path(store, uri).exists()


@pytest.mark.asyncio
async def test_grouped_bodies_move_oldest_first_across_days_within_max_batches(env):
    """Main's condition: 3 UTC days, batch_size larger than any group, max_batches bounds a night."""

    pool, schema, store, archive, retention = env
    base = datetime(2026, 9, 8, 9, tzinfo=UTC)
    specs = [  # (user, conv, day offset, minute)
        ("u1", "c1", 0, 0), ("u1", "c1", 0, 5), ("u2", "c2", 0, 1),
        ("u1", "c1", 1, 0), ("u3", "c3", 1, 2), ("u3", "c3", 1, 3),
        ("u2", "c2", 2, 0), ("u1", "c1", 2, 7),
    ]
    rows = {}
    for user, conv, day, minute in specs:
        row_id, uri = await _put(pool, schema, store, user, conv, base + timedelta(days=day, minutes=minute))
        rows[row_id] = uri
    marker_id, _ = await _put(pool, schema, store, "u2", "c2", base + timedelta(days=1, minutes=30), index_only=True)
    rows[marker_id] = "index_only"
    recent_id, recent_uri = await _put(pool, schema, store, "u4", "c4", datetime(2026, 10, 5, 9, tzinfo=UTC))
    rows[recent_id] = recent_uri
    written = []
    original = archive.write_batch

    async def track(**kw):
        written.append((min(r["ts"] for r in kw["records"]), sorted(r["id"] for r in kw["records"])))
        return await original(**kw)

    archive.write_batch = track
    night = _night(10, 7)

    first = await retention.archive_before(night, batch_size=50, max_batches=2)
    assert (first["batches"], first["rows"], first["stuck"]) == (2, 3, 0)
    assert first["bodies_moved"] == 3 and first["body_errors"] == []
    assert written[0][1] == sorted(list(rows)[:2]) and written[1][1] == [list(rows)[2]]
    await _assert_conserved(pool, schema, retention, store, rows)

    second = await retention.archive_before(night, batch_size=50, max_batches=3)
    assert (second["batches"], second["rows"]) == (3, 4)  # u1/c1 09-09, u2/c2 09-09 (index_only), u3/c3 09-09
    assert second["bodies_moved"] == 3 and second["bodies_not_a_body"] == 1
    await _assert_conserved(pool, schema, retention, store, rows)

    third = await retention.archive_before(night, batch_size=50)
    assert (third["batches"], third["rows"]) == (2, 2)
    assert await _hot_ids(pool, schema) == [recent_id]
    await _assert_conserved(pool, schema, retention, store, rows)
    # Age order: every part starts no earlier than the one before it; nothing is skipped.
    assert [stamp for stamp, _ in written] == sorted(stamp for stamp, _ in written)
    assert sorted(i for _, ids in written for i in ids) == sorted(set(rows) - {recent_id})
    async with pool.acquire() as con:
        states = {r["state"] + "/" + r["body_state"] for r in await con.fetch(
            f"SELECT state, body_state FROM {schema}.conv_archive_batches")}
    assert states == {"pruned/complete"}


@pytest.mark.asyncio
async def test_a_crash_between_body_copy_and_hot_replacement_is_finished_the_next_night(env, monkeypatch):
    pool, schema, store, archive, retention = env
    rows = dict([await _put(pool, schema, store, "u1", "c1", datetime(2026, 9, 10, 9, m, tzinfo=UTC)) for m in (0, 1)])
    real_pointer = cold._write_pointer

    async def die(*args, **kwargs):
        raise _Crash("process killed after the cold copy, before the hot body was replaced")

    monkeypatch.setattr(cold, "_write_pointer", die)
    with pytest.raises(_Crash):
        await retention.archive_before(_night(10, 1))
    monkeypatch.setattr(cold, "_write_pointer", real_pointer)

    first_uri = next(iter(rows.values()))
    assert _cold_path(store, first_uri).exists()  # copied
    assert not _is_pointer(store, first_uri) and _raw(store, first_uri)["text"].startswith("body of")  # still hot
    assert await _hot_ids(pool, schema) == sorted(rows) and await _cold_ids(retention) == []
    async with pool.acquire() as con:
        assert await con.fetchval(f"SELECT state FROM {schema}.conv_archive_batches") == "written"

    summary = await retention.archive_before(_night(10, 2))
    assert (summary["resumed"], summary["stuck"], summary["batches"]) == (1, 0, 0)
    assert summary["bodies_moved"] == 2
    await _assert_conserved(pool, schema, retention, store, rows)
    assert await _hot_ids(pool, schema) == []


@pytest.mark.asyncio
async def test_a_crash_after_the_hot_pointer_before_the_prune_is_finished_the_next_night(env, monkeypatch):
    pool, schema, store, archive, retention = env
    rows = dict([await _put(pool, schema, store, "u1", "c1", datetime(2026, 9, 10, 9, m, tzinfo=UTC)) for m in (0, 1)])
    real_move = retention._move_bodies

    async def move_then_die(records):
        await real_move(records)
        raise _Crash("process killed after the bodies moved, before the hot rows were pruned")

    monkeypatch.setattr(retention, "_move_bodies", move_then_die)
    with pytest.raises(_Crash):
        await retention.archive_before(_night(10, 1))
    monkeypatch.setattr(retention, "_move_bodies", real_move)
    assert all(_is_pointer(store, uri) for uri in rows.values())
    assert await _hot_ids(pool, schema) == sorted(rows)
    for uri in rows.values():  # reads are whole meanwhile
        assert (await store.get_message(uri))["storage"] == "cold"

    summary = await retention.archive_before(_night(10, 2))
    assert (summary["resumed"], summary["bodies_already_cold"], summary["bodies_moved"]) == (1, 2, 0)
    await _assert_conserved(pool, schema, retention, store, rows)


@pytest.mark.asyncio
async def test_a_body_that_fails_its_readback_stays_hot_is_reported_and_later_nights_go_on(env, monkeypatch):
    """D3 for a body part: the batch is isolated as stuck, its rows and bodies stay hot, the night goes on."""

    pool, schema, store, archive, retention = env
    bad_id, bad_uri = await _put(pool, schema, store, "u1", "c1", datetime(2026, 9, 10, 9, tzinfo=UTC))
    rows = {bad_id: bad_uri}
    for user, hour in (("u2", 10), ("u3", 11)):
        row_id, uri = await _put(pool, schema, store, user, "c1", datetime(2026, 9, 10, hour, tzinfo=UTC))
        rows[row_id] = uri
    bad_cold = cold.cold_key_for(_rel(store, bad_uri))
    write = store.backend.write_bytes_a
    armed = {"on": True}

    async def corrupt_cold_copy(key, data, *args, **kwargs):
        if armed["on"] and key == bad_cold:
            data = data[:-10] + b"corrupted!"  # the storage keeps different bytes
        return await write(key, data, *args, **kwargs)

    monkeypatch.setattr(store.backend, "write_bytes_a", corrupt_cold_copy)

    night1 = await retention.archive_before(_night(10, 1))
    assert night1["stuck"] == 1 and len(night1["stuck_batches"]) == 1
    assert night1["batches"] == 2 and night1["rows"] == 2 and night1["bodies_moved"] == 2
    assert len(night1["body_errors"]) == 1 and "readback mismatch" in night1["body_errors"][0]
    assert bad_uri in night1["body_errors"][0]
    assert await _hot_ids(pool, schema) == [bad_id]
    body = await store.get_message(bad_uri)
    assert body["storage"] == "hot" and body["text"].startswith("body of") and not _is_pointer(store, bad_uri)
    await _assert_conserved(pool, schema, retention, store, rows)  # the bad row and body: hot; the others: cold

    late_id, late_uri = await _put(pool, schema, store, "u1", "c2", datetime(2026, 9, 12, 9, tzinfo=UTC))
    rows[late_id] = late_uri
    night2 = await retention.archive_before(_night(10, 2))  # still failing: reported again, the new row moves
    assert night2["stuck"] == 1 and night2["batches"] == 1 and night2["rows"] == 1
    assert await _hot_ids(pool, schema) == [bad_id] and _raw(store, bad_uri)["text"].startswith("body of")

    armed["on"] = False  # storage healthy again
    night3 = await retention.archive_before(_night(10, 3))
    assert (night3["resumed"], night3["stuck"], night3["body_errors"]) == (1, 0, [])
    assert await _hot_ids(pool, schema) == []
    await _assert_conserved(pool, schema, retention, store, rows)


@pytest.mark.asyncio
async def test_a_deletion_mid_run_waits_at_most_one_batch_and_removes_both_body_tiers(env):
    pool, schema, store, archive, retention = env
    night = datetime(2026, 9, 10, 9, tzinfo=UTC)
    rows = {}
    for i in range(4):
        row_id, uri = await _put(pool, schema, store, f"u{i}", "c1", night + timedelta(minutes=i))
        rows[row_id] = uri
    first_uri = rows[min(rows)]
    events: list = []
    entered, gate = asyncio.Event(), asyncio.Event()
    write = archive.write_batch

    async def tracked_write(**kw):
        events.append("write")
        manifest = await write(**kw)
        if events.count("write") == 2:
            entered.set()
            await gate.wait()
        return manifest

    archive.write_batch = tracked_write
    run = asyncio.create_task(retention.archive_before(_night(10, 7)))
    await asyncio.wait_for(entered.wait(), 10)
    deletion = asyncio.create_task(retention.delete_messages(actor="u0", user_id="u0", conversation_id="c1"))
    deadline = asyncio.get_running_loop().time() + 10
    while True:  # the deletion queues for the retention lock
        async with pool.acquire() as con:
            if await con.fetchval("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted"):
                break
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.02)
    gate.set()
    done = await asyncio.wait_for(deletion, 10)
    events.append("deleted")
    summary = await asyncio.wait_for(run, 10)

    assert events[:3] == ["write", "write", "deleted"]  # it waited for the in-flight batch only
    assert done["cold_rows"] == 1 and done["body_objects"] == 1
    assert not _cold_path(store, first_uri).exists() and _raw(store, first_uri) is None
    with pytest.raises(FileNotFoundError):
        await store.get_message(first_uri)
    assert summary["batches"] == 4
    remaining = {k: v for k, v in rows.items() if v != first_uri}
    await _assert_conserved(pool, schema, retention, store, remaining)


@pytest.mark.asyncio
async def test_successive_nights_with_a_quiet_night_move_each_body_once(env, tmp_path):
    pool, schema, store, archive, retention = env
    rows = {}

    async def add(ts):
        row_id, uri = await _put(pool, schema, store, "u1", "c1", ts, turn=f"t{len(rows)}")
        rows[row_id] = uri
        return row_id

    a = await add(datetime(2026, 9, 15, 9, tzinfo=UTC))
    b = await add(datetime(2026, 9, 17, 1, tzinfo=UTC))
    c = await add(datetime(2026, 9, 20, 9, tzinfo=UTC))

    n1 = await retention.archive_before(_night(10, 1))   # cutoff 09-17 02:20
    assert (n1["rows"], n1["bodies_moved"]) == (2, 2) and await _hot_ids(pool, schema) == [c]
    await _assert_conserved(pool, schema, retention, store, rows)
    files = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    n2 = await retention.archive_before(_night(10, 2))   # cutoff 09-18 02:20: quiet
    assert (n2["batches"], n2["rows"], n2["bodies_moved"], n2["stuck"]) == (0, 0, 0, 0)
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == files

    d = await add(datetime(2026, 10, 5, 9, tzinfo=UTC))
    n3 = await retention.archive_before(_night(10, 5))   # cutoff 09-21 02:20
    assert (n3["rows"], n3["bodies_moved"], n3["bodies_already_cold"]) == (1, 1, 0)
    assert await _hot_ids(pool, schema) == [d]
    await _assert_conserved(pool, schema, retention, store, rows)
    assert sorted(await _cold_ids(retention)) == sorted([a, b, c])


@pytest.mark.asyncio
async def test_parts_archived_without_bodies_are_caught_up_within_max_batches(env):
    pool, schema, store, archive, retention = env
    rows = {}
    for day in (8, 9, 10):
        row_id, uri = await _put(pool, schema, store, "u1", "c1", datetime(2026, 9, day, 9, tzinfo=UTC))
        rows[row_id] = uri
    retention.store = None  # an older release: index rows only
    assert (await retention.archive_before(_night(10, 7)))["rows"] == 3
    assert not any(_is_pointer(store, uri) for uri in rows.values())
    retention.store = store

    first = await retention.archive_before(_night(10, 7), max_batches=2)
    assert first["body_resumed"] == 2 and first["bodies_moved"] == 2
    second = await retention.archive_before(_night(10, 7), max_batches=2)
    assert second["body_resumed"] == 1
    await _assert_conserved(pool, schema, retention, store, rows)


@pytest.mark.asyncio
async def test_reads_name_hot_cold_or_unavailable_and_never_return_an_empty_body(env):
    pool, schema, store, archive, retention = env
    old_id, old_uri = await _put(pool, schema, store, "u1", "c1", datetime(2026, 9, 10, 9, tzinfo=UTC), turn="t1")
    bad_id, bad_uri = await _put(pool, schema, store, "u1", "c1", datetime(2026, 9, 10, 10, tzinfo=UTC), turn="t2")
    lost_id, lost_uri = await _put(pool, schema, store, "u1", "c1", datetime(2026, 9, 10, 11, tzinfo=UTC), turn="t3")
    new_id, new_uri = await _put(pool, schema, store, "u1", "c1", datetime(2026, 10, 6, 9, tzinfo=UTC), turn="t4")
    await retention.archive_before(_night(10, 7))

    assert (await store.get_message(new_uri))["storage"] == "hot"
    old = await store.get_message(old_uri)
    assert old["storage"] == "cold" and old["text"].startswith("body of")
    _cold_path(store, bad_uri).write_bytes(b'{"text": "tampered"}')
    bad = await store.get_message(bad_uri)
    assert bad["storage"] == "unavailable" and bad["body_unavailable_reason"] == "cold_body_hash_mismatch"
    assert bad["text"] is None and bad["conversation_id"] == "c1" and bad["turn_id"] == "t2"
    # The hot pointer is gone: the read finds the cold copy by the message's date.
    (Path(store.backend.base_path) / _rel(store, lost_uri)).unlink()
    lost = await store.get_message(lost_uri)
    assert lost["storage"] == "cold" and lost["text"].startswith("body of")
    listed = store.list_conversation(tenant="t", project="p", user_type="registered", user_or_fp="u1",
                                     conversation_id="c1")
    assert {m["turn_id"]: m["storage"] for m in listed} == {"t1": "cold", "t2": "unavailable", "t4": "hot"}
    # A delete removes the dated cold body even without the hot pointer (privacy).
    assert await store.delete_message(lost_uri) is True
    assert not _cold_path(store, lost_uri).exists()
    with pytest.raises(FileNotFoundError):
        await store.get_message(lost_uri)
    assert await store.delete_message(old_uri) is True
    assert not _cold_path(store, old_uri).exists() and _raw(store, old_uri) is None


@pytest.mark.asyncio
async def test_archived_conversations_are_retrievable_by_date_range_and_filters(env):
    """Operator: old data leaves hot storage "but still be retrievable on demand (even if not with convenient
    search semantic of lexical search - but at least with dates ranges an other fitlers we have directly in the
    cold sotrgae (user, project, agent, etc)."
    """

    pool, schema, store, archive, retention = env
    day1 = datetime(2026, 9, 8, tzinfo=UTC)
    day2, day3 = day1 + timedelta(days=1), day1 + timedelta(days=2)
    seeded = {}  # row id -> (user, conv, agent, bundle, ts, uri)

    async def seed(user, conv, agent, bundle, ts):
        row_id, uri = await _put(pool, schema, store, user, conv, ts, turn=f"t{len(seeded)}", bundle=bundle,
                                 agent=agent)
        seeded[row_id] = (user, conv, agent, bundle, ts, uri)
        return row_id

    for day in (day1, day2, day3):
        for user, conv, agent, bundle, hour in (("u1", "c1", "a1", "b1", 9), ("u2", "c2", "a1", "b1", 10),
                                                ("u1", "c1", "a2", "b2", 11), ("u2", "c2", "a2", "b2", 12)):
            await seed(user, conv, agent, bundle, day + timedelta(hours=hour))
    edge_before = await seed("u1", "c1", "a1", "b1", day2 - timedelta(seconds=1))  # day 1, 23:59:59
    edge_after = await seed("u1", "c1", "a2", "b2", day3)                           # day 3, 00:00:00
    recent = {await seed("u1", "c1", "a1", "b1", datetime(2026, 10, 5, 9, tzinfo=UTC)),
              await seed("u2", "c2", "a2", "b2", datetime(2026, 10, 6, 9, tzinfo=UTC))}
    old = set(seeded) - recent

    summary = await retention.archive_before(_night(10, 7))  # the nightly path, cutoff 09-23 02:20
    assert summary["rows"] == len(old) == 14 and summary["bodies_moved"] == 14 and summary["stuck"] == 0
    assert set(await _hot_ids(pool, schema)) == recent
    for row_id, (*_, uri) in seeded.items():
        assert _is_pointer(store, uri) is (row_id in old)  # hot keeps only a pointer for archived bodies

    def text(row_id):
        user, conv, _, _, ts, _ = seeded[row_id]
        return f"body of {user}/{conv} at {ts.isoformat()}"

    async def assert_bodies(records):
        for record in records:
            body = await store.get_message(record["hosted_uri"])
            assert body["storage"] == "cold" and body["text"] == text(record["id"])

    def expect(start, end, **match):
        keys = ("user", "conv", "agent", "bundle")
        rows = [i for i in old if start <= seeded[i][4] < end
                and all(seeded[i][keys.index(k)] == v for k, v in match.items())]
        return sorted(rows, key=lambda i: (seeded[i][4], i))

    # Day 2 only, user u1: exactly u1's day-2 records, in time order; the day boundaries are excluded.
    u1_day2 = await retention.fetch_cold(from_ts=day2, to_ts=day3, user_id="u1")
    assert [r["id"] for r in u1_day2] == expect(day2, day3, user="u1")
    assert len(u1_day2) == 2 and {edge_before, edge_after}.isdisjoint(r["id"] for r in u1_day2)
    assert [r["ts"] for r in u1_day2] == sorted(r["ts"] for r in u1_day2)
    assert all(r["storage"] == "cold" and r["user_id"] == "u1" for r in u1_day2)
    await assert_bodies(u1_day2)

    # Agent, bundle, conversation: exactly the matching subset.
    a2_all = await retention.fetch_cold(from_ts=day1, to_ts=day3 + timedelta(days=1), agent_id="a2")
    assert [r["id"] for r in a2_all] == expect(day1, day3 + timedelta(days=1), agent="a2")
    assert len(a2_all) == 7 and edge_after in {r["id"] for r in a2_all}
    await assert_bodies(a2_all)
    u2_a1_days12 = await retention.fetch_cold(from_ts=day1, to_ts=day3, user_id="u2", agent_id="a1")
    assert [r["id"] for r in u2_a1_days12] == expect(day1, day3, user="u2", agent="a1") and len(u2_a1_days12) == 2
    await assert_bodies(u2_a1_days12)
    b1_c1_day1 = await retention.fetch_cold(from_ts=day1, to_ts=day2, bundle_id="b1", conversation_id="c1")
    assert [r["id"] for r in b1_c1_day1] == expect(day1, day2, bundle="b1", conv="c1") and len(b1_c1_day1) == 2
    await assert_bodies(b1_c1_day1)

    # A range with no archived data, and a scope with none: nothing.
    assert await retention.fetch_cold(from_ts=datetime(2026, 9, 15, tzinfo=UTC),
                                      to_ts=datetime(2026, 9, 20, tzinfo=UTC)) == []
    assert await retention.fetch_cold(from_ts=day1, to_ts=day3, user_id="u1", agent_id="a3") == []
