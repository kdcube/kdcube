# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import traceback
from typing import Any, Dict

from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint

from .orchestrator.workflow import WithReactWorkflow
from .event_filter import BundleEventFilter

BUNDLE_ID = "react"


@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class ReactWorkflow(BaseEntrypoint):
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
            event_filter=BundleEventFilter(),
        )
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def orchestrate(state: BundleState) -> BundleState:
            from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
            from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

            conv_idx = ConvIndex(pool=self.pg_pool)
            kb = KBClient(pool=self.pg_pool)
            store = ConversationStore(self.settings.STORAGE_PATH)
            conv_ticket_store = ConvTicketStore(pool=self.pg_pool)

            await conv_idx.init()
            await kb.init()
            await conv_ticket_store.init()

            ctx_client = ContextRAGClient(
                conv_idx=conv_idx,
                store=store,
                model_service=self.models_service,
            )

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
