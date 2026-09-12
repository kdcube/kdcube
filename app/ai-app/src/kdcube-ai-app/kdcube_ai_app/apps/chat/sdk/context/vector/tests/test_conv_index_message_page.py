from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex


class _Connection:
    def __init__(self) -> None:
        self.query = ""
        self.args: tuple[object, ...] = ()

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.query = query
        self.args = args
        return []


class _Pool:
    def __init__(self) -> None:
        self.connection = _Connection()

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def _index() -> ConvIndex:
    return ConvIndex(pool=_Pool(), schema="kdcube_test_project")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_message_page_reads_authoritative_rows_in_stable_newest_first_order() -> None:
    index = _index()

    await index.fetch_message_page(
        user_id="user-one",
        conversation_id="conversation-one",
        roles=("user", "assistant"),
        bundle_id="problem-board@1-0",
        agent_id="codex",
        days=3650,
        limit=51,
    )

    query = index._pool.connection.query  # type: ignore[union-attr]
    args = index._pool.connection.args  # type: ignore[union-attr]
    assert "FROM kdcube_test_project.conv_messages" in query
    assert "ORDER BY ts DESC, id DESC" in query
    assert "LIMIT 51" in query
    assert "conversation_id = $4" in query
    assert "bundle_id = $5" in query
    assert "agent_id = $6" in query
    assert "(ts, id) <" not in query
    assert args == (
        "user-one",
        ["user", "assistant"],
        "3650",
        "conversation-one",
        "problem-board@1-0",
        "codex",
    )


@pytest.mark.asyncio
async def test_message_page_continues_with_timestamp_and_row_id_keyset() -> None:
    index = _index()
    before = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)

    await index.fetch_message_page(
        user_id="user-one",
        conversation_id="conversation-one",
        before_ts=before,
        before_id=42,
        limit=20,
    )

    query = index._pool.connection.query  # type: ignore[union-attr]
    args = index._pool.connection.args  # type: ignore[union-attr]
    assert "(ts, id) < ($5::timestamptz, $6::bigint)" in query
    assert "OFFSET" not in query.upper()
    assert args[-2:] == (before, 42)


@pytest.mark.asyncio
async def test_message_page_applies_tag_filters_before_the_keyset() -> None:
    index = _index()

    await index.fetch_message_page(
        user_id="user-one",
        conversation_id="conversation-one",
        all_tags=("pb:project:project-one",),
        not_tags=("pb:kind:internal",),
        before_ts="2026-09-12T03:00:00Z",
        before_id=42,
    )

    query = index._pool.connection.query  # type: ignore[union-attr]
    args = index._pool.connection.args  # type: ignore[union-attr]
    assert "tags @> $5::text[]" in query
    assert "NOT (tags && $6::text[])" in query
    assert "(ts, id) < ($7::timestamptz, $8::bigint)" in query
    assert args[4:6] == (["pb:project:project-one"], ["pb:kind:internal"])


@pytest.mark.asyncio
async def test_message_page_requires_a_complete_continuation_key() -> None:
    index = _index()

    with pytest.raises(ValueError, match="before_ts and before_id"):
        await index.fetch_message_page(
            user_id="user-one",
            conversation_id="conversation-one",
            before_ts="2026-09-12T03:00:00Z",
        )

    with pytest.raises(ValueError, match="before_id must be a positive integer"):
        await index.fetch_message_page(
            user_id="user-one",
            conversation_id="conversation-one",
            before_ts="2026-09-12T03:00:00Z",
            before_id=True,
        )
