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
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.target_scope import bind_conversation_targets
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.target_policy import ConversationTargetPolicy


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

    async def get_turn_log(self, *, turn_id, conversation_id=None, bundle_id=None):
        return {
            "blocks": [{
                "type": "conv.working.summary",
                "turn_id": turn_id,
                "ts": "2026-05-05T10:00:00Z",
                "path": "conv:ws:turn_prev.conv.working.summary",
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
        tenant="t", project="p", user_id="user_42", conversation_id="conv_99", bundle_id="caller-app",
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


def _search_request(filters: dict) -> NamedServiceRequest:
    return NamedServiceRequest.from_dict({
        "operation": "object.search",
        "namespace": "conv",
        "query": "invoice",
        "filters": filters,
    })


def test_search_filters_offer_an_optional_bundle_id():
    scope = conversation_search_named_service_spec().search_scopes[0]
    assert scope.filters_schema["bundle_id"]["type"] == "string"


@pytest.mark.asyncio
async def test_a_requested_bundle_id_replaces_the_default_scope():
    backend = FakeBackend()
    checked = []

    async def validator(ns_ctx, bundle_id):
        checked.append(bundle_id)
        return True

    async def policy(_ctx):
        return ConversationTargetPolicy(configured=("workspace@2026-03-31-13-36",))

    provider = make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id="u", bundle_id="caller-app"),
        search_backend_factory=lambda c: backend,
        bundle_validator=validator,
        target_policy_factory=policy,
    )

    with bind_conversation_targets(("workspace@2026-03-31-13-36",)):
        response = await provider.object_search(
            NamedServiceContext(), _search_request({"bundle_id": "workspace@2026-03-31-13-36"})
        )

    assert response.ok
    assert checked == ["workspace@2026-03-31-13-36"]
    assert backend.search_kwargs["bundle_id"] == "workspace@2026-03-31-13-36"


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,disabled,expected", [
    ((), (), "conversation_target_outside_ceiling"),
    (("other-app",), ("other-app",), "conversation_target_disabled"),
])
async def test_cross_bundle_target_requires_admin_ceiling_and_user_choice(
    configured, disabled, expected
):
    backend = FakeBackend()

    async def policy(_ctx):
        return ConversationTargetPolicy(configured=configured, disabled=disabled)

    async def validator(_ctx, _bundle_id):
        return True

    provider = make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id="u", bundle_id="caller-app"),
        search_backend_factory=lambda c: backend,
        bundle_validator=validator,
        target_policy_factory=policy,
    )
    with bind_conversation_targets(("other-app",)):
        response = await provider.object_search(
            NamedServiceContext(), _search_request({"bundle_id": "other-app"})
        )
    assert response.status == 403
    assert response.error.code == expected
    assert backend.search_kwargs == {}


@pytest.mark.asyncio
async def test_user_can_disable_own_conversation_target():
    backend = FakeBackend()

    async def policy(_ctx):
        return ConversationTargetPolicy(disabled=("caller-app",))

    provider = make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id="u", bundle_id="caller-app"),
        search_backend_factory=lambda c: backend,
        target_policy_factory=policy,
    )
    response = await provider.object_search(NamedServiceContext(), _search_request({}))
    assert response.status == 403
    assert response.error.code == "conversation_target_disabled"
    assert backend.search_kwargs == {}


@pytest.mark.asyncio
async def test_without_a_requested_bundle_id_the_context_scope_stands():
    backend = FakeBackend()
    provider = make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id="u", bundle_id="caller-app"),
        search_backend_factory=lambda c: backend,
    )

    response = await provider.object_search(NamedServiceContext(), _search_request({}))

    assert response.ok
    assert backend.search_kwargs["bundle_id"] == "caller-app"


@pytest.mark.asyncio
async def test_known_cross_bundle_target_requires_card_permission():
    backend = FakeBackend()

    async def validator(ns_ctx, bundle_id):
        return True

    provider = make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id="u", bundle_id="caller-app"),
        search_backend_factory=lambda c: backend,
        bundle_validator=validator,
    )
    with bind_conversation_targets(("different-app",)):
        response = await provider.object_search(
            NamedServiceContext(), _search_request({"bundle_id": "workspace@2026-03-31-13-36"})
        )

    assert response.status == 403
    assert response.error.code == "conversation_target_not_granted"
    assert backend.search_kwargs == {}


@pytest.mark.asyncio
async def test_an_unregistered_bundle_id_is_refused_before_searching():
    backend = FakeBackend()

    async def validator(ns_ctx, bundle_id):
        return False

    provider = make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id="u"),
        search_backend_factory=lambda c: backend,
        bundle_validator=validator,
    )

    response = await provider.object_search(
        NamedServiceContext(), _search_request({"bundle_id": "no-such-app@1-0"})
    )

    assert not response.ok
    assert response.status == 404
    assert response.error.code == "conversation_bundle_not_found"
    assert backend.search_kwargs == {}


@pytest.mark.asyncio
async def test_object_search_missing_query_errors():
    backend = FakeBackend()
    provider = make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id="u", conversation_id="c", bundle_id="caller-app"),
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
