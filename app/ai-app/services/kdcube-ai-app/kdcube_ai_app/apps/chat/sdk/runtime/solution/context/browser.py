# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/context/browser.py

from __future__ import annotations

import copy, logging
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type

from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import BaseTurnView
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.retrieval import reconcile_citations_for_context
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
import kdcube_ai_app.apps.chat.sdk.runtime.solution.context.presentation as ctx_representation_module
import kdcube_ai_app.apps.chat.sdk.runtime.solution.context.retrieval as ctx_retrieval_module
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext  # used only by make_react_context

PROJECT_LOG_SLOTS = { "project_log" }

logger = logging.getLogger(__name__)
@dataclass
class ContextBundle:
    """A single, reusable container for both React and Codegen."""
    program_history: List[Dict[str, Any]]
    program_history_reconciled: List[Dict[str, Any]]
    canonical_sources: List[Dict[str, Any]]
    last_mat_working_canvas: str
    program_playbook: Dict[str, Any]
    program_playbook_2: Dict[str, Any]


class ContextBrowser:
    """
    Centralized context/history loader + reconciliation.
    Produces a ContextBundle that can be consumed by:
      - ReactSolver (to build a ReactContext)
      - CodegenToolManager (to extract canvas, playbook, etc.)
    """

    def __init__(self, *,
                 tool_manager: Any,
                 turn_view_class: Type[BaseTurnView] = BaseTurnView,
                 logger: Optional[AgentLogger] = None):
        self.tool_manager = tool_manager
        self.log = logger or AgentLogger("context_browser")
        self.turn_view_class = turn_view_class

    async def materialize(
            self,
            *,
            materialize_turn_ids: Optional[List[str]] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            scope: str = "track",
            days: int = 365,
            max_sources: int = 60,
            playbook_turns: int = 5,
    ) -> ContextBundle:
        """
        Fetch history → reconcile citations → compute playbook + last working canvas.
        Returns a ContextBundle that fits both React and Codegen flows.
        """
        materialize_turn_ids = materialize_turn_ids or []

        # --- 1) history
        program_history: List[Dict[str, Any]] = []
        if materialize_turn_ids:
            try:
                program_history = await ctx_retrieval_module.build_program_history_from_turn_ids(
                    self.tool_manager,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    turn_ids=materialize_turn_ids,
                    scope=scope,
                    days=days,
                )
            except Exception as e:
                self.log.log(f"[context_browser] history error: {e}", level="ERROR")

        # --- 2) canvas block (based on original history, not the reconciled copy)
        try:
            last_mat_working_canvas = ctx_representation_module._compose_last_materialized_canvas_block(program_history)
        except Exception:
            last_mat_working_canvas = "(no prior project work)"

        # --- 3) reconcile (updates tokens + web_links_citations)
        program_history_reconciled = copy.deepcopy(program_history)
        canonical_sources: List[Dict[str, Any]] = []
        try:
            rec = reconcile_citations_for_context(
                program_history_reconciled, max_sources=max_sources, rewrite_tokens_in_place=True
            )
            canonical_sources = rec.get("canonical_sources", [])
            self.log.log(f"[context_browser] canonical sources: {len(canonical_sources)}")
        except Exception as e:
            self.log.log(f"[context_browser] reconcile error: {e}", level="ERROR")

        # --- 4) playbook
        try:
            # how we did this for codegen
            program_playbook = ctx_representation_module.build_program_playbook_codegen(
                program_history_reconciled, max_turns=playbook_turns
            )
            # If something is malformed, just skip that record
            # how we started to do this with new approach based on reconstructed turn log of past turn
            program_playbook_2 = ctx_representation_module.build_program_playbook_codegen_new(program_history_reconciled,
                                                                                              max_turns=playbook_turns,
                                                                                              turn_view_class=self.turn_view_class)

            # TODO: how we did it for react

            # how we would do this for answer generator if this would be the same turn where execution happened



        except Exception as e:
            self.log.log(f"[context_browser] playbook error: {e}", level="ERROR")
            program_playbook = {}
            program_playbook_2 = {}

        return ContextBundle(
            program_history=program_history,
            program_history_reconciled=program_history_reconciled,
            canonical_sources=canonical_sources,
            last_mat_working_canvas=last_mat_working_canvas,
            program_playbook=program_playbook,
            program_playbook_2=program_playbook_2,
        )

    def make_react_context(
            self,
            *,
            bundle: ContextBundle,
            scratchpad,
            user_id: Optional[str],
            conversation_id: Optional[str],
            turn_id: Optional[str],
            bundle_id: Optional[str]
    ) -> ReactContext:
        """
        Convert a ContextBundle into a fully prepared ReactContext, including:
          - prior_turns
          - sources_pool seeded with canonical_sources
          - SOURCE_ID_CV initialization
          - turn meta (ids, user text, start ts)
        """
        ctx = ReactContext(history_turns=copy.deepcopy(bundle.program_history_reconciled))

        # Build from reconciled history
        for rec_turn in (bundle.program_history_reconciled or []):
            try:
                execution_id, meta = next(iter(rec_turn.items()))
                deliverables: Dict[str, Dict[str, Any]] = {}
                for d_item in (meta.get("deliverables") or []):
                    slot = d_item.get("slot")
                    if not slot:
                        continue
                    deliverables[slot] = d_item

                turn_sources = ((meta.get("web_links_citations") or {}).get("items") or [])
                t_id = meta.get("turn_id")
                ctx.prior_turns[t_id] = {
                    "turn_id": t_id,
                    "ts": (meta.get("ts") or ""),
                    "user": meta.get("user", {}),
                    "assistant": meta.get("assistant", ""),
                    "project_log": (meta.get("project_log") or {}),
                    "solver_failure": meta.get("solver_failure", ""),
                    "deliverables": deliverables,
                    "sources": turn_sources,
                }
            except Exception as e:
                self.log.log(f"[context_browser] react_context parse error: {e}", level="ERROR")

        # Seed source pool with canonical sources if present
        if bundle.canonical_sources:
            ctx.sources_pool = bundle.canonical_sources
            try:
                ctx.max_sid = max(
                    int(s.get("sid") or 0) for s in bundle.canonical_sources if isinstance(s, dict)
                )
            except Exception:
                ctx.max_sid = 0
        else:
            try:
                ctx.seed_sources_pool_from_prior()
            except Exception as e:
                self.log.log(f"[context_browser] seed pool error: {e}", level="ERROR")

        # Keep SOURCE_ID_CV in sync
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
            val = ctx.max_sid
            try:
                next_sid = int(val) + 1
            except (TypeError, ValueError):
                next_sid = 0
            SOURCE_ID_CV.set({"next": next_sid})
            self.log.log(f"[context_browser] SOURCE_ID_CV initialized to {ctx.max_sid}")
        except Exception as e:
            self.log.log(f"[context_browser] failed to set SOURCE_ID_CV: {e}", level="ERROR")

        # Basic ids + user text
        ctx.turn_id = getattr(scratchpad, "turn_id", None) or turn_id
        ctx.conversation_id = conversation_id
        ctx.user_id = user_id
        ctx.user_text = getattr(scratchpad, "user_text", "")
        ctx.started_at = getattr(scratchpad, "started_at", None)
        ctx.bundle_id = bundle_id

        return ctx

