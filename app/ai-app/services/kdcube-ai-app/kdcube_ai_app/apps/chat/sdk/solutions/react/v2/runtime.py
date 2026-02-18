# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/runtime.py

import asyncio
import json
import pathlib
import random
import traceback

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, Awaitable, Type

from langgraph.graph import StateGraph, END

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.infra import emit_event
from kdcube_ai_app.apps.chat.sdk.runtime.execution import execute_tool, _safe_label
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.agents.decision import react_decision_stream_v2
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import ReactResult
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import ApplicationHostingService
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.exec import DecisionExecCodeStreamer
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.canvas import (
    ReactWriteContentStreamer,
    RenderingWriteContentStreamer,
    TimelineStreamer,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers

from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger
import logging

from kdcube_ai_app.apps.chat.sdk.solutions.widgets.conversation_turn_work_status import (
    ConversationTurnWorkStatus,
)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.plan import apply_plan_updates
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.round import ReactRound
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import ReactStateSnapshot

@dataclass
class ReactStateV2:
    session_id: str
    turn_id: str

    adapters: List[Dict[str, Any]]

    workdir: pathlib.Path
    outdir: pathlib.Path
    plan_steps: List[str]
    plan_status: Dict[str, str] = field(default_factory=dict)
    # next_decision_model: str = "strong"

    # Loop control
    iteration: int = 0
    max_iterations: int = 6
    decision_retries: int = 0
    max_decision_retries: int = 2

    exit_reason: Optional[str] = None
    final_answer: Optional[str] = None
    suggested_followups: Optional[List[str]] = None

    last_decision: Optional[Dict[str, Any]] = None
    last_tool_result: Optional[List[Dict[str, Any]]] = None

    pending_tool_skills: Optional[List[str]] = None

    session_log: List[Dict[str, Any]] = field(default_factory=list)
    round_timings: List[Dict[str, Any]] = field(default_factory=list)


class ReactSolverV2:
    MODULE_AGENT_NAME = "solver.react.v2"
    DECISION_AGENT_NAME = "decision.v2"

    def __init__(
        self,
        *,
        service: ModelServiceBase,
        logger: AgentLogger,
        tools_subsystem: ToolSubsystem,
        skills_subsystem: Any,

        scratchpad: TurnScratchpad,
        comm: ChatCommunicator,
        comm_context: ChatTaskPayload,
        hosting_service: Optional[ApplicationHostingService] = None,
        ctx_browser: Optional[ContextBrowser] = None,
    ) -> None:
        self.svc = service
        if isinstance(logger, AgentLogger):
            self.log = logger
        else:
            name = getattr(logger, "name", None) or "react.v2"
            level = "INFO"
            try:
                lvl = getattr(logger, "level", None)
                if isinstance(lvl, int):
                    level = logging.getLevelName(lvl)
            except Exception:
                level = "INFO"
            self.log = AgentLogger(str(name), str(level))
        self.tools_subsystem = tools_subsystem
        self.skills_subsystem = skills_subsystem
        self.scratchpad = scratchpad
        self.comm = comm
        self.comm_context = comm_context
        self.hosting_service = hosting_service
        self.ctx_browser = ctx_browser
        self.graph = self._build_graph()
        self._timeline_text_idx = {}
        self._outdir_cv_token = None
        self._workdir_cv_token = None

    async def _render_timeline_with_announce(
        self,
        *,
        iteration: int,
        max_iterations: int,
        force_sanitize: bool = False,
    ) -> List[Dict[str, Any]]:
        if not self.ctx_browser:
            return []
        await self._update_announce(iteration=iteration, max_iterations=max_iterations)
        return await self.ctx_browser.timeline.render(
            cache_last=True,
            force_sanitize=force_sanitize,
            include_sources=True,
            include_announce=True,
        )

    async def _update_announce(self, *, iteration: int, max_iterations: int) -> None:
        if not self.ctx_browser:
            return
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_announce_text
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
            active_block = build_announce_text(
                iteration=iteration,
                max_iterations=max_iterations,
                started_at=getattr(self.scratchpad, "started_at", "") or "",
                timezone=getattr(runtime_ctx, "timezone", None) if runtime_ctx else None,
                timeline_blocks=self.ctx_browser.timeline.blocks,
                constraints=None,
                mode=getattr(runtime_ctx, "announce_mode", "full") if runtime_ctx else "full",
            )
            debug_announce = bool(getattr(runtime_ctx, "debug_log_announce", False))
            debug_sources = bool(getattr(runtime_ctx, "debug_log_sources_pool", False))
            if debug_announce:
                try:
                    self.log.log(f"[react.v2] announce:\n{active_block}", level="INFO")
                except Exception:
                    pass
            if debug_sources:
                try:
                    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_sources_pool_text
                    sources_text = build_sources_pool_text(
                        sources_pool=list(self.ctx_browser.sources_pool or []),
                    )
                    if sources_text:
                        self.log.log(f"[react.v2] sources_pool:\n{sources_text}", level="INFO")
                except Exception:
                    pass
            self.ctx_browser.announce(
                blocks=[{"text": active_block}],
            )
        except Exception:
            pass

    def _build_graph(self) -> StateGraph:

        def failing_node(state: Dict[str, Any]):
            """Simulate a tool/API failure"""
            try:
                raise ValueError("PPTX file was not produced")
            except Exception as exc:
                tb = traceback.format_exc()
                raise RuntimeError(f"[react.v2] Error control {exc}/{tb}") from exc

        wf = StateGraph(dict)
        wf.add_node("decision", self._decision_node)
        wf.add_node("tool_execution", self._tool_execution_node)
        wf.add_node("exit", self._exit_node)

        wf.set_entry_point("decision")
        wf.add_conditional_edges(
            "decision",
            self._route_after_decision,
            {"tool_execution": "tool_execution", "decision": "decision", "exit": "exit"},
        )
        wf.add_edge("tool_execution", "decision")
        wf.add_edge("exit", END)
        return wf.compile()

    def _mk_mainstream(self, phase: str) -> Callable[..., Awaitable[None]]:
        counters: Dict[str, int] = {}

        async def emit_delta(**kwargs):
            text = kwargs.get("text") or ""
            if not text:
                return
            marker = kwargs.get("marker") or kwargs.get("channel") or "thinking"
            i = counters.get(marker, 0)
            counters[marker] = i + 1
            author = f"{self.MODULE_AGENT_NAME}.{phase}"
            await self.comm.delta(text=text, index=i, marker=marker, agent=author, completed=bool(kwargs.get("completed")))

        return emit_delta

    def _mk_exec_code_streamer(
        self,
        phase: str,
        idx: int,
        execution_id: Optional[str] = None,
    ) -> tuple[Callable[[str], Awaitable[None]], DecisionExecCodeStreamer]:
        artifact_suffix = execution_id or str(idx)
        streamer = DecisionExecCodeStreamer(
            emit_delta=self.comm.delta,
            agent=f"{self.MODULE_AGENT_NAME}.{phase}",
            artifact_name=f"react.exec.{artifact_suffix}",
            execution_id=execution_id,
        )
        return self._wrap_raw_streamer(streamer), streamer

    def _mk_content_streamers(
        self,
        phase: str,
        *,
        sources_list: Optional[List[Dict[str, object]]] = None,
        artifact_name: Optional[str] = None,
    ) -> tuple[List[Callable[[str], Awaitable[None]]], List[Any]]:
        safe_name = artifact_name or f"react.record.{uuid.uuid4().hex[:8]}"
        sources_getter = None
        if self.ctx_browser:
            sources_getter = lambda: list(self.ctx_browser.sources_pool or [])
        base_args = {
            "emit_delta": self.comm.delta,
            "agent": f"{self.MODULE_AGENT_NAME}.{phase}",
            "artifact_name": safe_name,
            "sources_list": sources_list or [],
            "sources_getter": sources_getter,
            "turn_id": (self.ctx_browser.runtime_ctx.turn_id if self.ctx_browser and self.ctx_browser.runtime_ctx else ""),
        }
        react_streamer = ReactWriteContentStreamer(
            **base_args,
            stream_tool_id="react.write",
        )
        rendering_streamer = RenderingWriteContentStreamer(
            **base_args,
            write_tool_prefix="rendering_tools.write_",
        )
        fns = [
            self._wrap_json_streamer(react_streamer, sources_list=sources_list),
            self._wrap_json_streamer(rendering_streamer, sources_list=sources_list),
        ]
        return fns, [react_streamer, rendering_streamer]

    def _wrap_json_streamer(
        self,
        streamer: Any,
        *,
        sources_list: Optional[List[Dict[str, object]]] = None,
    ) -> Callable[[str], Awaitable[None]]:
        if sources_list is not None and hasattr(streamer, "update_sources"):
            try:
                streamer.update_sources(sources_list)
            except Exception:
                pass

        async def emit_json_delta(text: str, completed: bool = False, **_kwargs):
            if completed:
                await streamer.finish()
                return
            await streamer.feed(text)

        return emit_json_delta

    def _wrap_raw_streamer(
        self,
        streamer: Any,
    ) -> Callable[[str], Awaitable[None]]:
        async def emit_raw_delta(text: str, completed: bool = False, **_kwargs):
            if completed:
                await streamer.finish()
                return
            await streamer.feed_raw(text)

        return emit_raw_delta

    def _mk_timeline_streamer(
        self,
        phase: str,
        *,
        sources_list: Optional[List[Dict[str, object]]] = None,
        agent: Optional[str] = None,
        notes_artifact_name: Optional[str] = None,
        final_answer_artifact_name: Optional[str] = None,
    ) -> tuple[Callable[[str], Awaitable[None]], TimelineStreamer]:
        sources_getter = None
        if self.ctx_browser:
            sources_getter = lambda: list(self.ctx_browser.sources_pool or [])
        streamer = TimelineStreamer(
            emit_delta=self.comm.delta,
            agent=agent or f"{self.MODULE_AGENT_NAME}.{phase}",
            sources_list=sources_list or [],
            sources_getter=sources_getter,
            notes_artifact_name=notes_artifact_name or "timeline_text.react.decision",
            final_answer_artifact_name=final_answer_artifact_name or "react.final_answer",
        )
        return self._wrap_json_streamer(streamer, sources_list=sources_list), streamer

    def _next_tool_streamer_idx(self, outdir: pathlib.Path, tool_id: str) -> int:
        dest_dir = outdir / "executed_programs"
        if not dest_dir.exists():
            return 0
        label = _safe_label(tool_id)
        max_idx = -1
        for path in dest_dir.glob("*_main.py"):
            if not path.name.startswith(label + "_"):
                continue
            parts = path.name.split("_")
            if len(parts) < 2:
                continue
            try:
                idx = int(parts[1])
                max_idx = max(max_idx, idx)
            except Exception:
                continue
        return max_idx + 1

    def _short_json(self, obj: Any, *, max_len: int = 800) -> str:
        try:
            text = json.dumps(obj, ensure_ascii=False)
        except Exception:
            try:
                text = str(obj)
            except Exception:
                return "<unprintable>"
        if len(text) > max_len:
            return text[:max_len] + "...(truncated)"
        return text

    def _protocol_violation_message(
        self,
        *,
        code: str,
        state: Dict[str, Any],
        decision: Dict[str, Any] = None,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        action = (decision.get("action") or "").strip()
        tool_id = ((decision.get("tool_call") or {}).get("tool_id") or "").strip()
        final_answer = decision.get("final_answer") or ""
        if code.startswith("invalid_action"):
            return f"Action '{action}' is not allowed. Allowed: call_tool | complete | exit."
        if code == "final_answer_required":
            return "final_answer is required for action=complete/exit."
        if code == "missing_tool_id":
            return "tool_call.tool_id is missing for action=call_tool."
        if code == "missing_contract":
            return f"exec tool requires params.contract (tool_id={tool_id or 'unknown'})."
        if code == "tool_call_invalid":
            return f"tool_call failed protocol validation for tool_id={tool_id or 'unknown'}."
        if code == "tool_signature_red":
            return f"tool params failed signature validation for tool_id={tool_id or 'unknown'}."
        if code == "ReactDecisionOutV2_schema_error":
            return f"Bad Protocol. ReactDecisionOutV2_schema_error, cannot parse agent output. {error}'."
        if final_answer and action == "call_tool":
            return f"final_answer present with action={action}."
        if extra:
            try:
                return f"Protocol violation: {code}. extra={self._short_json(extra)}"
            except Exception:
                return f"Protocol violation: {code}."
        return f"Protocol violation: {code}."

    async def _emit_timeline_text(self, *, text: str, agent: str, artifact_name: str):
        if not text:
            return
        idx = int(self._timeline_text_idx.get(artifact_name, 0))
        await self.comm.delta(
            text=text,
            index=idx,
            marker="timeline_text",
            agent=agent,
            format="markdown",
            artifact_name=artifact_name,
            completed=False,
        )
        self._timeline_text_idx[artifact_name] = idx + 1
        await self.comm.delta(
            text="",
            index=idx + 1,
            marker="timeline_text",
            agent=agent,
            format="markdown",
            artifact_name=artifact_name,
            completed=True,
        )
        self._timeline_text_idx[artifact_name] = idx + 2

    def _validate_decision(self, decision: Dict[str, Any]) -> Optional[str]:
        action = (decision.get("action") or "").strip()
        if action not in {"call_tool", "complete", "exit"}:
            return f"invalid_action:{action}"
        if action in {"complete", "exit"} and not (decision.get("final_answer") or "").strip():
            return "final_answer_required"
        if action != "call_tool":
            return None
        tool_call = decision.get("tool_call") or {}
        tool_id = (tool_call.get("tool_id") or "").strip()
        if not tool_id:
            return "missing_tool_id"
        if tool_id == "react.read":
            return None
        if tool_id == "react.write":
            params = tool_call.get("params") or {}
            if isinstance(params, dict) and params:
                keys = list(params.keys())
                if len(keys) >= 1 and keys[0] != "path":
                    return "path_not_first"
                if len(keys) >= 2 and keys[1] != "channel":
                    return "channel_not_second"
                if len(keys) >= 3 and keys[2] != "content":
                    return "content_not_third"
                if len(keys) >= 4 and keys[3] != "kind":
                    return "kind_not_fourth"
        return None

    def _validate_tool_call_protocol(
        self,
        *,
        tool_call: Any,
        adapters_by_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        violations: List[Dict[str, Any]] = []

        tc = tool_call if isinstance(tool_call, dict) else {}
        tool_id = (tc.get("tool_id") or "").strip() or None

        params = tc.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            violations.append({
                "code": "bad_params",
                "message": "tool_call.params must be a dict",
            })
            params = {}

        if not tool_id:
            violations.append({
                "code": "missing_tool_id",
                "message": "tool_call.tool_id is missing or empty",
            })
        else:
            react_tool_ids = {
                "react.read",
                "react.write",
                "react.plan",
                "react.hide",
                "react.memsearch",
                "react.patch",
                "react.search_files",
            }
            if tool_id not in adapters_by_id and tool_id not in react_tool_ids:
                violations.append({
                    "code": "unknown_tool_id",
                    "message": f"tool_id '{tool_id}' is not in adapters/available tools",
                    "tool_id": tool_id,
                })
        allowed_params: set[str] = set()
        if tool_id in {
            "react.read",
            "react.write",
            "react.plan",
            "react.hide",
            "react.memsearch",
            "react.patch",
            "react.search_files",
        }:
            if tool_id == "react.read":
                allowed_params.update({"paths"})
            elif tool_id == "react.plan":
                allowed_params.update({"mode", "steps"})
            elif tool_id == "react.hide":
                allowed_params.update({"path", "replacement"})
            elif tool_id == "react.memsearch":
                allowed_params.update({"query", "targets", "top_k", "days"})
            elif tool_id == "react.patch":
                allowed_params.update({"path", "channel", "patch", "kind"})
            elif tool_id == "react.search_files":
                allowed_params.update({"name_regex", "content_regex", "max_files", "max_bytes", "max_hits"})
            else:
                allowed_params.update({"path", "channel", "content", "kind"})
        else:
            adapter = adapters_by_id.get(tool_id) if tool_id else None
            if adapter:
                doc_args = (adapter.get("doc") or {}).get("args") or {}
                if isinstance(doc_args, dict):
                    allowed_params.update(str(k) for k in doc_args.keys())
                template = adapter.get("call_template") or ""
                if "(" in template and ")" in template:
                    inner = template.split("(", 1)[1].rsplit(")", 1)[0]
                    parts = [p.strip() for p in inner.split(",") if p.strip()]
                    for p in parts:
                        name = p.split("=", 1)[0].strip()
                        if name:
                            allowed_params.add(name)

        artifact_specs: List[Dict[str, Any]] = []
        if tools_insights.is_exec_tool(tool_id):
            contract = params.get("contract") if isinstance(params, dict) else None
            if not isinstance(contract, list) or not contract:
                violations.append({
                    "code": "missing_contract",
                    "message": "exec_tools.execute_code_python requires params.contract",
                    "tool_id": tool_id,
                })
            else:
                for a in contract:
                    if not isinstance(a, dict):
                        continue
                    nm = (a.get("name") or "").strip()
                    if not nm:
                        continue
                    rec = {"name": nm, "kind": "file"}
                    if a.get("filename"):
                        rec["filename"] = a.get("filename")
                    if a.get("description"):
                        rec["description"] = a.get("description")
                    artifact_specs.append(rec)
        if tool_id == "react.write":
            if isinstance(params, dict) and params:
                keys = list(params.keys())
                if len(keys) >= 1 and keys[0] != "path":
                    violations.append({
                        "code": "path_not_first",
                        "message": "react.write params must start with path",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 2 and keys[1] != "channel":
                    violations.append({
                        "code": "channel_not_second",
                        "message": "react.write params must have channel as the second field",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 3 and keys[2] != "content":
                    violations.append({
                        "code": "content_not_third",
                        "message": "react.write params must have content as the third field",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 4 and keys[3] != "kind":
                    violations.append({
                        "code": "kind_not_fourth",
                        "message": "react.write params must have kind as the fourth field",
                        "tool_id": tool_id,
                    })
        if tool_id == "react.read":
            if isinstance(params, dict):
                if not isinstance(params.get("paths"), list):
                    violations.append({
                        "code": "bad_params",
                        "message": "react.read params.paths must be a list",
                        "tool_id": tool_id,
                    })
        # ref: bindings validated separately (need visibility + sources_pool rules)

        ok = len(violations) == 0
        return {
            "ok": ok,
            "tool_id": tool_id,
            "artifact_specs": artifact_specs,
            "violations": violations,
            "params": params,
        }

    async def prepare_session(
        self,
        *,
        allowed_plugins: List[str],
    ) -> ReactStateV2:
        outdir = pathlib.Path(self.ctx_browser.runtime_ctx.outdir or "")
        workdir = pathlib.Path(self.ctx_browser.runtime_ctx.workdir or "")
        try:
            from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import set_active_skills_subsystem
            if self.skills_subsystem:
                set_active_skills_subsystem(self.skills_subsystem)
        except Exception:
            pass

        session_id = f"react-v2-{uuid.uuid4().hex[:8]}"
        turn_id = self.ctx_browser.runtime_ctx.turn_id
        self.log.log(f"[react.v2] Start {session_id} in {workdir}")

        adapters = await self.tools_subsystem.react_tools(
            allowed_plugins=allowed_plugins,
            allowed_ids=None,
        )

        plan_steps: List[str] = []
        max_iterations = int(getattr(self.ctx_browser.runtime_ctx, "max_iterations", None) or 15)
        if max_iterations <= 0:
            max_iterations = 15

        return ReactStateV2(
            session_id=session_id,
            turn_id=turn_id,
            plan_steps=plan_steps,
            plan_status={},
            adapters=adapters,
            workdir=workdir,
            outdir=outdir,
            max_iterations=max_iterations,
            decision_retries=0,
            max_decision_retries=2,
        )

    async def run(
        self,
        *,
        allowed_plugins: List[str],
    ):
        turn_status = ConversationTurnWorkStatus(
            emit_delta=self.comm.delta,
            agent=self.MODULE_AGENT_NAME,
        )
        await turn_status.send(random.choice(["executing", "working", "solving"]))

        state = await self.prepare_session(
            allowed_plugins=allowed_plugins,
        )

        start_ts = time.time()
        try:
            recursion_limit = max(20, (int(state.max_iterations) * 3) + 6)
            final_state = await self.graph.ainvoke(self._to_dict(state), config={"recursion_limit": recursion_limit})
        except Exception as exc:
            tb = traceback.format_exc()
            try:
                self.log.log(f"[react.v2] Graph error: {exc}\n{tb}", level="ERROR")
            except Exception:
                pass
            raise RuntimeError(f"[react.v2] Graph error: {exc}\n{tb}") from exc
        finally:
            # workspace is managed by ContextBrowser; no CV reset here
            self._outdir_cv_token = None
            self._workdir_cv_token = None

        try:
            final_state["total_runtime_sec"] = float(time.time() - start_ts)
        except Exception:
            pass

        return await self._analyze_and_build_result(final_state)

    def _to_dict(self, s: ReactStateV2) -> Dict[str, Any]:
        return {
            "session_id": s.session_id,
            "turn_id": s.turn_id,
            "plan_steps": s.plan_steps,
            "plan_status": s.plan_status,
            "adapters": s.adapters,
            "workdir": str(s.workdir),
            "outdir": str(s.outdir),
            # "next_decision_model": s.next_decision_model,
            "iteration": s.iteration,
            "max_iterations": s.max_iterations,
            "decision_retries": s.decision_retries,
            "max_decision_retries": s.max_decision_retries,
            "exit_reason": s.exit_reason,
            "final_answer": s.final_answer,
            "suggested_followups": s.suggested_followups,
            "last_decision": s.last_decision,
            "last_tool_result": s.last_tool_result,
            "pending_tool_skills": s.pending_tool_skills,
            "session_log": s.session_log,
            "round_timings": s.round_timings,
        }

    async def persist_workspace(self) -> Optional[dict]:
        """
        Persist execution snapshot (out/work trees) into ConversationStore.
        Uses runtime_ctx for all required fields.
        """
        if not self.hosting_service or not self.ctx_browser:
            return None
        runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
        if not runtime_ctx:
            return None

        def _derive_run_id(path_value: Optional[str]) -> str:
            if not path_value:
                return ""
            try:
                p = pathlib.Path(path_value)
                if p.name in {"work", "out"} and p.parent.name:
                    return p.parent.name
                return p.name
            except Exception:
                return ""

        tenant = getattr(runtime_ctx, "tenant", None)
        project = getattr(runtime_ctx, "project", None)
        user = getattr(runtime_ctx, "user_id", None)
        user_type = getattr(runtime_ctx, "user_type", None) or "anonymous"
        conversation_id = getattr(runtime_ctx, "conversation_id", None)
        turn_id = getattr(runtime_ctx, "turn_id", None)
        workdir = getattr(runtime_ctx, "workdir", None)
        outdir = getattr(runtime_ctx, "outdir", None)
        codegen_run_id = _derive_run_id(workdir) or _derive_run_id(outdir)

        if not (tenant and project and conversation_id and turn_id and codegen_run_id):
            return None

        try:
            return await self.hosting_service.persist_workspace(
                outdir=outdir,
                workdir=workdir,
                tenant=tenant,
                project=project,
                user=user,
                conversation_id=conversation_id,
                user_type=user_type,
                turn_id=turn_id,
                codegen_run_id=codegen_run_id,
            )
        except Exception as exc:
            try:
                self.log.log(f"[react.v2] persist_workspace failed: {exc}", level="ERROR")
            except Exception:
                pass
            return None

    def _route_after_decision(self, state: Dict[str, Any]) -> str:
        if state.get("retry_decision"):
            try:
                self.log.log("[react.v2] retry_decision=True -> route=decision", level="INFO")
            except Exception:
                pass
            state["retry_decision"] = False
            return "decision"
        decision = state.get("last_decision") or {}
        action = decision.get("action")
        if action == "call_tool":
            try:
                self.log.log("[react.v2] route=tool_execution", level="INFO")
            except Exception:
                pass
            return "tool_execution"
        try:
            self.log.log(f"[react.v2] route=exit action={action}", level="INFO")
        except Exception:
            pass
        return "exit"

    async def _decision_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        iteration = int(state.get("iteration") or 0)
        if iteration >= int(state.get("max_iterations") or 0):
            state["exit_reason"] = "max_iterations"
            return state
        try:
            return await self._decision_node_impl(state, iteration)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            try:
                self.log.log(f"[react.v2] decision_node error: {exc}\n{tb}", level="ERROR")
            except Exception:
                pass
            raise

    async def _decision_node_impl(self, state: Dict[str, Any], iteration: int) -> Dict[str, Any]:
        announced_adapters = [
            a for a in (state.get("adapters") or [])
            if a.get("id") not in tools_insights.CODEGEN_ONLY_TOOL_IDS and not tools_insights.is_code_tool(a["id"])
        ]
        extra_adapters = [a for a in (state.get("adapters") or []) if tools_insights.is_code_tool(a["id"])]
        extra_adapters_for_decision = [
            a for a in extra_adapters if not tools_insights.is_codegen_tool(a["id"])
        ]

        await self._update_announce(
            iteration=iteration,
            max_iterations=int(state.get("max_iterations") or 0),
        )
        model_kind = state.get("next_decision_model") or "strong"
        role = f"{self.MODULE_AGENT_NAME}.{self.DECISION_AGENT_NAME}.{model_kind}"
        async with with_accounting(
            self.ctx_browser.runtime_ctx.bundle_id,
            agent=role,
            metadata={"agent": role},
        ):
            mainstream = self._mk_mainstream(f"decision ({iteration})")
            pending_tool_call_id = f"tc_{uuid.uuid4().hex[:12]}"
            exec_id = f"exec_{pending_tool_call_id}"
            exec_streamer_idx = self._next_tool_streamer_idx(
                pathlib.Path(state["outdir"]),
                "exec_tools.execute_code_python",
            )
            exec_streamer_fn, exec_streamer_widget = self._mk_exec_code_streamer(
                f"decision ({iteration})",
                exec_streamer_idx,
                execution_id=exec_id,
            )
            sources_list = []
            try:
                if self.ctx_browser:
                    sources_list = list(self.ctx_browser.sources_pool or [])
            except Exception:
                sources_list = []
        record_streamer_fns, record_streamers = self._mk_content_streamers(
            f"decision.record ({iteration})",
            sources_list=sources_list,
            artifact_name=f"react.record.{pending_tool_call_id}",
        )

        timeline_agent = f"{role}.timeline.{state.get('turn_id') or ''}.{iteration}"
        timeline_streamer_fn, timeline_streamer = self._mk_timeline_streamer(
            f"decision.timeline ({iteration})",
            sources_list=sources_list,
            agent=timeline_agent,
            notes_artifact_name=f"timeline_text.react.decision.{iteration}",
            final_answer_artifact_name=f"react.final_answer.{iteration}",
        )

        async def _hub_on_json(text: str, completed: bool = False, **_kwargs):
            for fn in record_streamer_fns:
                await fn(text, completed=completed)
            await timeline_streamer_fn(text, completed=completed)

        t0 = time.perf_counter()
        from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.agent_retry import retry_with_compaction
        from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.agents.decision import build_decision_system_text

        async def _decision_agent(*, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
            from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSubscribers
            subs = ChannelSubscribers().subscribe("ReactDecisionOutV2", _hub_on_json)
            if exec_streamer_widget is not None:
                subs = subs.subscribe("ReactDecisionOutV2", exec_streamer_widget.feed_json)
                subs = subs.subscribe("code", exec_streamer_widget.feed_code)
            return await react_decision_stream_v2(
                svc=self.svc,
                adapters=announced_adapters,
                infra_adapters=extra_adapters_for_decision,
                on_progress_delta=mainstream,
                subscribers=subs,
                agent_name=role,
                max_tokens=20000,
                user_blocks=blocks,
            )

        render_params = {
            "cache_last": True,
            "include_sources": True,
            "include_announce": True,
            "force_sanitize": bool(state.get("force_compaction_next_decision")),
        }
        decision = await retry_with_compaction(
            ctx_browser=self.ctx_browser,
            system_text_fn=lambda: build_decision_system_text(
                adapters=announced_adapters,
                infra_adapters=extra_adapters_for_decision,
            ),
            render_params=render_params,
            agent_fn=_decision_agent,
            emit_status=None,
        )
        # Reset forced compaction once we have a decision attempt.
        state["force_compaction_next_decision"] = False
        state["last_decision_raw"] = decision
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        self._append_react_timing(round_idx=iteration, stage="decision", elapsed_ms=elapsed_ms)
        logging_helpers.log_agent_packet(role, "react.decision.v2", decision)
        error = (decision.get("log") or {}).get("error")

        try:
            ReactRound.thinking(
                ctx_browser=self.ctx_browser,
                decision=decision,
                title=f"solver.react.v2.decision ({iteration})",
                iteration=iteration,
            )
        except Exception:
            pass

        notes = None
        action = None
        tool_id = ""
        protocol_entry = None

        if error:
            try:
                self.ctx_browser.contribute_notice(
                    code="protocol_violation.ReactDecisionOutV2_schema_error",
                    message=self._protocol_violation_message(
                        code="ReactDecisionOutV2_schema_error",
                        error=error,
                        state=state,
                        decision={}
                    )
                )
                self.log.log(f"[react.v2] decision schema error: {error}", level="ERROR")
            except Exception:
                pass
            try:
                ReactRound.decision_raw(
                    ctx_browser=self.ctx_browser,
                    decision=decision,
                    iteration=iteration,
                )
            except Exception:
                pass
            retries = int(state.get("decision_retries") or 0)
            if retries < int(state.get("max_iterations") or 0):
                state["decision_retries"] = retries + 1
                state["retry_decision"] = True
                decision["notes"] = "ReactDecisionOutV2_schema_error; retry decision"
                try:
                    ReactRound.decision_raw(
                        ctx_browser=self.ctx_browser,
                        decision=decision,
                        iteration=iteration,
                        reason="schema_error",
                    )
                except Exception:
                    pass
                try:
                    self.log.log(
                        f"[react.v2] retry decision after schema error (retries={state['decision_retries']})",
                        level="INFO",
                    )
                except Exception:
                    pass
            else:
                decision = {
                    "action": "exit",
                    "final_answer": "ReactDecisionOutV2_schema_error validation failed.",
                    "notes": "ReactDecisionOutV2_schema_error",
                }
                action = "exit"
                tool_call = {}
                tool_id = ""
        else:

            decision = decision.get("agent_response") or {}
            if not isinstance(decision, dict):
                decision = {}

            original_action = (decision.get("action") or "").strip()
            has_final_answer = bool((decision.get("final_answer") or "").strip())
            if original_action == "call_tool" and has_final_answer:
                try:
                    if self.ctx_browser:
                        self.ctx_browser.contribute_notice(
                            code="protocol_violation.final_answer_with_tool_call",
                            message=self._protocol_violation_message(
                                code="final_answer_with_tool_call",
                                decision={**decision, "action": "call_tool"},
                                state=state,
                            ),
                            extra={"action": "call_tool"},
                        )
                except Exception:
                    pass
                try:
                    self.log.log(
                        f"[react.v2] final_answer present with call_tool; coercing to complete",
                        level="ERROR",
                    )
                except Exception:
                    pass
                decision["action"] = "complete"
                decision["tool_call"] = None

            validation_error = self._validate_decision(decision)
            if validation_error:
                if validation_error.startswith("invalid_action"):
                    invalid_retries = int(state.get("invalid_action_retries") or 0) + 1
                    state["invalid_action_retries"] = invalid_retries
                    # If we see invalid_action repeatedly, force compaction on next decision.
                    if invalid_retries >= 2:
                        state["force_compaction_next_decision"] = True
                try:
                    self.ctx_browser.contribute_notice(
                        code=f"protocol_violation.{validation_error}",
                        message=self._protocol_violation_message(
                            code=validation_error,
                            decision=decision,
                            state=state,
                        ),
                        extra={"validation_error": validation_error},
                    )
                except Exception:
                    pass
                try:
                    self.log.log(
                        f"[react.v2] decision validation failed: {validation_error} | decision={self._short_json(decision)}",
                        level="ERROR",
                    )
                except Exception:
                    pass
                retries = int(state.get("decision_retries") or 0)
                if retries < int(state.get("max_iterations") or 0):
                    state["decision_retries"] = retries + 1
                    state["retry_decision"] = True
                    decision = {"action": "call_tool", "notes": f"{validation_error}; retry decision"}
                    try:
                        self.log.log(
                            f"[react.v2] retry decision after validation error (retries={state['decision_retries']})",
                            level="INFO",
                        )
                    except Exception:
                        pass
                    state["session_log"].append({
                        "type": "decision_invalid",
                        "iteration": iteration,
                        "timestamp": time.time(),
                        "error": validation_error,
                    })
                    state["last_decision"] = decision
                    return state
                else:
                    decision = {"action": "exit", "final_answer": "Decision validation failed."}
            else:
                state["invalid_action_retries"] = 0
                state["force_compaction_next_decision"] = False
                # Clear any pending retry flags on a valid decision.
                state["retry_decision"] = False
            action = (decision.get("action") or "").strip()
            notes = (decision.get("notes") or "").strip()
            tool_call = decision.get("tool_call") or {}
            tool_id = (tool_call.get("tool_id") or "").strip()
            if action == "complete" and (decision.get("final_answer") or "").strip():
                state["retry_decision"] = False

            plan_steps = state.get("plan_steps") or []
            if not plan_steps and self.ctx_browser:
                try:
                    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.plan import collect_plan_snapshots, PlanSnapshot
                    plans_by_id, order = collect_plan_snapshots(self.ctx_browser.timeline.blocks)
                    if order:
                        snap = PlanSnapshot.from_any(plans_by_id.get(order[-1]) or {})
                        if snap and snap.steps:
                            plan_steps = list(snap.steps)
                            state["plan_steps"] = plan_steps
                except Exception:
                    pass
            try:
                status_map = state.get("plan_status") or {}
                status_map, plan_blocks = apply_plan_updates(
                    notes=notes,
                    plan_steps=plan_steps,
                    status_map=status_map if isinstance(status_map, dict) else {},
                    timeline_blocks=self.ctx_browser.timeline.blocks if self.ctx_browser else [],
                    turn_id=state.get("turn_id") or "",
                    iteration=iteration,
                    ts=time.time(),
                )
                if status_map:
                    state["plan_status"] = status_map
                if plan_blocks and self.ctx_browser:
                    self.ctx_browser.contribute(blocks=plan_blocks)
            except Exception:
                pass

            exec_only_ids = {"ctx_tools.fetch_ctx"}
            if action == "call_tool" and tool_id in exec_only_ids:
                try:
                    self.ctx_browser.contribute_notice(
                        code="protocol_violation.tool_not_allowed_in_react",
                        message=self._protocol_violation_message(
                            code="tool_not_allowed_in_react",
                            decision=decision,
                            state=state,
                            extra={"tool_id": tool_id},
                        ),
                        extra={"tool_id": tool_id, "iteration": iteration},
                        meta={"rel": "call"},
                    )
                except Exception:
                    pass
                try:
                    self.log.log(f"[react.v2] tool_not_allowed_in_react: {tool_id}", level="ERROR")
                except Exception:
                    pass
                retries = int(state.get("decision_retries") or 0)
                if retries < int(state.get("max_iterations") or 0):
                    state["decision_retries"] = retries + 1
                    state["retry_decision"] = True
                    decision["notes"] = "tool_not_allowed_in_react; retry decision"
                    action = "call_tool"
                    try:
                        self.log.log(
                            f"[react.v2] retry decision after tool_not_allowed (retries={state['decision_retries']})",
                            level="INFO",
                        )
                    except Exception:
                        pass
                else:
                    decision = {
                        "action": "exit",
                        "final_answer": "Tool not allowed in react loop.",
                        "notes": "tool_not_allowed_in_react",
                    }
                    action = "exit"
            # Plan acknowledgements are informative only; do not block completion.
            if action == "call_tool":
                adapters_by_id = self._adapters_index(state.get("adapters") or [])
                verdict = self._validate_tool_call_protocol(
                    tool_call=tool_call,
                    adapters_by_id=adapters_by_id,
                )
                protocol_entry = {
                    "type": "protocol_verify",
                    "iteration": iteration,
                    "timestamp": time.time(),
                    "tool_id": verdict.get("tool_id"),
                    "ok": bool(verdict.get("ok")),
                    "violations": verdict.get("violations") or [],
                }
                if verdict.get("ok"):
                    state["session_log"].append(protocol_entry)
                else:
                    try:
                        self.ctx_browser.contribute_notice(
                            code="protocol_violation.tool_call_invalid",
                            message=self._protocol_violation_message(
                                code="tool_call_invalid",
                                decision=decision,
                                state=state,
                            ),
                            extra={
                                "violations": verdict.get("violations") or [],
                                "tool_id": verdict.get("tool_id"),
                                "iteration": iteration,
                            },
                            meta={"rel": "call"},
                        )
                    except Exception:
                        pass
                    try:
                        self.log.log(
                            f"[react.v2] tool_call_invalid: {verdict.get('violations')} | decision={self._short_json(decision)}",
                            level="ERROR",
                        )
                    except Exception:
                        pass
                    state["session_log"].append(protocol_entry)
                    retries = int(state.get("decision_retries") or 0)
                    if retries < int(state.get("max_iterations") or 0):
                        state["decision_retries"] = retries + 1
                        state["retry_decision"] = True
                        decision["notes"] = "tool_call_invalid; retry decision"
                        try:
                            self.log.log(
                                f"[react.v2] retry decision after tool_call_invalid (retries={state['decision_retries']})",
                                level="INFO",
                            )
                        except Exception:
                            pass
                    else:
                        decision = {
                            "action": "exit",
                            "final_answer": "Tool call validation failed.",
                            "notes": "tool_call_invalid",
                        }
                        action = "exit"
                        tool_call = {}
                        tool_id = ""

            # ---- tool signature validation (filter params + classify issues) ----
            sig_status = None
            sig_issues: List[Dict[str, Any]] = []
            filtered_params = tool_call.get("params") if isinstance(tool_call, dict) else {}
            try:
                if tool_id and not str(tool_id).startswith("react."):
                    tv = await self.tools_subsystem.validate_tool_params(tool_id=tool_id, params=filtered_params)
                    sig_status = tv.get("status")
                    sig_issues = tv.get("issues") or []
                    filtered_params = tv.get("params") or {}
                    if isinstance(tool_call, dict):
                        tool_call["params"] = filtered_params
                        decision["tool_call"] = tool_call
                        state["last_decision"] = decision
            except Exception:
                sig_status = None
                sig_issues = []


            if sig_status in ("yellow", "red"):
                try:
                    self.ctx_browser.contribute_notice(
                        code="tool_signature_validation",
                        message=f"tool_signature_validation={sig_status}",
                        extra={"iteration": iteration, "tool_id": tool_id, "status": sig_status, "issues": sig_issues},
                        meta={"rel": "call"},
                    )
                except Exception:
                    pass
                state["session_log"].append({
                    "type": "tool_signature_validation",
                    "iteration": iteration,
                    "timestamp": time.time(),
                    "tool_id": tool_id,
                    "status": sig_status,
                    "issues": sig_issues,
                })

            if sig_status == "red":
                try:
                    self.ctx_browser.contribute_notice(
                        code="protocol_violation.tool_signature_red",
                        message=self._protocol_violation_message(
                            code="tool_signature_red",
                            decision=decision,
                            state=state,
                        ),
                        extra={"violations": sig_issues, "tool_id": tool_id, "iteration": iteration},
                        meta={"rel": "call"},
                    )
                except Exception:
                    pass
                try:
                    self.log.log(
                        f"[react.v2] tool_signature_red: {sig_issues} | decision={self._short_json(decision)}",
                        level="ERROR",
                    )
                except Exception:
                    pass
                if protocol_entry is not None:
                    protocol_entry["ok"] = False
                    protocol_entry["violations"] = (protocol_entry.get("violations") or []) + (sig_issues or [])
                retries = int(state.get("decision_retries") or 0)
                if retries < int(state.get("max_iterations") or 0):
                    state["decision_retries"] = retries + 1
                    state["retry_decision"] = True
                    decision["notes"] = "tool_signature_red; retry decision"
                    try:
                        self.log.log(
                            f"[react.v2] retry decision after tool_signature_red (retries={state['decision_retries']})",
                            level="INFO",
                        )
                    except Exception:
                        pass
                else:
                    decision = {
                        "action": "exit",
                        "final_answer": "Tool signature validation failed.",
                        "notes": "tool_signature_red",
                    }
                    action = "exit"
                    tool_call = {}
                    tool_id = ""
            if notes:
                if tools_insights.is_exec_tool(tool_id) and exec_streamer_widget:
                    await exec_streamer_widget.emit_reasoning(notes)

        if not state.get("retry_decision") and action in {"complete", "exit"}:
            state["exit_reason"] = action
            state["final_answer"] = (decision.get("final_answer") or "").strip()
            state["suggested_followups"] = decision.get("suggested_followups") or []
            try:
                sf = state.get("suggested_followups") or []
                self.log.log(
                    f"[react.v2] decision followups: count={len(sf)}",
                    level="INFO",
                )
            except Exception:
                pass

        try:
            if notes:
                ReactRound.note(
                    ctx_browser=self.ctx_browser,
                    notes=notes,
                    tool_call_id=pending_tool_call_id,
                    tool_id=tool_id,
                    action=action,
                    iteration=iteration,
                )
        except Exception:
            pass
        await emit_event(
            comm=self.comm,
            etype="solver.react.decision",
            title=f"ReAct Round ({iteration}). Decision Node",
            step=f"react({iteration}).decision",
            data=decision if decision else {"error": "no_decision"},
            agent=f"solver.react({iteration}).decision",
        )
        try:
            self.scratchpad.register_agentic_response(
                f"solver.react.decision ({iteration})",
                decision,
            )
        except Exception as exc:
            self.log.log(f"[react.v2] register_agentic_response failed: {exc}", level="ERROR")

        state["exec_code_streamer"] = exec_streamer_widget
        state["record_streamers"] = record_streamers
        state["timeline_streamer"] = timeline_streamer
        state["pending_exec_id"] = exec_id
        state["pending_tool_call_id"] = pending_tool_call_id
        state["last_decision"] = decision
        state["iteration"] = iteration + 1
        bs = state.get("budget_state_v2")
        if bs is not None:
            try:
                bs.decision_rounds_used = int(state.get("iteration") or 0)
            except Exception:
                self.log.log(traceback.format_exc())
        return state

    async def _tool_execution_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state = await ReactRound.execute(react=self, state=state)
        pending_sources = state.pop("pending_sources", None)
        if pending_sources:
            try:
                self.log.log(
                    f"[react.v2] merge_sources: pending_sources={len(pending_sources or [])}",
                    level="INFO",
                )

                await self._merge_with_pool(
                    collections=[pending_sources],
                    workdir=pathlib.Path(state["workdir"]),
                    outdir=pathlib.Path(state["outdir"]),
                    record_streamers=state.get("record_streamers"),
                    timeline_streamer=state.get("timeline_streamer"),
                )
            except Exception:
                self.log.log(traceback.format_exc())
        return state

    async def _exit_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reason = state.get("exit_reason", "complete")
        state["session_log"].append({
            "type": "exit",
            "timestamp": time.time(),
            "reason": reason,
        })
        pre_blocks = []
        post_blocks = []

        exit_block = {
            "type": "react.exit",
            "author": "react",
            "turn_id": self.scratchpad.turn_id or "",
            "ts": time.time(),
            "mime": "application/json",
            "path": f"ar:{self.scratchpad.turn_id}.react.exit",
            "text": json.dumps({
                "reason": reason,
            }, ensure_ascii=False, indent=2),
        }

        # persist final turn stats to contrib log, then clear announce
        try:
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
            final_text = ""
            try:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_announce_text
                final_text = build_announce_text(
                    iteration=int(state.get("iteration") or 0),
                    max_iterations=int(state.get("max_iterations") or 0),
                    started_at=getattr(self.scratchpad, "started_at", "") or "",
                    timezone=getattr(runtime_ctx, "timezone", None) if runtime_ctx else None,
                    timeline_blocks=self.ctx_browser.timeline.blocks if self.ctx_browser else [],
                    constraints=None,
                    mode="turn_finalize",
                ).strip()
            except Exception:
                final_text = ""
            if final_text:
                pre_blocks.append({"text": final_text})
            if self.ctx_browser:
                self.ctx_browser.announce(blocks=None)
        except Exception:
            self.log.log(traceback.format_exc())
        # persist react_state snapshot as a contribution block
        try:
            if self.ctx_browser:
                react_state = ReactStateSnapshot.from_state(state)
                post_blocks.append({
                    "type": "react.state",
                    "author": "react",
                    "turn_id": self.ctx_browser.runtime_ctx.turn_id or "",
                    "ts": time.time(),
                    "mime": "application/json",
                    "path": f"ar:{self.ctx_browser.runtime_ctx.turn_id}.react.state",
                    "text": json.dumps(react_state.to_dict(), ensure_ascii=False, indent=2),
                })
        except Exception:
            self.log.log(traceback.format_exc())
        if exit_block:
            post_blocks.append(exit_block)
        try:
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
            if runtime_ctx is not None:
                if pre_blocks:
                    existing = getattr(runtime_ctx, "on_before_completion_contribution", None)
                    def _hook_pre(blocks=pre_blocks, prior=existing):
                        out = []
                        if callable(prior):
                            try:
                                out.extend(prior() or [])
                            except Exception:
                                self.log.log(traceback.format_exc())
                        out.extend(list(blocks))
                        blocks.clear()
                        return out
                    runtime_ctx.on_before_completion_contribution = _hook_pre
                if post_blocks:
                    existing = getattr(runtime_ctx, "on_after_completion_contribution", None)
                    def _hook_post(blocks=post_blocks, prior=existing):
                        out = []
                        if callable(prior):
                            try:
                                out.extend(prior() or [])
                            except Exception:
                                self.log.log(traceback.format_exc())
                        out.extend(list(blocks))
                        blocks.clear()
                        return out
                    runtime_ctx.on_after_completion_contribution = _hook_post
        except Exception as ex:
            self.log.log(f"[react.v2] completion_hooks: {ex}", level="ERROR")

        # Emit citations used in this turn (files already emitted on host)
        try:
            if self.hosting_service and self.ctx_browser and self.ctx_browser.timeline:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import extract_sources_used_from_blocks
                blocks = self.ctx_browser.timeline.get_turn_blocks()
                used_sids = extract_sources_used_from_blocks(blocks)
                try:
                    answer_sids = citations_module.extract_citation_sids_any(state.get("final_answer") or "")
                except Exception:
                    answer_sids = []
                if answer_sids:
                    for sid in answer_sids:
                        if sid not in used_sids:
                            used_sids.append(sid)
                if used_sids:
                    sid_set = set(used_sids)
                    citations = []
                    pool = list(self.ctx_browser.timeline.sources_pool or [])
                    pool_updated = False
                    for row in pool:
                        if not isinstance(row, dict):
                            continue
                        if row.get("sid") in sid_set:
                            if row.get("used") is not True:
                                row["used"] = True
                                pool_updated = True
                            # strip heavy fields
                            clean = {k: v for k, v in row.items() if k not in ("content", "base64")}
                            citations.append(clean)
                    if pool_updated:
                        try:
                            self.ctx_browser.set_sources_pool(sources_pool=pool)
                        except Exception:
                            self.log.log(traceback.format_exc())
                    self.log.log(
                        f"[react.v2] emit_citations: used_sids={sorted(sid_set)} "
                        f"pool={len(pool)} citations={len(citations)}",
                        level="INFO",
                    )
                    await self.hosting_service.emit_solver_artifacts(files=[], citations=citations)
                else:
                    self.log.log("[react.v2] emit_citations: no used_sids detected", level="INFO")
        except Exception:
            self.log.log(traceback.format_exc())
        return state
    def _adapters_index(self, adapters: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        idx: Dict[str, Dict[str, Any]] = {}
        for a in adapters or []:
            tid = (a.get("id") or "").strip()
            if tid:
                idx[tid] = a
        return idx

    def _append_react_timing(
            self,
            *,
            round_idx: int,
            stage: str,
            elapsed_ms: Optional[int],
            tool_id: Optional[str] = None,
            artifact_id: Optional[str] = None,
    ) -> None:
        if elapsed_ms is None:
            return
        if not getattr(self, "scratchpad", None):
            return
        timings = getattr(self.scratchpad, "timings", None)
        if not isinstance(timings, list):
            return
        title = f"react.{round_idx}.{stage}"
        if tool_id:
            title += f".{tool_id}"
        if artifact_id:
            title += f".{artifact_id}"
        timings.append({"title": title, "elapsed_ms": int(elapsed_ms)})

    async def _merge_with_pool(
        self,
        *,
        collections: List[List[Dict[str, Any]]],
        workdir: pathlib.Path,
        outdir: pathlib.Path,
        record_streamers: Optional[List[Any]] = None,
        timeline_streamer: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:

        params = {
            "source_collections": [list(self.ctx_browser.sources_pool or [])] + [c or [] for c in collections]
        }
        try:
            self.log.log(
                f"[react.v2] merge_sources: existing={len(self.ctx_browser.sources_pool or [])} "
                f"collections={[len(c or []) for c in collections]}",
                level="INFO",
            )
        except Exception:
            pass
        tr = await execute_tool(
            runtime_ctx=self.ctx_browser.runtime_ctx,
            tool_execution_context={"tool_id": "ctx_tools.merge_sources", "params": params},
            workdir=workdir,
            outdir=outdir,
            tool_manager=self.tools_subsystem,
            logger=self.log,
        )
        merged = tr.get("output")
        if not isinstance(merged, list):
            items = tr.get("items") or []
            first_item = next(iter(items), None)
            merged = first_item.get("output") if isinstance(first_item, dict) else None
        if not isinstance(merged, list):
            merged = []
        if merged:
            turn_id = self.ctx_browser.runtime_ctx.turn_id if self.ctx_browser else ""
            if turn_id:
                for row in merged:
                    if isinstance(row, dict) and not row.get("turn_id"):
                        row["turn_id"] = turn_id

        if not merged and collections:
            self.log.log(
                f"[react.merge_sources] empty merge result; "
                f"inputs={[len(c or []) for c in collections]} "
                f"raw_type={type(merged).__name__}",
                level="WARNING",
            )
        else:
            self.ctx_browser.set_sources_pool(sources_pool=merged)
            try:
                self.log.log(
                    f"[react.v2] merge_sources: merged={len(merged)}",
                    level="INFO",
                )
            except Exception:
                pass
        if merged:
            try:
                mx = max(int(s.get("sid") or 0) for s in merged if isinstance(s, dict))
                try:
                    from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
                    try:
                        next_sid = int(mx) + 1
                    except (TypeError, ValueError):
                        next_sid = 0
                    SOURCE_ID_CV.set({"next": next_sid})
                except Exception:
                    pass
                try:
                    for rs in (record_streamers or []):
                        if rs and hasattr(rs, "update_sources"):
                            rs.update_sources(self.ctx_browser.sources_pool if self.ctx_browser else [])
                except Exception:
                    pass
                try:
                    if timeline_streamer and hasattr(timeline_streamer, "update_sources"):
                        timeline_streamer.update_sources(self.ctx_browser.sources_pool if self.ctx_browser else [])
                except Exception:
                    pass
            except Exception:
                pass
        return merged

    async def _analyze_and_build_result(self, state: Dict[str, Any])-> ReactResult:

        outdir = pathlib.Path(state["outdir"])
        workdir = pathlib.Path(state["workdir"])
        outdir.mkdir(parents=True, exist_ok=True)

        sources_pool = [s for s in (self.ctx_browser.sources_pool or []) if isinstance(s, dict)]
        sources_pool.sort(key=lambda s: int(s.get("sid") or 0))

        artifacts_out: List[Dict[str, Any]] = []
        try:
            if self.scratchpad is not None:
                react_state = ReactStateSnapshot.from_state(state)
                self.scratchpad.react_state = react_state.to_dict()
        except Exception:
            pass

        react_result = ReactResult(
            ok=True,
            out=artifacts_out,
            sources_pool=sources_pool,
            final_answer=(state.get("final_answer") or "").strip(),
            suggested_followups=state.get("suggested_followups") or [],
            error=state.get("error"),
            round_timings=state.get("round_timings") or [],
            total_runtime_sec=float(state.get("total_runtime_sec") or 0.0),
            run_id=state.get("session_id") or "",
            outdir=str(outdir),
            workdir=str(workdir),
        )

        (outdir / "result.json").write_text(
            json.dumps(react_result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return react_result
