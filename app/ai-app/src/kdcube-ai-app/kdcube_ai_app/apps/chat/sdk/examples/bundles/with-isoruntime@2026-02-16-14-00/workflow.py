# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── workflow.py ──
# Core orchestration logic for the iso-runtime bundle.
#
# This is the main workflow that ties everything together. The execution flow:
#
#   1. construct_turn_and_scratchpad() — prepare turn context (from BaseWorkflow)
#   2. Create tool subsystem (loads tools from tools_descriptor.py)
#   3. Resolve user identity and workspace/sandbox paths (bootstrap.py)
#   4. Bootstrap sandbox — copy user workspace → sandbox (clean slate)
#   5. Select test scenario based on user message text (exec.py)
#   6. Build scenario — generate Python code + output contract (exec.py)
#   7. Execute code:
#      - WITH contract (exec_contract.py) — checks expected output files
#      - WITHOUT contract (exec_side_effects.py) — diffs the output dir
#   8. Sync sandbox back → user workspace (always, even on failure)
#   9. Collect diagnostics — parse logs for errors/tracebacks (exec.py)
#  10. Build UI timeline — visualize execution in chat (timeline.py)
#  11. Report results via communicator (events, errors, timeline)
#
# Isolation model (two layers):
#   FILE layer  — bootstrap.py copies workspace→sandbox before execution and
#                 syncs sandbox→workspace after (so failures don't corrupt data)
#   PROCESS layer — run_exec_tool() delegates to _InProcessRuntime which can
#                 execute code in one of three modes (configured per-deployment):
#                   • in-memory  — fastest, no isolation (dev/testing)
#                   • subprocess — Linux network namespace + UID drop
#                   • Docker     — full container isolation (production)
#                 In subprocess/Docker modes a ~900-line injected header is
#                 prepended to the user's main.py; it bootstraps OUTPUT_DIR,
#                 logging, tool modules, signal handlers, and reconstructs
#                 the parent's ModelService/Communicator via PortableSpec.

import os
import uuid
from typing import Dict, Any, Optional

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
    def _select_exec_runtime_for_scenario(self, scenario_id: str) -> Dict[str, Any]:
        """
        Select the exec runtime for a scenario.

        Most scenarios use the bundle's default runtime.
        Scenario 13 explicitly selects the profile named in bundle props:

          config.execution.runtime.profiles.fargate_default

        via:

          self.resolve_exec_runtime(profile="fargate_default")
        """
        if str(scenario_id) == "13":
            self.logger.log("[with-isoruntime] scenario 13 selecting exec runtime profile=fargate_default")
            return self.resolve_exec_runtime(profile="fargate_default")
        return self.resolve_exec_runtime()

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
            bundle_props: Optional[Dict[str, Any]] = None,
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
            # Provide user-friendly error messages (from resources.py)
            message_resources_fn=get_friendly_error_message,
        )
        # Enable verbose debug logging for this demo bundle
        self.runtime_ctx.debug_log_announce = True
        self.runtime_ctx.debug_log_sources_pool = True
        self.runtime_ctx.max_iterations = 15       # Max LLM tool-use iterations
        self.runtime_ctx.debug_timeline = True

    async def construct_turn_and_scratchpad(self, payload: dict) -> CTurnScratchpad:
        scratchpad = await super().construct_turn_and_scratchpad(payload)
        return scratchpad

    async def process(self, payload: dict) -> Dict[str, Any]:
        """
        Main entry point called by the Entrypoint orchestrate node.
        Runs the full: setup → execute → diagnose → report cycle.
        """
        scratchpad = await self.construct_turn_and_scratchpad(payload)

        try:

            async def _exec_node(state: Dict[str, Any]) -> Dict[str, Any]:
                # ── Step 1: Create tool subsystem (loads tools from tools_descriptor) ──
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

                # ── Step 2: Resolve user + bootstrap sandbox ──
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
                # Sandbox has two subdirectories:
                #   work/ — working directory for the running code
                #   out/  — output directory where code writes results
                workdir = sandbox_user_root / "work"
                outdir = sandbox_user_root / "out"
                workdir.mkdir(parents=True, exist_ok=True)
                outdir.mkdir(parents=True, exist_ok=True)

                # ── Step 3: Select and build a test scenario ──
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
                # ── Step 4: Execute the code in the sandbox ──
                # run_with_contract / run_without_contract delegate to run_exec_tool()
                # which writes main.py, prepends the injected header, and launches
                # _InProcessRuntime (subprocess with namespace isolation or Docker)
                exec_runtime = self._select_exec_runtime_for_scenario(scenario.id)
                try:
                    if use_contract:  # Most scenarios: check output against contract
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
                            exec_runtime=exec_runtime,
                        )
                        if err:
                            ok = False
                            answer_text = f"Invalid exec contract: {err.get('message', 'unknown error')}"
                    else:  # Scenario 12: side-effects mode, diff the output dir
                        envelope, tool_params, _ = await run_without_contract(
                            tool_manager=tool_subsystem,
                            logger=self.logger,
                            code=code,
                            timeout_s=timeout_s,
                            workdir=workdir,
                            outdir=outdir,
                            exec_id=exec_id,
                            prog_name=scenario.label,
                            exec_runtime=exec_runtime,
                        )
                finally:
                    # ── Step 5: Always sync sandbox → workspace (even on failure) ──
                    sync_user_sandbox(
                        user_id=user_id,
                        user_workspace_root=user_workspace_root,
                        sandbox_root=sandbox_root,
                        logger=self.logger,
                    )
                if ok:
                    # ── Step 6: Collect diagnostics (logs, errors, tracebacks) ──
                    diagnostics = collect_exec_diagnostics(
                        sandbox_root=sandbox_user_root,
                        outdir=outdir,
                        exec_id=exec_id,
                    )
                    # Report errors found in executor and infra logs
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
                    # ── Step 7: Emit full execution report to the UI ──
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
                    # ── Step 8: Build and render the execution timeline for the UI ──
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
                    # ── Step 9: Format the final answer ──
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

                # ── Step 10: Finalize the turn ──
                scratchpad.answer = answer_text
                await self.emit_suggested_followups(suggested_followups=scenarios())

                await self.finish_turn(scratchpad, ok=ok)
                state["result"] = {"answer": answer_text, "suggested_followups": []}
                state["short_circuit"] = True
                return state

            # Wrap _exec_node in a single-node LangGraph for structured execution
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
