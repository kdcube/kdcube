from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import BaseEntrypointWithEconomics


def test_non_anonymous_plan_lanes_can_use_project_budget_without_plan_name_hardcoding():
    entrypoint = object.__new__(BaseEntrypointWithEconomics)

    assert entrypoint.wallet_users_use_project_budget_first() is True
    assert entrypoint.project_budget_allowed_for_plan(
        user_type="paid",
        plan_id="starter",
        plan_source="role",
        has_wallet=True,
        has_active_subscription=False,
    ) is True
    assert entrypoint.project_budget_allowed_for_plan(
        user_type="known",
        plan_id="team-zero",
        plan_source="role",
        has_wallet=False,
        has_active_subscription=False,
    ) is True
    assert entrypoint.project_budget_allowed_for_plan(
        user_type="anonymous",
        plan_id="anonymous",
        plan_source="role",
        has_wallet=False,
        has_active_subscription=False,
    ) is False


def test_economics_run_authority_projects_actor_to_platform_subject():
    entrypoint = object.__new__(BaseEntrypointWithEconomics)

    projection = entrypoint._project_economics_run_authority(
        {
            "user": "telegram_434804821",
            "user_type": "registered",
            "identity_authority": {
                "actor_user_id": "telegram_434804821",
                "platform_user_id": "02e53484-0081-70ce-11c1-e96706b1a182",
                "roles": ["kdcube:role:super-admin"],
            },
        }
    )

    assert projection.actor_user_id == "telegram_434804821"
    assert projection.economics_user_id == "02e53484-0081-70ce-11c1-e96706b1a182"
    assert projection.budget_bypass is True
    assert projection.user_type == "privileged"


def test_economics_run_authority_does_not_trust_legacy_privileged_user_type():
    entrypoint = object.__new__(BaseEntrypointWithEconomics)

    projection = entrypoint._project_economics_run_authority(
        {
            "user": "telegram_434804821",
            "user_type": "privileged",
        }
    )

    assert projection.actor_user_id == "telegram_434804821"
    assert projection.economics_user_id == "telegram_434804821"
    assert projection.budget_bypass is None
    assert projection.user_type == "registered"


def test_economics_run_authority_reads_cross_runtime_context_authority():
    entrypoint = object.__new__(BaseEntrypointWithEconomics)
    entrypoint.comm_context = SimpleNamespace(
        user=SimpleNamespace(
            identity_authority={
                "actor_user_id": "delegated_client:claude",
                "economics_user_id": "platform-user-1",
                "budget_bypass": False,
                "roles": ["kdcube:role:chat-user"],
            },
            roles=(),
            permissions=(),
            user_type="registered",
        )
    )
    entrypoint._comm = None

    projection = entrypoint._project_economics_run_authority(
        {
            "user": "delegated_client:claude",
            "user_type": "registered",
        }
    )

    assert projection.actor_user_id == "delegated_client:claude"
    assert projection.economics_user_id == "platform-user-1"
    assert projection.roles == ("kdcube:role:chat-user",)
    assert projection.budget_bypass is False
    assert projection.user_type == "registered"


@pytest.mark.asyncio
async def test_economics_pre_run_hook_accepts_legacy_state_only_signature():
    class LegacyHookEntrypoint(BaseEntrypointWithEconomics):
        async def pre_run_hook(self, *, state):
            self.seen_state = state

    entrypoint = object.__new__(LegacyHookEntrypoint)
    state = {"turn_id": "turn_1"}

    await entrypoint._invoke_pre_run_hook(state=state, econ_ctx={"lane": "project"})

    assert entrypoint.seen_state is state


@pytest.mark.asyncio
async def test_economics_pre_run_hook_passes_econ_context_when_supported():
    class ModernHookEntrypoint(BaseEntrypointWithEconomics):
        async def pre_run_hook(self, *, state, econ_ctx):
            self.seen_state = state
            self.seen_econ_ctx = econ_ctx

    entrypoint = object.__new__(ModernHookEntrypoint)
    state = {"turn_id": "turn_1"}
    econ_ctx = {"lane": "project"}

    await entrypoint._invoke_pre_run_hook(state=state, econ_ctx=econ_ctx)

    assert entrypoint.seen_state is state
    assert entrypoint.seen_econ_ctx is econ_ctx
