# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kdcube_ai_app.auth.sessions import (
    RequestContext,
    SessionManager,
    UserSession,
    UserType,
)
from kdcube_ai_app.infra.gateway.config import GatewayProfile, RateLimitSettings
from kdcube_ai_app.infra.gateway.gateway import RequestGateway
from kdcube_ai_app.infra.gateway.rate_limiter import RateLimiter


class _Pipeline:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def _record(self, key: str):
        self.keys.append(key)
        return self

    def zremrangebyscore(self, key, *_args):
        return self._record(key)

    def zcard(self, key):
        return self._record(key)

    def zadd(self, key, *_args):
        return self._record(key)

    def expire(self, key, *_args):
        return self._record(key)

    def incr(self, key):
        return self._record(key)

    async def execute(self):
        return [0, 0, 1, True, 1, True]


class _Redis:
    def __init__(self) -> None:
        self.last_pipeline: _Pipeline | None = None

    def pipeline(self) -> _Pipeline:
        self.last_pipeline = _Pipeline()
        return self.last_pipeline


class _SessionRedis:
    def __init__(self) -> None:
        self.session_key = ""

    async def eval(self, _script, _key_count, session_key, payload, *_args):
        self.session_key = session_key
        return [1, payload]


class _CapturingSessionManager:
    def __init__(self) -> None:
        self.user_type: UserType | None = None

    async def get_or_create_session(self, context, user_type, user_data):
        self.user_type = user_type
        return UserSession(
            session_id="card-session",
            user_type=user_type,
            user_id=user_data["user_id"],
            roles=user_data["roles"],
            request_context=context,
            identity_authority=user_data["identity_authority"],
            rate_limit_subject=user_data["rate_limit_subject"],
        )


def _limiter() -> RateLimiter:
    config = SimpleNamespace(
        tenant_id="demo-tenant",
        project_id="demo-project",
        rate_limits=RateLimitSettings(),
        redis=SimpleNamespace(rate_limit_key_ttl=3600),
        profile=GatewayProfile.TESTING,
    )
    limiter = RateLimiter("redis://unused", config, SimpleNamespace())
    limiter.redis = _Redis()
    return limiter


def test_card_principal_owns_rate_limit_bucket_and_uses_role_limits():
    limiter = _limiter()
    access_id = "oauth-7211bfadcbe1e3b8"
    session = UserSession(
        session_id="opaque-session-id",
        user_type=UserType.PAID,
        user_id=f"card:{access_id}",
        rate_limit_subject=f"card:{access_id}",
    )

    asyncio.run(
        limiter.check_and_record(
            session,
            RequestContext(client_ip="127.0.0.1", user_agent="connection-hub-cli"),
            "/api/integrations/bundles/demo/project/records@1-0/public/mcp/records",
        )
    )

    assert limiter.limits[UserType.PAID].requests_per_hour == 2000
    assert limiter.redis.last_pipeline is not None
    assert limiter.redis.last_pipeline.keys
    assert all(f"card:{access_id}" in key for key in limiter.redis.last_pipeline.keys)
    assert all("opaque-session-id" not in key for key in limiter.redis.last_pipeline.keys)


def test_session_manager_persists_card_rate_limit_subject():
    access_id = "oauth-7211bfadcbe1e3b8"
    principal = f"card:{access_id}"
    manager = SessionManager(
        "redis://unused",
        tenant="demo-tenant",
        project="demo-project",
    )
    manager.redis = _SessionRedis()

    session = asyncio.run(
        manager.get_or_create_session(
            RequestContext(client_ip="127.0.0.1", user_agent="connection-hub-cli"),
            UserType.REGISTERED,
            {
                "user_id": principal,
                "username": "integration:worker:platform-user-1",
                "roles": ["kdcube:role:registered"],
                "permissions": [],
                "rate_limit_subject": principal,
            },
        )
    )

    assert manager.redis.session_key.endswith(f":registered:{principal}")
    assert session.user_id == principal
    assert session.rate_limit_subject == principal


def test_gateway_resolves_card_role_from_grantor_economics_identity():
    principal = "card:oauth-7211bfadcbe1e3b8"
    resolved_user_ids: list[str] = []

    async def resolve_role(user_id: str) -> UserType:
        resolved_user_ids.append(user_id)
        return UserType.PAID if user_id == "platform-user-1" else UserType.REGISTERED

    gateway = RequestGateway.__new__(RequestGateway)
    gateway.econ_role_resolver = resolve_role
    gateway.session_manager = _CapturingSessionManager()
    gateway._post_session_create_hooks = []

    session = asyncio.run(
        gateway.get_or_create_session_with_econ_role(
            RequestContext(client_ip="127.0.0.1", user_agent="connection-hub-cli"),
            UserType.PAID,
            {
                "user_id": principal,
                "username": "integration:worker:platform-user-1",
                "roles": ["kdcube:role:paid", "kdcube:role:registered"],
                "permissions": [],
                "identity_authority": {
                    "economics_user_id": "platform-user-1",
                },
                "rate_limit_subject": principal,
            },
        )
    )

    assert resolved_user_ids == ["platform-user-1"]
    assert gateway.session_manager.user_type == UserType.PAID
    assert session.user_type == UserType.PAID
