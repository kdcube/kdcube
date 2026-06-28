# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.solutions.connections.request_auth import RequestAuthResolver
from kdcube_ai_app.auth.AuthManager import User
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType


class _AuthManager:
    def __init__(self):
        self.called = False

    async def authenticate_with_both(self, access_token, id_token):
        self.called = True
        assert access_token == "token-1"
        assert id_token == "id-1"
        return User(
            username="platform-user",
            email="u@example.com",
            roles=["kdcube:role:super-admin"],
            permissions=["demo:*"],
        )


async def test_request_auth_resolver_standard_auth_creates_session():
    created = {}

    async def _factory(context, user_type, user_data):
        created["user_type"] = user_type
        created["user_data"] = user_data
        return UserSession(
            session_id="s1",
            user_type=user_type,
            user_id=user_data["user_id"],
            username=user_data["username"],
            roles=user_data["roles"],
            permissions=user_data["permissions"],
            request_context=context,
        )

    auth = _AuthManager()
    resolver = RequestAuthResolver(auth_manager=auth, session_factory=_factory)
    context = RequestContext(
        client_ip="127.0.0.1",
        user_agent="test",
        authorization_header="Bearer token-1",
        id_token="id-1",
    )

    session = await resolver.resolve_session(SimpleNamespace(), context)

    assert auth.called is True
    assert session.user_id == "platform-user"
    assert session.user_type == UserType.PRIVILEGED
    assert created["user_data"]["roles"] == ["kdcube:role:super-admin"]


async def test_request_auth_resolver_connection_hub_surface_wins():
    async def _factory(context, user_type, user_data):
        raise AssertionError("standard auth factory should not be called")

    async def _connection_hub_surface(_request, context, _session_factory):
        return UserSession(
            session_id="s-channel",
            user_type=UserType.PRIVILEGED,
            user_id="telegram_42",
            roles=["kdcube:role:admin"],
            request_context=context,
            identity_authority={"actor_user_id": "telegram_42"},
        )

    auth = _AuthManager()
    resolver = RequestAuthResolver(auth_manager=auth, session_factory=_factory)
    resolver.install_connection_hub_surface(_connection_hub_surface)
    context = RequestContext(client_ip="127.0.0.1", user_agent="test")

    session = await resolver.resolve_session(SimpleNamespace(), context)

    assert auth.called is False
    assert session.user_id == "telegram_42"
    assert session.identity_authority == {"actor_user_id": "telegram_42"}


async def test_request_auth_resolver_valid_platform_auth_wins_before_connection_hub():
    created = {}

    async def _factory(context, user_type, user_data):
        created["user_type"] = user_type
        created["user_data"] = user_data
        return UserSession(
            session_id="s-standard",
            user_type=user_type,
            user_id=user_data["user_id"],
            username=user_data["username"],
            roles=user_data["roles"],
            permissions=user_data["permissions"],
            request_context=context,
        )

    async def _connection_hub_surface(_request, _context, _session_factory):
        raise AssertionError("valid platform auth is role-providing and must win first")

    auth = _AuthManager()
    resolver = RequestAuthResolver(auth_manager=auth, session_factory=_factory)
    resolver.install_connection_hub_surface(_connection_hub_surface)
    context = RequestContext(
        client_ip="127.0.0.1",
        user_agent="test",
        authorization_header="Bearer token-1",
        id_token="id-1",
    )

    session = await resolver.resolve_session(SimpleNamespace(), context)

    assert auth.called is True
    assert session.user_id == "platform-user"
    assert session.user_type == UserType.PRIVILEGED
    assert created["user_type"] == UserType.PRIVILEGED


async def test_request_auth_resolver_can_disable_connection_hub_surface():
    created = {}

    async def _factory(context, user_type, user_data):
        created["user_type"] = user_type
        created["user_data"] = user_data
        return UserSession(
            session_id="s-standard",
            user_type=user_type,
            user_id=user_data["user_id"],
            username=user_data["username"],
            roles=user_data["roles"],
            permissions=user_data["permissions"],
            request_context=context,
        )

    async def _connection_hub_surface(_request, _context, _session_factory):
        raise AssertionError("header-only auth must not invoke Connection Hub surface")

    auth = _AuthManager()
    resolver = RequestAuthResolver(auth_manager=auth, session_factory=_factory)
    resolver.install_connection_hub_surface(_connection_hub_surface)
    context = RequestContext(
        client_ip="127.0.0.1",
        user_agent="test",
        authorization_header="Bearer token-1",
        id_token="id-1",
    )

    session = await resolver.resolve_session(SimpleNamespace(), context, allow_connection_hub=False)

    assert auth.called is True
    assert session.user_id == "platform-user"
    assert session.user_type == UserType.PRIVILEGED
    assert created["user_type"] == UserType.PRIVILEGED
