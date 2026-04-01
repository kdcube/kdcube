from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
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
    headers = Headers({"KDC-Stream-ID": stream_id} if stream_id else {})
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
            "headers": [(b"kdc-stream-id", b"stream-123")],
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

    result = await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        payload=integrations.BundleSuggestionsRequest(
            bundle_id="bundle.demo",
            data={"foo": "bar"},
        ),
        request=_request(stream_id="stream-xyz"),
        operation="ping",
        session=_session(),
    )

    assert captured["comm_context"].routing.socket_id == "stream-xyz"
    assert captured["kwargs"]["user_id"] == "user-1"
    assert captured["kwargs"]["fingerprint"] == "fp-1"
    assert captured["kwargs"]["foo"] == "bar"
    assert result["bundle_id"] == "bundle.demo"
    assert result["ping"] == {"pong": True}
