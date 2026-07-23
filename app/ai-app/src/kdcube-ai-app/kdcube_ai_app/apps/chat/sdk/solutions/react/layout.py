# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/layout.py

import ast
import difflib
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
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace.references import CONVERSATION_FILE_REF_PREFIX

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


def latest_assistant_completion_text(scratchpad: Any) -> str:
    texts = assistant_completion_texts(scratchpad)
    return texts[-1] if texts else ""


def _normalized_completion_text(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def completion_attempt_texts_equivalent(
    left: Any,
    right: Any,
    *,
    threshold: float = 0.9,
) -> bool:
    """Two completion attempts are equivalent when their normalized texts are
    identical or highly similar (trivial wording variations between re-emits
    of the same answer). Cheap by construction: whitespace-collapsed casefold
    plus a length precheck before the sequence ratio."""
    a = _normalized_completion_text(left)
    b = _normalized_completion_text(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (len(a), len(b)) if len(a) <= len(b) else (len(b), len(a))
    if longer and (shorter / longer) < threshold:
        return False
    # autojunk off: on prose-sized strings the popular-character heuristic
    # (spaces) collapses the match and near-identical texts score low.
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if matcher.quick_ratio() < threshold:
        return False
    return matcher.ratio() >= threshold


def collapse_equivalent_completion_texts(texts: List[str]) -> List[str]:
    """Collapse consecutive equivalent completion attempts into one answer
    each (keeping the LAST text of each run). A turn legitimately produces
    multiple answers when followups arrive mid-turn; equivalent re-attempts of
    the SAME completion are one answer."""
    collapsed: List[str] = []
    for raw in texts or []:
        text = str(raw or "").strip()
        if not text:
            continue
        if collapsed and completion_attempt_texts_equivalent(collapsed[-1], text):
            collapsed[-1] = text
            continue
        collapsed.append(text)
    return collapsed


def build_assistant_completion_deferred_blocks(
    *,
    runtime: RuntimeCtx,
    attempt_index: int,
    reason: str,
    ts: Optional[str],
    block_factory,
) -> List[Dict[str, Any]]:
    """The rendered VERDICT for a completion attempt the runtime held back
    from finalizing the turn. Every attempt marker gets an outcome: without
    this block the model only sees its own provisional attempt and re-emitting
    it is the only rational move."""
    tid = (getattr(runtime, "turn_id", None) or "").strip()
    if not tid:
        return []
    try:
        idx = max(1, int(attempt_index))
    except Exception:
        idx = 1
    reason_key = str(reason or "").strip() or "external_events_arrived"
    if reason_key == "external_events_arrived":
        body = (
            f"Completion attempt {idx} was held back from finalizing the turn: new conversation "
            "events arrived while it was being written, and the timeline above now includes them. "
            "Review the newest events and finish the turn with a `complete` action — reuse attempt "
            f"{idx}'s text unchanged if the new events do not change the outcome."
        )
    else:
        body = (
            f"Completion attempt {idx} was held back from finalizing the turn while the conversation "
            "event lane settled. Nothing further is required from the events. Finish the turn with a "
            f"`complete` action — attempt {idx}'s text is a valid final answer to reuse."
        )
    meta: Dict[str, Any] = {
        "completion_attempt_index": idx,
        "completion_attempt_outcome": "deferred",
        "completion_attempt_defer_reason": reason_key,
    }
    return [block_factory(
        type="assistant.completion.attempt.outcome",
        author="assistant",
        turn_id=tid,
        ts=str(ts or "").strip(),
        path=f"conv:ar:{tid}.assistant.completion.attempt.{idx}.outcome",
        text=body,
        meta=meta,
    )]


def assistant_completion_texts(scratchpad: Any) -> List[str]:
    entries = getattr(scratchpad, "assistant_completion_attempts", None)
    if not isinstance(entries, list):
        return []
    texts: List[str] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if text:
            texts.append(text)
    return texts


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
    event_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    tid = (getattr(runtime, "turn_id", None) or "").strip()
    if not tid:
        return []
    prompt_message_id = "m0"
    ts = (getattr(runtime, "started_at", "") or "").strip()
    blocks: List[Dict[str, Any]] = []
    user_text = (user_text or "").strip()
    if user_text:
        prompt_path = f"conv:ar:{tid}.user.prompt"
        prompt_meta: Dict[str, Any] = {"message_id": prompt_message_id}
        if event_type:
            prompt_meta["event_type"] = event_type
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
        path_root=f"conv:fi:{tid}.user.attachments",
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
        path = f"conv:ar:{tid}.assistant.completion" if idx == total else f"conv:ar:{tid}.assistant.completion.{idx}"
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
        path=f"conv:ar:{tid}.assistant.completion.attempt.{idx}",
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
        path=f"conv:ws:{tid}.conv.working.summary.attempt.{idx}",
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
        path=f"conv:ar:{tid}.react.decision.raw.interrupted.{int(iteration or 0)}",
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
    """ANNOUNCE [WORKSPACE]: the live material map of the sparse per-turn workspace.

    Two views, rebuilt every round:
      LOCAL  — exactly what is materialized on disk this turn (the only content
               local-bytes tools can touch directly).
      REMOTE — top-level git-branch projects that can be pulled, plus the single
               "latest committed turn" anchor used to build every pull/checkout ref.
    """
    if runtime_ctx is None:
        return []
    impl = get_workspace_implementation(runtime_ctx)
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    lines: List[str] = ["[WORKSPACE]"]
    if turn_id:
        lines.append(f"  current_turn_root: {turn_id}/")

    outdir_raw = str(getattr(runtime_ctx, "outdir", "") or "").strip()
    artifact_root = None
    if outdir_raw:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace import artifact_outdir_for
            artifact_root = artifact_outdir_for(pathlib.Path(outdir_raw), create=False)
        except Exception:
            artifact_root = None

    try:
        roots = list_materialized_turn_roots(runtime_ctx=runtime_ctx) or []
    except Exception:
        roots = []

    # checkout provenance: current files/<scope> -> source ref
    provenance: Dict[str, str] = {}
    current_checkout = latest_workspace_checkout_event(timeline_blocks, turn_id=turn_id) if turn_id else None
    if current_checkout:
        for item in (current_checkout.get("checked_out_from") or []):
            ref = str(item or "").strip()
            m = re.search(r"\.git/projects/([^/]+)", ref)
            if m:
                provenance.setdefault(m.group(1), ref)

    # per-project MODIFIED (uncommitted changes in the current-turn repo)
    modified_scopes: set = set()
    if outdir_raw:
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.git_workspace import current_turn_modified_files_scopes
            modified_scopes = current_turn_modified_files_scopes(runtime_ctx=runtime_ctx, outdir=pathlib.Path(outdir_raw)) or set()
        except Exception:
            modified_scopes = set()

    def _top_dirs(root_name: str, namespace: str) -> List[str]:
        if artifact_root is None:
            return []
        base = artifact_root / root_name / namespace
        if not base.is_dir():
            return []
        try:
            return sorted((c.name for c in base.iterdir() if c.is_dir()), key=str.lower)
        except Exception:
            return []

    def _workdir_entries() -> List[pathlib.Path]:
        if artifact_root is None or not artifact_root.is_dir():
            return [artifact_root / turn_id] if turn_id and artifact_root is not None else []
        try:
            children = list(artifact_root.iterdir())
        except Exception:
            children = []
        turn_root_names = set(roots)
        current: List[pathlib.Path] = []
        prior_turns: List[pathlib.Path] = []
        for child in children:
            name = child.name
            if turn_id and name == turn_id:
                current.append(child)
            elif child.is_dir() and name in turn_root_names:
                prior_turns.append(child)
        if turn_id and not current and artifact_root is not None:
            current.append(artifact_root / turn_id)
        current.sort(key=lambda p: p.name.lower())
        prior_turns.sort(key=lambda p: p.name, reverse=True)
        return current + prior_turns

    def _direct_children(base: pathlib.Path) -> List[pathlib.Path]:
        if not base.is_dir():
            return []
        try:
            return sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except Exception:
            return []

    def _rel_files(base: pathlib.Path, *, cap: int = 120) -> Tuple[List[str], int]:
        if not base.is_dir():
            return [], 0
        rels: List[str] = []
        try:
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [
                    d for d in sorted(dirnames, key=str.lower)
                    if d not in {".git", "__pycache__"}
                ]
                for fn in sorted(filenames, key=str.lower):
                    if fn in {".DS_Store"}:
                        continue
                    rels.append(os.path.relpath(os.path.join(dirpath, fn), base))
        except Exception:
            return [], 0
        return rels[:cap], len(rels)

    def _render_projects_namespace(
        out: List[str],
        root_path: pathlib.Path,
        *,
        is_current: bool,
        show_empty: bool = False,
    ) -> None:
        projects_root = root_path / "git" / "projects"
        children = _direct_children(projects_root)
        if not children:
            if show_empty:
                out.append("    git/projects/ (empty)")
            return
        out.append("    git/projects/")
        for child in children:
            name = child.name
            ann = []
            if is_current and child.is_dir() and name in provenance:
                ann.append(f"checked out from {provenance[name]}")
            if is_current and child.is_dir() and name in modified_scopes:
                ann.append("MODIFIED this turn")
            suffix = ("      \u2014 " + " \u00b7 ".join(ann)) if ann else ""
            marker = "/" if child.is_dir() else ""
            out.append(f"      {name}{marker}{suffix}")

    def _render_snapshots_namespace(
        out: List[str],
        root_path: pathlib.Path,
        *,
        show_empty: bool = False,
    ) -> None:
        snapshots_root = root_path / "git" / "snapshots"
        files, total = _rel_files(snapshots_root)
        if not files:
            if show_empty:
                out.append("    git/snapshots/ (empty)")
            return
        out.append("    git/snapshots/")
        for rel in files:
            out.append(f"      {rel}")
        if total > len(files):
            out.append(f"      \u2026 +{total - len(files)} more")

    def _render_recursive_namespace(
        out: List[str],
        ns_path: pathlib.Path,
        *,
        is_current: bool,
        show_empty: bool = False,
    ) -> None:
        name = ns_path.name
        if name in {".git", "__pycache__"}:
            return
        if ns_path.is_file():
            out.append(f"    {name}")
            return
        if not ns_path.is_dir():
            if show_empty:
                out.append(f"    {name}/ (empty)")
            return
        files, total = _rel_files(ns_path)
        if not files:
            if show_empty:
                out.append(f"    {name}/ (empty)")
            return
        out.append(f"    {name}/")
        for rel in files:
            suffix = ""
            if is_current and name == "files":
                suffix = "   \u2014 produced this turn"
            elif is_current and name == "attachments":
                suffix = "   \u2014 user upload this turn"
            elif is_current and name == "external" and "/attachments/" in rel:
                suffix = "   \u2014 live event attachment"
            out.append(f"      {rel}{suffix}")
        if total > len(files):
            out.append(f"      \u2026 +{total - len(files)} more")

    # local git/projects/ presence (for cross-tagging REMOTE rows)
    current_files_projects = set(_top_dirs(turn_id, "git/projects")) if turn_id else set()
    readonly_files_projects: set = set()
    for root in roots:
        if root != turn_id:
            readonly_files_projects.update(_top_dirs(root, "git/projects"))

    # ---------- LOCAL ----------
    lines.append("")
    lines.append("  LOCAL — materialized on disk THIS turn.")
    lines.append("  This tree lists actual files already present under the artifact workdir.")
    lines.append("  Timeline conv:fi: refs that are not listed here are hosted/unhydrated; use react.pull to hydrate them before local-byte tools.")
    lines.append("  react.read may inspect provider-rendered text for a hosted ref, but it does not make local bytes appear here.")
    lines.append("  react.rg / react.patch / exec can touch only paths listed here or paths you create in this turn.")
    local_entries = _workdir_entries()
    local_lines: List[str] = []
    for entry in local_entries:
        name = entry.name
        is_current = bool(turn_id and name == turn_id)
        is_turn_root = name in set(roots)
        if is_current:
            label = "current turn \u00b7 writable"
        elif is_turn_root:
            label = "pulled reference \u00b7 read-only \u2014 checkout into the current turn to write"
        else:
            label = "local workdir entry"
        if not entry.is_dir() and not is_current:
            continue

        entry_lines: List[str] = []
        children = _direct_children(entry)
        by_name = {child.name: child for child in children}
        rendered: set[str] = set()
        if is_current:
            _render_projects_namespace(entry_lines, entry, is_current=is_current, show_empty=True)
            _render_snapshots_namespace(entry_lines, entry, show_empty=True)
            rendered.add("git")
        elif (entry / "git").is_dir():
            _render_projects_namespace(entry_lines, entry, is_current=is_current)
            _render_snapshots_namespace(entry_lines, entry)
            rendered.add("git")
        ordered_names = ["git", "files", "attachments", "external"]
        for child_name in ordered_names:
            child = by_name.get(child_name)
            if child_name in rendered:
                continue
            if child is None:
                if is_current:
                    _render_recursive_namespace(entry_lines, entry / child_name, is_current=is_current, show_empty=True)
                    rendered.add(child_name)
                continue
            _render_recursive_namespace(entry_lines, child, is_current=is_current, show_empty=is_current)
            rendered.add(child_name)
        for child in children:
            if child.name in rendered:
                continue
            _render_recursive_namespace(entry_lines, child, is_current=is_current)
        if entry_lines or is_current:
            local_lines.append(f"  {name}/   ({label})")
            local_lines.extend(entry_lines)
    if local_lines:
        lines.extend(local_lines)
    else:
        lines.append("  (no materialized files in the artifact workdir yet)")

    # ---------- REMOTE (git only) ----------
    if impl == "git":
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.git_workspace import summarize_current_turn_git_lineage_scopes
            lineage = summarize_current_turn_git_lineage_scopes(runtime_ctx=runtime_ctx, outdir=pathlib.Path(outdir_raw)) if outdir_raw else []
        except Exception:
            lineage = []
        any_publish = latest_workspace_publish_event(timeline_blocks)
        anchor = str((any_publish or {}).get("turn_id") or "").strip() if any_publish else ""
        projects = [
            str(it.get("scope") or "").strip().strip("/")
            for it in (lineage or [])
            if str(it.get("namespace") or "git/projects").strip().strip("/") == "git/projects"
        ]
        projects = [p for p in projects if p]
        lines.append("")
        lines.append("  REMOTE git branch \u2014 top-level projects you can pull (NOT local until pulled).")
        if anchor:
            lines.append(f"  latest committed turn: {anchor}")
            lines.append("  \u2192 pull any project or subpath at its latest by building the ref with THIS turn id (same anchor for all projects):")
            lines.append(f"        conv:fi:{anchor}.git/projects/<project>[/<subpath>]")
        if projects:
            for p in projects[:12]:
                if p in current_files_projects:
                    tag = "   [writable in current turn]"
                elif p in readonly_files_projects:
                    tag = "   [pulled \u00b7 read-only]"
                else:
                    tag = ""
                lines.append(f"    git/projects/{p}{tag}")
            if len(projects) > 12:
                lines.append(f"    \u2026 +{len(projects) - 12} more")
        elif anchor:
            lines.append("    (no committed projects yet)")
        if anchor and projects:
            ex = projects[0]
            lines.append("  examples:")
            lines.append(f'    pull a subfolder:  react.pull(paths=["conv:fi:{anchor}.git/projects/{ex}/<subpath>"])')
            lines.append(f'    make writable:     react.checkout(mode="replace", paths=["conv:fi:{anchor}.git/projects/{ex}"])')

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
    attachment_blocks: List[Dict[str, Any]] = []
    for block in timeline_blocks:
        btype = str(block.get("type") or "").strip()
        block_turn_id = str(block.get("turn_id") or "").strip()
        if block_turn_id != turn_id:
            continue
        if btype == "user.attachment.meta":
            attachment_blocks.append(block)
            continue
        if btype not in {"user.followup", "user.steer"}:
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

    def _event_attachment_blocks(event_block: Dict[str, Any]) -> List[Dict[str, Any]]:
        meta = event_block.get("meta") if isinstance(event_block.get("meta"), dict) else {}
        batch_id = str(meta.get("batch_id") or "").strip()
        event_id = str(meta.get("event_id") or meta.get("message_id") or "").strip()
        seq = meta.get("sequence")
        out: List[Dict[str, Any]] = []
        for att in attachment_blocks:
            att_meta = att.get("meta") if isinstance(att.get("meta"), dict) else {}
            att_batch_id = str(att_meta.get("batch_id") or "").strip()
            att_event_id = str(att_meta.get("message_id") or att_meta.get("event_id") or "").strip()
            att_seq = att_meta.get("sequence")
            if batch_id and att_batch_id == batch_id:
                out.append(att)
                continue
            if event_id and att_event_id == event_id:
                out.append(att)
                continue
            if seq is not None and att_seq == seq:
                out.append(att)
                continue
        return out

    def _attachment_label(block: Dict[str, Any]) -> str:
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        path = str(block.get("path") or meta.get("logical_path") or "").strip()
        filename = str(meta.get("filename") or "").strip()
        if not filename and path:
            filename = path.rsplit("/", 1)[-1]
        mime = str(meta.get("mime") or block.get("mime") or "").strip()
        parts = [filename or "(attachment)"]
        if mime:
            parts.append(mime)
        if path:
            parts.append(f"ref={path}")
        return " | ".join(parts)

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
        attachments = _event_attachment_blocks(block)
        if attachments:
            lines.append(f"      attachments={len(attachments)}")
            for idx, attachment in enumerate(attachments[:3], start=1):
                lines.append(f"        attachment.{idx} {_shorten(_attachment_label(attachment), 180)}")
            if len(attachments) > 3:
                lines.append(f"        … +{len(attachments) - 3} more")
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
        "  policy: durable user memory; current user message and visible turn context override memory if they conflict."
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
        object_ref = str(row.get("object_ref") or "").strip() or f"mem:record:{memory_id}"
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
        lines.append(f"  - {object_ref} bundle={bundle_label}{metric_text}{label_text}")
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

    def _bytes_label(value: int) -> str:
        if value >= 1024 * 1024:
            return f"{value // (1024 * 1024)}MB"
        if value >= 1024:
            return f"{value // 1024}KB"
        return f"{value}B"

    read_text = _int_attr("read_visible_max_text_symbols", DEFAULT_READ_VISIBLE_MAX_TEXT_SYMBOLS)
    read_tokens = _int_attr("read_visible_max_tokens", DEFAULT_READ_VISIBLE_MAX_TOKENS)
    read_bytes = _int_attr("read_visible_max_bytes", DEFAULT_READ_VISIBLE_MAX_BYTES)
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
            f"tool_result_preview={tool_preview}; exec_file_preview={exec_preview}; line_numbers={line_numbers_mode}"
        ),
        "  regular text is capped; skills are always uncapped; use stats_only plus ranged react.read items for capped text; exec_stdout=capped",
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
        "  recomputed each round; materialized attachments, current-turn files, and git/projects count when present locally"
    )
    return lines


def build_announce_inactive_tools_lines(*, runtime_ctx: Optional[RuntimeCtx]) -> List[str]:
    """`[INACTIVE TOOLS THIS TURN]` — tools dropped for this turn (e.g. unmet
    connected-account claims), grouped by provider.

    Lives in ANNOUNCE (the uncached, per-round zone) rather than the static
    instructions: the set changes the moment the user connects an account or
    flips a toggle, and the cached system-prompt slice must stay byte-stable.
    """
    if runtime_ctx is None:
        return []
    groups = getattr(runtime_ctx, "inactive_tools", None)
    groups = [g for g in groups if isinstance(g, dict)] if isinstance(groups, list) else []
    if not groups:
        return []
    lines = ["[INACTIVE TOOLS THIS TURN]"]
    for group in groups:
        label = str(group.get("provider_label") or group.get("provider_id") or "External").strip() or "External"
        tools = sorted({
            str(t).rsplit(".", 1)[-1]
            for t in (group.get("tools") or [])
            if str(t or "").strip()
        })
        tool_list = ", ".join(tools)
        lines.append(
            f"  - {label} tools ({tool_list}): the user has no connected {label} account; "
            f"they can connect one in Connection Hub."
        )
    lines.append(
        "  Work with the remaining tools. When the request needs an inactive tool, say which "
        "account to connect (named above) instead of attempting the call."
    )
    return lines


def build_announce_reactivated_tools_lines(*, runtime_ctx: Optional[RuntimeCtx]) -> List[str]:
    """`[CONNECTED ACCOUNTS UPDATE]` — providers whose tools became active
    again since the previous turn (the user connected or approved the account
    mid-conversation). The conversation history still carries the earlier
    "connect your account" prose, so this block states the current truth
    explicitly and supersedes it."""
    if runtime_ctx is None:
        return []
    groups = getattr(runtime_ctx, "reactivated_tools", None)
    groups = [g for g in groups if isinstance(g, dict)] if isinstance(groups, list) else []
    if not groups:
        return []
    lines = ["[CONNECTED ACCOUNTS UPDATE]"]
    for group in groups:
        label = str(group.get("provider_label") or group.get("provider_id") or "External").strip() or "External"
        tools = sorted({
            str(t).rsplit(".", 1)[-1]
            for t in (group.get("tools") or [])
            if str(t or "").strip()
        })
        tool_list = ", ".join(tools)
        lines.append(
            f"  - {label} account is connected; tools ({tool_list}) are active this turn."
        )
    lines.append(
        "  This is the current state — it supersedes earlier notes in this conversation "
        "that named these tools unavailable. Use them directly for the user's request."
    )
    return lines


def _delegation_status_by_child(timeline_blocks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-child delegation status derived from timeline blocks.

    Fork marker blocks open a row (caption, alias/class, budget); folded
    ``subagent.*`` event blocks advance its status: contributions count up,
    a converged/failed event closes it.
    """
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.events import (
        SUBAGENT_CONTRIBUTION_EVENT_KIND,
        SUBAGENT_CONVERGED_EVENT_KIND,
        SUBAGENT_FAILED_EVENT_KIND,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.fork import (
        FORK_MARKER_BLOCK_TYPE,
    )

    def _event_child_id(meta: Dict[str, Any]) -> str:
        payload = meta.get("payload") if isinstance(meta.get("payload"), dict) else {}
        stamp = payload.get("subagent") if isinstance(payload.get("subagent"), dict) else {}
        event_meta = meta.get("event") if isinstance(meta.get("event"), dict) else {}
        nested = event_meta.get("payload") if isinstance(event_meta.get("payload"), dict) else {}
        nested_event = nested.get("event") if isinstance(nested.get("event"), dict) else {}
        return str(
            payload.get("child_conversation_id")
            or stamp.get("child_conversation_id")
            or nested_event.get("child_conversation_id")
            or ""
        ).strip()

    rows: Dict[str, Dict[str, Any]] = {}
    for block in timeline_blocks or []:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "").strip()
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        if btype == FORK_MARKER_BLOCK_TYPE:
            child_id = str(meta.get("child_conversation_id") or "").strip()
            if not child_id:
                continue
            rows[child_id] = {
                "caption": str(meta.get("charter_summary") or "").strip(),
                "agent_title": str(meta.get("agent_title") or "").strip(),
                "agent_alias": str(meta.get("agent_alias") or "").strip(),
                "agent_class": str(meta.get("agent_class") or "").strip(),
                "contributions": 0,
                "outcome": "",
            }
            continue
        event_type = str(meta.get("event_type") or btype or "").strip()
        if event_type not in {
            SUBAGENT_CONTRIBUTION_EVENT_KIND,
            SUBAGENT_CONVERGED_EVENT_KIND,
            SUBAGENT_FAILED_EVENT_KIND,
        }:
            continue
        child_id = _event_child_id(meta)
        row = rows.get(child_id)
        if row is None:
            continue
        if event_type == SUBAGENT_CONTRIBUTION_EVENT_KIND:
            row["contributions"] = int(row.get("contributions") or 0) + 1
        elif event_type == SUBAGENT_CONVERGED_EVENT_KIND:
            row["outcome"] = "converged"
            row["outcome_turn"] = str(block.get("turn") or "").strip()
        else:
            row["outcome"] = "failed"
            row["outcome_turn"] = str(block.get("turn") or "").strip()
    return rows


def build_announce_delegation_lines(
    *,
    runtime_ctx: Optional[RuntimeCtx],
    timeline_blocks: List[Dict[str, Any]],
) -> List[str]:
    """`[DELEGATION]` — the situational half of react.delegate's contract.

    The tool doc (cached instructions) is static and identical for every
    user; who the agent is, which helper aliases exist, and what is live
    right now are per-round facts, so they render here: the agent's own
    strength class (matched, never guessed), the helper aliases with class +
    caption, and one status line per delegation on this conversation's
    timeline. Parents only — a subagent completes its own assignment and
    gets no section.
    """
    if runtime_ctx is None:
        return []
    if int(getattr(runtime_ctx, "subagent_depth", 0) or 0) >= 1:
        return []
    if getattr(runtime_ctx, "subagent_spawner", None) is None:
        return []
    facts = getattr(runtime_ctx, "subagent_model_facts", None)
    facts = facts if isinstance(facts, dict) else {}
    aliases = [
        row for row in (facts.get("aliases") or [])
        if isinstance(row, dict) and str(row.get("alias") or "").strip()
    ]
    lines = ["[DELEGATION]"]
    own_alias = str(facts.get("own_alias") or "").strip()
    if own_alias:
        # The agent's identity in the SAME vocabulary as the helper list, so
        # "would a helper be stronger than me?" is a direct comparison.
        own_class = str(facts.get("own_class") or "").strip()
        lines.append(
            f"  you are: {own_alias}" + (f" [{own_class}]" if own_class else "")
        )
    if aliases:
        default_alias = str(facts.get("default_alias") or "").strip()
        lines.append("  helper aliases (react.delegate agent_alias):")
        for row in aliases:
            alias = str(row.get("alias") or "").strip()
            klass = str(row.get("class") or "").strip()
            caption = str(row.get("caption") or "").strip()
            entry = f"    - {alias}"
            if klass:
                entry += f" [{klass}]"
            if alias == default_alias:
                entry += " (default)"
            if caption:
                entry += f": {caption}"
            lines.append(entry)
    delegations = _delegation_status_by_child(timeline_blocks or [])
    # Lifecycle: an unresolved delegation is a live obligation and renders
    # every round; a terminal one renders only during the turn its completion
    # folded — from the next turn on the outcome is ordinary timeline history.
    current_turn = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    delegations = {
        child_id: row
        for child_id, row in delegations.items()
        if not str(row.get("outcome") or "")
        or str(row.get("outcome_turn") or "") == current_turn
    }
    if delegations:
        lines.append("  delegations in this conversation:")
        for child_id, row in delegations.items():
            outcome = str(row.get("outcome") or "")
            if outcome:
                status = outcome
            elif int(row.get("contributions") or 0) > 0:
                status = f"contributed {int(row['contributions'])}"
            else:
                status = "running"
            caption = _shorten(str(row.get("caption") or "") or f"conv_{child_id}", 100)
            # Lead with the title the delegating agent named in its own
            # react.delegate call, so it recognizes this live row as the
            # helper it launched without decoding conversation ids.
            title = str(row.get("agent_title") or "").strip()
            label = f"{title} — {caption}" if title else caption
            entry = f"    - {label}"
            alias = str(row.get("agent_alias") or "").strip()
            klass = str(row.get("agent_class") or "").strip()
            if alias:
                entry += f" — {alias}"
                if klass:
                    entry += f" [{klass}]"
            entry += f" — {status}"
            lines.append(entry)
    if len(lines) == 1:
        return []
    return lines


def build_announce_cold_turn_lines(*, runtime_ctx: Optional[RuntimeCtx]) -> List[str]:
    """`[CACHE]` — one small turn-local line when a selection change applied on
    a warm conversation: the context re-caches this turn, and the rebuild
    premium is one identifiable component within this turn's spend sum."""
    if runtime_ctx is None:
        return []
    marker = getattr(runtime_ctx, "cold_turn_marker", None)
    if not isinstance(marker, dict) or not marker.get("warm", True):
        return []
    reasons = [str(r) for r in (marker.get("reasons") or []) if str(r or "").strip()]
    reason_text = ", ".join(reasons) or str(marker.get("reason") or "selection_change")
    detail = ""
    prev_model = marker.get("prev_model") or {}
    new_model = marker.get("new_model") or {}
    if isinstance(prev_model, dict) or isinstance(new_model, dict):
        prev_id = str((prev_model or {}).get("model") or "").strip()
        new_id = str((new_model or {}).get("model") or "").strip()
        if prev_id or new_id:
            detail = f" ({prev_id or 'configured default'} -> {new_id or 'configured default'})"
    lines = ["[CACHE]"]
    lines.append(
        f"  cold turn: {reason_text}{detail}; the context re-caches at full input rates this turn."
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

    inactive_tool_lines = build_announce_inactive_tools_lines(runtime_ctx=runtime_ctx)
    if show_status_sections and inactive_tool_lines:
        lines.append("")
        lines.extend(inactive_tool_lines)

    reactivated_tool_lines = build_announce_reactivated_tools_lines(runtime_ctx=runtime_ctx)
    if show_status_sections and reactivated_tool_lines:
        lines.append("")
        lines.extend(reactivated_tool_lines)

    cold_turn_lines = build_announce_cold_turn_lines(runtime_ctx=runtime_ctx)
    if show_status_sections and cold_turn_lines:
        lines.append("")
        lines.extend(cold_turn_lines)

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

        delegation_lines = build_announce_delegation_lines(
            runtime_ctx=runtime_ctx,
            timeline_blocks=timeline_blocks,
        )
        if delegation_lines:
            lines.append("")
            lines.extend(delegation_lines)

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
            if artifact_path and (source_type in {"file", "attachment"} or artifact_path.startswith(CONVERSATION_FILE_REF_PREFIX)):
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
        lines.append("  Load:  react.read(paths=[\"conv:so:sources_pool[1,3,5]\"])")
        lines.append("  Exec:  ctx_tools.fetch_ctx(path=\"conv:so:sources_pool[1]\") returns rows; for web rows use content first, text second")
        lines.append("  Hide:  react.hide(path=\"conv:so:sources_pool[1]\", replacement=\"<replacement text to understand why/what its hidden>\")")
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
        "path": f"conv:ar:{turn_id}.stage.gate" if turn_id else "",
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
        "path": f"conv:ar:{turn_id}.stage.feedback" if turn_id else "",
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
        "path": f"conv:ar:{turn_id}.stage.clarification" if turn_id else "",
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
        "path": f"conv:ar:{turn_id}.stage.clarification.resolved",
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
        "path": f"conv:ar:{turn_id}.stage.suggested_followups" if turn_id else "",
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
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        namespaces_applicable = doc.get("namespaces_applicable") or metadata.get("namespaces_applicable")
        if namespaces_applicable:
            item["namespaces_applicable"] = list(namespaces_applicable)
        search_scopes_by_namespace = doc.get("search_scopes_by_namespace") or metadata.get("search_scopes_by_namespace")
        if isinstance(search_scopes_by_namespace, dict) and search_scopes_by_namespace:
            item["search_scopes_by_namespace"] = dict(search_scopes_by_namespace)
        tool_traits_by_namespace = doc.get("tool_traits_by_namespace") or metadata.get("tool_traits_by_namespace")
        if isinstance(tool_traits_by_namespace, dict) and tool_traits_by_namespace:
            item["tool_traits_by_namespace"] = dict(tool_traits_by_namespace)
        tool_traits = doc.get("tool_traits") or metadata.get("tool_traits")
        if isinstance(tool_traits, dict) and tool_traits:
            item["tool_traits"] = dict(tool_traits)
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
        detail: str = "full",
) -> str:
    if not tool_catalog:
        return ""

    if str(detail or "full").strip().lower() == "compact":
        return build_compact_tools_block(tool_catalog, header=header)

    # Derived roster: a completeness check the model can verify against
    # ("keep reading until you have seen N"), generated from the catalog it
    # heads so it can never disagree with the entries below.
    roster_ids = [str(t.get("id") or "unknown") for t in tool_catalog]
    roster = f"This catalog: {len(roster_ids)} tools — {', '.join(roster_ids)}."
    lines: List[str] = [
        header,
        "Available tools extend agent capabilities with specific operations. "
        "Call tools using their full ID (e.g., web_tools.web_search).",
        *_wrap_lines(roster, indent=""),
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
        namespaces_applicable = tool.get("namespaces_applicable")
        search_scopes_by_namespace = tool.get("search_scopes_by_namespace")
        tool_traits_by_namespace = tool.get("tool_traits_by_namespace")
        tool_traits = tool.get("tool_traits") if isinstance(tool.get("tool_traits"), dict) else {}

        async_txt = " [async]" if is_async else ""
        lines.append(f"🔧 [{idx}] {tid}{async_txt}")
        lines.append("")

        if purpose:
            lines.extend(_wrap_lines(purpose, indent="   "))
            lines.append("")

        if namespaces_applicable or tool_traits:
            lines.append("   Scope:")
            if namespaces_applicable:
                lines.append(f"       • namespaces applicable: {', '.join(str(ns) for ns in namespaces_applicable)}")
            if isinstance(search_scopes_by_namespace, dict) and search_scopes_by_namespace:
                lines.append("       • provider search scopes:")
                for namespace, scopes in search_scopes_by_namespace.items():
                    if not isinstance(scopes, list) or not scopes:
                        continue
                    lines.append(f"           {namespace}:")
                    for scope in scopes:
                        if not isinstance(scope, dict):
                            continue
                        scope_ns = str(scope.get("namespace") or "").strip()
                        if not scope_ns:
                            continue
                        label = str(scope.get("label") or scope.get("description") or scope.get("object_kind") or "").strip()
                        suffix = f" — {label}" if label else ""
                        filter_keys = []
                        filters_schema = scope.get("filters_schema")
                        if isinstance(filters_schema, dict):
                            filter_keys = [str(key) for key in filters_schema.keys() if str(key).strip()]
                        filter_suffix = ""
                        if filter_keys:
                            shown = ", ".join(filter_keys[:10])
                            if len(filter_keys) > 10:
                                shown += ", ..."
                            filter_suffix = f" (filters: {shown}; details: object_schema(namespace=\"{scope_ns}\"))"
                        lines.append(f"             - {scope_ns}{suffix}{filter_suffix}")
            if tool_traits:
                for trait_name, trait_value in tool_traits.items():
                    if isinstance(trait_value, dict):
                        text = ", ".join(
                            f"{key}={value}"
                            for key, value in trait_value.items()
                            if str(key or "").strip() and str(value or "").strip()
                        )
                    else:
                        values = trait_value if isinstance(trait_value, list) else [trait_value]
                        text = ", ".join(str(item) for item in values if str(item or "").strip())
                    if text:
                        if trait_name == "strategy" and isinstance(tool_traits_by_namespace, dict) and tool_traits_by_namespace:
                            text = f"{text} (default)"
                        lines.append(f"       • {trait_name}: {text}")
            if isinstance(tool_traits_by_namespace, dict) and tool_traits_by_namespace:
                lines.append("       • strategy overrides by namespace:")
                for namespace, traits in tool_traits_by_namespace.items():
                    if not isinstance(traits, dict):
                        continue
                    strategy = traits.get("strategy")
                    values = strategy if isinstance(strategy, list) else [strategy]
                    text = ", ".join(str(item) for item in values if str(item or "").strip())
                    if text:
                        lines.append(f"           - {namespace}: {text}")
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


def _compact_catalog_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _annotation_node_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _annotation_node_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _compact_annotation_node(node: ast.AST) -> str:
    type_aliases = {
        "array": "list",
        "boolean": "bool",
        "integer": "int",
        "number": "float",
        "string": "str",
    }
    if isinstance(node, ast.Name):
        return type_aliases.get(node.id, node.id)
    if isinstance(node, ast.List):
        values = [
            str(item.value)
            for item in node.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        non_null = [value for value in values if value not in {"null", "None", "NoneType"}]
        if len(non_null) == 1 and len(non_null) + 1 == len(values):
            return f"{type_aliases.get(non_null[0], non_null[0])}?"
        if values:
            return " | ".join(type_aliases.get(value, value) for value in values)
    if isinstance(node, ast.Subscript):
        name = _annotation_node_name(node.value).removeprefix("typing.")
        items = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        if name == "Annotated" and items:
            return _compact_annotation_node(items[0])
        if name == "Optional" and items:
            return f"{_compact_annotation_node(items[0]).rstrip('?')}?"
        if name == "Union" and items:
            rendered = [_compact_annotation_node(item) for item in items]
            non_null = [item for item in rendered if item not in {"None", "NoneType"}]
            if len(non_null) + 1 == len(rendered) and len(non_null) == 1:
                return f"{non_null[0].rstrip('?')}?"
    text = ast.unparse(node).replace("typing.", "")
    for suffix in (" | None", " | NoneType"):
        if text.endswith(suffix):
            return f"{text[:-len(suffix)].strip().rstrip('?')}?"
    return text


def _compact_annotation_text(value: Any) -> str:
    """Render a complete type while discarding ``Annotated`` documentation."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if text.startswith("<class '") and text.endswith("'>"):
        return text[8:-2].removeprefix("builtins.")
    try:
        node = ast.parse(text, mode="eval").body
        # Some legacy docs use ``type, description``. A valid tuple here still
        # means its first item is the type, not that the parameter accepts a tuple.
        if isinstance(node, ast.Tuple) and node.elts:
            node = node.elts[0]
        rendered = _compact_annotation_node(node).strip()
        if rendered:
            return rendered
    except (SyntaxError, ValueError):
        pass

    # Last-resort compatibility for unquoted ``type, description`` strings.
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote:
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote == char:
                quote = ""
            elif not quote:
                quote = char
            continue
        if quote:
            continue
        if char in "[({":
            depth += 1
        elif char in "])}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            text = text[:index].strip()
            break
    return text or "any"


def _compact_annotation_parts(value: Any) -> Tuple[str, str]:
    """Return a compact type plus the parameter documentation it carries."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    try:
        node = ast.parse(text, mode="eval").body
        type_node = node
        description_nodes: List[ast.AST] = []
        if isinstance(node, ast.Tuple) and node.elts:
            type_node = node.elts[0]
            description_nodes = list(node.elts[1:])
        elif isinstance(node, ast.Subscript):
            name = _annotation_node_name(node.value).removeprefix("typing.")
            items = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            if name == "Annotated" and items:
                type_node = items[0]
                description_nodes = list(items[1:])

        descriptions: List[str] = []
        for item in description_nodes:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                rendered = item.value
            else:
                rendered = ast.unparse(item)
            rendered = re.sub(r"\s+", " ", str(rendered or "")).strip()
            if rendered:
                descriptions.append(rendered)
        return _compact_annotation_node(type_node).strip() or "any", " ".join(descriptions)
    except (SyntaxError, ValueError):
        pass

    # Semantic Kernel metadata uses ``type, description``. Split only at a
    # top-level comma so commas inside generic types remain part of the type.
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote:
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote == char:
                quote = ""
            elif not quote:
                quote = char
            continue
        if quote:
            continue
        if char in "[({":
            depth += 1
        elif char in "])}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            arg_type = _compact_annotation_text(text[:index].strip())
            description = re.sub(r"\s+", " ", text[index + 1 :]).strip()
            return arg_type, description
    return _compact_annotation_text(text), ""


def _compact_arg_spec(arg_name: Any, arg_info: Any) -> str:
    name = str(arg_name or "").strip()
    if isinstance(arg_info, str):
        raw = str(arg_info or "").strip()
        default_match = re.search(r"\s+\(default=(.*?)\)\s*$", raw)
        suffix = ""
        if default_match:
            default_val = default_match.group(1)
            suffix = f"={default_val}" if default_val else "?"
            raw = raw[:default_match.start()].strip()
        arg_type, description = _compact_annotation_parts(raw)
        if arg_type.endswith("?") and suffix == "?":
            suffix = ""
        spec = f"{name}:{arg_type}{suffix}"
        return f"{spec} — {description}" if description else spec
    if isinstance(arg_info, dict):
        arg_type = arg_info.get("type") or arg_info.get("anyOf") or "any"
        suffix = f"={arg_info['default']}" if "default" in arg_info else ""
        spec = f"{name}:{_compact_annotation_text(arg_type)}{suffix}"
        description = re.sub(r"\s+", " ", str(arg_info.get("description") or "")).strip()
        return f"{spec} — {description}" if description else spec
    return f"{name}:{_compact_annotation_text(arg_info)}"


def build_compact_tools_block(
    tool_catalog: Optional[List[Dict[str, Any]]],
    *,
    header: str,
) -> str:
    """Render the exact effective tools without full prose/examples schemas."""
    tools = [tool for tool in (tool_catalog or []) if isinstance(tool, dict)]
    if not tools:
        return ""
    roster_ids = [str(tool.get("id") or "unknown") for tool in tools]
    lines = [
        f"{header} [COMPACT]",
        f"This catalog: {len(roster_ids)} tools - {', '.join(roster_ids)}.",
        "Only these tool ids are callable. Parameter names, types, and descriptions below are exact.",
    ]
    for tool in tools:
        tool_id = str(tool.get("id") or "unknown")
        async_suffix = " [async]" if tool.get("is_async") else ""
        lines.append(f"- {tool_id}{async_suffix}")
        purpose = _compact_catalog_text(tool.get("purpose"), limit=240)
        if purpose:
            lines.append(f"  purpose: {purpose}")
        args = tool.get("args")
        if isinstance(args, dict) and args:
            lines.append("  params:")
            for name, info in args.items():
                lines.append(f"    - {_compact_arg_spec(name, info)}")
        namespaces = tool.get("namespaces_applicable")
        if isinstance(namespaces, list) and namespaces:
            lines.append("  namespaces: " + ", ".join(str(item) for item in namespaces))
        constraints = tool.get("constraints")
        if isinstance(constraints, str):
            constraints = [constraints]
        if isinstance(constraints, list) and constraints:
            constraint = _compact_catalog_text(constraints[0], limit=180)
            if constraint:
                lines.append(f"  constraint: {constraint}")
    return "\n".join(lines)


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
        tool_catalog_detail: str = "full",
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
        react_block = build_tools_block(
            react_only,
            header="[AVAILABLE REACT TOOLS]",
            detail=tool_catalog_detail,
        )
        if react_block:
            parts_tools.append(react_block)
        common_block = build_tools_block(
            common,
            header="[AVAILABLE COMMON TOOLS]",
            detail=tool_catalog_detail,
        )
        if common_block:
            parts_tools.append(common_block)
        exec_block = build_tools_block(
            exec_only,
            header="[TOOLS AVAILABLE ONLY IN CODE SNIPPET]",
            detail=tool_catalog_detail,
        )
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
