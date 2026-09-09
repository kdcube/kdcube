# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.request_auth import (
    CONNECTION_HUB_DELEGATED_BEARER_ONLY,
)
from kdcube_ai_app.apps.middleware.gateway import FastAPIGatewayAdapter
from kdcube_ai_app.auth.sessions import UserSession, UserType
from kdcube_ai_app.infra.gateway.rate_limiter import RateLimitError


class _Gateway:
    auth_manager = None

    async def get_or_create_session_with_econ_role(self, _context, user_type, _user_data):
        return UserSession(session_id="anonymous-session", user_type=user_type)

    async def process_request(self, *_args, **_kwargs):
        session = UserSession(
            session_id="card-session",
            user_type=UserType.REGISTERED,
            user_id="card:oauth-7211bfadcbe1e3b8",
        )
        raise RateLimitError("Hourly limit exceeded", retry_after=37, session=session)


class _Policy:
    def resolve(self, _request):
        return SimpleNamespace(
            requirements=[],
            bypass_throttling=False,
            bypass_gate=False,
            bypass_backpressure=False,
        )


def test_mcp_gateway_429_response_includes_retry_after():
    adapter = FastAPIGatewayAdapter(_Gateway(), _Policy())
    app = FastAPI()

    @app.post("/api/integrations/bundles/demo/project/records@1-0/public/mcp/records")
    async def guarded(request: Request):
        await adapter.process_by_policy(
            request,
            header_only_auth=True,
            connection_hub=CONNECTION_HUB_DELEGATED_BEARER_ONLY,
        )
        return {"ok": True}

    response = TestClient(app).post(
        "/api/integrations/bundles/demo/project/records@1-0/public/mcp/records"
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "37"
