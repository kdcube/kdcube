# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

import json
import pathlib
import time

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_artifact_meta_block,
    build_artifact_binary_block,
    build_artifact_view,
    build_tool_result_error_block,
    normalize_physical_path,
    physical_path_to_logical_path,
    detect_edit,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifact_analysis import (
    analyze_write_tool_output,
)
from kdcube_ai_app.apps.chat.sdk.runtime.execution import execute_tool
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for, resolve_artifact_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
    host_artifact_file,
    emit_hosted_files,
    tc_result_path,
    enrich_artifact_file_metadata,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import (
    extract_code_file_paths,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.sources import (
    build_sources_pool_items_stats,
    ensure_rendering_assets,
    merge_sources_pool_for_attachment_rows,
    merge_sources_pool_for_file_rows,
    merge_sources_pool_with_map,
    _bump_sources_pool_next_sid,
)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.tools.content_type import is_text_mime_type
from kdcube_ai_app.apps.chat.sdk.util import normalize_artifact_visibility

DEFAULT_VISIBLE_BINARY_BYTES = 10 * 1024 * 1024
def _positive_int(value: Any) -> int:
    try:
        out = int(value)
    except Exception:
        return 0
    return out if out > 0 else 0


def _auto_binary_visibility_limit(runtime_ctx: Any) -> Dict[str, Any]:
    read_cap = _positive_int(getattr(runtime_ctx, "read_visible_max_bytes", None)) or DEFAULT_VISIBLE_BINARY_BYTES
    session = getattr(runtime_ctx, "session", None)
    keep_images = _positive_int(getattr(session, "cache_truncation_keep_recent_images", None))
    if session is not None and getattr(session, "cache_truncation_keep_recent_images", None) == 0:
        return {
            "bytes": 0,
            "source": "cache_truncation_keep_recent_images",
            "read_visible_max_bytes": read_cap,
            "cache_truncation_keep_recent_images": 0,
        }
    b64_sum = _positive_int(getattr(session, "cache_truncation_max_image_pdf_b64_sum", None))
    b64_raw_cap = (b64_sum * 3) // 4 if b64_sum else 0
    candidates = [read_cap]
    if b64_raw_cap:
        candidates.append(b64_raw_cap)
    return {
        "bytes": min(candidates),
        "source": "min(read_visible_max_bytes, cache_truncation_max_image_pdf_b64_sum_as_bytes)" if b64_raw_cap else "read_visible_max_bytes",
        "read_visible_max_bytes": read_cap,
        "cache_truncation_max_image_pdf_b64_sum": b64_sum or None,
        "cache_truncation_keep_recent_images": keep_images or None,
    }


def _should_attach_binary_to_prompt(*, runtime_ctx: Any, abs_path: pathlib.Path) -> tuple[bool, Dict[str, Any]]:
    try:
        size_bytes = abs_path.stat().st_size
    except Exception:
        return True, {}
    limit = _auto_binary_visibility_limit(runtime_ctx)
    cap = int(limit.get("bytes") or 0)
    if cap <= 0 or size_bytes > cap:
        return False, {
            "multimodal_status": "too_large_for_visible_context",
            "size_bytes": size_bytes,
            "visible_image_limit_bytes": cap,
            "visible_image_limit_source": limit.get("source"),
            "read_visible_max_bytes": limit.get("read_visible_max_bytes"),
            "cache_truncation_max_image_pdf_b64_sum": limit.get("cache_truncation_max_image_pdf_b64_sum"),
            "recover_with": "request a smaller screenshot/viewport, downsample/crop with exec, or inspect with react.read only if under byte caps",
        }
    return True, {
        "visible_image_limit_bytes": cap,
        "visible_image_limit_source": limit.get("source"),
    }


def _format_sources_pool_path(sids: List[int]) -> str:
    sids = sorted({int(s) for s in (sids or []) if isinstance(s, int) and s > 0})
    if not sids:
        return ""
    ranges: List[tuple[int, int]] = []
    start = prev = sids[0]
    for sid in sids[1:]:
        if sid == prev + 1:
            prev = sid
            continue
        ranges.append((start, prev))
        start = prev = sid
    ranges.append((start, prev))
    parts = [str(a) if a == b else f"{a}-{b}" for a, b in ranges]
    return f"so:sources_pool[{', '.join(parts)}]"


def _shape_of(value: Any, *, depth: int = 0, max_depth: int = 3) -> Any:
    if depth >= max_depth:
        if isinstance(value, str):
            return f"str[{len(value)}]"
        if isinstance(value, list):
            return f"list[{len(value)}]"
        if isinstance(value, dict):
            return f"dict[{len(value)}]"
        return type(value).__name__
    if isinstance(value, dict):
        out = {}
        for key, child in list(value.items())[:12]:
            out[str(key)] = _shape_of(child, depth=depth + 1, max_depth=max_depth)
        if len(value) > 12:
            out["..."] = f"+{len(value) - 12} keys"
        return {"type": f"dict[{len(value)}]", "fields": out}
    if isinstance(value, list):
        out: Dict[str, Any] = {"type": f"list[{len(value)}]"}
        if value:
            out["sample"] = _shape_of(value[0], depth=depth + 1, max_depth=max_depth)
        if len(value) > 1:
            out["more_items"] = len(value) - 1
        return out
    if isinstance(value, str):
        return f"str[{len(value)}]"
    return type(value).__name__


def _items_stats_for_output(output: Any) -> Dict[str, Any]:
    data = output
    if isinstance(data, dict) and "ret" in data:
        data = data.get("ret")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return {}
    if not isinstance(data, list):
        return {}
    source_like = [
        r for r in data
        if isinstance(r, dict) and (r.get("sid") is not None or r.get("url") or r.get("content") is not None)
    ]
    if source_like and len(source_like) == len(data):
        return build_sources_pool_items_stats(source_like)
    dict_items = [r for r in data if isinstance(r, dict)]
    keys = sorted({str(k) for row in dict_items[:50] for k in row.keys()}) if dict_items else []
    return {
        "kind": "items",
        "items_count": len(data),
        "item_type": type(data[0]).__name__ if data else "",
        "item_keys": keys,
        "sample_shape": _shape_of(data[0]) if data else None,
    }


def _extract_tool_source_rows(output: Any) -> List[Dict[str, Any]]:
    data = output
    if isinstance(data, dict) and "ret" in data:
        data = data.get("ret")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("url")]


def _looks_like_declared_file(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    return bool(
        row.get("path")
        or row.get("physical_path")
        or row.get("local_path")
        or row.get("artifact_path")
        or row.get("logical_path")
        or row.get("hosted_uri")
        or row.get("rn")
        or row.get("key")
    )


def _declared_file_rows(output: Any) -> List[Dict[str, Any]]:
    """
    Extract explicitly declared deliverable files from a tool result.

    The result must opt in with the strict marker:
      {"artifact_type": "files", "files": [...]}

    This marker is the file-hosting contract for external tool results.
    """
    data = output
    if isinstance(data, dict) and "ret" in data:
        data = data.get("ret")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return []
    if not isinstance(data, dict):
        return []

    if str(data.get("artifact_type") or "").strip() != "files":
        return []
    candidate = data.get("files")
    if not isinstance(candidate, list):
        return []
    return [dict(row) for row in candidate if _looks_like_declared_file(row)]


def _declared_files_to_tool_items(
    *,
    output: Any,
    tool_id: str,
    default_summary: str = "",
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for idx, row in enumerate(_declared_file_rows(output)):
        filename = str(row.get("filename") or "").strip()
        raw_path = str(
            row.get("physical_path")
            or row.get("path")
            or row.get("local_path")
            or row.get("artifact_path")
            or row.get("logical_path")
            or ""
        ).strip()
        if not filename:
            filename = pathlib.PurePosixPath(raw_path).name or f"file_{idx + 1}"
        mime = str(row.get("mime") or row.get("mime_type") or "").strip() or "application/octet-stream"
        description = str(row.get("description") or row.get("summary") or filename or "").strip()
        value = {
            "type": "file",
            "path": raw_path,
            "filename": filename,
            "mime": mime,
            "description": description,
        }
        for key in (
            "hosted_uri",
            "rn",
            "key",
            "size",
            "size_bytes",
            "local_path",
            "physical_path",
            "logical_path",
            "artifact_path",
        ):
            if row.get(key) not in ("", None):
                value[key] = row[key]
        already_hosted = bool(
            row.get("hosted") is True
            or row.get("already_hosted") is True
            or ((row.get("hosted_uri") or row.get("rn") or row.get("key")) and not raw_path)
        )
        hosted_record = {
            "slot": str(row.get("slot") or row.get("artifact_id") or "").strip(),
            "key": row.get("key") or "",
            "filename": filename,
            "mime": mime,
            "size": row.get("size") if row.get("size") is not None else row.get("size_bytes"),
            "tool_id": tool_id,
            "description": description,
            "owner_id": row.get("owner_id") or "",
            "rn": row.get("rn") or "",
            "hosted_uri": row.get("hosted_uri") or "",
            "physical_path": row.get("physical_path") or raw_path,
        }
        artifact_id = str(
            row.get("artifact_id")
            or row.get("slot")
            or row.get("resource_id")
            or f"{tool_id}_file_{idx + 1}"
        ).strip()
        items.append(
            {
                "artifact_id": artifact_id,
                "output": value,
                "artifact_kind": "file",
                "summary": description or default_summary,
                "filepath": raw_path,
                "visibility": normalize_artifact_visibility(row.get("visibility"), default="external"),
                "already_hosted": already_hosted,
                "emitted": bool(row.get("emitted")),
                "hosted_record": hosted_record,
            }
        )
    return items


def _remap_tool_sources(
    *,
    ctx_browser: Any,
    rows: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[int]]:
    if not rows:
        return [], []
    prior = list(ctx_browser.sources_pool or [])
    merged, sid_map = merge_sources_pool_with_map(prior=prior, new=rows)
    if merged:
        ctx_browser.set_sources_pool(sources_pool=merged)
        _bump_sources_pool_next_sid(merged)

    from kdcube_ai_app.apps.chat.sdk.tools.citations import normalize_url, _get_physical_path

    def _key_for(row: Dict[str, Any]) -> str:
        phys = _get_physical_path(row)
        if phys:
            return f"local:{phys}"
        return normalize_url(row.get("url", ""))

    key_to_sid: Dict[str, int] = {}
    for r in merged:
        if not isinstance(r, dict):
            continue
        key = _key_for(r)
        if key:
            try:
                key_to_sid[key] = int(r.get("sid") or 0)
            except Exception:
                continue

    remapped: List[Dict[str, Any]] = []
    used_sids: List[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        new_row = dict(row)
        try:
            old_sid = int(row.get("sid") or 0)
        except Exception:
            old_sid = 0
        new_sid = sid_map.get(old_sid)
        if not new_sid:
            key = _key_for(row)
            new_sid = key_to_sid.get(key)
        if new_sid:
            new_row["sid"] = int(new_sid)
            used_sids.append(int(new_sid))
        remapped.append(new_row)
    return remapped, used_sids

TOOL_SPEC = None  # external tools are dynamic


def _extract_exec_code_from_state(state: Dict[str, Any]) -> str:
    exec_streamer = state.get("exec_code_streamer")
    if exec_streamer:
        try:
            code_txt = exec_streamer.get_code() or ""
            if isinstance(code_txt, str) and code_txt:
                return code_txt
        except Exception:
            pass

    packet = state.get("last_decision_raw")
    if isinstance(packet, dict):
        channels = packet.get("channels") or {}
        if isinstance(channels, dict):
            code = channels.get("code") or {}
            if isinstance(code, dict):
                text = code.get("text")
                if isinstance(text, str) and text:
                    return text

    return ""


def _exec_code_contamination(code: str) -> Dict[str, Any] | None:
    text = code or ""
    if not text.strip():
        return None
    markers = [
        "<channel:",
        "</channel:",
        "<thinking>",
        "</thinking>",
        "<channel:thinking>",
        "</channel:thinking>",
        "<channel:ReactDecisionOutV2>",
        "</channel:ReactDecisionOutV2>",
    ]
    lower = text.lower()
    marker = next((m for m in markers if m.lower() in lower), "")
    first_nonempty = ""
    for line in text.splitlines():
        if line.strip():
            first_nonempty = line.strip()
            break
    if not marker and not first_nonempty.startswith(("```", "`")):
        return None
    line_no = 0
    offending = first_nonempty
    if marker:
        marker_l = marker.lower()
        for idx, line in enumerate(text.splitlines(), start=1):
            if marker_l in line.lower():
                line_no = idx
                offending = line.strip()
                break
    return {
        "code": "exec_code_contaminated",
        "message": "Exec code channel contained non-code text or channel tags; no code was executed.",
        "where": "react.exec_code_validation",
        "marker": marker or "markdown_fence_or_backtick",
        "line": line_no or 1,
        "excerpt": offending[:500],
    }


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
        details: List[str] = []
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            msg = str(violation.get("message") or "").strip()
            if msg:
                details.append(msg)
            suggested = str(violation.get("suggested_ref") or "").strip()
            bad_path = str(violation.get("path") or "").strip()
            if suggested and bad_path:
                details.append(f"Use `ref:{suggested}` instead of `ref:{bad_path}`.")
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.param_ref_not_visible",
            message=" ".join(dict.fromkeys(details)) or (
                "One or more ref: bindings are not visible to this tool call. channel=internal artifacts are private. "
                "For rendering_tools.write_* source refs, write the source as an external artifact first "
                "(react.write channel=canvas, or exec visibility=external)."
            ),
            extra={"violations": violations, "tool_id": tool_id, "protocol_violation": True},
        )
        state["retry_decision"] = True
        return state

    exec_streamer = state.get("exec_code_streamer") if tools_insights.is_exec_tool(tool_id) else None

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

        code_txt = _extract_exec_code_from_state(state)
        if not code_txt:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.exec_missing_code",
                message="Exec tool requires code in <channel:code>; no code was received.",
                extra={"tool_id": tool_id},
            )
            try:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.round import ReactRound
                ReactRound.decision_raw(
                    ctx_browser=ctx_browser,
                    decision=state.get("last_decision_raw") or state.get("last_decision") or {},
                    iteration=int(state.get("iteration") or 0),
                    reason="missing_channel.code",
                )
            except Exception:
                pass
            try:
                error_payload = {
                    "tool_id": tool_id,
                    "reason": "missing_channel.code",
                    "recovery": (
                        "Use exec only when raw Python is emitted in channel:code. "
                        "For ordinary PDF/PPTX/DOCX rendering, call rendering_tools.write_* directly."
                    ),
                }
                add_block(ctx_browser, build_tool_result_error_block(
                    turn_id=ctx_browser.runtime_ctx.turn_id,
                    tool_call_id=tool_call_id,
                    code="exec_missing_code",
                    message="Exec tool requires raw Python in channel:code; no code was received.",
                    details=error_payload,
                ))
                state["last_tool_result"] = [{
                    "artifact_id": tool_id,
                    "output": None,
                    "summary": "",
                    "error": {
                        "code": "exec_missing_code",
                        "message": "Exec tool requires raw Python in channel:code; no code was received.",
                        "details": error_payload,
                    },
                }]
                state["last_tool_id"] = tool_id
            except Exception:
                pass
            state["retry_decision"] = True
            return state
        contamination = _exec_code_contamination(code_txt)
        if contamination:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.exec_code_contaminated",
                message=contamination["message"],
                extra={**contamination, "tool_id": tool_id},
            )
            add_block(ctx_browser, build_tool_result_error_block(
                turn_id=ctx_browser.runtime_ctx.turn_id,
                tool_call_id=tool_call_id,
                code=contamination["code"],
                message=contamination["message"],
                details=contamination,
            ))
            state["retry_decision"] = True
            state["last_tool_result"] = [{
                "artifact_id": tool_id,
                "output": None,
                "summary": "",
                "error": contamination,
            }]
            state["last_tool_id"] = tool_id
            return state
        code_rewrites = []
        if isinstance(code_txt, str):
            rewritten_code, code_rewrites = rewrite_exec_code_paths(code_txt, turn_id=turn_id)
            if rewritten_code != code_txt:
                code_txt = rewritten_code
                final_params["code"] = rewritten_code
                if exec_streamer:
                    try:
                        exec_streamer.set_code(rewritten_code)
                    except Exception:
                        pass
            elif code_txt:
                final_params["code"] = code_txt
        if code_rewrites:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.exec_code_rewritten",
                message="Exec code contained relative files/ or attachments/ paths; rewritten to current turn.",
                extra={"rewritten": code_rewrites, "tool_id": tool_id},
            )

        if code_txt:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            lang = ""
            if exec_streamer:
                try:
                    lang = (exec_streamer.subsystem_language or "").strip()
                except Exception:
                    lang = ""
            mime = "text/x-python" if (lang or "").lower() in {"python", "py"} else "text/plain"
            add_block(ctx_browser, {
                "type": "react.tool.code",
                "call_id": tool_call_id,
                "tool_id": tool_id,
                "mime": mime,
                "path": f"fi:{turn_id}.code.{tool_call_id}" if turn_id else "",
                "text": code_txt,
                "ts": ts,
                "meta": {
                    "lang": lang or "python",
                    "kind": "file",
                    "tool_call_id": tool_call_id,
                    "tool_id": tool_id,
                },
            })

        paths, rewritten_paths = extract_code_file_paths(
            code_txt, turn_id=turn_id
        ) if isinstance(code_txt, str) else ([], [])
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
                    f"[react] exec_path_rewritten: {rewritten_paths}",
                    level="WARNING",
                )
            except Exception:
                pass
        if paths:
            outdir = pathlib.Path(state["outdir"])
            missing_local = [p for p in paths if not resolve_artifact_path(outdir, p).exists()]
            if missing_local:
                logical_missing = [physical_path_to_logical_path(p) or p for p in missing_local]
                pull_hint = f"react.pull(paths={json.dumps(logical_missing, ensure_ascii=False)})"
                notice_block(
                    ctx_browser=ctx_browser,
                    tool_call_id=tool_call_id,
                    code="protocol_violation.exec_requires_pull",
                    message="Exec code referenced historical files that are not materialized locally. Use react.pull(paths=[...]) first.",
                    extra={
                        "missing": missing_local,
                        "logical_missing": logical_missing,
                        "pull_hint": pull_hint,
                        "tool_id": tool_id,
                    },
                )
                add_block(ctx_browser, build_tool_result_error_block(
                    turn_id=ctx_browser.runtime_ctx.turn_id,
                    tool_call_id=tool_call_id,
                    code="pre_exec_pull_required",
                    message="Exec code referenced historical files that are not materialized locally. Use react.pull(paths=[...]) first.",
                    details={
                        "missing": missing_local,
                        "logical_missing": logical_missing,
                        "pull_hint": pull_hint,
                    },
                ))
                state["retry_decision"] = True
                state["last_tool_result"] = []
                state["last_tool_id"] = tool_id
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
            physical, rel, rewritten = normalize_physical_path(
                path_val,
                turn_id=turn_id,
                allow_generic_fi=True,
            )
            if "/attachments/" in physical:
                # writing to attachments is not allowed; rewrite to files/<name>
                rel = rel.split("/", 1)[-1] if rel else "output"
                physical = f"{turn_id}/files/{rel}"
                rewritten = True
            if physical and physical != path_val:
                final_params["path"] = physical
                if rewritten:
                    notice_block(
                        ctx_browser=ctx_browser,
                        tool_call_id=tool_call_id,
                        code="protocol_violation.path_rewritten",
                        message="Rendering tool path was rewritten to current turn files/.",
                        extra={"original": path_val, "rewritten": physical, "tool_id": tool_id},
                    )
        # Ensure referenced local assets (files/attachments) exist under OUT_DIR for rendering.
        try:
            await ensure_rendering_assets(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                tool_id=tool_id,
                content=final_params.get("content"),
                outdir=pathlib.Path(state["outdir"]),
                notice_fn=notice_block,
            )
        except Exception:
            pass

    # Ensure timeline.json is flushed before running isolated tools (rendering/exec).
    try:
        if tools_insights.should_isolate_tool_execution(tool_id) or tools_insights.is_exec_tool(tool_id):
            if ctx_browser.timeline:
                ctx_browser.timeline.write_local()
    except Exception:
        pass

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

    merged_sources_inline = False
    remapped_source_sids: List[int] = []
    if tools_insights.is_search_tool(tool_id) or tools_insights.is_fetch_uri_content_tool(tool_id):
        try:
            rows = _extract_tool_source_rows(output)
            if rows:
                remapped_rows, remapped_source_sids = _remap_tool_sources(
                    ctx_browser=ctx_browser,
                    rows=rows,
                )
                if remapped_rows:
                    output = remapped_rows
                    merged_sources_inline = True
        except Exception:
            pass
    if not is_exec:
        item_error = tool_err or call_error
        if item_error is None and isinstance(tool_response, dict) and tool_response.get("status") == "error":
            item_error = {
                "code": "tool_error",
                "message": summary or "Tool execution failed",
                "where": tool_id,
            }
        items = [
            {
                "artifact_id": tool_id,
                "output": output,
                "summary": summary or "",
                "error": item_error,
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
            "path": tc_result_path(turn_id=ctx_browser.runtime_ctx.turn_id or "", call_id=tool_call_id),
            "text": report_text,
            "meta": {
                "tool_call_id": tool_call_id,
            },
        })
    declared_file_items: List[Dict[str, Any]] = []
    if not is_exec:
        declared_file_items = _declared_files_to_tool_items(
            output=output,
            tool_id=tool_id,
            default_summary=summary or "",
        )
    for idx, tr in enumerate(items):
        if not isinstance(tr, dict):
            continue
        artifact_id = (tr.get("artifact_id") or f"{tool_id}_{idx}").strip()
        output = tr.get("output")
        artifact_kind = tr.get("artifact_kind") or "file"
        if tools_insights.is_search_tool(tool_id) or tools_insights.is_fetch_uri_content_tool(tool_id):
            artifact_kind = "file"
        visibility = "external" if (tools_insights.is_write_tool(tool_id) or tools_insights.is_exec_tool(tool_id)) else "internal"
        if tools_insights.is_exec_tool(tool_id):
            candidate_visibility = tr.get("visibility")
            if candidate_visibility is None and isinstance(output, dict):
                candidate_visibility = output.get("visibility")
            visibility = normalize_artifact_visibility(candidate_visibility, default="external")
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
            path_hint = final_params.get("path")
            if isinstance(path_hint, str) and path_hint.strip() == candidate:
                phys_path_override = candidate
                rel_path_override = candidate
                rewritten_override = False
            else:
                phys_path_override, rel_path_override, rewritten_override = normalize_physical_path(
                    candidate,
                    turn_id=turn_id,
                    allow_generic_fi=True,
                )
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
                if isinstance(final_params.get("path"), str) and final_params.get("path", "").strip() == file_hint:
                    file_for_stats = file_hint
                else:
                    phys_hint, _, _ = normalize_physical_path(
                        file_hint,
                        turn_id=turn_id,
                        allow_generic_fi=True,
                    )
                    file_for_stats = phys_hint or file_hint
                try:
                    output_root = pathlib.Path(state["outdir"])
                    stats_path = pathlib.Path(file_for_stats)
                    stats_output_dir = artifact_outdir_for(output_root)
                    if not stats_path.is_absolute():
                        resolved_stats_path = resolve_artifact_path(output_root, file_for_stats)
                        if resolved_stats_path.exists():
                            file_for_stats = str(resolved_stats_path)
                            stats_output_dir = None
                    artifact_stats = analyze_write_tool_output(
                        file_path=file_for_stats,
                        mime=tools_insights.default_mime_for_write_tool(tool_id),
                        output_dir=stats_output_dir,
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
        sources_used: List[Any] = []
        if tool_id.startswith("rendering_tools."):
            try:
                from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_citation_sids_any
                content_val = final_params.get("content")
                if isinstance(content_val, str) and content_val.strip():
                    sources_used = extract_citation_sids_any(content_val) or []
            except Exception:
                sources_used = []
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
            sources_used=sources_used,
            inputs=final_params,
            call_record_rel=tool_response.get("call_record_rel"),
            call_record_abs=tool_response.get("call_record_abs"),
            error=tr_error,
            content_lineage=content_lineage,
            tool_call_id=tool_call_id,
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
        should_resolve_file_path = visibility == "external" or tools_insights.is_exec_tool(tool_id)
        if should_resolve_file_path:
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
                phys_path, rel_path, rewritten = normalize_physical_path(
                    artifact_rel,
                    turn_id=turn_id,
                    allow_generic_fi=True,
                )
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
        expose_internal_exec_file = bool(tools_insights.is_exec_tool(tool_id) and visibility == "internal" and phys_path)
        artifact_path = (
            physical_path_to_logical_path(phys_path)
            if (phys_path and (visibility == "external" or expose_internal_exec_file))
            else tc_result_path(turn_id=turn_id, call_id=tool_call_id)
        )
        physical_path = phys_path if (phys_path and (visibility == "external" or expose_internal_exec_file)) else ""
        if tools_insights.is_search_tool(tool_id) or tools_insights.is_fetch_uri_content_tool(tool_id):
            try:
                sids = remapped_source_sids
                if not sids:
                    data = output
                    if isinstance(data, dict) and "ret" in data:
                        data = data.get("ret")
                    if isinstance(data, list):
                        sids = [int(r.get("sid") or 0) for r in data if isinstance(r, dict) and r.get("sid") is not None]
                        sids = [s for s in sids if s > 0]
                if sids:
                    artifact_path = _format_sources_pool_path(sids)
            except Exception:
                pass
        edited = detect_edit(
            timeline=getattr(ctx_browser, "timeline", None),
            artifact_path=artifact_path if artifact_path.startswith("fi:") else "",
            tool_call_id=tool_call_id,
        )
        enrich_artifact_file_metadata(
            artifact=artifact_view.raw,
            outdir=pathlib.Path(state["outdir"]),
            physical_path=physical_path,
            mime=artifact_view.mime or "",
        )
        if isinstance((artifact_view.raw or {}).get("value"), dict):
            try:
                artifact_view.size_bytes = (artifact_view.raw.get("value") or {}).get("size_bytes")
            except Exception:
                pass
        # Add produced images to sources_pool (for rendering/embedding only).
        if visibility == "external" and physical_path:
            try:
                merge_sources_pool_for_file_rows(
                    ctx_browser=ctx_browser,
                    rows=[{
                        "physical_path": physical_path,
                        "artifact_path": artifact_path,
                        "mime": artifact_view.mime or "",
                        "size_bytes": artifact_view.size_bytes,
                        "filename": artifact_view.filename or pathlib.Path(physical_path).name,
                        "turn_id": turn_id,
                        "raw": (artifact_view.raw or {}),
                    }],
                )
            except Exception:
                pass
        meta_block = build_artifact_meta_block(
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            artifact=artifact_view.raw,
            artifact_path=artifact_path,
            physical_path=physical_path,
            edited=edited,
        )
        add_block(ctx_browser, meta_block)

        raw_val = artifact_view.raw or {}
        raw_value = raw_val.get("value") if isinstance(raw_val.get("value"), dict) else {}
        meta_extra = {"tool_call_id": tool_call_id, "turn_id": turn_id, "visibility": visibility}
        items_stats = _items_stats_for_output(output)
        if items_stats:
            meta_extra["items_stats"] = items_stats
        try:
            meta_text = meta_block.get("text") if isinstance(meta_block, dict) else None
            if isinstance(meta_text, str) and meta_text.strip():
                meta_extra["digest"] = meta_text
        except Exception:
            pass
        for key in ("hosted_uri", "rn", "key", "physical_path"):
            val = raw_value.get(key) or raw_val.get(key)
            if val:
                meta_extra[key] = val
        size_bytes = raw_value.get("size_bytes") or raw_val.get("size_bytes")
        if size_bytes is not None and size_bytes != "":
            meta_extra["size_bytes"] = size_bytes
        text_symbols = raw_value.get("text_symbols") or raw_val.get("text_symbols")
        if text_symbols is None and isinstance(raw_value.get("text"), str):
            text_symbols = len(raw_value.get("text") or "")
        if text_symbols is not None and text_symbols != "":
            meta_extra["text_symbols"] = text_symbols
        line_count = raw_value.get("line_count") or raw_val.get("line_count")
        if line_count is not None and line_count != "":
            meta_extra["line_count"] = line_count
        text_preview_line_start = raw_value.get("text_preview_line_start") or raw_val.get("text_preview_line_start")
        text_preview_line_end = raw_value.get("text_preview_line_end") or raw_val.get("text_preview_line_end")
        if text_preview_line_start is not None and text_preview_line_end is not None:
            meta_extra["text_preview_lines"] = {
                "line_start": text_preview_line_start,
                "line_end": text_preview_line_end,
                "line_numbers": bool(raw_value.get("text_preview_line_numbers") or raw_val.get("text_preview_line_numbers")),
            }
        if not meta_extra.get("physical_path"):
            legacy = raw_value.get("local_path") or raw_val.get("local_path")
            if legacy:
                meta_extra["physical_path"] = legacy

        mime = (artifact_view.mime or (artifact_view.raw.get("value") or {}).get("mime") or "").strip().lower()
        if visibility in {"external", "internal"} and (mime.startswith("image/") or mime == "application/pdf") and physical_path:
            abs_path = resolve_artifact_path(pathlib.Path(state["outdir"]), physical_path)
            attach_binary, binary_meta = _should_attach_binary_to_prompt(
                runtime_ctx=ctx_browser.runtime_ctx,
                abs_path=abs_path,
            )
            meta_extra.update({k: v for k, v in binary_meta.items() if v is not None})
            if attach_binary:
                bin_block = build_artifact_binary_block(
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    artifact_path=artifact_path,
                    abs_path=abs_path,
                    mime=mime,
                    meta_extra=meta_extra,
                )
                if bin_block:
                    add_block(ctx_browser, bin_block)
        visible_text = ""
        if isinstance(value, dict):
            preview_text = value.get("text_preview")
            if not (isinstance(preview_text, str) and preview_text.strip()):
                # Backward compatibility for artifacts produced before the field was shortened.
                preview_text = value.get("text_visible_preview")
            visible_text = preview_text if isinstance(preview_text, str) and preview_text.strip() else value.get("text")
        if isinstance(value, dict) and isinstance(visible_text, str) and visible_text.strip():
            mime_out = (value.get("mime") or "").strip() or "text/plain"
            if is_text_mime_type(mime_out):
                add_block(ctx_browser, {
                    "turn": turn_id,
                    "type": "react.tool.result",
                    "call_id": tool_call_id,
                    "mime": mime_out,
                    "path": artifact_path,
                    "text": visible_text,
                    "meta": meta_extra,
                })
        elif visibility == "external" and meta_extra and artifact_path:
            # Emit a metadata-only file block so hosting info is attached to the file path.
            add_block(ctx_browser, {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": mime or "",
                "path": artifact_path,
                "meta": meta_extra,
            })

        if not merged_sources_inline:
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

    for idx, tr in enumerate(declared_file_items, start=len(items)):
        if not isinstance(tr, dict):
            continue
        artifact_id = (tr.get("artifact_id") or f"{tool_id}_{idx}").strip()
        output = tr.get("output")
        artifact_kind = tr.get("artifact_kind") or "file"
        visibility = normalize_artifact_visibility(tr.get("visibility"), default="external")
        summary = tr.get("summary") or ""
        tr_error = _strip_managed(tr.get("error")) if tr.get("error") else None
        turn_id = (ctx_browser.runtime_ctx.turn_id or "")
        value = dict(output) if isinstance(output, dict) else {"text": "" if output is None else str(output), "mime": "text/plain"}
        already_hosted = bool(tr.get("already_hosted"))
        sources_used: List[Any] = []
        artifact_view = build_artifact_view(
            turn_id=turn_id,
            is_current=True,
            artifact_id=artifact_id,
            tool_id=tool_id,
            value=value,
            summary=summary,
            artifact_kind=artifact_kind,
            visibility=visibility,
            description=str(value.get("description") or summary or ""),
            channel=None,
            sources_used=sources_used,
            inputs=final_params,
            call_record_rel=tool_response.get("call_record_rel"),
            call_record_abs=tool_response.get("call_record_abs"),
            error=tr_error,
            content_lineage=content_lineage,
            tool_call_id=tool_call_id,
            artifact_stats=None,
        )
        hosted = []
        if visibility == "external":
            if already_hosted:
                hosted_record = tr.get("hosted_record")
                if isinstance(hosted_record, dict) and (
                    hosted_record.get("hosted_uri") or hosted_record.get("rn") or hosted_record.get("key")
                ):
                    hosted = [hosted_record]
                await emit_hosted_files(
                    hosting_service=react.hosting_service,
                    hosted=hosted,
                    should_emit=bool(hosted and visibility == "external" and not tr.get("emitted")),
                )
            else:
                hosted = await host_artifact_file(
                    hosting_service=react.hosting_service,
                    comm=react.comm,
                    runtime_ctx=ctx_browser.runtime_ctx,
                    artifact=artifact_view.raw,
                    outdir=pathlib.Path(state["outdir"]),
                )
                await emit_hosted_files(
                    hosting_service=react.hosting_service,
                    hosted=hosted,
                    should_emit=True,
                )

        if already_hosted:
            artifact_path = str(
                value.get("logical_path")
                or value.get("artifact_path")
                or value.get("rn")
                or value.get("hosted_uri")
                or tc_result_path(turn_id=turn_id, call_id=tool_call_id)
            ).strip()
            physical_path = str(value.get("physical_path") or value.get("local_path") or "").strip()
        else:
            artifact_rel = (artifact_view.path or (artifact_view.raw.get("value") or {}).get("path") or artifact_id or "").strip()
            tr_path = (tr.get("filepath") or "").strip()
            if tr_path:
                artifact_rel = tr_path
            phys_path, rel_path, rewritten = normalize_physical_path(
                artifact_rel,
                turn_id=turn_id,
                allow_generic_fi=True,
            )
            if rewritten:
                original_path = artifact_rel
                if phys_path and original_path and original_path != phys_path:
                    notice_block(
                        ctx_browser=ctx_browser,
                        tool_call_id=tool_call_id,
                        code="protocol_violation.path_rewritten",
                        message="Declared file path contained a turn/files prefix; rewritten to current-turn relative path.",
                        extra={"original": original_path, "normalized": phys_path},
                    )
            expose_file_path = bool(phys_path and visibility in {"external", "internal"})
            artifact_path = (
                physical_path_to_logical_path(phys_path)
                if expose_file_path
                else tc_result_path(turn_id=turn_id, call_id=tool_call_id)
            )
            physical_path = phys_path if expose_file_path else ""
        edited = detect_edit(
            timeline=getattr(ctx_browser, "timeline", None),
            artifact_path=artifact_path if artifact_path.startswith("fi:") else "",
            tool_call_id=tool_call_id,
        )
        enrich_artifact_file_metadata(
            artifact=artifact_view.raw,
            outdir=pathlib.Path(state["outdir"]),
            physical_path=physical_path,
            mime=artifact_view.mime or "",
        )
        if isinstance((artifact_view.raw or {}).get("value"), dict):
            try:
                artifact_view.size_bytes = (artifact_view.raw.get("value") or {}).get("size_bytes")
            except Exception:
                pass
        if visibility == "external" and physical_path:
            try:
                merge_sources_pool_for_file_rows(
                    ctx_browser=ctx_browser,
                    rows=[{
                        "physical_path": physical_path,
                        "artifact_path": artifact_path,
                        "mime": artifact_view.mime or "",
                        "size_bytes": artifact_view.size_bytes,
                        "filename": artifact_view.filename or pathlib.Path(physical_path).name,
                        "turn_id": turn_id,
                        "raw": (artifact_view.raw or {}),
                    }],
                )
            except Exception:
                pass
        meta_block = build_artifact_meta_block(
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            artifact=artifact_view.raw,
            artifact_path=artifact_path,
            physical_path=physical_path,
            edited=edited,
        )
        add_block(ctx_browser, meta_block)

        raw_val = artifact_view.raw or {}
        raw_value = raw_val.get("value") if isinstance(raw_val.get("value"), dict) else {}
        meta_extra = {"tool_call_id": tool_call_id, "turn_id": turn_id, "visibility": visibility}
        try:
            meta_text = meta_block.get("text") if isinstance(meta_block, dict) else None
            if isinstance(meta_text, str) and meta_text.strip():
                meta_extra["digest"] = meta_text
        except Exception:
            pass
        for key in ("hosted_uri", "rn", "key", "physical_path"):
            val = raw_value.get(key) or raw_val.get(key)
            if val:
                meta_extra[key] = val
        if not meta_extra.get("physical_path"):
            legacy = raw_value.get("local_path") or raw_val.get("local_path")
            if legacy:
                meta_extra["physical_path"] = legacy

        mime = (artifact_view.mime or (artifact_view.raw.get("value") or {}).get("mime") or "").strip().lower()
        if visibility in {"external", "internal"} and (mime.startswith("image/") or mime == "application/pdf") and physical_path:
            abs_path = resolve_artifact_path(pathlib.Path(state["outdir"]), physical_path)
            attach_binary, binary_meta = _should_attach_binary_to_prompt(
                runtime_ctx=ctx_browser.runtime_ctx,
                abs_path=abs_path,
            )
            meta_extra.update({k: v for k, v in binary_meta.items() if v is not None})
            if attach_binary:
                bin_block = build_artifact_binary_block(
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    artifact_path=artifact_path,
                    abs_path=abs_path,
                    mime=mime,
                    meta_extra=meta_extra,
                )
                if bin_block:
                    add_block(ctx_browser, bin_block)
        if meta_extra and artifact_path and (
            visibility == "external" or (visibility == "internal" and physical_path)
        ):
            add_block(ctx_browser, {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": mime or "",
                "path": artifact_path,
                "meta": meta_extra,
            })

    if declared_file_items:
        items = list(items or []) + declared_file_items
    state["last_tool_result"] = items
    state["last_tool_id"] = tool_id
    return state
