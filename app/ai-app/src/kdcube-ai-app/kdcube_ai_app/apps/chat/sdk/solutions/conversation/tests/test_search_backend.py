# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation search backend factory: ns-context mapping + lazy construction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ConversationSearchContext,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.search_backend import (
    conversation_search_context_from_ns,
    conversation_search_context_from_tool_subsystem,
    make_conversation_search_backend,
    make_conversation_search_backend_from_client,
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


def test_tool_context_uses_only_bound_caller_identity():
    comm = SimpleNamespace(
        tenant="tenant-a",
        project="project-a",
        user_id="user-a",
        user_type="regular",
        service={
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-a",
            "conversation_id": "conversation-current",
            "turn_id": "turn-current",
            "bundle_id": "bundle@1-0",
            "agent_id": "agent-a",
        },
        conversation={
            "conversation_id": "conversation-current",
            "turn_id": "turn-current",
        },
    )
    tool_subsystem = SimpleNamespace(
        comm=comm,
        registry={},
        bundle_spec=SimpleNamespace(id="fallback@1-0"),
    )

    context = conversation_search_context_from_tool_subsystem(tool_subsystem)

    assert context.user_id == "user-a"
    assert context.conversation_id == "conversation-current"
    assert context.turn_id == "turn-current"
    assert context.bundle_id == "bundle@1-0"
    assert context.agent_id == "agent-a"
    assert context.tenant == "tenant-a"
    assert context.project == "project-a"


def test_tool_context_requires_a_resolved_caller_user():
    tool_subsystem = SimpleNamespace(
        comm=SimpleNamespace(
            tenant="tenant-a",
            project="project-a",
            user_id="",
            service={},
            conversation={},
        ),
        registry={},
        bundle_spec=SimpleNamespace(id="bundle@1-0"),
    )

    with pytest.raises(RuntimeError, match="resolved caller user"):
        conversation_search_context_from_tool_subsystem(tool_subsystem)


def test_existing_client_backend_preserves_bound_runtime_identity():
    client = SimpleNamespace(model_service=object())
    context = ConversationSearchContext(
        user_id="user-a",
        conversation_id="conversation-current",
        turn_id="turn-current",
        bundle_id="bundle@1-0",
        agent_id="agent-a",
        tenant="tenant-a",
        project="project-a",
    )

    backend = make_conversation_search_backend_from_client(
        context_rag_client=client,
        context=context,
    )

    assert backend.ctx_client is client
    assert backend.svc is client.model_service
    assert backend._runtime_ctx.user_id == "user-a"
    assert backend._runtime_ctx.conversation_id == "conversation-current"
    assert backend._runtime_ctx.agent_id == "agent-a"
