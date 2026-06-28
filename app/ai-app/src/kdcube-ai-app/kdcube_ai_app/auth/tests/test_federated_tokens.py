from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.federated import (
    FEDERATED_TOKEN_SECRET_KEY,
    FederatedTokenInvalid,
    issue_federated_data_bus_token,
    verify_federated_data_bus_token,
)
from kdcube_ai_app.auth.sessions import UserSession, UserType


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def setex(self, key, ttl, value):
        del ttl
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)


class FakeSessionManager:
    def __init__(self):
        self.sessions: dict[str, UserSession] = {}
        self.redis = None

    async def init_redis(self):
        return None

    async def get_or_create_session(self, context, user_type, user_data):
        user_id = user_data["user_id"]
        existing = next((s for s in self.sessions.values() if s.user_id == user_id), None)
        if existing is not None:
            existing.request_context = context
            existing.user_type = user_type
            existing.roles = list(user_data.get("roles") or [])
            existing.permissions = list(user_data.get("permissions") or [])
            existing.identity_authority = user_data.get("identity_authority") or {}
            return existing
        session = UserSession(
            session_id=f"session-{user_id}",
            user_type=user_type,
            fingerprint=context.get_fingerprint(),
            user_id=user_id,
            username=user_data.get("username"),
            email=user_data.get("email"),
            roles=list(user_data.get("roles") or []),
            permissions=list(user_data.get("permissions") or []),
            timezone=context.user_timezone or "UTC",
            request_context=context,
            identity_authority=user_data.get("identity_authority") or {},
        )
        self.sessions[session.session_id] = session
        return session

    async def get_session_by_id(self, session_id):
        return self.sessions.get(session_id)


def _request(redis: FakeRedis, session_manager: FakeSessionManager):
    return SimpleNamespace(
        headers={"user-agent": "pytest"},
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                redis_async=redis,
                gateway_adapter=SimpleNamespace(
                    gateway=SimpleNamespace(session_manager=session_manager)
                ),
            )
        ),
    )


@pytest.mark.asyncio
async def test_issue_and_verify_federated_data_bus_token():
    redis = FakeRedis()
    session_manager = FakeSessionManager()

    grant = await issue_federated_data_bus_token(
        request=_request(redis, session_manager),
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        user_id="telegram:42",
        user_type=UserType.PRIVILEGED,
        username="alice",
        roles=["kdcube:role:bundle-admin"],
        identity_authority={
            "actor_user_id": "telegram:42",
            "identity_provider": "telegram",
            "identity_provider_subject": "42",
            "platform_user_id": "platform-user-1",
        },
        secret="test-secret",
    )

    verified = await verify_federated_data_bus_token(
        token=grant.token,
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        redis=redis,
        session_manager=session_manager,
        secret="test-secret",
    )

    assert verified.session.session_id == grant.session.session_id
    assert verified.session.user_type == UserType.PRIVILEGED
    assert verified.claims["allowed_transports"] == ["data_bus"]
    assert verified.claims["credential"]["schema"] == "kdcube.credential.v1"
    assert verified.claims["credential"]["credential_kind"] == "derived_session"
    assert verified.claims["credential"]["issuer_authority_id"] == "kdcube.ingress_session"
    assert verified.claims["credential"]["issuer_authenticator_id"] == "kdcube.signed_active_record"
    assert verified.claims["credential"]["audience"] == "kdcube:data_bus"
    assert verified.claims["credential"]["verified_authority"]["platform_user_id"] == "platform-user-1"
    assert set(verified.claims) == {
        "schema",
        "jti",
        "sub",
        "tenant",
        "project",
        "bundle_id",
        "session_id",
        "allowed_transports",
        "credential",
        "iat",
        "exp",
    }
    assert verified.session.identity_authority["platform_user_id"] == "platform-user-1"


@pytest.mark.asyncio
async def test_federated_data_bus_token_is_bundle_scoped():
    redis = FakeRedis()
    session_manager = FakeSessionManager()

    grant = await issue_federated_data_bus_token(
        request=_request(redis, session_manager),
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        user_id="telegram:42",
        secret="test-secret",
    )

    with pytest.raises(FederatedTokenInvalid):
        await verify_federated_data_bus_token(
            token=grant.token,
            tenant="tenant-a",
            project="project-a",
            bundle_id="other@1-0",
            redis=redis,
            session_manager=session_manager,
            secret="test-secret",
        )


@pytest.mark.asyncio
async def test_federated_data_bus_token_does_not_fallback_to_service_tokens(monkeypatch):
    monkeypatch.setenv("SECRETS_TOKEN", "service-local-read-token")
    monkeypatch.setenv("SECRETS_ADMIN_TOKEN", "service-local-admin-token")

    redis = FakeRedis()
    session_manager = FakeSessionManager()

    with pytest.raises(FederatedTokenInvalid, match=FEDERATED_TOKEN_SECRET_KEY):
        await issue_federated_data_bus_token(
            request=_request(redis, session_manager),
            tenant="tenant-a",
            project="project-a",
            bundle_id="task-tracker@1-0",
            user_id="telegram:42",
        )
