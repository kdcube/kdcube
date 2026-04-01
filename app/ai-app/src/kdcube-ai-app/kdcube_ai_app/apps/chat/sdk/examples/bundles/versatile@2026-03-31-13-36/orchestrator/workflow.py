import traceback
from typing import Any, Dict

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import CTurnScratchpad
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.util import _tend, _tstart
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import Config, ModelServiceBase
from langgraph.graph import END, START, StateGraph

from .. import skills_descriptor, tools_descriptor
from ..agents.gate import GateOut as MinimalGateOut, gate_stream
from ..preferences_store import auto_capture_preferences, build_preferences_storage
from ..resources.service_messages.resources import get_friendly_error_message


class VersatileWorkflow(BaseWorkflow):
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
        bundle_props: Dict[str, Any] | None = None,
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
            bundle_props=bundle_props,
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

    def _capture_preferences_from_turn(self, *, text: str) -> None:
        if not self.bundle_prop("preferences.auto_capture", True):
            return
        user_id = getattr(self.runtime_ctx, "user_id", None) or "anonymous"
        try:
            bundle_id = getattr(getattr(self.config, "ai_bundle_spec", None), "id", None) or "versatile"
            tenant = getattr(self.runtime_ctx, "tenant", None)
            project = getattr(self.runtime_ctx, "project", None)
            if not tenant or not project:
                return
            storage = build_preferences_storage(
                tenant=tenant,
                project=project,
                bundle_id=bundle_id,
            )
            captured = auto_capture_preferences(storage, user_id, text=text, source="chat")
            if captured:
                self.logger.log(
                    f"[versatile] captured {len(captured)} preference observation(s) for {user_id}",
                    level="INFO",
                )
        except Exception:
            self.logger.log(traceback.format_exc(), level="ERROR")

    async def process(self, payload: dict) -> Dict[str, Any]:
        scratchpad = await self.construct_turn_and_scratchpad(payload)
        await self.start_turn(scratchpad)

        try:
            async def _gate_node(state: Dict[str, Any]) -> Dict[str, Any]:
                t2, ms2 = _tstart()
                async with with_accounting(
                    self.config.ai_bundle_spec.id,
                    agent="gate.simple",
                    metadata={"agent": "gate.simple"},
                ):
                    gate_payload, gate_channels = await gate_stream(
                        self.model_service,
                        is_new_conversation=bool(getattr(scratchpad, "is_new_conversation", False)),
                        on_thinking_delta=self.mk_streamer("gate"),
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
                scratchpad.route = (
                    getattr(scratchpad.gate, "route", None)
                    or gate_payload.get("route")
                    or ""
                ).strip().lower()
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

                await self.handle_conversation_title(scratchpad=scratchpad, pre_out=gate_payload)

                timing_gate = _tend(t2, ms2)
                scratchpad.timings.append({"title": "gate", "elapsed_ms": timing_gate["elapsed_ms"]})
                await self._emit(
                    {
                        "type": "chat.step",
                        "agent": "gate",
                        "step": "gate",
                        "status": "completed",
                        "title": "Gate",
                        "data": gate_payload,
                        "timing": timing_gate,
                    }
                )

                scratchpad.clarification_questions = [
                    q.strip()
                    for q in (gate_payload.get("clarification_questions") or [])
                    if q.strip()
                ]
                if scratchpad.clarification_questions:
                    try:
                        self.ctx_browser.contribute_clarification(
                            ticket=scratchpad.open_ticket,
                            clarification_questions=scratchpad.clarification_questions,
                        )
                    except Exception:
                        pass
                await self.persist_user_message(scratchpad)
                self._capture_preferences_from_turn(text=payload.get("text") or "")

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
                    scratchpad=scratchpad,
                )
                allowed_plugins = [
                    spec.get("alias")
                    for spec in (tools_descriptor.TOOLS_SPECS or [])
                    if spec.get("alias")
                ]
                for spec in (getattr(tools_descriptor, "MCP_TOOL_SPECS", None) or []):
                    alias = spec.get("alias") or f"mcp_{spec.get('server_id')}"
                    if alias:
                        allowed_plugins.append(alias)
                allowed_plugins = list(dict.fromkeys([alias for alias in allowed_plugins if alias]))

                sr = await react.run(allowed_plugins=allowed_plugins)
                try:
                    await react.persist_workspace()
                except Exception:
                    self.logger.log(traceback.format_exc(), level="ERROR")

                await self._emit_turn_work_status(
                    [
                        "closing the loop",
                        "wrapping up",
                        "tying it together",
                    ]
                )

                answer_text = sr.final_answer
                suggested_followups = [
                    item.strip()
                    for item in (sr.suggested_followups or [])
                    if isinstance(item, str) and item.strip()
                ]

                if answer_text:
                    scratchpad.answer = answer_text
                    scratchpad.suggested_followups = list(suggested_followups or [])
                    await self.emit_suggested_followups(suggested_followups=suggested_followups)
                    try:
                        await self.finish_turn(scratchpad, ok=True)
                    except Exception:
                        pass
                    state["result"] = {
                        "answer": answer_text,
                        "suggested_followups": suggested_followups,
                    }
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
            return final_state.get("result") or {
                "answer": scratchpad.answer or "",
                "suggested_followups": scratchpad.suggested_followups or [],
            }
        except Exception as exc:
            await self._handle_turn_exception(exc, scratchpad)
            raise
