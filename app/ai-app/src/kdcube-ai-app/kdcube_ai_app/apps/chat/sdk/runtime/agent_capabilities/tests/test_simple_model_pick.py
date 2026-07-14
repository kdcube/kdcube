# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Phase 1 contract tests: registry + generic simple_model_pick provider.

Seams are NOT wired yet (no behavior change to ReAct); these exercise the new
building block in isolation.
"""

import types

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities import (
    AgentCapabilitiesProvider,
    CapabilityBlocks,
    ConversationCaps,
    SIMPLE_MODEL_PICK_KIND,
    capability_provider_kind,
    registered_provider_kinds,
    resolve_capability_provider,
)

_SUPPORTED = [
    {"model": "claude-sonnet-4-6", "provider": "anthropic", "label": "Sonnet 4.6"},
    {"model": "claude-haiku-4-5", "provider": "anthropic", "label": "Haiku 4.5"},
]


def _bundle_props(*, kind="simple_model_pick", role="lg_solution_port.answer", default="claude-sonnet-4-6"):
    return {
        "surfaces": {
            "as_consumer": {
                "default_agent": "main",
                "agents": {
                    "main": {
                        "capability_provider": kind,
                        "capabilities": {"models": {
                            "role": role, "default": default, "supported": _SUPPORTED,
                        }},
                    }
                },
            }
        }
    }


def test_generic_provider_is_registered():
    assert SIMPLE_MODEL_PICK_KIND in registered_provider_kinds()


def test_provider_kind_reads_config_else_defaults():
    assert capability_provider_kind(_bundle_props(), "main") == "simple_model_pick"
    # Unset -> default kind (react), even for an unknown agent id.
    assert capability_provider_kind({}, "main") == "react"


def test_resolve_builds_generic_provider_from_config():
    prov = resolve_capability_provider(_bundle_props(), "main")
    assert isinstance(prov, AgentCapabilitiesProvider)
    assert prov.agent_kind == "simple_model_pick"
    assert prov.role == "lg_solution_port.answer"


def test_capability_blocks_render_wire_fields():
    prov = resolve_capability_provider(_bundle_props(), "main")
    blocks = prov.capability_blocks(bundle_props=_bundle_props(), bundle_root=None, agent_id="main")
    assert isinstance(blocks, CapabilityBlocks)
    fields = blocks.to_catalog_fields()
    # default_model is the {provider, model} pair (matching a supported row),
    # NOT a bare id string — the picker reads default_model.model / .provider to
    # mark the default row, so a string would leave the "default" tag unrendered.
    assert fields["default_model"] == {"provider": "anthropic", "model": "claude-sonnet-4-6"}
    assert [r["model"] for r in fields["supported_models"]] == ["claude-sonnet-4-6", "claude-haiku-4-5"]
    # A minimal agent declares no skills/subagents.
    assert fields["skills"] == [] and fields["subagents"] is None
    # A generic run-to-completion agent CANNOT consume the mid-turn affordances,
    # so it declares both false. The composer reads this to present a mid-turn
    # message as queued-for-next-turn and hide the steer control.
    assert fields["conversation"] == {"accepts_followup": False, "accepts_steer": False}


def test_conversation_block_omitted_when_unset_means_unknown():
    # Backward-compat: a provider that does NOT declare `conversation` omits the
    # key entirely, so absence on the wire = "unknown". The composer treats that
    # as today's behavior (followup + steer enabled). Only an explicit false
    # gates the affordance off.
    assert "conversation" not in CapabilityBlocks().to_catalog_fields()
    assert CapabilityBlocks(
        conversation=ConversationCaps(accepts_followup=True, accepts_steer=False)
    ).to_catalog_fields()["conversation"] == {
        "accepts_followup": True, "accepts_steer": False,
    }


@pytest.mark.asyncio
async def test_apply_selection_rebases_the_declared_role():
    prov = resolve_capability_provider(_bundle_props(), "main")
    ctx = types.SimpleNamespace(agent_role_models={})
    tc, sc = await prov.apply_selection(
        selection={"model": {"provider": "anthropic", "model": "claude-haiku-4-5"}},
        tool_config="TC", skill_config="SC", runtime_ctx=ctx,
    )
    # The pick rebases the declared role only.
    assert ctx.agent_role_models["lg_solution_port.answer"] == {"provider": "anthropic", "model": "claude-haiku-4-5"}
    # No deny-lists -> configs pass through untouched.
    assert (tc, sc) == ("TC", "SC")


@pytest.mark.asyncio
async def test_apply_selection_ignores_a_foreign_pick():
    prov = resolve_capability_provider(_bundle_props(), "main")
    ctx = types.SimpleNamespace(agent_role_models={})
    await prov.apply_selection(
        selection={"model": {"provider": "openai", "model": "gpt-does-not-exist"}},
        tool_config="TC", skill_config="SC", runtime_ctx=ctx,
    )
    # A stale/foreign pick is not applied AS the pick; the agent falls back to
    # the admin-configured default so the turn still routes to a declared model
    # (never the model router's own platform default, a different provider).
    assert ctx.agent_role_models["lg_solution_port.answer"] == {"provider": "anthropic", "model": "claude-sonnet-4-6"}


@pytest.mark.asyncio
async def test_apply_selection_applies_configured_default_when_no_pick():
    # No user pick -> the admin-declared ``default`` ROUTES the turn (not merely
    # a UI pre-selection). Regression guard: without this the role stays unmapped
    # and the model router falls back to its own platform default.
    prov = resolve_capability_provider(_bundle_props(), "main")
    ctx = types.SimpleNamespace(agent_role_models={})
    await prov.apply_selection(
        selection={}, tool_config="TC", skill_config="SC", runtime_ctx=ctx,
    )
    assert ctx.agent_role_models["lg_solution_port.answer"] == {"provider": "anthropic", "model": "claude-sonnet-4-6"}


@pytest.mark.asyncio
async def test_apply_selection_loads_when_no_selection_injected_and_fails_open():
    # selection=None -> the provider attempts a store load from runtime_ctx
    # identity; with no pg_pool bound it fails open to an empty selection. It
    # then applies the admin-configured default (never raises, never silences).
    prov = resolve_capability_provider(_bundle_props(), "main")
    ctx = types.SimpleNamespace(agent_role_models={}, user_id="u1", bundle_id="b@1-0")
    tc, sc = await prov.apply_selection(tool_config="TC", skill_config="SC", runtime_ctx=ctx)
    assert ctx.agent_role_models["lg_solution_port.answer"] == {"provider": "anthropic", "model": "claude-sonnet-4-6"}
    assert (tc, sc) == ("TC", "SC")


@pytest.mark.asyncio
async def test_apply_selection_fails_open_without_role():
    # No role configured -> nothing to rebase, but must not raise.
    props = _bundle_props(role="")
    prov = resolve_capability_provider(props, "main")
    ctx = types.SimpleNamespace(agent_role_models={})
    tc, sc = await prov.apply_selection(
        selection={"model": {"provider": "anthropic", "model": "claude-haiku-4-5"}},
        tool_config="TC", skill_config="SC", runtime_ctx=ctx,
    )
    assert ctx.agent_role_models == {} and (tc, sc) == ("TC", "SC")
