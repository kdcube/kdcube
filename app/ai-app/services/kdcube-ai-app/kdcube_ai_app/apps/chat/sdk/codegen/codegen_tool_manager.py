# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/codegen/codegen_tool_manager.py

import os
import sys
import traceback
import uuid

from typing import Callable, Dict, Any, Awaitable, Optional, List, Tuple
import pathlib
import importlib.util
import importlib
import inspect
import time
import json
import asyncio

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.codegen.contracts import ToolModuleSpec, SolutionPlan, PlannedTool, SolveResult, \
    SolutionExecution
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
import kdcube_ai_app.apps.chat.sdk.codegen.project_retrieval as project_retrieval
from kdcube_ai_app.apps.chat.sdk.inventory import ModelServiceBase, AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad
from kdcube_ai_app.apps.chat.sdk.runtime.simple_runtime import _InProcessRuntime
from kdcube_ai_app.apps.chat.sdk.codegen.team import (
    tool_router_stream,
    assess_solvability_stream,
    solver_codegen_stream,
    _today_str,
)
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers

def _rid(prefix: str = "r") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _here(*parts: str) -> pathlib.Path:
    """Path relative to this file (workflow.py)."""
    return pathlib.Path(__file__).resolve().parent.joinpath(*parts)

def _module_to_file(module_name: str) -> pathlib.Path:
    """
    Resolve a dotted module to a concrete .py file path.
    Works for single-file modules and packages (returns __init__.py).
    """
    spec = importlib.util.find_spec(module_name)
    if not spec or not spec.origin:
        raise ImportError(f"Cannot resolve module '{module_name}' to a file (no spec.origin).")
    return pathlib.Path(spec.origin).resolve()

def _resolve_tools(specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize mixed 'module' or 'ref' specs to the form expected by CodegenToolManager:
      {"ref": "/abs/path/to/file.py", "alias": "...", "use_sk": True}
    """
    resolved = []
    for s in specs:
        alias = s["alias"]
        use_sk = bool(s.get("use_sk", True))
        if "module" in s:
            file_path = _module_to_file(s["module"])
        elif "ref" in s:
            file_path = pathlib.Path(s["ref"]).resolve()
        else:
            raise ValueError(f"Tool spec for alias={alias} must have 'module' or 'ref'.")
        resolved.append({"ref": str(file_path), "alias": alias, "use_sk": use_sk})
    return resolved

# ---------- Manager ----------

class CodegenToolManager:
    AGENT_NAME = "codegen_tool_manager"

    def __init__(
        self,
        *,
        service: ModelServiceBase,
        comm: ChatCommunicator,
        logger: Optional[AgentLogger] = None,
        emit: Callable[[Dict[str, Any]], Awaitable[None]],
        registry: Optional[Dict[str, Any]] = None,
        context_rag_client: Optional[ContextRAGClient] = None,
        tools_specs: Optional[List[Dict[str, Any]]] = None, # list of {ref, use_sk, alias}
    ):
        tools_modules = _resolve_tools(tools_specs or [])
        self.svc = service
        self.comm = comm
        self.context_rag_client = context_rag_client
        self.log = logger or AgentLogger("tool_manager")
        self.emit = emit
        self.registry = registry or {}
        self.runtime = _InProcessRuntime(self.log)

        # Normalize module specs
        specs: List[ToolModuleSpec] = []

        if tools_modules:
            for m in tools_modules:
                specs.append(ToolModuleSpec(
                    ref=m.get("ref"),
                    use_sk=bool(m.get("use_sk", False)),
                    alias=m.get("alias")
                ))

        # Load & introspect all modules
        self._modules: List[Dict[str, Any]] = []     # [{name, mod, alias, use_sk}]
        self.tools_info: List[Dict[str, Any]] = []   # flattened entries across modules

        used_aliases: set[str] = set()

        for spec in specs:
            mod_name, mod = self._load_tools_module(spec.ref)
            alias = spec.alias or pathlib.Path(mod_name).name
            # keep alias unique
            base_alias = alias
            i = 1
            while alias in used_aliases:
                alias = f"{base_alias}{i}"
                i += 1
            used_aliases.add(alias)

            # Bind service if the module wants it
            try:
                if hasattr(mod, "bind_service"):
                    mod.bind_service(self.svc)
            except Exception:
                pass
            try:
                if hasattr(mod, "bind_registry"):
                    mod.bind_registry(self.registry)
            except Exception:
                pass
            try:
                if hasattr(mod, "bind_integrations"):
                    mod.bind_integrations({ "ctx_client": self.context_rag_client })
            except Exception:
                pass

            self._modules.append({"name": mod_name, "mod": mod, "alias": alias, "use_sk": spec.use_sk})
            self.tools_info.extend(self._introspect_module(mod, mod_name, alias, spec.use_sk))

        self._by_id = {e["id"]: e for e in self.tools_info}              # qualified id -> entry
        self._mods_by_alias = {m["alias"]: m for m in self._modules}     # alias -> {name,mod,alias,use_sk}

    # -------- module loading --------
    def _load_tools_module(self, ref: str) -> Tuple[str, object]:
        if not ref:
            raise RuntimeError("tools_module ref is required")
        # file path (abs/rel) OR dotted import
        if ref.endswith(".py") or os.path.sep in ref:
            p = pathlib.Path(ref)
            if not p.is_absolute():
                p = (pathlib.Path.cwd() / p).resolve()
            if not p.exists():
                raise RuntimeError(f"Tools module not found: {ref} -> {p}")
            mod_name = p.stem
            spec = importlib.util.spec_from_file_location(mod_name, str(p))
            if not spec or not spec.loader:
                raise RuntimeError(f"Cannot load tools module from path: {p}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)  # type: ignore
            return mod_name, mod
        # dotted path
        mod = importlib.import_module(ref)
        return mod.__name__, mod

    # -------- module introspection --------
    def _introspect_module(self, mod, mod_name: str, alias: str, use_sk: bool) -> List[Dict[str, Any]]:
        """
        Returns entries with qualified ids and alias-based import/call:
          {
            "id": "<alias>.<fn>",
            "import": f"from {mod_name} import tools as {alias}",
            "call_template": f"{alias}.{fn}({k=v,...})",
            "doc": {purpose, args, returns, constraints, examples},
            "raw": {...}  # optional raw metadata
          }
        """
        if use_sk and hasattr(mod, "kernel"):
            return self._introspect_via_semantic_kernel(mod, mod_name, alias)

        # Prefer list_tools() if present (non-SK)
        if hasattr(mod, "list_tools"):
            reg = mod.list_tools()  # {fn_name: {callable, description, signature?}}
            entries: List[Dict[str, Any]] = []
            for fn_name, meta in reg.items():
                fn = meta.get("callable") or getattr(getattr(mod, "tools", mod), fn_name, None)
                desc = meta.get("description") or getattr(fn, "description", "") or (getattr(fn, "__doc__", "") or "")
                params = self._sig_to_params(fn)
                import_stmt = f"from {mod_name} import tools as {alias}"
                call_template = self._make_call_template(alias, fn_name, params)
                ret_annot = (
                    str(meta.get("return_annotation")) if isinstance(meta, dict) and meta.get("return_annotation") is not None
                    else self._annot_from_sig_return(fn)
                )
                entries.append(self._mk_entry(
                    alias, fn_name, import_stmt, call_template, desc, params,
                    raw=meta, is_async=asyncio.iscoroutinefunction(fn), return_annotation=ret_annot
                ))
            return entries

        # Fallback: reflect on 'tools' or module
        owner = getattr(mod, "tools", mod)
        import_stmt = f"from {mod_name} import tools as {alias}" if hasattr(mod, "tools") else f"import {mod_name} as {alias}"
        entries: List[Dict[str, Any]] = []
        for name in dir(owner):
            if name.startswith("_"):
                continue
            fn = getattr(owner, name, None)
            if not callable(fn):
                continue
            params = self._sig_to_params(fn)
            desc = getattr(fn, "description", "") or (getattr(fn, "__doc__", "") or "")
            call_template = self._make_call_template(alias, name, params)
            is_async = asyncio.iscoroutinefunction(fn)
            ret_annot = self._annot_from_sig_return(fn)
            entries.append(self._mk_entry(
                alias, name, import_stmt, call_template, desc, params,
                raw=None, is_async=is_async, return_annotation=ret_annot
            ))
        return entries

    def _introspect_via_semantic_kernel(self, mod, mod_name: str, alias: str) -> List[Dict[str, Any]]:
        kernel = getattr(mod, "kernel")
        # get list of function metadata; normalize to dicts
        metas = getattr(kernel, "get_full_list_of_function_metadata")()
        dict_metas: List[Dict[str, Any]] = []
        for m in metas:
            if hasattr(m, "model_dump"):
                dict_metas.append(m.model_dump())
            elif hasattr(m, "to_dict"):
                dict_metas.append(m.to_dict())
            elif isinstance(m, dict):
                dict_metas.append(m)
            else:
                # last resort, try vars()
                dict_metas.append(vars(m))

        entries: List[Dict[str, Any]] = []
        import_stmt = f"from {mod_name} import tools as {alias}"

        for fm in dict_metas:
            fn_name = fm.get("name")
            if not fn_name:
                continue
            desc = fm.get("description", "")
            plugin = fm.get("plugin_name") or ""
            params_meta = fm.get("parameters", []) or []
            params = []
            for p in params_meta:
                pname = p.get("name")
                if not pname or pname == "self":
                    continue
                default = p.get("default_value", None)
                annot = ""
                schema = p.get("schema_data") or {}
                # keep whatever SK provides (type, description, maybe min/max)
                if schema:
                    t = schema.get("type")
                    d = schema.get("description")
                    annot = ", ".join([s for s in [str(t) if t else "", str(d) if d else ""] if s]).strip(", ")
                params.append({
                    "name": pname,
                    "annotation": annot,
                    "default": default,
                    "kind": "POSITIONAL_OR_KEYWORD",
                })

            call_template = self._make_call_template(alias, fn_name, params)
            is_async = bool(fm.get("is_asynchronous"))
            ret_annot = self._annot_from_sk_return(fm)
            entry = self._mk_entry(
                alias, fn_name, import_stmt, call_template, desc, params,
                raw=fm, is_async=is_async, return_annotation=ret_annot
            )
            entry["plugin"] = plugin                       # <-- keep plugin on the entry
            entry["plugin_alias"] = alias
            entries.append(entry)
        return entries

    def _sig_to_params(self, fn) -> List[Dict[str, Any]]:
        out = []
        try:
            sig = inspect.signature(fn)
        except Exception:
            sig = None
        if not sig:
            return out
        for p in sig.parameters.values():
            if p.name == "self":
                continue
            out.append({
                "name": p.name,
                "annotation": str(p.annotation) if p.annotation is not inspect._empty else "",
                "default": None if p.default is inspect._empty else p.default,
                "kind": str(p.kind),
            })
        return out

    def _annot_from_sig_return(self, fn) -> str:
        try:
            sig = inspect.signature(fn)
            ra = sig.return_annotation
            if ra is inspect._empty:
                return ""
            # normalize typing annotations to string
            return str(ra)
        except Exception:
            return ""

    def _annot_from_sk_return(self, fm: Dict[str, Any]) -> str:
        """
        fm: SK function metadata dict. Looks like:
          {
            "return_parameter": {
              "type_": "str",
              "description": "...",
              "schema_data": {"type": "string", "description": "..."}
            },
            ...
          }
        Returns a concise string like "string — Markdown summary (string)" when available.
        """
        rp = (fm or {}).get("return_parameter") or {}
        if not isinstance(rp, dict):
            return ""
        # prefer schema_data
        schema = rp.get("schema_data") or {}
        t = schema.get("type") or rp.get("type_") or ""
        d = rp.get("description") or schema.get("description") or ""
        parts = []
        if t:
            parts.append(str(t))
        if d:
            parts.append(str(d))
        return " — ".join(parts) if parts else ""

    def _make_call_template(self, alias: str, fn_name: str, params: List[Dict[str, Any]]) -> str:
        if params:
            kw = ", ".join([f"{p['name']}={{${p['name']}$}}" for p in params])
            return f"{alias}.{fn_name}({kw})"
        return f"{alias}.{fn_name}()"

    def _mk_entry(
            self,
            alias: str,
            fn_name: str,
            import_stmt: str,
            call_template: str,
            desc: str,
            params: List[Dict[str, Any]],
            raw: Optional[Dict[str, Any]] = None,
            is_async: bool = False,
            return_annotation: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Doc surface for LLM
        args_doc = {}
        for p in params:
            type_hint = (p.get("annotation") or "any")
            if p.get("default") not in (None, inspect._empty):
                type_hint += f" (default={p['default']})"
            args_doc[p["name"]] = type_hint
        returns_doc = (return_annotation or "").strip() or "str or JSON (tool-specific)"
        entry = {
            "id": f"{alias}.{fn_name}",     # QUALIFIED id
            "desc": desc.strip(),
            "params": params,
            "import": import_stmt,
            "call_template": call_template.replace("${","{").replace("}$","}"),
            "is_async": bool(is_async),
            "doc": {
                "purpose": desc.strip(),
                "args": args_doc,
                "returns": returns_doc,
                "constraints": [],
                "examples": [],
            },
            "raw": raw or {},
        }
        if "plugin" not in entry: entry["plugin"] = (raw or {}).get("plugin_name", "") or ""
        if "plugin_alias" not in entry: entry["plugin_alias"] = alias
        return entry

    # -------- catalogs / adapters --------

    def _filter_entries(self, allowed_plugins: Optional[List[str]] = None, allowed_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        ents = list(self.tools_info)
        system_tool = lambda e: (e.get("plugin_alias") or "") in ["io_tools"]
        if allowed_plugins:
            allow = set([p.strip() for p in allowed_plugins if p and str(p).strip()])
            ents = [e for e in ents if (e.get("plugin_alias") or "") in allow]
        if allowed_ids:
            allow_ids = set(allowed_ids)
            ents = [e for e in ents if system_tool(e) or e["id"] in allow_ids]
        return ents

    def tool_catalog_for_prompt(self, *, allowed_plugins: Optional[List[str]] = None, allowed_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        catalog = []
        for e in self._filter_entries(allowed_plugins, allowed_ids):
            catalog.append({"id": e["id"], "doc": {"purpose": e["doc"]["purpose"], "args": e["doc"]["args"], "returns": e["doc"]["returns"]}})
        return catalog

    def adapters_for_codegen(self, *, allowed_plugins: Optional[List[str]] = None, allowed_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:

        allowed_plugins = set(allowed_plugins) if allowed_plugins else set()
        allowed_plugins.add("io_tools")
        allowed_plugins.add("ctx_tools")
        allowed_plugins = list(allowed_plugins)

        return [{
            "id": e["id"],
            "import": e["import"],
            "call_template": e["call_template"].replace("${","{").replace("}$","}"),
            "is_async": bool(e.get("is_async")),
            "doc": e["doc"],
        } for e in self._filter_entries(allowed_plugins, allowed_ids)]

    # -------- router / solvability --------

    async def plan(self, *,
                     ctx: Dict[str, Any],
                     scratchpad: TurnScratchpad,
                     allowed_plugins: Optional[List[str]] = None,
                     allowed_ids: Optional[List[str]] = None,
                     ) -> Dict[str, Any]:
        rid = ctx.get("request_id") or "req-unknown"

        t0 = time.perf_counter()

        tr = await self._run_tool_router({**ctx}, allowed_plugins=allowed_plugins, allowed_ids=allowed_ids)
        t_router_ms = int((time.perf_counter() - t0) * 1000)
        await self._emit_event(rid, etype="tools.suggest",
                               title="Tool Candidates Generated",
                               step="candidates",
                               data=tr,
                               timing={"elapsed_ms": t_router_ms})
        sv_ret = {}
        sv = {}

        tr_error = tr.get("__service", {}).get("error")
        if not tr_error:
            tr_note = (f"[solver.tool_router]: Notes: {tr.get('notes') or ''}. Selected tools={tr.get('candidates') or []}.")
            scratchpad.tlog.note(tr_note)

            t1 = time.perf_counter()
            sv_ret = await self._run_solvability(ctx, tr.get("candidates") or [])
            sv = sv_ret.get("agent_response")

            t_solv_ms = int((time.perf_counter() - t1) * 1000)
            await self._emit_event(rid, etype="solver.plan", title="Solvability Decision",
                                   step="plan", data=sv, timing={"elapsed_ms": t_solv_ms})

        sv_error = sv_ret.get("__service", {}).get("error")
        plan = self._materialize_decision(tr, sv_ret)

        if plan.error:
            if tr_error:
                self.log.log(f"[Solver.Tool Selector]. Error: {tr_error}", level="ERROR")
                scratchpad.tlog.note((f"[solver.tool_router]: planning failed — tool selection error: {plan.tool_selector_error}\n"
                                      f"Tool selector reasoning was: {plan.tool_selector_internal_thinking}\n."
                                      f"Tool selector plan was: {plan.tool_selector_raw_data}. It failed."))
            if sv_error:
                scratchpad.tlog.note(f"[Solver.Solvability] ERROR during attempt to plan the solution: {plan.solvability_error}")
                scratchpad.tlog.note(f"[solver.solvability] User request is not solved, plan failed. "
                                     f"Solvability reasoning was: {plan.solvability_internal_thinking}.\n"
                                     f"Solvability plan was  {plan.solvability_raw_data}. It failed.")
        else:
            solvability_note = (f"[Solver.Solvability] decision: solving mode={plan.mode}, confidence={plan.confidence}, "
                                f"solvability_reasoning={plan.reasoning}, ")
            if plan.mode != "llm_only":
                solvability_note += (f"tools={[t.id for t in (plan.tools or [])]}, "
                                     f"When solved, these slots must be filled: contract_dyn={plan.contract_dyn}. If the slots are not filled, the user request is not solved.")
            solvability_note += f"instructions_for_downstream={plan.instructions_for_downstream}, "
            scratchpad.tlog.note(solvability_note)

        return {
            "plan": plan,
            "tr": tr,
            "sv": sv,
        }

    async def _run_tool_router(self,
                               ctx: Dict[str, Any],
                               *,
                               allowed_plugins: Optional[List[str]] = None,
                               allowed_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        out = await tool_router_stream(
            self.svc,
            ctx["text"],
            policy_summary=(ctx.get("policy_summary") or ""),
            context_hint=(ctx.get("context_hint") or ""),
            topic_hint=(ctx.get("topic_hint") or ""),
            prefs_hint=(ctx.get("prefs_hint") or {}),
            topics=ctx.get("topics") or [],
            tool_catalog=self.tool_catalog_for_prompt(allowed_plugins=allowed_plugins, allowed_ids=allowed_ids),  # <-- scoped
            on_thinking_delta=self._mk_thinking_streamer("tool router"),
            max_tokens=1500
        )
        logging_helpers.log_agent_packet(self.AGENT_NAME, "tool router", out)
        tr = out.get("agent_response") or {"candidates": [], "notes": ""}
        elog = out.setdefault("log", {})
        internal_thinking = out.get("internal_thinking")
        error = elog.get("error")
        __service = {
            "internal_thinking": internal_thinking,
            "raw_data": elog.get("raw_data")
        }
        if error:
            __service["error"] = error

        cands = []
        for c in (tr.get("candidates") or []):
            tool_id = c.get("name")  # EXPECTS qualified id, e.g., "agent_tools.web_search"
            info = next((e for e in self.tools_info if e["id"] == tool_id), None)
            params_schema = (info or {}).get("doc", {}).get("args", {})
            purpose = (info or {}).get("doc", {}).get("purpose", "")
            cands.append({
                "id": tool_id,
                "purpose": purpose,
                "reason": c.get("reason", ""),
                "params_schema": params_schema,
                "suggested_parameters": c.get("parameters") or {},
                "confidence": c.get("confidence", 0.0)
            })
        return {
            "candidates": cands, "notes": tr.get("notes", ""), "today": _today_str(),
            "__service": __service
        }

    async def _run_solvability(self, ctx: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        out = await assess_solvability_stream(
            self.svc,
            ctx["text"],
            candidates=[{
                "name": c["id"],  # still pass qualified ids
                "purpose": c.get("purpose", ""),
                "reason": c.get("reason", ""),
                "confidence": c.get("confidence", 0.0),
                "suggested_parameters": c.get("suggested_parameters", {}),
            } for c in candidates],
            policy_summary=(ctx.get("policy_summary") or ""),
            prefs_hint=(ctx.get("prefs_hint") or {}),
            # is_spec_domain=ctx.get("is_spec_domain"),
            topics=ctx.get("topics") or [],
            on_thinking_delta=self._mk_thinking_streamer("solvability"),
            max_tokens=2000,
        )
        logging_helpers.log_agent_packet(self.AGENT_NAME, "solvability", out)
        # return out.get("agent_response") or {"error": "no response from solvability"}
        return out

    def _materialize_decision(self,
                              tr: Dict[str, Any],
                              sv_ret: Dict[str, Any]) -> SolutionPlan:
        tr_service = tr.get("__service")
        sv_service = sv_ret.get("__service")

        tr_error = tr_service.get("error")
        sv_error = {"error": "no response from solvability"} if not sv_ret.get("agent_response") else None
        sv_error = sv_service.get("error") or sv_error

        sv = sv_ret.get("agent_response")

        cbyid = {c["id"]: c for c in (tr.get("candidates") or [])}
        tools: List[PlannedTool] = []
        if sv.get("solvable") and sv.get("tools_to_use"):
            for tid in sv.get("tools_to_use"):
                if tid in cbyid:
                    c = cbyid[tid]
                    tools.append(PlannedTool(
                        id=tid,
                        purpose=c.get("purpose", ""),
                        params=c.get("suggested_parameters", {}),
                        reason=c.get("reason", ""),
                        confidence=c.get("confidence", 0.0),
                    ))
        mode = sv.get("solver_mode") if tools else "llm_only"

        error = ""
        failure_presentation = {}
        if tr_error:
            error += (f"Solver.Tool Selector error. Thinking: {tr_service.get('internal_thinking')}\n"
                      f"Raw output: {tr_service.get('internal_thinking')}\n"
                      f"Error: {tr_service.get('error')}\n"
                      )
            failure_presentation["solver.tool_router"] = tr_service
        if sv_error:
            failure_presentation["solver.solvability"] = sv_service
            error += (f"Solver.Solvability error. Thinking: {sv_service.get('internal_thinking')}\n"
                      f"Raw output: {sv_service.get('internal_thinking')}\n"
                      f"Error: {sv_service.get('error')}\n"
                      )
        if error.strip() == "":
            error = None
            failure_presentation = None
        else:
            failure_presentation = {
                "markdown": error,
                "struct": failure_presentation
            }
        return SolutionPlan(
            mode=mode,                                   # of how to solve the problem
            tools=tools,                                 # subset of candidates with concrete params
            confidence=float(sv.get("confidence", 0.0)), # that the problem is solvable with the chosen tools
            reasoning=sv.get("reasoning", ""),           # solvability reasoning
            clarification_questions=list((sv.get("clarifying_questions") or [])),
            instructions_for_downstream=sv.get("instructions_for_downstream", ""),
            error=error,
            failure_presentation=failure_presentation,
            tool_router_notes=tr.get("notes", ""),       # notes of the tool router
            contract_dyn=sv.get("output_contract_dyn", {}),     # slot -> description. What products will be produced by solver if the problem is solved
            service={
                "tool_router": tr_service,
                "solvability": sv_service
            },
        )

    # -------- solution entry point --------

    async def solve(
            self,
            *,
            request_id: str,
            user_text: str,
            policy_summary: str = "",
            topic_hint: Optional[str] = None,
            topics: Optional[List[str]] = None,
            allowed_plugins: Optional[List[str]] = None,
            prefs_hint: Optional[dict] = None,
            extra_task_hint: Optional[Dict[str, Any]] = None,
            context_hint: str = "",
            materialize_turn_ids: Optional[List[str]] = None,
            scratchpad: TurnScratchpad = None
    ) -> SolveResult:
        """
        Orchestrate routing/solvability and, when in 'codegen' mode, return a clean normalized envelope:
          {
            mode: "codegen" | "direct_tools_exec" | "llm_only",
            decision: {...},
            contract_dyn: {...},              # dynamic contract (if any)
            out: [result.json 'out' items],   # raw out items (not rehosted)
            deliverables: {slot: {description, value:[...] } },
            citations: [...],
            calls: [...],                     # grouped out-items by (tool_id, input)
            codegen: {...},                   # full codegen envelope (rounds etc.)
            execution_id: "<from result.json or run_id>"
          }
        """
        topics = topics or []
        # Build targeted program history only from the materialized turns the classifier chose
        program_history = []
        if materialize_turn_ids:
            try:
                # 1) Build program history (you already do this)
                program_history = await project_retrieval._build_program_history_from_turn_ids(self, turn_ids=materialize_turn_ids, scope="track", days=365)

            except Exception:
                program_history = []

        history_hint = project_retrieval._history_digest(program_history, limit=3)
        context_hint = (context_hint or "")
        context_hint = (
            f"{context_hint}\n"
            f"Relevant prior runs (selected by classifier): {history_hint}. "
            f"They are available under OUTPUT_DIR/context.json → program_history[]."
        ).strip()

        result: Dict[str, Any] = {
            "plan": None,
            "execution": None,
        }

        # 1) Router + Solvability (scoped)
        tm_out = await self.plan(
            ctx={
                "request_id": request_id,
                "text": user_text,
                "topics": topics,
                "policy_summary": policy_summary,
                "context_hint": context_hint,
                "topic_hint": topic_hint or ", ".join((topics or [])),
                "prefs_hint": prefs_hint or {},
            },
            allowed_plugins=allowed_plugins,
            scratchpad=scratchpad
        )
        plan: SolutionPlan = tm_out.get("plan")
        result["plan"] = plan

        if plan.error:
            return SolveResult(result)

        chosen = [t.id for t in (plan.tools or [])]
        mode = plan.mode or ("direct_tools_exec" if chosen else "llm_only")

        # ---- direct execution (simple, one tool typical) ----
        if mode == "direct_tools_exec":
            steps = [{
                "tool": chosen[0],
                "args": (plan.tools[0].params or {}),
                "save_as": chosen[0]
            }]
            exec_res = await self.execute_plan(steps, allowed_plugins=allowed_plugins)
            result["exec"] = exec_res
            result["out"] = exec_res.get("out") or []
            result["calls"] = exec_res.get("calls") or []
            result["result_interpretation_instruction"] = (
                f"Artifacts shown under the 'Context — not authored by the user' block were produced automatically "
                f"by executing {chosen[0]}. Treat them as system-provided context for this turn; cite any URLs within."
            )
            result["execution_id"] = None
            # tlog for direct exec
            try:
                scratchpad.tlog.solver(f"[solver.execution] mode=direct_tools_exec tool={chosen[0]} steps={len(exec_res.get('steps') or [])}")
            except Exception:
                pass
            return SolveResult(result)

        # ---- codegen flow ----
        if mode == "codegen":
            # always include IO utils so codegen can persist files/JSON
            support_ids = [e["id"] for e in self.tools_info if (e.get("plugin_alias") or "") in ["io_tools", "ctx_tools"]]
            adapters = self.adapters_for_codegen(
                allowed_plugins=allowed_plugins,
                allowed_ids=list(set(chosen) | set(support_ids)),
            )
            cg_res = await self.run_code_gen(
                request_id=request_id,
                user_text=user_text,
                adapters=adapters,
                solvability=tm_out.get("sv"),
                policy_summary=policy_summary,
                topics=topics,
                prefs_hint=prefs_hint or {},
                extra_task_hint=extra_task_hint,
                constraints={"prefer_direct_tools_exec": True, "minimize_logic": True, "concise": True, "line_budget": 80},
                max_rounds=1,
                program_history=program_history,
            )

            # Extract solver JSON and derived blocks
            rounds = cg_res.get("rounds") or []
            solver_json = self._extract_solver_json_from_round(rounds[0]) if rounds else {}
            out_items: List[Dict[str, Any]] = (solver_json or {}).get("out") or []

            # Deliverables: STRICTLY ONE artifact per contract slot
            contract_keys = set(plan.contract_dyn.keys()) if isinstance(plan.contract_dyn, dict) else set()

            by_slot_single: Dict[str, Dict[str, Any]] = {}
            for art in out_items:
                rid = str(art.get("resource_id") or "")
                if not rid.startswith("slot:"):
                    continue
                slot_name = rid.split(":", 1)[1]  # after 'slot:' prefix
                # last write wins if duplicates (shouldn't happen)
                by_slot_single[slot_name] = art

            deliverables = {
                k: {"description": plan.contract_dyn[k], "value": by_slot_single.get(k)}
                for k in contract_keys
            }
            # Citations: any citable inline out item
            citations = []
            for a in out_items:
                if a.get("type") == "inline" and bool(a.get("citable")):
                    citations.append(a)

            execution = SolutionExecution(
                deliverables=deliverables,
                citations=citations,
                calls=self._group_calls_sequential(out_items)
            )
            result["codegen"] = cg_res
            result["execution"] = execution
            sr = SolveResult(result)

            # PROJECT LOG → tlog
            pl = (deliverables or {}).get("project_log") or {}
            v = pl.get("value")
            if isinstance(v, dict):
                pl_text = v.get("value") or v.get("text") or ""
            elif isinstance(v, str):
                pl_text = v
            else:
                pl_text = ""

            # ---- tlog summary for downstream answer agent
            try:
                ok = bool((solver_json or {}).get("ok"))
                contract = plan.contract_dyn or {}
                filled = sorted([k for k,v in (deliverables or {}).items() if isinstance(v.get("value"), dict)])
                missing = sorted(list(set(contract.keys()) - set(filled)))
                status = "ok" if ok else "failed"
                scratchpad.tlog.solver(f"[solver] mode=codegen; status={status}; filled={filled}; missing={missing}; result_interpretation_instruction={sr.interpretation_instruction()}")

                if pl_text:
                    scratchpad.tlog.note(f"[project_log]\n{pl_text.strip()[:4000]}")
                    # error details (if any)
                try:
                    for c in (execution.calls or []):
                        tool_id = c.get("tool_id","")
                        order = c.get("order")
                        outputs_n = len(c.get("outputs") or [])
                        inputs_n = len(c.get("input") or [])
                        l = f"[solver.calls] order={order} tool={tool_id} inputs_n={inputs_n} outputs={outputs_n} "
                        self.log.log(l)
                        scratchpad.tlog.note(l)
                except Exception:
                    pass

                if not ok and isinstance(solver_json, dict):
                    err = solver_json.get("error") or {}
                    """
                    "error": {
                        "where": (where or "runtime"),
                        "details": str(details or ""),
                        "error": str(error or ""),
                        "description": description,
                        "managed": bool(managed),
                    }
                    """
                    description = err.get("description")
                    error = err.get("error")
                    where = err.get("where") or ""
                    details = err.get("details") or ""
                    if any([description, where, details]):
                        execution.error = f"where={where} error={error} details={details} description={description}"
                        scratchpad.tlog.note(f"[solver.error] {execution.error}")
                        failure_md = ("### Solver JSON Failure",
                                      "```json",
                                      json.dumps(solver_json, ensure_ascii=False, indent=2),
                                      "```")
                        execution.failure_presentation = (
                            { "markdown": failure_md, "struct": err }
                        )
            except Exception:
                pass

        # ---- llm only (no tools) ----
        return SolveResult(result)


    # -------- execution & artifact promotion --------

    def _mk_artifact(
        self,
        *,
        resource_id: str,
        type_: str,
        tool_id: str,
        path: Optional[str] = None,
        value: Optional[str] = None,
        mime: Optional[str] = None,
        citable: bool = False,
        description: Optional[str] = None,
        tool_input: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        a = {
            "resource_id": resource_id,
            "type": type_,
            "tool_id": tool_id,
            "mime": mime,
            "citable": bool(citable),
            "description": description or "",
            "input": tool_input or {},
        }
        if type_ == "file": a["path"] = path or ""
        else: a["value"] = value or ""
        return a

    async def execute_plan(self, steps: List[Dict[str, Any]], *, allowed_plugins: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Execute [{tool:'alias.fn', args:{...}, save_as:'name'}].
        Returns:
          {
            steps:[{ok, tool, args, return, elapsed_ms, call_id, save_as?}, ...],
            out:[artifacts...],                  # flattened normalized artifacts (with tool_input)
            calls:[{call_id, tool_id, args, artifacts:[...]}]  # one entry per invocation
          }
        """
        out = {"steps": [], "out": [], "calls": []}

        from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
        token_sid = SOURCE_ID_CV.set({"next": 1})
        allowed = set(allowed_plugins or [])
        call_seq = 0

        try:
            for s in steps or []:
                tool_id = s.get("tool")
                entry = self._by_id.get(tool_id)
                if not entry:
                    out["steps"].append({"ok": False, "tool": tool_id, "args": s.get("args") or {}, "error": "tool_not_found"})
                    continue

                plugin_name  = (entry.get("plugin") or "")
                plugin_alias = (entry.get("plugin_alias") or "")

                if allowed and not ({plugin_name, plugin_alias, tool_id} & allowed):
                    out["steps"].append({
                        "ok": False,
                        "tool": tool_id,
                        "args": s.get("args") or {},
                        "error": "plugin_not_allowed",
                        "plugin": plugin_name,
                        "alias": plugin_alias
                    })
                    continue

                fn = self._resolve_callable(tool_id)
                if fn is None:
                    out["steps"].append({"ok": False, "tool": tool_id, "args": s.get("args") or {}, "error": "callable_not_found"})
                    continue

                want = {p["name"] for p in (entry.get("params") or [])}
                args = {k: v for (k, v) in (s.get("args") or {}).items() if k in want}

                t0 = time.perf_counter()
                try:
                    ret = fn(**args) if args else fn()
                    if inspect.isawaitable(ret): ret = await ret
                    elapsed = int((time.perf_counter() - t0) * 1000)

                    parsed = None
                    if isinstance(ret, str):
                        sv = ret.strip()
                        if (sv.startswith("{") and sv.endswith("}")) or (sv.startswith("[") and sv.endswith("]")):
                            try: parsed = json.loads(sv)
                            except Exception: parsed = sv
                        else:
                            parsed = sv
                    else:
                        parsed = ret

                    # Promote artifacts per your contract

                    call_seq += 1
                    out["calls"].append({
                        "call_id": call_seq,
                        "tool_id": tool_id,
                        "args": args,
                    })
                    out["steps"].append({
                        "ok": True, "tool": tool_id, "args": args, "save_as": s.get("save_as"),
                        "return": parsed, "elapsed_ms": elapsed, "call_id": call_seq
                    })
                    # naive promotion: if tool returned our normalized out[] put it through
                    if isinstance(parsed, dict) and isinstance(parsed.get("out"), list):
                        for it in parsed["out"]:
                            if isinstance(it, dict):
                                it.setdefault("tool_id", tool_id)
                                it.setdefault("input", args)
                                out["out"].append(it)
                except Exception as e:
                    elapsed = int((time.perf_counter() - t0) * 1000)
                    out["steps"].append({"ok": False, "tool": tool_id, "args": args, "error": f"{type(e).__name__}: {e}", "elapsed_ms": elapsed})
        finally:
            SOURCE_ID_CV.reset(token_sid)
        return out

    # -------- codegen runtime --------

    async def run_code_gen(
            self,
            *,
            request_id: str,
            user_text: str,
            adapters: List[Dict[str, Any]],
            solvability: Optional[Dict[str, Any]] = None,
            policy_summary: str = "",
            topics: Optional[List[str]] = None,
            prefs_hint: Optional[Dict[str, Any]] = None,
            extra_task_hint: Optional[Dict[str, Any]] = None,
            constraints: Optional[Dict[str, Any]] = None,
            reuse_outdir: bool = False,
            outdir: Optional[pathlib.Path] = None,
            max_rounds: int = 1,
            timeout_s=120,
            program_history: Optional[List[dict]] = None,
    ) -> Dict[str, Any]:
        """Materialize + run codegen once (or a few times with chaining) and collect outputs."""
        from kdcube_ai_app.apps.chat.sdk.codegen.team import _today_str


        topics = topics or []
        constraints = constraints or {"prefer_direct_tools_exec": True, "minimize_logic": True, "concise": True, "line_budget": 80}

        # attach a stable run-id to this codegen session
        import uuid as _uuid
        run_id = f"cg-{_uuid.uuid4().hex[:8]}"

        # Working dirs
        if not reuse_outdir or outdir is None:
            import tempfile
            tmp = pathlib.Path(tempfile.mkdtemp(prefix="solver_"))
            workdir, outdir = tmp / "pkg", tmp / "out"
            workdir.mkdir(parents=True, exist_ok=True); outdir.mkdir(parents=True, exist_ok=True)
        else:
            workdir = outdir / "pkg"
            workdir.mkdir(parents=True, exist_ok=True)
        self.log.log(f"Working directory: {workdir}")
        rounds: List[Dict[str, Any]] = []
        remaining = max(1, int(max_rounds))

        current_task_spec = {
            "objective": user_text,
            "constraints": constraints,
            "tools_selected": [a["id"] for a in adapters],
            "notes": [extra_task_hint or {}],
            "prefs_hint": prefs_hint or {},
        }

        remaining = max(1, int(max_rounds))
        while remaining > 0:
            # stream codegen
            cg_stream = await solver_codegen_stream(
                self.svc,
                task=current_task_spec,
                adapters=adapters,
                solvability=solvability,
                on_thinking_delta=self._mk_thinking_streamer("solver_codegen"),
                ctx="solver_codegen"
            )
            cg = (cg_stream or {}).get("agent_response") or {}
            internal_thinking = (cg_stream or {}).get("internal_thinking") or ""
            files = cg.get("files") or []
            entrypoint = cg.get("entrypoint") or "python main.py"
            result_interpretation_instruction = cg.get("result_interpretation_instruction") or ""
            outputs = cg.get("outputs") or [{"filename": "result.json", "kind": "json", "key": "solver_output"}]
            notes = cg.get("notes") or ""
            current_task_spec["notes"].append(notes)

            # materialize files
            files_map = {f["path"]: f["content"] for f in files if f.get("path") and f.get("content") is not None}
            for rel, content in files_map.items():
                p = workdir / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

            # derive brief + latest materials from history
            latest_presentation = ""
            latest_solver_failure = ""
            latest_turn_log = ""
            try:
                if program_history:
                    # 2) Reconcile & get canonical sources (and rewritten tokens in-place)
                    rec = project_retrieval.reconcile_citations_for_context(program_history, max_sources=60, rewrite_tokens_in_place=True)
                    canonical_sources = rec["canonical_sources"]   # ← pass this to context.json["sources"]

                    # 3) Choose the “current” editable (usually newest run)
                    latest = next(iter(program_history[0].values()), {}) if program_history else {}
                    canvas_md = (latest.get("project_canvas") or {}).get("text") or (latest.get("project_canvas") or {}).get("value") or ""

                    exec_id, inner = next(iter(program_history[0].items()))
                    latest_presentation = inner.get("program_presentation") if isinstance(inner.get("program_presentation"), str) else ""
                    latest_solver_failure = inner.get("solver_failure") if isinstance(inner.get("solver_failure"), str) else ""
                    latest_turn_log = ((inner.get("project_log") or {}).get("text") or "")
            except Exception:
                pass

            contract_out = solvability.get("output_contract_dyn") if isinstance(solvability, dict) else {}
            context = {
                "request_id": request_id,
                "program_history": program_history or [],
                "program_history_brief": project_retrieval._history_digest(program_history, limit=3),
                "latest_program_presentation": latest_presentation,
                "latest_solver_failure": latest_solver_failure,
                "latest_turn_log": latest_turn_log,
                "topics": topics,
                "prefs_hint": prefs_hint or {},
                "policy_summary": policy_summary,
                "today": _today_str(),
                "notes": notes,
                "run_id": run_id,
                "result_interpretation_instruction": result_interpretation_instruction,
                "internal_thinking": internal_thinking,
            }
            # write runtime inputs
            self.write_runtime_inputs(
                output_dir=outdir,
                context=context,
                task={
                    **current_task_spec,
                    "adapters_spec": adapters,
                    "contract": {
                        "out": contract_out
                    }
                }
            )

            # run + collect
            run_res = await self.run_main_py_package(workdir=workdir,
                                                     output_dir=outdir,
                                                     files={},
                                                     timeout_s=timeout_s,
                                                     globals={
                                                         "CONTRACT": contract_out
                                                     })
            collected = self.collect_outputs(output_dir=outdir, outputs=outputs)

            round_rec = {
                "entrypoint": entrypoint,
                "files": [{"path": p, "size": len(c or "")} for p, c in files_map.items()],
                "run": run_res,
                "notes": current_task_spec["notes"],
                "outputs": collected,
                "internal_thinking": internal_thinking,
                "result_interpretation_instruction": result_interpretation_instruction,
                "inputs": {
                    "constraints": constraints,
                    "objective": user_text,
                    "topics": topics,
                    "tools_selected": current_task_spec["tools_selected"],
                    "policy_summary": policy_summary,
                },
                "workdir": str(workdir),
                "outdir": str(outdir),
                "run_id": run_id,
            }
            # inline preview of main.py when short
            main_src = files_map.get("main.py")
            if main_src and len(main_src) <= 8000:
                round_rec["main_preview"] = main_src

            rounds.append(round_rec)

            # Optional chaining: detect another round
            next_spec_path = outdir / "next_codegen.json"
            remaining -= 1
            if remaining <= 0 or not next_spec_path.exists():
                break
            try:
                next_spec = json.loads(next_spec_path.read_text(encoding="utf-8"))
            except Exception:
                break

            # Update for the next round (reuse same outdir so artifacts accumulate)
            current_task_spec = {
                "objective": next_spec.get("objective") or current_task_spec["objective"],
                "constraints": next_spec.get("constraints") or current_task_spec.get("constraints"),
                "tools_selected": next_spec.get("tools_selected") or current_task_spec.get("tools_selected"),
                "notes": next_spec.get("notes") or {},
            }
            requested = set(current_task_spec["tools_selected"] or [])
            support_ids = [e["id"] for e in self.tools_info if (e.get("plugin_alias") or "") in ["io_tools", "ctx_tools"]]
            adapters = self.adapters_for_codegen(allowed_ids=list(requested | set(support_ids)))

        return {
            "rounds": rounds,
            "outdir": str(outdir),
            "workdir": str(workdir),
            "run_id": run_id,
        }

    # -------- runtime IO & exec --------

    def _extract_solver_json_from_round(self, r0: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch the primary JSON payload (first output item with kind=json)."""
        items = (r0.get("outputs") or {}).get("items", [])
        for it in items:
            data = it.get("data")
            if isinstance(data, dict) and data.get("ok") is not None:
                return data
        return None

    def write_runtime_inputs(self, *, output_dir: pathlib.Path, context: Dict[str, Any], task: Dict[str, Any]) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")

    def _tool_modules_tuple_list(self) -> List[Tuple[str, object]]:
        return [(m["name"], m["mod"]) for m in self._modules]

    async def run_solver_snippet(self, *,
                                 code: str,
                                 output_dir: pathlib.Path,
                                 globals: Optional[Dict[str, Any]] = None,
                                 timeout_s: int = 90) -> Dict[str, Any]:
        return await self.runtime.run_snippet(
            code=code,
            output_dir=output_dir,
            tool_modules=self._tool_modules_tuple_list(),
            globals=globals,
            timeout_s=timeout_s,
        )

    async def run_main_py_package(self, *,
                                  workdir: pathlib.Path,
                                  output_dir: pathlib.Path,
                                  files: Dict[str, str],
                                  globals: Optional[Dict[str, Any]] = None,
                                  timeout_s: int = 90) -> Dict[str, Any]:
        for rel, content in (files or {}).items():
            p = workdir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        return await self.runtime.run_main_py(
            workdir=workdir,
            output_dir=output_dir,
            tool_modules=self._tool_modules_tuple_list(),  # <-- ALL modules injected
            globals=globals or {},
            timeout_s=timeout_s,
        )

    def collect_outputs(self, *, output_dir: pathlib.Path, outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"items": []}
        for spec in outputs or []:
            fn = spec.get("filename") or ""
            kind = (spec.get("kind") or "json").lower()
            key  = spec.get("key")
            p = (output_dir / fn)
            item = {"filename": fn, "present": p.exists()}
            if p.exists():
                try:
                    if kind == "json":
                        item["data"] = json.loads(p.read_text(encoding="utf-8"))
                    elif kind == "text":
                        item["data"] = p.read_text(encoding="utf-8")
                    else:
                        item["size"] = p.stat().st_size
                        item["data"] = None
                except Exception as e:
                    item["error"] = f"{type(e).__name__}: {e}"
            if key:
                item["key"] = key
            out["items"].append(item)
        return out

    # -------- call grouping (ordered) --------

    def _group_calls_sequential(self, out_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Group normalized out[] into *ordered* calls by sequence:
        consecutive items with identical (tool_id, tool_input) belong to the same call.
        This preserves call order and avoids merging separate identical calls.
        Returns:
          [{"order": i, "tool_id": "...", "input": {...}, "outputs":[out-item,...]}]
        """
        calls: List[Dict[str, Any]] = []
        cur: Optional[Dict[str, Any]] = None

        def _same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
            return (a.get("tool_id") or "") == (b.get("tool_id") or "") and (a.get("input") or {}) == (b.get("input") or {})

        for it in (out_items or []):
            if not isinstance(it, dict):
                continue
            base = {"tool_id": it.get("tool_id") or "", "input": it.get("input") or {}}
            if cur and _same(cur, base):
                cur["outputs"].append(it)
            else:
                if cur:
                    calls.append(cur)
                cur = {"tool_id": base["tool_id"], "input": base["input"], "outputs": [it]}
        if cur:
            calls.append(cur)
        for i, c in enumerate(calls, 1):
            c["order"] = i
        return calls

    # -------- comm helpers --------

    def _mk_thinking_streamer(self, phase: str) -> Callable[[str], Awaitable[None]]:
        counter = {"n": 0}
        async def emit_thinking_delta(text: str, completed: bool = False):
            if not text:
                return
            i = counter["n"]; counter["n"] += 1
            author = f"{self.AGENT_NAME}.{phase}"
            await self.comm.delta(text=text, index=i, marker="thinking", agent=author, completed=completed)
        return emit_thinking_delta

    async def _emit_event(self, rid: str, *, etype: str, title: str, step: str, data: Dict[str, Any],
                          timing: Optional[Dict[str, Any]] = None, status: str = "completed"):
        evt = {
            "type": etype,
            "agent": self.AGENT_NAME,
            "step": step,
            "status": status,
            "title": title,
            "data": data,
            "timing": timing or {},
        }
        await self.emit(evt)

    # -------- internals --------

    def _resolve_callable(self, qualified_id: str):
        try:
            alias, fn = qualified_id.split(".", 1)
            modrec = self._mods_by_alias[alias]
            owner = getattr(modrec["mod"], "tools", modrec["mod"])
            return getattr(owner, fn)
        except Exception:
            return None