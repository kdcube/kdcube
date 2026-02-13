# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

import json
import pathlib

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.artifacts import (
    build_artifact_meta_block,
    build_artifact_binary_block,
    build_artifact_view,
    normalize_physical_path,
    detect_edit,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.artifact_analysis import (
    analyze_write_tool_output,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.execution import execute_tool
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
    host_artifact_file,
    emit_hosted_files,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.solution_workspace import (
    extract_code_file_paths,
    rehost_files_from_timeline,
)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights

TOOL_SPEC = None  # external tools are dynamic


async def handle_external_tool(*,
                               react: Any,
                               ctx_browser: Any,
                               state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = (tool_call.get("tool_id") or "").strip()
    root_notes = (last_decision.get("notes") or "").strip()

    base_params = tool_call.get("params") or {}
    visible_paths = None
    try:
        visible_paths = ctx_browser.timeline_visible_paths()
    except Exception:
        visible_paths = None
    final_params, content_lineage, violations = ctx_browser.bind_params_with_refs(
        base_params=base_params,
        tool_id=tool_id,
        visible_paths=visible_paths,
    )
    if violations:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.param_ref_not_visible",
            message="One or more ref: bindings are not visible",
            extra={"violations": violations, "tool_id": tool_id, "protocol_violation": True},
        )
        state["retry_decision"] = True
        return state

    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "params": tool_call.get("params") or {},
        },
    )

    # Normalize paths for rendering_tools.* (must be physical paths under current turn)
    if tool_id.startswith("rendering_tools."):
        path_val = final_params.get("path")
        if isinstance(path_val, str) and path_val.strip():
            turn_id = (ctx_browser.runtime_ctx.turn_id or "").strip()
            physical, rel, rewritten = normalize_physical_path(path_val, turn_id=turn_id)
            if "/attachments/" in physical:
                # writing to attachments is not allowed; rewrite to files/<name>
                rel = rel.split("/", 1)[-1] if rel else "output"
                physical = f"{turn_id}/files/{rel}"
                rewritten = True
            if rewritten and physical:
                final_params["path"] = physical
                if physical != path_val:
                    notice_block(
                        ctx_browser=ctx_browser,
                        tool_call_id=tool_call_id,
                        code="protocol_violation.path_rewritten",
                        message="Rendering tool path was rewritten to current turn files/.",
                        extra={"original": path_val, "rewritten": physical, "tool_id": tool_id},
                    )

    # If exec tool: normalize contract/code + rehost any referenced historical files before execution
    if tools_insights.is_exec_tool(tool_id):
        from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import (
            normalize_exec_contract_for_turn,
            rewrite_exec_code_paths,
        )
        turn_id = (ctx_browser.runtime_ctx.turn_id or "").strip()
        base_contract = final_params.get("contract")
        normalized_contract, contract_rewrites, contract_err = normalize_exec_contract_for_turn(
            base_contract, turn_id=turn_id
        )
        if contract_err:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.exec_contract_invalid",
                message=contract_err.get("message") or "Invalid exec contract",
                extra={"error": contract_err, "tool_id": tool_id, "protocol_violation": True},
            )
            state["retry_decision"] = True
            return state
        if contract_rewrites:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.exec_contract_rewritten",
                message="Exec contract filenames were rewritten to current turn files/ paths.",
                extra={"rewritten": contract_rewrites, "tool_id": tool_id},
            )
        if normalized_contract is not None:
            final_params["contract"] = normalized_contract

        code_txt = final_params.get("code")
        code_rewrites = []
        if isinstance(code_txt, str):
            rewritten_code, code_rewrites = rewrite_exec_code_paths(code_txt, turn_id=turn_id)
            if rewritten_code != code_txt:
                final_params["code"] = rewritten_code
        if code_rewrites:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.exec_code_rewritten",
                message="Exec code contained relative files/ or attachments/ paths; rewritten to current turn.",
                extra={"rewritten": code_rewrites, "tool_id": tool_id},
            )

        paths, rewritten_paths = extract_code_file_paths(
            final_params.get("code"), turn_id=turn_id
        ) if isinstance(final_params.get("code"), str) else ([], [])
        try:
            if ctx_browser.timeline:
                ctx_browser.timeline.write_local()
        except Exception:
            pass
        if rewritten_paths:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.exec_path_rewritten",
                message="Exec code referenced relative files/… paths; rewritten to current turn.",
                extra={"rewritten": rewritten_paths},
            )
            try:
                ctx_browser.log.log(
                    f"[react.v2] exec_path_rewritten: {rewritten_paths}",
                    level="WARNING",
                )
            except Exception:
                pass
        if paths:
            try:
                rehost = await rehost_files_from_timeline(
                    ctx_browser=ctx_browser,
                    paths=paths,
                    outdir=pathlib.Path(state["outdir"]),
                )
                if rehost.get("missing"):
                    notice_block(
                        ctx_browser=ctx_browser,
                        tool_call_id=tool_call_id,
                        code="tool_call_error.pre_exec_missing_paths",
                        message="Exec code referenced files that were not found in timeline.",
                        extra={"missing": rehost.get("missing"), "tool_id": tool_id},
                    )
                    from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.artifacts import build_tool_result_error_block
                    add_block(ctx_browser, build_tool_result_error_block(
                        turn_id=ctx_browser.runtime_ctx.turn_id,
                        tool_call_id=tool_call_id,
                        tool_id=tool_id,
                        code="pre_exec_missing_paths",
                        message="Exec code referenced files that were not found in timeline.",
                        details={"missing": rehost.get("missing")},
                    ))
                    state["retry_decision"] = True
                    state["last_tool_result"] = []
                    state["last_tool_id"] = tool_id
                    return state
            except Exception:
                pass

    exec_streamer = state.get("exec_code_streamer") if tools_insights.is_exec_tool(tool_id) else None
    tool_response = await execute_tool(
        runtime_ctx=ctx_browser.runtime_ctx,
        tool_execution_context={
            "tool_id": tool_id,
            "params": final_params,
            "reasoning": root_notes,
        },
        workdir=pathlib.Path(state["workdir"]),
        outdir=pathlib.Path(state["outdir"]),
        tool_manager=react.tools_subsystem,
        logger=react.log,
        tool_call_id=tool_call_id,
        exec_streamer=exec_streamer,
    )

    is_exec = tools_insights.is_exec_tool(tool_id)
    items = tool_response.get("items") if (isinstance(tool_response, dict) and is_exec) else None
    call_error = tool_response.get("call_error") if isinstance(tool_response, dict) else None
    def _strip_managed(err: Any) -> Any:
        if not isinstance(err, dict):
            return err
        cleaned = {k: v for k, v in err.items() if k != "managed"}
        details = cleaned.get("details")
        if isinstance(details, dict):
            if "managed" in details:
                details = {k: v for k, v in details.items() if k != "managed"}
            tool_err = details.get("tool_error")
            if isinstance(tool_err, dict) and "managed" in tool_err:
                tool_err = {k: v for k, v in tool_err.items() if k != "managed"}
                details["tool_error"] = tool_err
            cleaned["details"] = details
        return cleaned
    call_error = _strip_managed(call_error) if call_error else call_error
    report_text = (tool_response.get("report_text") or tool_response.get("summary") or "").strip()
    output = tool_response.get("output") if isinstance(tool_response, dict) else None
    summary = tool_response.get("summary") if isinstance(tool_response, dict) else ""
    tool_err = tool_response.get("error") if isinstance(tool_response, dict) else None
    if not is_exec:
        items = [
            {
                "artifact_id": tool_id,
                "output": output,
                "summary": summary or "",
                "error": tool_err,
            }
        ]
    if call_error and not is_exec:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="tool_call_error",
            message=(call_error.get("message") if isinstance(call_error, dict) else str(call_error)) or "tool call failed",
            extra={"tool_id": tool_id, "error": call_error},
        )
    if is_exec and report_text:
        add_block(ctx_browser, {
            "turn": ctx_browser.runtime_ctx.turn_id or "",
            "type": "react.tool.result",
            "call_id": tool_call_id,
            "mime": "text/markdown",
            "path": f"tc:{ctx_browser.runtime_ctx.turn_id}.tool_calls.{tool_call_id}.out.json" if ctx_browser.runtime_ctx.turn_id else "",
            "text": report_text,
        })
    for idx, tr in enumerate(items):
        if not isinstance(tr, dict):
            continue
        artifact_id = (tr.get("artifact_id") or f"{tool_id}_{idx}").strip()
        output = tr.get("output")
        artifact_kind = tr.get("artifact_kind") or "file"
        if tools_insights.is_search_tool(tool_id) or tools_insights.is_fetch_uri_content_tool(tool_id):
            artifact_kind = "file"
        visibility = "external" if (tools_insights.is_write_tool(tool_id) or tools_insights.is_exec_tool(tool_id)) else "internal"
        summary = tr.get("summary") or ""
        tr_error = _strip_managed(tr.get("error")) if tr.get("error") else None

        # If a write tool returns the {ok,error} envelope, use params.path for artifacts.
        if tools_insights.is_write_tool(tool_id):
            if isinstance(output, dict) and "ok" in output and "error" in output:
                if output.get("ok") is True and output.get("error") is None:
                    candidate = final_params.get("path")
                    if isinstance(candidate, str) and candidate.strip():
                        output = candidate.strip()
                elif output.get("ok") is False:
                    # Failed write: keep as internal to avoid emitting a bogus external file artifact.
                    visibility = "internal"
            elif output is None and tr_error is None:
                candidate = final_params.get("path")
                if isinstance(candidate, str) and candidate.strip():
                    output = candidate.strip()

        turn_id = (ctx_browser.runtime_ctx.turn_id or "")
        rel_path_override = ""
        phys_path_override = ""
        rewritten_override = False
        rewrite_original = ""

        value: Any = {}
        if isinstance(output, dict) and output.get("type") == "file":
            value = dict(output)
        elif tools_insights.is_write_tool(tool_id) and isinstance(output, str) and output.strip():
            raw_path = output.strip()
            candidate = raw_path
            try:
                p = pathlib.Path(candidate)
                if p.is_absolute():
                    try:
                        candidate = str(p.resolve().relative_to(pathlib.Path(state["outdir"]).resolve()))
                    except Exception:
                        candidate = p.name
            except Exception:
                pass
            phys_path_override, rel_path_override, rewritten_override = normalize_physical_path(candidate, turn_id=turn_id)
            if phys_path_override:
                value = phys_path_override
                rewrite_original = candidate
            else:
                value = candidate
        elif tools_insights.is_write_tool(tool_id):
            path_hint = final_params.get("path")
            if isinstance(path_hint, str) and path_hint.strip():
                value = {"type": "file", "path": path_hint.strip()}
        elif isinstance(output, (dict, list)):
            value = {"text": json.dumps(output, ensure_ascii=False, indent=2), "mime": "application/json"}
        elif isinstance(output, str):
            value = {"text": output, "mime": "text/plain"}
        else:
            value = {"text": "" if output is None else str(output), "mime": "text/plain"}

        artifact_stats = None
        if tools_insights.is_write_tool(tool_id):
            file_hint = ""
            if isinstance(output, dict) and isinstance(output.get("path"), str):
                file_hint = output.get("path") or ""
            elif isinstance(output, str):
                file_hint = output
            elif isinstance(final_params.get("path"), str):
                file_hint = final_params.get("path") or ""
            if file_hint:
                phys_hint, _, _ = normalize_physical_path(file_hint, turn_id=turn_id)
                file_for_stats = phys_hint or file_hint
                try:
                    artifact_stats = analyze_write_tool_output(
                        file_path=file_for_stats,
                        mime=tools_insights.default_mime_for_write_tool(tool_id),
                        output_dir=pathlib.Path(state["outdir"]),
                        artifact_id=artifact_id,
                    )
                except Exception:
                    artifact_stats = None
                if isinstance(artifact_stats, dict) and artifact_stats.get("write_error") and not tr_error:
                    tr_error = {
                        "code": "artifact_invalid",
                        "message": artifact_stats.get("write_error"),
                        "where": "artifact_analysis",
                    }
        artifact_view = build_artifact_view(
            turn_id=turn_id,
            is_current=True,
            artifact_id=artifact_id,
            tool_id=tool_id,
            value=value,
            summary=summary,
            artifact_kind=artifact_kind,
            visibility=visibility,
            description="",
            channel=None,
            sources_used=[],
            inputs=final_params,
            call_record_rel=tool_response.get("call_record_rel"),
            call_record_abs=tool_response.get("call_record_abs"),
            error=tr_error,
            content_lineage=content_lineage,
            tool_call_id=tool_call_id,
            tool_call_item_index=None,
            artifact_stats=artifact_stats,
        )
        if tr_error and not is_exec:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="tool_result_error",
                message=(tr_error or {}).get("message") if isinstance(tr_error, dict) else str(tr_error),
                extra={"tool_id": tool_id, "artifact_id": artifact_id, "error": tr_error},
            )
        hosted = []
        if visibility == "external":
            hosted = await host_artifact_file(
                hosting_service=react.hosting_service,
                comm=react.comm,
                runtime_ctx=ctx_browser.runtime_ctx,
                artifact=artifact_view.raw,
                outdir=pathlib.Path(state["outdir"]),
            )
            should_emit = tools_insights.is_exec_tool(tool_id) or tools_insights.is_write_tool(tool_id)
            await emit_hosted_files(
                hosting_service=react.hosting_service,
                hosted=hosted,
                should_emit=should_emit,
            )

        phys_path = ""
        rel_path = ""
        if visibility == "external":
            if phys_path_override or rel_path_override:
                phys_path = phys_path_override
                rel_path = rel_path_override
                if rewritten_override and rewrite_original and phys_path and rewrite_original != phys_path:
                    notice_block(
                        ctx_browser=ctx_browser,
                        tool_call_id=tool_call_id,
                        code="protocol_violation.path_rewritten",
                        message="Artifact path contained a turn/files prefix; rewritten to current-turn relative path.",
                        extra={"original": rewrite_original, "normalized": phys_path},
                    )
            else:
                artifact_rel = (artifact_view.path or (artifact_view.raw.get("value") or {}).get("path") or artifact_id or "").strip()
                tr_path = (tr.get("filepath") or "").strip()
                if tr_path:
                    artifact_rel = tr_path
                phys_path, rel_path, rewritten = normalize_physical_path(artifact_rel, turn_id=turn_id)
                if rewritten:
                    original_path = (artifact_view.path or (artifact_view.raw.get("value") or {}).get("path") or artifact_id or "")
                    if phys_path and original_path and original_path != phys_path:
                        notice_block(
                            ctx_browser=ctx_browser,
                            tool_call_id=tool_call_id,
                            code="protocol_violation.path_rewritten",
                            message="Artifact path contained a turn/files prefix; rewritten to current-turn relative path.",
                            extra={"original": original_path, "normalized": phys_path},
                        )
        artifact_path = f"fi:{turn_id}.files/{rel_path}" if (turn_id and rel_path and visibility == "external") else f"tc:{turn_id}.tool_calls.{tool_call_id}.out.json"
        physical_path = phys_path if (turn_id and rel_path and visibility == "external") else ""
        edited = detect_edit(
            timeline=getattr(ctx_browser, "timeline", None),
            artifact_path=artifact_path if artifact_path.startswith("fi:") else "",
            tool_call_id=tool_call_id,
        )
        meta_block = build_artifact_meta_block(
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            artifact=artifact_view.raw,
            artifact_path=artifact_path,
            physical_path=physical_path,
            edited=edited,
        )
        add_block(ctx_browser, meta_block)

        mime = (artifact_view.mime or (artifact_view.raw.get("value") or {}).get("mime") or "").strip().lower()
        if visibility == "external" and (mime.startswith("image/") or mime == "application/pdf") and physical_path:
            abs_path = pathlib.Path(state["outdir"]) / physical_path
            bin_block = build_artifact_binary_block(
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                artifact_path=artifact_path,
                abs_path=abs_path,
                mime=mime,
            )
            if bin_block:
                add_block(ctx_browser, bin_block)
        if isinstance(value, dict) and isinstance(value.get("text"), str) and value.get("text").strip():
            mime_out = value.get("mime") or "text/plain"
            add_block(ctx_browser, {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": mime_out,
                "path": artifact_path,
                "text": value.get("text"),
            })

        if tools_insights.is_search_tool(tool_id):
            data = output
            if isinstance(data, dict) and "ret" in data:
                data = data.get("ret")
            if isinstance(data, list):
                srcs = [r for r in data if isinstance(r, dict) and r.get("url")]
                if srcs:
                    pending = state.setdefault("pending_sources", [])
                    pending.extend(srcs)
        elif tools_insights.is_fetch_uri_content_tool(tool_id):
            data = output
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = None
            if isinstance(data, dict) and "ret" in data:
                data = data.get("ret")
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict) and r.get("url")]
                if rows:
                    pending = state.setdefault("pending_sources", [])
                    pending.extend(rows)

    state["last_tool_result"] = items
    state["last_tool_id"] = tool_id
    return state
