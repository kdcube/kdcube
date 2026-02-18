# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
import os
import uuid
from typing import Dict, Any

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import CTurnScratchpad
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import create_tool_subsystem_with_mcp
from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import (
    build_exec_output_contract,
)
from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, Config
from langgraph.graph import StateGraph, START, END

from . import tools_descriptor
from .resources import get_friendly_error_message
from .bootstrap import (
    resolve_roots,
    resolve_user_id,
    bootstrap_user_sandbox,
    sync_user_sandbox,
)
from .exec import (
    collect_exec_diagnostics,
    scenarios,
    select_scenario,
    build_scenario,
)
from .exec_contract import run_with_contract
from .exec_side_effects import run_without_contract
from .timeline import build_exec_timeline, render_timeline_text


class WithIsoRuntimeWorkflow(BaseWorkflow):
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
        self.runtime_ctx.debug_log_announce = True
        self.runtime_ctx.debug_log_sources_pool = True
        self.runtime_ctx.max_iterations = 15
        self.runtime_ctx.debug_timeline = True

    async def construct_turn_and_scratchpad(self, payload: dict) -> CTurnScratchpad:
        scratchpad = await super().construct_turn_and_scratchpad(payload)
        return scratchpad

    async def process(self, payload: dict) -> Dict[str, Any]:
        scratchpad = await self.construct_turn_and_scratchpad(payload)
        # await self.start_turn(scratchpad)

        try:

            async def _exec_node(state: Dict[str, Any]) -> Dict[str, Any]:
                tool_subsystem, _ = create_tool_subsystem_with_mcp(
                    service=self.model_service,
                    comm=self.comm,
                    logger=self.logger,
                    bundle_spec=self.config.ai_bundle_spec,
                    context_rag_client=self.ctx_client,
                    registry={"kb_client": self.kb},
                    raw_tool_specs=tools_descriptor.TOOLS_SPECS,
                    tool_runtime=getattr(tools_descriptor, "TOOL_RUNTIME", None),
                    mcp_tool_specs=[],
                    mcp_env_json=os.environ.get("MCP_SERVICES") or "",
                )

                user_id = resolve_user_id(
                    runtime_ctx_user_id=self.runtime_ctx.user_id,
                    fallback_user=scratchpad.user,
                )
                user_workspace_root, sandbox_root = resolve_roots()
                sandbox_user_root = bootstrap_user_sandbox(
                    user_id=user_id,
                    user_workspace_root=user_workspace_root,
                    sandbox_root=sandbox_root,
                    logger=self.logger,
                )
                workdir = sandbox_user_root / "work"
                outdir = sandbox_user_root / "out"
                workdir.mkdir(parents=True, exist_ok=True)
                outdir.mkdir(parents=True, exist_ok=True)

                turn_id = self.runtime_ctx.turn_id or scratchpad.turn_id or "turn_unknown"
                scenario = select_scenario(getattr(scratchpad, "user_text", "") or "")
                scenario_payload = build_scenario(turn_id=turn_id, scenario=scenario)
                contract_spec = scenario_payload.get("contract") or []
                use_contract = bool(scenario_payload.get("use_contract", True))
                ok = True
                code = scenario_payload.get("code") or ""
                timeout_s = int(scenario_payload.get("timeout_s") or 120)
                tool_call_id = f"tc_{uuid.uuid4().hex[:12]}"
                exec_id = f"{turn_id}-{tool_call_id}"
                tool_params: Dict[str, Any] = {}
                envelope: Dict[str, Any] = {}
                try:
                    if use_contract:
                        envelope, err, tool_params = await run_with_contract(
                            tool_manager=tool_subsystem,
                            logger=self.logger,
                            contract_spec=contract_spec,
                            code=code,
                            timeout_s=timeout_s,
                            workdir=workdir,
                            outdir=outdir,
                            exec_id=exec_id,
                            prog_name=scenario.label,
                        )
                        if err:
                            ok = False
                            answer_text = f"Invalid exec contract: {err.get('message', 'unknown error')}"
                    else:
                        envelope, tool_params, _ = await run_without_contract(
                            tool_manager=tool_subsystem,
                            logger=self.logger,
                            code=code,
                            timeout_s=timeout_s,
                            workdir=workdir,
                            outdir=outdir,
                            exec_id=exec_id,
                            prog_name=scenario.label,
                        )
                finally:
                    sync_user_sandbox(
                        user_id=user_id,
                        user_workspace_root=user_workspace_root,
                        sandbox_root=sandbox_root,
                        logger=self.logger,
                    )
                if ok:
                    diagnostics = collect_exec_diagnostics(
                        sandbox_root=sandbox_user_root,
                        outdir=outdir,
                        exec_id=exec_id,
                    )
                    if diagnostics.get("has_error") == "1":
                        await self.comm.error(
                            message="Program error detected in user.log",
                            agent="turn.error",
                            data={
                                "error_type": "executor_log_error",
                                "error_lines": diagnostics.get("error_lines") or "",
                                "tracebacks": diagnostics.get("tracebacks") or "",
                            },
                        )
                    if diagnostics.get("runtime_has_error") == "1":
                        await self.comm.error(
                            message="Infra error detected in infra.log",
                            agent="turn.error",
                            data={
                                "error_type": "infra_log_error",
                                "error_lines": diagnostics.get("runtime_error_lines") or "",
                                "tracebacks": diagnostics.get("runtime_tracebacks") or "",
                            },
                        )
                    await self.comm.event(
                        agent="exec",
                        type="chat.exec_report",
                        title="Execution Report",
                        data={
                            "tree": diagnostics.get("tree") or "",
                            "executor_info_log": diagnostics.get("info_log") or "",
                            "executor_error_log": diagnostics.get("error_log") or "",
                            "executor_error_lines": diagnostics.get("error_lines") or "",
                            "executor_tracebacks": diagnostics.get("tracebacks") or "",
                            "runtime_error_log": diagnostics.get("runtime_error_log") or "",
                            "runtime_error_lines": diagnostics.get("runtime_error_lines") or "",
                            "runtime_tracebacks": diagnostics.get("runtime_tracebacks") or "",
                        },
                        markdown=(
                            "### Execution Workspace Tree\n"
                            f"```\n{diagnostics.get('tree') or ''}\n```\n\n"
                            "### Executor Info Log (tail)\n"
                            f"```\n{diagnostics.get('info_log') or ''}\n```\n\n"
                            "### Executor Error Log (tail)\n"
                            f"```\n{diagnostics.get('error_log') or ''}\n```\n\n"
                            "### Executor Error Lines\n"
                            f"```\n{diagnostics.get('error_lines') or ''}\n```\n\n"
                            "### Executor Tracebacks\n"
                            f"```\n{diagnostics.get('tracebacks') or ''}\n```\n"
                            "\n### Infra Log (tail)\n"
                            f"```\n{diagnostics.get('runtime_error_log') or ''}\n```\n\n"
                            "### Infra Error Lines\n"
                            f"```\n{diagnostics.get('runtime_error_lines') or ''}\n```\n\n"
                            "### Infra Tracebacks\n"
                            f"```\n{diagnostics.get('runtime_tracebacks') or ''}\n```\n"
                        ),
                    )
                    try:
                        timeline_browser = await build_exec_timeline(
                            runtime_ctx=self.runtime_ctx,
                            tool_call_id=tool_call_id,
                            tool_response=envelope or {},
                            tool_params=tool_params,
                            outdir=outdir,
                            scenario_label=scenario.label,
                            scenario_description=scenario.description,
                            code_text=code,
                        )
                        timeline_text = await render_timeline_text(timeline_browser)
                        if timeline_text:
                            await self.comm.event(
                                agent="exec",
                                type="chat.exec_timeline",
                                title="Execution Timeline",
                                data={"scenario": scenario.label},
                                markdown=f"```\n{timeline_text}\n```",
                            )
                    except Exception as exc:
                        try:
                            self.logger.log(
                                f"[with-isoruntime] timeline render failed: {exc}",
                                level="WARNING",
                            )
                        except Exception:
                            pass
                    ok = bool(envelope.get("ok"))
                    if ok:
                        produced = [item.get("filename") for item in (envelope.get("succeeded") or []) if item]
                        produced = [p for p in produced if isinstance(p, str) and p]
                        produced_text = ", ".join(produced) if produced else "no files"
                        answer_text = f"{scenario.label}: completed. Produced: {produced_text}."
                    else:
                        err_obj = envelope.get("error") or {}
                        answer_text = (
                            f"{scenario.label}: failed — "
                            f"{err_obj.get('description') or err_obj.get('error') or 'unknown error'}"
                        )

                scratchpad.answer = answer_text
                await self.emit_suggested_followups(suggested_followups=scenarios())

                try:
                    await self.finish_turn(scratchpad, ok=ok)
                except Exception:
                    pass
                state["result"] = {"answer": answer_text, "suggested_followups": []}
                state["short_circuit"] = True
                return state

            graph = StateGraph(dict)
            graph.add_node("exec", _exec_node)
            graph.add_edge(START, "exec")
            graph.add_edge("exec", END)
            flow = graph.compile()

            initial_state = dict()
            final_state = await flow.ainvoke(initial_state)
            return final_state.get("result") or {"answer": scratchpad.answer or "", "suggested_followups": scratchpad.suggested_followups or []}

        except Exception as e:
            await self._handle_turn_exception(e, scratchpad)
            raise
