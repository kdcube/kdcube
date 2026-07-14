# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Phase 2: the ReAct adapter behind the capabilities contract.

The ReAct provider is the registry default. Its ``capability_blocks`` must
reproduce the four inventory fields byte-identically; its ``apply_selection``
delegates to the bound workflow's ``apply_user_agent_selection`` (the canonical,
workflow-coupled implementation) and fails open when no workflow is bound.
"""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities import (
    AgentCapabilitiesProvider,
    ReactCapabilitiesProvider,
    REACT_PROVIDER_KIND,
    registered_provider_kinds,
    resolve_capability_provider,
)
from kdcube_ai_app.apps.chat.sdk.runtime import agent_inventory as ai

_MODELS = [
    {"model": "claude-sonnet-4-6", "provider": "anthropic", "label": "Sonnet 4.6"},
    {"model": "claude-haiku-4-5", "provider": "anthropic", "label": "Haiku 4.5"},
]


def _props(*, subagents=None):
    react_block = {
        "supported_models": [dict(r) for r in _MODELS],
        "role_models": {
            ai.USER_MODEL_TARGET_ROLE: {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        },
    }
    if subagents is not None:
        react_block["subagents"] = subagents
    return {
        "surfaces": {"as_consumer": {"default_agent": "main", "agents": {"main": {}}}},
        "react": {"default_agent": react_block},
    }


def test_react_is_the_registry_default():
    assert REACT_PROVIDER_KIND == "react"
    assert "react" in registered_provider_kinds()
    # No config declared -> the default kind (react) resolves.
    prov = resolve_capability_provider({}, "main")
    assert isinstance(prov, ReactCapabilitiesProvider)
    assert isinstance(prov, AgentCapabilitiesProvider)
    assert prov.agent_kind == "react"


@pytest.mark.parametrize("subagents", [None, {"allowed": True, "default_on": False}])
def test_capability_blocks_match_the_inline_embedding(subagents):
    props = _props(subagents=subagents)
    prov = resolve_capability_provider(props, "main")
    fields = prov.capability_blocks(bundle_props=props, bundle_root=None, agent_id="main").to_catalog_fields()

    # The same helper calls the historical inline path used.
    assert fields["supported_models"] == ai.react_supported_models(props, "main")
    assert fields["default_model"] == ai.configured_strong_model(props, "main")
    sub_enabled, sub_def = ai.react_subagents_config(props, "main")
    expected_sub = (
        {
            "available": True,
            "label": ai.SUBAGENTS_CAPABILITY_LABEL,
            "description": ai.SUBAGENTS_CAPABILITY_DESCRIPTION,
            "default_on": ai.subagents_default_on(sub_def),
        }
        if sub_enabled
        else None
    )
    assert fields["subagents"] == expected_sub
    # ReAct declares it consumes both mid-turn affordances (a DECLARATION only —
    # the runtime followup/steer handling at the ReAct node is unchanged).
    assert fields["conversation"] == {"accepts_followup": True, "accepts_steer": True}
    # The four historical fields keep their exact order (skills -> models ->
    # subagents); the additive `conversation` key is appended last, so the byte
    # order of the pre-existing fields is preserved.
    assert list(fields.keys()) == [
        "skills", "supported_models", "default_model", "subagents", "conversation",
    ]


@pytest.mark.asyncio
async def test_apply_selection_delegates_to_the_bound_workflow():
    seen = {}

    class _WF:
        async def apply_user_agent_selection(self, tool_config, skill_config):
            seen["args"] = (tool_config, skill_config)
            return "NARROWED_TC", "NARROWED_SC"

    prov = ReactCapabilitiesProvider(bundle_props={}, agent_id="main")
    prov.bind_workflow(_WF())
    tc, sc = await prov.apply_selection(tool_config="TC", skill_config="SC", runtime_ctx=object())
    assert (tc, sc) == ("NARROWED_TC", "NARROWED_SC")
    assert seen["args"] == ("TC", "SC")


@pytest.mark.asyncio
async def test_apply_selection_fails_open_without_a_bound_workflow():
    prov = ReactCapabilitiesProvider(bundle_props={}, agent_id="main")
    tc, sc = await prov.apply_selection(tool_config="TC", skill_config="SC", runtime_ctx=object())
    assert (tc, sc) == ("TC", "SC")
