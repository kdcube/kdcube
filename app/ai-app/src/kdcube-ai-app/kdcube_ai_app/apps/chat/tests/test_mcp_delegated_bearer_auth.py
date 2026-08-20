# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Bundle-MCP delegated-bearer auth: the narrow Connection Hub mode.

MCP integration routes are header-only (no cookies, no query-param auth) but
consult EXACTLY the Connection Hub surface's delegated platform BEARER branch,
so verified delegated automation identities resolve with their grantor roles
while everything else keeps failing closed to the anonymous session.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType
from kdcube_ai_app.apps.chat.sdk.solutions.connections.request_auth import (
    CONNECTION_HUB_DELEGATED_BEARER_ONLY,
    RequestAuthResolver,
)


def _session(user_type: UserType, roles: list[str] | None = None) -> UserSession:
    return UserSession(
        session_id="s-1",
        user_type=user_type,
        fingerprint="fp",
        roles=list(roles or []),
        permissions=[],
    )


async def _session_factory(context, user_type, user_data):
    del context
    roles = list((user_data or {}).get("roles") or [])
    return _session(user_type, roles)


class _HubSurface:
    """Fake Connection Hub surface recording which branch was consulted."""

    def __init__(self, *, bearer_session: UserSession | None):
        self.bearer_session = bearer_session
        self.full_calls = 0
        self.bearer_calls = 0

    async def __call__(self, request, context, session_factory):
        self.full_calls += 1
        return _session(UserType.REGISTERED, ["kdcube:role:registered"])

    async def authenticate_delegated_bearer(self, request, context, session_factory):
        self.bearer_calls += 1
        return self.bearer_session


def _resolver(surface) -> RequestAuthResolver:
    resolver = RequestAuthResolver(auth_manager=None, session_factory=_session_factory)
    resolver.install_connection_hub_surface(surface)
    return resolver


def _context(authorization: str = "") -> RequestContext:
    return RequestContext(
        client_ip="127.0.0.1",
        user_agent="pytest",
        authorization_header=authorization or None,
    )


REQUEST = SimpleNamespace(headers={}, state=SimpleNamespace())


def test_delegated_bearer_only_accepts_projected_identity():
    projected = _session(
        UserType.EXTERNAL, ["kdcube:role:registered", "kdcube:role:super-admin"]
    )
    surface = _HubSurface(bearer_session=projected)
    resolver = _resolver(surface)
    session = asyncio.run(
        resolver.resolve_session(
            REQUEST,
            _context("Bearer kst1.xxx"),
            allow_connection_hub=CONNECTION_HUB_DELEGATED_BEARER_ONLY,
        )
    )
    assert session is projected
    assert "kdcube:role:super-admin" in session.roles
    assert surface.bearer_calls == 1
    # The full surface (cookies/providers/telegram selector) is NEVER consulted.
    assert surface.full_calls == 0


def test_delegated_bearer_only_unknown_token_stays_anonymous():
    surface = _HubSurface(bearer_session=None)  # garbage/expired kst1 -> None
    resolver = _resolver(surface)
    session = asyncio.run(
        resolver.resolve_session(
            REQUEST,
            _context("Bearer garbage"),
            allow_connection_hub=CONNECTION_HUB_DELEGATED_BEARER_ONLY,
        )
    )
    assert session.user_type == UserType.ANONYMOUS
    assert session.roles == []
    assert surface.full_calls == 0


def test_disabled_hub_mode_consults_nothing():
    surface = _HubSurface(bearer_session=_session(UserType.EXTERNAL, ["x"]))
    resolver = _resolver(surface)
    session = asyncio.run(
        resolver.resolve_session(
            REQUEST, _context("Bearer kst1.xxx"), allow_connection_hub=False
        )
    )
    assert session.user_type == UserType.ANONYMOUS
    assert surface.full_calls == 0 and surface.bearer_calls == 0


def test_full_mode_unchanged():
    surface = _HubSurface(bearer_session=None)
    resolver = _resolver(surface)
    session = asyncio.run(
        resolver.resolve_session(REQUEST, _context(), allow_connection_hub=True)
    )
    assert surface.full_calls == 1
    assert session.user_type == UserType.REGISTERED


def test_platform_jwt_wins_before_hub_in_every_mode():
    platform_session = _session(UserType.PRIVILEGED, ["kdcube:role:super-admin"])

    class _Platform:
        async def __call__(self, request, context, session_factory):
            return platform_session

    surface = _HubSurface(bearer_session=None)
    resolver = _resolver(surface)
    resolver._platform_authenticator = _Platform()  # type: ignore[assignment]
    for mode in (True, False, CONNECTION_HUB_DELEGATED_BEARER_ONLY):
        session = asyncio.run(
            resolver.resolve_session(
                REQUEST, _context("Bearer platform-jwt"), allow_connection_hub=mode
            )
        )
        assert session is platform_session
    assert surface.full_calls == 0 and surface.bearer_calls == 0


def test_surface_without_narrow_method_fails_closed():
    class _LegacySurface:
        async def __call__(self, request, context, session_factory):
            raise AssertionError("full surface must not be consulted in narrow mode")

    resolver = _resolver(_LegacySurface())
    session = asyncio.run(
        resolver.resolve_session(
            REQUEST,
            _context("Bearer kst1.xxx"),
            allow_connection_hub=CONNECTION_HUB_DELEGATED_BEARER_ONLY,
        )
    )
    assert session.user_type == UserType.ANONYMOUS


def test_mcp_projection_lands_on_request_state(monkeypatch):
    """_apply_delegated_mcp_runtime_projection upgrades request.state.user_session
    so bundle-side gates observe the projected (grantor-role) identity."""
    from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
    from kdcube_ai_app.apps.middleware.gateway import STATE_SESSION

    projection = {
        "user_id": "grantor-1",
        "user_type": "external",
        "username": "integration:automation:aut_x:grantor-1",
        "roles": ["kdcube:role:registered", "kdcube:role:super-admin"],
        "permissions": ["named_services:use"],
        "identity_authority": {"authority_id": "delegated_client"},
        "delegate_identity": "integration:automation:aut_x:grantor-1",
        "grantor_user_id": "grantor-1",
        "grants": [],
        "identity_scope": "grantor",
    }
    monkeypatch.setattr(
        integrations, "delegated_mcp_runtime_projection", lambda request: projection
    )
    session = _session(UserType.ANONYMOUS)
    request = SimpleNamespace(state=SimpleNamespace())
    comm_context = SimpleNamespace(user=None, actor=None)
    integrations._apply_delegated_mcp_runtime_projection(
        request=request,
        session=session,
        comm_context=comm_context,
        bundle_id="press@2026-08-16",
        endpoint_alias="press",
    )
    attached = getattr(request.state, STATE_SESSION)
    assert attached is session
    assert attached.user_id == "grantor-1"
    assert "kdcube:role:super-admin" in attached.roles
    assert attached.user_type == UserType.EXTERNAL


def test_mcp_projection_absent_leaves_state_untouched(monkeypatch):
    from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
    from kdcube_ai_app.apps.middleware.gateway import STATE_SESSION

    monkeypatch.setattr(
        integrations, "delegated_mcp_runtime_projection", lambda request: {}
    )
    session = _session(UserType.ANONYMOUS)
    request = SimpleNamespace(state=SimpleNamespace())
    integrations._apply_delegated_mcp_runtime_projection(
        request=request,
        session=session,
        comm_context=SimpleNamespace(user=None, actor=None),
        bundle_id="b",
        endpoint_alias="e",
    )
    assert getattr(request.state, STATE_SESSION, None) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# Regression: the narrow slice must NEVER reject (publisher-lane incident).
# Pre-slice, managed-mode MCP routes resolved delegated tokens via the
# managed guard alone; a slice failure (catalog ambiguity, denial, verifier
# error) must contribute nothing so that path keeps working unchanged.
# ---------------------------------------------------------------------------

from kdcube_ai_app.auth.AuthManager import AuthenticationError, AuthorizationError
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authentication_surface import (
    ConnectionHubAuthenticationSurface,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    oauth_delegated_config_from_connections,
)


def _surface_with_bearer(raiser_or_session):
    surface = object.__new__(ConnectionHubAuthenticationSurface)
    surface.tenant = "t"
    surface.project = "p"

    async def _bearer(request, context, session_factory):
        if isinstance(raiser_or_session, Exception):
            raise raiser_or_session
        return raiser_or_session

    surface._try_delegated_platform_bearer = _bearer
    return surface


@pytest.mark.parametrize(
    "failure",
    [
        AuthorizationError("delegated platform resource has ambiguous operation catalog: x"),
        AuthenticationError("bad token"),
        RuntimeError("verifier exploded"),
    ],
)
def test_slice_never_rejects_any_resolution_failure(failure):
    surface = _surface_with_bearer(failure)
    result = asyncio.run(
        surface.authenticate_delegated_bearer(
            SimpleNamespace(url=SimpleNamespace(path="/api/x")), _context("Bearer kst1.y"), _session_factory
        )
    )
    assert result is None  # anonymous downstream; managed guard proceeds


def test_slice_failure_resolves_anonymous_end_to_end():
    """Through the resolver: an ambiguity raise inside the slice must not
    escape as 401/403 from the middleware — the request resolves anonymous
    and the managed-mode guard downstream owns the token, as pre-change."""
    surface = _surface_with_bearer(
        AuthorizationError("delegated platform resource has ambiguous operation catalog: x")
    )
    resolver = _resolver(surface)
    session = asyncio.run(
        resolver.resolve_session(
            REQUEST,
            _context("Bearer kst1.y"),
            allow_connection_hub=CONNECTION_HUB_DELEGATED_BEARER_ONLY,
        )
    )
    assert session.user_type == UserType.ANONYMOUS


def test_slice_success_still_projects():
    projected = _session(UserType.EXTERNAL, ["kdcube:role:super-admin"])
    surface = _surface_with_bearer(projected)
    result = asyncio.run(
        surface.authenticate_delegated_bearer(
            SimpleNamespace(url=SimpleNamespace(path="/api/x")), _context("Bearer kst1.y"), _session_factory
        )
    )
    assert result is projected


# ---------------------------------------------------------------------------
# Most-specific-match preference: a specific resource row beats the
# `resource: '*'` admin fallback; the wildcard covers only what nothing
# specific matches.
# ---------------------------------------------------------------------------

_NAMED_SERVICES_PATTERN = "*/kdcube-services@1-0/public/mcp/named_services*"


def _catalog():
    """A real config, parsed by the reader the request path uses."""
    return oauth_delegated_config_from_connections(
        {
            "delegated_credentials": {
                "oauth": {
                    "resources": [
                        # Wildcard declared FIRST: order must not matter.
                        {
                            "resource": "*",
                            "grants": ["platform:admin"],
                            "admin_only": True,
                        },
                        {
                            "resource": _NAMED_SERVICES_PATTERN,
                            "grants": ["named_services:use"],
                            "tools": {
                                "named_services_list": {
                                    "label": "List",
                                    "grants": ["named_services:use"],
                                },
                                "named_services_action": {
                                    "label": "Action",
                                    "grants": ["named_services:use"],
                                },
                            },
                        },
                    ],
                }
            }
        }
    )


def test_specific_row_beats_wildcard():
    row = _catalog().resource_config(
        "https://host/api/integrations/bundles/t/p/kdcube-services@1-0/public/mcp/named_services"
    )
    assert row is not None and row.resource == _NAMED_SERVICES_PATTERN
    assert row.admin_only is False  # the specific row keeps its own semantics


def test_wildcard_serves_only_the_unmatched():
    row = _catalog().resource_config(
        "https://host/api/integrations/bundles/t/p/press@2026-08-16/mcp/press"
    )
    assert row is not None and row.resource == "*" and row.admin_only is True
