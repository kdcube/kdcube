# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/execution.py

import base64
import json
import mimetypes
import pathlib
import re
import shutil
import traceback
import uuid
from typing import Any, Dict, Optional, Callable, Awaitable, Tuple, List

from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem
from kdcube_ai_app.apps.chat.sdk.tools.backends.summary_backends import build_summary_for_tool_output
from kdcube_ai_app.apps.chat.sdk.util import _to_jsonable
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import build_portable_spec
from kdcube_ai_app.apps.chat.sdk.runtime.logging_utils import errors_log_tail

from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _InProcessRuntime
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext

import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights

def _safe_label(s: str, *, maxlen: int = 96) -> str:
    """Filesystem-safe label from tool_id."""
    lbl = re.sub(r"[^A-Za-z0-9_.-]+", "_", s or "")
    return lbl[:maxlen] if len(lbl) > maxlen else lbl

def _safe_exec_id(val: Optional[str]) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", (val or "")).strip("_")
    return safe or uuid.uuid4().hex[:12]

_SUMMARY_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_SUMMARY_DOC_MIME = {"application/pdf"}
_SUMMARY_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_SUMMARY_MAX_DOC_BYTES = 10 * 1024 * 1024

def _coerce_summary_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)

def _guess_mime_from_path(path: str) -> Optional[str]:
    if not path:
        return None
    guess, _ = mimetypes.guess_type(path)
    return guess

def _prepare_summary_artifact(artifact: Dict[str, Any], base_dir: Optional[pathlib.Path]) -> Optional[Dict[str, Any]]:
    if not isinstance(artifact, dict):
        return None

    art_type = (artifact.get("type") or "").strip().lower()
    output = artifact.get("output")
    filename = artifact.get("filename") or (output.get("filename") if isinstance(output, dict) else None)
    mime = (artifact.get("mime") or (output.get("mime") if isinstance(output, dict) else None) or "").strip().lower()
    if not mime:
        guess = _guess_mime_from_path((output or {}).get("path") if isinstance(output, dict) else "")
        mime = (guess or "").strip().lower()

    if art_type == "inline":
        if isinstance(output, dict):
            text = output.get("text")
            if text is None:
                text = output.get("value")
        else:
            text = output
        return {
            "type": "inline",
            "mime": mime or None,
            "text": _coerce_summary_text(text),
        }

    if art_type != "file":
        return None

    if isinstance(output, dict):
        relpath = output.get("path") or ""
        text = output.get("text")
    else:
        relpath = ""
        text = output

    file_path = None
    if relpath:
        p = pathlib.Path(relpath)
        if not p.is_absolute() and base_dir:
            p = base_dir / relpath
        file_path = p

    size_bytes = None
    base64_data = None
    read_error = None

    if file_path and file_path.exists() and file_path.is_file():
        try:
            size_bytes = file_path.stat().st_size
            if mime in _SUMMARY_IMAGE_MIME or mime in _SUMMARY_DOC_MIME:
                limit = _SUMMARY_MAX_IMAGE_BYTES if mime in _SUMMARY_IMAGE_MIME else _SUMMARY_MAX_DOC_BYTES
                if size_bytes <= limit:
                    base64_data = base64.b64encode(file_path.read_bytes()).decode("ascii")
                else:
                    read_error = f"file too large to attach ({size_bytes} bytes > {limit})"
        except Exception as exc:
            read_error = f"file read failed: {exc}"
    elif relpath:
        read_error = "file not found on disk"

    return {
        "type": "file",
        "mime": mime or None,
        "text": _coerce_summary_text(text),
        "base64": base64_data,
        "filename": filename,
        "path": str(file_path) if file_path else relpath,
        "size_bytes": size_bytes,
        "read_error": read_error,
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


async def _build_program_run_items(
    *,
    envelope: Dict[str, Any],
    contract: Dict[str, Any],
    tool_id: str,
    params: Dict[str, Any],
    call_reason: str,
    call_signature: Optional[str],
    param_bindings_for_summary: Optional[Any],
    tool_doc_for_summary: Optional[str],
    codefile: Optional[str],
    use_llm_summary: bool,
    llm_service: Optional[Any],
    context: ReactContext,
    tool_call_id: Optional[str],
    logger: AgentLogger,
    errors_log_tail: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    contract = contract or {}
    envelope_error = envelope.get("error")
    artifacts = envelope.get("artifacts") or []
    items: List[Dict[str, Any]] = []

    run_outdir = pathlib.Path(envelope.get("outdir") or "")
    result_path = run_outdir / (envelope.get("result_filename") or "")
    call_record_abs = str(result_path) if result_path.exists() else None
    call_record_rel = None

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
        summary_artifact = _prepare_summary_artifact(a, run_outdir)
        contract_entry = contract.get(artifact_id) or {}

        ctx = (
            f"[Call Reason]\n{call_reason}\n"
            f"[artifact: {artifact_id}]\n"
            "IMPORTANT: Summarize ONLY this artifact. "
            "Do NOT assess or mention other artifacts in the contract; "
            "they will be summarized separately.\n"
            f"[In contract]\n{contract_entry}\n"
            f"[Code file]\n```python\n{codefile or ''}\n```\n[]"
        )
        summary_obj, summary_txt = await build_summary_for_tool_output(
            tool_id=tool_id,
            output=value,
            summary_artifact=summary_artifact,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            call_reason=ctx,
            tool_inputs=params,
            call_signature=call_signature,
            param_bindings_for_summary=param_bindings_for_summary,
            tool_doc_for_summary=tool_doc_for_summary,
            bundle_id=context.bundle_id,
            timezone=context.timezone,
            structured=False,
        )

        item = {
            "artifact_id": artifact_id,
            "artifact_type": contract_entry.get("format"),
            "artifact_kind": artifact_kind,
            "tool_id": tool_id,
            "output": value,
            "summary": summary_txt or "",
            "inputs": params,
            "call_record_rel": call_record_rel,
            "call_record_abs": call_record_abs,
            "error": None,
            "content_inventorization": a.get("content_inventorization"),
            "tool_call_id": tool_call_id,
            "tool_call_item_index": i,
        }
        items.append(item)

    from kdcube_ai_app.apps.chat.sdk.runtime.solution.contracts import SERVICE_LOG_SLOT
    missing_ids = [
        k for k in contract.keys()
        if k not in set(produced_ids) and k != SERVICE_LOG_SLOT
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
        items.append({
            "artifact_id": artifact_id,
            "artifact_type": contract_entry.get("format"),
            "artifact_kind": contract_entry.get("type"),
            "tool_id": tool_id,
            "output": None,
            "summary": f"MISSING: {artifact_id}",
            "inputs": params,
            "call_record_rel": call_record_rel,
            "call_record_abs": call_record_abs,
            "error": missing_error,
            "content_inventorization": None,
            "tool_call_id": tool_call_id,
            "tool_call_item_index": i,
        })

    return items, call_record_abs, call_record_rel


async def _execute_tool_in_memory(
    *,
    context: ReactContext,
    tool_execution_context: Dict[str, Any],
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    tool_manager: ToolSubsystem,
    codegen: "CodegenRunner",
    logger: AgentLogger,
    solution_gen_stream: Callable[..., Awaitable[Dict[str, Any]]],
    tool_call_id: Optional[str] = None,
    use_llm_summary: bool = False,
    llm_service: Optional[Any] = None,
    artifacts_contract: list[dict] = None,

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
        artifacts_spec = params.get("artifacts")
        code = params.get("code") or ""
        timeout_s = params.get("timeout_s") or 600
        contract, normalized_artifacts, err = build_exec_output_contract(artifacts_spec)
        if err:
            return {
                "status": "error",
                "output": None,
                "summary": f"Exec tool requires valid artifacts spec: {err.get('message')}",
                "inputs": params,
                "call_record_rel": None,
                "call_record_abs": None,
                "items": [],
                "tool_call_id": tool_call_id,
                "error": {
                    "code": err.get("code", "invalid_artifacts"),
                    "message": err.get("message", "Invalid artifacts spec"),
                    "where": "exec_execution",
                    "managed": True,
                },
            }
        if not code:
            return {
                "status": "error",
                "output": None,
                "summary": "Exec tool requires non-empty 'code' parameter",
                "inputs": params,
                "call_record_rel": None,
                "call_record_abs": None,
                "items": [],
                "tool_call_id": tool_call_id,
                "error": {
                    "code": "missing_parameters",
                    "message": "Parameter 'code' is required for exec tool",
                    "where": "exec_execution",
                    "managed": True,
                },
            }
        exec_id = _safe_exec_id(tool_call_id)
        envelope = await run_exec_tool(
            tool_manager=tool_manager,
            logger=logger,
            output_contract=contract,
            code=code,
            artifacts=normalized_artifacts or [],
            timeout_s=int(timeout_s),
            outdir=outdir,
            workdir=workdir,
            exec_id=exec_id,
        )
        err_tail = errors_log_tail(outdir / "logs" / "errors.log", exec_id=exec_id)
        project_log = envelope.get("project_log")
        if isinstance(project_log, dict) and project_log:
            context.add_event(
                kind="exec_proj_log",
                data={
                    "tool_call_id": tool_call_id,
                    "result_filename": envelope.get("result_filename"),
                    "project_log": project_log,
                },
            )
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
                base_msg = (err_obj.get("message") or err_obj.get("description") or "").strip()
                err_obj["message"] = f"{base_msg}\n{err_tail}" if base_msg else err_tail
            items, call_record_abs, call_record_rel = await _build_program_run_items(
                envelope=envelope,
                contract=contract or {},
                tool_id=tool_id,
                params=params,
                call_reason=call_reason,
                call_signature=call_signature,
                param_bindings_for_summary=param_bindings_for_summary,
                tool_doc_for_summary=tool_doc_for_summary,
                codefile=codefile,
                use_llm_summary=use_llm_summary,
                llm_service=llm_service,
                context=context,
                tool_call_id=tool_call_id,
                logger=logger,
                errors_log_tail=err_tail,
            )
            return {
                "status": "error",
                "output": envelope,
                "summary": summary,
                "inputs": params,
                "call_record_rel": call_record_rel,
                "call_record_abs": call_record_abs,
                "items": items,
                "tool_call_id": tool_call_id,
                "error": err_obj,
            }

        artifacts = envelope.get("artifacts") or []
        items = []
        items, call_record_abs, call_record_rel = await _build_program_run_items(
            envelope=envelope,
            contract=contract or {},
            tool_id=tool_id,
            params=params,
            call_reason=call_reason,
            call_signature=call_signature,
            param_bindings_for_summary=param_bindings_for_summary,
            tool_doc_for_summary=tool_doc_for_summary,
            codefile=codefile,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            context=context,
            tool_call_id=tool_call_id,
            logger=logger,
            errors_log_tail=err_tail,
        )

        first = items[0] if items else {}
        return {
            "status": "success",
            "output": first.get("output"),
            "summary": first.get("summary", ""),
            "inputs": first.get("inputs", params),
            "call_record_rel": first.get("call_record_rel"),
            "call_record_abs": first.get("call_record_abs"),
            "items": items,
            "tool_call_id": tool_call_id,
        }

    if tools_insights.is_codegen_tool(tool_id):
        # Codegen tool must be called directly
        from kdcube_ai_app.apps.chat.sdk.tools.codegen_tools import run_codegen_tool
        allowed_plugins = ["security_tools", "llm_tools", "generic_tools", "codegen_tools"]
        contract = params.get("output_contract") or {}
        instruction = params.get("instruction") or ""
        if not contract or not instruction:
            return {
                "status": "error",
                "output": None,
                "summary": "Codegen tool requires 'output_contract' and 'instruction' parameters",
                "inputs": params,
                "call_record_rel": None,
                "call_record_abs": None,
                "items": [],
                "tool_call_id": tool_call_id,
                "error": {
                    "code": "missing_parameters",
                    "message": "Both 'output_contract' and 'instruction' parameters are required for codegen tool",
                    "where": "codegen_execution",
                    "managed": True,
                },
            }
        exec_id = _safe_exec_id(tool_call_id)
        dest_dir = outdir / "executed_programs"
        dest_dir.mkdir(parents=True, exist_ok=True)
        i = 0
        while (dest_dir / f"{_safe_label(tool_id)}_{i}_main.py").exists():
            i += 1
        envelope = await run_codegen_tool(codegen=codegen,
                                          context=context,
                                          logger=logger,
                                          allowed_plugins=allowed_plugins,
                                          output_contract=contract,
                                          instruction=instruction,
                                          reasoning=call_reason,
                                          outdir=outdir,
                                          workdir=workdir,
                                          solution_gen_stream=solution_gen_stream,
                                          exec_id=exec_id,
                                          invocation_idx=i)
        err_tail = errors_log_tail(outdir / "logs" / "errors.log", exec_id=exec_id)
        project_log = envelope.get("project_log")
        if isinstance(project_log, dict) and project_log:
            context.add_event(
                kind="code_proj_log",
                data={
                    "tool_call_id": tool_call_id,
                    "run_id": envelope.get("run_id"),
                    "result_filename": envelope.get("result_filename"),
                    "project_log": project_log,
                },
            )
        codegen_workdir = pathlib.Path(envelope.get("workdir") or "")
        codegen_codefile_path = codegen_workdir / "main.py"
        codegen_codefile = codegen_codefile_path.read_text(encoding="utf-8") if codegen_codefile_path.exists() else None
        try:
            if codegen_codefile_path.exists():
                dest_main = dest_dir / f"{_safe_label(tool_id)}_{i}_main.py"
                shutil.copy2(codegen_codefile_path, dest_main)
                logger.log(f"[react.exec] main.py preserved as {dest_main.relative_to(outdir)}")
        except Exception as e:
            logger.log(f"[react.exec] Failed to preserve main.py: {e}", level="WARNING")
        if not envelope.get("ok", False):
            err = envelope.get("error") or {"code": "codegen_failed", "message": "unknown", "where": "codegen"}
            msg = (err.get("description") or err.get("message") or "").strip()
            summary = f"ERROR [{err.get('error') or err.get('code')}] at codegen: {msg}"[:300]
            if err_tail:
                err = dict(err)
                err.setdefault("details", {})
                if isinstance(err["details"], dict):
                    err["details"]["errors_log_tail"] = err_tail
                base_msg = (err.get("message") or err.get("description") or "").strip()
                err["message"] = f"{base_msg}\n{err_tail}" if base_msg else err_tail
            items, call_record_abs, call_record_rel = await _build_program_run_items(
                envelope=envelope,
                contract=contract,
                tool_id=tool_id,
                params=params,
                call_reason=call_reason,
                call_signature=call_signature,
                param_bindings_for_summary=param_bindings_for_summary,
                tool_doc_for_summary=tool_doc_for_summary,
                codefile=codegen_codefile,
                use_llm_summary=use_llm_summary,
                llm_service=llm_service,
                context=context,
                tool_call_id=tool_call_id,
                logger=logger,
                errors_log_tail=err_tail,
            )
            return {
                "status": "error",
                "output": envelope,
                "summary": summary,
                "inputs": params,
                "call_record_rel": call_record_rel,
                "call_record_abs": call_record_abs,
                "items": items,
                "tool_call_id": tool_call_id,
                "error": err,
            }

        artifacts = envelope.get("artifacts") or []
        items = []
        items, call_record_abs, call_record_rel = await _build_program_run_items(
            envelope=envelope,
            contract=contract,
            tool_id=tool_id,
            params=params,
            call_reason=call_reason,
            call_signature=call_signature,
            param_bindings_for_summary=param_bindings_for_summary,
            tool_doc_for_summary=tool_doc_for_summary,
            codefile=codegen_codefile,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            context=context,
            tool_call_id=tool_call_id,
            logger=logger,
            errors_log_tail=err_tail,
        )

        # legacy compatibility: mirror first item
        first = items[0] if items else {}
        return {
            "status": "success",
            "output": first.get("output"),
            "summary": first.get("summary", ""),
            "inputs": first.get("inputs", params),
            "call_record_rel": first.get("call_record_rel"),
            "call_record_abs": first.get("call_record_abs"),
            "items": items,
            "tool_call_id": tool_call_id,
        }

    # bootstrap once via subsystem (sets OUTDIR/WORKDIR; service bindings; comm)
    await tool_manager.prebind_for_in_memory(workdir=workdir,
                                             outdir=outdir,
                                             logger=logger,
                                             bootstrap_env=True)

    # resolve "<alias>.<fn>"
    try:
        alias, fn_name = tool_id.split(".", 1)
    except ValueError:
        return {
            "status": "error",
            "output": None,
            "summary": f"Bad tool_id: {tool_id}",
            "error": {
                "code": "invalid_tool_id",
                "message": f"Tool ID '{tool_id}' is not in format 'alias.function'",
                "where": "execution",
                "managed": True,
            }
        }

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
    idx_path = outdir / "tool_calls_index.json"
    if not idx_path.exists():
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

    try:
        idx_map = json.loads(idx_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.log(f"[react.exec] Service error. Malformed index of tool calls: {e}", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": f"Bad tool calls index JSON: {e}",
            "error": {
                "code": "malformed_tool_calls_index",
                "message": str(e),
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
    content_inventorization = None
    if error_info:
        # Error-focused summary, short and consistent
        code = error_info.get("code", "unknown")
        where = error_info.get("where", "tool")
        msg = (error_info.get("message") or "").strip()
        if len(msg) > 200:
            msg = msg[:197] + "..."
        summary = f"ERROR [{code}] at {where}: {msg}"
    else:
        summary_obj, summary = await build_summary_for_tool_output(
            tool_id=tool_id,
            output=output,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            call_reason=call_reason,
            tool_inputs=params,
            call_signature=call_signature,
            param_bindings_for_summary=param_bindings_for_summary,
            tool_doc_for_summary=tool_doc_for_summary,
            bundle_id=context.bundle_id,
            timezone=context.timezone,
            structured=False,
        )

        if summary_obj is None or isinstance(summary_obj, str):
            summary = summary_obj or summary
        else:
            if hasattr(summary_obj, "to_md"):
                try:
                    summary = summary_obj.to_md()
                except Exception:
                    summary = str(summary_obj)
            else:
                summary = str(summary_obj)
            content_inventorization = _to_jsonable(summary_obj)

    inputs = (payload.get("in") or {}).get("params") or {}
    call_record_rel = last_rel
    call_record_abs = str(call_path)

    item = {
        "artifact_id": artifacts_contract[0].get("name") if artifacts_contract else None,
        "artifact_kind": artifacts_contract[0].get("kind") if artifacts_contract else None,
        "artifact_type": artifacts_contract[0].get("type") if artifacts_contract else None,
        "tool_id": tool_id,
        "output": output,
        "summary": summary or "",
        "inputs": inputs,
        "call_record_rel": call_record_rel,
        "call_record_abs": call_record_abs,
        "tool_call_id": tool_call_id,
        "tool_call_item_index": 0,
    }
    if error_info:
        item["error"] = error_info
    if content_inventorization is not None:
        item["content_inventorization"] = content_inventorization

    result = {
        "status": status,
        "items": [item],
        "call_record_rel": call_record_rel,
        "call_record_abs": call_record_abs,
    }
    return result


async def execute_tool_in_isolation(
        tool_execution_context: Dict[str, Any],
        context: ReactContext,
        workdir: pathlib.Path,
        outdir: pathlib.Path,
        tool_manager: ToolSubsystem,
        logger: AgentLogger,
        tool_call_id: Optional[str] = None,
        artifacts_contract: Optional[list[dict]] = None,
        use_llm_summary: bool = False,
        llm_service: Optional[Any] = None,
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
    call_signature = tool_execution_context.get("call_signature")
    param_bindings_for_summary = tool_execution_context.get("param_bindings_for_summary")
    tool_doc_for_summary= tool_execution_context.get("tool_doc_for_summary")

    logger.log(f"[react.exec] Executing {tool_id} via iso-runtime")

    runtime = _InProcessRuntime(logger)

    # alias maps + modules provided by subsystem
    tool_modules = tool_manager.tool_modules_tuple_list()

    # alias maps (must match main program executor)
    runtime_globals = tool_manager.export_runtime_globals()
    alias_to_dyn, alias_to_file = tool_manager.get_alias_maps()

    # portable spec for child to rebind services
    spec = build_portable_spec(svc=tool_manager.svc, chat_comm=tool_manager.comm)
    portable_spec_json = spec.to_json()

    # communicator spec (redis relay etc.)
    comm_spec = getattr(tool_manager.comm, "_export_comm_spec_for_runtime", lambda: {})()

    globals_for_runtime = {
        "CONTRACT": {},
        "COMM_SPEC": comm_spec,
        "PORTABLE_SPEC_JSON": portable_spec_json,
        **runtime_globals,  # TOOL_ALIAS_MAP, TOOL_MODULE_FILES, BUNDLE_SPEC, RAW_TOOL_SPECS
    }

    isolation = tools_insights.tool_isolation(tool_id=tool_id)
    # Unless there's no third-party blackboxed tools, and the tools are all verified, it is safe. TODO.
    # Tool isolation settings TODO define on the level of Tool Subsystem and simply inherit here as a part of runtime_globals.
    # Per-call overrides for sandbox behavior; _run_subprocess will read these via env
    # We need docker when filesystem isolation and/or network isolation are needed. solely network isolation can be done with "local_network"
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
    idx_path = outdir / "tool_calls_index.json"
    if not idx_path.exists():
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

    try:
        idx_map = json.loads(idx_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.log(f"[react.exec] Service error. Malformed index of tool calls: {e}", level="ERROR")
        return {
            "status": "error",
            "output": None,
            "summary": f"Bad tool calls index JSON: {e}",
            "error": {
                "code": "malformed_tool_calls_index",
                "message": str(e),
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

    content_inventorization = None
    # Build summary with centralized logic
    if error_info:
        code = error_info.get("code", "unknown")
        where = error_info.get("where", "subprocess")
        msg = (error_info.get("message") or "").strip()
        if len(msg) > 200:
            msg = msg[:197] + "..."
        summary = f"ERROR [{code}] at {where}: {msg}"
    else:
        summary_obj, summary = await build_summary_for_tool_output(
            tool_id=tool_id,
            output=output,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            call_reason=call_reason,
            tool_inputs=params,
            call_signature=call_signature,
            param_bindings_for_summary=param_bindings_for_summary,
            tool_doc_for_summary=tool_doc_for_summary,
            bundle_id=context.bundle_id,
            timezone=context.timezone,
            structured=False,
        )

        if summary_obj is None or isinstance(summary_obj, str):
            summary = summary_obj or summary
        else:
            if hasattr(summary_obj, "to_md"):
                try:
                    summary = summary_obj.to_md()
                except Exception:
                    summary = str(summary_obj)
            else:
                summary = str(summary_obj)
            content_inventorization = _to_jsonable(summary_obj)

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

    inputs = (payload.get("in") or {}).get("params") or {}
    call_record_rel = last_rel
    call_record_abs = str(call_path)

    tool_call_group_id = tool_execution_context.get("tool_call_group_id") or uuid.uuid4().hex[:12]

    item = {
        "artifact_id": artifacts_contract[0].get("name") if artifacts_contract else None,
        "artifact_kind": artifacts_contract[0].get("kind") if artifacts_contract else None,
        "artifact_type": artifacts_contract[0].get("type") if artifacts_contract else None,
        "tool_call_id": tool_call_id,
        "tool_id": tool_id,
        "output": output,
        "summary": summary or "",
        "inputs": inputs,
        "call_record_rel": call_record_rel,
        "call_record_abs": call_record_abs,
        "tool_call_group_id": tool_call_group_id,
        "tool_call_item_index": 0,
    }
    if error_info:
        item["error"] = error_info
    if content_inventorization is not None:
        item["content_inventorization"] = content_inventorization
    result = {
        "status": status,
        "items": [item],
        "call_record_rel": call_record_rel,
        "call_record_abs": call_record_abs,
    }

    return result

async def execute_tool(
    tool_execution_context: Dict[str, Any],
    context: ReactContext,
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    tool_manager: ToolSubsystem,
    logger: AgentLogger,
    solution_gen_stream: Callable[..., Awaitable[Dict[str, Any]]] = None,
    tool_call_id: Optional[str] = None,
    use_llm_summary: bool = False,
    llm_service: Optional[Any] = None,
    codegen_runner: "CodegenRunner" = None,
    artifacts_contract: list[dict] = None,

) -> Dict[str, Any]:
    """
    Unified entry with error capture and optional LLM summarization.
      - if tool_id ∈ IN_MEMORY_TOOL_IDS → run in-process (io_tools.tool_call)
      - else → run in sandbox subprocess (preserves main.py into executed_programs/)
    """
    tool_id = tool_execution_context.get("tool_id") or ""

    if not tools_insights.should_isolate_tool_execution(tool_id):
        return await _execute_tool_in_memory(
            tool_execution_context=tool_execution_context,
            context=context,
            workdir=workdir,
            outdir=outdir,
            tool_manager=tool_manager,
            logger=logger,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            codegen=codegen_runner,
            tool_call_id=tool_call_id,
            artifacts_contract=artifacts_contract,
            solution_gen_stream=solution_gen_stream
        )

    # fs_isolated = bool(tool_execution_context.get("fs_isolated", False))
    # net_isolated = bool(tool_execution_context.get("net_isolated", False))
    # fall back to sandbox subprocess path
    return await execute_tool_in_isolation(tool_execution_context=tool_execution_context,
                                           context=context,
                                           workdir=workdir,
                                           outdir=outdir,
                                           tool_manager=tool_manager,
                                           logger=logger,
                                           use_llm_summary=use_llm_summary,
                                           llm_service=llm_service,
                                           artifacts_contract=artifacts_contract,
                                           tool_call_id=tool_call_id)
