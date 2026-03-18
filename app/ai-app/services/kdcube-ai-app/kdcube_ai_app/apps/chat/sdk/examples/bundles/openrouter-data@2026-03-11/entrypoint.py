# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── entrypoint.py ──
# Bundle entry point for the OpenRouter data-processing bundle.
#
# This bundle demonstrates using OpenRouter as a backend for ad-hoc,
# single-turn data processing tasks: extraction, classification, tagging,
# summarization, schema generation, and similar workflows.
#
# It does NOT use the React agent pattern. OpenRouter lacks context-cache
# management, making it unsuitable for multi-turn copilot workflows.
# Instead, this bundle provides a simple request → OpenRouter completion
# → response pipeline, with full accounting integration.
#
# To create your own data-processing bundle:
#   - Subclass BaseEntrypoint
#   - Decorate with @agentic_workflow(name=..., version=..., priority=...)
#   - Override _build_graph() to define the processing pipeline
#   - Configure the OpenRouter model in the `configuration` property

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint

from .orchestrator.workflow import OpenRouterDataWorkflow
from .event_filter import BundleEventFilter

BUNDLE_ID = "openrouter-data"

# Default model — good balance of quality and cost for data processing.
# Users can override via role_models configuration.
DEFAULT_OR_MODEL = "google/gemini-2.5-flash-preview"


@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=50)
class OpenRouterDataBundle(BaseEntrypoint):
    """
    Single-turn data processing bundle backed by OpenRouter.

    Suitable for: extraction, classification, tagging, summarization,
    schema generation, meta-tagging, and other ad-hoc tasks.
    """

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
        """Build a single-node graph for data processing."""
        g = StateGraph(BundleState)

        async def process(state: BundleState) -> BundleState:
            try:
                orch = OpenRouterDataWorkflow(
                    comm=self.comm,
                    config=self.config,
                    comm_context=self.comm_context,
                )

                res = await orch.process({
                    "request_id": state["request_id"],
                    "tenant": state["tenant"],
                    "project": state["project"],
                    "user": state["user"],
                    "user_type": state.get("user_type") or "anonymous",
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
                await self.report_turn_error(state=state, exc=e, title="Processing Error")

            return state

        g.add_node("process", process)
        g.add_edge(START, "process")
        g.add_edge("process", END)
        return g.compile()

    @property
    def configuration(self) -> Dict[str, Any]:
        """
        Configure the OpenRouter model for data processing.

        The "data-processor" role maps to an OpenRouter model. The provider
        is set to "openrouter" so the ModelRouter can (in future) create
        appropriate clients. For now, this bundle calls OpenRouter directly
        via the openrouter_completion() function.
        """
        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        role_models.update({
            "data-processor": {
                "provider": "openrouter",
                "model": DEFAULT_OR_MODEL,
            },
        })
        config["role_models"] = role_models
        return config

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        """Required by BaseEntrypoint — runs the compiled LangGraph."""
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
