# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import ConversationSearchContext
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.instructions import (
    CONVERSATION_NAMESPACE_INTRO,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.named_service import (
    NAMESPACE,
    PROVIDER_ID,
    conversation_search_named_service_spec,
    make_conversation_search_named_service_provider,
)


class FakeBackend:
    def __init__(self):
        self.search_kwargs = {}

    async def search(self, **kwargs):
        self.search_kwargs = kwargs
        return "turn_prev", [{
            "turn_id": "turn_prev",
            "conversation_id": kwargs.get("conv"),
            "score": 0.9,
            "matched_via_role": "assistant",
            "ts": "2026-05-05T10:00:00Z",
        }]

    async def search_turn_catalog(self, **kwargs):
        return []

    async def get_turn_log(self, *, turn_id, conversation_id=None):
        return {
            "blocks": [{
                "type": "conv.working.summary",
                "turn_id": turn_id,
                "ts": "2026-05-05T10:00:00Z",
                "path": "ws:turn_prev.conv.working.summary",
                "text": "Goal: retrieve invoices.",
                "meta": {},
            }],
            "sources_pool": [],
        }


def test_spec_is_search_only_with_realm_intro():
    spec = conversation_search_named_service_spec(bundle_id="b")
    assert spec.provider_id == PROVIDER_ID
    assert spec.namespace == NAMESPACE == "conv"
    assert spec.intro == CONVERSATION_NAMESPACE_INTRO
    # Search realm: no write operations advertised.
    assert "object.search" in spec.operations
    assert "object.upsert" not in spec.operations
    assert "object.delete" not in spec.operations


@pytest.mark.asyncio
async def test_object_search_uses_explicit_context_factory():
    backend = FakeBackend()

    # The bundle wires identity explicitly: context from request auth, backend
    # bound to the caller's schema.
    def context_factory(ns_ctx: NamedServiceContext) -> ConversationSearchContext:
        return ConversationSearchContext(
            user_id=ns_ctx.user_id or "",
            conversation_id=ns_ctx.conversation_id or "",
            turn_id=ns_ctx.turn_id or "",
            bundle_id=ns_ctx.bundle_id,
            tenant=ns_ctx.tenant,
            project=ns_ctx.project,
        )

    provider = make_conversation_search_named_service_provider(
        context_factory=context_factory,
        search_backend_factory=lambda ns_ctx: backend,
        bundle_id="b",
    )

    ns_ctx = NamedServiceContext(
        tenant="t", project="p", user_id="user_42", conversation_id="conv_99",
    )
    request = NamedServiceRequest.from_dict({
        "operation": "object.search",
        "namespace": "conv",
        "query": "invoice",
        "filters": {"targets": ["summary"], "scope": "conversation"},
        "limit": 3,
    })

    response = await provider.object_search(ns_ctx, request)

    assert response.ok
    # Identity from the named-service ctx flowed through to the backend.
    assert backend.search_kwargs["user"] == "user_42"
    assert backend.search_kwargs["conv"] == "conv_99"
    items = response.ret.get("items") or []
    assert len(items) == 1
    # Turn search hits are lean: the kind is encoded in the ref, no object envelope.
    assert items[0]["ref"] == "conv:turn:turn_prev"
    assert "object_kind" not in items[0]
    assert items[0]["body"]["turn_id"] == "turn_prev"


@pytest.mark.asyncio
async def test_object_search_missing_query_errors():
    backend = FakeBackend()
    provider = make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id="u", conversation_id="c"),
        search_backend_factory=lambda c: backend,
    )
    request = NamedServiceRequest.from_dict({
        "operation": "object.search",
        "namespace": "conv",
        "query": "",
    })
    response = await provider.object_search(NamedServiceContext(), request)
    assert not response.ok
    assert response.error.code == "conversation_query_required"
