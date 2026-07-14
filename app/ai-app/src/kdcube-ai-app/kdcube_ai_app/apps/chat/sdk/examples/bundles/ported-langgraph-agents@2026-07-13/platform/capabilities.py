# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── platform/capabilities.py ── the per-turn, per-agent model-pick seam ──
#
# The chat component's Capabilities widget lets a user pick the answer model for a
# conversation. The pickable inventory + the saved selection are platform-owned
# (BaseEntrypoint serves `agent_capabilities` / `agent_selection_update`, backed by
# the UserAgentSelectionStore). This app declares the generic `simple_model_pick`
# provider PER AGENT in config (`surfaces.as_consumer.agents.lg-solution` /
# `.lg-react`), so the widget is active for each agent with zero adapter code.
#
# What stays app-side is the ONE thing that is framework-specific: HOW a saved pick
# is applied at runtime. Neither agent runs the ReAct node, so neither can reuse
# ReAct's `runtime_ctx.agent_role_models` seam. Instead this module resolves the
# pick for the ACTIVE (dispatched) agent, and `entrypoint.py` binds it onto
# `bundle_call_context.role_models` around that agent's graph run — the KDCube model
# router overlays it on that agent's answer role (`lg-solution.answer` /
# `lg-react.answer`), so the chosen model is used for that turn only. Everything
# fails open: any absence or error yields no override, and the router's configured
# default routes the turn.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities import (
    resolve_capability_provider,
)


@dataclass
class _CapabilityTurnCtx:
    """The minimal per-turn identity the capabilities provider reads to LOAD the
    saved pick and REBASE the answer role.

    The generic ``simple_model_pick`` provider inspects exactly these attributes on
    the ``runtime_ctx`` it is given: the pg pool + identity to key the selection
    store, and ``agent_role_models`` which it rebases in place with the validated
    pick. This holder carries only those fields — a value object for one turn, not
    the ReAct workflow runtime context.
    """

    pg_pool: Any = None
    tenant: str = "default"
    project: str = "default"
    user_id: str = ""
    bundle_id: str = ""
    agent_id: str = "lg-solution"
    conversation_id: str = ""
    agent_role_models: Dict[str, Dict[str, str]] = field(default_factory=dict)


async def resolve_turn_role_models(
    entrypoint: Any, state: Dict[str, Any], agent_id: str
) -> Dict[str, Dict[str, str]]:
    """Resolve THIS (user, conversation)'s model pick for the ACTIVE ``agent_id``
    into a ``role_models`` overlay for the turn.

    Returns ``{"<agent>.answer": {"provider", "model"}}`` when the user has a stored
    pick for this conversation under this agent, or ``{}`` when they picked nothing
    (the model router's configured default then routes the turn). The provider is
    resolved from ``surfaces.as_consumer.agents.<agent_id>``, so a pick for
    lg-solution rebases ``lg-solution.answer`` and a pick for lg-react rebases
    ``lg-react.answer`` — the two agents never cross-apply. The identity keys are
    resolved exactly as the ``agent_capabilities`` wire op resolves them, so the
    LOAD key here matches the SAVE key there — and a pick under conversation A never
    leaks to conversation B (the store key includes the conversation id).

    Fails open: any error yields ``{}`` so the turn always runs.
    """
    try:
        provider = resolve_capability_provider(entrypoint.bundle_props, agent_id)
        if provider is None:
            return {}
        # Reuse the base entrypoint's own identity resolution so the store key
        # (tenant/project/user_id/bundle_id) is byte-identical to the wire op's.
        identity = entrypoint._agent_selection_identity()
        conversation_id = str(
            state.get("conversation_id") or state.get("session_id") or ""
        ).strip()
        holder = _CapabilityTurnCtx(
            pg_pool=getattr(entrypoint, "pg_pool", None),
            tenant=str(identity.get("tenant") or "default"),
            project=str(identity.get("project") or "default"),
            user_id=str(identity.get("user_id") or "anonymous"),
            bundle_id=str(identity.get("bundle_id") or ""),
            agent_id=agent_id,
            conversation_id=conversation_id,
            agent_role_models={},
        )
        # selection=None -> the provider loads the saved pick from the store keyed
        # by the holder identity, validates it against the admin-allowed list, and
        # rebases holder.agent_role_models. No tool/skill narrowing applies here
        # (these agents have no pickable tools), so tool_config/skill_config are None.
        await provider.apply_selection(
            tool_config=None,
            skill_config=None,
            runtime_ctx=holder,
        )
        return dict(holder.agent_role_models or {})
    except Exception:
        return {}


async def resolve_turn_disabled_tools(
    entrypoint: Any, state: Dict[str, Any], agent_id: str
) -> Dict[str, Any]:
    """Resolve THIS (user, conversation)'s tool opt-outs for the ACTIVE ``agent_id``.

    Returns the platform deny-map ``{alias: true | [tool_names]}`` the user saved in
    the capabilities widget for this conversation under this agent, or ``{}`` when
    they disabled nothing. The tools themselves are declared as a connection list on
    the agent (the admin ceiling); `platform/tool_pick.py` turns this deny-map plus
    that ceiling into the bound tool set for the turn. Identity keys are resolved
    exactly as the model pick / the wire op resolve them, so the LOAD key here
    matches the SAVE key there and conversation A never leaks into conversation B.

    Fails open: any error yields ``{}`` so every tool the admin allows stays bound.
    """
    try:
        if getattr(entrypoint, "pg_pool", None) is None:
            return {}
        identity = entrypoint._agent_selection_identity()
        store = entrypoint._agent_selection_store(identity)
        conversation_id = str(
            state.get("conversation_id") or state.get("session_id") or ""
        ).strip()
        selection = await store.get_selection(
            user_id=str(identity.get("user_id") or "anonymous"),
            bundle_id=str(identity.get("bundle_id") or ""),
            agent_id=agent_id,
            conversation_id=conversation_id,
            materialize=bool(conversation_id),
        )
        disabled = ((selection or {}).get("disabled") or {}).get("tools") or {}
        return dict(disabled) if isinstance(disabled, dict) else {}
    except Exception:
        return {}
