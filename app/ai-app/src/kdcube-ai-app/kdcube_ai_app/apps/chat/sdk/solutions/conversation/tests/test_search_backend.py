# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation search backend factory: ns-context mapping + lazy construction."""

from __future__ import annotations

from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.search_backend import (
    conversation_search_context_from_ns,
    make_control_plane_search_backend,
)


def test_ns_context_mapping():
    ns = SimpleNamespace(user_id="u1", conversation_id="c1", turn_id="t1", bundle_id="b", tenant="t", project="p")
    ctx = conversation_search_context_from_ns(ns)
    assert ctx.user_id == "u1"
    assert ctx.conversation_id == "c1"
    assert ctx.turn_id == "t1"
    assert ctx.tenant == "t" and ctx.project == "p" and ctx.bundle_id == "b"


def test_backend_is_lazy_and_satisfies_protocol():
    calls = {"pool": 0}

    def pool_factory():
        calls["pool"] += 1
        return object()

    backend = make_control_plane_search_backend(pool_factory=pool_factory, tenant="t", project="p")
    # Satisfies the ConversationSearchBackend protocol.
    for method in ("search", "search_turn_catalog", "get_turn_log"):
        assert hasattr(backend, method)
    # Construction is lazy: nothing built, pool not touched until the first search.
    assert calls["pool"] == 0
