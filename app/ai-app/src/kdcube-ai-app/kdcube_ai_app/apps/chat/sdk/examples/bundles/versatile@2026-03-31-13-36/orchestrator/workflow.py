from typing import Any, Dict

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.context.memory.instructions import MEMORY_REACT_ADDITIONAL_INSTRUCTIONS
from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import CTurnScratchpad
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.instructions import CANVAS_REACT_ADDITIONAL_INSTRUCTIONS
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.util import _tend, _tstart
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import Config, ModelServiceBase
from langgraph.graph import END, START, StateGraph

from .. import events_descriptor, skills_descriptor, tools_descriptor
from ..agents.gate import GateOut as MinimalGateOut, gate_stream
from ..resources.service_messages.resources import get_friendly_error_message


# Channel-conditional ReAct instructions: the bundle decides at agent-construction
# time which surface the user is reaching us through, and passes the matching
# UI-topology description. The bundles.yaml common React config stays
# channel-agnostic — only what truly applies regardless of inbound surface.

_WEB_CHAT_REACT_INSTRUCTIONS = """\
UI topology for this chat (web interface):
- The user sees per-turn tabs and can switch between:
  * "Artifacts" tab — anything written via react.write channel="canvas" (large markdown reports, HTML/JSON/YAML/XML/Mermaid sources, slide/document sources, renderer inputs).
  * "Files" tab — downloadable files: anything kind="file", PDFs/PPTX/DOCX/PNG produced by rendering_tools.write_*, archives, spreadsheets, and other binary deliverables.
  * "Links" tab — citations (sources_pool entries cited) and any web_search/web_fetch URLs referenced.
  * "Steps" tab — tool calls and process steps for the turn.
  * "Timeline" tab — the full message-by-message timeline.
When the final_answer needs to point the user at something produced, use these exact tab names: e.g., "the report is in the Artifacts tab", "available in the Files tab", "see the Links tab for the source".
"""

_TELEGRAM_REACT_INSTRUCTIONS = """\
UI topology for this chat (Telegram):
- The user is interacting through Telegram. Everything reaches them as plain messages and attachments in a single conversation thread; there are no tabs, panels, or side areas.
- For artifacts written via react.write channel="canvas" and large files: confirm in plain prose that the artifact was produced and mention that the full version is available in the web chat for review (Telegram's message surface is short-form).
- For downloadable files small enough for Telegram, the user receives them as document attachments in the conversation thread directly.
"""


def _resolve_react_ui_instructions(comm_context: ExternalEventPayload) -> str:
    """
    Return the UI-topology block that matches the inbound channel for this turn.

    The ingress layer sets `event.source` to `ingress.<transport>` (e.g.,
    `ingress.telegram`, `ingress.web`). When the source is unknown we default
    to the web-chat instructions, since that's the canonical interface for
    the bundle.
    """
    source = ""
    event = getattr(comm_context, "event", None) if comm_context is not None else None
    if event is not None:
        source = str(getattr(event, "source", "") or "").strip().lower()
    if source.endswith(".telegram") or source == "ingress.telegram":
        return _TELEGRAM_REACT_INSTRUCTIONS
    return _WEB_CHAT_REACT_INSTRUCTIONS


def _resolve_react_additional_instructions(comm_context: ExternalEventPayload) -> str:
    blocks = [
        _resolve_react_ui_instructions(comm_context),
        MEMORY_REACT_ADDITIONAL_INSTRUCTIONS,
        CANVAS_REACT_ADDITIONAL_INSTRUCTIONS,
    ]
    return "\n\n".join(block.strip() for block in blocks if str(block or "").strip())


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
        comm_context: ExternalEventPayload,
        ctx_client: Any = None,
        pg_pool: Any = None,
        redis: Any = None,
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
            pg_pool=pg_pool,
            redis=redis,
            bundle_props=bundle_props,
            message_resources_fn=get_friendly_error_message,
        )
        self.gate_out_class = MinimalGateOut
        self.runtime_ctx.debug_log_announce = True
        self.runtime_ctx.debug_log_sources_pool = True
        if not getattr(self.runtime_ctx, "max_iterations", None):
            self.runtime_ctx.max_iterations = 15
        self.runtime_ctx.debug_timeline = self.react_debug_timeline_enabled(default=True)

    async def construct_turn_and_scratchpad(self, payload: dict) -> CTurnScratchpad:
        scratchpad = await super().construct_turn_and_scratchpad(payload)
        scratchpad.gate_out_class = MinimalGateOut
        return scratchpad

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

                state["route"] = scratchpad.route
                state["gate_payload"] = gate_payload
                return state

            async def _react_node(state: Dict[str, Any]) -> Dict[str, Any]:
                react = self.build_react(
                    tools_runtime=getattr(tools_descriptor, "TOOL_RUNTIME", None),
                    mod_tools_spec=tools_descriptor.TOOLS_SPECS,
                    mcp_tools_spec=getattr(tools_descriptor, "MCP_TOOL_SPECS", None) or [],
                    event_source_specs=getattr(events_descriptor, "EVENT_SOURCE_SPECS", None) or [],
                    custom_skills_root=skills_descriptor.CUSTOM_SKILLS_ROOT,
                    skills_visibility_agents_config=skills_descriptor.AGENTS_CONFIG or {},
                    scratchpad=scratchpad,
                    additional_instructions=_resolve_react_additional_instructions(self.comm_context),
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
                    await self.finish_turn(scratchpad, ok=True)
                    state["result"] = {
                        "answer": answer_text,
                        "suggested_followups": suggested_followups,
                    }
                    state["short_circuit"] = True
                else:
                    ok = getattr(scratchpad, "solver_status", None) != "failed"
                    await self.finish_turn(scratchpad, ok=ok)
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
