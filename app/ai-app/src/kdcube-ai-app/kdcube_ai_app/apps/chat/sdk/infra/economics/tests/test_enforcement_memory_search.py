# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Phase-4 wiring tests: memory semantic search -> economic_preflight + BM25 downgrade.

Exercises the search economics helpers added to the memory entrypoint without
standing up the store/embedder. `economic_preflight` is monkeypatched to drive
the allow / limit branches; methods are called unbound against a light stub.
"""

from __future__ import annotations

import types

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    MemoryEntrypointMixin as M,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics import enforcement as enf
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException


class _Spec:
    id = "b@1"


class _Config:
    ai_bundle_spec = _Spec()


class _StubSearchEP:
    """Minimal stand-in for the memory entrypoint, synchronous (widget) shape."""

    def __init__(self, *, economics=True, user_type="registered", user_id="u1", reservation=0.01):
        self.cp_manager = object() if economics else None
        self.rl = object() if economics else None
        self.budget_limiter = object() if economics else None
        self._reservation = reservation
        self.embed_calls: list[str] = []
        self.comm_context = types.SimpleNamespace(
            actor=types.SimpleNamespace(tenant_id="t", project_id="p"),
            user=types.SimpleNamespace(user_id=user_id, user_type=user_type),
        )
        self.comm = types.SimpleNamespace(user_id=user_id)
        self.settings = types.SimpleNamespace(TENANT="t", PROJECT="p")
        self.config = _Config()

    def _memory_widget_config(self):
        return {"search_reservation_amount_dollars": self._reservation}

    async def _memory_embed_one(self, text):
        self.embed_calls.append(text)
        return [0.1, 0.2, 0.3]

    # delegate to the real mixin implementations
    def _memory_economics_enabled(self):
        return M._memory_economics_enabled(self)

    def _memory_scope(self):
        return M._memory_scope(self)

    def _memory_effective_user_type(self, default="registered"):
        return M._memory_effective_user_type(self, default)

    def _memory_search_reservation_usd(self):
        return M._memory_search_reservation_usd(self)

    def _memory_search_econ_subject(self):
        return M._memory_search_econ_subject(self)

    async def _memory_search_embed_or_downgrade(self, query):
        return await M._memory_search_embed_or_downgrade(self, query)


def test_search_reservation_usd_from_config_and_default():
    assert M._memory_search_reservation_usd(_StubSearchEP(reservation=0.03)) == 0.03
    # bad value -> default
    assert M._memory_search_reservation_usd(_StubSearchEP(reservation="nope")) == 0.01


def test_search_subject_uses_session_role():
    subj = M._memory_search_econ_subject(_StubSearchEP(user_type="paid", user_id="u9"))
    assert (subj.tenant, subj.project, subj.user_id) == ("t", "p", "u9")
    assert subj.user_type == "paid"


async def test_empty_query_skips_embed_and_preflight(monkeypatch):
    called = {"preflight": 0}

    async def _pf(*a, **k):
        called["preflight"] += 1

    monkeypatch.setattr(enf, "economic_preflight", _pf)
    ep = _StubSearchEP()
    assert await ep._memory_search_embed_or_downgrade("   ") is None
    assert ep.embed_calls == []
    assert called["preflight"] == 0


async def test_economics_disabled_embeds_without_preflight(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("preflight must not run when economics disabled")

    monkeypatch.setattr(enf, "economic_preflight", _boom)
    ep = _StubSearchEP(economics=False)
    out = await ep._memory_search_embed_or_downgrade("hello")
    assert out == [0.1, 0.2, 0.3]
    assert ep.embed_calls == ["hello"]


async def test_preflight_ok_embeds(monkeypatch):
    seen = {}

    async def _pf(entrypoint, *, subject, estimate, flow, policy=None):
        seen["flow"] = flow
        seen["reservation"] = estimate.reservation_usd
        seen["role"] = subject.user_type
        return object()

    monkeypatch.setattr(enf, "economic_preflight", _pf)
    ep = _StubSearchEP(user_type="paid", reservation=0.02)
    out = await ep._memory_search_embed_or_downgrade("query text")
    assert out == [0.1, 0.2, 0.3]
    assert ep.embed_calls == ["query text"]
    assert seen == {"flow": "memory.search", "reservation": 0.02, "role": "paid"}


async def test_economics_limit_downgrades_to_bm25(monkeypatch):
    async def _pf(*a, **k):
        raise EconomicsLimitException("rate limited", code="rate_limited")

    monkeypatch.setattr(enf, "economic_preflight", _pf)
    ep = _StubSearchEP()
    out = await ep._memory_search_embed_or_downgrade("query text")
    assert out is None             # query_embedding=None -> store falls back to FTS/BM25
    assert ep.embed_calls == []    # embedding cost was NOT paid


async def test_anonymous_skips_preflight_and_embeds(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("preflight must not run for anonymous user")

    monkeypatch.setattr(enf, "economic_preflight", _boom)
    ep = _StubSearchEP(user_id="anonymous")
    out = await ep._memory_search_embed_or_downgrade("query text")
    assert out == [0.1, 0.2, 0.3]
    assert ep.embed_calls == ["query text"]
