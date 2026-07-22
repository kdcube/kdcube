# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The agent-capabilities provider contract.

The chat component's Capabilities widget + per-user (per-conversation) selection
are agent-neutral: one inventory schema, one picker, one selection store. Only a
few pieces are framework-specific — the admin-allowed model list, which runtime
channel a chosen model rebases, and optional capabilities like skills/subagents.

An ``AgentCapabilitiesProvider`` is the seam that owns exactly those pieces for a
given agent implementation. ReAct is the first adapter; a generic model-pick
provider (``simple_model_pick``) is the reusable one every non-ReAct port declares
by config. Everything else — storage, MCP/named-service/realm/connected-account
enrichment, deny-list narrowing, the picker UI — stays shared and untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class ModelPick:
    """The model-selection block: an admin-allowed list + the configured default.

    Framework-neutral. Rows use ``{model, provider, label}`` and may carry
    admin-owned serving metadata such as ``num_ctx``. HOW a chosen pick is
    applied at runtime — which role/channel it rebases — is the provider's
    ``apply_selection`` concern. The saved user selection remains only
    ``{provider, model}``.
    """

    supported: List[Dict[str, Any]] = field(default_factory=list)
    default: Optional[str] = None


@dataclass
class InstructionProfiles:
    """Admin-declared instruction-set options the user may pick from, by id.

    Framework-neutral and ID-BASED: the wire carries only
    ``{id, label, description?}`` rows plus the default id. What an id
    RESOLVES to (an instruction body, named blocks, the platform default) is
    the provider/agent's apply-time concern and never crosses this contract —
    any agent kind can declare its own ids.
    """

    options: List[Dict[str, str]] = field(default_factory=list)
    default: Optional[str] = None


@dataclass
class ConversationCaps:
    """Whether this agent can CONSUME the two mid-turn conversation affordances.

    The chat composer offers a running turn a *followup* (an extra message the
    turn folds in at a decision boundary) and a *steer* (cancel + finalize).
    A ReAct turn consumes both; a run-to-completion (ported) agent consumes
    neither — the platform promotes an unconsumed followup to the next turn
    instead. This is a DECLARATION the composer reads to enable/disable those
    affordances per agent; the safe default (run-to-completion) is False/False.
    """

    accepts_followup: bool = False
    accepts_steer: bool = False


@dataclass
class CapabilityBlocks:
    """The adapter-owned half of the ONE agent-capabilities inventory schema.

    All fields optional so a minimal agent declares only a model list and a
    non-ReAct agent returns empty ``skills``/``subagents``. ``to_catalog_fields``
    renders exactly the wire keys the existing catalog response + picker already
    consume, so neither the wire schema nor the picker changes.
    """

    models: Optional[ModelPick] = None
    skills: List[Dict[str, Any]] = field(default_factory=list)
    subagents: Optional[Dict[str, Any]] = None
    conversation: Optional[ConversationCaps] = None
    instructions: Optional[InstructionProfiles] = None

    def to_catalog_fields(self) -> Dict[str, Any]:
        # Key order mirrors the historical catalog literal
        # (skills, then the model pair, then subagents) so a ``catalog.update``
        # over the neutral ``{agent, tools, mcp, named_services}`` base yields a
        # byte-identical field order to the pre-adapter response for the four
        # historical fields.
        # ``default_model`` is the ``{provider, model}`` pair (matching a
        # ``supported`` row), NOT the raw id string. The picker marks the row
        # whose ``{provider, model}`` equals this as the default (and selecting
        # it clears the pick); a bare string leaves ``default_model.model``
        # undefined on the client, so the "default" tag never renders.
        default_model: Optional[Dict[str, str]] = None
        if self.models and self.models.default:
            d = self.models.default
            if isinstance(d, dict):
                # Already a {provider, model} pair (the react path passes the
                # resolved strong-model object) — pass it through unchanged.
                default_model = dict(d)
            else:
                # A bare model-id string (simple_model_pick) — resolve its
                # provider from the supported list so the client gets the pair.
                match = next(
                    (r for r in (self.models.supported or []) if r.get("model") == d),
                    None,
                )
                default_model = {"provider": (match or {}).get("provider", ""), "model": d}
        out: Dict[str, Any] = {
            "skills": list(self.skills),
            "supported_models": (self.models.supported if self.models else []),
            "default_model": default_model,
            "subagents": self.subagents,
        }
        # `conversation` is ADDITIVE and appended last so the four historical
        # fields keep their exact order. Emitted only when a provider declares
        # it — absence on the wire means "unknown", which the composer treats as
        # the backward-compatible "followup + steer enabled" default. A provider
        # that declares it (even all-False) opts into explicit gating.
        if self.conversation is not None:
            out["conversation"] = {
                "accepts_followup": bool(self.conversation.accepts_followup),
                "accepts_steer": bool(self.conversation.accepts_steer),
            }
        # ADDITIVE like `conversation`: emitted only when the provider declares
        # options, so agents without instruction profiles keep the exact
        # historical wire shape and the picker section stays hidden.
        if self.instructions is not None and self.instructions.options:
            out["instruction_profiles"] = {
                "options": [dict(o) for o in self.instructions.options],
                "default": self.instructions.default,
            }
        return out


@runtime_checkable
class AgentCapabilitiesProvider(Protocol):
    """Contract an agent implementation supplies to drive the Capabilities widget
    and apply a saved selection at runtime. Resolved per ``agent_id``."""

    #: stable kind label for telemetry/registry (e.g. "react", "simple_model_pick")
    agent_kind: str

    def capability_blocks(
        self, *, bundle_props: Any, bundle_root: Any, agent_id: str
    ) -> CapabilityBlocks:
        """The adapter-owned inventory blocks for this agent. Merged into the
        neutral ``tools/mcp/named_services`` catalog by the platform shell."""
        ...

    async def apply_selection(
        self,
        *,
        tool_config: Any,
        skill_config: Any,
        runtime_ctx: Any,
        selection: Any = None,
    ) -> Any:
        """Apply a saved selection for the current turn. Owns the model→channel
        rebase and any adapter-specific capability; uses the shared neutral
        narrowing helpers for deny-lists. Returns ``(tool_config, skill_config)``.

        ``selection`` defaults to ``None`` — the provider then LOADS it from the
        selection store keyed by ``runtime_ctx`` identity (the ReAct adapter's
        cold-cache governance is entangled with that load, so loading is the
        provider's job). A caller/test may inject a ``selection`` to bypass the
        store. Must fail open (unchanged configs) on any error."""
        ...
