# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import (
    CANVAS_BOARD_OBJECT_KIND,
    CANVAS_CARD_OBJECT_KIND,
    CANVAS_OBJECT_OBJECT_KIND,
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
    }
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
