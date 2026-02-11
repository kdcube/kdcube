# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

import json
import pathlib
import time

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.artifacts import (
    build_artifact_meta_block,
    build_artifact_binary_block,
    build_artifact_view,
    normalize_physical_path,
    detect_edit,
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
            "notes": root_notes,
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
        },
        workdir=pathlib.Path(state["workdir"]),
        outdir=pathlib.Path(state["outdir"]),
        tool_manager=react.tools_subsystem,
        logger=react.log,
        tool_call_id=tool_call_id,
        exec_streamer=exec_streamer,
    )

    items = tool_response.get("items") or []
    call_error = tool_response.get("error") if isinstance(tool_response, dict) else None
    if call_error:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="tool_call_error",
            message=(call_error.get("message") if isinstance(call_error, dict) else str(call_error)) or "tool call failed",
            extra={"tool_id": tool_id, "error": call_error},
        )
    produced_ids: List[str] = []
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
        elif isinstance(output, (dict, list)):
            value = {"text": json.dumps(output, ensure_ascii=False, indent=2), "mime": "application/json"}
        elif isinstance(output, str):
            value = {"text": output, "mime": "text/plain"}
        else:
            value = {"text": "" if output is None else str(output), "mime": "text/plain"}
        artifact_view = build_artifact_view(
            turn_id=turn_id,
            is_current=True,
            artifact_id=artifact_id,
            tool_id=tool_id,
            value=value,
            summary=summary,
            artifact_kind=artifact_kind,
            visibility=visibility,
        description=root_notes,
            channel=None,
            sources_used=[],
            inputs=final_params,
            call_record_rel=tool_response.get("call_record_rel"),
            call_record_abs=tool_response.get("call_record_abs"),
            error=tr.get("error"),
            content_lineage=content_lineage,
            tool_call_id=tool_call_id,
            tool_call_item_index=None,
            artifact_stats=None,
        )
        if tr.get("error"):
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="tool_result_error",
                message=(tr.get("error") or {}).get("message") if isinstance(tr.get("error"), dict) else str(tr.get("error")),
                extra={"tool_id": tool_id, "artifact_id": artifact_id, "error": tr.get("error")},
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
        produced_ids.append(artifact_id)

        phys_path = ""
        rel_path = ""
        if visibility == "external":
            if phys_path_override or rel_path_override:
                phys_path = phys_path_override
                rel_path = rel_path_override
                if rewritten_override:
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

        if tools_insights.is_search_tool(tool_id) and isinstance(output, list):
            srcs = [r for r in output if isinstance(r, dict) and r.get("url")]
            if srcs:
                pending = state.setdefault("pending_sources", [])
                pending.extend(srcs)
        elif tools_insights.is_fetch_uri_content_tool(tool_id):
            rows: List[Dict[str, Any]] = []
            data = output
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = None
            if isinstance(data, dict):
                for url, payload in data.items():
                    if not isinstance(url, str) or not url.strip() or not isinstance(payload, dict):
                        continue
                    row = {"url": url.strip()}
                    content = (payload.get("content") or "").strip()
                    if content:
                        row["content"] = content
                    title = payload.get("title")
                    if isinstance(title, str) and title.strip():
                        row["title"] = title.strip()
                    for meta_key in (
                        "published_time_iso",
                        "modified_time_iso",
                        "fetched_time_iso",
                        "date_method",
                        "date_confidence",
                        "status",
                        "content_length",
                        "mime",
                        "base64",
                        "size_bytes",
                        "fetch_status",
                    ):
                        if meta_key in payload:
                            row[meta_key] = payload[meta_key]
                    rows.append(row)
            if rows:
                pending = state.setdefault("pending_sources", [])
                pending.extend(rows)

    state["last_tool_result"] = items
    state["last_tool_id"] = tool_id
    return state
