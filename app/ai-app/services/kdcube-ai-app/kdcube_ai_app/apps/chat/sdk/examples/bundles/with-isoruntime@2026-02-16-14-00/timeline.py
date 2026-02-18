# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pathlib
from typing import Any, Dict, List

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.artifacts import (
    build_artifact_meta_block,
    build_artifact_binary_block,
    build_artifact_view,
    normalize_physical_path,
    detect_edit,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import (
    tool_call_block,
    add_block,
    tc_result_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser
from kdcube_ai_app.tools.content_type import is_text_mime_type


async def build_exec_timeline(
    *,
    runtime_ctx: Any,
    tool_call_id: str,
    tool_response: Dict[str, Any],
    tool_params: Dict[str, Any],
    outdir: pathlib.Path,
    scenario_label: str | None = None,
    scenario_description: str | None = None,
    code_text: str | None = None,
) -> FakeBrowser:
    ctx_browser = FakeBrowser(runtime_ctx=runtime_ctx)
    tool_id = "exec_tools.execute_code_python"

    if scenario_label or scenario_description:
        label = (scenario_label or "").strip()
        desc = (scenario_description or "").strip()
        summary = label if label else "Scenario"
        if desc:
            summary = f"{summary} — {desc}" if summary else desc
        add_block(ctx_browser, {
            "turn": runtime_ctx.turn_id or "",
            "type": "react.notice",
            "call_id": tool_call_id,
            "mime": "text/plain",
            "path": tc_result_path(turn_id=runtime_ctx.turn_id or "", call_id=tool_call_id),
            "text": f"[scenario] {summary}",
            "meta": {
                "tool_call_id": tool_call_id,
            },
        })

    if code_text:
        add_block(ctx_browser, {
            "turn": runtime_ctx.turn_id or "",
            "type": "react.tool.code",
            "call_id": tool_call_id,
            "tool_id": tool_id,
            "mime": "text/x-python",
            "path": f"fi:{runtime_ctx.turn_id}.code.{tool_call_id}" if runtime_ctx.turn_id else "",
            "text": code_text,
            "meta": {
                "lang": "python",
                "kind": "file",
                "tool_call_id": tool_call_id,
                "tool_id": tool_id,
            },
        })
    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "params": tool_params,
        },
    )

    report_text = (tool_response.get("report_text") or tool_response.get("summary") or "").strip()
    if report_text:
        add_block(ctx_browser, {
            "turn": runtime_ctx.turn_id or "",
            "type": "react.tool.result",
            "call_id": tool_call_id,
            "mime": "text/markdown",
            "path": tc_result_path(turn_id=runtime_ctx.turn_id or "", call_id=tool_call_id),
            "text": report_text,
            "meta": {
                "tool_call_id": tool_call_id,
            },
        })

    items = tool_response.get("items") or []
    for idx, tr in enumerate(items):
        if not isinstance(tr, dict):
            continue
        artifact_id = (tr.get("artifact_id") or f"{tool_id}_{idx}").strip()
        output = tr.get("output")
        artifact_kind = tr.get("artifact_kind") or "file"
        summary = tr.get("summary") or ""
        visibility = "external"

        artifact_view = build_artifact_view(
            turn_id=runtime_ctx.turn_id or "",
            is_current=True,
            artifact_id=artifact_id,
            tool_id=tool_id,
            value=output,
            summary=summary,
            artifact_kind=artifact_kind,
            visibility=visibility,
            description="",
            channel=None,
            inputs=tool_params,
            call_record_rel=None,
            call_record_abs=None,
            error=tr.get("error"),
            content_lineage=[],
            tool_call_id=tool_call_id,
            artifact_stats=None,
        )

        artifact_rel = (artifact_view.path or (artifact_view.raw.get("value") or {}).get("path") or artifact_id or "").strip()
        tr_path = (tr.get("filepath") or "").strip()
        if tr_path:
            artifact_rel = tr_path
        phys_path, rel_path, _ = normalize_physical_path(artifact_rel, turn_id=runtime_ctx.turn_id or "")
        physical_path = phys_path or artifact_rel
        artifact_path = f"fi:{physical_path}" if physical_path else tc_result_path(
            turn_id=runtime_ctx.turn_id or "", call_id=tool_call_id
        )

        edited = detect_edit(
            timeline=getattr(ctx_browser, "timeline", None),
            artifact_path=artifact_path if artifact_path.startswith("fi:") else "",
            tool_call_id=tool_call_id,
        )
        meta_block = build_artifact_meta_block(
            turn_id=runtime_ctx.turn_id or "",
            tool_call_id=tool_call_id,
            artifact=artifact_view.raw,
            artifact_path=artifact_path,
            physical_path=physical_path,
            edited=edited,
        )
        add_block(ctx_browser, meta_block)

        raw_val = artifact_view.raw or {}
        raw_value = raw_val.get("value") if isinstance(raw_val.get("value"), dict) else {}
        meta_extra = {"tool_call_id": tool_call_id, "turn_id": runtime_ctx.turn_id or ""}
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

        mime = (artifact_view.mime or (artifact_view.raw.get("value") or {}).get("mime") or "").strip().lower()
        if visibility == "external" and (mime.startswith("image/") or mime == "application/pdf") and physical_path:
            abs_path = pathlib.Path(outdir) / physical_path
            bin_block = build_artifact_binary_block(
                turn_id=runtime_ctx.turn_id or "",
                tool_call_id=tool_call_id,
                artifact_path=artifact_path,
                abs_path=abs_path,
                mime=mime,
                meta_extra=meta_extra,
            )
            if bin_block:
                add_block(ctx_browser, bin_block)

        if isinstance(output, dict) and isinstance(output.get("text"), str) and output.get("text").strip():
            mime_out = (output.get("mime") or "").strip() or "text/plain"
            if is_text_mime_type(mime_out):
                add_block(ctx_browser, {
                    "turn": runtime_ctx.turn_id or "",
                    "type": "react.tool.result",
                    "call_id": tool_call_id,
                    "mime": mime_out,
                    "path": artifact_path,
                    "text": output.get("text"),
                    "meta": meta_extra,
                })
        elif visibility == "external" and meta_extra and artifact_path:
            add_block(ctx_browser, {
                "turn": runtime_ctx.turn_id or "",
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": mime or "",
                "path": artifact_path,
                "meta": meta_extra,
            })

    return ctx_browser


async def render_timeline_text(ctx_browser: FakeBrowser) -> str:
    blocks = await ctx_browser.timeline.render(include_sources=False, include_announce=True)
    chunks: List[str] = []
    for b in blocks:
        if b.get("type") == "text":
            chunks.append(b.get("text") or "")
        else:
            data = b.get("data") or ""
            chunks.append(f"[{b.get('type')}] data_len={len(data)}")
    return "\n".join(chunks).strip()
