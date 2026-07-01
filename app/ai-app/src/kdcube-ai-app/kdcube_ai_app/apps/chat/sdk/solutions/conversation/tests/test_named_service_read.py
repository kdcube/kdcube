# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""conv provider list/get/export operations over the SDK read facade.

Drives the named-service handlers with a fake `ConversationReadService`, asserting
scope mapping (self default, selected-user admin), object shaping, and the
read-not-configured guard. No control-plane, no database.
"""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import ConversationSearchContext
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.named_service import (
    make_conversation_search_named_service_provider,
)


class FakeReadService:
    def __init__(self, *, summaries=None, record=None, export=None):
        self._summaries = summaries or []
        self._record = record
        self._export = export or {"ok": True, "count": 0, "total_available": 0, "limited": False, "conversations": []}
        self.list_scope = None
        self.get_scope = None
        self.get_conversation_id = None
        self.export_scope = None

    async def list_user_conversations(self, request):
        self.list_scope = request.scope
        return list(self._summaries)

    async def get_conversation(self, request):
        self.get_scope = request.scope
        self.get_conversation_id = request.conversation_id
        return self._record

    async def export_conversations(self, request):
        self.export_scope = request.scope
        return dict(self._export)


def _provider(read_service=None):
    return make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id=c.user_id or "", conversation_id=c.conversation_id or ""),
        search_backend_factory=lambda c: None,
        read_service_factory=(lambda c: read_service) if read_service is not None else None,
        bundle_id="b",
    )


def _req(operation, **kw):
    return NamedServiceRequest.from_dict({"operation": operation, "namespace": "conv", **kw})


@pytest.mark.asyncio
async def test_capabilities_reflect_read_enabled():
    on = _provider(FakeReadService())
    off = _provider(None)
    r_on = await on.provider_capabilities(NamedServiceContext(), _req("provider.capabilities"))
    r_off = await off.provider_capabilities(NamedServiceContext(), _req("provider.capabilities"))
    caps_on = r_on.ret["attrs"]["capabilities"]
    caps_off = r_off.ret["attrs"]["capabilities"]
    assert caps_on["list"] and caps_on["get"] and caps_on["export"]
    assert not caps_off["list"] and not caps_off["get"] and not caps_off["export"]
    assert caps_on["search"] and caps_off["search"]


@pytest.mark.asyncio
async def test_object_list_returns_summaries_with_self_scope():
    svc = FakeReadService(summaries=[
        {"conversation_id": "c1", "user_id": "user_42", "title": "First", "started_at": "2026-01-01", "turn_count": 3},
    ])
    provider = _provider(svc)
    ctx = NamedServiceContext(tenant="t", project="p", user_id="user_42")
    resp = await provider.object_list(ctx, _req("object.list"))
    assert resp.ok
    # Default scope is the caller's own conversations.
    assert svc.list_scope.normalized_mode == "self"
    assert svc.list_scope.resolve() == "user_42"
    items = resp.ret["items"]
    assert len(items) == 1
    assert items[0]["object_kind"] == "conversation"
    assert items[0]["ref"] == "conv:conversation:c1"
    assert items[0]["body"]["turn_count"] == 3


@pytest.mark.asyncio
async def test_object_get_returns_object():
    record = {"conversation_id": "c1", "tenant": "t", "project": "p", "user_id": "u", "title": "First", "turns": []}
    provider = _provider(FakeReadService(record=record))
    resp = await provider.object_get(NamedServiceContext(user_id="u"), _req("object.get", object_ref="conv:conversation:c1"))
    assert resp.ok
    obj = resp.ret["object"]
    assert obj["object_kind"] == "conversation"
    assert obj["ref"] == "conv:conversation:c1"
    assert obj["body"] == record


@pytest.mark.asyncio
async def test_object_get_missing_returns_404():
    provider = _provider(FakeReadService(record=None))
    resp = await provider.object_get(NamedServiceContext(user_id="u"), _req("object.get", object_ref="conv:conversation:nope"))
    assert not resp.ok
    assert resp.error.code == "conversation_not_found"
    assert resp.status == 404


@pytest.mark.asyncio
async def test_object_get_requires_id():
    provider = _provider(FakeReadService())
    resp = await provider.object_get(NamedServiceContext(user_id="u"), _req("object.get"))
    assert not resp.ok
    assert resp.error.code == "conversation_id_required"


@pytest.mark.asyncio
async def test_object_export_returns_result_extra():
    export = {"ok": True, "count": 2, "total_available": 2, "limited": False, "conversations": [{"conversation_id": "c1"}, {"conversation_id": "c2"}]}
    svc = FakeReadService(export=export)
    provider = _provider(svc)
    resp = await provider.object_export(NamedServiceContext(user_id="u"), _req("object.export", limit=50))
    assert resp.ok
    extra = resp.ret["extra"]
    assert extra["count"] == 2 and extra["total_available"] == 2 and extra["scope"] == "self"
    assert svc.export_scope.resolve() == "u"


@pytest.mark.asyncio
async def test_selected_user_scope_routes_to_selected_user():
    svc = FakeReadService(export={"ok": True, "count": 0, "total_available": 0, "limited": False, "conversations": []})
    provider = _provider(svc)
    ctx = NamedServiceContext(user_id="admin-1")
    resp = await provider.object_export(ctx, _req("object.export", filters={"scope": {"mode": "user", "user_id": "other-user"}}))
    assert resp.ok
    assert svc.export_scope.normalized_mode == "user"
    assert svc.export_scope.resolve() == "other-user"


@pytest.mark.asyncio
async def test_read_not_configured_guard():
    provider = _provider(None)
    resp = await provider.object_get(NamedServiceContext(user_id="u"), _req("object.get", object_ref="conv:conversation:c1"))
    assert not resp.ok
    assert resp.error.code == "conversation_read_not_configured"
    assert resp.status == 501


def _read_only_provider(read_service):
    # No search factories: a read/export-only registration (no search backend).
    return make_conversation_search_named_service_provider(
        read_service_factory=lambda c: read_service,
        bundle_id="b",
    )


@pytest.mark.asyncio
async def test_search_not_configured_guard():
    provider = _read_only_provider(FakeReadService())
    resp = await provider.object_search(NamedServiceContext(user_id="u"), _req("object.search", query="x"))
    assert not resp.ok
    assert resp.error.code == "conversation_search_not_configured"
    assert resp.status == 501


@pytest.mark.asyncio
async def test_capabilities_search_false_without_backend():
    provider = _read_only_provider(FakeReadService())
    resp = await provider.provider_capabilities(NamedServiceContext(), _req("provider.capabilities"))
    caps = resp.ret["attrs"]["capabilities"]
    assert caps["search"] is False
    assert caps["list"] is True and caps["get"] is True and caps["export"] is True
