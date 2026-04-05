from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from kdcube_ai_app.apps.middleware.gateway import (
    AccountingContextBinder,
    STATE_FLAG,
    STATE_SESSION,
    STATE_USER_TYPE,
)
from kdcube_ai_app.apps.middleware.gateway_policy import EndpointClass, GatewayPolicyResolver
from kdcube_ai_app.auth.sessions import UserSession, UserType


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "http_version": "1.1",
            "path_params": {"tenant": "tenant-a", "project": "project-a"},
        }
    )


def _session() -> UserSession:
    return UserSession(
        session_id="session-1",
        user_type=UserType.ANONYMOUS,
        fingerprint="fp-1",
        roles=[],
        permissions=[],
    )


def test_gateway_policy_resolver_treats_public_bundle_route_as_guarded_ingress():
    resolver = GatewayPolicyResolver()
    request = _request("/api/integrations/bundles/tenant-a/project-a/bundle.demo/public/webhook")

    policy = resolver.resolve(request)

    assert policy.cls == EndpointClass.CHAT_INGRESS
    assert policy.bypass_throttling is False
    assert policy.bypass_gate is False
    assert policy.bypass_backpressure is False


@pytest.mark.parametrize(
    "path",
    [
        "/api/integrations/bundles/tenant-a/project-a/bundle.demo",
        "/api/integrations/bundles/tenant-a/project-a/bundle.demo/widgets",
        "/api/integrations/bundles/tenant-a/project-a/bundle.demo/widgets/preferences",
        "/api/integrations/static/tenant-a/project-a/bundle.demo",
        "/api/integrations/static/tenant-a/project-a/bundle.demo/index.html",
    ],
)
def test_gateway_policy_resolver_treats_bundle_interface_widget_and_static_routes_as_guarded_ingress(path: str):
    resolver = GatewayPolicyResolver()
    request = _request(path)

    policy = resolver.resolve(request)

    assert policy.cls == EndpointClass.CHAT_INGRESS
    assert policy.bypass_throttling is False
    assert policy.bypass_gate is False
    assert policy.bypass_backpressure is False


@pytest.mark.asyncio
async def test_accounting_http_dependency_uses_policy_resolution_when_state_missing():
    calls: list[str] = []
    session = _session()

    async def _process_by_policy(request: Request):
        calls.append(request.url.path)
        return session

    binder = AccountingContextBinder(
        gateway_adapter=SimpleNamespace(process_by_policy=_process_by_policy),
        storage_backend=object(),
        get_tenant_fn=lambda: "tenant-a",
        accounting_enabled=False,
        default_component="chat-rest",
    )
    request = _request("/api/integrations/bundles/tenant-a/project-a/bundle.demo/public/webhook")

    resolved = await binder.http_dependency("chat-rest")(request)

    assert resolved is session
    assert calls == ["/api/integrations/bundles/tenant-a/project-a/bundle.demo/public/webhook"]
    assert getattr(request.state, STATE_SESSION) is session
    assert getattr(request.state, STATE_USER_TYPE) == session.user_type.value
    assert getattr(request.state, STATE_FLAG) is True


@pytest.mark.asyncio
async def test_accounting_http_dependency_reuses_existing_request_state_session():
    calls: list[str] = []
    session = _session()

    async def _process_by_policy(request: Request):
        calls.append(request.url.path)
        return session

    binder = AccountingContextBinder(
        gateway_adapter=SimpleNamespace(process_by_policy=_process_by_policy),
        storage_backend=object(),
        get_tenant_fn=lambda: "tenant-a",
        accounting_enabled=False,
        default_component="chat-rest",
    )
    request = _request("/profile")
    setattr(request.state, STATE_SESSION, session)
    setattr(request.state, STATE_USER_TYPE, session.user_type.value)
    setattr(request.state, STATE_FLAG, True)

    resolved = await binder.http_dependency("chat-rest")(request)

    assert resolved is session
    assert calls == []
