# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── entrypoint.py ──
# Bundle entry point. This file registers the bundle in the plugin system
# via the @agentic_workflow decorator and defines how the bundle is launched.
#
# What it does:
#   1. Registers the bundle under the name "with-isoruntime" (@agentic_workflow)
#   2. Builds a LangGraph StateGraph with a single "orchestrate" node
#   3. The "orchestrate" node initializes all dependencies (DB, indexes, RAG)
#      and delegates execution to WithIsoRuntimeWorkflow.process()
#   4. Defines role_models (unused in this bundle — included as an example
#      for bundles that DO call an LLM; this bundle runs pre-built code only)
#   5. Returns suggestions (list of test scenarios) to the UI
#
# NOTE: This bundle does NOT call any LLM. The code executed in the sandbox
# is generated deterministically by exec.py:build_scenario(), not by a model.
# It serves as a test/demo harness for the iso-runtime execution pipeline.
#
# To create your own bundle:
#   - Subclass BaseEntrypoint
#   - Decorate with @agentic_workflow(name=..., version=..., priority=...)
#   - Implement _build_graph() → LangGraph StateGraph
#   - Implement execute_core() to invoke the graph
#   - Optionally override configuration (role_models) and suggestions()

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint

from .workflow import WithIsoRuntimeWorkflow
from .exec import scenarios

# Unique bundle ID — used by the plugin system to discover and load this bundle
BUNDLE_ID = "with-isoruntime"


# @agentic_workflow — registration decorator: on application startup the system
# scans all bundles and auto-loads classes decorated with this.
# priority=100 — selection order when multiple bundles match (higher = preferred)
@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class Entrypoint(BaseEntrypoint):
    """Minimal bundle with context search + react + simple answer."""

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
            delegates the actual work to WithIsoRuntimeWorkflow.
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
                orch = WithIsoRuntimeWorkflow(
                    conv_idx=conv_idx,
                    kb=kb,
                    store=store,
                    comm=self.comm,
                    model_service=self.models_service,
                    conv_ticket_store=conv_ticket_store,
                    config=self.config,
                    comm_context=self.comm_context,
                    ctx_client=ctx_client,
                    bundle_props=self.bundle_props,
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
        role_models maps logical roles (e.g. "iso") to specific LLM providers/models.
        This bundle doesn't actually call any LLM — the "iso" role is defined
        here as an EXAMPLE of how a real bundle would configure its models.
        """
        sonnet_45 = "claude-sonnet-4-5-20250929"
        haiku_4 = "claude-haiku-4-5-20251001"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        for key, value in {
            "iso": {"provider": "anthropic", "model": sonnet_45},
        }.items():
            role_models.setdefault(key, value)
        config["role_models"] = role_models
        return config

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        """Required by BaseEntrypoint — runs the compiled LangGraph."""
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})

    def suggestions(self, **kwargs):
        """Return scenario labels shown as quick-action buttons in the chat UI."""
        return scenarios()
