# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/layout.py

import json
import datetime
import time
import urllib.parse
import pathlib
import os
from typing import Dict, Any, List, Tuple, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.apps.chat.sdk.solutions.react.plan import (
    collect_plan_snapshots,
    latest_current_plan_snapshot,
    PlanSnapshot,
    plan_snapshot_ref,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import (
    get_workspace_implementation,
    list_materialized_turn_roots,
    latest_workspace_checkout_event,
    summarize_current_turn_scopes,
    latest_workspace_publish_event,
)

from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
    set_active_skill_tool_catalog,
    skills_gallery_text,
)
from kdcube_ai_app.apps.chat.sdk.util import (
    LINE_NUMBERS_LINES,
    _shorten,
    _wrap_lines,
    normalize_line_numbers_mode,
    token_count,
)
from kdcube_ai_app.tools.content_type import is_text_mime_type
import re

MAX_VISIBLE_OPEN_PLANS = 4
MAX_VISIBLE_LIVE_TURN_EVENTS = 4
DEFAULT_READ_VISIBLE_MAX_TEXT_SYMBOLS = 48_000
DEFAULT_READ_VISIBLE_MAX_TOKENS = 12_000
DEFAULT_READ_VISIBLE_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_READ_VISIBLE_CONTEXT_FRACTION = 0.15
DEFAULT_EXEC_TEXT_PREVIEW_MAX_SYMBOLS = 8_000
DEFAULT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS = 12_000
DEFAULT_EXEC_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_EXEC_MAX_WORKSPACE_DELTA_BYTES = 250 * 1024 * 1024


def record_assistant_completion_attempt(
    *,
    scratchpad: Any,
    answer_text: str,
    ts: Optional[str],
    iteration: Optional[int] = None,
    working_summary_text: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    text = str(answer_text or "").strip()
    if not text:
        return None
    entries = getattr(scratchpad, "assistant_completion_attempts", None)
    if not isinstance(entries, list):
        entries = []
        setattr(scratchpad, "assistant_completion_attempts", entries)
    entry: Dict[str, Any] = {
        "text": text,
        "ts": (ts or "").strip(),
    }
    if iteration is not None:
        try:
            entry["iteration"] = int(iteration)
        except Exception:
            pass
    working_summary = str(working_summary_text or "").strip()
    if working_summary:
        entry["working_summary"] = working_summary
    try:
        sources_used = citations_module.extract_citation_sids_any(text)
    except Exception:
        sources_used = []
    if sources_used:
        entry["sources_used"] = list(sources_used)
    entries.append(entry)
    return entry


def _normalized_assistant_completion_entries(
    *,
    runtime: RuntimeCtx,
    completion_entries: Optional[List[Dict[str, Any]]],
    final_answer_text: str,
    ended_at: Optional[str],
) -> List[Dict[str, Any]]:
    tid = (getattr(runtime, "turn_id", None) or "").strip()
    if not tid:
        return []
    ts_fallback = (ended_at or getattr(runtime, "started_at", "") or "").strip()
    normalized: List[Dict[str, Any]] = []
    for raw in completion_entries or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        item: Dict[str, Any] = {
            "text": text,
            "ts": str(raw.get("ts") or ts_fallback or "").strip(),
        }
        sources_used = raw.get("sources_used")
        if isinstance(sources_used, list) and sources_used:
            item["sources_used"] = list(sources_used)
        else:
            try:
                extracted = citations_module.extract_citation_sids_any(text)
            except Exception:
                extracted = []
            if extracted:
                item["sources_used"] = list(extracted)
        if raw.get("iteration") is not None:
            item["iteration"] = raw.get("iteration")
        working_summary = str(raw.get("working_summary") or "").strip()
        if working_summary:
            item["working_summary"] = working_summary
        normalized.append(item)

    latest_text = str(final_answer_text or "").strip()
    if latest_text:
        if normalized and str(normalized[-1].get("text") or "").strip() == latest_text:
            if not str(normalized[-1].get("ts") or "").strip():
                normalized[-1]["ts"] = ts_fallback
        else:
            item = {
                "text": latest_text,
                "ts": ts_fallback,
            }
            try:
                extracted = citations_module.extract_citation_sids_any(latest_text)
            except Exception:
                extracted = []
            if extracted:
                item["sources_used"] = list(extracted)
            normalized.append(item)
    return normalized



def build_user_input_blocks(
    *,
    runtime: RuntimeCtx,
    user_text: str,
    user_attachments: Optional[List[Dict[str, Any]]],
    block_factory,
    continuation_kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    tid = (getattr(runtime, "turn_id", None) or "").strip()
    if not tid:
        return []
    prompt_message_id = "m0"
    ts = (getattr(runtime, "started_at", "") or "").strip()
    blocks: List[Dict[str, Any]] = []
    user_text = (user_text or "").strip()
    if user_text:
        prompt_path = f"ar:{tid}.user.prompt"
        prompt_meta: Dict[str, Any] = {"message_id": prompt_message_id}
        if continuation_kind and continuation_kind != "regular":
            prompt_meta["continuation_kind"] = continuation_kind
        blocks.append(block_factory(
            type="user.prompt",
            author="user",
            turn_id=tid,
            ts=ts,
            path=prompt_path,
            text=user_text,
            meta=prompt_meta,
        ))
    blocks.extend(build_user_attachment_blocks(
        turn_id=tid,
        ts=ts,
        user_attachments=user_attachments,
        block_factory=block_factory,
        path_root=f"fi:{tid}.user.attachments",
        synthetic_physical_root=f"{tid}/attachments",
        meta_extra={"message_id": prompt_message_id},
    ))
    return blocks


def build_user_attachment_blocks(
    *,
    turn_id: str,
    ts: str,
    user_attachments: Optional[List[Dict[str, Any]]],
    block_factory,
    path_root: str,
    synthetic_physical_root: Optional[str] = None,
    meta_extra: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    tid = (turn_id or "").strip()
    if not tid:
        return []
    path_root = (path_root or "").rstrip("/")
    if not path_root:
        return []
    blocks: List[Dict[str, Any]] = []
    for att in (user_attachments or []):
        if not isinstance(att, dict):
            continue
        name = (att.get("filename") or att.get("name") or "").strip() or "(attachment)"
        mime = (att.get("mime") or "").strip() or "application/octet-stream"
        summary = (att.get("summary") or "").strip()
        attachment_path = path_root
        if name and name != "(attachment)":
            attachment_path = f"{path_root}/{name}"
        meta = {k: att.get(k) for k in ("hosted_uri", "rn", "key", "physical_path") if att.get(k)}
        if not meta.get("physical_path") and att.get("local_path"):
            meta["physical_path"] = att.get("local_path")
        if tid:
            meta["turn_id"] = tid
        if isinstance(meta_extra, dict):
            for k, v in meta_extra.items():
                if v is not None:
                    meta[k] = v
        if summary:
            meta["summary"] = summary
        if name and name != "(attachment)":
            meta["filename"] = name
        if mime:
            meta["mime"] = mime
        physical_path = ""
        if synthetic_physical_root and name and name != "(attachment)":
            physical_path = f"{synthetic_physical_root.rstrip('/')}/{name}"
            meta["physical_path"] = physical_path
        # Build a stable, safe metadata digest (no hosted_uri/rn/key).
        try:
            digest_obj = {
                "artifact_path": attachment_path,
                "physical_path": physical_path,
                "mime": mime,
                "kind": "file",
                "visibility": "external",
                "ts": ts,
            }
            size_bytes = att.get("size") or att.get("size_bytes")
            if size_bytes is not None:
                digest_obj["size_bytes"] = size_bytes
            if summary:
                digest_obj["description"] = summary
            digest_text = json.dumps(
                {k: v for k, v in digest_obj.items() if v not in ("", None)},
                ensure_ascii=False,
                indent=2,
            )
            meta["digest"] = digest_text
        except Exception:
            digest_text = ""
        blocks.append(block_factory(
            type="user.attachment.meta",
            author="user",
            turn_id=tid,
            ts=ts,
            path=attachment_path,
            meta=meta or None,
            text=digest_text or None,
            mime="application/json" if digest_text else None,
        ))
        if att.get("base64"):
            blocks.append(block_factory(
                type="user.attachment",
                author="user",
                turn_id=tid,
                ts=ts,
                mime=mime,
                base64=att.get("base64"),
                path=attachment_path,
                meta=meta or None,
            ))
        if is_text_mime_type(mime):
            text_val = (att.get("text") or "").strip()
            if text_val:
                blocks.append(block_factory(
                    type="user.attachment.text",
                    author="user",
                    turn_id=tid,
                    ts=ts,
                    path=attachment_path,
                    text=text_val,
                    meta=meta or None,
                ))
    return blocks


def build_assistant_completion_blocks(
    *,
    runtime: RuntimeCtx,
    completion_entries: Optional[List[Dict[str, Any]]] = None,
    final_answer_text: str = "",
    ended_at: Optional[str],
    block_factory,
) -> List[Dict[str, Any]]:
    tid = runtime.turn_id
    if not tid:
        return []
    entries = _normalized_assistant_completion_entries(
        runtime=runtime,
        completion_entries=completion_entries,
        final_answer_text=final_answer_text,
        ended_at=ended_at,
    )
    if not entries:
        return []
    blocks: List[Dict[str, Any]] = []
    total = len(entries)
    for idx, entry in enumerate(entries, start=1):
        path = f"ar:{tid}.assistant.completion" if idx == total else f"ar:{tid}.assistant.completion.{idx}"
        meta: Dict[str, Any] = {}
        sources_used = entry.get("sources_used") if isinstance(entry.get("sources_used"), list) else []
        if sources_used:
            meta["sources_used"] = list(sources_used)
        if total > 1:
            meta["completion_index"] = idx
            meta["completion_count"] = total
        if entry.get("iteration") is not None:
            meta["iteration"] = entry.get("iteration")
        blocks.append(block_factory(
            type="assistant.completion",
            author="assistant",
            turn_id=tid,
            ts=str(entry.get("ts") or "").strip(),
            path=path,
            text=str(entry.get("text") or ""),
            meta=meta or None,
        ))
    return blocks


def build_assistant_completion_attempt_blocks(
    *,
    runtime: RuntimeCtx,
    entry: Dict[str, Any],
    attempt_index: int,
    block_factory,
) -> List[Dict[str, Any]]:
    tid = (getattr(runtime, "turn_id", None) or "").strip()
    text = str((entry or {}).get("text") or "").strip() if isinstance(entry, dict) else ""
    if not tid or not text:
        return []
    try:
        idx = max(1, int(attempt_index))
    except Exception:
        idx = 1
    meta: Dict[str, Any] = {
        "completion_attempt_index": idx,
        "provisional": True,
    }
    sources_used = entry.get("sources_used") if isinstance(entry.get("sources_used"), list) else []
    if sources_used:
        meta["sources_used"] = list(sources_used)
    if entry.get("iteration") is not None:
        meta["iteration"] = entry.get("iteration")
    return [block_factory(
        type="assistant.completion.attempt",
        author="assistant",
        turn_id=tid,
        ts=str(entry.get("ts") or "").strip(),
        path=f"ar:{tid}.assistant.completion.attempt.{idx}",
        text=text,
        meta=meta,
    )]


def build_working_summary_blocks(
    *,
    runtime: RuntimeCtx,
    summary_text: str = "",
    ended_at: Optional[str],
    block_factory,
) -> List[Dict[str, Any]]:
    # Canonical working-summary paths are read aliases, not persisted blocks.
    # The only persisted summary records are generated from React
    # <channel:summary> completion attempts.
    return []


def build_working_summary_attempt_blocks(
    *,
    runtime: RuntimeCtx,
    summary_text: str = "",
    attempt_index: int,
    attempt_count: Optional[int] = None,
    assistant_completion_path: Optional[str] = None,
    iteration: Optional[int] = None,
    ts: Optional[str] = None,
    block_factory,
) -> List[Dict[str, Any]]:
    tid = (getattr(runtime, "turn_id", None) or "").strip()
    text = str(summary_text or "").strip()
    if not tid or not text:
        return []
    try:
        idx = max(1, int(attempt_index))
    except Exception:
        idx = 1
    meta: Dict[str, Any] = {
        "kind": "working_summary",
        "created_by": "react",
        "source_channel": "summary",
        "summary_scope": "completion_attempt",
        "assistant_completion_attempt_index": idx,
        "covered_turn_ids": [tid],
        "covered_until_turn_id": tid,
    }
    if attempt_count is not None:
        try:
            meta["assistant_completion_attempt_count"] = int(attempt_count)
        except Exception:
            pass
    if assistant_completion_path:
        meta["assistant_completion_path"] = str(assistant_completion_path)
    if iteration is not None:
        meta["iteration"] = iteration
    return [block_factory(
        type="conv.working.summary",
        author="assistant",
        turn_id=tid,
        ts=(ts or getattr(runtime, "started_at", "") or "").strip(),
        path=f"ws:{tid}.conv.working.summary.attempt.{idx}",
        text=text,
        mime="text/markdown",
        meta=meta,
    )]


def build_interrupted_generation_blocks(
    *,
    runtime: RuntimeCtx,
    raw_text: str,
    iteration: int,
    interrupted_at: Optional[str],
    checkpoint: Optional[str],
    cancelled_phase: Optional[str],
    sequence: Optional[int],
    block_factory,
) -> List[Dict[str, Any]]:
    tid = (getattr(runtime, "turn_id", None) or "").strip()
    raw_text = str(raw_text or "").strip()
    if not tid or not raw_text:
        return []
    ts = (interrupted_at or getattr(runtime, "started_at", "") or "").strip()
    meta: Dict[str, Any] = {
        "channel": "raw",
        "iteration": int(iteration or 0),
        "interrupted": True,
        "reason": "steer.interrupted",
    }
    if checkpoint:
        meta["checkpoint"] = str(checkpoint)
    if cancelled_phase:
        meta["cancelled_phase"] = str(cancelled_phase)
    if sequence is not None:
        try:
            meta["sequence"] = int(sequence)
        except Exception:
            pass
    return [block_factory(
        type="react.decision.raw",
        author="react",
        turn_id=tid,
        ts=ts,
        mime="text/plain",
        path=f"ar:{tid}.react.decision.raw.interrupted.{int(iteration or 0)}",
        text=raw_text,
        meta=meta,
    )]


def build_turn_header_text(*, turn_id: str, started_at: str) -> str:
    turn_id = (turn_id or "").strip()
    started_at = (started_at or "").strip()
    if started_at:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"TURN {turn_id} (started at {started_at})",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"TURN {turn_id}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])


def _plan_sort_key(snap: PlanSnapshot) -> Tuple[str, str]:
    return (
        (snap.last_ts or snap.created_ts or "").strip(),
        snap.plan_id,
    )


def _open_plan_snapshots(blocks: List[Dict[str, Any]]) -> List[PlanSnapshot]:
    plans_by_id, order = collect_plan_snapshots(blocks)
    snapshots: List[PlanSnapshot] = []
    for pid in order:
        snap = PlanSnapshot.from_any(plans_by_id.get(pid) or {})
        if snap and snap.is_active():
            snapshots.append(snap)
    snapshots.sort(key=_plan_sort_key)
    return snapshots


def build_announce_plan_lines(
    *,
    timeline_blocks: List[Dict[str, Any]],
    max_visible: int = MAX_VISIBLE_OPEN_PLANS,
) -> List[str]:
    lines: List[str] = ["[OPEN PLANS]"]
    try:
        snapshots = _open_plan_snapshots(timeline_blocks)
        current_snap = latest_current_plan_snapshot(timeline_blocks)
        if not snapshots:
            lines.append("  - plans: none")
            return lines
        visible = snapshots[-max(1, int(max_visible or 1)) :]
        lines.append(f"  - plans: {len(visible)} visible")
        for idx, snap in enumerate(visible, start=1):
            tags: List[str] = []
            if current_snap and snap.plan_id == current_snap.plan_id:
                tags.append("current")
            suffix = f" ({', '.join(tags)})" if tags else ""
            lines.append(f"    • plan_id={snap.plan_id}{suffix}")
            snapshot_ref = plan_snapshot_ref(snap.plan_id)
            if snapshot_ref:
                lines.append(f"      snapshot_ref={snapshot_ref}")
            if snap.origin_turn_id:
                lines.append(f"      created_turn={snap.origin_turn_id}")
            if snap.created_ts:
                lines.append(f"      created_ts={snap.created_ts}")
            last_turn = snap.last_turn_id or snap.origin_turn_id
            if last_turn:
                lines.append(f"      last_update_turn={last_turn}")
            last_ts = snap.last_ts or snap.created_ts
            if last_ts:
                lines.append(f"      last_update_ts={last_ts}")
            for step_idx, step in enumerate(snap.steps or [], start=1):
                lines.append(f"      {snap.status_mark(step_idx)} [{step_idx}] {step}")
    except Exception:
        lines = ["[OPEN PLANS]", "  - plans: none"]
    return lines


def build_timeline_render_directive(
    *,
    block: Dict[str, Any],
    call_id_to_tool_id: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Layout-owned render contract for timeline blocks that need special model-facing
    formatting or should stay internal.
    """
    btype = (block.get("type") or "").strip()
    text = block.get("text")
    if btype == "stage.suggested_followups":
        return {"skip": True}
    if btype == "react.turn.finalize" or (
        isinstance(text, str)
        and "Turn completed with these stats" in text
        and "[BUDGET]" in text
    ):
        compact = compact_turn_finalize_budget_text(text if isinstance(text, str) else "")
        return {"skip": not bool(compact), "text": compact}
    if btype in {
        "react.plan",
        "react.plan.ack",
        "react.state",
        "react.exit",
        "react.workspace.publish",
        "react.workspace.checkout",
    }:
        return {"skip": True}

    if btype == "react.notice":
        payload = None
        if isinstance(text, str) and text.strip():
            try:
                payload = json.loads(text)
            except Exception:
                payload = None
        if isinstance(payload, dict) and str(payload.get("code") or "").strip() == "plan_closed":
            return {"skip": True}
        return {"skip": False}

    return {"skip": False}


def compact_turn_finalize_budget_text(text: str) -> str:
    """
    Keep only the stable finalize header plus budget and open-plan lines. The
    full announce contains volatile memory/workspace/live-event sections and
    must not be carried into later prompt renders.
    """
    raw_lines = str(text or "").splitlines()
    if not raw_lines:
        return ""

    out: List[str] = []
    title_idx = next(
        (idx for idx, line in enumerate(raw_lines) if "Turn completed with these stats" in line),
        None,
    )
    if title_idx is not None:
        start = title_idx
        if title_idx - 1 >= 0 and raw_lines[title_idx - 1].lstrip().startswith("╔"):
            start = title_idx - 1
        end = title_idx
        if title_idx + 1 < len(raw_lines) and raw_lines[title_idx + 1].lstrip().startswith("╚"):
            end = title_idx + 1
        out.extend(line.rstrip() for line in raw_lines[start : end + 1])

    def _append_section(section: str) -> None:
        section_idx = next(
            (idx for idx, line in enumerate(raw_lines) if line.strip() == f"[{section}]"),
            None,
        )
        if section_idx is None:
            return
        section_lines: List[str] = []
        for line in raw_lines[section_idx + 1 :]:
            stripped = line.strip()
            if not stripped:
                if section_lines and section_lines[-1] != "":
                    section_lines.append("")
                continue
            if stripped.startswith("[") or stripped.startswith("╔"):
                break
            section_lines.append(line.rstrip())
        while section_lines and section_lines[-1] == "":
            section_lines.pop()
        if section == "OPEN PLANS":
            meaningful = [
                item.strip()
                for item in section_lines
                if item.strip() and "plans: none" not in item.strip().lower()
            ]
            if not meaningful:
                return
        if out:
            out.append("")
        out.append(f"[{section}]")
        out.extend(section_lines)

    _append_section("BUDGET")
    _append_section("OPEN PLANS")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out).strip()


def build_announce_workspace_lines(
    *,
    runtime_ctx: Optional[RuntimeCtx],
    timeline_blocks: List[Dict[str, Any]],
) -> List[str]:
    if runtime_ctx is None:
        return []
    impl = get_workspace_implementation(runtime_ctx)
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    lines: List[str] = ["[WORKSPACE]"]
    lines.append(f"  implementation: {impl}")
    if turn_id:
        lines.append(f"  current_turn_root: {turn_id}/")

    try:
        roots = list_materialized_turn_roots(runtime_ctx=runtime_ctx)
    except Exception:
        roots = []
    if roots:
        labels = []
        for root in roots[-6:]:
            if root == turn_id:
                labels.append(f"{root} (current)")
            else:
                labels.append(f"{root} (read-only)")
        lines.append(f"  local turn roots: {', '.join(labels)}")
    else:
        lines.append("  local turn roots: none")

    try:
        scopes = summarize_current_turn_scopes(runtime_ctx=runtime_ctx)
    except Exception:
        scopes = []
    if scopes:
        lines.append("  current editable workspace:")
        for item in scopes[:6]:
            scope = str(item.get("scope") or "").strip()
            files = int(item.get("files") or 0)
            lines.append(f"    - files/{scope} ({files} file{'s' if files != 1 else ''})")
    else:
        lines.append("  current editable workspace: none")

    current_checkout = latest_workspace_checkout_event(timeline_blocks, turn_id=turn_id) if turn_id else None
    if current_checkout:
        checkout_mode = str(current_checkout.get("mode") or "").strip()
        if checkout_mode:
            lines.append(f"  checkout_mode: {checkout_mode}")
        checked_out_from = [
            str(item).strip()
            for item in (current_checkout.get("checked_out_from") or [])
            if str(item).strip()
        ]
        if checked_out_from:
            lines.append("  checked_out_from:")
            for item in checked_out_from[:6]:
                lines.append(f"    - {item}")
        else:
            lines.append("  checked_out_from: current-turn only")
    else:
        lines.append("  checked_out_from: none")

    current_publish = latest_workspace_publish_event(timeline_blocks, turn_id=turn_id) if turn_id else None
    any_publish = latest_workspace_publish_event(timeline_blocks)
    last_published_turn = ""
    last_publish_status = ""
    if any_publish:
        last_published_turn = str(any_publish.get("turn_id") or "").strip()
        last_publish_status = str(any_publish.get("status") or "").strip() or "unknown"

    if impl == "git":
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.git_workspace import describe_current_turn_git_repo
            from kdcube_ai_app.apps.chat.sdk.solutions.react.git_workspace import summarize_current_turn_git_lineage_scopes
            outdir = getattr(runtime_ctx, "outdir", None)
            repo_info = describe_current_turn_git_repo(
                runtime_ctx=runtime_ctx,
                outdir=pathlib.Path(str(outdir or "")),
            )
            lineage_scopes = summarize_current_turn_git_lineage_scopes(
                runtime_ctx=runtime_ctx,
                outdir=pathlib.Path(str(outdir or "")),
            )
        except Exception:
            repo_info = {}
            lineage_scopes = []
        repo_mode = str(repo_info.get("repo_mode") or "").strip()
        repo_status = str(repo_info.get("repo_status") or "").strip()
        if repo_mode:
            lines.append(f"  repo_mode: {repo_mode}")
        if repo_status:
            lines.append(f"  repo_status: {repo_status}")
        if lineage_scopes:
            lines.append("  previous saved workspace paths (pull to bring local; checkout to edit):")
            for item in lineage_scopes[:6]:
                scope = str(item.get("scope") or "").strip()
                files = int(item.get("files") or 0)
                lines.append(f"    - files/{scope} ({files} git-tracked file{'s' if files != 1 else ''})")
            source_turn = last_published_turn or "<published_turn>"
            first_path = str((lineage_scopes[0] or {}).get("scope") or "<path_under_files>").strip().strip("/")
            source_ref = f"fi:{source_turn}.files/{first_path}"
            lines.append("  to focus on one path, use its fi: form, for example:")
            lines.append(f"    react.pull(paths=[\"{source_ref}\"])")
            lines.append(f"    react.checkout(mode=\"replace\", paths=[\"{source_ref}\"])")
        else:
            lines.append("  previous saved workspace paths: none")

    if current_publish:
        status = str(current_publish.get("status") or "").strip() or "unknown"
        lines.append(f"  current_turn_publish: {status}")
        if status == "failed":
            msg = str(current_publish.get("message") or current_publish.get("error") or "").strip()
            if msg:
                lines.append(f"  publish_error: {_shorten(msg, 120)}")
    else:
        lines.append("  current_turn_publish: pending")
        if last_published_turn and last_published_turn != turn_id:
            lines.append(f"  last_published_turn: {last_published_turn} ({last_publish_status})")

    return lines


def build_announce_live_turn_event_lines(
    *,
    runtime_ctx: Optional[RuntimeCtx],
    timeline_blocks: List[Dict[str, Any]],
    max_visible: int = MAX_VISIBLE_LIVE_TURN_EVENTS,
) -> List[str]:
    if runtime_ctx is None:
        return []
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not turn_id:
        return []

    visible: List[Dict[str, Any]] = []
    for block in timeline_blocks:
        btype = str(block.get("type") or "").strip()
        if btype not in {"user.followup", "user.steer"}:
            continue
        if str(block.get("turn_id") or "").strip() != turn_id:
            continue
        visible.append(block)

    if not visible:
        return []

    total = len(visible)
    limit = max(1, int(max_visible or 1))
    kept = visible[-limit:]
    lines: List[str] = ["[LIVE TURN EVENTS]"]
    if len(kept) < total:
        lines.append(f"  - events: showing last {len(kept)} of {total}")
    else:
        lines.append(f"  - events: {total} total")
    for block in kept:
        btype = str(block.get("type") or "").strip()
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        kind = "steer" if btype == "user.steer" else "followup"
        seq = meta.get("sequence")
        explicit = meta.get("explicit")
        seq_suffix = f" seq={seq}" if seq is not None else ""
        explicit_suffix = f" explicit={bool(explicit)}" if explicit is not None else ""
        lines.append(f"    • {kind}{seq_suffix}{explicit_suffix}")
        text = str(block.get("text") or "").strip()
        if text:
            lines.append(f"      text={_shorten(text, 120)}")
        elif kind == "steer":
            lines.append("      text=(empty stop control)")
    return lines


def build_announce_memory_lines(*, runtime_ctx: Optional[RuntimeCtx]) -> List[str]:
    if runtime_ctx is None:
        return []
    if not bool(getattr(runtime_ctx, "memory_announce_enabled", False)):
        return []

    scope_filter = str(getattr(runtime_ctx, "memory_scope_filter", "") or "current_bundle").strip()
    rows = getattr(runtime_ctx, "memory_hotset", None)
    rows = rows if isinstance(rows, list) else []
    limit = getattr(runtime_ctx, "memory_hotset_limit", 8)
    try:
        limit = max(1, int(limit or 8))
    except Exception:
        limit = 8
    error = str(getattr(runtime_ctx, "memory_hotset_error", "") or "").strip()

    lines: List[str] = ["[USER MEMORY HOTSET]"]
    lines.append(
        "  policy: read-only durable user memory; current user message and visible turn context override memory if they conflict."
    )
    lines.append(
        "  use: consult these only when relevant; do not restate them unless they affect the answer."
    )
    lines.append(
        "  format: memory text carries the trigger+rule; context is why/provenance/examples only."
    )
    lines.append(f"  scope_filter: {scope_filter}")
    if error:
        lines.append(f"  status: unavailable ({_shorten(error, 160)})")
        return lines
    if not rows:
        lines.append("  memories: none")
        return lines

    shown = rows[:limit]
    lines.append(f"  memories: showing {len(shown)} of {len(rows)}")
    for row in shown:
        if not isinstance(row, dict):
            continue
        memory_id = str(row.get("id") or "").strip() or "unknown"
        bundle_id = str(row.get("bundle_id") or "").strip()
        bundle_label = bundle_id or "global"
        tier = row.get("tier")
        confidence = row.get("confidence_score")
        salience = row.get("salience_score")
        updated = str(row.get("updated_at") or "").strip()
        labels = row.get("labels")
        label_text = ""
        if isinstance(labels, list) and labels:
            label_text = " labels=[" + ", ".join(str(item) for item in labels[:5]) + "]"
        metrics = []
        if tier is not None:
            metrics.append(f"tier={tier}")
        if bool(row.get("pinned")):
            metrics.append("pinned=true")
        try:
            metrics.append(f"confidence={float(confidence):.2f}")
        except Exception:
            pass
        try:
            metrics.append(f"salience={float(salience):.2f}")
        except Exception:
            pass
        if updated:
            metrics.append(f"updated={updated[:19]}")
        metric_text = (" " + " ".join(metrics)) if metrics else ""
        lines.append(f"  - me:{memory_id} bundle={bundle_label}{metric_text}{label_text}")
        text = str(row.get("memory") or "").strip()
        if text:
            lines.append(f"    {_shorten(' '.join(text.split()), 220)}")
        context = str(row.get("context") or "").strip()
        if context:
            lines.append(f"    context={_shorten(' '.join(context.split()), 160)}")
    return lines


def build_announce_context_cap_lines(*, runtime_ctx: Optional[RuntimeCtx]) -> List[str]:
    if runtime_ctx is None:
        return []

    def _int_attr(name: str, default: int) -> int:
        raw = getattr(runtime_ctx, name, None)
        try:
            value = int(raw)
        except Exception:
            value = default
        return value if value > 0 else default

    def _optional_int_attr(name: str) -> Optional[int]:
        raw = getattr(runtime_ctx, name, None)
        try:
            value = int(raw)
        except Exception:
            return None
        return value if value > 0 else None

    def _bytes_label(value: int) -> str:
        if value >= 1024 * 1024:
            return f"{value // (1024 * 1024)}MB"
        if value >= 1024:
            return f"{value // 1024}KB"
        return f"{value}B"

    def _optional_label(value: Optional[int], *, bytes_value: bool = False) -> str:
        if value is None:
            return "none"
        if bytes_value:
            return _bytes_label(value)
        return str(value)

    read_text = _int_attr("read_visible_max_text_symbols", DEFAULT_READ_VISIBLE_MAX_TEXT_SYMBOLS)
    read_tokens = _int_attr("read_visible_max_tokens", DEFAULT_READ_VISIBLE_MAX_TOKENS)
    read_bytes = _int_attr("read_visible_max_bytes", DEFAULT_READ_VISIBLE_MAX_BYTES)
    ks_read_text = _optional_int_attr("knowledge_read_visible_max_text_symbols")
    ks_read_tokens = _optional_int_attr("knowledge_read_visible_max_tokens")
    ks_read_bytes = _optional_int_attr("knowledge_read_visible_max_bytes")
    try:
        read_fraction = float(getattr(runtime_ctx, "read_visible_context_fraction", None))
    except Exception:
        read_fraction = DEFAULT_READ_VISIBLE_CONTEXT_FRACTION
    if read_fraction <= 0:
        read_fraction = DEFAULT_READ_VISIBLE_CONTEXT_FRACTION
    exec_preview = _int_attr("exec_text_preview_max_symbols", DEFAULT_EXEC_TEXT_PREVIEW_MAX_SYMBOLS)
    tool_preview = _int_attr(
        "tool_result_preview_max_text_symbols",
        DEFAULT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS,
    )
    line_numbers_mode = normalize_line_numbers_mode(
        getattr(runtime_ctx, "line_numbers_mode", LINE_NUMBERS_LINES),
        default=LINE_NUMBERS_LINES,
    )
    return [
        "[CONTEXT CAPS]",
        (
            f"  read text={read_text} tok={read_tokens} bytes={_bytes_label(read_bytes)} ctx_frac={read_fraction:g}; "
            f"ks_read text={_optional_label(ks_read_text)} tok={_optional_label(ks_read_tokens)} bytes={_optional_label(ks_read_bytes, bytes_value=True)}; "
            f"tool_result_preview={tool_preview}; exec_file_preview={exec_preview}; line_numbers={line_numbers_mode}"
        ),
        "  regular text is capped; skills are always uncapped; ks: is uncapped unless knowledge_read_visible_* caps are configured; use stats_only plus ranged react.read items for capped text; exec_stdout=capped",
    ]


def _limit_bytes(raw: Any, *, default: Optional[int] = None) -> Optional[int]:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return default
    text = str(raw).strip().lower()
    if not text:
        return default
    if text in {"0", "false", "off", "none", "no", "disabled", "unlimited"}:
        return None
    multiplier = 1
    for suffix, value in (
        ("gib", 1024 ** 3),
        ("gb", 1024 ** 3),
        ("g", 1024 ** 3),
        ("mib", 1024 ** 2),
        ("mb", 1024 ** 2),
        ("m", 1024 ** 2),
        ("kib", 1024),
        ("kb", 1024),
        ("k", 1024),
        ("b", 1),
    ):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            multiplier = value
            break
    try:
        value = int(float(text) * multiplier)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else None


def _bytes_label(value: Optional[int]) -> str:
    if value is None:
        return "none"
    value = max(0, int(value))
    if value >= 1024 * 1024 * 1024:
        scaled = value / float(1024 * 1024 * 1024)
        return f"{scaled:.1f}GB" if scaled % 1 else f"{int(scaled)}GB"
    if value >= 1024 * 1024:
        scaled = value / float(1024 * 1024)
        return f"{scaled:.1f}MB" if scaled % 1 else f"{int(scaled)}MB"
    if value >= 1024:
        scaled = value / float(1024)
        return f"{scaled:.1f}KB" if scaled % 1 else f"{int(scaled)}KB"
    return f"{value}B"


def _path_is_relative_to(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except Exception:
        return False


def _active_workspace_roots(runtime_ctx: RuntimeCtx) -> List[pathlib.Path]:
    raw_roots = [
        getattr(runtime_ctx, "workdir", None),
        getattr(runtime_ctx, "outdir", None),
    ]
    roots: List[pathlib.Path] = []
    seen: set[str] = set()
    for raw in raw_roots:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            path = pathlib.Path(text).expanduser().resolve(strict=False)
        except Exception:
            path = pathlib.Path(text).expanduser().absolute()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    roots.sort(key=lambda item: len(str(item)))
    filtered: List[pathlib.Path] = []
    for root in roots:
        if any(root != existing and _path_is_relative_to(root, existing) for existing in filtered):
            continue
        filtered.append(root)
    return filtered


def _directory_size_bytes(root: pathlib.Path) -> Tuple[int, int]:
    if not root.exists():
        return 0, 0
    total = 0
    files = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(pathlib.Path(entry.path))
                            continue
                        if entry.is_file(follow_symlinks=False):
                            total += int(entry.stat(follow_symlinks=False).st_size)
                            files += 1
                    except OSError:
                        continue
        except (OSError, FileNotFoundError, NotADirectoryError):
            continue
    return total, files


def _current_active_workspace_size(runtime_ctx: RuntimeCtx) -> Tuple[int, int]:
    total = 0
    files = 0
    for root in _active_workspace_roots(runtime_ctx):
        root_total, root_files = _directory_size_bytes(root)
        total += root_total
        files += root_files
    return total, files


def _runtime_value(cfg: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in cfg:
            return cfg.get(key)
    return None


def _min_present(*values: Optional[int]) -> Optional[int]:
    present = [int(value) for value in values if value is not None]
    return min(present) if present else None


def _effective_runtime_limits(runtime_ctx: RuntimeCtx) -> Dict[str, Optional[int]]:
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        py_exec_cfg = get_settings().PLATFORM.EXEC.PY
    except Exception:
        py_exec_cfg = None
    platform_file = getattr(py_exec_cfg, "EXEC_MAX_FILE_BYTES", "100m") if py_exec_cfg else "100m"
    platform_delta = getattr(py_exec_cfg, "EXEC_MAX_WORKSPACE_DELTA_BYTES", "250m") if py_exec_cfg else "250m"
    platform_workspace = getattr(py_exec_cfg, "EXEC_MAX_WORKSPACE_BYTES", "") if py_exec_cfg else ""

    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.exec_runtime_config import resolve_exec_runtime_profile
        cfg = resolve_exec_runtime_profile(runtime=getattr(runtime_ctx, "exec_runtime", None), profile=None)
    except Exception:
        cfg = getattr(runtime_ctx, "exec_runtime", None) or {}
        if not isinstance(cfg, dict):
            cfg = {}

    file_raw = _runtime_value(cfg, "max_file_bytes")
    delta_raw = _runtime_value(cfg, "max_exec_workspace_delta_bytes")
    workspace_raw = _runtime_value(cfg, "max_workspace_bytes")
    return {
        "max_file_bytes": _limit_bytes(file_raw if file_raw not in (None, "") else platform_file, default=DEFAULT_EXEC_MAX_FILE_BYTES),
        "max_exec_workspace_delta_bytes": _limit_bytes(
            delta_raw if delta_raw not in (None, "") else platform_delta,
            default=DEFAULT_EXEC_MAX_WORKSPACE_DELTA_BYTES,
        ),
        "max_workspace_bytes": _limit_bytes(workspace_raw if workspace_raw not in (None, "") else platform_workspace, default=None),
    }


def build_announce_runtime_limit_lines(*, runtime_ctx: Optional[RuntimeCtx]) -> List[str]:
    if runtime_ctx is None:
        return []
    limits = _effective_runtime_limits(runtime_ctx)
    max_file = limits.get("max_file_bytes")
    max_delta = limits.get("max_exec_workspace_delta_bytes")
    max_workspace = limits.get("max_workspace_bytes")
    try:
        current_bytes, current_files = _current_active_workspace_size(runtime_ctx)
        usage_label = f"{_bytes_label(current_bytes)} across {current_files} file{'s' if current_files != 1 else ''}"
    except Exception:
        current_bytes = 0
        usage_label = "unknown"

    if max_workspace is None:
        remaining_workspace = None
        remaining_label = "unbounded"
    else:
        remaining_workspace = max(0, int(max_workspace) - int(current_bytes))
        remaining_label = _bytes_label(remaining_workspace)

    next_exec_new_bytes = max_delta
    if remaining_workspace is not None:
        next_exec_new_bytes = _min_present(max_delta, remaining_workspace)
    effective_single_file = _min_present(max_file, next_exec_new_bytes)

    lines = ["[RUNTIME LIMITS]"]
    lines.append(
        "  exec file max="
        f"{_bytes_label(max_file)}; exec workspace delta max={_bytes_label(max_delta)}; "
        f"active workspace max={_bytes_label(max_workspace)}"
    )
    lines.append(
        f"  active workspace used={usage_label}; remaining={remaining_label}; "
        f"next exec new bytes max={_bytes_label(next_exec_new_bytes)}; effective single new file max={_bytes_label(effective_single_file)}"
    )
    lines.append(
        "  recomputed each round; materialized attachments and current-turn files/outputs count when present locally"
    )
    return lines


def build_announce_text(
    *,
    iteration: int,
    max_iterations: int,
    base_max_iterations: Optional[int] = None,
    reactive_iteration_credit: int = 0,
    started_at: Optional[str],
    timezone: Optional[str],
    timeline_blocks: List[Dict[str, Any]],
    runtime_ctx: Optional[RuntimeCtx] = None,
    constraints: Optional[List[str]] = None,
    feedback_updates: Optional[List[Dict[str, Any]]] = None,
    feedback_incorporated: bool = False,
    mode: str = "full",
) -> str:
    def _fmt_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        if total < 60:
            return f"{total}s"
        mins, secs = divmod(total, 60)
        if mins < 60:
            return f"{mins}m{secs:02d}s"
        hrs, rem = divmod(mins, 60)
        return f"{hrs}h{rem:02d}m"

    def _mk_box(title: str, *, min_width: int = 35) -> List[str]:
        inner_width = max(min_width, len(title) + 2)
        top = "╔" + ("═" * inner_width) + "╗"
        mid = "║  " + title.ljust(inner_width - 2) + "║"
        bot = "╚" + ("═" * inner_width) + "╝"
        return [top, mid, bot]

    iter_total = int(max_iterations)
    iter_base_total = int(base_max_iterations if base_max_iterations is not None else max_iterations)
    iter_bonus = max(0, int(reactive_iteration_credit or 0))
    if iter_base_total <= 0:
        iter_base_total = iter_total
    iter_display = int(iteration) + 1
    if iter_total > 0:
        iter_display = max(1, min(iter_display, iter_total))
    remaining_iter = max(0, iter_total - iter_display) if iter_total > 0 else 0

    mode = (mode or "full").strip().lower()
    show_title = mode != "budget"
    show_temporal = mode == "full"
    show_plan = mode in {"full", "turn_finalize", "turn_finalize_budget"}
    show_constraints = mode == "full"
    show_status_sections = mode not in {"budget", "turn_finalize_budget"}

    lines: List[str] = []
    if show_title:
        if mode in {"turn_finalize", "turn_finalize_budget"}:
            title = "Turn completed with these stats"
        else:
            title = f"ANNOUNCE — Iteration {iter_display}/{iter_total}"
            if iter_bonus > 0 and iter_total > iter_base_total:
                title += f" ({iter_base_total} + {iter_bonus} reactive bonus)"
        lines.extend(_mk_box(title))
        lines.append("")

    bar_len = 10
    if iter_total > 0:
        used = max(0, min(iter_display, iter_total))
        filled = int(round((used / iter_total) * bar_len))
    else:
        filled = 0
    filled = max(0, min(filled, bar_len))
    bar = ("█" * filled) + ("░" * (bar_len - filled))
    lines.append("[BUDGET]")
    budget_line = f"  iterations  {bar}  {remaining_iter} remaining"
    if iter_bonus > 0 and iter_total > iter_base_total:
        budget_line += f"  (base {iter_base_total} + {iter_bonus} bonus from live reactive events)"
    lines.append(budget_line)
    if started_at:
        try:
            ts = datetime.datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
            elapsed = time.time() - ts
            lines.append(f"  time_elapsed_in_turn   {_fmt_elapsed(elapsed)}")
        except Exception:
            pass

    cap_lines = build_announce_context_cap_lines(runtime_ctx=runtime_ctx)
    if show_status_sections and cap_lines:
        lines.append("")
        lines.extend(cap_lines)

    runtime_limit_lines = build_announce_runtime_limit_lines(runtime_ctx=runtime_ctx)
    if show_status_sections and runtime_limit_lines:
        lines.append("")
        lines.extend(runtime_limit_lines)

    if show_temporal:
        try:
            now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            tz = (timezone or "UTC").strip()
            lines.append("")
            lines.append("[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]")
            lines.append(f"  user_timezone: {tz}")
            lines.append(f"  current_utc_timestamp: {now}")
            lines.append(f"  current_utc_date: {today}")
            lines.append("  All relative dates MUST be interpreted against this context.")
        except Exception:
            pass

    if show_plan:
        plan_lines = build_announce_plan_lines(timeline_blocks=timeline_blocks)
        if not (
            mode == "turn_finalize_budget"
            and not [
                item
                for item in plan_lines
                if item.strip() and "plans: none" not in item.strip().lower() and item.strip() != "[OPEN PLANS]"
            ]
        ):
            lines.append("")
            lines.extend(plan_lines)

    if show_status_sections:
        live_turn_event_lines = build_announce_live_turn_event_lines(
            runtime_ctx=runtime_ctx,
            timeline_blocks=timeline_blocks,
        )
        if live_turn_event_lines:
            lines.append("")
            lines.extend(live_turn_event_lines)

        memory_lines = build_announce_memory_lines(runtime_ctx=runtime_ctx)
        if memory_lines:
            lines.append("")
            lines.extend(memory_lines)

        workspace_lines = build_announce_workspace_lines(
            runtime_ctx=runtime_ctx,
            timeline_blocks=timeline_blocks,
        )
        if workspace_lines:
            lines.append("")
            lines.extend(workspace_lines)

    if show_status_sections and feedback_updates and mode != "turn_finalize":
        updates = [u for u in (feedback_updates or []) if isinstance(u, dict)]
        if updates:
            lines.append("")
            origins = {str(u.get("origin") or "").strip().lower() for u in updates}
            if origins and origins.issubset({"user", ""}):
                lines.append("[NEW USER FEEDBACKS]")
            else:
                lines.append("[NEW FEEDBACKS]")
            for u in updates:
                turn_id = str(u.get("turn_id") or "").strip()
                turn_ts = str(u.get("turn_ts") or "").strip()
                fb_ts = str(u.get("feedback_ts") or "").strip()
                reaction = u.get("reaction")
                text = str(u.get("text") or "").strip()
                parts = []
                if turn_id:
                    parts.append(f"turn {turn_id}")
                if turn_ts:
                    parts.append(f"turn_ts={turn_ts}")
                if fb_ts:
                    parts.append(f"feedback_ts={fb_ts}")
                if reaction is not None:
                    parts.append(f"reaction={reaction}")
                if text:
                    parts.append(f"text={text}")
                if parts:
                    lines.append("  - " + " | ".join(parts))
            if feedback_incorporated:
                lines.append("  (incorporated into turn timeline)")

    if show_constraints and constraints:
        filtered = [item for item in constraints if item]
        if filtered:
            lines.append("")
            lines.append("[CONSTRAINTS]")
            for item in filtered:
                lines.append(f"  - {item}")

    return "\n".join(lines) + "\n"


def build_sources_pool_text(
    *,
    sources_pool: List[Dict[str, Any]],
    prefer_content: bool = False,
    snippet_chars: Optional[int] = 200,
) -> str:
    pool = [s for s in (sources_pool or []) if isinstance(s, dict)]
    pool.sort(key=lambda s: int(s.get("sid") or 0))
    total = len(pool)

    if prefer_content:
        header = f" SOURCES POOL CONTENT  ({total} sources)   explicit read; prefers fetched content over search preview"
    else:
        header = f" SOURCES POOL  ({total} sources)   use react.read to load full text if not on a timeline"
    width = max(80, len(header))
    hr = "━" * width

    title_w = 36
    sid_pad = max(2, len(str(max([int(s.get('sid') or 0) for s in pool] or [0]))))

    def _domain_from_url(url: str) -> str:
        if not url:
            return ""
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.netloc:
                return parsed.netloc
            if "://" not in url and "/" in url:
                return url.split("/")[0]
        except Exception:
            pass
        return url

    def _fmt_tokens(n: int) -> str:
        if n <= 0:
            return "~0 tok"
        if n < 1000:
            return f"~{n} tok"
        return f"~{n/1000:.1f}K tok"

    def _text_from(src: Dict[str, Any]) -> tuple[str, str]:
        keys = ("content", "text", "snippet", "summary", "preview") if prefer_content else ("text", "snippet", "summary", "preview", "content")
        for key in keys:
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                text = val.strip() if prefer_content else " ".join(val.strip().split())
                return text, key
        return "", ""

    lines: List[str] = [hr, header.ljust(width), hr, ""]

    if not pool:
        lines.append("  (none)")
        lines.append("")
    else:
        display = pool

        def _emit(src: Dict[str, Any]) -> None:
            sid = int(src.get("sid") or 0)
            sid_label = str(sid).zfill(sid_pad)
            url = (src.get("url") or src.get("physical_path") or src.get("local_path") or "").strip()
            artifact_path = (src.get("artifact_path") or "").strip()
            source_type = (src.get("source_type") or "").strip().lower()
            mime = (src.get("mime") or "").strip()
            title = (src.get("title") or src.get("name") or url or "(untitled)").strip()
            if title and not (title.startswith("\"") and title.endswith("\"")):
                title = f"\"{title}\""
            title = _shorten(title, title_w)
            if artifact_path and (source_type in {"file", "attachment"} or artifact_path.startswith("fi:")):
                domain = artifact_path
            else:
                domain = (src.get("domain") or "").strip() or _domain_from_url(url)
            domain = domain or "-"
            mime_label = mime or "-"
            text_val, text_key = _text_from(src)
            tok_count = token_count(text_val) if text_val else 0
            if tok_count <= 0:
                size_bytes = src.get("size_bytes")
                if isinstance(size_bytes, (int, float)) and size_bytes > 0:
                    tok_count = max(1, int(size_bytes) // 4)
            tok_label = _fmt_tokens(tok_count)
            lines.append(f"SID:{sid_label}  {title:<{title_w}}  {mime_label}  {domain}  {tok_label}")
            if text_val:
                snippet = text_val if snippet_chars is None else _shorten(text_val, snippet_chars)
                label = f"{text_key}: " if prefer_content and text_key else ""
            else:
                snippet = "<base64>" if (mime.startswith("image/") or mime == "application/pdf") else "<text>"
                label = ""
            lines.append(f"        {label}{snippet}")
            lines.append("")

        for row in display:
            _emit(row)

    lines.append(hr)
    if pool:
        lines.append("  Hint: to see the full snippet if not visible / hide if no need and big (example)")
        lines.append("  Load:  react.read([\"so:sources_pool[1,3,5]\"])")
        lines.append("  Exec:  ctx_tools.fetch_ctx(\"so:sources_pool[1]\") returns rows; for web rows use content first, text second")
        lines.append("  Hide:  react.hide([\"so:sources_pool[1]\"])")
        lines.append(hr)
    return "\n".join(lines) + "\n"

def build_gate_stage_block(*, runtime: RuntimeCtx, gate_out: Any, clarification_questions: Optional[List[str]] = None) -> Dict[str, Any]:
    lines = ["[STAGE: GATE OUTPUT]"]
    route = getattr(gate_out, "route", None) or (gate_out.get("route") if isinstance(gate_out, dict) else "")
    if route:
        lines.append(f"route: {route}")
    conversation_title = getattr(gate_out, "conversation_title", None)
    if conversation_title:
        lines.append(f"conversation_title: {conversation_title}")
    extracted_answer = getattr(gate_out, "extracted_answer", None)
    if conversation_title:
        lines.append(f"extracted_answer: {extracted_answer}")
    needs_clarification = getattr(gate_out, "needs_clarification", None)
    if isinstance(gate_out, dict) and needs_clarification is None:
        needs_clarification = gate_out.get("needs_clarification")
    if needs_clarification is not None:
        lines.append(f"needs_clarification: {bool(needs_clarification)}")
    qs = []
    if isinstance(gate_out, dict):
        qs = [q for q in (gate_out.get("clarification_questions") or []) if isinstance(q, str)]
    if not qs:
        qs = [q for q in (clarification_questions or []) if isinstance(q, str)]
    if qs:
        lines.append("clarification_questions: " + " | ".join(qs))
    turn_id = (getattr(runtime, "turn_id", None) or "").strip()
    return {
        "type": "stage.gate",
        "author": "gate",
        "turn_id": turn_id,
        "ts": getattr(runtime, "started_at", "") or "",
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.gate" if turn_id else "",
    }


def build_feedback_stage_block(*, runtime: RuntimeCtx, reaction: Dict[str, Any]) -> Dict[str, Any]:
    lines = ["[STAGE: FEEDBACK]"]
    origin = (reaction.get("origin") or "").strip()
    if origin:
        lines.append(f"origin: {origin}")
    if reaction.get("reaction") is not None:
        lines.append(f"reaction: {reaction.get('reaction')}")
    if reaction.get("text"):
        lines.append(f"text: {reaction.get('text')}")
    if reaction.get("confidence") is not None:
        lines.append(f"confidence: {reaction.get('confidence')}")
    if reaction.get("from_turn_id"):
        lines.append(f"from_turn_id: {reaction.get('from_turn_id')}")
    ts = reaction.get("ts") or getattr(runtime, "started_at", "") or ""
    turn_id = (getattr(runtime, "turn_id", None) or "").strip()
    return {
        "type": "stage.feedback",
        "author": "user",
        "turn_id": turn_id,
        "ts": ts,
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.feedback" if turn_id else "",
    }


def build_clarification_stage_block(*, runtime: RuntimeCtx, ticket: Any = None, clarification_questions: Optional[List[str]] = None) -> Dict[str, Any]:
    lines = ["[STAGE: CLARIFICATION]"]
    qs = [q for q in (clarification_questions or []) if isinstance(q, str)]
    if qs:
        lines.append("questions: " + " | ".join(qs))
    if ticket is not None:
        try:
            status = getattr(ticket, "status", None) or ticket.get("status")
            title = getattr(ticket, "title", None) or ticket.get("title")
            if status:
                lines.append(f"ticket_status: {status}")
            if title:
                lines.append(f"ticket_title: {title}")
        except Exception:
            pass
    turn_id = (getattr(runtime, "turn_id", None) or "").strip()
    return {
        "type": "stage.clarification",
        "author": "system",
        "turn_id": turn_id,
        "ts": getattr(runtime, "started_at", "") or "",
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.clarification" if turn_id else "",
        "meta": {"questions": qs} if qs else None,
    }


def build_clarification_resolution_block(*, runtime_ctx: RuntimeCtx, ticket: Any = None, resolved_with_answer: bool | None = None) -> Dict[str, Any]:
    lines = ["[STAGE: CLARIFICATION RESOLVED]"]
    if ticket is not None:
        tid = getattr(ticket, "ticket_id", None) or (ticket.get("ticket_id") if isinstance(ticket, dict) else "")
        if tid:
            lines.append(f"ticket_id: {tid}")
        title = getattr(ticket, "title", None) or (ticket.get("title") if isinstance(ticket, dict) else "")
        if title:
            lines.append(f"title: {title}")
        status = getattr(ticket, "status", None) or (ticket.get("status") if isinstance(ticket, dict) else "")
        if status:
            lines.append(f"status: {status}")
    if resolved_with_answer is not None:
        lines.append(f"resolved_with_answer: {bool(resolved_with_answer)}")
    turn_id = runtime_ctx.turn_id
    return {
        "type": "stage.clarification.resolved",
        "author": "system",
        "turn_id": turn_id,
        "ts": runtime_ctx.started_at,
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.clarification.resolved",
    }


def build_suggested_followups_block(
    *,
    runtime: RuntimeCtx,
        suggested_followups: Optional[List[str]] = None,
) -> Dict[str, Any]:
    items = [s for s in (suggested_followups or []) if isinstance(s, str) and s.strip()]
    lines = ["[STAGE: SUGGESTED FOLLOW-UPS]"]
    if items:
        lines.append("items: " + " | ".join(items))
    turn_id = (getattr(runtime, "turn_id", None) or "").strip()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "type": "stage.suggested_followups",
        "author": "system",
        "turn_id": turn_id,
        "ts": ts,
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.suggested_followups" if turn_id else "",
        "meta": {"items": items} if items else None,
    }


def build_tool_catalog(adapters: Optional[List[Dict[str, Any]]] = None,
                       *,
                       exclude_tool_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    tool_catalog: List[Dict[str, Any]] = []
    exclude = set(exclude_tool_ids or [])
    for a in (adapters or []):
        tool_id = a.get("id")
        if tool_id in exclude:
            continue
        doc = a.get("doc") or {}
        item = {
            "id": tool_id,
            "call_template": a.get("call_template"),
            "purpose": doc.get("purpose", ""),
            "is_async": bool(a.get("is_async")),
            "args": doc.get("args", {}),
            "returns": doc.get("returns", ""),
        }
        if "constraints" in doc:
            item["constraints"] = doc["constraints"]
        if "examples" in doc:
            item["examples"] = doc["examples"]
        tool_catalog.append(item)
    return tool_catalog


def build_tools_block(
        tool_catalog: Optional[List[Dict[str, Any]]],
        *,
        header: str,
) -> str:
    if not tool_catalog:
        return ""

    lines: List[str] = [
        header,
        "Available tools extend agent capabilities with specific operations. "
        "Call tools using their full ID (e.g., web_tools.web_search).",
        "",
        "═" * 79,
        "",
        ]

    for idx, tool in enumerate(tool_catalog, start=1):
        tid = tool.get("id", "unknown")
        purpose = tool.get("purpose", "")
        is_async = tool.get("is_async", False)
        args = tool.get("args", {})
        returns = tool.get("returns", "")
        examples = tool.get("examples", [])
        constraints = tool.get("constraints", [])

        async_txt = " [async]" if is_async else ""
        lines.append(f"🔧 [{idx}] {tid}{async_txt}")
        lines.append("")

        if purpose:
            lines.extend(_wrap_lines(purpose, indent="   "))
            lines.append("")

        if args:
            lines.append("   📥 Parameters:")
            for arg_name, arg_info in args.items():
                if isinstance(arg_info, str):
                    parts = arg_info.split(", ", 1)
                    arg_type = parts[0] if parts else "any"
                    arg_desc = parts[1] if len(parts) > 1 else ""
                    default_match = re.search(r"\(default=(.*?)\)$", arg_desc)
                    default_txt = ""
                    if default_match:
                        default_val = default_match.group(1)
                        default_txt = f" [default: {default_val}]" if default_val else " [optional]"
                        arg_desc = arg_desc[:default_match.start()].strip()
                    lines.append(f"       • {arg_name}: {arg_type}{default_txt}")
                    if arg_desc:
                        lines.extend(_wrap_lines(arg_desc, indent="         "))
                else:
                    lines.append(f"       • {arg_name}: {arg_info}")
            lines.append("")

        if returns:
            lines.append("   📤 Returns:")
            lines.extend(_wrap_lines(returns, indent="       "))
            lines.append("")

        call_template = tool.get("call_template", "")
        if call_template:
            match = re.match(r"([^(]+)\(", call_template)
            if match:
                sig = f"{match.group(1)}(...)"
                lines.append(f"   📞 Usage: {sig}")
                lines.append("")

        if constraints:
            lines.append("   ⚠️  Constraints:")
            for constraint in constraints:
                lines.append(f"       • {constraint}")
            lines.append("")

        if examples:
            lines.append("   💡 Examples:")
            for ex_idx, example in enumerate(examples, start=1):
                if isinstance(example, dict):
                    desc = example.get("description", "")
                    code = example.get("code", "")
                    if desc:
                        lines.append(f"       {ex_idx}. {desc}")
                    if code:
                        lines.extend(_wrap_lines(code, indent="          "))
                else:
                    lines.extend(_wrap_lines(str(example), indent="       "))
            lines.append("")

        lines.append("━" * 77)
        lines.append("")

    return "\n".join([l for l in lines if l is not None])


def format_tool_signature(
        tool_id: str,
        params: Dict[str, Any],
        fetch_directives: List[Dict[str, Any]],
        adapters: List[Dict[str, Any]],
        *,
        trim: Optional[int] = None,
) -> str:
    """
    Build call signature like:
      web_tools.web_search(queries=["..."], objective=<turn_42.artifacts.digest_md.text>, n=10)
    Paths injected via fetch_context appear as <path>; multiple paths use " | ".
    Param ordering follows adapter.call_template when available.
    """
    order: List[str] = []
    template = next((a.get("call_template") for a in adapters if a.get("id") == tool_id), "")
    if "(" in template and ")" in template:
        inner = template.split("(", 1)[1].rsplit(")", 1)[0]
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        for p in parts:
            name = p.split("=", 1)[0].strip()
            order.append(name)

    fetch_map: Dict[str, List[str]] = {}
    for fd in (fetch_directives or []):
        pn = (fd or {}).get("param_name")
        path = (fd or {}).get("path")
        if pn and path:
            fetch_map.setdefault(pn, []).append(path)

    keys = list(dict.fromkeys(order + list(params.keys())))
    segs = []
    for k in keys:
        v_inline = params.get(k, None)
        paths = fetch_map.get(k, [])
        if paths:
            placeholder = " | ".join([f"<{p}>" for p in paths])
            if v_inline is None or v_inline == "" or (isinstance(v_inline, (list, dict)) and not v_inline):
                segs.append(f"{k}={placeholder}")
            else:
                vv = _shorten(v_inline, trim) if isinstance(trim, int) else json.dumps(v_inline, ensure_ascii=False)
                segs.append(f"{k}={vv} + {placeholder}")
        else:
            if isinstance(trim, int):
                segs.append(f"{k}={_shorten(v_inline, trim)}")
            else:
                segs.append(f"{k}={json.dumps(v_inline, ensure_ascii=False)}")
    return f"{tool_id}({', '.join(segs)})"


def build_instruction_catalog_block(
        *,
        consumer: str,
        tool_catalog: Optional[List[Dict[str, Any]]] = None,
        tool_catalog_json: Optional[str] = None,
        react_tools: Optional[List[Dict[str, Any]]] = None,
        include_skill_gallery: bool = True,
        skill_tool_catalog: Optional[List[Dict[str, Any]]] = None,
) -> str:
    from kdcube_ai_app.apps.chat.sdk.tools import tools_insights
    tools_list: List[Dict[str, Any]] = []
    if tool_catalog:
        tools_list = list(tool_catalog)
    elif tool_catalog_json:
        try:
            parsed = json.loads(tool_catalog_json or "[]")
            if isinstance(parsed, list):
                tools_list = parsed
        except Exception:
            tools_list = []

    # Normalize entries that come from tool_catalog_for_prompt() (doc nested).
    for tool in tools_list:
        if not isinstance(tool, dict):
            continue
        doc = tool.get("doc")
        if not isinstance(doc, dict):
            continue
        if "purpose" not in tool and doc.get("purpose") is not None:
            tool["purpose"] = doc.get("purpose")
        if "args" not in tool and doc.get("args") is not None:
            tool["args"] = doc.get("args")
        if "returns" not in tool and doc.get("returns") is not None:
            tool["returns"] = doc.get("returns")
        if "constraints" not in tool and doc.get("constraints") is not None:
            tool["constraints"] = doc.get("constraints")
        if "examples" not in tool and doc.get("examples") is not None:
            tool["examples"] = doc.get("examples")

    for tool in tools_list:
        if not isinstance(tool, dict):
            continue
        tid = tool.get("id")
        if tid != "exec_tools.execute_code_python":
            continue
        args = tool.get("args")
        if isinstance(args, dict) and "code" in args:
            args = dict(args)
            args.pop("code", None)
            tool["args"] = args
        purpose = tool.get("purpose") or ""
        if "channel:code" not in purpose:
            note = (
                "Code is provided via <channel:code> when using this tool from React decision; "
                "omit params.code in JSON."
            )
            tool["purpose"] = f"{purpose}\n{note}".strip()

    react_tools = react_tools or []
    ids = {t.get("id") for t in tools_list if isinstance(t, dict)}
    for it in react_tools:
        if it.get("id") not in ids:
            tools_list.append(it)
    skill_tools_list = list(skill_tool_catalog) if skill_tool_catalog is not None else list(tools_list)
    if include_skill_gallery:
        set_active_skill_tool_catalog(skill_tools_list)

    tool_block = ""
    if tools_list:
        react_ids: set[str] = set()
        for t in react_tools:
            tid = (t or {}).get("id")
            if isinstance(tid, str) and tid:
                react_ids.add(tid)
        for t in tools_list:
            tid = (t or {}).get("id")
            if isinstance(tid, str) and tid.startswith("react."):
                react_ids.add(tid)

        exec_ids: set[str] = set()
        exec_ids.update(tools_insights.PY_EXEC_ONLY_TOOL_IDS)
        for t in tools_list:
            tid = (t or {}).get("id")
            if not isinstance(tid, str) or not tid:
                continue
            if tid.startswith("web_tools."):
                react_ids.add(tid)
                continue
            if tools_insights.is_exec_tool(tid):
                react_ids.add(tid)
                continue
            # rendering_tools.write_* remain in common tools

        react_only = [t for t in tools_list if t.get("id") in react_ids]
        exec_only = [t for t in tools_list if t.get("id") in exec_ids and t.get("id") not in react_ids]
        common = [
            t for t in tools_list
            if t.get("id") not in react_ids and t.get("id") not in exec_ids
        ]

        parts_tools: List[str] = []
        react_block = build_tools_block(react_only, header="[AVAILABLE REACT TOOLS]")
        if react_block:
            parts_tools.append(react_block)
        common_block = build_tools_block(common, header="[AVAILABLE COMMON TOOLS]")
        if common_block:
            parts_tools.append(common_block)
        exec_block = build_tools_block(exec_only, header="[TOOLS AVAILABLE ONLY IN CODE SNIPPET]")
        if exec_block:
            parts_tools.append(exec_block)
        tool_block = "\n\n".join([p for p in parts_tools if p.strip()])

    skill_block = ""
    if include_skill_gallery:
        skill_block = skills_gallery_text(
            consumer=consumer,
            tool_catalog=skill_tools_list,
        )
    active_block = ""

    parts = []
    if tool_block:
        parts.append(tool_block)
    if skill_block:
        parts.append(skill_block)
    if active_block:
        parts.append(active_block)
    return "\n\n".join(parts)

def build_embedding_presentation(blocks: List[Dict[str, Any]]) -> str:
    """
    Build a compact presentation for semantic indexing.
    Only include external artifacts (file/display) from react tool results.
    """
    def _maybe_parse_json(val: str) -> Optional[Any]:
        try:
            return json.loads(val)
        except Exception:
            return None

    lines: List[str] = []
    call_id_to_tool_id: Dict[str, str] = {}
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") != "react.tool.call":
            continue
        payload = _maybe_parse_json(b.get("text") or "") if (b.get("mime") or "").strip() == "application/json" else None
        tool_id = ""
        tool_call_id = ""
        if isinstance(payload, dict):
            tool_id = (payload.get("tool_id") or "").strip()
            tool_call_id = (payload.get("tool_call_id") or "").strip()
        meta_local = b.get("meta") if isinstance(b.get("meta"), dict) else {}
        if not tool_call_id:
            tool_call_id = (meta_local.get("tool_call_id") or b.get("call_id") or "").strip()
        if tool_call_id and tool_id:
            call_id_to_tool_id[tool_call_id] = tool_id
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") != "react.tool.result":
            continue
        if (b.get("mime") or "").strip() != "application/json":
            continue
        text = b.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            meta = json.loads(text)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        visibility = (meta.get("visibility") or "").strip()
        kind = (meta.get("kind") or "").strip()
        if visibility != "external" or kind not in {"file", "display"}:
            continue
        artifact_path = (meta.get("artifact_path") or "").strip()
        physical_path = (meta.get("physical_path") or "").strip()
        mime = (meta.get("mime") or "").strip()
        tool_id = (meta.get("tool_id") or "").strip()
        tool_call_id = (meta.get("tool_call_id") or "").strip()
        if not tool_id and tool_call_id:
            tool_id = call_id_to_tool_id.get(tool_call_id, "")
        parts = []
        if artifact_path:
            parts.append(f"artifact_path={artifact_path}")
        if physical_path:
            parts.append(f"physical_path={physical_path}")
        if mime:
            parts.append(f"mime={mime}")
        if kind:
            parts.append(f"kind={kind}")
        if tool_id:
            parts.append(f"tool_id={tool_id}")
        if tool_call_id:
            parts.append(f"tool_call_id={tool_call_id}")
        if parts:
            lines.append("- " + " | ".join(parts))
    return "\n".join(lines).strip()
