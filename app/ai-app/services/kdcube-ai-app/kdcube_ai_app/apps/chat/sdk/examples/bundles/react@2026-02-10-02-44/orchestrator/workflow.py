# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
import traceback
# examples/bundles/react@YYYY-MM-DD-hh-mm/orchestrator/workflow.py

from typing import Dict, Any

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import CTurnScratchpad
from kdcube_ai_app.apps.chat.sdk.util import _tstart, _tend
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers

from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, Config
from kdcube_ai_app.infra.accounting import with_accounting
from langgraph.graph import StateGraph, START, END

from ..agents.gate import gate_stream, GateOut as MinimalGateOut
from .. import tools_descriptor, skills_descriptor
from ..resources import get_friendly_error_message


class WithReactWorkflow(BaseWorkflow):
    def __init__(
        self,
        *,
        conv_idx: ConvIndex,
        kb: KBClient,
        store: ConversationStore,
        comm: ChatCommunicator,
        model_service: ModelServiceBase,
        conv_ticket_store: ConvTicketStore,
        config: Config,
        comm_context: ChatTaskPayload,
        ctx_client: Any = None,
    ):
        super().__init__(
            conv_idx=conv_idx,
            kb=kb,
            store=store,
            comm=comm,
            model_service=model_service,
            conv_ticket_store=conv_ticket_store,
            config=config,
            comm_context=comm_context,
            ctx_client=ctx_client,
            message_resources_fn=get_friendly_error_message,
        )
        self.gate_out_class = MinimalGateOut
        self.runtime_ctx.debug_log_announce = True
        self.runtime_ctx.debug_log_sources_pool = True
        self.runtime_ctx.max_iterations = 15
        self.runtime_ctx.debug_timeline = True

    async def construct_turn_and_scratchpad(self, payload: dict) -> CTurnScratchpad:
        scratchpad = await super().construct_turn_and_scratchpad(payload)
        scratchpad.gate_out_class = MinimalGateOut
        return scratchpad

    async def process(self, payload: dict) -> Dict[str, Any]:
        scratchpad = await self.construct_turn_and_scratchpad(payload)
        await self.start_turn(scratchpad)

        try:
            async def _gate_node(state: Dict[str, Any]) -> Dict[str, Any]:
                # --- Gate agent
                t2, ms2 = _tstart()
                async with with_accounting(
                    self.config.ai_bundle_spec.id,
                    agent="gate.simple",
                    metadata={"agent": "gate.simple"},
                ):
                    gate_payload, gate_channels = await gate_stream(
                        self.model_service,
                        is_new_conversation=bool(getattr(scratchpad, "is_new_conversation", False)),
                        on_thinking_delta=self.mk_thinking_streamer("gate"),
                        ctx_browser=self.ctx_browser,
                        render_params={
                            "include_sources": False,
                            "include_announce": False,
                        },
                    )
                try:
                    scratchpad.gate = MinimalGateOut.model_validate(gate_payload)
                except Exception:
                    scratchpad.gate = MinimalGateOut()
                scratchpad.route = (getattr(scratchpad.gate, "route", None) or gate_payload.get("route") or "").strip().lower()
                scratchpad.register_agentic_response("gate", gate_payload)
                scratchpad.register_agentic_response("gate.channels", gate_channels)
                logging_helpers.log_agent_packet(
                    "gate",
                    "gate",
                    {
                        "user_thinking": gate_channels.get("thinking"),
                        "agent_response": gate_payload,
                    },
                )
                logging_helpers.log_stream_channels("gate", "channels", gate_channels)

                # Uses BaseWorkflow to persist + emit conversation title on first turn.
                await self.handle_conversation_title(scratchpad=scratchpad, pre_out=gate_payload)

                timing_gate = _tend(t2, ms2)
                scratchpad.timings.append({"title": "gate", "elapsed_ms": timing_gate["elapsed_ms"]})
                await self._emit({
                    "type": "chat.step",
                    "agent": "gate",
                    "step": "gate",
                    "status": "completed",
                    "title": "Gate",
                    "data": gate_payload,
                    "timing": timing_gate,
                })

                # --- Contribute gate insights into timeline
                # gate_block = build_gate_stage_block(
                #     runtime=self.ctx_browser.runtime_ctx,
                #     gate_out=gate_payload,
                #     clarification_questions=list(getattr(scratchpad, "clarification_questions", []) or []),
                # )
                # self.ctx_browser.contribute(
                #     blocks=[gate_block],
                # )
                scratchpad.clarification_questions = [q.strip() for q in (gate_payload.get("clarification_questions") or []) if q.strip()]
                if scratchpad.clarification_questions:
                    try:
                        self.ctx_browser.contribute_clarification(
                            ticket=scratchpad.open_ticket,
                            clarification_questions=scratchpad.clarification_questions
                        )
                    except Exception:
                        pass
                # persist user message (index)
                await self.persist_user_message(scratchpad)

                state["route"] = scratchpad.route
                state["gate_payload"] = gate_payload
                return state

            async def _react_node(state: Dict[str, Any]) -> Dict[str, Any]:
                react = self.build_react(
                    tools_runtime=getattr(tools_descriptor, "TOOL_RUNTIME", None),
                    mod_tools_spec=tools_descriptor.TOOLS_SPECS,
                    mcp_tools_spec=getattr(tools_descriptor, "MCP_TOOL_SPECS", None) or [],
                    custom_skills_root=skills_descriptor.CUSTOM_SKILLS_ROOT,
                    skills_visibility_agents_config=skills_descriptor.AGENTS_CONFIG or {},
                    scratchpad=scratchpad
                )
                allowed_plugins = [
                    s.get("alias")
                    for s in (tools_descriptor.TOOLS_SPECS or [])
                    if s.get("alias")
                ]
                for s in (getattr(tools_descriptor, "MCP_TOOL_SPECS", None) or []):
                    alias = s.get("alias") or f"mcp_{s.get('server_id')}"
                    if alias:
                        allowed_plugins.append(alias)
                allowed_plugins = list(dict.fromkeys([a for a in allowed_plugins if a]))
                sr = await react.run(
                    allowed_plugins=allowed_plugins,
                )
                try:
                    await react.persist_workspace()
                except Exception as ex:
                    self.logger.log(traceback.format_exc(), level="ERROR")
                await self._emit_turn_work_status(
                    [
                        "closing the loop",
                        "wrapping up",
                        "tying it together",
                    ]
                )
                answer_text = sr.final_answer
                suggested_followups = sr.suggested_followups
                suggested_followups = [s.strip() for s in suggested_followups if isinstance(s, str) and s.strip()]

                if answer_text:
                    scratchpad.answer = answer_text
                    scratchpad.suggested_followups = list(suggested_followups or [])
                    await self.emit_suggested_followups(suggested_followups=suggested_followups)
                    try:
                        await self.finish_turn(scratchpad, ok=True)
                    except Exception:
                        pass
                    state["result"] = {"answer": answer_text, "suggested_followups": suggested_followups}
                    state["short_circuit"] = True
                else:
                    try:
                        ok = getattr(scratchpad, "solver_status", None) != "failed"
                        await self.finish_turn(scratchpad, ok=ok)
                    except Exception:
                        pass
                return state

            graph = StateGraph(dict)
            graph.add_node("gate", _gate_node)
            graph.add_node("solver", _react_node)
            graph.add_edge(START, "gate")
            graph.add_edge("gate", "solver")
            graph.add_edge("solver", END)
            flow = graph.compile()

            final_state = await flow.ainvoke({"scratchpad": scratchpad})
            return final_state.get("result") or {"answer": scratchpad.answer or "", "suggested_followups": scratchpad.suggested_followups or []}

        except Exception as e:
            await self._handle_turn_exception(e, scratchpad)
            raise
