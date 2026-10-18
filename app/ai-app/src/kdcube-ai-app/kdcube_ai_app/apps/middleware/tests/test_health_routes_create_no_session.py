# SPDX-License-Identifier: MIT
"""Health checks never create a session.

The ingress and processor health routes are polled by load balancers and
container checks. An unauthenticated request that reaches session resolution
gets an anonymous session stored for a day, so a health route must neither pass
through the gateway middleware's session step, nor declare a dependency that
resolves one, nor create one in its handler. A non-exempt path is the control:
it does reach session resolution, and the session-creation spy does catch it.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Match

from kdcube_ai_app.apps.chat.ingress import web_app as ingress
from kdcube_ai_app.apps.chat.proc import web_app as proc
from kdcube_ai_app.auth.sessions import RequestContext, SessionManager, UserType

HEALTH_ROUTES = {
    "ingress": (ingress, ("/health",)),
    "proc": (proc, ("/health", "/health/live")),
}


class _RecordingGateway:
    """Stands in for the gateway adapter; every session resolution is recorded."""

    def __init__(self) -> None:
        self.resolved: list[str] = []

    async def process_by_policy(self, request: Request, **_options):
        self.resolved.append(request.url.path)
        return SimpleNamespace(user_type=UserType.ANONYMOUS, session_id="control-session")


def _request(app, path: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"user-agent", b"ELB-HealthChecker/2.0")],
        "client": ("10.0.0.10", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
        "app": app,
    }
    return Request(scope)


async def _reach_handler(request: Request):
    return PlainTextResponse("ok")


@pytest.fixture
def recording_gateway(monkeypatch: pytest.MonkeyPatch):
    gateways = {}
    for name, (module, _paths) in HEALTH_ROUTES.items():
        gateway = _RecordingGateway()
        monkeypatch.setattr(module.app.state, "gateway_adapter", gateway, raising=False)
        monkeypatch.setattr(module.app.state, "draining", False, raising=False)
        gateways[name] = gateway
    return gateways


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "component,path",
    [(name, path) for name, (_module, paths) in HEALTH_ROUTES.items() for path in paths],
)
async def test_health_request_never_reaches_session_resolution(recording_gateway, component, path):
    module, _paths = HEALTH_ROUTES[component]

    response = await module.gateway_middleware(_request(module.app, path), _reach_handler)

    assert response.status_code == 200
    assert recording_gateway[component].resolved == []


@pytest.mark.asyncio
@pytest.mark.parametrize("component", sorted(HEALTH_ROUTES))
async def test_a_non_exempt_path_does_reach_session_resolution(recording_gateway, component):
    module, _paths = HEALTH_ROUTES[component]
    control = "/api/platform/require-session" if component == "ingress" else "/api/integrations/control"

    await module.gateway_middleware(_request(module.app, control), _reach_handler)

    assert recording_gateway[component].resolved == [control]


@pytest.mark.parametrize(
    "component,path",
    [(name, path) for name, (_module, paths) in HEALTH_ROUTES.items() for path in paths],
)
def test_health_route_declares_no_dependency_that_could_resolve_a_session(component, path):
    module, _paths = HEALTH_ROUTES[component]
    scope = {"type": "http", "method": "GET", "path": path}
    routes = [route for route in module.app.router.routes if route.matches(scope)[0] is Match.FULL]

    assert routes, f"{component} has no GET {path} route"
    for route in routes:
        assert route.dependant.dependencies == []


class _SessionCreationForbidden(AssertionError):
    pass


@pytest.fixture
def session_creation_spy(monkeypatch: pytest.MonkeyPatch):
    """Fail fast if anything in the request path creates or stores a session."""

    calls: list[str] = []

    async def forbidden(self, *args, **kwargs):
        calls.append("get_or_create_session")
        raise _SessionCreationForbidden("a health request created a session")

    async def forbidden_update(self, *args, **kwargs):
        calls.append("update_session")
        raise _SessionCreationForbidden("a health request stored a session")

    monkeypatch.setattr(SessionManager, "get_or_create_session", forbidden)
    monkeypatch.setattr(SessionManager, "update_session", forbidden_update)
    monkeypatch.setattr(proc, "get_settings", lambda: SimpleNamespace(TENANT="health-tenant", PROJECT="health-project"))
    for module in (ingress, proc):
        monkeypatch.setattr(module.app.state, "draining", False, raising=False)
    return calls


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("10.0.0.10", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path, headers={"user-agent": "ELB-HealthChecker/2.0"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "component,path",
    [(name, path) for name, (_module, paths) in HEALTH_ROUTES.items() for path in paths],
)
async def test_a_dispatched_health_request_runs_its_handler_without_creating_a_session(
        session_creation_spy, component, path):
    module, _paths = HEALTH_ROUTES[component]

    response = await _get(module.app, path)

    # The real handler ran (its own JSON body), and nothing created or stored a session.
    assert response.status_code in (200, 503)
    assert response.json().get("draining") is False
    assert session_creation_spy == []


@pytest.mark.asyncio
async def test_the_spy_does_catch_a_dispatched_request_that_creates_a_session(session_creation_spy, monkeypatch):
    """Control for the spy itself: a non-exempt request whose resolution creates a session is caught."""

    class _CreatingGateway:
        async def process_by_policy(self, request, **_options):
            context = RequestContext(client_ip="10.0.0.10", user_agent="control")
            return await SessionManager.get_or_create_session(object(), context, UserType.ANONYMOUS)

    monkeypatch.setattr(ingress.app.state, "gateway_adapter", _CreatingGateway(), raising=False)

    with pytest.raises(_SessionCreationForbidden):
        await _get(ingress.app, "/api/platform/require-session")
    assert session_creation_spy == ["get_or_create_session"]
