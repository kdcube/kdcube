# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    authority_has_platform_privilege,
    project_execution_authority,
)


def test_project_execution_authority_charges_projected_platform_user():
    projection = project_execution_authority(
        {
            "actor_user_id": "telegram_434804821",
            "platform_user_id": "02e53484-0081-70ce-11c1-e96706b1a182",
            "platform_roles": ["kdcube:role:super-admin"],
            "platform_permissions": ["memories:read"],
        },
        fallback_user_id="telegram_434804821",
    )

    assert projection.actor_user_id == "telegram_434804821"
    assert projection.economics_user_id == "02e53484-0081-70ce-11c1-e96706b1a182"
    assert projection.roles == ("kdcube:role:super-admin",)
    assert projection.permissions == ("memories:read",)
    assert projection.budget_bypass is True


def test_project_execution_authority_keeps_unlinked_actor_local():
    projection = project_execution_authority(
        {},
        actor_user_id="telegram_434804821",
        fallback_user_id="telegram_434804821",
        fallback_roles=["kdcube:role:chat-user"],
    )

    assert projection.actor_user_id == "telegram_434804821"
    assert projection.economics_user_id == "telegram_434804821"
    assert projection.roles == ("kdcube:role:chat-user",)
    assert projection.budget_bypass is None


def test_authority_has_platform_privilege_uses_central_platform_role_set():
    assert authority_has_platform_privilege(["kdcube:role:admin"])
    assert not authority_has_platform_privilege(["kdcube:role:chat-user"])
