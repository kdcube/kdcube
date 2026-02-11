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
from typing import Any, Dict, Optional, Tuple, List

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import build_portable_spec
from kdcube_ai_app.apps.chat.sdk.runtime.logging_utils import errors_log_tail, last_error_block

from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _InProcessRuntime
from kdcube_ai_app.apps.chat.sdk.runtime.tool_index import read_index
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.artifacts import ArtifactView
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.artifact_analysis import (
    analyze_write_tool_output,
)

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

def _extract_error_from_output(output: Any) -> Optional[Dict[str, Any]]:
    """
    Extract error information from tool output if present.
    Handles the envelope pattern: {"ok": False, "error": {...}}
    """
    if not isinstance(output, dict):
        return None

    # Check for error envelope
    if output.get("ok") is False and "error" in output:
        error = output["error"]
        if isinstance(error, dict):
            return {
                "code": error.get("code", "unknown"),
                "message": error.get("message", ""),
                "where": error.get("where", "tool"),
                "managed": error.get("managed", False),
            }

    return None

def _format_error_tail(raw_tail: str) -> str:
    tail = (raw_tail or "").strip()
    if not tail:
        return ""
    if tail.startswith("Error logs tail:"):
        return tail
    return f"Error logs tail:\n{tail}"

_INFO_LINE_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2} .*\\bINFO\\b")

def _extract_non_info_tail(text: str, max_chars: int) -> Optional[str]:
    if not text:
        return None
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if _INFO_LINE_RE.match(line):
            continue
        lines.append(line)
    if not lines:
        return None
    payload = "\n".join(lines).strip()
    if not payload:
        return None
    return payload[-max_chars:] if max_chars and max_chars > 0 else payload

def _block_tail(log_path: pathlib.Path, exec_id: Optional[str], *, max_chars: int, filter_info: bool) -> Optional[str]:
    blk = last_error_block(log_path, exec_id=exec_id)
    if not blk:
        return None
    text = (blk.get("text") or "").strip()
    if not text:
        return None
    if filter_info:
        return _extract_non_info_tail(text, max_chars)
    return text[-max_chars:] if max_chars and max_chars > 0 else text

def _select_exec_error_tail(outdir: pathlib.Path, exec_id: Optional[str], max_chars: int = 4000) -> Optional[str]:
    log_dir = outdir / "logs"
    tail = _block_tail(log_dir / "runtime.err.log", exec_id, max_chars=max_chars, filter_info=True)
    if tail:
        return tail
    return errors_log_tail(log_dir / "errors.log", exec_id=exec_id, max_chars=max_chars)

async def _build_program_run_items(
    *,
    envelope: Dict[str, Any],
    contract: Dict[str, Any],
    tool_id: str,
    params: Dict[str, Any],
    tool_call_id: Optional[str],
    logger: AgentLogger,
    errors_log_tail: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    contract = contract or {}
    envelope_error = envelope.get("error")
    artifacts = envelope.get("artifacts") or envelope.get("contract") or []
    items: List[Dict[str, Any]] = []

    run_outdir = pathlib.Path(envelope.get("outdir") or "")
    result_path = run_outdir / (envelope.get("result_filename") or "")
    # call_record_* currently unused in v2

    produced_ids: List[str] = []
    for i, a in enumerate(artifacts):
        artifact_id = (a.get("resource_id") or "").removeprefix("artifact:")
        if not artifact_id or artifact_id not in contract:
            if artifact_id:
                logger.log(f"[react.exec-inline] Skipping artifact {artifact_id} not in contract", level="WARNING")
            continue
        produced_ids.append(artifact_id)
        artifact_kind = a.get("type")
        value = a.get("output")
        contract_entry = contract.get(artifact_id) or {}
        artifact_stats = None
        if isinstance(value, dict):
            view = ArtifactView.from_output(value)
            rel_path, mime_hint = view.path, (view.mime or "")
            if rel_path:
                artifact_stats = analyze_write_tool_output(
                    file_path=str(rel_path),
                    mime=mime_hint,
                    output_dir=run_outdir if run_outdir.exists() else None,
                    artifact_id=artifact_id,
                )
                logger.log(
                    f"[react.exec-inline] artifact={artifact_id} path={rel_path} stats={artifact_stats}",
                    level="INFO",
                )

        summary_txt = ""
        summary_timing_ms = None
        if (artifact_stats or {}).get("write_error"):
            summary_txt = f"ERROR: {artifact_stats.get('write_error')}"

        item_error = a.get("error") if a.get("error") else None
        if artifact_stats and artifact_stats.get("write_error"):
            item_error = item_error or {
                "code": "artifact_invalid",
                "message": artifact_stats.get("write_error"),
                "where": "artifact_analysis",
                "managed": True,
            }
        item_status = "error" if item_error or value is None else "success"
        item = {
            "artifact_id": artifact_id,
            "artifact_kind": artifact_kind,
            "tool_id": tool_id,
            "output": value,
            "summary": summary_txt or "",
            "status": item_status,
            "error": item_error,
            "tool_call_id": tool_call_id,
            "filepath": contract_entry.get("filename"),
        }
        items.append(item)

    missing_ids = [
        k for k in contract.keys()
        if k not in set(produced_ids)
    ]
    for i, artifact_id in enumerate(missing_ids, len(items)):
        contract_entry = contract.get(artifact_id) or {}
        err_code = "missing_artifact"
        err_msg = f"Artifact '{artifact_id}' not produced"
        err_where = "execution"
        if isinstance(envelope_error, dict):
            err_code = envelope_error.get("error") or envelope_error.get("code") or err_code
            err_msg = envelope_error.get("description") or envelope_error.get("message") or err_msg
            err_where = envelope_error.get("where") or err_where
        missing_error = {
            "code": err_code,
            "message": err_msg,
            "where": err_where,
            "managed": True,
            "details": {"missing_artifact": artifact_id},
        }
        if errors_log_tail:
            missing_error["details"]["errors_log_tail"] = errors_log_tail
            err_tail = _format_error_tail(errors_log_tail)
            if err_tail:
                base_msg = (missing_error.get("message") or "").strip()
                missing_error["message"] = f"{base_msg}\n{err_tail}" if base_msg else err_tail
        items.append({
            "artifact_id": artifact_id,
            "artifact_kind": contract_entry.get("type"),
            "tool_id": tool_id,
            "output": None,
            "summary": f"MISSING: {artifact_id}",
            "status": "error",
            "error": missing_error,
            "tool_call_id": tool_call_id,
            "filepath": contract_entry.get("filename"),
        })

    return items, None, None


async def _execute_tool_in_memory(
    *,
    tool_execution_context: Dict[str, Any],
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    tool_manager: ToolSubsystem,
    logger: AgentLogger,
    tool_call_id: Optional[str] = None,
    artifacts_contract: list[dict] = None,
    exec_streamer: Optional[Any] = None,

) -> Dict[str, Any]:
    """
    Execute a single tool in this process using io_tools.tool_call.
    Now includes error capture and better summaries.
    """
    from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools

    tool_id = tool_execution_context["tool_id"]

    params = tool_execution_context.get("params") or {}
    call_reason = tool_execution_context.get("reasoning") or f"ReAct call: {tool_id}"
    call_signature = tool_execution_context.get("call_signature")
    param_bindings_for_summary = tool_execution_context.get("param_bindings_for_summary")
    tool_doc_for_summary = tool_execution_context.get("tool_doc_for_summary")

    if tools_insights.is_exec_tool(tool_id=tool_id):
        from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import run_exec_tool, build_exec_output_contract
        artifacts_spec = params.get("contract")
        code = params.get("code") or ""
        timeout_s = params.get("timeout_s") or 600
        exec_id = _safe_exec_id(tool_execution_context.get("exec_id") or tool_call_id)
        if exec_streamer:
            try:
                exec_streamer.set_execution_id(exec_id)
            except Exception:
                logger.log("[react.exec] Failed to set execution_id for exec tool widget" + traceback.format_exc(), level="WARNING")
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
        err_tail = _select_exec_error_tail(outdir, exec_id)

        # we might want to add this to timeline
        project_log = envelope.get("project_log")

        exec_workdir = pathlib.Path(envelope.get("workdir") or "")
        codefile_path = exec_workdir / "main.py"
        codefile = codefile_path.read_text(encoding="utf-8") if codefile_path.exists() else None
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

        if not envelope.get("ok", False):
            err_obj = envelope.get("error") or {"code": "exec_failed", "message": "unknown", "where": "exec"}
            msg = (err_obj.get("description") or err_obj.get("message") or "").strip()
            summary = f"ERROR [{err_obj.get('error') or err_obj.get('code')}] at exec: {msg}"[:300]
            if err_tail:
                err_obj = dict(err_obj)
                err_obj.setdefault("details", {})
                if isinstance(err_obj["details"], dict):
                    err_obj["details"]["errors_log_tail"] = err_tail

                err_tail_msg = _format_error_tail(err_tail)
                base_msg = (err_obj.get("message") or err_obj.get("description") or "").strip()
                err_obj["message"] = f"{base_msg}\n{err_tail_msg}" if base_msg else err_tail_msg
            items, _call_record_abs, _call_record_rel = await _build_program_run_items(
                envelope=envelope,
                contract=contract or {},
                tool_id=tool_id,
                params=params,
                tool_call_id=tool_call_id,
                logger=logger,
                errors_log_tail=err_tail,
            )
            return {
                "status": "error",
                "output": envelope,
                "summary": summary,
                "items": items,
                "tool_call_id": tool_call_id,
                "error": err_obj,
            }

        artifacts = envelope.get("contract") or []
        items = []
        items, _call_record_abs, _call_record_rel = await _build_program_run_items(
            envelope=envelope,
            contract=contract or {},
            tool_id=tool_id,
            params=params,
            tool_call_id=tool_call_id,
            logger=logger,
            errors_log_tail=err_tail,
        )

        first = items[0] if items else {}
        return {
            "status": "success",
            "output": first.get("output"),
            "summary": first.get("summary", ""),
            "items": items,
            "tool_call_id": tool_call_id,
        }

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
    exception_occurred = False
    exception_info = None
    try:
        await agent_io_tools.tool_call(
            fn=fn,
            params=params,
            call_reason=call_reason,
            tool_id=tool_id,
        )
    except Exception as e:
        exception_occurred = True
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

    # Check for error in output
    error_info = _extract_error_from_output(output) or exception_info
    status = "error" if error_info else "success"

    # Build summary with centralized logic
    mime_hint = ""
    if error_info:
        # Error-focused summary, short and consistent
        code = error_info.get("code", "unknown")
        where = error_info.get("where", "tool")
        msg = (error_info.get("message") or "").strip()
        if len(msg) > 200:
            msg = msg[:197] + "..."
        summary = f"ERROR [{code}] at {where}: {msg}"
    else:
        summary = ""

    # inputs/call_record_* not used in v2

    # write tool artifact_stats not used in v2

    item = {
        "artifact_id": artifacts_contract[0].get("name") if artifacts_contract else None,
        "artifact_kind": artifacts_contract[0].get("kind") if artifacts_contract else None,
        "tool_id": tool_id,
        "output": output,
        "summary": summary or "",
        "status": status,
        "tool_call_id": tool_call_id,
    }
    if error_info:
        item["error"] = error_info

    result = {
        "status": status,
        "items": [item],
    }
    return result


async def execute_tool_in_isolation(
        runtime_ctx: RuntimeCtx,
        tool_execution_context: Dict[str, Any],
        workdir: pathlib.Path,
        outdir: pathlib.Path,
        tool_manager: ToolSubsystem,
        logger: AgentLogger,
        tool_call_id: Optional[str] = None,
        artifacts_contract: Optional[list[dict]] = None,
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

    # Check for error in output (envelope pattern)
    error_info = _extract_error_from_output(output) or subprocess_error
    status = "error" if error_info else "success"

    mime_hint = ""
    # Build summary with centralized logic
    if error_info:
        code = error_info.get("code", "unknown")
        where = error_info.get("where", "subprocess")
        msg = (error_info.get("message") or "").strip()
        if len(msg) > 200:
            msg = msg[:197] + "..."
        summary = f"ERROR [{code}] at {where}: {msg}"
        summary_timing_ms = None
    else:
        summary = ""
        summary_timing_ms = None

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

    artifact_stats = None
    if tools_insights.is_write_tool(tool_id):
        file_path = ""
        if isinstance(output, dict):
            file_path = ArtifactView.from_output(output).path
        elif isinstance(output, str):
            file_path = output.strip()
        artifact_stats = analyze_write_tool_output(
            file_path=file_path,
            mime=mime_hint or tools_insights.default_mime_for_write_tool(tool_id),
            output_dir=outdir,
            artifact_id=artifacts_contract[0].get("name") if artifacts_contract else None,
        )

    item = {
        "artifact_id": artifacts_contract[0].get("name") if artifacts_contract else None,
        "artifact_kind": artifacts_contract[0].get("kind") if artifacts_contract else None,
        "tool_call_id": tool_call_id,
        "tool_id": tool_id,
        "output": output,
        "summary": summary or "",
    }
    if error_info:
        item["error"] = error_info
    result = {
        "status": status,
        "items": [item],
    }

    return result

async def execute_tool(
        runtime_ctx: RuntimeCtx,
        tool_execution_context: Dict[str, Any],
        workdir: pathlib.Path,
        outdir: pathlib.Path,
        tool_manager: ToolSubsystem,
        logger: AgentLogger,
        tool_call_id: Optional[str] = None,
        artifacts_contract: list[dict] = None,
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

    if runtime_override == "none":
        return await _execute_tool_in_memory(
            tool_execution_context=tool_execution_context,
            workdir=workdir,
            outdir=outdir,
            tool_manager=tool_manager,
            logger=logger,
            tool_call_id=tool_call_id,
            artifacts_contract=artifacts_contract,
            exec_streamer=exec_streamer,
        )

    if not tools_insights.should_isolate_tool_execution(tool_id) and runtime_override is None:
        return await _execute_tool_in_memory(
            tool_execution_context=tool_execution_context,
            workdir=workdir,
            outdir=outdir,
            tool_manager=tool_manager,
            logger=logger,
            tool_call_id=tool_call_id,
            artifacts_contract=artifacts_contract,
            exec_streamer=exec_streamer,
        )

    return await execute_tool_in_isolation(runtime_ctx=runtime_ctx,
                                           tool_execution_context=tool_execution_context,
                                           workdir=workdir,
                                           outdir=outdir,
                                           tool_manager=tool_manager,
                                           logger=logger,
                                           artifacts_contract=artifacts_contract,
                                           tool_call_id=tool_call_id,
                                           isolation_override=runtime_override)
