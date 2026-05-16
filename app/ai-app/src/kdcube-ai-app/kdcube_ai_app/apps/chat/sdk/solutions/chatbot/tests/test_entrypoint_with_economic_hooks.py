from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import BaseEntrypointWithEconomics


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
