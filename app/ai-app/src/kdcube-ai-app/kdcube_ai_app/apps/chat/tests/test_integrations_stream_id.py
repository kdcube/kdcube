from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.responses import HTMLResponse
from starlette.datastructures import Headers
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_comm,
    get_current_request_context,
)
from kdcube_ai_app.infra.plugin import bundle_storage
from kdcube_ai_app.infra.plugin.agentic_loader import api
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.middleware.gateway import (
    STATE_STREAM_ID,
    bind_stream_id_to_request_state,
)


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        session_id="session-1",
        user_type=SimpleNamespace(value="registered"),
        user_id="user-1",
        username="elena",
        fingerprint="fp-1",
        roles=["registered"],
        permissions=["chat.use"],
        request_context=SimpleNamespace(user_timezone="Europe/Berlin", user_utc_offset_min=120),
    )


def _request(*, stream_id: str | None = None) -> SimpleNamespace:
    headers = Headers({get_settings().RUNTIME_CONFIG.STREAM_ID_HEADER_NAME: stream_id} if stream_id else {})
    return SimpleNamespace(
        headers=headers,
        state=SimpleNamespace(**({STATE_STREAM_ID: stream_id} if stream_id else {})),
        app=SimpleNamespace(state=SimpleNamespace(redis_async=object(), pg_pool=object())),
    )


async def _empty_bundle_props(*args, **kwargs):
    del args, kwargs
    return {}


def test_bind_stream_id_to_request_state_extracts_header():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/integrations/demo",
            "query_string": b"",
            "headers": [(get_settings().RUNTIME_CONFIG.STREAM_ID_HEADER_NAME.lower().encode("utf-8"), b"stream-123")],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "http_version": "1.1",
        }
    )

    stream_id = bind_stream_id_to_request_state(request)

    assert stream_id == "stream-123"
    assert getattr(request.state, STATE_STREAM_ID) == "stream-123"


@pytest.mark.asyncio
async def test_load_bundle_props_defaults_preserves_request_stream_id(monkeypatch):
    captured = {}

    async def _resolve_bundle_async(*args, **kwargs):
        bundle_id = kwargs.get("bundle_id") or (args[0] if args else None)
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=False)

    def _create_workflow_config(_cfg_req):
        return SimpleNamespace(ai_bundle_spec=None)

    async def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None, pg_pool=None):
        del spec, wf_config, redis, pg_pool
        captured["comm_context"] = comm_context
        workflow = SimpleNamespace(bundle_props_defaults={"demo": True}, configuration={"bundle_version": "1.0.0"})
        return workflow, None

    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance_async", _get_workflow_instance)

    result = await integrations._load_bundle_props_defaults(
        bundle_id="bundle.demo",
        tenant="tenant-a",
        project="project-a",
        request=_request(stream_id="stream-abc"),
        session=_session(),
    )

    assert captured["comm_context"].routing.socket_id == "stream-abc"
    assert result["demo"] is True
    assert result["bundle_version"] == "1.0.0"


@pytest.mark.asyncio
async def test_call_bundle_op_inner_preserves_request_stream_id(monkeypatch):
    captured = {}

    async def _resolve_bundle_async(*args, **kwargs):
        bundle_id = kwargs.get("bundle_id") or (args[0] if args else None)
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=False)

    def _create_workflow_config(_cfg_req):
        return SimpleNamespace(ai_bundle_spec=None)

    class _Workflow:
        @api(alias="ping")
        async def ping(self, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return {"pong": True}

    async def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None, pg_pool=None):
        del spec, wf_config, redis, pg_pool
        captured["comm_context"] = comm_context
        return _Workflow(), None

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            OPENAI_API_KEY=None,
            ANTHROPIC_API_KEY=None,
            TENANT="tenant-a",
            PROJECT="project-a",
        ),
    )
    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance_async", _get_workflow_instance)
    monkeypatch.setattr(integrations, "store_get_bundle_props", _empty_bundle_props)
    result = await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id=None,
        payload=integrations.BundleSuggestionsRequest(
            bundle_id="bundle.demo",
            data={"foo": "bar"},
        ),
        request=_request(stream_id="stream-xyz"),
        operation="ping",
        route="operations",
        session=_session(),
    )

    assert captured["comm_context"].routing.socket_id == "stream-xyz"
    assert captured["kwargs"]["user_id"] == "user-1"
    assert captured["kwargs"]["fingerprint"] == "fp-1"
    assert captured["kwargs"]["foo"] == "bar"
    assert result["bundle_id"] == "bundle.demo"
    assert result["ping"] == {"pong": True}


@pytest.mark.asyncio
async def test_call_bundle_op_inner_preserves_explicit_user_id_payload(monkeypatch):
    captured = {}

    async def _resolve_bundle_async(*args, **kwargs):
        bundle_id = kwargs.get("bundle_id") or (args[0] if args else None)
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=False)

    def _create_workflow_config(_cfg_req):
        return SimpleNamespace(ai_bundle_spec=None)

    class _Workflow:
        @api(alias="manage_user")
        async def manage_user(self, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return {"ok": True, "target_user_id": kwargs.get("user_id")}

    async def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None, pg_pool=None):
        del spec, wf_config, comm_context, redis, pg_pool
        return _Workflow(), None

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            OPENAI_API_KEY=None,
            ANTHROPIC_API_KEY=None,
            TENANT="tenant-a",
            PROJECT="project-a",
        ),
    )
    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance_async", _get_workflow_instance)
    monkeypatch.setattr(integrations, "store_get_bundle_props", _empty_bundle_props)
    result = await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id=None,
        payload=integrations.BundleSuggestionsRequest(
            bundle_id="bundle.demo",
            data={"user_id": "target-user-77", "foo": "bar"},
        ),
        request=_request(stream_id="stream-xyz"),
        operation="manage_user",
        route="operations",
        session=_session(),
    )

    assert captured["kwargs"]["user_id"] == "target-user-77"
    assert captured["kwargs"]["fingerprint"] == "fp-1"
    assert captured["kwargs"]["foo"] == "bar"
    assert result["manage_user"] == {"ok": True, "target_user_id": "target-user-77"}


@pytest.mark.asyncio
async def test_call_bundle_op_inner_binds_runtime_request_context(monkeypatch):
    captured = {}

    async def _resolve_bundle_async(*args, **kwargs):
        bundle_id = kwargs.get("bundle_id") or (args[0] if args else None)
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=True)

    def _create_workflow_config(_cfg_req):
        return SimpleNamespace(ai_bundle_spec=None)

    class _Workflow:
        @api(alias="ping")
        async def ping(self, **kwargs):
            del kwargs
            current = get_current_request_context()
            current_comm = get_current_comm()
            captured["before"] = (
                getattr(getattr(current, "user", None), "user_id", None),
                getattr(getattr(current, "routing", None), "socket_id", None),
                current_comm is not None,
            )
            await asyncio.sleep(0)
            current = get_current_request_context()
            current_comm = get_current_comm()
            captured["after"] = (
                getattr(getattr(current, "user", None), "user_id", None),
                getattr(getattr(current, "routing", None), "socket_id", None),
                current_comm is not None,
            )
            return {"pong": True}

    workflow = _Workflow()

    async def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None, pg_pool=None):
        del spec, wf_config, comm_context, redis, pg_pool
        return workflow, None

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            OPENAI_API_KEY=None,
            ANTHROPIC_API_KEY=None,
            TENANT="tenant-a",
            PROJECT="project-a",
        ),
    )
    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance_async", _get_workflow_instance)
    monkeypatch.setattr(integrations, "store_get_bundle_props", _empty_bundle_props)
    await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id=None,
        payload=integrations.BundleSuggestionsRequest(bundle_id="bundle.demo"),
        request=_request(stream_id="stream-ctx"),
        operation="ping",
        route="operations",
        session=_session(),
    )

    assert captured["before"] == ("user-1", "stream-ctx", True)
    assert captured["after"] == ("user-1", "stream-ctx", True)


@pytest.mark.asyncio
async def test_call_bundle_op_inner_uses_default_bundle_when_omitted(monkeypatch):
    captured = {}

    async def _resolve_bundle_async(*args, **kwargs):
        bundle_id = kwargs.get("bundle_id") or (args[0] if args else None)
        captured["resolved_bundle_id"] = bundle_id
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=False)

    def _create_workflow_config(_cfg_req):
        return SimpleNamespace(ai_bundle_spec=None)

    class _Workflow:
        @api(alias="ping")
        async def ping(self, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return {"pong": True}

    async def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None, pg_pool=None):
        del spec, wf_config, redis, pg_pool
        captured["comm_context"] = comm_context
        return _Workflow(), None

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            OPENAI_API_KEY=None,
            ANTHROPIC_API_KEY=None,
            TENANT="tenant-a",
            PROJECT="project-a",
        ),
    )
    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance_async", _get_workflow_instance)
    monkeypatch.setattr(integrations, "store_get_bundle_props", _empty_bundle_props)
    async def _load_registry(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(default_bundle_id="bundle.default", bundles={})

    monkeypatch.setattr(integrations, "load_registry", _load_registry)

    result = await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id=None,
        payload=integrations.BundleSuggestionsRequest(data={"foo": "bar"}),
        request=_request(stream_id="stream-default"),
        operation="ping",
        route="operations",
        session=_session(),
    )

    assert captured["resolved_bundle_id"] == "bundle.default"
    assert captured["comm_context"].routing.bundle_id == "bundle.default"
    assert result["bundle_id"] == "bundle.default"
    assert result["ping"] == {"pong": True}


def test_serve_static_asset_builds_ui_on_first_request(monkeypatch, tmp_path):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    storage_root = tmp_path / "storage"

    async def _resolve_bundle_async(*args, **kwargs):
        bundle_id = kwargs.get("bundle_id") or (args[0] if args else None)
        return SimpleNamespace(id=bundle_id, path=str(bundle_root), module="entrypoint", singleton=False, version="v1")

    async def _load_bundle_props_defaults(**kwargs):
        assert kwargs.get("evict_before_load") is False
        ui_root = storage_root / "ui"
        ui_root.mkdir(parents=True, exist_ok=True)
        (ui_root / "index.html").write_text("<html><head></head><body>Echo UI</body></html>", encoding="utf-8")
        return {"ui": {"main_view": {"src_folder": "ui/main"}}}

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"),
    )
    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "_load_bundle_props_defaults", _load_bundle_props_defaults)
    monkeypatch.setattr(bundle_storage, "storage_for_spec", lambda **kwargs: storage_root)

    response = asyncio.run(
        integrations.serve_static_asset(
            tenant="tenant-a",
            project="project-a",
            bundle_id="echo.ui@2026-03-30",
            request=_request(),
            session=_session(),
        )
    )

    assert isinstance(response, HTMLResponse)
    html = response.body.decode("utf-8")
    assert "Echo UI" in html
    assert '/api/integrations/static/tenant-a/project-a/echo.ui@2026-03-30/' in html
    assert "data-kdcube-resize-reporter" in html
    assert "type:'kdcube-resize'" in html
    assert "contentWidth" in html
    assert "viewportWidth" in html


def test_serve_static_asset_refreshes_existing_ui_on_entrypoint_request(monkeypatch, tmp_path):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    storage_root = tmp_path / "storage"
    ui_root = storage_root / "ui"
    ui_root.mkdir(parents=True)
    (ui_root / "index.html").write_text("<html><head></head><body>Stale UI</body></html>", encoding="utf-8")
    load_calls = []

    async def _resolve_bundle_async(*args, **kwargs):
        bundle_id = kwargs.get("bundle_id") or (args[0] if args else None)
        return SimpleNamespace(id=bundle_id, path=str(bundle_root), module="entrypoint", singleton=False, version="v1")

    async def _load_bundle_props_defaults(**kwargs):
        load_calls.append((kwargs["bundle_id"], kwargs.get("evict_before_load")))
        (ui_root / "index.html").write_text("<html><head></head><body>Fresh UI</body></html>", encoding="utf-8")
        return {"ui": {"main_view": {"src_folder": "ui/main"}}}

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"),
    )
    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "_load_bundle_props_defaults", _load_bundle_props_defaults)
    monkeypatch.setattr(bundle_storage, "storage_for_spec", lambda **kwargs: storage_root)

    response = asyncio.run(
        integrations.serve_static_asset(
            tenant="tenant-a",
            project="project-a",
            bundle_id="echo.ui@2026-03-30",
            request=_request(),
            session=_session(),
        )
    )

    assert load_calls == [("echo.ui@2026-03-30", False)]
    assert isinstance(response, HTMLResponse)
    html = response.body.decode("utf-8")
    assert "Fresh UI" in html
    assert "Stale UI" not in html
    assert "data-kdcube-resize-reporter" in html

    response = asyncio.run(
        integrations.serve_static_asset(
            tenant="tenant-a",
            project="project-a",
            bundle_id="echo.ui@2026-03-30",
            request=_request(),
            session=_session(),
        )
    )

    assert load_calls == [("echo.ui@2026-03-30", False)]
    assert isinstance(response, HTMLResponse)
    html = response.body.decode("utf-8")
    assert "Fresh UI" in html
    assert "data-kdcube-resize-reporter" in html


def test_serve_static_asset_does_not_refresh_existing_asset_request(monkeypatch, tmp_path):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    storage_root = tmp_path / "storage"
    asset = storage_root / "ui" / "assets" / "app.js"
    asset.parent.mkdir(parents=True)
    asset.write_text("console.log('cached asset');", encoding="utf-8")

    async def _resolve_bundle_async(*args, **kwargs):
        bundle_id = kwargs.get("bundle_id") or (args[0] if args else None)
        return SimpleNamespace(id=bundle_id, path=str(bundle_root), module="entrypoint", singleton=False, version="v1")

    async def _load_bundle_props_defaults(**kwargs):
        del kwargs
        raise AssertionError("asset requests should not force a UI refresh")

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"),
    )
    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "_load_bundle_props_defaults", _load_bundle_props_defaults)
    monkeypatch.setattr(bundle_storage, "storage_for_spec", lambda **kwargs: storage_root)

    response = asyncio.run(
        integrations.serve_static_asset(
            tenant="tenant-a",
            project="project-a",
            bundle_id="echo.ui@2026-03-30",
            path="assets/app.js",
            request=_request(),
            session=_session(),
        )
    )

    assert getattr(response, "path", None) == str(asset)
