# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/middleware/economics_role.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from kdcube_ai_app.apps.chat.sdk.infra.control_plane.manager import ControlPlaneManager
from kdcube_ai_app.apps.chat.sdk.infra.economics.plan_resolution import subscription_is_active
from kdcube_ai_app.auth.sessions import UserSession, UserType


class EconomicsRoleResolver:
    """
    Resolve economics-driven role overrides.

    Rule:
    - admin/privileged stays privileged (no override)
    - otherwise:
        - PAID if active subscription OR wallet balance > 0
        - REGISTERED otherwise
    """

    def __init__(self, *, pg_pool, tenant: str, project: str):
        self._tenant = tenant
        self._project = project
        self._cp = ControlPlaneManager(pg_pool=pg_pool, redis=None)

    async def resolve_role_for_user_id(self, user_id: str) -> Optional[UserType]:
        if not user_id:
            return None

        sub = await self._cp.subscription_mgr.get_subscription(
            tenant=self._tenant, project=self._project, user_id=user_id
        )
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        has_active_subscription = subscription_is_active(sub, now)

        wallet_tokens = await self._cp.user_credits_mgr.get_lifetime_balance(
            tenant=self._tenant, project=self._project, user_id=user_id
        )
        has_wallet = bool(wallet_tokens and int(wallet_tokens) > 0)

        if has_active_subscription or has_wallet:
            return UserType.PAID

        return UserType.REGISTERED

    async def resolve_role(self, session: UserSession) -> Optional[UserType]:
        if not session or not session.user_id:
            return None
        return await self.resolve_role_for_user_id(session.user_id)

    async def ensure_baseline_subscription_for_session(self, session: UserSession) -> None:
        if not session or not session.user_id:
            return
        if session.user_type in {UserType.ANONYMOUS, UserType.EXTERNAL}:
            return

        plan_id = "admin" if session.user_type == UserType.PRIVILEGED else "free"
        await self._cp.subscription_mgr.ensure_baseline_subscription(
            tenant=self._tenant,
            project=self._project,
            user_id=session.user_id,
            plan_id=plan_id,
        )
