# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The ReAct agent-capabilities provider — the first adapter behind the contract.

Phase 2 extracts ReAct as an ``AgentCapabilitiesProvider`` with ZERO behavior
change. The adapter owns the two seams that used to be inlined:

* ``capability_blocks`` reproduces the four inventory fields the catalog builder
  embedded by hand (``supported_models`` / ``default_model`` / ``skills`` /
  ``subagents``) — the exact same helper calls, so the wire output is
  byte-identical.
* ``apply_selection`` adapts the workflow-coupled application path. ReAct's
  selection application (cold-cache governance, model→role rebase, subagents
  toggle, neutral deny-narrowing) is entangled with the workflow instance (it
  reads the conversation timeline and cache warmth, writes ``_user_subagents_denied``,
  logs through ``self.logger``). That logic stays verbatim on
  ``BaseWorkflow.apply_user_agent_selection``; this adapter binds the workflow it
  serves and delegates to that method, so the observable behavior at the ReAct
  node is identical. The registry default kind is ``"react"``, so every existing
  bundle resolves this provider with no config.
"""

from __future__ import annotations

from typing import Any, Optional

from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities.provider import (
    CapabilityBlocks,
    ConversationCaps,
    InstructionProfiles,
    ModelPick,
)
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities.registry import (
    register_capability_provider,
)

PROVIDER_KIND = "react"


class ReactCapabilitiesProvider:
    """The ReAct adapter: inventory blocks + workflow-delegated application."""

    agent_kind = PROVIDER_KIND

    def __init__(
        self,
        *,
        bundle_props: Any = None,
        agent_id: str = "",
        workflow: Any = None,
    ):
        self._bundle_props = bundle_props
        self._agent_id = agent_id
        self._workflow = workflow

    # -- inventory -----------------------------------------------------------

    def capability_blocks(
        self, *, bundle_props: Any = None, bundle_root: Any = None, agent_id: str = ""
    ) -> CapabilityBlocks:
        """The four ReAct inventory fields, byte-identical to the historical
        inline embedding in ``agent_capabilities_catalog``."""
        # Lazy import: agent_inventory is a large module and the registry is
        # populated at package import time — keep that cheap.
        from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
            SUBAGENTS_CAPABILITY_DESCRIPTION,
            SUBAGENTS_CAPABILITY_LABEL,
            _catalog_skills,
            configured_strong_model,
            react_instruction_profiles,
            react_subagents_config,
            react_supported_models,
            subagents_default_on,
        )
        from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import DEFAULT_AGENT_ID

        # ``default_agent_id`` is not part of the neutral contract; the sole
        # catalog caller uses the ``DEFAULT_AGENT_ID`` default, so binding it
        # here keeps the skills expansion byte-identical.
        skills_out = _catalog_skills(
            bundle_props,
            agent_id,
            bundle_root=bundle_root,
            default_agent_id=DEFAULT_AGENT_ID,
        )

        subagents_enabled, _subagent_defaults = react_subagents_config(bundle_props, agent_id)
        subagents = (
            {
                "available": True,
                "label": SUBAGENTS_CAPABILITY_LABEL,
                "description": SUBAGENTS_CAPABILITY_DESCRIPTION,
                "default_on": subagents_default_on(_subagent_defaults),
            }
            if subagents_enabled
            else None
        )

        profiles = react_instruction_profiles(bundle_props, agent_id)
        return CapabilityBlocks(
            models=ModelPick(
                supported=react_supported_models(bundle_props, agent_id),
                default=configured_strong_model(bundle_props, agent_id),
            ),
            instructions=(
                InstructionProfiles(
                    options=profiles["options"],
                    default=profiles.get("default"),
                )
                if profiles
                else None
            ),
            skills=skills_out,
            subagents=subagents,
            # ReAct consumes both mid-turn affordances: an in-turn followup at a
            # decision boundary and a steer (cancel + finalize). This is a
            # DECLARATION only — the runtime followup/steer handling is untouched
            # (it already consumes them at the ReAct node). It just surfaces
            # ReAct's real behavior to the composer so the affordances stay on.
            conversation=ConversationCaps(accepts_followup=True, accepts_steer=True),
        )

    # -- runtime application -------------------------------------------------

    def bind_workflow(self, workflow: Any) -> "ReactCapabilitiesProvider":
        """Bind the workflow this adapter serves (the ReAct node call site).

        The factory has no workflow to hand; the node that owns the workflow
        binds it right before ``apply_selection``."""
        self._workflow = workflow
        return self

    async def apply_selection(
        self,
        *,
        tool_config: Any,
        skill_config: Any,
        runtime_ctx: Any,
        selection: Any = None,
    ):
        """Apply the saved selection for this turn.

        Delegates to the bound workflow's ``apply_user_agent_selection`` — the
        canonical ReAct implementation (loads the selection from the store,
        runs cold-cache governance, rebases the strong decision role, captures
        the subagents toggle, and narrows via the neutral helpers). ReAct always
        loads internally, so an injected ``selection`` is accepted for
        signature-compatibility but the store load is authoritative (the
        cold-cache path is entangled with the load). Fails open (unchanged
        configs) when no workflow is bound."""
        workflow = self._workflow
        if workflow is None:
            return tool_config, skill_config
        return await workflow.apply_user_agent_selection(tool_config, skill_config)


def make_react_capabilities_provider(
    *, bundle_props: Any, agent_id: str
) -> ReactCapabilitiesProvider:
    """Factory: the workflow is bound later (``bind_workflow``) at the node."""
    return ReactCapabilitiesProvider(bundle_props=bundle_props, agent_id=agent_id)


register_capability_provider(PROVIDER_KIND, make_react_capabilities_provider)
