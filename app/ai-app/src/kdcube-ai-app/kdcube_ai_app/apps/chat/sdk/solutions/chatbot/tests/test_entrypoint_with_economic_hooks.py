from __future__ import annotations

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
