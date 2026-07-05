# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from starlette.requests import Request

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators.models import AuthenticatedRequest
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authentication_surface import (
    ConnectionHubAuthenticationSurface,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth import (
    surface_guard,
)
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType


PLATFORM_RESOURCE_PATTERN = "https://testserver*/api/platform/admin/redeploy"


class _GrantStore:
    def __init__(self, record=None):
        self.record = record

    async def get_access_grant_record(self, access_token: str):
        return self.record


class _AppState:
    def __init__(self, grant_record=None):
        self.oauth_grant_store = _GrantStore(grant_record)


class _App:
    def __init__(self, grant_record=None):
        self.state = _AppState(grant_record)


def _authority(
    *,
    scopes=None,
    resource=PLATFORM_RESOURCE_PATTERN,
    grantor_subject="platform-user-1",
    subject="integration:automation:platform-user-1",
):
    return {
        "schema": "kdcube.credential.v1",
        "credential_kind": "delegated_client_access",
        "issuer_authority_id": "delegated_client",
        "issuer_authenticator_id": "delegated_client.bearer",
        "subject": subject,
        "audience": "kdcube:delegated_client",
        "attrs": {
            "scopes": list(scopes or ["devops:deploy"]),
            "resource_grants": {
                resource: list(scopes or ["devops:deploy"]),
            },
            "grantor_subject": grantor_subject,
            "identity_scope": "grantor",
        },
    }


def _request(
    headers: dict[str, str] | None = None,
    *,
    path: str = "/api/integrations/bundles/demo/project/user-memories/operations/memories_widget_data",
    app=None,
) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "server": ("testserver", 80),
        "scheme": "https",
        "app": app or _App(),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


async def test_connection_hub_surface_projects_identity_authority_to_session():
    surface = ConnectionHubAuthenticationSurface(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )

    async def _call_connection_hub(envelope):
        assert envelope.headers["x-telegram-init-data"] == "telegram-proof"
        assert envelope.headers["x-kdcube-auth-authority-id"] == "telegram.support"
        assert envelope.headers["x-kdcube-auth-authenticator-id"] == "telegram.support"
        return AuthenticatedRequest(
            ok=True,
            authenticated=True,
            linked=True,
            provider="telegram",
            provider_subject="100200300",
            actor_user_id="telegram_100200300",
            connection_id="telegram.support",
            platform_user_id="platform-user-1",
            principal={"roles": ["kdcube:role:registered"]},
            identity_authority={
                "actor_user_id": "telegram_100200300",
                "platform_user_id": "platform-user-1",
                "platform_roles": ["kdcube:role:super-admin"],
                "platform_permissions": ["demo:*"],
                "economics_budget_bypass": True,
            },
        ).to_dict()

    surface._call_connection_hub = _call_connection_hub

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

    session = await surface(
        _request({
            "X-Telegram-Init-Data": "telegram-proof",
            "X-KDCube-Auth-Authority-ID": "telegram.support",
            "X-KDCube-Auth-Authenticator-ID": "telegram.support",
        }),
        RequestContext(client_ip="127.0.0.1", user_agent="test"),
        _session_factory,
    )

    assert session is not None
    assert session.user_id == "telegram_100200300"
    assert session.user_type == UserType.PRIVILEGED
    assert session.roles == ["kdcube:role:super-admin"]
    assert session.permissions == ["demo:*"]
    assert session.identity_authority["platform_user_id"] == "platform-user-1"
    assert session.identity_authority["connection_id"] == "telegram.support"


async def test_connection_hub_surface_accepts_delegated_bearer_for_platform_resource(monkeypatch):
    async def fake_authenticate(token: str):
        if token != "automation-token":
            return None
        return {
            "sub": "integration:automation:platform-user-1",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["devops:deploy"],
        }

    monkeypatch.setattr(
        surface_guard,
        "_authenticate_delegated_client_access_token",
        fake_authenticate,
    )

    surface = ConnectionHubAuthenticationSurface(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )
    surface._delegated_oauth_raw_config = lambda _request: {
        "enabled": True,
        "resources": [
            {
                "resource": PLATFORM_RESOURCE_PATTERN,
                "operations": {
                    "platform_admin_redeploy": {
                        "grants": ["devops:deploy"],
                    },
                },
            },
        ],
    }

    grant_record = {
        "operations": ["platform_admin_redeploy"],
        "credential": _authority(),
        "grantor_authority": {
            "grantor_roles": ["kdcube:role:super-admin"],
            "grantor_permissions": ["devops:deploy"],
        },
    }

    async def _session_factory(context, user_type, user_data):
        return UserSession(
            session_id="s-delegated",
            user_type=user_type,
            user_id=user_data["user_id"],
            username=user_data["username"],
            roles=user_data["roles"],
            permissions=user_data["permissions"],
            request_context=context,
            identity_authority=user_data["identity_authority"],
        )

    session = await surface(
        _request(
            {"Authorization": "Bearer automation-token"},
            path="/api/platform/admin/redeploy",
            app=_App(grant_record),
        ),
        RequestContext(
            client_ip="127.0.0.1",
            user_agent="test",
            authorization_header="Bearer automation-token",
        ),
        _session_factory,
    )

    assert session is not None
    assert session.user_id == "platform-user-1"
    assert session.user_type == UserType.PRIVILEGED
    assert session.roles == ["kdcube:role:super-admin"]
    assert session.permissions == ["devops:deploy"]
    assert session.identity_authority["delegate_identity"] == "integration:automation:platform-user-1"
    assert session.identity_authority["resource_grants"] == {PLATFORM_RESOURCE_PATTERN: ["devops:deploy"]}


async def test_connection_hub_surface_accepts_admin_delegated_bearer_for_all_resources(monkeypatch):
    async def fake_authenticate(token: str):
        if token != "automation-token":
            return None
        return {
            "sub": "integration:automation:platform-admin-1",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["kdcube:role:super-admin"],
        }

    monkeypatch.setattr(
        surface_guard,
        "_authenticate_delegated_client_access_token",
        fake_authenticate,
    )

    surface = ConnectionHubAuthenticationSurface(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )
    surface._delegated_oauth_raw_config = lambda _request: {
        "enabled": True,
        "resources": [
            {
                "resource": "*",
                "label": "All platform and application APIs",
                "admin_only": True,
                "grants": ["kdcube:role:super-admin"],
            },
        ],
    }

    grant_record = {
        "tools": [],
        "credential": _authority(
            scopes=["kdcube:role:super-admin"],
            resource="*",
            grantor_subject="platform-admin-1",
            subject="integration:automation:platform-admin-1",
        ),
        "grantor_authority": {
            "grantor_roles": ["kdcube:role:super-admin"],
        },
    }

    async def _session_factory(context, user_type, user_data):
        return UserSession(
            session_id="s-admin-delegated",
            user_type=user_type,
            user_id=user_data["user_id"],
            username=user_data["username"],
            roles=user_data["roles"],
            permissions=user_data["permissions"],
            request_context=context,
            identity_authority=user_data["identity_authority"],
        )

    session = await surface(
        _request(
            {"Authorization": "Bearer automation-token"},
            path="/api/integrations/bundles/demo/project/news@2026-05-20-12-05/operations/kdcube_news_admin_upload_issue",
            app=_App(grant_record),
        ),
        RequestContext(
            client_ip="127.0.0.1",
            user_agent="test",
            authorization_header="Bearer automation-token",
        ),
        _session_factory,
    )

    assert session is not None
    assert session.user_id == "platform-admin-1"
    assert session.user_type == UserType.PRIVILEGED
    assert session.roles == ["kdcube:role:super-admin"]
    assert session.permissions == ["kdcube:role:super-admin"]
    assert session.identity_authority["delegate_identity"] == "integration:automation:platform-admin-1"
    assert session.identity_authority["resource_grants"] == {"*": ["kdcube:role:super-admin"]}


async def test_connection_hub_surface_rejects_all_resources_delegated_bearer_without_admin(monkeypatch):
    async def fake_authenticate(token: str):
        if token != "automation-token":
            return None
        return {
            "sub": "integration:automation:platform-user-1",
            "roles": ["kdcube:role:delegated-client"],
            "permissions": ["kdcube:role:super-admin"],
        }

    monkeypatch.setattr(
        surface_guard,
        "_authenticate_delegated_client_access_token",
        fake_authenticate,
    )

    surface = ConnectionHubAuthenticationSurface(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )
    surface._delegated_oauth_raw_config = lambda _request: {
        "enabled": True,
        "resources": [
            {
                "resource": "*",
                "label": "All platform and application APIs",
                "admin_only": True,
                "grants": ["kdcube:role:super-admin"],
            },
        ],
    }

    grant_record = {
        "tools": [],
        "credential": _authority(
            scopes=["kdcube:role:super-admin"],
            resource="*",
            grantor_subject="platform-user-1",
            subject="integration:automation:platform-user-1",
        ),
        "grantor_authority": {
            "grantor_roles": ["kdcube:role:registered"],
        },
    }

    async def _session_factory(_context, _user_type, _user_data):
        raise AssertionError("non-admin all-resource token must not create a session")

    session = await surface(
        _request(
            {"Authorization": "Bearer automation-token"},
            path="/api/platform/admin/redeploy",
            app=_App(grant_record),
        ),
        RequestContext(
            client_ip="127.0.0.1",
            user_agent="test",
            authorization_header="Bearer automation-token",
        ),
        _session_factory,
    )

    assert session is None


async def test_connection_hub_surface_ignores_delegated_bearer_for_unconfigured_resource(monkeypatch):
    async def fake_authenticate(_token: str):
        raise AssertionError("unconfigured resources must not try delegated bearer auth")

    monkeypatch.setattr(
        surface_guard,
        "_authenticate_delegated_client_access_token",
        fake_authenticate,
    )

    surface = ConnectionHubAuthenticationSurface(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )
    surface._delegated_oauth_raw_config = lambda _request: {
        "enabled": True,
        "resources": [
            {
                "resource": PLATFORM_RESOURCE_PATTERN,
                "operations": {
                    "platform_admin_redeploy": {
                        "grants": ["devops:deploy"],
                    },
                },
            },
        ],
    }

    async def _session_factory(_context, _user_type, _user_data):
        raise AssertionError("unmatched delegated bearer must not create a session")

    session = await surface(
        _request(
            {"Authorization": "Bearer automation-token"},
            path="/api/platform/other",
            app=_App(),
        ),
        RequestContext(
            client_ip="127.0.0.1",
            user_agent="test",
            authorization_header="Bearer automation-token",
        ),
        _session_factory,
    )

    assert session is None


async def test_connection_hub_surface_declines_when_hub_does_not_authenticate():
    surface = ConnectionHubAuthenticationSurface(
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

    surface._call_connection_hub = _call_connection_hub

    async def _session_factory(_context, _user_type, _user_data):
        raise AssertionError("declined request-auth must not create a session")

    session = await surface(
        _request({"X-Telegram-Init-Data": "bad-proof"}),
        RequestContext(client_ip="127.0.0.1", user_agent="test"),
        _session_factory,
    )

    assert session is None


async def test_connection_hub_surface_marks_verified_unlinked_actor_external():
    surface = ConnectionHubAuthenticationSurface(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )

    async def _call_connection_hub(_envelope):
        return AuthenticatedRequest(
            ok=True,
            authenticated=True,
            linked=False,
            provider="telegram",
            provider_subject="100200300",
            actor_user_id="telegram_100200300",
            connection_id="telegram.support",
            principal={"roles": []},
            identity_authority={
                "actor_user_id": "telegram_100200300",
                "storage_user_id": "telegram_100200300",
                "economics_user_id": "telegram_100200300",
                "identity_provider": "telegram",
                "identity_provider_subject": "100200300",
                "platform_authority_resolved": False,
                "platform_authority_error": "platform_user_not_linked",
            },
        ).to_dict()

    surface._call_connection_hub = _call_connection_hub

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

    session = await surface(
        _request({"X-Telegram-Init-Data": "telegram-proof"}),
        RequestContext(client_ip="127.0.0.1", user_agent="test"),
        _session_factory,
    )

    assert session is not None
    assert session.user_type == UserType.EXTERNAL
    assert session.user_id == "telegram_100200300"
    assert session.roles == []
    assert session.identity_authority["platform_authority_resolved"] is False


async def test_connection_hub_surface_skips_hub_without_selector_hints_or_provider_proof():
    surface = ConnectionHubAuthenticationSurface(
        redis=None,
        pg_pool=None,
        tenant="demo-tenant",
        project="demo-project",
    )
    called = False

    async def _call_connection_hub(_envelope):
        nonlocal called
        called = True
        return AuthenticatedRequest(
            ok=False,
            authenticated=False,
            error="no_authenticator_accepted",
        ).to_dict()

    surface._call_connection_hub = _call_connection_hub

    async def _session_factory(_context, _user_type, _user_data):
        raise AssertionError("declined request-auth must not create a session")

    session = await surface(
        _request(),
        RequestContext(client_ip="127.0.0.1", user_agent="test"),
        _session_factory,
    )

    assert session is None
    assert called is False
