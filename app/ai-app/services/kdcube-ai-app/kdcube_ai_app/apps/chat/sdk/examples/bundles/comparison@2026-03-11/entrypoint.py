# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── entrypoint.py ──
# Bundle entry point for the comparison demo.
#
# Registers the bundle "comparison" in the plugin system.
# On load, seeds the knowledge base with initial technologies and comparisons.
# On each turn, injects the KB into comparison tools so the solver
# can research, cache, and export technology comparisons.
#
# Key features:
#   - Knowledge base with technology profiles and daily comparison cache
#   - History tracking for comparison evolution over time
#   - Web search for up-to-date information
#   - Export to JSON for the website frontend widget
#   - Add/compare new technologies via conversation

from __future__ import annotations

import traceback
import pathlib
from typing import Any, Dict

from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint

from .orchestrator.workflow import ComparisonWorkflow
from .event_filter import BundleEventFilter
from .knowledge_base.store import ComparisonKnowledgeBase
from .knowledge_base.seed_data import seed_knowledge_base
from .tools import comparison_tools

BUNDLE_ID = "comparison"


@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class ComparisonBundleWorkflow(BaseEntrypoint):
    """Comparison demo bundle — generates up-to-date KDCube vs alternatives tables."""

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
            event_filter=BundleEventFilter(),
        )
        self._kb: ComparisonKnowledgeBase | None = None
        self.graph = self._build_graph()

    def _get_or_create_kb(self) -> ComparisonKnowledgeBase:
        """Lazy-init the knowledge base under bundle storage."""
        if self._kb is not None:
            return self._kb
        ws_root = self.bundle_storage_root()
        if not ws_root:
            ws_root = pathlib.Path(__file__).resolve().parent / "_local_storage"
        self._kb = ComparisonKnowledgeBase(ws_root)
        seed_knowledge_base(self._kb)
        return self._kb

    def on_bundle_load(self, *, storage_root=None, bundle_spec=None, logger=None, **_):
        """One-time init: seed the knowledge base."""
        if storage_root:
            self._kb = ComparisonKnowledgeBase(pathlib.Path(storage_root))
            seed_knowledge_base(self._kb)

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def orchestrate(state: BundleState) -> BundleState:
            from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
            from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

            conv_idx = ConvIndex(pool=self.pg_pool)
            kb_client = KBClient(pool=self.pg_pool)
            store = ConversationStore(self.settings.STORAGE_PATH)
            conv_ticket_store = ConvTicketStore(pool=self.pg_pool)

            await conv_idx.init()
            await kb_client.init()
            await conv_ticket_store.init()

            ctx_client = ContextRAGClient(
                conv_idx=conv_idx,
                store=store,
                model_service=self.models_service,
            )

            # Inject KB into comparison tools before each turn
            comparison_kb = self._get_or_create_kb()
            comparison_tools.set_kb(comparison_kb)

            orch = ComparisonWorkflow(
                conv_idx=conv_idx,
                kb=kb_client,
                store=store,
                comm=self.comm,
                model_service=self.models_service,
                conv_ticket_store=conv_ticket_store,
                config=self.config,
                comm_context=self.comm_context,
                ctx_client=ctx_client,
            )

            try:
                res = await orch.process({
                    "request_id": state["request_id"],
                    "tenant": state["tenant"],
                    "project": state["project"],
                    "user": state["user"],
                    "user_type": state["user_type"] or "anonymous",
                    "session_id": state["session_id"],
                    "conversation_id": state["conversation_id"],
                    "turn_id": state["turn_id"],
                    "text": state["text"],
                    "attachments": state.get("attachments") or [],
                })
                if not isinstance(res, dict):
                    res = {}
                state["final_answer"] = res.get("answer") or ""
                state["followups"] = res.get("followups") or []
            except Exception as e:
                self.logger.log(traceback.format_exc(), "ERROR")
                state["error_message"] = str(e)
                await self.comm.step(
                    step="turn",
                    status="error",
                    title="Turn Error",
                    data={"error": str(e)},
                    markdown=f"**Error:** {e}",
                )

            return state

        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    @property
    def configuration(self) -> Dict[str, Any]:
        sonnet_45 = "claude-sonnet-4-5-20250929"
        haiku_4 = "claude-haiku-4-5-20251001"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        role_models.update({
            "gate.simple": {"provider": "anthropic", "model": haiku_4},
            "answer.generator.simple": {"provider": "anthropic", "model": sonnet_45},
            "solver.coordinator.v2": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},
        })
        config["role_models"] = role_models
        return config

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
