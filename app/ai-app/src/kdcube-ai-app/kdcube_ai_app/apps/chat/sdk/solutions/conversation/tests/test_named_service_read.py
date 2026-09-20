# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""conv provider list/get operations over the SDK read facade.

Drives the named-service handlers with a fake `ConversationReadService`, asserting
scope mapping (self default, selected-user admin), object shaping, and the
read-not-configured guard. Reading a conversation is object.get; there is no
export operation. No control-plane, no database.
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
    def __init__(self, *, summaries=None, record=None, export=None, fetched=None):
        self._summaries = summaries or []
        self._record = record
        self._fetched = fetched
        self._export = export or {"ok": True, "count": 0, "total_available": 0, "limited": False, "conversations": []}
        self.list_scope = None
        self.list_bundle_id = None
        self.get_scope = None
        self.get_conversation_id = None
        self.fetch_scope = None
        self.fetch_bundle_id = None
        self.export_scope = None

    async def list_user_conversations(self, request):
        self.list_scope = request.scope
        self.list_bundle_id = request.bundle_id
        return list(self._summaries)

    async def get_conversation(self, request):
        self.get_scope = request.scope
        self.get_conversation_id = request.conversation_id
        return self._record

    async def fetch_conversation(self, request):
        # Rich per-turn artifacts (object.get conv:conversation distills these).
        self.fetch_scope = request.scope
        self.fetch_bundle_id = request.bundle_id
        self.get_conversation_id = request.conversation_id
        return dict(self._fetched or {})

    async def export_conversations(self, request):
        self.export_scope = request.scope
        return dict(self._export)


def _provider(read_service=None):
    return make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id=c.user_id or "", conversation_id=c.conversation_id or "", bundle_id="caller-app"),
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
    assert caps_on["list"] and caps_on["get"]
    assert not caps_off["list"] and not caps_off["get"]
    assert caps_on["search"] and caps_off["search"]
    # There is no export capability — reading a conversation is object.get.
    assert "export" not in caps_on


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
    assert svc.list_bundle_id == "caller-app"
    items = resp.ret["items"]
    assert len(items) == 1
    assert items[0]["object_kind"] == "conversation"
    assert items[0]["ref"] == "conv:conversation:c1"
    assert items[0]["body"]["turn_count"] == 3


@pytest.mark.asyncio
async def test_object_get_returns_interleaved_timeline():
    # Rich per-turn artifacts -> lightweight chronological timeline. The produced
    # file surfaces as an assistant.file event with a conv:fi: ref.
    fetched = {
        "conversation_id": "c1", "user_id": "u", "conversation_title": "News",
        "turns": [{
            "turn_id": "t1",
            "artifacts": [
                {"type": "chat:user", "ts": "2026-07-01T22:00:00Z", "data": {"text": "make a chart"}},
                {"type": "artifact:assistant.file", "ts": "2026-07-01T22:02:00Z",
                 "data": {"payload": {"filename": "chart.png", "mime": "image/png",
                                       "artifact_path": "conv:fi:turn_t1.files/chart.png"}}},
                {"type": "chat:assistant", "ts": "2026-07-01T22:03:00Z", "data": {"text": "here it is"}},
            ],
        }],
    }
    provider = _provider(FakeReadService(fetched=fetched))
    resp = await provider.object_get(NamedServiceContext(user_id="u"), _req("object.get", object_ref="conv:conversation:c1"))
    assert resp.ok
    obj = resp.ret["object"]
    assert obj["object_kind"] == "conversation"
    assert obj["ref"] == "conv:conversation:c1"
    turns = obj["body"]["turns"]
    assert [t["turn_id"] for t in turns] == ["t1"]
    events = turns[0]["events"]
    assert [e["type"] for e in events] == ["user.message", "assistant.file", "assistant.message"]
    file_event = events[1]
    assert file_event["ref"] == "conv:fi:conv_c1.turn_t1.files/chart.png"
    assert file_event["filename"] == "chart.png"
    assert obj["body"]["turn_count"] == 1


@pytest.mark.asyncio
async def test_object_get_missing_returns_404():
    provider = _provider(FakeReadService(fetched={}))  # no turns -> not found
    resp = await provider.object_get(NamedServiceContext(user_id="u"), _req("object.get", object_ref="conv:conversation:nope"))
    assert not resp.ok
    assert resp.error.code == "conversation_not_found"
    assert resp.status == 404


@pytest.mark.asyncio
async def test_object_get_uses_exact_bundle_scope():
    svc = FakeReadService(fetched={"conversation_id": "c1", "turns": []})
    provider = _provider(svc)
    await provider.object_get(NamedServiceContext(user_id="u"), _req("object.get", object_ref="conv:conversation:c1"))
    assert svc.fetch_bundle_id == "caller-app"


@pytest.mark.asyncio
async def test_object_get_requires_id():
    provider = _provider(FakeReadService())
    resp = await provider.object_get(NamedServiceContext(user_id="u"), _req("object.get"))
    assert not resp.ok
    assert resp.error.code == "conversation_id_required"


@pytest.mark.asyncio
async def test_selected_user_scope_routes_to_selected_user():
    # Selected-user scope routes reads only when its stronger permission was
    # projected into this invocation. Covered via object.list now that export
    # is gone.
    svc = FakeReadService(summaries=[])
    provider = _provider(svc)
    ctx = NamedServiceContext(
        user_id="admin-1",
        permissions=("conversations:read:any_user",),
    )
    resp = await provider.object_list(ctx, _req("object.list", filters={"scope": {"mode": "user", "user_id": "other-user"}}))
    assert resp.ok
    assert svc.list_scope.normalized_mode == "user"
    assert svc.list_scope.resolve() == "other-user"


@pytest.mark.asyncio
async def test_selected_current_user_does_not_require_any_user_permission():
    svc = FakeReadService(summaries=[])
    provider = _provider(svc)
    resp = await provider.object_list(
        NamedServiceContext(user_id="user-1"),
        _req(
            "object.list",
            filters={"scope": {"mode": "user", "user_id": "user-1"}},
        ),
    )
    assert resp.ok
    assert svc.list_scope.resolve() == "user-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("read_kind", ["list", "get", "file"])
async def test_cross_user_read_requires_any_user_permission(read_kind):
    svc = FakeReadService(
        summaries=[],
        fetched={"conversation_id": "c1", "turns": []},
    )
    backend = _FileBackend({"ok": True, "data": b"secret"})
    provider = _file_provider(backend, read_service=svc)
    request = {
        "list": _req("object.list"),
        "get": _req("object.get", object_ref="conv:conversation:c1"),
        "file": _req(
            "object.get",
            object_ref="conv:fi:conv_c1.turn_1.files/summary.md",
        ),
    }[read_kind]
    request.filters["scope"] = {"mode": "user", "user_id": "other-user"}

    response = await provider.dispatch(
        NamedServiceContext(user_id="user-1"),
        request,
    )

    assert response.status == 403
    assert response.error.code == "conversation_user_not_granted"
    assert response.error.details["required_permission"] == (
        "conversations:read:any_user"
    )
    assert svc.list_scope is None
    assert svc.fetch_scope is None
    assert backend.calls == []


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
        context_factory=lambda c: ConversationSearchContext(user_id=c.user_id or "", bundle_id="caller-app"),
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
    assert caps["list"] is True and caps["get"] is True
    assert "export" not in caps


class _FileBackend:
    """Search backend that only materializes files (conv:fi: object.get path)."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    async def materialize_file(self, *, fi_ref, conversation_id=""):
        self.calls.append((fi_ref, conversation_id))
        return dict(self._result)


def _file_provider(backend, *, file_url_factory=None, read_service=None):
    files = ("summary.md", "a.png", "chart.png", "missing.md", "big.bin")
    fetched = {
        "conversation_id": "c1", "user_id": "u",
        "turns": [{"turn_id": "1", "artifacts": [
            {"type": "artifact:assistant.file", "data": {"payload": {
                "filename": name, "artifact_path": f"conv:fi:turn_1.files/{name}",
            }}} for name in files
        ]}],
    }
    return make_conversation_search_named_service_provider(
        context_factory=lambda c: ConversationSearchContext(user_id=c.user_id or "", conversation_id=c.conversation_id or "", bundle_id="caller-app"),
        search_backend_factory=lambda c: backend,
        read_service_factory=lambda c: read_service or FakeReadService(fetched=fetched),
        file_url_factory=file_url_factory,
        bundle_id="b",
    )


@pytest.mark.asyncio
async def test_file_ref_not_in_scoped_conversation_is_not_materialized():
    backend = _FileBackend({"ok": True, "data": b"secret"})
    provider = _file_provider(backend)
    response = await provider.object_get(
        NamedServiceContext(user_id="u"),
        _req("object.get", object_ref="conv:fi:conv_c1.turn_1.files/secret.txt"),
    )
    assert response.status == 404
    assert response.error.code == "conversation_file_not_found"
    assert backend.calls == []


@pytest.mark.asyncio
async def test_object_get_conv_fi_returns_text_inline():
    backend = _FileBackend({"ok": True, "filename": "summary.md", "mime": "text/markdown", "size": 5, "data": b"hello"})
    provider = _file_provider(backend)
    resp = await provider.object_get(
        NamedServiceContext(user_id="u", conversation_id="c1"),
        _req("object.get", object_ref="conv:fi:conv_c1.turn_1.files/summary.md"),
    )
    assert resp.ok
    obj = resp.ret["object"]
    assert obj["object_kind"] == "conversation.file"
    assert obj["ref"] == "conv:fi:conv_c1.turn_1.files/summary.md"
    assert obj["body"]["encoding"] == "text"
    assert obj["body"]["content"] == "hello"
    assert obj["body"]["filename"] == "summary.md"
    # fi ref carries no conv_ prefix -> conversation_id falls back to the caller's ctx.
    assert backend.calls == [("conv:fi:conv_c1.turn_1.files/summary.md", "c1")]


@pytest.mark.asyncio
async def test_object_get_conv_fi_binary_base64():
    import base64 as _b64
    backend = _FileBackend({"ok": True, "filename": "a.png", "mime": "image/png", "size": 3, "data": b"\x89PN"})
    provider = _file_provider(backend)
    resp = await provider.object_get(
        NamedServiceContext(user_id="u", conversation_id="c1"),
        _req("object.get", object_ref="conv:fi:conv_c1.turn_1.files/a.png"),
    )
    assert resp.ok
    obj = resp.ret["object"]
    assert obj["body"]["encoding"] == "base64"
    assert _b64.b64decode(obj["body"]["content"]) == b"\x89PN"


@pytest.mark.asyncio
async def test_object_get_conv_fi_binary_prefers_download_url():
    # With a URL factory wired, a binary is delivered as a short-lived download URL
    # (bytes stay out of the model's context) — never base64.
    seen = {}

    async def _factory(ctx, info):
        seen.update(info)
        return {"url": "https://host/api/integrations/bundles/t/p/b/public/conv_file_download?download_token=tok", "expires_at": 1900}

    backend = _FileBackend({"ok": True, "filename": "chart.png", "mime": "image/png", "size": 143145, "data": b"\x89PNG..."})
    provider = _file_provider(backend, file_url_factory=_factory)
    resp = await provider.object_get(
        NamedServiceContext(user_id="u", conversation_id="c1"),
        _req("object.get", object_ref="conv:fi:conv_c1.turn_1.files/chart.png"),
    )
    assert resp.ok
    body = resp.ret["object"]["body"]
    assert body["encoding"] == "url"
    assert body["url"].endswith("download_token=tok")
    assert body["expires_at"] == 1900
    assert "content" not in body
    # The factory receives the canonical conversation-file ref + descriptor.
    assert seen["fi_ref"] == "conv:fi:conv_c1.turn_1.files/chart.png"
    assert seen["mime"] == "image/png"


@pytest.mark.asyncio
async def test_object_get_conv_fi_binary_large_without_url_returns_metadata():
    # No URL factory + a binary above the inline cap -> metadata only (never a
    # context-blowing base64 blob).
    backend = _FileBackend({
        "ok": True, "filename": "chart.png", "mime": "image/png",
        "size": 143145, "data": b"x" * 143145,
    })
    provider = _file_provider(backend)  # no file_url_factory
    resp = await provider.object_get(
        NamedServiceContext(user_id="u", conversation_id="c1"),
        _req("object.get", object_ref="conv:fi:conv_c1.turn_1.files/chart.png"),
    )
    assert resp.ok
    body = resp.ret["object"]["body"]
    assert body["encoding"] == "none"
    assert "content" not in body


@pytest.mark.asyncio
async def test_object_get_conv_fi_not_found():
    backend = _FileBackend({"ok": False, "reason": "not_found"})
    provider = _file_provider(backend)
    resp = await provider.object_get(
        NamedServiceContext(user_id="u", conversation_id="c1"),
        _req("object.get", object_ref="conv:fi:conv_c1.turn_1.files/missing.md"),
    )
    assert not resp.ok
    assert resp.status == 404
    assert resp.error.code == "conversation_file_not_found"


@pytest.mark.asyncio
async def test_object_get_conv_fi_too_large_returns_metadata_only():
    backend = _FileBackend({
        "ok": False, "reason": "too_large",
        "detail": {"filename": "big.bin", "mime": "application/octet-stream", "size": 999999999},
    })
    provider = _file_provider(backend)
    resp = await provider.object_get(
        NamedServiceContext(user_id="u", conversation_id="c1"),
        _req("object.get", object_ref="conv:fi:conv_c1.turn_1.files/big.bin"),
    )
    assert resp.ok
    obj = resp.ret["object"]
    assert obj["body"]["encoding"] == "none"
    assert obj["body"]["size"] == 999999999
    assert "content" not in obj["body"]
