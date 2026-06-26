# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import (
    CANVAS_CARD_COMMENT_OBJECT_KIND,
    CANVAS_CARD_DELETE_OBJECT_KIND,
    CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND,
    CANVAS_CARD_LAYOUT_OBJECT_KIND,
    CANVAS_BOARD_OBJECT_KIND,
    CANVAS_CARD_OBJECT_KIND,
    CANVAS_OBJECT_OBJECT_KIND,
    CANVAS_OPERATION_BATCH_OBJECT_KIND,
    CANVAS_PIN_OBJECT_KIND,
    CanvasPinSearchNamedServiceProvider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
)


@pytest.mark.asyncio
async def test_canvas_pin_provider_schema_exposes_search_filters() -> None:
    provider = CanvasPinSearchNamedServiceProvider()
    assert provider.spec.matches_ref("cnv:canvas/users/user-a/canvases/main")
    assert "object.upsert" in provider.spec.operations

    response = await provider.object_schema(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(operation="object.schema", namespace="cnv"),
    )

    assert response.ok is True
    schema = response.ret["extra"]["schema"]
    assert schema["object_kind"] == CANVAS_PIN_OBJECT_KIND
    assert schema["object_kind"] == CANVAS_CARD_OBJECT_KIND
    assert set(response.ret["extra"]["schemas"]) >= {
        CANVAS_BOARD_OBJECT_KIND,
        CANVAS_CARD_OBJECT_KIND,
        CANVAS_OBJECT_OBJECT_KIND,
        CANVAS_OPERATION_BATCH_OBJECT_KIND,
        CANVAS_CARD_COMMENT_OBJECT_KIND,
        CANVAS_CARD_DELETE_OBJECT_KIND,
        CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND,
        CANVAS_CARD_LAYOUT_OBJECT_KIND,
    }
    assert "comment" in schema["tools"]
    assert schema["tools"]["comment"]["tool"] == "named_services.upsert_object"
    filters = schema["search"]["filters"]
    assert "kinds" in filters
    assert "namespaces" in filters
    assert "semantic_score" in filters["thresholds"]["properties"]
    assert response.ret["extra"]["search_scopes"][0]["namespace"] == "cnv"


@pytest.mark.asyncio
async def test_canvas_pin_provider_search_normalizes_pin_results() -> None:
    async def _search_handler(_ctx, request):
        assert request.namespace == "cnv"
        return {
            "ok": True,
            "results": [
                {
                    "card_id": "card-1",
                    "kind": "memory",
                    "title": "Known fact",
                    "logical_path": "mem:record:mem_1",
                    "namespace": "mem",
                    "board": "cnv:user:main",
                    "score": 0.91,
                }
            ],
        }

    provider = CanvasPinSearchNamedServiceProvider(search_handler=_search_handler)
    response = await provider.object_search(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(operation="object.search", namespace="cnv", query="known"),
    )

    assert response.ok is True
    item = response.ret["items"][0]
    assert item["object_ref"] == "mem:record:mem_1"
    assert item["object_kind"] == CANVAS_PIN_OBJECT_KIND
    assert item["body"]["card_id"] == "card-1"


@pytest.mark.asyncio
async def test_canvas_provider_lists_boards_with_canonical_board_refs() -> None:
    async def _list_handler(_ctx, _request):
        return {
            "ok": True,
            "active_canvas": "demo-board",
            "canvases": [
                {
                    "canvas_name": "demo-board",
                    "canvas_id": "cnv:user-a:demo-board",
                    "latest_revision": 7,
                    "canvas_ref": "cnv:demo-board@7",
                }
            ],
        }

    provider = CanvasPinSearchNamedServiceProvider(list_handler=_list_handler)
    response = await provider.object_list(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(operation="object.list", namespace="cnv", limit=5),
    )

    assert response.ok is True
    assert response.ret["attrs"]["source"] == "canvas.board_list"
    item = response.ret["items"][0]
    assert item["object_ref"] == "cnv:demo-board"
    assert item["object_kind"] == CANVAS_BOARD_OBJECT_KIND
    assert item["body"]["canvas_id"] == "cnv:user-a:demo-board"
    assert item["body"]["active"] is True


@pytest.mark.asyncio
async def test_canvas_provider_card_upsert_maps_to_canvas_patch() -> None:
    class Store:
        def __init__(self) -> None:
            self.patch_args = None

        def canvas_id(self, *, canvas_name, canvas_id=None):
            return canvas_id or f"cnv:user-a:{canvas_name}"

        def patch(self, *, canvas_name, canvas_id, patch, actor):
            self.patch_args = {
                "canvas_name": canvas_name,
                "canvas_id": canvas_id,
                "patch": patch,
                "actor": actor,
            }
            card = {
                "id": "card-1",
                "kind": "user.text",
                "title": "Hello",
                "logical_path": "cnv:canvas/users/user-a/canvases/cnv_user-a_main/objects/user-text/card-1/v000001.md",
            }
            return {
                "ok": True,
                "canvas_ref": "cnv:main@2",
                "latest_ref": "cnv:main",
                "canvas_uri": "cnv:main@2",
                "canvas": {
                    "canvas_id": canvas_id,
                    "canvas_name": canvas_name,
                    "revision": 2,
                    "cards": [card],
                },
                "changed_cards": [card],
            }

    store = Store()
    provider = CanvasPinSearchNamedServiceProvider(store_factory=lambda _ctx: store)
    response = await provider.object_upsert(
        NamedServiceContext(
            tenant="tenant-a",
            project="project-a",
            user_id="user-a",
            actor={"name": "agent-a"},
        ),
        NamedServiceRequest(
            operation="object.upsert",
            namespace="cnv",
            object={
                "object_kind": CANVAS_CARD_OBJECT_KIND,
                "canvas_name": "main",
                "card": {
                    "kind": "user.text",
                    "title": "Hello",
                    "mime": "text/markdown",
                    "content": {"text": "hello"},
                },
            },
        ),
    )

    assert response.ok is True
    assert store.patch_args["canvas_name"] == "main"
    assert store.patch_args["actor"] == "agent-a"
    op = store.patch_args["patch"]["operations"][0]
    assert op["op"] == "new_card"
    assert op["card"]["kind"] == "user.text"
    assert response.ret["attrs"]["object_ref"].startswith("cnv:canvas/users/user-a/")
    assert response.ret["object"]["object_kind"] == CANVAS_CARD_OBJECT_KIND
    assert response.ret["object"]["card_id"] == "card-1"


@pytest.mark.asyncio
async def test_canvas_provider_raw_patch_preserves_patch_batch() -> None:
    class Store:
        def __init__(self) -> None:
            self.patch_args = None

        def canvas_id(self, *, canvas_name, canvas_id=None):
            return canvas_id or f"cnv:user-a:{canvas_name}"

        def patch(self, *, canvas_name, canvas_id, patch, actor):
            self.patch_args = {
                "canvas_name": canvas_name,
                "canvas_id": canvas_id,
                "patch": patch,
                "actor": actor,
            }
            return {
                "ok": True,
                "canvas_ref": "cnv:main@3",
                "latest_ref": "cnv:main",
                "canvas_uri": "cnv:main@3",
                "canvas": {
                    "canvas_id": canvas_id,
                    "canvas_name": canvas_name,
                    "revision": 3,
                    "cards": [],
                },
                "changed_cards": [],
            }

    store = Store()
    provider = CanvasPinSearchNamedServiceProvider(store_factory=lambda _ctx: store)
    response = await provider.object_upsert(
        NamedServiceContext(
            tenant="tenant-a",
            project="project-a",
            user_id="user-a",
            actor={"name": "ui"},
        ),
        NamedServiceRequest(
            operation="object.upsert",
            namespace="cnv",
            object={
                "object_kind": CANVAS_BOARD_OBJECT_KIND,
                "canvas_name": "main",
                "patch": {
                    "operations": [
                        {"op": "move_card", "card_id": "card-1", "x": 10, "y": 20},
                        {"op": "resize_card", "card_id": "card-1", "w": 120, "h": 90},
                    ]
                },
            },
            base_revision="2",
        ),
    )

    assert response.ok is True
    assert store.patch_args["actor"] == "ui"
    assert store.patch_args["patch"]["base_revision"] == "2"
    assert [op["op"] for op in store.patch_args["patch"]["operations"]] == ["move_card", "resize_card"]
    assert response.extra["raw_result"]["canvas_ref"] == "cnv:main@3"


@pytest.mark.asyncio
async def test_canvas_provider_rejects_sanitized_storage_id_as_board_ref() -> None:
    class Store:
        def canvas_id(self, *, canvas_name, canvas_id=None):
            return canvas_id or f"cnv:user-a:{canvas_name}"

        def patch(self, **_kwargs):
            raise AssertionError("malformed refs must fail before storage mutation")

    provider = CanvasPinSearchNamedServiceProvider(store_factory=lambda _ctx: Store())
    response = await provider.object_upsert(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.upsert",
            namespace="cnv",
            object_ref="cnv:user-a_demo-board",
            object={
                "object_kind": CANVAS_CARD_OBJECT_KIND,
                "card": {"kind": "agent.text", "title": "Report", "logical_path": "fi:turn.outputs/report.html"},
            },
            base_revision="1",
        ),
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "canvas_object_ref_not_canonical"


@pytest.mark.asyncio
async def test_canvas_provider_typed_comment_upsert_maps_to_comment_card() -> None:
    class Store:
        def __init__(self) -> None:
            self.patch_args = None

        def canvas_id(self, *, canvas_name, canvas_id=None):
            return canvas_id or f"cnv:user-a:{canvas_name}"

        def patch(self, *, canvas_name, canvas_id, patch, actor):
            self.patch_args = {
                "canvas_name": canvas_name,
                "canvas_id": canvas_id,
                "patch": patch,
                "actor": actor,
            }
            return {
                "ok": True,
                "canvas_ref": "cnv:main@4",
                "latest_ref": "cnv:main",
                "canvas_uri": "cnv:main@4",
                "canvas": {
                    "canvas_id": canvas_id,
                    "canvas_name": canvas_name,
                    "revision": 4,
                    "cards": [{"id": "card-1", "kind": "memory", "logical_path": "mem:record:1"}],
                },
                "changed_cards": [{"id": "card-1", "kind": "memory", "logical_path": "mem:record:1"}],
            }

    store = Store()
    provider = CanvasPinSearchNamedServiceProvider(store_factory=lambda _ctx: store)
    response = await provider.object_upsert(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.upsert",
            namespace="cnv",
            object_ref="cnv:main",
            object={
                "object_kind": CANVAS_CARD_COMMENT_OBJECT_KIND,
                "canvas_name": "main",
                "card_id": "card-1",
                "text": "Looks correct.",
            },
            base_revision="3",
        ),
    )

    assert response.ok is True
    assert store.patch_args["patch"]["base_revision"] == "3"
    op = store.patch_args["patch"]["operations"][0]
    assert op == {"op": "comment_card", "card_id": "card-1", "text": "Looks correct."}


@pytest.mark.asyncio
async def test_canvas_provider_typed_deletion_suggestion_maps_to_suggest_deletion() -> None:
    class Store:
        def __init__(self) -> None:
            self.patch_args = None

        def canvas_id(self, *, canvas_name, canvas_id=None):
            return canvas_id or f"cnv:user-a:{canvas_name}"

        def patch(self, *, canvas_name, canvas_id, patch, actor):
            self.patch_args = {"canvas_name": canvas_name, "canvas_id": canvas_id, "patch": patch, "actor": actor}
            return {
                "ok": True,
                "canvas_ref": "cnv:main@5",
                "latest_ref": "cnv:main",
                "canvas_uri": "cnv:main@5",
                "canvas": {"canvas_id": canvas_id, "canvas_name": canvas_name, "revision": 5, "cards": []},
                "changed_cards": [],
            }

    store = Store()
    provider = CanvasPinSearchNamedServiceProvider(store_factory=lambda _ctx: store)
    response = await provider.object_upsert(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.upsert",
            namespace="cnv",
            object_ref="cnv:main",
            object={
                "object_kind": CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND,
                "canvas_name": "main",
                "card_id": "card-1",
                "reason": "Duplicate of newer card.",
            },
            base_revision="4",
        ),
    )

    assert response.ok is True
    op = store.patch_args["patch"]["operations"][0]
    assert op == {"op": "suggest_deletion", "card_id": "card-1", "reason": "Duplicate of newer card."}


@pytest.mark.asyncio
async def test_canvas_provider_rejects_direct_hosted_object_upsert() -> None:
    provider = CanvasPinSearchNamedServiceProvider(store_factory=lambda _ctx: object())
    response = await provider.object_upsert(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.upsert",
            namespace="cnv",
            object_ref="cnv:canvas/users/user-a/canvases/main/objects/user-text/card-1/v000001.md",
        ),
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "canvas_object_upsert_uses_card"


def test_canvas_schemas_declare_update_strategies() -> None:
    from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import (
        CANVAS_BOARD_SCHEMA,
        CANVAS_CARD_REPLACEMENT_SCHEMA,
        CANVAS_OPERATION_BATCH_SCHEMA,
    )

    # A board upsert replaces the whole cards array (not a delta).
    assert CANVAS_BOARD_SCHEMA["fields"]["cards"]["update_strategy"] == "replace"
    # An operation batch is the complete op list for THIS upsert (replace).
    assert CANVAS_OPERATION_BATCH_SCHEMA["fields"]["operations"]["update_strategy"] == "replace"
    # An in_place card replacement shallow-merges (patch) onto the existing card.
    assert CANVAS_CARD_REPLACEMENT_SCHEMA["fields"]["card"]["update_strategy"] == "patch"
