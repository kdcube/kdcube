# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Tests for the conversation search API's EXPLICIT calling context.

The point of api.py is that identity is set by the caller, not read off ambient
runtime/contextvars. These tests drive `run_conversation_search` directly with a
`ConversationSearchContext` and a fake backend — there is no `RuntimeCtx`, no
`ctx_browser.runtime_ctx`, and no contextvar in scope.
"""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ConversationSearchContext,
    ConversationSearchParams,
    run_conversation_search,
)


class FakeBackend:
    """Satisfies the ConversationSearchBackend protocol; records its kwargs."""

    def __init__(self):
        self.search_kwargs = {}
        self.catalog_kwargs = {}
        self._turn_logs = {}

    async def search(self, **kwargs):
        self.search_kwargs = kwargs
        return "turn_prev", [{
            "turn_id": "turn_prev",
            "conversation_id": kwargs.get("conv"),
            "score": 0.9,
            "sim": 0.88,
            "rec": 0.95,
            "matched_via_role": "assistant",
            "source_query": "invoice",
            "ts": "2026-05-05T10:00:00Z",
        }]

    async def search_turn_catalog(self, **kwargs):
        self.catalog_kwargs = kwargs
        return [{
            "turn_id": "turn_second",
            "turn_index_path": "ar:turn_second.react.turn.index",
            "working_summary_path": "ws:turn_second.conv.working.summary",
            "user_path": "ar:turn_second.user.prompt",
            "ordinal": 2,
            "total_turns": 8,
            "started_at": "2026-05-03T01:17:11Z",
            "working_summary_text": "Goal: find medicine news. Outcome: answered.",
            "first_user_text": "check the medicine news",
        }]

    async def get_turn_log(self, *, turn_id, conversation_id=None):
        return self._turn_logs.get((conversation_id or "", turn_id), self._turn_logs.get(turn_id, {}))


@pytest.mark.asyncio
async def test_hybrid_search_uses_explicit_context_no_ambient_state():
    backend = FakeBackend()
    backend._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "conv.working.summary",
                "turn_id": "turn_prev",
                "ts": "2026-05-05T10:00:00Z",
                "path": "ws:turn_prev.conv.working.summary",
                "text": "Goal: retrieve invoices.",
                "meta": {},
            }
        ],
        "sources_pool": [],
    }
    # Identity is set explicitly — there is no RuntimeCtx and no contextvar.
    context = ConversationSearchContext(
        user_id="user_explicit",
        conversation_id="conv_explicit",
        turn_id="turn_now",
        bundle_id="bundle_x",
        tenant="tenant_a",
        project="project_b",
    )
    params = ConversationSearchParams(query="invoice", targets=["summary"], top_k=3)

    result = await run_conversation_search(context=context, params=params, search_backend=backend)

    # The explicit identity flowed into the backend call.
    assert backend.search_kwargs["user"] == "user_explicit"
    assert backend.search_kwargs["conv"] == "conv_explicit"
    assert backend.search_kwargs["scope"] == "conversation"
    assert backend.search_kwargs["targets"] == [{"where": "assistant", "query": "invoice"}]

    assert not result.missing_query
    assert result.effective_mode == "hybrid"
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit["turn_id"] == "turn_prev"
    assert hit["snippets"][0]["role"] == "summary"
    assert "invoices" in hit["snippets"][0]["text"]


@pytest.mark.asyncio
async def test_catalog_ordinal_routes_to_turn_catalog_without_query():
    backend = FakeBackend()
    context = ConversationSearchContext(user_id="u1", conversation_id="c1", turn_id="t_now")
    params = ConversationSearchParams.from_tool_params({
        "query": "",
        "targets": ["summary", "user"],
        "mode": "ordinal",
        "ordinal": 2,
    })

    result = await run_conversation_search(context=context, params=params, search_backend=backend)

    assert backend.catalog_kwargs["ordinal"] == 2
    assert backend.catalog_kwargs["user"] == "u1"
    assert backend.catalog_kwargs["conv"] == "c1"
    assert backend.catalog_kwargs["days"] == 3650
    assert result.effective_mode == "ordinal"
    assert result.hits[0]["turn_id"] == "turn_second"
    assert result.hits[0]["ordinal"] == 2
    assert [sn["role"] for sn in result.hits[0]["snippets"]] == ["summary", "user"]


@pytest.mark.asyncio
async def test_missing_query_is_flagged_not_raised():
    backend = FakeBackend()
    context = ConversationSearchContext(user_id="u1", conversation_id="c1")
    params = ConversationSearchParams(query="", targets=["summary"])

    result = await run_conversation_search(context=context, params=params, search_backend=backend)

    assert result.missing_query is True
    assert result.hits == []
    # No backend search was attempted.
    assert backend.search_kwargs == {}
    assert backend.catalog_kwargs == {}


@pytest.mark.asyncio
async def test_user_scope_is_forwarded_and_temporal_bounds_become_filters():
    backend = FakeBackend()
    backend._turn_logs[("conv_other", "turn_prev")] = {"blocks": [], "sources_pool": []}
    context = ConversationSearchContext(user_id="u1", conversation_id="conv_current")
    params = ConversationSearchParams.from_tool_params({
        "query": "invoice",
        "targets": ["summary"],
        "scope": "user",
        "from": "2026-03-01T00:00:00Z",
        "to": "2026-04-01T00:00:00Z",
    })

    await run_conversation_search(context=context, params=params, search_backend=backend)

    assert backend.search_kwargs["scope"] == "user"
    assert backend.search_kwargs["days"] == 3650
    assert backend.search_kwargs["timestamp_filters"] == [
        {"op": ">=", "value": "2026-03-01T00:00:00Z"},
        {"op": "<", "value": "2026-04-01T00:00:00Z"},
    ]


def test_context_from_runtime_ctx_captures_identity():
    class FakeRuntime:
        user_id = "ru"
        conversation_id = "rc"
        turn_id = "rt"
        bundle_id = "rb"
        tenant = "rten"
        project = "rproj"

    ctx = ConversationSearchContext.from_runtime_ctx(FakeRuntime())
    assert ctx.user_id == "ru"
    assert ctx.conversation_id == "rc"
    assert ctx.turn_id == "rt"
    assert ctx.bundle_id == "rb"
    assert ctx.tenant == "rten"
    assert ctx.project == "rproj"
