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
from typing import Any, Dict, Optional, Callable, Awaitable, Tuple, List

from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem
from kdcube_ai_app.apps.chat.sdk.tools.backends.summary_backends import build_summary_for_tool_output

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import build_portable_spec
from kdcube_ai_app.apps.chat.sdk.runtime.logging_utils import errors_log_tail, last_error_block

from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _InProcessRuntime
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.artifact_analysis import (
    analyze_write_tool_output,
    prepare_summary_artifact,
    prepare_write_tool_summary_artifact,
)

import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.tools.ctx_tools import SourcesUsedStore

def _safe_label(s: str, *, maxlen: int = 96) -> str:
    """Filesystem-safe label from tool_id."""
    lbl = re.sub(r"[^A-Za-z0-9_.-]+", "_", s or "")
    return lbl[:maxlen] if len(lbl) > maxlen else lbl

def _safe_exec_id(val: Optional[str]) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", (val or "")).strip("_")
    return safe or uuid.uuid4().hex[:12]

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

def _build_sources_used_hint(
        *,
        context: ReactContext,
        artifact_name: Optional[str] = None,
        filename: Optional[str] = None,
) -> str:
    try:
        store = SourcesUsedStore()
        store.load()
        sids = store.get_sids(artifact_name=artifact_name, filename=filename)
    except Exception:
        sids = []
    if not sids:
        return ""
    rows = []
    for src in (context.sources_pool or []):
        if not isinstance(src, dict):
            continue
        sid = src.get("sid")
        if isinstance(sid, int) and sid in sids:
            title = (src.get("title") or "").strip()
            url = (src.get("url") or src.get("local_path") or "").strip()
            text = (src.get("text") or "").strip()
            parts = [f"S{sid}"]
            if title:
                parts.append(title)
            if url:
                parts.append(url)
            if text:
                parts.append(text[:160])
            rows.append(" - " + " | ".join(parts))
    if not rows:
        return ""
    return "[Sources used]\n" + "\n".join(rows) + "\n"

def _build_sources_used_hint_from_sids(
        *,
        context: ReactContext,
        sids: List[int],
) -> str:
    if not sids:
        return ""
    rows = []
    for src in (context.sources_pool or []):
        if not isinstance(src, dict):
            continue
        sid = src.get("sid")
        if isinstance(sid, int) and sid in sids:
            title = (src.get("title") or "").strip()
            url = (src.get("url") or src.get("local_path") or "").strip()
            text = (src.get("text") or "").strip()
            parts = [f"S{sid}"]
            if title:
                parts.append(title)
            if url:
                parts.append(url)
            if text:
                parts.append(text[:160])
            rows.append(" - " + " | ".join(parts))
    if not rows:
        return ""
    return "[Sources used]\n" + "\n".join(rows) + "\n"

def _sids_from_llm_artifact_name(
        *,
        artifact_name: Any,
        context: ReactContext,
) -> List[int]:
    try:
        store = SourcesUsedStore()
        store.load()
    except Exception:
        return []
    names: List[str] = []
    if isinstance(artifact_name, dict):
        names.extend([str(k).strip() for k in artifact_name.keys() if str(k).strip()])
    elif isinstance(artifact_name, str) and artifact_name.strip():
        parsed = None
        try:
            parsed = json.loads(artifact_name)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            names.extend([str(k).strip() for k in parsed.keys() if str(k).strip()])
        else:
            names.append(artifact_name.strip())
    sids: List[int] = []
    seen: set[int] = set()
    for name in names:
        for sid in store.get_sids(artifact_name=name) or []:
            if isinstance(sid, int) and sid not in seen:
                seen.add(sid)
                sids.append(sid)
    return sids


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
    artifacts = envelope.get("out_artifacts_spec") or envelope.get("artifacts") or []
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
        summary_artifact = prepare_summary_artifact(a, run_outdir)
        contract_entry = contract.get(artifact_id) or {}

        def _file_stats(artifact: Dict[str, Any]) -> Dict[str, Any]:
            output = artifact.get("output") if isinstance(artifact.get("output"), dict) else {}
            filename = (output.get("filename") or "").strip()
            path = (output.get("path") or "").strip()
            mime = (output.get("mime") or artifact.get("mime") or "").strip()
            size_bytes = None
            if path:
                p = pathlib.Path(path)
                if not p.is_absolute():
                    p = run_outdir / p
                if p.exists() and p.is_file():
                    try:
                        size_bytes = p.stat().st_size
                    except Exception:
                        size_bytes = None
            elif filename:
                p = run_outdir / filename
                if p.exists() and p.is_file():
                    try:
                        size_bytes = p.stat().st_size
                    except Exception:
                        size_bytes = None
            return {
                "artifact_id": (artifact.get("resource_id") or "").removeprefix("artifact:"),
                "filename": filename or path or "",
                "mime": mime,
                "size_bytes": size_bytes,
            }

        sibling_stats = []
        for sibling in artifacts:
            sib_id = (sibling.get("resource_id") or "").removeprefix("artifact:")
            if not sib_id or sib_id == artifact_id:
                continue
            sibling_stats.append(_file_stats(sibling))

        sibling_lines = []
        for stat in sibling_stats:
            parts = [f"artifact_id={stat.get('artifact_id') or 'unknown'}"]
            filename = stat.get("filename") or ""
            if filename:
                parts.append(f"filename={filename}")
            size_bytes = stat.get("size_bytes")
            if isinstance(size_bytes, int):
                parts.append(f"size_bytes={size_bytes}")
            mime = stat.get("mime") or ""
            if mime:
                parts.append(f"mime={mime}")
            sibling_lines.append("- " + "; ".join(parts))

        ctx = (
            f"[Call Reason]\n{call_reason}\n"
            f"[artifact: {artifact_id}]\n"
            "CRITICAL: Summarize ONLY this artifact. "
            "Do NOT assess, compare, or mention other artifacts; "
            "do NOT claim missing siblings. They are summarized separately.\n"
            + (("[Sibling artifacts (context only)]\n" + "\n".join(sibling_lines) + "\n") if sibling_lines else "")
            + _build_sources_used_hint(context=context, artifact_name=artifact_id, filename=contract_entry.get("filename"))
            + f"[In contract]\n{contract_entry}\n"
            f"[Code file]\n```python\n{codefile or ''}\n```\n[]"
        )
        artifact_stats = None
        if isinstance(value, dict):
            rel_path = value.get("path") or value.get("filename") or ""
            mime_hint = value.get("mime") or ""
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
                ctx += f"[Artifact stats]\n{artifact_stats}\n"

        summary_txt = ""
        summary_obj = None
        summary_timing_ms = None
        if not (artifact_stats or {}).get("write_error"):
            t0 = time.perf_counter()
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
            summary_timing_ms = int((time.perf_counter() - t0) * 1000)
        else:
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
            "artifact_type": contract_entry.get("format"),
            "artifact_kind": artifact_kind,
            "tool_id": tool_id,
            "output": value,
            "summary": summary_txt or "",
            "status": item_status,
            "summary_timing_ms": summary_timing_ms,
            "inputs": params,
            "call_record_rel": call_record_rel,
            "call_record_abs": call_record_abs,
            "error": item_error,
            "tool_call_id": tool_call_id,
            "tool_call_item_index": i,
        }
        if artifact_stats:
            item["artifact_stats"] = artifact_stats
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
            err_tail = _format_error_tail(errors_log_tail)
            if err_tail:
                base_msg = (missing_error.get("message") or "").strip()
                missing_error["message"] = f"{base_msg}\n{err_tail}" if base_msg else err_tail
        items.append({
            "artifact_id": artifact_id,
            "artifact_type": contract_entry.get("format"),
            "artifact_kind": contract_entry.get("type"),
            "tool_id": tool_id,
            "output": None,
            "summary": f"MISSING: {artifact_id}",
            "status": "error",
            "inputs": params,
            "call_record_rel": call_record_rel,
            "call_record_abs": call_record_abs,
            "error": missing_error,
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
    exec_streamer: Optional[Any] = None,
    codegen_streamer: Optional[Any] = None,

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
        artifacts_spec = params.get("out_artifacts_spec")
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
            "inputs": params,
            "call_record_rel": None,
            "call_record_abs": None,
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
        # exec_id = _safe_exec_id(tool_execution_context.get("exec_id") or tool_call_id)
        # if exec_streamer:
        #     try:
        #         exec_streamer.set_execution_id(exec_id)
        #     except Exception:
        #         logger.log("[react.exec] Failed to set execution_id for exec tool widget" + traceback.format_exc(), level="WARNING")
        exec_t0 = time.perf_counter()
        envelope = await run_exec_tool(
            tool_manager=tool_manager,
            logger=logger,
            output_contract=contract,
            code=code,
            out_artifacts_spec=normalized_artifacts or [],
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

                err_tail_msg = _format_error_tail(err_tail)
                base_msg = (err_obj.get("message") or err_obj.get("description") or "").strip()
                err_obj["message"] = f"{base_msg}\n{err_tail_msg}" if base_msg else err_tail_msg
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

        artifacts = envelope.get("out_artifacts_spec") or envelope.get("artifacts") or []
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
        exec_id = _safe_exec_id(tool_execution_context.get("exec_id") or tool_call_id)
        if codegen_streamer:
            try:
                codegen_streamer.set_execution_id(exec_id)
            except Exception as ex:
                logger.log("Failed to set execution_id for codegen tool widget" + traceback.format_exc(), level="WARNING")
            prog_name = (params.get("prog_name") or "").strip()
            if prog_name:
                try:
                    await codegen_streamer.emit_program_name(prog_name)
                except Exception:
                    logger.log("[react.codegen] Failed to emit program name", level="WARNING")
            try:
                await codegen_streamer.emit_contract(contract)
            except Exception:
                logger.log("[react.codegen] Failed to emit codegen contract", level="WARNING")
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
                                          skills=params.get("skills"),
                                          outdir=outdir,
                                          workdir=workdir,
                                          solution_gen_stream=solution_gen_stream,
                                          exec_id=exec_id,
                                          invocation_idx=i,
                                          attachments=context.show_artifact_attachments,
                                          emit_delta_fn=getattr(tool_manager, "comm", None) and tool_manager.comm.delta,
                                          timeline_agent="solver.codegen",
                                          json_streamer=codegen_streamer)
        if codegen_streamer:
            try:
                timings = envelope.get("timings") or {}
                codegen_streamer.set_timings(
                    codegen_ms=timings.get("codegen_ms"),
                    exec_ms=timings.get("exec_ms"),
                )
                await codegen_streamer.emit_status(
                    status="done" if envelope.get("ok", False) else "error",
                    error=envelope.get("error") if not envelope.get("ok", False) else None,
                )
            except Exception:
                logger.log("[react.codegen] Failed to emit codegen status", level="WARNING")
        err_tail = _select_exec_error_tail(outdir, exec_id)
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
                err_tail_msg = _format_error_tail(err_tail)
                base_msg = (err.get("message") or err.get("description") or "").strip()
                err["message"] = f"{base_msg}\n{err_tail_msg}" if base_msg else err_tail_msg
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

        artifacts = envelope.get("out_artifacts_spec") or envelope.get("artifacts") or []
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
    mime_hint = ""
    if error_info:
        # Error-focused summary, short and consistent
        code = error_info.get("code", "unknown")
        where = error_info.get("where", "tool")
        msg = (error_info.get("message") or "").strip()
        if len(msg) > 200:
            msg = msg[:197] + "..."
        summary = f"ERROR [{code}] at {where}: {msg}"
        summary_timing_ms = None
    else:
        surrogate_text = ""
        mime_hint = ""
        if tools_insights.is_write_tool(tool_id):
            surrogate_text, mime_hint = context._surrogate_from_writer_inputs(
                tool_id=tool_id,
                inputs=params or {},
            )
        summary_artifact = prepare_write_tool_summary_artifact(
            tool_id=tool_id,
            output=output,
            inputs=params,
            base_dir=outdir,
            surrogate_text=surrogate_text,
            mime_hint=mime_hint,
        )
        t0 = time.perf_counter()
        call_reason_with_sources = call_reason + _build_sources_used_hint(
            context=context,
            artifact_name=artifacts_contract[0].get("name") if artifacts_contract else None,
            filename=pathlib.Path(params.get("path") or "").name if isinstance(params.get("path"), str) else None,
        )
        summary_obj, summary = await build_summary_for_tool_output(
            tool_id=tool_id,
            output=output,
            summary_artifact=summary_artifact,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            call_reason=call_reason_with_sources,
            tool_inputs=params,
            call_signature=call_signature,
            param_bindings_for_summary=param_bindings_for_summary,
            tool_doc_for_summary=tool_doc_for_summary,
            bundle_id=context.bundle_id,
            timezone=context.timezone,
            structured=False,
        )
        summary_timing_ms = int((time.perf_counter() - t0) * 1000)

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

    inputs = (payload.get("in") or {}).get("params") or {}
    call_record_rel = last_rel
    call_record_abs = str(call_path)

    artifact_stats = None
    if tools_insights.is_write_tool(tool_id):
        file_path = ""
        if isinstance(output, dict) and isinstance(output.get("path"), str):
            file_path = output.get("path", "").strip()
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
        "artifact_type": artifacts_contract[0].get("type") if artifacts_contract else None,
        "tool_id": tool_id,
        "output": output,
        "summary": summary or "",
        "status": status,
        "inputs": inputs,
        "call_record_rel": call_record_rel,
        "call_record_abs": call_record_abs,
        "tool_call_id": tool_call_id,
        "tool_call_item_index": 0,
    }
    if artifact_stats:
        item["artifact_stats"] = artifact_stats
    if error_info:
        item["error"] = error_info

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
    call_signature = tool_execution_context.get("call_signature")
    param_bindings_for_summary = tool_execution_context.get("param_bindings_for_summary")
    tool_doc_for_summary= tool_execution_context.get("tool_doc_for_summary")

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

    isolation = isolation_override or tools_insights.tool_isolation(tool_id=tool_id)
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
        surrogate_text = ""
        mime_hint = ""
        if tools_insights.is_write_tool(tool_id):
            surrogate_text, mime_hint = context._surrogate_from_writer_inputs(
                tool_id=tool_id,
                inputs=params or {},
            )
        summary_artifact = prepare_write_tool_summary_artifact(
            tool_id=tool_id,
            output=output,
            inputs=params,
            base_dir=outdir,
            surrogate_text=surrogate_text,
            mime_hint=mime_hint,
        )
        t0 = time.perf_counter()
        call_reason_with_sources = call_reason
        if tools_insights.is_write_tool(tool_id):
            call_reason_with_sources += _build_sources_used_hint(
                context=context,
                artifact_name=artifacts_contract[0].get("name") if artifacts_contract else None,
                filename=pathlib.Path(params.get("path") or "").name if isinstance(params.get("path"), str) else None,
            )
        elif tool_id == "llm_tools.generate_content_llm":
            sids = _sids_from_llm_artifact_name(
                artifact_name=params.get("artifact_name"),
                context=context,
            )
            call_reason_with_sources += _build_sources_used_hint_from_sids(context=context, sids=sids)
        summary_obj, summary = await build_summary_for_tool_output(
            tool_id=tool_id,
            output=output,
            summary_artifact=summary_artifact,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            call_reason=call_reason_with_sources,
            tool_inputs=params,
            call_signature=call_signature,
            param_bindings_for_summary=param_bindings_for_summary,
            tool_doc_for_summary=tool_doc_for_summary,
            bundle_id=context.bundle_id,
            timezone=context.timezone,
            structured=False,
        )
        summary_timing_ms = int((time.perf_counter() - t0) * 1000)

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

    artifact_stats = None
    if tools_insights.is_write_tool(tool_id):
        file_path = ""
        if isinstance(output, dict) and isinstance(output.get("path"), str):
            file_path = output.get("path", "").strip()
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
        "artifact_type": artifacts_contract[0].get("type") if artifacts_contract else None,
        "tool_call_id": tool_call_id,
        "tool_id": tool_id,
        "output": output,
        "summary": summary or "",
        "summary_timing_ms": summary_timing_ms,
        "inputs": inputs,
        "call_record_rel": call_record_rel,
        "call_record_abs": call_record_abs,
        "tool_call_group_id": tool_call_group_id,
        "tool_call_item_index": 0,
    }
    if artifact_stats:
        item["artifact_stats"] = artifact_stats
    if error_info:
        item["error"] = error_info
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
    exec_streamer: Optional[Any] = None,
    codegen_streamer: Optional[Any] = None,

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
            solution_gen_stream=solution_gen_stream,
            exec_streamer=exec_streamer,
            codegen_streamer=codegen_streamer,
        )

    if not tools_insights.should_isolate_tool_execution(tool_id) and runtime_override is None:
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
            solution_gen_stream=solution_gen_stream,
            exec_streamer=exec_streamer,
            codegen_streamer=codegen_streamer,
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
                                           tool_call_id=tool_call_id,
                                           isolation_override=runtime_override)
