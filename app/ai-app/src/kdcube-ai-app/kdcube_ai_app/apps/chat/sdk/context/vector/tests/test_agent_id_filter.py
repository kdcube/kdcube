# SPDX-License-Identifier: MIT

"""agent_id storage semantics + the optional read-side filter.

Covers the spec rule "if no agent_id filter is given, it is not applied", for the
two memsearch call sites (ctx_browser.search / ctx_browser.search_turn_catalog)
and the ConvIndex SQL builders they delegate to.
"""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.event_identity import index_agent_id
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.solutions.react import browser as browser_mod
from kdcube_ai_app.apps.chat.sdk.solutions.react.browser import ContextBrowser


# --------------------------------------------------------------------------- #
# index_agent_id — storage value is the agent id as-is, None only when absent
# --------------------------------------------------------------------------- #

def test_index_agent_id_stores_value_as_is():
    assert index_agent_id("main") == "main"
    assert index_agent_id("research.react.agent") == "research.react.agent"
    # the platform default is a real value, NOT collapsed to NULL
    assert index_agent_id("default.react.agent") == "default.react.agent"


def test_index_agent_id_none_only_when_absent():
    assert index_agent_id(None) is None
    assert index_agent_id("") is None
    assert index_agent_id("   ") is None


def test_index_agent_id_strips_whitespace():
    assert index_agent_id("  main  ") == "main"


# --------------------------------------------------------------------------- #
# Fake asyncpg pool: capture the SQL text + bound args of the last query
# --------------------------------------------------------------------------- #

class _FakeConn:
    def __init__(self):
        self.calls: list[tuple[str, list]] = []

    async def fetch(self, q, *args):
        self.calls.append((q, list(args)))
        return []

    async def fetchrow(self, q, *args):
        self.calls.append((q, list(args)))
        return {"id": 1}


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self):
        self.conn = _FakeConn()

    def acquire(self):
        return _AcquireCtx(self.conn)


def _make_idx() -> ConvIndex:
    # Bypass __init__ (it reads settings) and inject only what the SQL builders use.
    idx = object.__new__(ConvIndex)
    idx._pool = _FakePool()
    idx.schema = "kdcube_t_p"
    idx.shared_pool = True
    return idx


# The WHERE clause is `m.agent_id = $N`; the SELECT lists `m.agent_id,` / `o.agent_id,`
# unconditionally, so we match the filter form specifically.
_FILTER = "m.agent_id = $"
_NAMED = "research.agent"
_BUNDLE = "problem-board@1-0"


# --------------------------------------------------------------------------- #
# ConvIndex.fetch_turn_catalog  (backs ctx_browser.search_turn_catalog)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_fetch_turn_catalog_no_filter_when_agent_absent():
    idx = _make_idx()
    await idx.fetch_turn_catalog(
        user_id="u", conversation_id="c",
        ctx={"user_id": "u", "conversation_id": "c"},
    )
    q, args = idx._pool.conn.calls[-1]
    assert _FILTER not in q
    assert _NAMED not in args


@pytest.mark.asyncio
async def test_fetch_turn_catalog_filters_when_agent_present():
    idx = _make_idx()
    await idx.fetch_turn_catalog(
        user_id="u", conversation_id="c", agent_id=_NAMED,
        ctx={"user_id": "u", "conversation_id": "c"},
    )
    q, args = idx._pool.conn.calls[-1]
    assert _FILTER in q
    assert _NAMED in args


@pytest.mark.asyncio
async def test_fetch_turn_catalog_filters_when_bundle_present():
    idx = _make_idx()
    await idx.fetch_turn_catalog(
        user_id="u",
        conversation_id="c",
        bundle_id=_BUNDLE,
        ctx={"user_id": "u", "conversation_id": "c"},
    )
    q, args = idx._pool.conn.calls[-1]
    assert "m.bundle_id = $" in q
    assert _BUNDLE in args


# --------------------------------------------------------------------------- #
# ConvIndex.search_turn_logs_via_content  (backs ctx_browser.search)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_search_turn_logs_no_filter_when_agent_absent():
    idx = _make_idx()
    await idx.search_turn_logs_via_content(user_id="u", conversation_id="c", scope="user")
    q, args = idx._pool.conn.calls[-1]
    assert _FILTER not in q
    assert _NAMED not in args


@pytest.mark.asyncio
async def test_search_turn_logs_filters_when_agent_present():
    idx = _make_idx()
    await idx.search_turn_logs_via_content(
        user_id="u", conversation_id="c", scope="user", agent_id=_NAMED,
    )
    q, args = idx._pool.conn.calls[-1]
    assert _FILTER in q
    assert _NAMED in args


@pytest.mark.asyncio
async def test_search_turn_logs_filters_when_bundle_present():
    idx = _make_idx()
    await idx.search_turn_logs_via_content(
        user_id="u",
        conversation_id="c",
        scope="user",
        bundle_id=_BUNDLE,
    )
    q, args = idx._pool.conn.calls[-1]
    assert "m.bundle_id = $" in q
    assert _BUNDLE in args


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "extra"),
    [
        ("search_turn_logs_via_content", {}),
        ("search_turn_logs_via_content_lexical", {"query_text": "find message"}),
        ("search_turn_logs_via_content_trigram", {"query_text": "find message"}),
    ],
)
async def test_search_arms_keep_indexed_turn_when_rich_turn_log_is_absent(method_name, extra):
    idx = _make_idx()
    method = getattr(idx, method_name)
    await method(user_id="u", conversation_id="c", scope="user", **extra)
    q, _args = idx._pool.conn.calls[-1]
    assert "LEFT JOIN LATERAL" in q
    assert "COALESCE(log.text, ut.matched_text) AS text" in q
    assert "ut.matched_text AS matched_text" in q
    assert "COALESCE(log.turn_id, ut.turn_id) AS turn_id" in q
    assert "bundle_id IS NOT DISTINCT FROM ut.matched_bundle_id" in q


# --------------------------------------------------------------------------- #
# ContextBrowser delegation — the exact memsearch call sites
# --------------------------------------------------------------------------- #

class _RecordingIdx:
    def __init__(self):
        self.kwargs: dict = {}

    async def fetch_turn_catalog(self, **kwargs):
        self.kwargs = kwargs
        return []


@pytest.mark.asyncio
async def test_ctx_browser_search_turn_catalog_defaults_agent_none():
    idx = _RecordingIdx()
    cb = ContextBrowser()
    await cb.search_turn_catalog(user="u", conv="c", conv_idx=idx)
    assert idx.kwargs["agent_id"] is None


@pytest.mark.asyncio
async def test_ctx_browser_search_turn_catalog_forwards_agent():
    idx = _RecordingIdx()
    cb = ContextBrowser()
    await cb.search_turn_catalog(user="u", conv="c", conv_idx=idx, agent_id=_NAMED)
    assert idx.kwargs["agent_id"] == _NAMED


@pytest.mark.asyncio
async def test_ctx_browser_search_turn_catalog_forwards_bundle():
    idx = _RecordingIdx()
    cb = ContextBrowser()
    await cb.search_turn_catalog(user="u", conv="c", conv_idx=idx, bundle_id=_BUNDLE)
    assert idx.kwargs["bundle_id"] == _BUNDLE


@pytest.mark.asyncio
async def test_ctx_browser_search_defaults_agent_none(monkeypatch):
    recorded: dict = {}

    async def _fake_search_context(**kwargs):
        recorded.update(kwargs)
        return (None, [])

    monkeypatch.setattr(browser_mod, "search_context", _fake_search_context)
    cb = ContextBrowser()
    await cb.search(targets=[], user="u", conv="c", conv_idx=object(), model_service=object())
    assert recorded["agent_id"] is None


@pytest.mark.asyncio
async def test_ctx_browser_search_forwards_agent(monkeypatch):
    recorded: dict = {}

    async def _fake_search_context(**kwargs):
        recorded.update(kwargs)
        return (None, [])

    monkeypatch.setattr(browser_mod, "search_context", _fake_search_context)
    cb = ContextBrowser()
    await cb.search(
        targets=[], user="u", conv="c", conv_idx=object(), model_service=object(),
        agent_id=_NAMED,
    )
    assert recorded["agent_id"] == _NAMED


@pytest.mark.asyncio
async def test_ctx_browser_search_forwards_bundle(monkeypatch):
    recorded: dict = {}

    async def _fake_search_context(**kwargs):
        recorded.update(kwargs)
        return (None, [])

    monkeypatch.setattr(browser_mod, "search_context", _fake_search_context)
    cb = ContextBrowser()
    await cb.search(
        targets=[], user="u", conv="c", conv_idx=object(), model_service=object(),
        bundle_id=_BUNDLE,
    )
    assert recorded["bundle_id"] == _BUNDLE
