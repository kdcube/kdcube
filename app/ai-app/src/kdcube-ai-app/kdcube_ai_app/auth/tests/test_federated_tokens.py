from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.federated import (
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
async def test_issue_and_verify_federated_data_bus_token(monkeypatch):
    monkeypatch.setenv("KDCUBE_FEDERATED_TOKEN_SECRET", "test-secret")
    redis = FakeRedis()
    session_manager = FakeSessionManager()

    grant = await issue_federated_data_bus_token(
        request=_request(redis, session_manager),
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        provider="telegram",
        provider_subject="42",
        user_id="telegram:42",
        user_type=UserType.PRIVILEGED,
        username="alice",
        roles=["kdcube:role:bundle-admin"],
        allowed_subjects=["task.patch"],
    )

    verified = await verify_federated_data_bus_token(
        token=grant.token,
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        redis=redis,
        session_manager=session_manager,
    )

    assert verified.session.session_id == grant.session.session_id
    assert verified.session.user_type == UserType.PRIVILEGED
    assert verified.claims["provider"] == "telegram"
    assert verified.claims["provider_subject"] == "42"
    assert verified.claims["allowed_transports"] == ["data_bus"]
    assert verified.claims["allowed_subjects"] == ["task.patch"]


@pytest.mark.asyncio
async def test_federated_data_bus_token_is_bundle_scoped(monkeypatch):
    monkeypatch.setenv("KDCUBE_FEDERATED_TOKEN_SECRET", "test-secret")
    redis = FakeRedis()
    session_manager = FakeSessionManager()

    grant = await issue_federated_data_bus_token(
        request=_request(redis, session_manager),
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        provider="telegram",
        provider_subject="42",
        user_id="telegram:42",
    )

    with pytest.raises(FederatedTokenInvalid):
        await verify_federated_data_bus_token(
            token=grant.token,
            tenant="tenant-a",
            project="project-a",
            bundle_id="other@1-0",
            redis=redis,
            session_manager=session_manager,
        )
