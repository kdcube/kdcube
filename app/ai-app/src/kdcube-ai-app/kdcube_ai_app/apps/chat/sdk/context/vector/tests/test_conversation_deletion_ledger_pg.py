"""The deletion ledger marks a conversation that is being deleted, on a real PostgreSQL.

Runs only with ``KDCUBE_TEST_POSTGRES_DSN`` (a throwaway database with pgvector).
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def index():
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    schema = f"w40d_{uuid.uuid4().hex[:10]}"
    idx = ConvIndex(pool=pool, schema=schema)
    await idx.ensure_schema()
    try:
        yield idx
    finally:
        async with pool.acquire() as con:
            await con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await pool.close()


async def _ledger(idx: ConvIndex, *, state: str, user: str = "u1", conversation: str = "c1", **scope) -> str:
    deletion_id = f"del_{uuid.uuid4().hex}"
    body = {"user_id": user, "conversation_id": conversation, "bundle_id": None, "tags_all": [], **scope}
    async with idx._pool.acquire() as con:
        await con.execute(
            f"INSERT INTO {idx.schema}.conv_archive_deletions (deletion_id, actor, scope, state)"
            " VALUES ($1, 'u1', ($2::text)::jsonb, $3)", deletion_id, json.dumps(body), state)
    return deletion_id


@pytest.mark.asyncio
@pytest.mark.parametrize("state,scope,is_open", [
    ("started", {}, True),
    ("failed", {}, True),
    ("completed", {}, False),
    ("started", {"bundle_ids": ["app@1-0"]}, True),        # the ingress delete always passes bundle_ids
    ("started", {"bundle_ids": []}, False),                # an empty bundle list deletes nothing
    ("started", {"tags_all": ["turn:t1"]}, False),         # part of a conversation, not all of it
])
async def test_what_counts_as_an_open_whole_conversation_deletion(index, state, scope, is_open) -> None:
    deletion_id = await _ledger(index, state=state, **scope)
    found = await index.open_conversation_deletion(user_id="u1", conversation_id="c1")
    assert (found is not None) is is_open
    if is_open:
        assert found["deletion_id"] == deletion_id and found["state"] == state


@pytest.mark.asyncio
async def test_the_check_is_per_user_and_conversation(index) -> None:
    await _ledger(index, state="started", user="u1", conversation="c1")
    assert await index.open_conversation_deletion(user_id="u1", conversation_id="c1")
    assert await index.open_conversation_deletion(user_id="u2", conversation_id="c1") is None
    assert await index.open_conversation_deletion(user_id="u1", conversation_id="c2") is None


@pytest.mark.asyncio
async def test_the_open_deletions_index_exists_and_serves_the_check(index) -> None:
    async with index._pool.acquire() as con:
        name = f"{index.schema}.idx_{index.schema}_conv_deletions_open"
        assert await con.fetchval("SELECT to_regclass($1)", name)
        for i in range(300):
            await con.execute(
                f"INSERT INTO {index.schema}.conv_archive_deletions (deletion_id, actor, scope, state)"
                " VALUES ($1, 'u', ($2::text)::jsonb, 'completed')",
                f"del_{i}", json.dumps({"user_id": f"u{i}", "conversation_id": "c", "tags_all": []}))
        await con.execute(f"ANALYZE {index.schema}.conv_archive_deletions")
        await con.execute("SET enable_seqscan = off")
        from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import open_conversation_deletion_sql

        plan = "\n".join(r[0] for r in await con.fetch(
            f"EXPLAIN SELECT 1 FROM {index.schema}.conv_archive_deletions d"
            f" WHERE {open_conversation_deletion_sql('d', '$1', '$2')}", "u1", "c1"))
        await con.execute("RESET enable_seqscan")
    assert f"idx_{index.schema}_conv_deletions_open" in plan


async def _set_created(idx: ConvIndex, deletion_id: str, created: str) -> None:
    async with idx._pool.acquire() as con:
        await con.execute(f"UPDATE {idx.schema}.conv_archive_deletions SET created_at = $2::text::timestamptz"
                          " WHERE deletion_id = $1", deletion_id, created)


@pytest.mark.asyncio
async def test_a_message_sent_before_a_completed_deletion_belongs_to_the_deleted_conversation(index) -> None:
    from datetime import datetime, timezone

    deletion_id = await _ledger(index, state="completed")
    await _set_created(index, deletion_id, "2026-11-26T12:00:00Z")
    before = datetime(2026, 11, 26, 11, 59, tzinfo=timezone.utc)
    after = datetime(2026, 11, 26, 12, 1, tzinfo=timezone.utc)
    found = await index.conversation_deletion_since(user_id="u1", conversation_id="c1", since=before)
    assert found and found["deletion_id"] == deletion_id
    # A message sent after the deletion starts a new conversation with that id.
    assert await index.conversation_deletion_since(user_id="u1", conversation_id="c1", since=after) is None
    # Without a send time only an open deletion counts.
    assert await index.conversation_deletion_since(user_id="u1", conversation_id="c1", since=None) is None
    opened = await _ledger(index, state="failed")
    found = await index.conversation_deletion_since(user_id="u1", conversation_id="c1", since=after)
    assert found and found["deletion_id"] == opened


@pytest.mark.asyncio
async def test_the_listing_check_finds_only_open_whole_conversation_deletions(index) -> None:
    await _ledger(index, state="started", conversation="c1")
    await _ledger(index, state="failed", conversation="c2")
    await _ledger(index, state="completed", conversation="c3")
    await _ledger(index, state="started", conversation="c4", tags_all=["turn:t1"])
    await _ledger(index, state="started", user="u2", conversation="c5")
    found = await index.conversations_being_deleted(user_id="u1", conversation_ids=["c1", "c2", "c3", "c4", "c5", "c6"])
    assert found == {"c1", "c2"}
    assert await index.conversations_being_deleted(user_id="u1", conversation_ids=[]) == set()


def _retention(idx: ConvIndex):
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import ConversationRetention

    return ConversationRetention(pool=idx._pool, schema=idx.schema, archive=None)


@pytest.mark.asyncio
async def test_unfinished_deletions_are_listed_oldest_first_with_their_latest_scope(index) -> None:
    first = await _ledger(index, state="failed", conversation="c1", bundle_ids=["a"])
    await _set_created(index, first, "2026-11-26T10:00:00Z")
    latest = await _ledger(index, state="started", conversation="c1", bundle_ids=["a", "b"])
    await _set_created(index, latest, "2026-11-26T11:00:00Z")
    other = await _ledger(index, state="failed", conversation="c2")
    await _set_created(index, other, "2026-11-26T10:30:00Z")
    await _ledger(index, state="completed", conversation="c3")
    await _ledger(index, state="failed", conversation="c4", tags_all=["turn:t1"])
    await _ledger(index, state="failed", conversation="c5", bundle_ids=[])

    listed = await _retention(index).unfinished_conversation_deletions(limit=10)
    assert [(i["conversation_id"], i["attempts"], i["bundle_ids"]) for i in listed] == [
        ("c1", 2, ["a", "b"]), ("c2", 1, None)]
    assert [i["conversation_id"] for i in await _retention(index).unfinished_conversation_deletions(limit=1)] == ["c1"]


@pytest.mark.asyncio
async def test_a_finished_deletion_closes_the_earlier_ones_it_covered(index) -> None:
    narrow = await _ledger(index, state="failed", conversation="c1", bundle_ids=["a"])
    wide = await _ledger(index, state="failed", conversation="c1", bundle_ids=["a", "b"])
    other_user = await _ledger(index, state="failed", user="u2", conversation="c1", bundle_ids=["a"])
    current = await _ledger(index, state="completed", conversation="c1", bundle_ids=["a"])
    closed = await _retention(index)._close_covered_deletions(current, user_id="u1", conversation_id="c1",
                                                             bundle_id=None, bundles=["a"])
    assert closed == 1
    async with index._pool.acquire() as con:
        states = {r["deletion_id"]: (r["state"], r["error"]) for r in await con.fetch(
            f"SELECT deletion_id, state, error FROM {index.schema}.conv_archive_deletions")}
    assert states[narrow] == ("completed", f"finished by {current}")
    assert states[wide][0] == "failed" and states[other_user][0] == "failed"


@pytest.mark.asyncio
async def test_a_background_deletion_never_waits_for_the_retention_lock(index) -> None:
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import ConversationDeleteBusy

    retention = _retention(index)
    async with retention._exclusive(wait_s=30):
        with pytest.raises(ConversationDeleteBusy):
            async with _retention(index)._exclusive(wait_s=0):
                pass
    async with _retention(index)._exclusive(wait_s=0):
        pass
