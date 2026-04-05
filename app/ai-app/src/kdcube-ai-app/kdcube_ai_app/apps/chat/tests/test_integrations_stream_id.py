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
from kdcube_ai_app.infra.namespaces import CONFIG
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
    headers = Headers({CONFIG.STREAM_ID_HEADER_NAME: stream_id} if stream_id else {})
    return SimpleNamespace(
        headers=headers,
        state=SimpleNamespace(**({STATE_STREAM_ID: stream_id} if stream_id else {})),
        app=SimpleNamespace(state=SimpleNamespace(redis_async=object())),
    )


def test_bind_stream_id_to_request_state_extracts_header():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/integrations/demo",
            "query_string": b"",
            "headers": [(CONFIG.STREAM_ID_HEADER_NAME.lower().encode("utf-8"), b"stream-123")],
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

    async def _resolve_bundle_async(bundle_id, override=None):
        del override
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=False)

    def _create_workflow_config(_cfg_req):
        return SimpleNamespace(ai_bundle_spec=None)

    def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None):
        del spec, wf_config, redis
        captured["comm_context"] = comm_context
        workflow = SimpleNamespace(bundle_props_defaults={"demo": True}, configuration={"bundle_version": "1.0.0"})
        return workflow, None

    monkeypatch.setattr(integrations, "resolve_bundle_async", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance", _get_workflow_instance)

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

    async def _resolve_bundle_async(bundle_id, override=None):
        del override
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=False)

    def _create_workflow_config(_cfg_req):
        return SimpleNamespace(ai_bundle_spec=None)

    class _Workflow:
        @api(alias="ping")
        async def ping(self, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return {"pong": True}

    def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None):
        del spec, wf_config, redis
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
    monkeypatch.setattr(integrations, "resolve_bundle_async", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance", _get_workflow_instance)
    monkeypatch.setattr(integrations, "get_default_id", lambda: None)

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
async def test_call_bundle_op_inner_binds_runtime_request_context(monkeypatch):
    captured = {}

    async def _resolve_bundle_async(bundle_id, override=None):
        del override
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

    def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None):
        del spec, wf_config, comm_context, redis
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
    monkeypatch.setattr(integrations, "resolve_bundle_async", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance", _get_workflow_instance)
    monkeypatch.setattr(integrations, "get_default_id", lambda: None)

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

    async def _resolve_bundle_async(bundle_id, override=None):
        del override
        captured["resolved_bundle_id"] = bundle_id
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=False)

    def _create_workflow_config(_cfg_req):
        return SimpleNamespace(ai_bundle_spec=None)

    class _Workflow:
        @api(alias="ping")
        async def ping(self, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return {"pong": True}

    def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None):
        del spec, wf_config, redis
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
    monkeypatch.setattr(integrations, "resolve_bundle_async", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance", _get_workflow_instance)
    monkeypatch.setattr(integrations, "get_default_id", lambda: "bundle.default")

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


@pytest.mark.asyncio
async def test_serve_static_asset_builds_ui_on_first_request(monkeypatch, tmp_path):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    storage_root = tmp_path / "storage"

    async def _resolve_bundle_async(bundle_id, override=None):
        del override
        return SimpleNamespace(id=bundle_id, path=str(bundle_root), module="entrypoint", singleton=False, version="v1")

    async def _load_bundle_props_defaults(**kwargs):
        del kwargs
        ui_root = storage_root / "ui"
        ui_root.mkdir(parents=True, exist_ok=True)
        (ui_root / "index.html").write_text("<html><head></head><body>Echo UI</body></html>", encoding="utf-8")
        return {"ui": {"main_view": {"src_folder": "ui-src"}}}

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"),
    )
    monkeypatch.setattr(integrations, "resolve_bundle_async", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "_load_bundle_props_defaults", _load_bundle_props_defaults)
    monkeypatch.setattr(bundle_storage, "storage_for_spec", lambda **kwargs: storage_root)

    response = await integrations.serve_static_asset(
        tenant="tenant-a",
        project="project-a",
        bundle_id="echo.ui@2026-03-30",
        request=_request(),
        session=_session(),
    )

    assert isinstance(response, HTMLResponse)
    assert "Echo UI" in response.body.decode("utf-8")
    assert '/api/integrations/static/tenant-a/project-a/echo.ui@2026-03-30/' in response.body.decode("utf-8")
