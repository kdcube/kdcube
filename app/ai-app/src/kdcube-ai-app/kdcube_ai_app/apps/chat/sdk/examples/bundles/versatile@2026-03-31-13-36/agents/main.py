import logging
import traceback
from pathlib import Path
from typing import Any, Dict

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.context.memory.instructions import MEMORY_REACT_ADDITIONAL_INSTRUCTIONS
from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import CTurnScratchpad
from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import agent_skill_config_from_bundle_props
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import agent_tool_config_from_bundle_props
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.defaults import default_canvas_event_source_specs
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.instructions import CANVAS_REACT_ADDITIONAL_INSTRUCTIONS
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS,
    named_service_agent_event_source_namespaces,
    named_service_agent_pull_namespaces,
    named_service_namespace_client_tools_config,
    named_service_namespaces,
    register_configured_named_service_artifact_rehosters,
    register_configured_named_service_event_sources,
)
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.util import _tend, _tstart
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import Config, ModelServiceBase
from langgraph.graph import END, START, StateGraph

from .gate import GateOut as MinimalGateOut, gate_stream
from ..resources.service_messages.resources import get_friendly_error_message

LOGGER = logging.getLogger(__name__)
BUNDLE_ROOT = Path(__file__).resolve().parents[1]

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


def _resolve_named_service_react_instructions(bundle_props: Dict[str, Any] | None, *, client_id: Any = None) -> str:
    all_namespaces = named_service_namespaces(bundle_props or {})
    event_namespaces = named_service_agent_event_source_namespaces(bundle_props or {}, client_id=client_id or "main")
    pull_namespaces = named_service_agent_pull_namespaces(bundle_props or {}, client_id=client_id or "main")
    namespaces = {}
    for namespace, cfg in all_namespaces.items():
        tool_cfg = named_service_namespace_client_tools_config(
            bundle_props or {},
            namespace=str(namespace),
            client_id=client_id or "main",
        )
        if tool_cfg or namespace in event_namespaces or namespace in pull_namespaces:
            namespaces[namespace] = cfg
    if not namespaces:
        return ""
    rows = []
    for namespace, cfg in sorted(namespaces.items(), key=lambda item: str(item[0])):
        provider = cfg.get("provider") if isinstance(cfg, dict) else {}
        provider_id = provider.get("provider") if isinstance(provider, dict) else ""
        bundle_id = provider.get("bundle_id") if isinstance(provider, dict) else ""
        label = f"- `{namespace}`"
        details = ", ".join(str(value) for value in (provider_id, bundle_id) if value)
        rows.append(f"{label}: {details}" if details else label)
    # Static ecosystem/workflow teaching + the per-agent namespace roster. The
    # teaching block is the single source of truth (no provider operation ids,
    # only visible tool ids); this function just appends which namespaces this
    # agent may pass as the `namespace` argument. The tool catalog stays
    # authoritative for which named_services.* tools are actually callable.
    roster = "Named-service namespaces available to this agent (pass one as the `namespace` argument):\n" + "\n".join(rows)
    return f"{NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS}\n\n{roster}"


def _resolve_react_additional_instructions(
    comm_context: ExternalEventPayload,
    *,
    bundle_props: Dict[str, Any] | None = None,
    client_id: Any = None,
) -> str:
    blocks = [
        _resolve_react_ui_instructions(comm_context),
        MEMORY_REACT_ADDITIONAL_INSTRUCTIONS,
        CANVAS_REACT_ADDITIONAL_INSTRUCTIONS,
        _resolve_named_service_react_instructions(bundle_props, client_id=client_id),
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

    def _register_named_service_react_surfaces(self, *, client_id: Any = None) -> None:
        event_sources = getattr(getattr(self, "runtime_ctx", None), "event_sources", None)
        if event_sources is None:
            return
        actor = getattr(self.comm_context, "actor", None) if self.comm_context is not None else None
        tenant = str(
            getattr(getattr(self, "runtime_ctx", None), "tenant", None)
            or getattr(actor, "tenant_id", None)
            or ""
        )
        project = str(
            getattr(getattr(self, "runtime_ctx", None), "project", None)
            or getattr(actor, "project_id", None)
            or ""
        )
        effective_client_id = client_id or getattr(getattr(self, "runtime_ctx", None), "agent_id", None) or "main"
        pull_namespaces = named_service_agent_pull_namespaces(
            self.bundle_props or {},
            client_id=effective_client_id,
        )
        event_namespaces = named_service_agent_event_source_namespaces(
            self.bundle_props or {},
            client_id=effective_client_id,
        )
        rehoster_count = register_configured_named_service_artifact_rehosters(
            event_sources,
            namespaces=pull_namespaces,
            tenant=tenant,
            project=project,
            logger=LOGGER,
        )
        register_configured_named_service_event_sources(
            event_sources,
            namespaces=event_namespaces,
            logger=LOGGER,
        )
        try:
            self.logger.log(
                "[named_services.react_surfaces] "
                f"client_id={effective_client_id} "
                f"pull_namespaces={list(pull_namespaces.keys())} "
                f"event_namespaces={list(event_namespaces.keys())} "
                f"rehosters_registered={rehoster_count}",
                level="INFO",
            )
        except Exception:
            pass

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
                client_id = getattr(getattr(self, "runtime_ctx", None), "agent_id", None)
                tool_config = agent_tool_config_from_bundle_props(
                    self.bundle_props,
                    client_id,
                    bundle_root=BUNDLE_ROOT,
                )
                skill_config = agent_skill_config_from_bundle_props(
                    self.bundle_props,
                    client_id,
                    bundle_root=BUNDLE_ROOT,
                )
                react = self.build_react(
                    tools_runtime=tool_config.tool_runtime,
                    tool_traits=tool_config.tool_traits,
                    mod_tools_spec=tool_config.tool_specs,
                    mcp_tools_spec=tool_config.mcp_tool_specs,
                    event_source_specs=default_canvas_event_source_specs(),
                    custom_skills_root=skill_config.custom_skills_root,
                    skills_visibility_agents_config=skill_config.agents_config,
                    scratchpad=scratchpad,
                    additional_instructions=_resolve_react_additional_instructions(
                        self.comm_context,
                        bundle_props=self.bundle_props,
                        client_id=client_id,
                    ),
                )
                self._register_named_service_react_surfaces(client_id=client_id)

                sr = await react.run(
                    allowed_plugins=tool_config.allowed_plugins,
                    allowed_tool_names_by_alias=tool_config.allowed_tool_names_by_alias,
                )
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
