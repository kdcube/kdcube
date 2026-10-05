# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""W536: a dated conversation search reaches the cold tier, and every hit names its storage.

* `ConvIndex.search_cold_turns` reads archived turns only through a lower
  date bound, under the hot index's scope rules, one row per turn, each
  marked `storage: "cold"`.
* `search_context` fuses that arm by rank with the hot arms; an archived turn
  reads `cold`, every index row `hot`, and a ConvIndex without the arm still
  searches.
* The search API and the ingress hit carry the backend's `storage`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kdcube_ai_app.apps.chat.ingress.conversations.search import _shape_hit
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import search_context

NOW = datetime.now(timezone.utc)
FROM = (NOW - timedelta(days=30)).isoformat()


def _record(turn_id, text, *, days_ago=20, role="assistant", tags=(), conversation_id="conv-1",
            user_id="u1", bundle_id="b1", agent_id=None, ttl_days=365, rid=None):
    return {
        "id": rid or hash((turn_id, text)) & 0xFFFF,
        "message_id": f"m-{turn_id}",
        "user_id": user_id,
        "conversation_id": conversation_id,
        "bundle_id": bundle_id,
        "agent_id": agent_id,
        "role": role,
        "text": text,
        "hosted_uri": f"s3://cold/{turn_id}",
        "ts": (NOW - timedelta(days=days_ago)).isoformat(),
        "tags": list(tags),
        "turn_id": turn_id,
        "ttl_days": ttl_days,
    }


class FakeRetention:
    """The retention ledger's fetch_cold contract: scope already applied, storage set."""

    def __init__(self, records):
        self.records = records
        self.calls = []

    async def fetch_cold(self, **kwargs):
        self.calls.append(kwargs)
        out = []
        for r in self.records:
            ts = datetime.fromisoformat(r["ts"])
            if ts < kwargs["from_ts"] or (kwargs.get("to_ts") is not None and ts >= kwargs["to_ts"]):
                continue
            if kwargs.get("user_id") and r["user_id"] != kwargs["user_id"]:
                continue
            if kwargs.get("conversation_id") and r["conversation_id"] != kwargs["conversation_id"]:
                continue
            if kwargs.get("roles") and r["role"] not in kwargs["roles"]:
                continue
            out.append({**r, "storage": "cold"})
        return out


def _index(records):
    index = object.__new__(ConvIndex)
    index.cold_retention = FakeRetention(records)
    return index


async def _cold(index, **overrides):
    kwargs = dict(
        user_id="u1", conversation_id="conv-1", query_text="migration receipt",
        search_roles=("assistant",), top_k=5, scope="user",
        timestamp_filters=[{"op": ">=", "value": FROM}],
    )
    kwargs.update(overrides)
    return await index.search_cold_turns(**kwargs)


@pytest.mark.asyncio
async def test_the_cold_arm_needs_a_lower_date_bound():
    index = _index([_record("t1", "the migration receipt")])
    assert await _cold(index, timestamp_filters=None) == []
    assert await _cold(index, timestamp_filters=[{"op": "<=", "value": NOW.isoformat()}]) == []
    assert index.cold_retention.calls == [], "no date bound reads no part"


@pytest.mark.asyncio
async def test_cold_turns_rank_by_query_terms_one_row_per_turn():
    index = _index([
        _record("t1", "the migration step", rid=1),
        _record("t1", "the migration receipt is renamed", rid=2),
        _record("t2", "receipt only", rid=3),
        _record("t3", "nothing relevant", rid=4),
    ])
    rows = await _cold(index)

    assert [r["turn_id"] for r in rows] == ["t1", "t2"]
    assert rows[0]["text"] == "the migration receipt is renamed"
    assert {r["storage"] for r in rows} == {"cold"}
    call = index.cold_retention.calls[0]
    assert call["from_ts"].isoformat() == FROM and call["to_ts"] is None
    # Cross-conversation (user) scope does not narrow to the current conversation.
    assert call["conversation_id"] is None


@pytest.mark.asyncio
async def test_cold_turns_keep_the_hot_scope_rules():
    index = _index([
        _record("t1", "migration receipt", conversation_id="conv-2"),
        _record("t2", "migration receipt", tags=["kind:react.recovery.session"]),
        _record("t3", "migration receipt", tags=["kind:working.summary"]),
        _record("t4", "migration receipt", role="user"),
    ])
    scoped = await _cold(index, scope="conversation")
    # conv-1 only; the recovery session and the user-role row stay out.
    assert [r["turn_id"] for r in scoped] == ["t3"]
    assert {r["turn_id"] for r in await _cold(index)} == {"t1", "t3"}, "recovery sessions stay out"
    tagged = await _cold(index, search_tags=["kind:working.summary"])
    assert [r["turn_id"] for r in tagged] == ["t3"]
    upper = await _cold(index, timestamp_filters=[
        {"op": ">=", "value": FROM}, {"op": "<=", "value": (NOW - timedelta(days=25)).isoformat()},
    ])
    assert upper == []
    assert index.cold_retention.calls[-1]["to_ts"] is not None


class FakeConvIndex:
    def __init__(self, *, lex, cold=None):
        self._lex = lex
        self._cold = cold

    async def search_turn_logs_via_content(self, **kwargs):
        return []

    async def search_turn_logs_via_content_lexical(self, **kwargs):
        return list(self._lex)

    async def search_turn_logs_via_content_trigram(self, **kwargs):
        return []


class FakeConvIndexWithCold(FakeConvIndex):
    async def search_cold_turns(self, **kwargs):
        self.cold_kwargs = kwargs
        return list(self._cold or [])


def _hot_row(turn_id):
    return {"turn_id": turn_id, "conversation_id": "conv-1", "role": "assistant",
            "ts": NOW.isoformat(), "rec": 1.0, "sim": 0.0, "score": 0.0, "text": "hot"}


async def _search(conv_idx, **overrides):
    kwargs = dict(
        conv_idx=conv_idx, ctx_client=object(), model_service=None,
        targets=[{"where": "assistant", "query": "migration receipt"}],
        user="u1", conv="conv-1", scope="user", top_k=10, scoring_mode="rrf_hybrid",
        timestamp_filters=[{"op": ">=", "value": FROM}],
    )
    kwargs.update(overrides)
    return await search_context(**kwargs)


@pytest.mark.asyncio
async def test_search_context_fuses_cold_turns_and_names_each_hits_storage():
    cold = {**_record("t-cold", "migration receipt"), "storage": "cold", "rec": 0.1}
    conv_idx = FakeConvIndexWithCold(lex=[_hot_row("t-hot")], cold=[cold])
    _, hits = await _search(conv_idx)

    by_turn = {h["turn_id"]: h for h in hits}
    assert by_turn["t-hot"]["storage"] == "hot"
    assert by_turn["t-cold"]["storage"] == "cold"
    assert by_turn["t-cold"]["cold_rank"] == 1 and by_turn["t-cold"]["primary_source"] == "cold"
    assert conv_idx.cold_kwargs["timestamp_filters"] == [{"op": ">=", "value": FROM}]


@pytest.mark.asyncio
async def test_an_index_without_the_cold_arm_still_searches_hot():
    _, hits = await _search(FakeConvIndex(lex=[_hot_row("t-hot")]))
    assert [(h["turn_id"], h["storage"]) for h in hits] == [("t-hot", "hot")]


def test_the_ingress_hit_carries_the_backends_storage():
    assert _shape_hit({"conversation_id": "c", "turn_id": "t", "storage": "cold"}).storage == "cold"
    assert _shape_hit({"conversation_id": "c", "turn_id": "t"}).storage is None
