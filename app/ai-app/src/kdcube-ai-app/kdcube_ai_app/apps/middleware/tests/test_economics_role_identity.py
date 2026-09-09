# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio

from kdcube_ai_app.apps.middleware.economics_role import EconomicsRoleResolver
from kdcube_ai_app.auth.sessions import UserSession, UserType


class _SubscriptionManager:
    def __init__(self) -> None:
        self.user_ids: list[str] = []

    async def ensure_baseline_subscription(self, *, tenant, project, user_id, plan_id):
        assert tenant == "demo-tenant"
        assert project == "demo-project"
        assert plan_id == "free"
        self.user_ids.append(user_id)


class _ControlPlane:
    def __init__(self) -> None:
        self.subscription_mgr = _SubscriptionManager()


def _resolver() -> EconomicsRoleResolver:
    resolver = EconomicsRoleResolver.__new__(EconomicsRoleResolver)
    resolver._tenant = "demo-tenant"
    resolver._project = "demo-project"
    resolver._cp = _ControlPlane()
    return resolver


def _card_session() -> UserSession:
    return UserSession(
        session_id="card-session",
        user_type=UserType.PAID,
        user_id="card:oauth-7211bfadcbe1e3b8",
        identity_authority={"economics_user_id": "platform-user-1"},
    )


def test_role_lookup_uses_linked_economics_user():
    resolver = _resolver()
    resolved_user_ids: list[str] = []

    async def resolve_role(user_id: str) -> UserType:
        resolved_user_ids.append(user_id)
        return UserType.PAID

    resolver.resolve_role_for_user_id = resolve_role

    role = asyncio.run(resolver.resolve_role(_card_session()))

    assert role == UserType.PAID
    assert resolved_user_ids == ["platform-user-1"]


def test_baseline_subscription_uses_linked_economics_user():
    resolver = _resolver()

    asyncio.run(resolver.ensure_baseline_subscription_for_session(_card_session()))

    assert resolver._cp.subscription_mgr.user_ids == ["platform-user-1"]
