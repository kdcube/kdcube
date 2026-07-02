# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Phase-4 wiring tests: memory semantic search -> metered embedder + BM25 downgrade.

Exercises the search economics helpers added to the memory entrypoint without
standing up the store/embedder. Methods are called unbound against a light stub.
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

    def __init__(
        self,
        *,
        economics=True,
        user_type="registered",
        user_id="u1",
        reservation=None,
        deny=False,
        identity_authority=None,
        roles=None,
        permissions=None,
    ):
        self.cp_manager = object() if economics else None
        self.rl = object() if economics else None
        self.budget_limiter = object() if economics else None
        self._reservation = reservation
        self._deny = deny
        self.embed_calls: list[str] = []
        self.metered_flows: list[str] = []
        self.metered_subjects: list[str] = []
        self.comm_context = types.SimpleNamespace(
            actor=types.SimpleNamespace(tenant_id="t", project_id="p"),
            user=types.SimpleNamespace(
                user_id=user_id,
                user_type=user_type,
                roles=list(roles or []),
                permissions=list(permissions or []),
                identity_authority=dict(identity_authority or {}),
            ),
        )
        self.comm = types.SimpleNamespace(user_id=user_id)
        self.settings = types.SimpleNamespace(TENANT="t", PROJECT="p")
        self.config = _Config()

    def _memory_widget_config(self):
        return {"search_reservation_amount_dollars": self._reservation} if self._reservation is not None else {}

    async def _memory_embed_one(self, text):
        self.embed_calls.append(text)
        return [0.1, 0.2, 0.3]

    def search_model_service(self, *, flow: str, subject=None):
        if not self._memory_economics_enabled():
            return None

        stub = self
        if subject is not None:
            stub.metered_subjects.append(str(subject.user_id))

        class _ModelService:
            async def embed_search_query(self, text: str, *, flow: str | None = None):
                stub.metered_flows.append(flow or "")
                if stub._deny:
                    raise EconomicsLimitException("rate limited", code="rate_limited")
                stub.embed_calls.append(str(text))
                return [0.1, 0.2, 0.3]

        return _ModelService()

    # delegate to the real mixin implementations
    def _memory_economics_enabled(self):
        return M._memory_economics_enabled(self)

    def _memory_scope(self):
        return M._memory_scope(self)

    def _memory_identity_authority_projection(self):
        return M._memory_identity_authority_projection(self)

    def _memory_effective_user_type(self, default="registered"):
        return M._memory_effective_user_type(self, default)

    def _memory_search_reservation_usd(self, query: str = ""):
        return M._memory_search_reservation_usd(self, query)

    def _memory_search_econ_subject(self):
        return M._memory_search_econ_subject(self)

    async def _memory_search_embed_or_downgrade(self, query):
        return await M._memory_search_embed_or_downgrade(self, query)


def test_search_reservation_usd_from_config_and_default():
    assert M._memory_search_reservation_usd(_StubSearchEP(reservation=0.03)) == 0.03
    # bad value -> price-table estimate floor for text-embedding-3-small
    assert M._memory_search_reservation_usd(_StubSearchEP(reservation="nope"), "hello") == 1e-6


def test_search_subject_uses_session_role():
    subj = M._memory_search_econ_subject(_StubSearchEP(user_type="paid", user_id="u9"))
    assert (subj.tenant, subj.project, subj.user_id) == ("t", "p", "u9")
    assert subj.budget_bypass is None
    assert subj.is_anonymous is False


def test_search_subject_uses_projected_platform_economics_user():
    subj = M._memory_search_econ_subject(
        _StubSearchEP(
            user_type="registered",
            user_id="telegram_100200300",
            identity_authority={
                "actor_user_id": "telegram_100200300",
                "economics_user_id": "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
                "platform_user_id": "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
                "platform_roles": ["kdcube:role:super-admin"],
                "platform_permissions": ["memories:read"],
            },
        )
    )
    assert subj.user_id == "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    assert subj.provenance["actor_user_id"] == "telegram_100200300"
    assert subj.roles == ("kdcube:role:super-admin",)
    assert subj.permissions == ("memories:read",)
    assert subj.budget_bypass is True


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


async def test_metered_embedder_ok_embeds():
    ep = _StubSearchEP(user_type="paid", reservation=0.02)
    out = await ep._memory_search_embed_or_downgrade("query text")
    assert out == [0.1, 0.2, 0.3]
    assert ep.embed_calls == ["query text"]
    assert ep.metered_flows == ["memory.search"]
    assert ep.metered_subjects == ["u1"]


async def test_metered_embedder_receives_projected_platform_subject():
    ep = _StubSearchEP(
        user_type="registered",
        user_id="telegram_100200300",
        reservation=0.02,
        identity_authority={
            "actor_user_id": "telegram_100200300",
            "economics_user_id": "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            "platform_user_id": "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            "platform_roles": ["kdcube:role:super-admin"],
        },
    )
    out = await ep._memory_search_embed_or_downgrade("query text")
    assert out == [0.1, 0.2, 0.3]
    assert ep.metered_subjects == ["a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"]


async def test_economics_limit_downgrades_to_bm25():
    ep = _StubSearchEP(deny=True)
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
