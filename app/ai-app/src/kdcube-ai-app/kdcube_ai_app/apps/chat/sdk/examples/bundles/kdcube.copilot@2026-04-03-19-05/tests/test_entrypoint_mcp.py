from __future__ import annotations

import importlib.util
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.infra.plugin.bundle_loader import discover_bundle_interface_manifest


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_package(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name,
        path / "__init__.py",
        submodule_search_locations=[str(path)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_entrypoint_module():
    root = _bundle_root()
    package_name = "copilot_bundle_entrypoint_testpkg"
    _ensure_package(package_name, root)
    module_name = f"{package_name}.entrypoint"
    spec = importlib.util.spec_from_file_location(
        module_name,
        root / "entrypoint.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _request(*, header_name: str, header_value: str | None) -> Request:
    headers = []
    if header_value is not None:
        headers.append((header_name.lower().encode("utf-8"), header_value.encode("utf-8")))

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/integrations/bundles/demo/demo/kdcube.copilot/mcp/doc_reader",
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "http_version": "1.1",
        },
        receive=_receive,
    )


class _Relay:
    async def emit(self, *, event: str, data: dict, **kwargs) -> None:
        return None


class _Logger:
    def __init__(self):
        self.lines = []

    def log(self, message, level=None):
        self.lines.append((level, message))


def _comm() -> ChatCommunicator:
    return ChatCommunicator(
        emitter=_Relay(),
        tenant="tenant-a",
        project="project-a",
        user_id="user-1",
        user_type="registered",
        service={
            "request_id": "req-1",
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-1",
            "bundle_id": "kdcube.copilot",
        },
        conversation={
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "turn_id": "turn-1",
        },
    )


@pytest.mark.anyio
async def test_doc_reader_mcp_auth_uses_bundle_prop_and_bundle_secret(monkeypatch):
    mod = _load_entrypoint_module()
    workflow = object.__new__(mod.ReactWorkflow)
    workflow.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="kdcube.copilot"))
    workflow.bundle_prop = lambda key, default=None: (
        "X-Test-MCP-Token" if key == "mcp.doc_reader.auth.header_name" else default
    )

    async def _get_secret(key, **kwargs):
        return "shared-token" if key == "b:mcp.doc_reader.auth.shared_token" else None

    monkeypatch.setattr(mod, "get_secret", _get_secret)

    await workflow._require_doc_reader_mcp_auth(
        _request(header_name="X-Test-MCP-Token", header_value="shared-token")
    )


@pytest.mark.anyio
async def test_doc_reader_mcp_auth_rejects_invalid_header(monkeypatch):
    mod = _load_entrypoint_module()
    workflow = object.__new__(mod.ReactWorkflow)
    workflow.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="kdcube.copilot"))
    workflow.bundle_prop = lambda key, default=None: (
        "X-Test-MCP-Token" if key == "mcp.doc_reader.auth.header_name" else default
    )

    async def _get_secret(key, **kwargs):
        return "shared-token" if key == "b:mcp.doc_reader.auth.shared_token" else None

    monkeypatch.setattr(mod, "get_secret", _get_secret)

    with pytest.raises(HTTPException) as exc:
        await workflow._require_doc_reader_mcp_auth(
            _request(header_name="X-Test-MCP-Token", header_value="wrong-token")
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing or invalid X-Test-MCP-Token"


def test_doc_reader_mcp_exposes_public_and_authenticated_routes():
    mod = _load_entrypoint_module()
    manifest = discover_bundle_interface_manifest(mod.ReactWorkflow, bundle_id=mod.BUNDLE_ID)

    endpoints = {(spec.alias, spec.route): spec for spec in manifest.mcp_endpoints}

    public = endpoints[("kdcube-doc", "public")]
    assert public.method_name == "kdcube_doc_mcp"
    assert public.transport == "streamable-http"

    authenticated = endpoints[("doc_reader", "operations")]
    assert authenticated.method_name == "doc_reader_mcp"
    assert authenticated.transport == "streamable-http"


def test_kdcube_doc_mcp_does_not_require_credentials():
    mod = _load_entrypoint_module()
    workflow = object.__new__(mod.ReactWorkflow)
    sentinel_app = object()
    workflow._build_doc_reader_mcp_app = lambda *, name_suffix: sentinel_app

    assert workflow.kdcube_doc_mcp() is sentinel_app


def test_kdcube_doc_mcp_builds_fresh_stateless_app_per_request():
    mod = _load_entrypoint_module()
    workflow = object.__new__(mod.ReactWorkflow)

    built = []

    def _build(*, name_suffix: str):
        app = object()
        built.append((name_suffix, app))
        return app

    workflow._build_doc_reader_mcp_app = _build

    first = workflow.kdcube_doc_mcp()
    second = workflow.kdcube_doc_mcp()

    assert first is not second
    assert [name for name, _app in built] == ["kdcube-doc", "kdcube-doc"]


@pytest.mark.anyio
async def test_event_recording_configures_react_scope_from_endpoint_and_secret(monkeypatch):
    mod = _load_entrypoint_module()
    workflow = object.__new__(mod.ReactWorkflow)
    workflow._comm = _comm()
    workflow.logger = _Logger()
    workflow.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="kdcube.copilot"))
    workflow.bundle_prop = lambda key, default=None: (
        "http://stats.local/telemetry/events" if key == "telemetry_sink.endpoint_url" else default
    )
    async def _get_secret(key, **kwargs):
        return "telemetry-token" if key == mod.TELEMETRY_SINK_TOKEN_SECRET else None

    monkeypatch.setattr(mod, "get_secret", _get_secret)

    await workflow._configure_event_recording()

    recording = workflow.comm.recording_config()
    assert workflow.comm.event_sink is not None
    assert recording["enabled"] is True
    assert recording["scopes"][0]["scope"] == {
        "owner": "react",
        "bundle": "kdcube.copilot",
        "runtime": "on_message",
    }


@pytest.mark.anyio
async def test_doc_reader_mcp_call_records_and_sends_scoped_event():
    mod = _load_entrypoint_module()
    workflow = object.__new__(mod.ReactWorkflow)
    workflow._comm = _comm()
    workflow.logger = _Logger()
    workflow.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="kdcube.copilot"))
    sent_batches = []

    async def sink(batch, **kwargs):
        sent_batches.append((batch, kwargs))
        return {"accepted": len(batch)}

    async def _make_event_sink():
        return sink

    workflow._make_event_sink = _make_event_sink

    await workflow._record_doc_reader_mcp_call(
        {
            "mcp_name": "doc_reader",
            "tool": "search",
            "duration_ms": 12,
            "result_count": 2,
            "query_len": 40,
            "status": "completed",
        }
    )

    assert len(sent_batches) == 1
    batch, kwargs = sent_batches[0]
    assert len(batch) == 1
    assert batch[0]["type"] == "kdcube.copilot.mcp.call"
    assert batch[0]["data"]["tool"] == "search"
    assert kwargs["filter"] == mod.MCP_EVENT_RECORD_SELECTOR
    assert workflow.comm.export_recorded_events() == []
