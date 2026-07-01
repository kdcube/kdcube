# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation search backend factory: ns-context mapping + lazy construction."""

from __future__ import annotations

from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.search_backend import (
    conversation_search_context_from_ns,
    make_conversation_search_backend,
)


def test_ns_context_mapping():
    ns = SimpleNamespace(user_id="u1", conversation_id="c1", turn_id="t1", bundle_id="b", tenant="t", project="p")
    ctx = conversation_search_context_from_ns(ns)
    assert ctx.user_id == "u1"
    assert ctx.conversation_id == "c1"
    assert ctx.turn_id == "t1"
    assert ctx.tenant == "t" and ctx.project == "p" and ctx.bundle_id == "b"


def test_backend_is_lazy_and_satisfies_protocol():
    # All resources are passed in from above; the backend must not build a
    # ContextBrowser until the first search call.
    backend = make_conversation_search_backend(
        pg_pool=object(), tenant="t", project="p", model_service=object(), store=object(),
    )
    # Satisfies the ConversationSearchBackend protocol.
    for method in ("search", "search_turn_catalog", "get_turn_log"):
        assert hasattr(backend, method)
    # Construction is lazy: nothing built until the first search.
    assert backend._browser is None
