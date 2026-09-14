# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Rank-weights plumbing: normalization/clamping, params parsing, and the
forward-only-when-set contract of run_conversation_search."""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import normalize_rank_weights
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ConversationSearchContext,
    ConversationSearchParams,
    run_conversation_search,
)


# ---------------------------------------------------------------------------
# normalize_rank_weights
# ---------------------------------------------------------------------------

def test_normalize_none_and_empty_yield_none():
    assert normalize_rank_weights(None) is None
    assert normalize_rank_weights({}) is None
    assert normalize_rank_weights("not-a-dict") is None
    assert normalize_rank_weights(1.5) is None


def test_normalize_clamps_to_0_2():
    out = normalize_rank_weights({"semantic": 5.0, "lexical": -1.0, "recency": 0.5})
    assert out == {"semantic": 2.0, "lexical": 0.0, "recency": 0.5}


def test_normalize_drops_unknown_keys_and_non_numeric_values():
    assert normalize_rank_weights({"bogus": 1.0}) is None
    out = normalize_rank_weights({"semantic": "1.5", "lexical": "abc", "recency": None})
    assert out == {"semantic": 1.5}


def test_normalize_drops_nan():
    assert normalize_rank_weights({"semantic": float("nan")}) is None


def test_normalize_keeps_explicit_ones():
    # 1.0 is a valid explicit value; only absence means "default".
    assert normalize_rank_weights({"semantic": 1.0}) == {"semantic": 1.0}


# ---------------------------------------------------------------------------
# ConversationSearchParams parsing
# ---------------------------------------------------------------------------

def test_from_tool_params_parses_and_clamps_rank_weights():
    params = ConversationSearchParams.from_tool_params({
        "query": "invoice",
        "rank_weights": {"semantic": 9, "lexical": "0.25", "recency": -3, "junk": 1},
    })
    assert params.rank_weights == {"semantic": 2.0, "lexical": 0.25, "recency": 0.0}


def test_from_tool_params_accepts_weights_alias():
    params = ConversationSearchParams.from_tool_params({
        "query": "invoice",
        "weights": {"recency": 0.0},
    })
    assert params.rank_weights == {"recency": 0.0}


def test_from_tool_params_defaults_rank_weights_to_none():
    assert ConversationSearchParams.from_tool_params({"query": "x"}).rank_weights is None
    assert ConversationSearchParams.from_tool_params({"query": "x", "rank_weights": {}}).rank_weights is None


def test_blank_query_with_temporal_bounds_routes_to_temporal_catalog():
    params = ConversationSearchParams.from_tool_params({
        "query": "",
        "from": "2026-05-01T00:00:00Z",
    })
    assert params.is_catalog() is True
    assert params.effective_mode() == "temporal"


def test_blank_query_without_bounds_is_missing_query_shape():
    params = ConversationSearchParams.from_tool_params({"query": ""})
    assert params.is_catalog() is False
    assert params.effective_mode() == "hybrid"


# ---------------------------------------------------------------------------
# run_conversation_search forwarding
# ---------------------------------------------------------------------------

class _RecordingBackend:
    def __init__(self):
        self.search_kwargs = None
        self.catalog_kwargs = None

    async def search(self, **kwargs):
        self.search_kwargs = kwargs
        return None, []

    async def search_turn_catalog(self, **kwargs):
        self.catalog_kwargs = kwargs
        return []

    async def get_turn_log(self, *, turn_id, conversation_id=None, bundle_id=None):
        return {}


@pytest.mark.asyncio
async def test_rank_weights_not_forwarded_when_unset():
    backend = _RecordingBackend()
    context = ConversationSearchContext(user_id="u1", conversation_id="c1")
    params = ConversationSearchParams(query="invoice", targets=["summary"])

    await run_conversation_search(context=context, params=params, search_backend=backend)

    # Byte-identical call shape for backends without the knob.
    assert "rank_weights" not in backend.search_kwargs


@pytest.mark.asyncio
async def test_rank_weights_forwarded_when_set():
    backend = _RecordingBackend()
    context = ConversationSearchContext(user_id="u1", conversation_id="c1")
    params = ConversationSearchParams(
        query="invoice",
        targets=["summary"],
        rank_weights={"semantic": 0.5, "recency": 0.0},
    )

    await run_conversation_search(context=context, params=params, search_backend=backend)

    assert backend.search_kwargs["rank_weights"] == {"semantic": 0.5, "recency": 0.0}


@pytest.mark.asyncio
async def test_blank_query_with_range_runs_catalog_and_ignores_rank_weights():
    backend = _RecordingBackend()
    context = ConversationSearchContext(user_id="u1", conversation_id="c1")
    params = ConversationSearchParams.from_tool_params({
        "query": "",
        "from": "2026-05-01T00:00:00Z",
        "to": "2026-06-01T00:00:00Z",
        "rank_weights": {"recency": 0.0},
    })

    result = await run_conversation_search(context=context, params=params, search_backend=backend)

    assert result.effective_mode == "temporal"
    assert result.missing_query is False
    assert backend.search_kwargs is None  # no hybrid search happened
    assert backend.catalog_kwargs["from_ts"] == "2026-05-01T00:00:00Z"
    assert backend.catalog_kwargs["to_ts"] == "2026-06-01T00:00:00Z"


@pytest.mark.asyncio
async def test_blank_query_without_range_flags_missing_query():
    backend = _RecordingBackend()
    context = ConversationSearchContext(user_id="u1", conversation_id="c1")
    params = ConversationSearchParams(query="", targets=["summary"])

    result = await run_conversation_search(context=context, params=params, search_backend=backend)

    assert result.missing_query is True
    assert backend.search_kwargs is None
    assert backend.catalog_kwargs is None
