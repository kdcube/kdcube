from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from kdcube_ai_app.apps.middleware.gateway import (
    AccountingContextBinder,
    FastAPIGatewayAdapter,
    STATE_AUTH_MODE,
    STATE_FLAG,
    STATE_SESSION,
    STATE_USER_TYPE,
)
from kdcube_ai_app.apps.middleware.gateway_policy import EndpointClass, GatewayPolicyResolver
from kdcube_ai_app.auth.sessions import UserSession, UserType
from kdcube_ai_app.apps.chat.sdk.config import get_settings


def _request(path: str, *, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": headers or [],
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


def test_gateway_policy_resolver_treats_public_bundle_mcp_route_as_guarded_ingress():
    resolver = GatewayPolicyResolver()
    request = _request("/api/integrations/bundles/tenant-a/project-a/bundle.demo/public/mcp/tools")

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
        "/api/integrations/bundles/tenant-a/project-a/bundle.demo/mcp/tools",
        "/api/integrations/bundles/tenant-a/project-a/bundle.demo/mcp/tools/list",
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


def test_gateway_adapter_header_only_auth_ignores_cookie_tokens():
    settings = get_settings()
    auth_cookie = settings.AUTH.AUTH_TOKEN_COOKIE_NAME.encode("utf-8")
    id_cookie = settings.AUTH.ID_TOKEN_COOKIE_NAME.encode("utf-8")
    request = _request(
        "/test/header-only-auth",
        headers=[
            (b"cookie", auth_cookie + b"=cookie-access; " + id_cookie + b"=cookie-id"),
        ],
    )
    adapter = FastAPIGatewayAdapter(
        gateway=SimpleNamespace(),
        policy_resolver=GatewayPolicyResolver(),
    )

    cookie_ctx = adapter._extract_context(request)
    header_only_ctx = adapter._extract_context(request, header_only_auth=True)

    assert cookie_ctx.authorization_header == "Bearer cookie-access"
    assert cookie_ctx.id_token == "cookie-id"
    assert header_only_ctx.authorization_header is None
    assert header_only_ctx.id_token is None


@pytest.mark.asyncio
async def test_accounting_http_dependency_can_force_header_only_auth():
    calls: list[bool] = []
    session = _session()

    async def _process_by_policy(request: Request, *, header_only_auth: bool = False):
        calls.append(header_only_auth)
        return session

    binder = AccountingContextBinder(
        gateway_adapter=SimpleNamespace(process_by_policy=_process_by_policy),
        storage_backend=object(),
        get_tenant_fn=lambda: "tenant-a",
        accounting_enabled=False,
        default_component="chat-rest",
    )
    request = _request("/test/header-only-auth")

    resolved = await binder.http_dependency("chat-rest", header_only_auth=True)(request)

    assert resolved is session
    assert calls == [True]


@pytest.mark.asyncio
async def test_accounting_http_dependency_does_not_reuse_default_auth_state_for_header_only_mode():
    calls: list[bool] = []
    default_session = _session()
    jwt_session = UserSession(
        session_id="session-jwt",
        user_type=UserType.REGISTERED,
        fingerprint="fp-jwt",
        roles=["kdcube:role:chat-user"],
        permissions=[],
    )

    async def _process_by_policy(request: Request, *, header_only_auth: bool = False):
        calls.append(header_only_auth)
        return jwt_session

    binder = AccountingContextBinder(
        gateway_adapter=SimpleNamespace(process_by_policy=_process_by_policy),
        storage_backend=object(),
        get_tenant_fn=lambda: "tenant-a",
        accounting_enabled=False,
        default_component="chat-rest",
    )
    request = _request("/test/header-only-auth")
    setattr(request.state, STATE_SESSION, default_session)
    setattr(request.state, STATE_USER_TYPE, default_session.user_type.value)
    setattr(request.state, STATE_FLAG, True)
    setattr(request.state, STATE_AUTH_MODE, "default")

    resolved = await binder.http_dependency("chat-rest", header_only_auth=True)(request)

    assert resolved is jwt_session
    assert calls == [True]
