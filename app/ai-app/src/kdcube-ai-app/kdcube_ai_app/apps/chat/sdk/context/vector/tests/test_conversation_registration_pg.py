"""One conversation-state row per (user, conversation), on a real PostgreSQL.

Runs only with ``KDCUBE_TEST_POSTGRES_DSN`` (a throwaway database with pgvector).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import CONV_STATE_KIND, ConvIndex

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def index():
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    schema = f"w40_{uuid.uuid4().hex[:10]}"
    idx = ConvIndex(pool=pool, schema=schema)
    try:
        yield idx
    finally:
        async with pool.acquire() as con:
            await con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await pool.close()


async def _state_rows(idx: ConvIndex, user: str, conversation: str) -> list:
    async with idx._pool.acquire() as con:
        return await con.fetch(
            f"SELECT id, tags, bundle_id FROM {idx.schema}.conv_messages "
            "WHERE user_id=$1 AND conversation_id=$2 AND role='artifact' AND tags @> ARRAY[$3]::text[]",
            user, conversation, CONV_STATE_KIND)


async def _insert_raw_state_row(idx: ConvIndex, user: str, conversation: str, ts: str) -> int:
    async with idx._pool.acquire() as con:
        return await con.fetchval(
            f"INSERT INTO {idx.schema}.conv_messages (user_id, conversation_id, role, text, hosted_uri, bundle_id,"
            " ts, ttl_days, user_type, tags) VALUES ($1,$2,'artifact','','s3://x','app@1-0',$3::text::timestamptz,3650,"
            "'system',$4) RETURNING id", user, conversation, ts, [CONV_STATE_KIND, "conv.state:idle"])


@pytest.mark.asyncio
async def test_existing_duplicates_are_reduced_to_the_newest_before_the_index(index) -> None:
    await index.ensure_schema()
    async with index._pool.acquire() as con:
        await con.execute(f"DROP INDEX {index.schema}.idx_{index.schema}_conv_state_one")
    older = await _insert_raw_state_row(index, "u1", "c1", "2026-11-01T00:00:00Z")
    newest = await _insert_raw_state_row(index, "u1", "c1", "2026-11-02T00:00:00Z")
    kept_other_user = await _insert_raw_state_row(index, "u2", "c1", "2026-11-01T00:00:00Z")

    await index.ensure_schema()

    assert [r["id"] for r in await _state_rows(index, "u1", "c1")] == [newest]
    assert [r["id"] for r in await _state_rows(index, "u2", "c1")] == [kept_other_user]
    assert older != newest
    async with index._pool.acquire() as con:
        assert await con.fetchval("SELECT to_regclass($1)", f"{index.schema}.idx_{index.schema}_conv_state_one")


@pytest.mark.asyncio
async def test_a_duplicate_an_edge_references_is_kept_and_the_index_is_not_created(index) -> None:
    await index.ensure_schema()
    async with index._pool.acquire() as con:
        await con.execute(f"DROP INDEX {index.schema}.idx_{index.schema}_conv_state_one")
    older = await _insert_raw_state_row(index, "u1", "c1", "2026-11-01T00:00:00Z")
    await _insert_raw_state_row(index, "u1", "c1", "2026-11-02T00:00:00Z")
    async with index._pool.acquire() as con:
        await con.execute(f"INSERT INTO {index.schema}.conv_artifact_edges (from_id, to_id) VALUES ($1, $1)", older)

    await index.ensure_schema()

    assert len(await _state_rows(index, "u1", "c1")) == 2
    async with index._pool.acquire() as con:
        assert await con.fetchval("SELECT to_regclass($1)", f"{index.schema}.idx_{index.schema}_conv_state_one") is None


@pytest.mark.asyncio
async def test_concurrent_first_writers_leave_one_state_row(index) -> None:
    await index.ensure_schema()

    async def turn(n: int):
        return await index.try_set_conversation_state_cas(
            user_id="u1", conversation_id="c-race", new_state="in_progress", hosted_uri=f"s3://t{n}",
            now_ts="2026-11-25T12:00:00Z", bundle_id="app@1-0", last_turn_id=f"t{n}")

    async def pick(n: int):
        return await index.register_conversation_state(
            user_id="u1", conversation_id="c-race", bundle_id="app@1-0", hosted_uri=f"s3://p{n}",
            now_ts="2026-11-25T12:00:00Z")

    results = await asyncio.gather(*[turn(n) for n in range(6)], *[pick(n) for n in range(6)])

    assert len(await _state_rows(index, "u1", "c-race")) == 1
    assert all(r and (r.get("ok") is not False) for r in results[:6])


@pytest.mark.asyncio
async def test_registration_never_overwrites_a_running_turn(index) -> None:
    await index.ensure_schema()
    await index.try_set_conversation_state_cas(
        user_id="u1", conversation_id="c1", new_state="in_progress", hosted_uri="s3://t",
        now_ts="2026-11-25T12:00:00Z", bundle_id="app@1-0", last_turn_id="t1")

    row = await index.register_conversation_state(
        user_id="u1", conversation_id="c1", bundle_id="other@1-0", hosted_uri="s3://p",
        now_ts="2026-11-25T12:01:00Z")

    assert "conv.state:in_progress" in row["tags"] and "conv.turn:t1" in row["tags"]
    assert row["bundle_id"] == "app@1-0"


@pytest.mark.asyncio
async def test_a_conversation_with_only_its_state_row_is_not_listed(index) -> None:
    await index.ensure_schema()
    await index.register_conversation_state(
        user_id="u1", conversation_id="c-picked", bundle_id="app@1-0", hosted_uri="s3://p",
        now_ts="2026-11-25T12:00:00Z")
    await index.register_conversation_state(
        user_id="u1", conversation_id="c-talked", bundle_id="app@1-0", hosted_uri="s3://p",
        now_ts="2026-11-25T12:00:00Z")
    async with index._pool.acquire() as con:
        await con.execute(
            f"INSERT INTO {index.schema}.conv_messages (user_id, conversation_id, role, text, hosted_uri, bundle_id,"
            " ts, ttl_days, user_type, tags) VALUES ('u1','c-talked','user','hello','s3://m','app@1-0',now(),30,"
            "'registered','{}')")

    listed = [r["conversation_id"] for r in await index.list_user_conversations(user_id="u1")]

    assert listed == ["c-talked"]
