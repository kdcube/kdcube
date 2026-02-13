# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/execution.py

import json
import time
import pathlib
import re
import shutil
import traceback
import uuid
from typing import Any, Dict, Optional, Tuple

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import build_portable_spec

from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _InProcessRuntime
from kdcube_ai_app.apps.chat.sdk.runtime.tool_index import read_index
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights

def _safe_label(s: str, *, maxlen: int = 96) -> str:
    """Filesystem-safe label from tool_id."""
    lbl = re.sub(r"[^A-Za-z0-9_.-]+", "_", s or "")
    return lbl[:maxlen] if len(lbl) > maxlen else lbl

def _safe_exec_id(val: Optional[str]) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", (val or "")).strip("_")
    return safe or uuid.uuid4().hex[:12]

def _build_exec_context(
    *,
    runtime_ctx: RuntimeCtx,
    tool_manager: ToolSubsystem,
    tool_execution_context: Dict[str, Any],
) -> Dict[str, Any]:
    comm = getattr(tool_manager, "comm", None)
    conv = getattr(comm, "conversation", {}) if comm else {}
    svc = getattr(comm, "service", {}) if comm else {}
    exec_id = tool_execution_context.get("exec_id") or tool_execution_context.get("tool_call_id")
    codegen_run_id = (
        tool_execution_context.get("codegen_run_id")
        or tool_execution_context.get("run_id")
        or exec_id
    )
    return {
        "tenant": getattr(comm, "tenant", None),
        "project": getattr(comm, "project", None),
        "user_id": getattr(comm, "user_id", None),
        "user_type": getattr(comm, "user_type", None),
        "conversation_id": runtime_ctx.conversation_id,
        "turn_id": runtime_ctx.turn_id or conv.get("turn_id"),
        "session_id": conv.get("session_id"),
        "request_id": svc.get("request_id"),
        "bundle_id": runtime_ctx.bundle_id,
        "exec_id": exec_id,
        "codegen_run_id": codegen_run_id,
    }

def _normalize_error_dict(err: Any, *, default_where: str) -> Optional[Dict[str, Any]]:
    if not err:
        return None
    if isinstance(err, dict):
        return {
            "code": err.get("code", "unknown"),
            "message": err.get("message", ""),
            "where": err.get("where", default_where),
            "managed": err.get("managed", False),
            **({"details": err.get("details")} if isinstance(err.get("details"), dict) else {}),
        }
    return {
        "code": "unknown",
        "message": str(err),
        "where": default_where,
        "managed": False,
    }


def _unwrap_tool_envelope(output: Any) -> Tuple[Any, Optional[Dict[str, Any]], bool]:
    """
    Tools may return {ok, error, ret}. Unwrap to ret and return (ret, error, ok).
    If no envelope detected, returns (output, None, True).
    """
    if not isinstance(output, dict):
        return output, None, True
    if not any(k in output for k in ("ok", "error", "ret")):
        return output, None, True
    tool_ok = bool(output.get("ok", True))
    tool_error = output.get("error")
    tool_ret = output.get("ret")
    return tool_ret, (tool_error if tool_error is not None else None), tool_ok


async def _execute_exec_tool(
    *,
    tool_execution_context: Dict[str, Any],
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    tool_manager: ToolSubsystem,
    logger: AgentLogger,
    tool_call_id: Optional[str] = None,
    exec_streamer: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute exec_tools.* with dedicated handling."""
    tool_id = tool_execution_context["tool_id"]
    params = tool_execution_context.get("params") or {}
    from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import run_exec_tool, build_exec_output_contract
    artifacts_spec = params.get("contract")
    code = params.get("code") or ""
    timeout_s = params.get("timeout_s") or 600
    exec_id = exec_streamer.execution_id
    # exec_id = _safe_exec_id(tool_execution_context.get("exec_id") or tool_call_id)

    base_error = {
        "status": "error",
        "output": None,
        "items": [],
        "tool_call_id": tool_call_id,
    }

    async def _emit_exec_error(summary: str, error_obj: Dict[str, Any]) -> Dict[str, Any]:
        if exec_streamer:
            try:
                await exec_streamer.emit_status(status="error", error=error_obj)
            except Exception:
                logger.log("[react.exec] Failed to emit execution status", level="WARNING")
        payload = dict(base_error)
        payload["summary"] = summary
        payload["error"] = error_obj
        return payload

    prog_name = (params.get("prog_name") or "").strip()
    if exec_streamer and prog_name:
        try:
            await exec_streamer.emit_program_name(prog_name)
        except Exception:
            logger.log("[react.exec] Failed to emit program name", level="WARNING")

    contract, normalized_artifacts, err = build_exec_output_contract(artifacts_spec)
    if exec_streamer and contract:
        try:
            await exec_streamer.emit_contract(contract)
        except Exception:
            logger.log("[react.exec] Failed to emit execution contract", level="WARNING")
    if err:
        error_obj = {
            "code": err.get("code", "invalid_artifacts"),
            "message": err.get("message", "Invalid artifacts spec"),
            "where": "exec_execution",
            "managed": True,
        }
        summary = f"Exec tool requires valid artifacts spec: {error_obj.get('message')}"
        return await _emit_exec_error(summary, error_obj)
    if not code:
        error_obj = {
            "code": "missing_parameters",
            "message": "Parameter 'code' is required for exec tool",
            "where": "exec_execution",
            "managed": True,
        }
        summary = "Exec tool requires non-empty 'code' parameter"
        return await _emit_exec_error(summary, error_obj)
    exec_t0 = time.perf_counter()
    envelope = await run_exec_tool(
        tool_manager=tool_manager,
        logger=logger,
        output_contract=contract,
        code=code,
        contract=normalized_artifacts or [],
        timeout_s=int(timeout_s),
        outdir=outdir,
        workdir=workdir,
        exec_id=exec_id,
    )
    exec_ms = int((time.perf_counter() - exec_t0) * 1000)
    if exec_streamer:
        try:
            exec_streamer.set_timings(exec_ms=exec_ms)
            await exec_streamer.emit_status(
                status="done" if envelope.get("ok", False) else "error",
                error=envelope.get("error") if not envelope.get("ok", False) else None,
            )
        except Exception:
            logger.log("[react.exec] Failed to emit execution status", level="WARNING")

    exec_workdir = pathlib.Path(envelope.get("workdir") or "")
    codefile_path = exec_workdir / "main.py"
    try:
        if codefile_path.exists():
            dest_dir = outdir / "executed_programs"
            dest_dir.mkdir(parents=True, exist_ok=True)
            i = 0
            while (dest_dir / f"{_safe_label(tool_id)}_{i}_main.py").exists():
                i += 1
            dest_main = dest_dir / f"{_safe_label(tool_id)}_{i}_main.py"
            shutil.copy2(codefile_path, dest_main)
            logger.log(f"[react.exec] main.py preserved as {dest_main.relative_to(outdir)}")
    except Exception as e:
        logger.log(f"[react.exec] Failed to preserve main.py: {e}", level="WARNING")

    err_obj = envelope.get("error")
    report_text = (envelope.get("report_text") or "").strip()
    items = envelope.get("items") or []
    first = items[0] if items else {}
    status = "success" if envelope.get("ok", True) and not err_obj else "error"
    return {
        "status": status,
        "output": first.get("output"),
        "summary": report_text,
        "items": items,
        "tool_call_id": tool_call_id,
        "error": err_obj,
        "report_text": report_text,
    }

async def _execute_tool_in_memory(
    *,
    tool_execution_context: Dict[str, Any],
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    tool_manager: ToolSubsystem,
    logger: AgentLogger,

) -> Dict[str, Any]:
    """
    Execute a single tool in this process using io_tools.tool_call.
    Now includes error capture and better summaries.
    """
    from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools

    tool_id = tool_execution_context["tool_id"]

    params = tool_execution_context.get("params") or {}
    call_reason = tool_execution_context.get("reasoning") or f"ReAct call: {tool_id}"

    # exec tools are handled by _execute_exec_tool

    # bootstrap once via subsystem (sets OUTDIR/WORKDIR; service bindings; comm)
    await tool_manager.prebind_for_in_memory(workdir=workdir,
                                             outdir=outdir,
                                             logger=logger,
                                             bootstrap_env=True)

    # resolve "<alias>.<fn>" or "mcp.<alias>.<tool_id...>"
    fn = None
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import parse_tool_id
        origin, provider, fn_name = parse_tool_id(tool_id)
        if origin == "mcp":
            fn = None
        elif origin == "mod" and provider and fn_name:
            alias = provider
            owner = tool_manager.get_owner_for_alias(alias)
            if owner is None:
                return {
                    "status": "error",
                    "output": None,
                    "summary": f"Alias '{alias}' not found",
                    "error": {
                        "code": "alias_not_found",
                        "message": f"Tool alias '{alias}' is not registered",
                        "where": "execution",
                        "managed": True,
                    }
                }
            fn = getattr(owner, fn_name, None)
            if fn is None:
                return {
                    "status": "error",
                    "output": None,
                    "summary": f"Function '{fn_name}' not found on '{alias}'",
                    "error": {
                        "code": "function_not_found",
                        "message": f"Function '{fn_name}' not found on alias '{alias}'",
                        "where": "execution",
                        "managed": True,
                    }
                }
        else:
            raise ValueError(f"Unsupported tool id for execution: {tool_id}")
    except ValueError:
        return {
            "status": "error",
            "output": None,
            "summary": f"Bad tool_id: {tool_id}",
            "error": {
                "code": "invalid_tool_id",
                "message": f"Tool ID '{tool_id}' is not in supported format",
                "where": "execution",
                "managed": True,
            }
        }

    outdir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    # Call via io_tools (handles its own error capture)
    exception_info = None
    try:
        await agent_io_tools.tool_call(
            fn=fn,
            params=params,
            call_reason=call_reason,
            tool_id=tool_id,
        )
    except Exception as e:
        exception_info = {
            "code": type(e).__name__,
            "message": str(e),
            "where": "tool_execution",
            "managed": False,
        }
        logger.log(
            f"[react.exec-inline] Exception during tool_call for {tool_id}\n{traceback.format_exc()}",
            level="ERROR"
        )

    # Read persisted call
    idx_map = read_index(outdir)
    if not idx_map:
        logger.log(f"[react.exec-inline] Tool {tool_id} call is not registered in tool call index", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": f"Tool {tool_id} call is not registered in tool call index",
            "error": exception_info or {
                "code": "no_tool_call_in_index",
                "message": "Tool execution completed but not registered in tool call index",
                "where": "io_tools",
                "managed": True,
            }
        }

    files = list(idx_map.get(tool_id) or [])
    if not files:
        logger.log(f"[react.exec-inline]. {tool_id} execution attempted but no output found from tool", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": "No tool output found",
            "error": exception_info or {
                "code": "missing_tool_output",
                "message": f"No output found for tool '{tool_id}'",
                "where": "io_tools",
                "managed": True,
            }
        }

    last_rel = files[-1]
    call_path = outdir / last_rel
    if not call_path.exists():
        logger.log(f"[react.exec] {tool_id} execution attempted but no output found from tool", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": f"No tool output found",
            "error": {
                "code": "missing_tool_output",
                "message": f"No output found for tool '{tool_id}'",
                "where": "io_tools",
                "managed": True,
            }
        }

    try:
        payload = json.loads(call_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.log(f"[react.exec] {tool_id} Malformed tool result: {e}", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": f"Bad tool result JSON: {e}",
            "error": {
                "code": "malformed_tool_result",
                "message": str(e),
                "where": "io_tools",
                "managed": True,
            }
        }

    output = payload.get("ret")
    output, tool_error, tool_ok = _unwrap_tool_envelope(output)

    tool_error_info = None
    if tool_error is not None or tool_ok is False:
        tool_error_info = _normalize_error_dict(tool_error, default_where=tool_id)
        if tool_ok is False and tool_error_info is None:
            tool_error_info = {
                "code": "tool_error",
                "message": "Tool returned ok=false",
                "where": tool_id,
                "managed": True,
            }
    call_error_info = exception_info

    status = "error" if (tool_error_info or call_error_info) else "success"

    # Build summary with centralized logic
    err_for_summary = call_error_info or tool_error_info
    if err_for_summary:
        code = err_for_summary.get("code", "unknown")
        where = err_for_summary.get("where", "tool")
        msg = (err_for_summary.get("message") or "").strip()
        if len(msg) > 200:
            msg = msg[:197] + "..."
        summary = f"ERROR [{code}] at {where}: {msg}"
    else:
        summary = ""

    return {
        "status": status,
        "output": output,
        "summary": summary,
        "error": tool_error_info,
        "call_error": call_error_info,
    }


async def execute_tool_in_isolation(
        runtime_ctx: RuntimeCtx,
        tool_execution_context: Dict[str, Any],
        workdir: pathlib.Path,
        outdir: pathlib.Path,
        tool_manager: ToolSubsystem,
        logger: AgentLogger,
        isolation_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a single tool using the iso runtime (bootstrapped child).
    Now includes error capture and better summaries.

    Returns:
      - status: "success" | "error"  (process exit status)
      - output: raw tool return (payload stored under 'ret' in saved call json)
      - summary: short human summary derived from output

    Additionally:
      - If workdir/main.py (or another discovered main.py) exists after execution,
        it is copied to: outdir/executed_programs/{safe_tool_id}_{call_index}_main.py
        where call_index == len(files) from tool_calls_index.json for this tool_id.
    """
    tool_id = tool_execution_context["tool_id"]
    params = tool_execution_context.get("params") or {}
    call_reason = tool_execution_context.get("reasoning") or f"ReAct call: {tool_id}"

    logger.log(f"[react.exec] Executing {tool_id} via iso-runtime")

    runtime = _InProcessRuntime(logger)

    # alias maps + modules provided by subsystem
    tool_modules = tool_manager.tool_modules_tuple_list()

    # alias maps (must match main program executor)
    runtime_globals = tool_manager.export_runtime_globals()
    try:
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import get_active_skills_subsystem
        runtime_globals = {
            **runtime_globals,
            **get_active_skills_subsystem().export_runtime_globals(),
        }
    except Exception:
        pass

    # portable spec for child to rebind services
    spec = build_portable_spec(svc=tool_manager.svc, chat_comm=tool_manager.comm)
    portable_spec_json = spec.to_json()

    # communicator spec (redis relay etc.)
    comm_spec = getattr(tool_manager.comm, "_export_comm_spec_for_runtime", lambda: {})()
    exec_context = _build_exec_context(
        runtime_ctx=runtime_ctx,
        tool_manager=tool_manager,
        tool_execution_context=tool_execution_context,
    )

    globals_for_runtime = {
        "CONTRACT": {},
        "COMM_SPEC": comm_spec,
        "PORTABLE_SPEC_JSON": portable_spec_json,
        "EXEC_CONTEXT": exec_context,
        **runtime_globals,  # TOOL_ALIAS_MAP, TOOL_MODULE_FILES, BUNDLE_SPEC, RAW_TOOL_SPECS
    }

    isolation = isolation_override or tools_insights.tool_isolation(tool_id=tool_id)
    # Unless there's no third-party blackboxed tools, and the tools are all verified, it is safe. TODO.
    # Tool isolation settings TODO define on the level of Tool Subsystem and simply inherit here as a part of runtime_globals.
    # Per-call overrides for sandbox behavior; _run_subprocess will read these via env
    # Docker is required when filesystem isolation is needed.
    res = await runtime.run_tool_in_isolation(
        workdir=workdir,
        output_dir=outdir,
        bundle_root=tool_manager.bundle_root,
        tool_modules=tool_modules,
        tool_id=tool_id,
        params=params,
        call_reason=call_reason,
        globals=globals_for_runtime,
        timeout_s=240,
        isolation=isolation
    )
    # Best-effort: bring back streamed deltas from the sandboxed tool run
    try:
        tool_manager.comm.merge_delta_cache_from_file(outdir / "delta_aggregates.json")
    except Exception:
        pass

    # Check for subprocess-level errors
    subprocess_error = None
    if res.get("error") == "timeout":
        subprocess_error = {
            "code": "timeout",
            "message": f"Tool execution exceeded {res.get('seconds', 240)}s timeout",
            "where": "subprocess",
            "managed": True,
        }
    elif not res.get("ok", True):
        subprocess_error = {
            "code": "subprocess_exit",
            "message": f"Subprocess exited with code {res.get('returncode', '?')}",
            "where": "subprocess",
            "managed": True,
        }

    # Read saved call
    idx_map = read_index(outdir)
    if not idx_map:
        logger.log(f"[react.exec] Tool {tool_id} call is not registered in tool call index", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": f"Tool {tool_id} call is not registered in tool call index",
            "error": subprocess_error or {
                "code": "no_tool_call_in_index",
                "message": "Tool execution completed but not registered in tool call index",
                "where": "io_tools",
                "managed": True,
            }
        }

    files = list(idx_map.get(tool_id) or [])
    if not files:
        logger.log(f"[react.exec-inline]. {tool_id} execution attempted but no output found from tool", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": "No tool output found",
            "error": subprocess_error or {
                "code": "missing_tool_output",
                "message": f"No output found for tool '{tool_id}'",
                "where": "io_tools",
                "managed": True,
            }
        }

    # Current call info
    call_index = len(files)
    last_rel = files[-1]
    call_path = outdir / last_rel
    if not call_path.exists():
        logger.log(f"[react.exec] {tool_id} execution attempted but no output found from tool", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": f"No tool output found",
            "error": {
                "code": "missing_tool_output",
                "message": f"No output found for tool '{tool_id}'",
                "where": "io_tools",
                "managed": True,
            }
        }

    try:
        payload = json.loads(call_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.log(f"[react.exec] {tool_id} Malformed tool result: {e}", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": f"Bad tool result JSON: {e}",
            "error": {
                "code": "malformed_tool_result",
                "message": str(e),
                "where": "io_tools",
                "managed": True,
            }
        }

    output = payload.get("ret")
    output, tool_error, tool_ok = _unwrap_tool_envelope(output)

    tool_error_info = None
    if tool_error is not None or tool_ok is False:
        tool_error_info = _normalize_error_dict(tool_error, default_where=tool_id)
        if tool_ok is False and tool_error_info is None:
            tool_error_info = {
                "code": "tool_error",
                "message": "Tool returned ok=false",
                "where": tool_id,
                "managed": True,
            }
    call_error_info = subprocess_error

    status = "error" if (tool_error_info or call_error_info) else "success"

    # Build summary with centralized logic
    err_for_summary = call_error_info or tool_error_info
    if err_for_summary:
        code = err_for_summary.get("code", "unknown")
        where = err_for_summary.get("where", "subprocess")
        msg = (err_for_summary.get("message") or "").strip()
        if len(msg) > 200:
            msg = msg[:197] + "..."
        summary = f"ERROR [{code}] at {where}: {msg}"
    else:
        summary = ""

    # Preserve executed main.py (existing code)
    try:
        # Prefer workdir/main.py; otherwise pick the newest main.py under workdir
        src_main = workdir / "main.py"
        if not src_main.exists():
            candidates = sorted(workdir.rglob("main.py"), key=lambda p: p.stat().st_mtime, reverse=True)
            src_main = candidates[0] if candidates else None

        if src_main and src_main.exists():
            dest_dir = outdir / "executed_programs"
            dest_dir.mkdir(parents=True, exist_ok=True)
            label = f"{_safe_label(tool_id)}_{call_index}_main.py"
            dest_main = dest_dir / label

            # Avoid rare collision
            if dest_main.exists():
                i = 1
                while (dest_dir / f"{_safe_label(tool_id)}_{call_index}_{i}_main.py").exists():
                    i += 1
                dest_main = dest_dir / f"{_safe_label(tool_id)}_{call_index}_{i}_main.py"

            shutil.copy2(src_main, dest_main)
            logger.log(f"[react.exec] main.py preserved as {dest_main.relative_to(outdir)}")
    except Exception as e:
        logger.log(f"[react.exec] Failed to preserve main.py: {e}", level="WARNING")

    # inputs/call_record_* not used in v2
    return {
        "status": status,
        "output": output,
        "summary": summary or "",
        "error": tool_error_info,
        "call_error": call_error_info,
    }

async def execute_tool(
        runtime_ctx: RuntimeCtx,
        tool_execution_context: Dict[str, Any],
        workdir: pathlib.Path,
        outdir: pathlib.Path,
        tool_manager: ToolSubsystem,
        logger: AgentLogger,
        tool_call_id: Optional[str] = None,
        exec_streamer: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Unified entry with error capture and optional LLM summarization.
      - if tool_id ∈ IN_MEMORY_TOOL_IDS → run in-process (io_tools.tool_call)
      - else → run in sandbox subprocess (preserves main.py into executed_programs/)
    """
    tool_id = tool_execution_context.get("tool_id") or ""
    runtime_override = None
    if tool_manager and hasattr(tool_manager, "get_tool_runtime"):
        try:
            runtime_override = tool_manager.get_tool_runtime(tool_id)
        except Exception:
            runtime_override = None

    if tools_insights.is_exec_tool(tool_id):
        return await _execute_exec_tool(
            tool_execution_context=tool_execution_context,
            workdir=workdir,
            outdir=outdir,
            tool_manager=tool_manager,
            logger=logger,
            tool_call_id=tool_call_id,
            exec_streamer=exec_streamer,
        )

    if runtime_override == "none":
        return await _execute_tool_in_memory(
            tool_execution_context=tool_execution_context,
            workdir=workdir,
            outdir=outdir,
            tool_manager=tool_manager,
            logger=logger,
        )

    if not tools_insights.should_isolate_tool_execution(tool_id) and runtime_override is None:
        return await _execute_tool_in_memory(
            tool_execution_context=tool_execution_context,
            workdir=workdir,
            outdir=outdir,
            tool_manager=tool_manager,
            logger=logger,
        )

    return await execute_tool_in_isolation(runtime_ctx=runtime_ctx,
                                           tool_execution_context=tool_execution_context,
                                           workdir=workdir,
                                           outdir=outdir,
                                           tool_manager=tool_manager,
                                           logger=logger,
                                           isolation_override=runtime_override)
