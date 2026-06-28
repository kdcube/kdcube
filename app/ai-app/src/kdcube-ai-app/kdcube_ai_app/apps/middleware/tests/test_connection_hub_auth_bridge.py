# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from starlette.requests import Request

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators.models import AuthenticatedRequest
from kdcube_ai_app.apps.middleware.connection_hub_auth import ConnectionHubRequestAuthBridge
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType


def _request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/integrations/bundles/demo/project/user-memories/operations/memories_widget_data",
        "query_string": b"",
        "headers": raw_headers,
        "server": ("testserver", 80),
        "scheme": "https",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


async def test_connection_hub_bridge_projects_identity_authority_to_session():
    bridge = ConnectionHubRequestAuthBridge(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )

    async def _call_connection_hub(envelope):
        assert envelope.headers["x-telegram-init-data"] == "telegram-proof"
        assert envelope.headers["x-kdcube-auth-connection-id"] == "telegram.support"
        return AuthenticatedRequest(
            ok=True,
            authenticated=True,
            linked=True,
            provider="telegram",
            provider_subject="434804821",
            actor_user_id="telegram_434804821",
            connection_id="telegram.support",
            platform_user_id="platform-user-1",
            principal={"roles": ["kdcube:role:registered"]},
            identity_authority={
                "actor_user_id": "telegram_434804821",
                "platform_user_id": "platform-user-1",
                "platform_roles": ["kdcube:role:super-admin"],
                "platform_permissions": ["demo:*"],
                "economics_user_type": "privileged",
            },
        ).to_dict()

    bridge._call_connection_hub = _call_connection_hub

    async def _session_factory(context, user_type, user_data):
        return UserSession(
            session_id="s1",
            user_type=user_type,
            user_id=user_data["user_id"],
            username=user_data["username"],
            roles=user_data["roles"],
            permissions=user_data["permissions"],
            request_context=context,
            identity_authority=user_data["identity_authority"],
        )

    session = await bridge(
        _request({
            "X-Telegram-Init-Data": "telegram-proof",
            "X-KDCube-Auth-Connection-ID": "telegram.support",
        }),
        RequestContext(client_ip="127.0.0.1", user_agent="test"),
        _session_factory,
    )

    assert session is not None
    assert session.user_id == "telegram_434804821"
    assert session.user_type == UserType.PRIVILEGED
    assert session.roles == ["kdcube:role:super-admin"]
    assert session.permissions == ["demo:*"]
    assert session.identity_authority["platform_user_id"] == "platform-user-1"
    assert session.identity_authority["connection_id"] == "telegram.support"


async def test_connection_hub_bridge_declines_when_hub_does_not_authenticate():
    bridge = ConnectionHubRequestAuthBridge(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )

    async def _call_connection_hub(_envelope):
        return AuthenticatedRequest(
            ok=False,
            authenticated=False,
            error="no_authenticator_accepted",
        ).to_dict()

    bridge._call_connection_hub = _call_connection_hub

    async def _session_factory(_context, _user_type, _user_data):
        raise AssertionError("declined request-auth must not create a session")

    session = await bridge(
        _request({"X-Telegram-Init-Data": "bad-proof"}),
        RequestContext(client_ip="127.0.0.1", user_agent="test"),
        _session_factory,
    )

    assert session is None


async def test_connection_hub_bridge_skips_request_without_external_auth_material():
    bridge = ConnectionHubRequestAuthBridge(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )

    async def _call_connection_hub(_envelope):
        raise AssertionError("bridge must not call Connection Hub without selector hints or provider proof")

    bridge._call_connection_hub = _call_connection_hub

    async def _session_factory(_context, _user_type, _user_data):
        raise AssertionError("declined request-auth must not create a session")

    session = await bridge(
        _request(),
        RequestContext(client_ip="127.0.0.1", user_agent="test"),
        _session_factory,
    )

    assert session is None
