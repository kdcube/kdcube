# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The generic ``simple_model_pick`` capabilities provider.

A model picker parameterized by a role + a model list, declared entirely in
bundle config — no adapter code. Any non-ReAct agent gets the Capabilities model
picker by declaring::

    surfaces:
      as_consumer:
        agents:
          main:
            capability_provider: simple_model_pick
            capabilities:
              models:
                role: lg_solution_port.answer          # the channel the pick rebases
                default: claude-sonnet-4-6
                supported:
                  - { model: claude-sonnet-4-6, provider: anthropic, label: Sonnet 4.6 }
                  - { model: claude-haiku-4-5,  provider: anthropic, label: Haiku 4.5 }

The per-user (per-conversation) pick is applied to
``runtime_ctx.agent_role_models[<role>]``; the model router overlays that onto
the agent's LLM calls, so a user's choice narrows the agent's generation model at
runtime. Deny-lists use the shared neutral narrowing helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities._config import agent_config_block
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities.provider import (
    CapabilityBlocks,
    ConversationCaps,
    ModelPick,
)
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities.registry import (
    register_capability_provider,
)

PROVIDER_KIND = "simple_model_pick"


def _normalize_supported(raw: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for row in raw or []:
        if not isinstance(row, Mapping):
            continue
        model = str(row.get("model") or "").strip()
        if not model:
            continue
        out.append({
            "model": model,
            "provider": str(row.get("provider") or "").strip() or "anthropic",
            "label": str(row.get("label") or "").strip() or model,
        })
    return out


class SimpleModelPickProvider:
    """Generic, config-parameterized capabilities provider (model pick only)."""

    agent_kind = PROVIDER_KIND

    def __init__(
        self,
        *,
        role: str,
        supported: List[Dict[str, str]],
        default: Optional[str] = None,
        bundle_props: Any = None,
        agent_id: str = "",
    ):
        self.role = role
        self.supported = list(supported or [])
        self.default = default
        self._bundle_props = bundle_props
        self._agent_id = agent_id

    # -- inventory -----------------------------------------------------------

    def capability_blocks(
        self, *, bundle_props: Any = None, bundle_root: Any = None, agent_id: str = ""
    ) -> CapabilityBlocks:
        # A minimal agent: only a model list; no skills, no subagents. A generic
        # run-to-completion agent consumes NEITHER mid-turn affordance, so it
        # declares both false — the composer then presents a mid-turn message as
        # queued-for-next-turn and hides the steer control.
        return CapabilityBlocks(
            models=ModelPick(supported=self.supported, default=self.default),
            conversation=ConversationCaps(accepts_followup=False, accepts_steer=False),
        )

    # -- runtime application -------------------------------------------------

    async def _load_selection(self, runtime_ctx: Any) -> Dict[str, Any]:
        """Best-effort load of the saved selection from ``runtime_ctx`` identity.

        Reads the pg pool from ``runtime_ctx.pg_pool`` when the host binds it;
        returns an empty selection on any absence/error (fail open)."""
        try:
            pg_pool = getattr(runtime_ctx, "pg_pool", None)
            user_id = str(getattr(runtime_ctx, "user_id", "") or "").strip()
            bundle_id = str(getattr(runtime_ctx, "bundle_id", "") or "").strip()
            if pg_pool is None or not user_id or not bundle_id:
                return {}
            from kdcube_ai_app.apps.chat.sdk.solutions.user_settings import (
                UserAgentSelectionStore,
            )

            store = UserAgentSelectionStore(
                pg_pool=pg_pool,
                tenant=str(getattr(runtime_ctx, "tenant", "") or "").strip() or "default",
                project=str(getattr(runtime_ctx, "project", "") or "").strip() or "default",
            )
            conversation_id = str(getattr(runtime_ctx, "conversation_id", "") or "").strip()
            return await store.get_selection(
                user_id=user_id,
                bundle_id=bundle_id,
                agent_id=str(getattr(runtime_ctx, "agent_id", "") or "").strip() or self._agent_id,
                conversation_id=conversation_id,
                materialize=bool(conversation_id),
            ) or {}
        except Exception:
            return {}

    async def apply_selection(
        self,
        *,
        tool_config: Any,
        skill_config: Any,
        runtime_ctx: Any,
        selection: Any = None,
    ):
        # Import the shared neutral helpers lazily (agent_inventory is a large
        # module; there is no need to load it at import time).
        from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
            match_supported_model,
            narrow_agent_skill_config,
            narrow_agent_tool_config,
        )

        # When no selection is injected, load it from the store keyed by the
        # runtime identity. Fails open to an empty selection (no pg_pool, no
        # row, store error — anything) so a missing store never silences the
        # agent. A caller/test that injects ``selection=`` bypasses this.
        if selection is None:
            selection = await self._load_selection(runtime_ctx)

        sel = selection if isinstance(selection, Mapping) else {}

        # 1) model pick -> role rebase (validated against the admin-allowed list).
        # When the user has NOT picked, fall back to the admin-configured
        # ``default`` so that default actually ROUTES the turn — not merely
        # pre-selects in the UI. Without this the role stays unmapped and the
        # model router silently uses its own platform default (a different
        # provider/model than the admin declared for this agent).
        try:
            matched = match_supported_model(sel.get("model"), self.supported)
            if not matched and self.default:
                # ``default`` is a bare model-id string; wrap it as a pick mapping
                # so it validates against the supported list the same way.
                matched = match_supported_model({"model": self.default}, self.supported)
            if matched and self.role:
                role_models = dict(getattr(runtime_ctx, "agent_role_models", None) or {})
                role_models[self.role] = matched
                runtime_ctx.agent_role_models = role_models
        except Exception:
            pass  # fail open: keep the configured model

        # 2) deny-lists -> shared neutral narrowing.
        disabled = sel.get("disabled") if isinstance(sel.get("disabled"), Mapping) else {}
        try:
            tool_config = narrow_agent_tool_config(
                tool_config, disabled,
                bundle_props=self._bundle_props, agent_id=self._agent_id,
            )
            skill_config = narrow_agent_skill_config(skill_config, (disabled or {}).get("skills"))
        except Exception:
            pass  # fail open: unchanged configs

        return tool_config, skill_config


def make_simple_model_pick_provider(*, bundle_props: Any, agent_id: str) -> SimpleModelPickProvider:
    """Factory: read ``capabilities.models`` from the agent's consumer block."""
    block = agent_config_block(bundle_props, agent_id)
    models_cfg = block.get("capabilities") or {}
    models_cfg = models_cfg.get("models") if isinstance(models_cfg, Mapping) else {}
    models_cfg = models_cfg if isinstance(models_cfg, Mapping) else {}
    return SimpleModelPickProvider(
        role=str(models_cfg.get("role") or "").strip(),
        supported=_normalize_supported(models_cfg.get("supported")),
        default=(str(models_cfg.get("default")).strip() or None) if models_cfg.get("default") else None,
        bundle_props=bundle_props,
        agent_id=agent_id,
    )


register_capability_provider(PROVIDER_KIND, make_simple_model_pick_provider)
