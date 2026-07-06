import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

# The pin search/index/clear ops live in the generic CanvasPinSearch service now;
# VersatileCanvasService delegates to it. Patch the ops there to observe wiring.
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import service as pin_search_service


def _load_canvas_service_module():
    path = Path(__file__).resolve().parents[1] / "services" / "canvas.py"
    spec = importlib.util.spec_from_file_location("versatile_canvas_service_test_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


canvas_service = _load_canvas_service_module()


async def _embed_texts(texts):
    return [[float(len(str(text)))] for text in texts]


class _Comm:
    user_id = None

    def __init__(self):
        self.events = []

    async def service_event(self, **kwargs):
        self.events.append(kwargs)


class _Entrypoint:
    models_service = SimpleNamespace(embed_texts=_embed_texts)

    def __init__(self, *, bundle_props=None):
        self.bundle_props = bundle_props or {}
        self.scope_filters = []
        self.comm = _Comm()

    def search_semantic_guard(self, *, flow: str):
        async def _guard(_query: str) -> bool:
            return True

        _guard.flow = flow
        return _guard

    def search_model_service(self, *, flow: str):
        class _ModelService:
            async def embed_texts(self, texts):
                vectors = await _embed_texts(texts)
                return [[float(v[0]) + 200.0] for v in vectors]

            async def embed_search_query(self, query: str, *, flow: str | None = None):
                self.flow = flow
                return [float(len(str(query))) + 100.0]

        service = _ModelService()
        service.flow = flow
        return service

    def runtime_identity(self):
        return {"tenant": "demo", "project": "project"}

    def _memory_store(self):
        return "memory-store"

    def _memory_scope(self):
        return "memory-scope"

    def _memory_scope_filter(self, value: str = ""):
        self.scope_filters.append(value)
        return value


def _service(monkeypatch, *, bundle_props=None):
    entrypoint = _Entrypoint(bundle_props=bundle_props)
    service = canvas_service.VersatileCanvasService(
        entrypoint,
        config=canvas_service.CanvasRuntimeConfig(
            bundle_id="versatile@test",
            artifact_prefix="canvas",
            origin_prefix="canvas",
            state_event_source_id="canvas.state",
            ui_event_type="canvas.patch.applied",
            artifact_resolver_name="canvas.bundle_artifact_storage",
        ),
        logger=SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(service, "store", lambda payload, user_id=None: SimpleNamespace(name="store"))
    return service


@pytest.mark.asyncio
async def test_canvas_search_awaits_pin_search_with_economics_guard(monkeypatch):
    seen = {}

    async def _search_pins(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "items": [], "results": [], "count": 0}

    monkeypatch.setattr(pin_search_service, "search_pins", _search_pins)
    service = _service(monkeypatch)

    result = await service.search({"user_id": "u-1", "query": "alpha"})

    assert result["ok"] is True
    assert seen["user_id"] == "u-1"
    assert "story_id" not in seen
    assert seen["semantic_guard"] is None
    assert await seen["embed_fn"](["alpha"]) == [[205.0]]
    assert seen["model_service"].flow == "canvas.pins.search"
    assert await seen["model_service"].embed_search_query("alpha") == [105.0]


@pytest.mark.asyncio
async def test_canvas_patch_indexes_after_successful_patch(monkeypatch):
    seen = {}

    def _patch(**kwargs):
        seen["patch"] = kwargs
        return {"ok": True, "canvas_id": "cnv-main", "canvas_name": "main"}

    async def _index_pins(**kwargs):
        seen["index"] = kwargs
        seen["index_embed"] = await kwargs["embed_fn"](["alpha"])
        return {"ok": True, "board": "cnv-main", "indexed": 1}

    monkeypatch.setattr(canvas_service.canvas_api, "patch", _patch)
    monkeypatch.setattr(pin_search_service, "index_pins", _index_pins)
    service = _service(monkeypatch)

    result = await service.apply_patch_payload({"user_id": "u-1", "canvas_name": "main"})

    assert result["ok"] is True
    assert seen["patch"]["user_id"] == "u-1"
    assert seen["index"]["payload"]["canvas_id"] == "cnv-main"
    assert seen["index"]["payload"]["canvas_name"] == "main"
    assert seen["index_embed"] == [[205.0]]
    assert seen["index"]["model_service"].flow == "canvas.pins.search"


@pytest.mark.asyncio
async def test_named_service_canvas_upsert_broadcasts_live_update(monkeypatch):
    def _patch(**_kwargs):
        return {
            "ok": True,
            "canvas_id": "cnv:u-1:main",
            "canvas_name": "main",
            "revision": 2,
            "canvas_ref": "cnv:main@2",
            "latest_ref": "cnv:main",
            "projection": {"canvas_name": "main", "revision": 2},
            "ui_event": {
                "type": "canvas.patch.applied",
                "canvas_name": "main",
                "revision": 2,
            },
        }

    monkeypatch.setattr(canvas_service.canvas_api, "patch", _patch)
    service = _service(monkeypatch)

    result = await service._named_service_upsert(
        canvas_service.NamedServiceContext(tenant="demo", project="project", user_id="u-1"),
        canvas_service.NamedServiceRequest(
            operation="object.upsert",
            namespace="cnv",
            object={
                "object_kind": canvas_service.CANVAS_CARD_OBJECT_KIND,
                "canvas_name": "main",
                "card": {"kind": "agent.text", "title": "hello"},
            },
            base_revision="1",
        ),
    )

    assert result["ok"] is True
    assert len(service.entrypoint.comm.events) == 1
    event = service.entrypoint.comm.events[0]
    assert event["type"] == "kdcube.data_bus.result"
    assert event["broadcast"] is True
    assert event["auto_markdown"] is False
    assert event["data"]["subject"] == "canvas.patch"
    assert event["data"]["object_ref"] == "cnv:main"
    assert event["data"]["data"]["revision"] == 2


@pytest.mark.asyncio
async def test_data_bus_originated_canvas_upsert_suppresses_live_bridge(monkeypatch):
    def _patch(**_kwargs):
        return {
            "ok": True,
            "canvas_id": "cnv:u-1:main",
            "canvas_name": "main",
            "revision": 2,
            "canvas_ref": "cnv:main@2",
            "latest_ref": "cnv:main",
            "projection": {"canvas_name": "main", "revision": 2},
            "ui_event": {"type": "canvas.patch.applied"},
        }

    monkeypatch.setattr(canvas_service.canvas_api, "patch", _patch)
    service = _service(monkeypatch)

    result = await service._named_service_upsert(
        canvas_service.NamedServiceContext(tenant="demo", project="project", user_id="u-1"),
        canvas_service.NamedServiceRequest(
            operation="object.upsert",
            namespace="cnv",
            object={
                "object_kind": canvas_service.CANVAS_CARD_OBJECT_KIND,
                "canvas_name": "main",
                "card": {"kind": "agent.text", "title": "hello"},
            },
            context={"suppress_live_broadcast": True},
            base_revision="1",
        ),
    )

    assert result["ok"] is True
    assert service.entrypoint.comm.events == []


@pytest.mark.asyncio
async def test_layout_only_patch_skips_indexing(monkeypatch):
    # A pure drag/resize (move_card/resize_card only) must NOT trigger the index op.
    seen = {}

    def _patch(**kwargs):
        return {"ok": True, "canvas_id": "cnv-main", "canvas_name": "main"}

    async def _index_pins(**kwargs):
        seen["index"] = kwargs
        return {"ok": True, "board": "cnv-main", "indexed": 1}

    monkeypatch.setattr(canvas_service.canvas_api, "patch", _patch)
    monkeypatch.setattr(pin_search_service, "index_pins", _index_pins)
    service = _service(monkeypatch)

    result = await service.apply_patch_payload({
        "user_id": "u-1",
        "canvas_name": "main",
        "patch": {"operations": [
            {"op": "move_card", "card_id": "c1", "x": 10, "y": 20},
            {"op": "resize_card", "card_id": "c2", "w": 100, "h": 80},
        ]},
    })

    assert result["ok"] is True
    assert "index" not in seen, "layout-only patch must not call index_pins"


@pytest.mark.asyncio
async def test_mixed_or_content_patch_still_indexes(monkeypatch):
    # A patch with any non-layout op (e.g. a content edit) must still index.
    seen = {}

    def _patch(**kwargs):
        return {"ok": True, "canvas_id": "cnv-main", "canvas_name": "main"}

    async def _index_pins(**kwargs):
        seen["index"] = kwargs
        return {"ok": True, "board": "cnv-main", "indexed": 1}

    monkeypatch.setattr(canvas_service.canvas_api, "patch", _patch)
    monkeypatch.setattr(pin_search_service, "index_pins", _index_pins)
    service = _service(monkeypatch)

    result = await service.apply_patch_payload({
        "user_id": "u-1",
        "canvas_name": "main",
        "patch": {"operations": [
            {"op": "move_card", "card_id": "c1", "x": 10, "y": 20},
            {"op": "upsert_card", "card_id": "c3", "label": "edited"},
        ]},
    })

    assert result["ok"] is True
    assert seen.get("index"), "a content op in the patch must still trigger index_pins"


@pytest.mark.asyncio
async def test_canvas_delete_clears_pin_index_after_successful_delete(monkeypatch):
    seen = {}

    def _delete(**kwargs):
        seen["delete"] = kwargs
        return {"ok": True, "canvas_id": "cnv-main", "canvas_name": "main"}

    async def _clear_pins(**kwargs):
        seen["clear"] = kwargs
        return {"ok": True, "board": "cnv-main", "removed": 2}

    monkeypatch.setattr(canvas_service.canvas_api, "delete", _delete)
    monkeypatch.setattr(pin_search_service, "clear_pins", _clear_pins)
    service = _service(monkeypatch)

    result = await service.delete({"user_id": "u-1", "canvas_name": "main"})

    assert result["ok"] is True
    assert seen["delete"]["user_id"] == "u-1"
    assert seen["clear"]["payload"]["canvas_id"] == "cnv-main"


@pytest.mark.asyncio
async def test_memory_object_action_uses_generic_named_service_resolver(monkeypatch):
    seen = {}

    def _register_configured_named_service_canvas_resolvers(registry, *, namespaces, tenant, project, logger):
        seen.update({"namespaces": namespaces, "tenant": tenant, "project": project})

        async def _resolve_mem(action_payload, resolver_user_id, action):
            seen["action_payload"] = action_payload
            seen["resolver_user_id"] = resolver_user_id
            seen["action"] = action
            return {
                "ok": True,
                "object_ref": action_payload["object_ref"],
                "resolver": "named_service.mem",
                "ui_event": {"target_surface": "sdk.memory.viewer", "memory_id": "mem_1"},
            }

        registry.register(
            canvas_service.CallableCanvasObjectResolver(
                namespace="mem",
                resolver="named_service.mem",
                resolver_status="configured",
                capabilities={"preview": True, "open": True, "download": False, "rehost": False},
                handler=_resolve_mem,
            )
        )
        return 1

    bundle_props = {
        "surfaces": {
            "as_consumer": {
                "ui": {
                    "canvas": {
                        "resolvers": [
                            {
                                "kind": "named_service",
                                "namespace": "mem",
                                "enabled": True,
                                "allowed": ["object.resolve", "object.action"],
                            }
                        ]
                    }
                }
            }
        }
    }

    monkeypatch.setattr(
        canvas_service,
        "register_configured_named_service_canvas_resolvers",
        _register_configured_named_service_canvas_resolvers,
    )
    service = _service(monkeypatch, bundle_props=bundle_props)

    result = await service.object_resolvers({}, user_id="u-1").object_action(
        {"object_ref": "mem:record:mem_1", "action": "open"},
        user_id="u-1",
    )

    assert result["ok"] is True
    assert result["resolver"] == "named_service.mem"
    assert seen["namespaces"]["mem"]["clients"]["canvas"]["resolver"]["enabled"] is True
    assert seen["namespaces"]["mem"]["clients"]["canvas"]["resolver"]["allowed_operations"] == [
        "object.resolve",
        "object.action",
    ]
    assert seen["action"] == "open"
    assert service.entrypoint.scope_filters == []


@pytest.mark.asyncio
async def test_conv_fi_download_uses_conversation_file_resolver(monkeypatch):
    service = _service(monkeypatch)

    result = await service.object_resolvers({}, user_id="u-1").object_action(
        {
            "action": "download",
            "object_ref": "conv:fi:conv_c1.turn_1.files/expense_tracker/README.md",
            "filename": "README.md",
            "mime": "text/markdown",
        },
        user_id="u-1",
    )

    assert result["ok"] is True
    assert result["object_kind"] == "conversation.file"
    assert "content_base64" not in result
    assert result["download_url"] == (
        "/api/cb/resources/demo/project/conv/u-1/c1/turn/turn_1/attachment/"
        "turn_1/files/expense_tracker/README.md/download"
    )
