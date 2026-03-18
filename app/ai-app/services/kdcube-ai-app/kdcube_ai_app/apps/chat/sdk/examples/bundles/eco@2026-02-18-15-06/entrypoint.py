# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── entrypoint.py ──
# Bundle entry point. This file registers the bundle in the plugin system
# via the @agentic_workflow decorator and defines how the bundle is launched.
#
# What it does:
#   1. Registers the bundle under the name "eco" (@agentic_workflow)
#   2. Builds a LangGraph StateGraph with a single "orchestrate" node
#   3. The "orchestrate" node initializes all dependencies (DB, indexes, RAG)
#      and delegates execution to WithReactWorkflow.process()
#   4. Defines role_models mapping logical roles to concrete LLM models
#   5. Defines per-user-tier quota policies (anonymous, free, payasyougo, admin)
#
# Key base class: BaseEntrypointWithEconomics
#   Extends BaseEntrypoint with quota enforcement, accounting, and the
#   execute_core() contract. Your bundle overrides:
#     - configuration      → role_models (which LLM for which agent)
#     - app_quota_policies  → per-tier rate/token limits
#     - execute_core()      → the async method called after quota checks pass
#
# To create your own bundle:
#   - Subclass BaseEntrypointWithEconomics (or BaseEntrypoint if no quotas)
#   - Decorate with @agentic_workflow(name=..., version=..., priority=...)
#   - Implement _build_graph() → LangGraph StateGraph
#   - Implement execute_core() to invoke the graph
#   - Override configuration (role_models) and app_quota_policies

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import ProviderBudgetPolicy, QuotaPolicy
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import BaseEntrypointWithEconomics

from .orchestrator.workflow import WithReactWorkflow
from .event_filter import BundleEventFilter

# Unique bundle ID — used by the plugin system to discover and load this bundle
BUNDLE_ID = "eco"


# @agentic_workflow — registration decorator: on application startup the system
# scans all bundles and auto-loads classes decorated with this.
# priority=100 — selection order when multiple bundles match (higher = preferred)
@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class EcoEntrypoint(BaseEntrypointWithEconomics):
    """Eco bundle — gate + ReAct solver with economics (quotas, accounting)."""

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
            # Inject bundle-specific event filter (controls which SSE events reach users)
            event_filter=BundleEventFilter(),
        )
        # Graph is built once at init and reused across invocations
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build a single-node LangGraph that runs the full workflow."""
        g = StateGraph(BundleState)

        async def orchestrate(state: BundleState) -> BundleState:
            """
            The only graph node. Initializes all SDK services lazily
            (imports inside the function to keep startup fast) and then
            delegates the actual work to WithReactWorkflow.
            """
            # -- Lazy imports: these services are only needed at execution time --
            from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
            from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

            # -- Initialize SDK services (DB-backed) --
            conv_idx = ConvIndex(pool=self.pg_pool)       # Conversation vector index
            kb = KBClient(pool=self.pg_pool)              # Knowledge base client
            store = ConversationStore(self.settings.STORAGE_PATH)  # Conversation file store
            conv_ticket_store = ConvTicketStore(pool=self.pg_pool) # Ticket storage

            await conv_idx.init()
            await kb.init()
            await conv_ticket_store.init()

            # RAG client — retrieves relevant context from conversation history
            ctx_client = ContextRAGClient(
                conv_idx=conv_idx,
                store=store,
                model_service=self.models_service,
            )

            try:
                # Create the workflow instance with all dependencies injected
                orch = WithReactWorkflow(
                    conv_idx=conv_idx,
                    kb=kb,
                    store=store,
                    comm=self.comm,
                    model_service=self.models_service,
                    conv_ticket_store=conv_ticket_store,
                    config=self.config,
                    comm_context=self.comm_context,
                    ctx_client=ctx_client,
                )

                # Execute the workflow, passing the full turn state
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
                await self.report_turn_error(state=state, exc=e, title="Turn Error")

            return state

        # Wire the graph: START -> orchestrate -> END (single-node linear graph)
        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    @property
    def configuration(self) -> Dict[str, Any]:
        """
        Override model configuration for this bundle.
        role_models maps logical roles to specific LLM providers/models.
        The gate agent uses a cheap/fast model (Haiku), while the solver
        uses a strong model (Sonnet) for hard steps and a regular model
        (Haiku) for routine steps.
        """
        sonnet_45 = "claude-sonnet-4-5-20250929"
        haiku_4 = "claude-haiku-4-5-20251001"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        for key, value in {
            "gate.simple": {"provider": "anthropic", "model": haiku_4},                      # Gate — fast, lightweight
            "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_45},  # Solver — hard reasoning
            "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},   # Solver — routine steps
        }.items():
            role_models.setdefault(key, value)
        config["role_models"] = role_models
        return config

    @property
    def app_quota_policies(self):
        """
        Per-user-tier quota policies enforced by the economics layer.
        Each key matches state["user_type"]. QuotaPolicy caps concurrent
        requests, daily/monthly request counts, and hourly/daily/monthly tokens.
        """
        from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy

        anonymous_policy = QuotaPolicy(
            max_concurrent=1,
            requests_per_day=2,
            requests_per_month=60,
            total_requests=None,
            tokens_per_hour=150_000,
            tokens_per_day=1_500_000,
            tokens_per_month=20_000_000,
        )
        return {
            "anonymous": anonymous_policy,
            "free": QuotaPolicy(
                max_concurrent=2,
                requests_per_day=100,
                requests_per_month=30000,
                total_requests=None,
                tokens_per_hour=500_000,
                tokens_per_day=2_000_000,
                tokens_per_month=30_000_000,
            ),
            "payasyougo": QuotaPolicy(
                max_concurrent=2,
                requests_per_day=200,
                requests_per_month=6000,
                total_requests=None,
                tokens_per_hour=1_500_000,
                tokens_per_day=4_000_000,
                tokens_per_month=60_000_000,
            ),
            "admin": QuotaPolicy(
                max_concurrent=10,
            )
        }

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        """Required by BaseEntrypointWithEconomics — runs the compiled LangGraph."""
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
