# SPDX-License-Identifier: MIT
"""Health checks never create a session.

The ingress and processor health routes are polled by load balancers and
container checks. An unauthenticated request that reaches session resolution
gets an anonymous session stored for a day, so a health route must neither pass
through the gateway middleware's session step nor declare a dependency that
resolves one. A non-exempt path is the control: it does reach session
resolution.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Match

from kdcube_ai_app.apps.chat.ingress import web_app as ingress
from kdcube_ai_app.apps.chat.proc import web_app as proc
from kdcube_ai_app.auth.sessions import UserType

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
