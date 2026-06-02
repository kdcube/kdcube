# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import pathlib
from typing import Any

from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for, resolve_artifact_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifact_analysis import analyze_write_tool_output
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_artifact_binary_block,
    build_artifact_meta_block,
    build_artifact_view,
    detect_edit,
    normalize_physical_path,
    physical_path_to_logical_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.heuristics import should_attach_binary_to_prompt
from kdcube_ai_app.apps.chat.sdk.solutions.react.sources import (
    build_sources_pool_items_stats,
    merge_sources_pool_for_file_rows,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    add_block,
    emit_hosted_files,
    enrich_artifact_file_metadata,
    host_artifact_file,
    notice_block,
    tc_result_path,
)
from kdcube_ai_app.apps.chat.sdk.util import normalize_artifact_visibility
from kdcube_ai_app.tools.content_type import is_text_mime_type


def _format_sources_pool_path(sids: list[int]) -> str:
    sids = sorted({int(s) for s in (sids or []) if isinstance(s, int) and s > 0})
    if not sids:
        return ""
    ranges: list[tuple[int, int]] = []
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
        out: dict[str, Any] = {"type": f"list[{len(value)}]"}
        if value:
            out["sample"] = _shape_of(value[0], depth=depth + 1, max_depth=max_depth)
        if len(value) > 1:
            out["more_items"] = len(value) - 1
        return out
    if isinstance(value, str):
        return f"str[{len(value)}]"
    return type(value).__name__


def _items_stats_for_output(output: Any) -> dict[str, Any]:
    data = output
    if isinstance(data, dict) and "ret" in data:
        data = data.get("ret")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return {}
    if isinstance(data, dict) and "ret" in data:
        data = data.get("ret")
    if isinstance(data, dict):
        dict_rows = [row for row in data.values() if isinstance(row, dict)]
        if dict_rows and all((row.get("sid") is not None or row.get("url") or row.get("content") is not None) for row in dict_rows):
            return build_sources_pool_items_stats(dict_rows)
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


def _source_pool_sids_for_item(output: Any, remapped_source_sids: list[int]) -> list[int]:
    sids = [int(s) for s in (remapped_source_sids or []) if isinstance(s, int) and s > 0]
    if sids:
        return sids
    data = output
    if isinstance(data, dict) and "ret" in data:
        data = data.get("ret")
    if not isinstance(data, list):
        return []
    out: list[int] = []
    for row in data:
        if not isinstance(row, dict) or row.get("sid") is None:
            continue
        try:
            sid = int(row.get("sid") or 0)
        except Exception:
            sid = 0
        if sid > 0:
            out.append(sid)
    return out


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


async def emit_policy_artifact_blocks(
    *,
    react: Any,
    ctx_browser: Any,
    state: dict[str, Any],
    tool_id: str,
    tool_call_id: str,
    final_params: dict[str, Any],
    tool_response: dict[str, Any],
    content_lineage: Any,
    items: list[dict[str, Any]],
    declared_file_items: list[dict[str, Any]] | None = None,
    remapped_source_sids: list[int] | None = None,
    notice_rows_produced: bool = False,
) -> list[dict[str, Any]]:
    """Emit existing ReAct artifact/meta/binary/text blocks for policy rows.

    This is the common policy-era artifact producer. It does not classify tools
    by `tool_id` and does not infer write/search/exec behavior. Tool and bundle
    policies provide rows with explicit fields such as `write_artifact`,
    `analyze_write_output`, `emit_hosted_file`, `resolve_file_path`,
    `artifact_path_mode`, `sources_used`, `visibility`, and
    `already_hosted`; this helper turns those rows into the same timeline
    blocks and hosted-file side effects the legacy external-tool path produced.
    """
    emitted_items: list[dict[str, Any]] = []
    all_items = list(items or [])
    declared = list(declared_file_items or [])
    if declared:
        all_items.extend(declared)

    for idx, tr in enumerate(all_items):
        if not isinstance(tr, dict):
            continue
        is_declared = idx >= len(items or [])
        artifact_id = (tr.get("artifact_id") or f"{tool_id}_{idx}").strip()
        output = tr.get("output")
        artifact_kind = tr.get("artifact_kind") or "file"
        candidate_visibility = tr.get("visibility")
        if candidate_visibility is None and isinstance(output, dict):
            candidate_visibility = output.get("visibility")
        visibility = normalize_artifact_visibility(candidate_visibility, default="internal" if not is_declared else "external")
        summary = tr.get("summary") or ""
        tr_error = _strip_managed(tr.get("error")) if tr.get("error") else None
        policy_write_artifact = bool(tr.get("write_artifact"))
        policy_analyze_write_output = bool(tr.get("analyze_write_output"))
        policy_emit_hosted_file = bool(tr.get("emit_hosted_file"))
        policy_resolve_file_path = bool(tr.get("resolve_file_path"))
        already_hosted = bool(tr.get("already_hosted"))
        turn_id = ctx_browser.runtime_ctx.turn_id or ""

        rel_path_override = ""
        phys_path_override = ""
        rewritten_override = False
        rewrite_original = ""

        if isinstance(output, dict) and output.get("type") == "file":
            value: Any = dict(output)
        elif policy_write_artifact and isinstance(output, str) and output.strip():
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
            value = phys_path_override or candidate
            rewrite_original = candidate
        elif policy_write_artifact:
            path_hint = final_params.get("path")
            if isinstance(path_hint, str) and path_hint.strip():
                value = {"type": "file", "path": path_hint.strip()}
            else:
                value = {"text": "" if output is None else str(output), "mime": "text/plain"}
        elif isinstance(output, (dict, list)):
            value = {"text": json.dumps(output, ensure_ascii=False, indent=2), "mime": "application/json"}
        elif isinstance(output, str):
            value = {"text": output, "mime": "text/plain"}
        else:
            value = {"text": "" if output is None else str(output), "mime": "text/plain"}

        artifact_stats = None
        if policy_analyze_write_output:
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
                    default_mime = str(tr.get("default_mime") or "").strip()
                    artifact_stats = analyze_write_tool_output(
                        file_path=file_for_stats,
                        mime=default_mime,
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

        sources_used_raw = tr.get("sources_used")
        sources_used = list(sources_used_raw) if isinstance(sources_used_raw, list) else []
        artifact_view = build_artifact_view(
            turn_id=turn_id,
            is_current=True,
            artifact_id=artifact_id,
            tool_id=tool_id,
            value=value,
            summary=summary,
            artifact_kind=artifact_kind,
            visibility=visibility,
            description=str(value.get("description") or summary or "") if isinstance(value, dict) else "",
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
        if tr_error and not notice_rows_produced:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="tool_result_error",
                message=(tr_error or {}).get("message") if isinstance(tr_error, dict) else str(tr_error),
                extra={"tool_id": tool_id, "artifact_id": artifact_id, "error": tr_error},
            )

        hosted = []
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
            raw_value_for_host = artifact_view.raw.get("value") if isinstance((artifact_view.raw or {}).get("value"), dict) else {}
            value_looks_like_file = bool(
                isinstance(raw_value_for_host, dict)
                and (raw_value_for_host.get("type") == "file" or raw_value_for_host.get("path"))
            )
            path_backed_artifact = bool(
                value_looks_like_file
                or policy_write_artifact
                or policy_resolve_file_path
                or policy_emit_hosted_file
                or is_declared
            )
            should_host_artifact = bool(
                path_backed_artifact
                and (
                    visibility == "external"
                    or visibility == "internal"
                    or policy_emit_hosted_file
                )
            )
            if should_host_artifact:
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
                    should_emit=bool(visibility == "external" and policy_emit_hosted_file),
                )

        phys_path = ""
        rel_path = ""
        should_resolve_file_path = bool(
            policy_resolve_file_path
            or policy_write_artifact
            or policy_emit_hosted_file
            or already_hosted
            or is_declared
        )
        if not should_resolve_file_path:
            raw_value_for_path = artifact_view.raw.get("value") if isinstance((artifact_view.raw or {}).get("value"), dict) else {}
            should_resolve_file_path = bool(
                isinstance(raw_value_for_path, dict)
                and (
                    raw_value_for_path.get("type") == "file"
                    or raw_value_for_path.get("path")
                    or raw_value_for_path.get("physical_path")
                    or raw_value_for_path.get("local_path")
                )
            )
        if should_resolve_file_path:
            if already_hosted:
                raw_value = artifact_view.raw.get("value") if isinstance((artifact_view.raw or {}).get("value"), dict) else {}
                phys_path = str(raw_value.get("physical_path") or raw_value.get("local_path") or "").strip()
            elif phys_path_override or rel_path_override:
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

        raw_for_path = artifact_view.raw.get("value") if isinstance((artifact_view.raw or {}).get("value"), dict) else {}
        if already_hosted:
            artifact_path = str(
                raw_for_path.get("logical_path")
                or raw_for_path.get("artifact_path")
                or raw_for_path.get("rn")
                or raw_for_path.get("hosted_uri")
                or tc_result_path(turn_id=turn_id, call_id=tool_call_id)
            ).strip()
            physical_path = phys_path
        else:
            expose_internal_file = bool(visibility == "internal" and phys_path)
            artifact_path = (
                physical_path_to_logical_path(phys_path)
                if (phys_path and (visibility == "external" or expose_internal_file))
                else tc_result_path(turn_id=turn_id, call_id=tool_call_id)
            )
            physical_path = phys_path if (phys_path and (visibility == "external" or expose_internal_file)) else ""
        if str(tr.get("artifact_path_mode") or "").strip() == "sources_pool":
            try:
                sids = _source_pool_sids_for_item(output, list(remapped_source_sids or []))
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
                        "raw": artifact_view.raw or {},
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
            attach_binary, binary_meta = should_attach_binary_to_prompt(
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
            add_block(ctx_browser, {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": mime or "",
                "path": artifact_path,
                "meta": meta_extra,
            })

        emitted_items.append(tr)

    return emitted_items
