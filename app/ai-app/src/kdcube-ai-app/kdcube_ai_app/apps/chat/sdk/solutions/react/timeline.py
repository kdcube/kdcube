# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import logging
import hashlib
import traceback
import copy
import os

import time
import datetime as _dt
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, Tuple

from kdcube_ai_app.apps.chat.sdk.solutions.react.caching import (
    cache_point_indices,
    tail_rounds_from_path as cache_tail_rounds_from_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.event_identity import index_agent_id

from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.apps.chat.sdk.util import (
    LINE_NUMBERS_DISABLED,
    LINE_NUMBERS_LINES,
    format_visible_line_window,
    line_number_text,
    normalize_line_numbers_mode,
    token_count,
    visible_line_window,
    isoz,
    ts_key,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
    build_sources_pool_text,
    build_turn_header_text,
    build_timeline_render_directive,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.plan import (
    latest_plan_block_by_id,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    ARTIFACT_NAMESPACE_FILES,
    REACT_FILE_REF_PREFIX,
    physical_path_to_logical_path,
    split_physical_artifact_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.compaction_memory import (
    build_internal_note_compaction_result,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.common import event_source_pipeline_enabled
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.projection import (
    apply_event_source_transformers,
    apply_event_source_transformers_async,
    clear_timeline_segment_marks,
    patch_timeline_segment_marks,
    produce_event_source_announce_blocks,
)
from kdcube_ai_app.tools.content_type import is_text_mime_type
from kdcube_ai_app.infra.service_hub.multimodality import (
    MODALITY_DOC_MIME,
    MODALITY_IMAGE_MIME,
    estimate_image_tokens_from_base64,
    estimate_pdf_tokens_from_base64,
)

TIMELINE_KIND = "conv.timeline.v1"
SOURCES_POOL_KIND = "conv:sources_pool"

TIMELINE_FILENAME = "timeline.json"

DEFAULT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS = 12_000
TOOL_RESULT_PREVIEW_SHAPE_DEPTH = 4
TOOL_RESULT_PREVIEW_SHAPE_MAX_ITEMS = 10
WEB_SOURCE_RESULT_TOOL_IDS = {"web_tools.web_search", "web_tools.web_fetch"}

logger = logging.getLogger(__name__)

CONV_AR_PREFIX = "conv:ar:"
CONV_TC_PREFIX = "conv:tc:"
CONV_SO_PREFIX = "conv:so:"
CONV_WS_PREFIX = "conv:ws:"
CONV_SU_PREFIX = "conv:su:"
CONV_NAMESPACE_PREFIXES = (
    REACT_FILE_REF_PREFIX,
    CONV_AR_PREFIX,
    CONV_TC_PREFIX,
    CONV_SO_PREFIX,
    CONV_WS_PREFIX,
    CONV_SU_PREFIX,
)


@dataclass(frozen=True)
class TimelineEventCursor:
    timestamp: str = ""
    event_id: str = ""
    block_path: str = ""
    block_index: Optional[int] = None
    sequence: Optional[int] = None
    fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.timestamp:
            out["timestamp"] = self.timestamp
        if self.event_id:
            out["event_id"] = self.event_id
        if self.block_path:
            out["block_path"] = self.block_path
        if self.block_index is not None:
            out["block_index"] = self.block_index
        if self.sequence is not None:
            out["sequence"] = self.sequence
        if self.fingerprint:
            out["fingerprint"] = self.fingerprint
        return out

    @classmethod
    def from_any(cls, raw: Any) -> "TimelineEventCursor":
        if not isinstance(raw, dict):
            return cls()
        block_index = raw.get("block_index")
        if block_index is not None:
            try:
                block_index = int(block_index)
            except Exception:
                block_index = None
        sequence = raw.get("sequence")
        if sequence is not None:
            try:
                sequence = int(sequence)
            except Exception:
                sequence = None
        return cls(
            timestamp=_block_ts({"ts": raw.get("timestamp")}),
            event_id=str(raw.get("event_id") or "").strip(),
            block_path=str(raw.get("block_path") or "").strip(),
            block_index=block_index,
            sequence=sequence,
            fingerprint=str(raw.get("fingerprint") or "").strip(),
        )

    def is_empty(self) -> bool:
        return not (self.timestamp or self.event_id or self.block_path or self.fingerprint)

def _maybe_parse_json(val: str) -> Optional[Any]:
    try:
        return json.loads(val)
    except Exception:
        return None

def _should_render_items_stats(*, tool_id: str, path: str, meta: Dict[str, Any]) -> bool:
    resolved_tool_id = (tool_id or "").strip()
    if not resolved_tool_id and isinstance(meta, dict):
        resolved_tool_id = str(meta.get("tool_id") or "").strip()
    if resolved_tool_id in WEB_SOURCE_RESULT_TOOL_IDS:
        return False
    return True

class TimelineView:
    def __init__(self, payload: Dict[str, Any]):
        self.payload = payload or {}

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "TimelineView":
        return cls(parse_timeline_payload(payload))

    def resolve_sources_pool(self, selector: str) -> List[Dict[str, Any]]:
        return resolve_sources_pool_selector(self.payload, selector)

    def resolve_artifact(self, path: str) -> Optional[Dict[str, Any]]:
        return resolve_artifact_from_timeline(self.payload, path)

    def timeline_artifacts(self, show_paths: List[str]) -> List[Dict[str, Any]]:
        return materialize_show_artifacts(self.payload, show_paths)


def extract_turn_ids_from_blocks(blocks: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for blk in blocks or []:
        if not isinstance(blk, dict):
            continue
        tid = (blk.get("turn_id") or "").strip()
        if tid and tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def _merge_turn_ids(*groups: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            tid = str(item or "").strip()
            if tid and tid not in seen:
                seen.add(tid)
                out.append(tid)
    return out


def build_timeline_payload(
    *,
    blocks: List[Dict[str, Any]],
    sources_pool: Optional[List[Dict[str, Any]]] = None,
    conversation_title: Optional[str] = None,
    conversation_started_at: Optional[str] = None,
    last_external_event_id: Optional[str] = None,
    last_external_event_seq: Optional[int] = None,
    last_rendered_event_cursor: Optional[TimelineEventCursor] = None,
    cache_last_touch_at: Optional[int] = None,
    cache_last_ttl_seconds: Optional[int] = None,
    agent_selection_snapshot: Optional[Dict[str, Any]] = None,
    last_known_feedback_ts: Optional[str] = None,
    include_sources_pool: bool = True,
) -> Dict[str, Any]:
    last_activity_at = _tail_ts(blocks or [])
    return {
        "version": 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "blocks": list(blocks or []),
        "sources_pool": list(sources_pool or []) if include_sources_pool else [],
        "turn_ids": extract_turn_ids_from_blocks(blocks or []),
        "conversation_title": conversation_title or "",
        "conversation_started_at": conversation_started_at or "",
        "last_activity_at": last_activity_at or "",
        "last_external_event_id": last_external_event_id or "",
        "last_external_event_seq": last_external_event_seq,
        "last_rendered_event_cursor": (
            last_rendered_event_cursor.to_dict()
            if isinstance(last_rendered_event_cursor, TimelineEventCursor)
            else {}
        ),
        "cache_last_touch_at": cache_last_touch_at,
        "cache_last_ttl_seconds": cache_last_ttl_seconds,
        "agent_selection_snapshot": dict(agent_selection_snapshot) if isinstance(agent_selection_snapshot, dict) else None,
        "last_known_feedback_ts": last_known_feedback_ts or "",
    }


def parse_timeline_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        blocks = []
    sources_pool = payload.get("sources_pool")
    if not isinstance(sources_pool, list):
        sources_pool = []
    last_external_event_id = payload.get("last_external_event_id")
    if isinstance(last_external_event_id, str):
        last_external_event_id = last_external_event_id.strip()
    else:
        last_external_event_id = ""
    last_external_event_seq = payload.get("last_external_event_seq")
    if last_external_event_seq is not None:
        try:
            last_external_event_seq = int(last_external_event_seq)
        except Exception:
            last_external_event_seq = None
    last_rendered_event_cursor = TimelineEventCursor.from_any(payload.get("last_rendered_event_cursor"))
    turn_ids = payload.get("turn_ids")
    if not isinstance(turn_ids, list) or not turn_ids:
        turn_ids = extract_turn_ids_from_blocks(blocks)
    cache_last_touch_at = payload.get("cache_last_touch_at")
    if cache_last_touch_at is not None:
        try:
            cache_last_touch_at = int(cache_last_touch_at)
        except Exception:
            cache_last_touch_at = None
    cache_last_ttl_seconds = payload.get("cache_last_ttl_seconds")
    if cache_last_ttl_seconds is not None:
        try:
            cache_last_ttl_seconds = int(cache_last_ttl_seconds)
        except Exception:
            cache_last_ttl_seconds = None
    agent_selection_snapshot = payload.get("agent_selection_snapshot")
    if not isinstance(agent_selection_snapshot, dict):
        agent_selection_snapshot = None
    last_known_feedback_ts = payload.get("last_known_feedback_ts")
    if isinstance(last_known_feedback_ts, str):
        last_known_feedback_ts = last_known_feedback_ts.strip()
    else:
        last_known_feedback_ts = ""
    return {
        "blocks": blocks,
        "sources_pool": sources_pool,
        "turn_ids": turn_ids,
        "ts": payload.get("ts"),
        "version": payload.get("version", 1),
        "conversation_title": payload.get("conversation_title") or "",
        "conversation_started_at": payload.get("conversation_started_at") or "",
        "last_activity_at": payload.get("last_activity_at") or "",
        "last_external_event_id": last_external_event_id or "",
        "last_external_event_seq": last_external_event_seq,
        "last_rendered_event_cursor": last_rendered_event_cursor,
        "cache_last_touch_at": cache_last_touch_at,
        "cache_last_ttl_seconds": cache_last_ttl_seconds,
        "agent_selection_snapshot": agent_selection_snapshot,
        "last_known_feedback_ts": last_known_feedback_ts,
    }


def _tail_ts(blocks: List[Dict[str, Any]]) -> str:
    if not blocks:
        return ""
    last = blocks[-1] if isinstance(blocks[-1], dict) else None
    if not last:
        return ""
    ts = last.get("ts")
    if isinstance(ts, (int, float)):
        return _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(ts, str):
        return isoz(ts)
    return ""


def _block_ts(block: Dict[str, Any]) -> str:
    if not isinstance(block, dict):
        return ""
    ts = block.get("ts")
    if isinstance(ts, (int, float)):
        return _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(ts, str) and ts.strip():
        return isoz(ts)
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    meta_ts = meta.get("ts") or meta.get("created_at")
    if isinstance(meta_ts, (int, float)):
        return _dt.datetime.fromtimestamp(float(meta_ts), tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(meta_ts, str) and meta_ts.strip():
        return isoz(meta_ts)
    text = block.get("text")
    if isinstance(text, str) and text.strip() and len(text) < 20000:
        parsed = _maybe_parse_json(text)
        if isinstance(parsed, dict):
            payload_ts = parsed.get("ts")
            if isinstance(payload_ts, (int, float)):
                return _dt.datetime.fromtimestamp(float(payload_ts), tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
            if isinstance(payload_ts, str) and payload_ts.strip():
                return isoz(payload_ts)
    return ""


def _first_block_ts(blocks: List[Dict[str, Any]]) -> str:
    for blk in blocks or []:
        ts = _block_ts(blk)
        if ts:
            return ts
    return ""


def _last_block_ts(blocks: List[Dict[str, Any]]) -> str:
    for blk in reversed(blocks or []):
        ts = _block_ts(blk)
        if ts:
            return ts
    return ""


def _timestamp_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except Exception:
        pass
    try:
        parse_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = _dt.datetime.fromisoformat(parse_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return float(dt.timestamp())
    except Exception:
        return 0.0


def _later_ts(left: Any, right: Any) -> str:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text:
        return right_text
    if not right_text:
        return left_text
    if _timestamp_epoch(right_text) >= _timestamp_epoch(left_text):
        return right_text
    return left_text


def _external_event_block_ts(block: Dict[str, Any]) -> str:
    if not isinstance(block, dict):
        return ""
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    event_meta = meta.get("event") if isinstance(meta.get("event"), dict) else {}
    has_external_identity = bool(
        meta.get("event_id")
        or meta.get("message_id")
        or meta.get("event_source_id")
        or meta.get("event_kind")
        or event_meta
        or str(meta.get("prompt_origin") or "") == "external_event_lane"
    )
    if not has_external_identity:
        return ""
    for candidate in (
        event_meta.get("timestamp"),
        event_meta.get("ts"),
        meta.get("timestamp"),
        meta.get("ts"),
        meta.get("created_at"),
    ):
        ts = _block_ts({"ts": candidate})
        if ts:
            return ts
    return _block_ts(block)


def _event_cursor_from_block(block: Dict[str, Any], *, block_index: int) -> TimelineEventCursor:
    if not isinstance(block, dict):
        return TimelineEventCursor()
    timestamp = _external_event_block_ts(block)
    if not timestamp:
        return TimelineEventCursor()
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    event_meta = meta.get("event") if isinstance(meta.get("event"), dict) else {}
    event_id = str(
        meta.get("event_id")
        or meta.get("message_id")
        or event_meta.get("event_id")
        or event_meta.get("message_id")
        or ""
    ).strip()
    block_path = str(
        block.get("path")
        or meta.get("logical_path")
        or meta.get("path")
        or event_meta.get("logical_path")
        or event_meta.get("path")
        or ""
    ).strip()
    sequence_raw = meta.get("sequence")
    if sequence_raw is None:
        sequence_raw = event_meta.get("sequence")
    sequence: Optional[int] = None
    if sequence_raw is not None:
        try:
            sequence = int(sequence_raw)
        except Exception:
            sequence = None
    fingerprint_source = {
        "timestamp": timestamp,
        "event_id": event_id,
        "block_path": block_path,
        "block_index": int(block_index),
        "sequence": sequence,
        "type": str(block.get("type") or "").strip(),
        "turn_id": str(block.get("turn_id") or block.get("turn") or "").strip(),
    }
    fingerprint = hashlib.sha1(
        json.dumps(fingerprint_source, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return TimelineEventCursor(
        timestamp=timestamp,
        event_id=event_id,
        block_path=block_path,
        block_index=int(block_index),
        sequence=sequence,
        fingerprint=fingerprint,
    )


def _last_rendered_external_event_cursor(blocks: List[Dict[str, Any]]) -> TimelineEventCursor:
    cursor = TimelineEventCursor()
    for idx, blk in enumerate(blocks or []):
        candidate = _event_cursor_from_block(blk, block_index=idx)
        if not candidate.is_empty():
            cursor = candidate
    return cursor


def _later_timeline_event_cursor(
    current: TimelineEventCursor,
    candidate: TimelineEventCursor,
) -> TimelineEventCursor:
    if candidate.is_empty():
        return current
    if current.is_empty():
        return candidate
    current_ts = _timestamp_epoch(current.timestamp)
    candidate_ts = _timestamp_epoch(candidate.timestamp)
    if candidate_ts >= current_ts:
        return candidate
    return current


def _first_user_message_ts(blocks: List[Dict[str, Any]]) -> str:
    for blk in blocks or []:
        if not isinstance(blk, dict):
            continue
        btype = (blk.get("type") or "").strip()
        author = (blk.get("author") or blk.get("role") or "").strip().lower()
        if btype == "user.prompt" or author == "user":
            ts = _block_ts(blk)
            if ts:
                return ts
    return _first_block_ts(blocks)


def _compact_source_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    sid = row.get("sid")
    try:
        sid = int(sid)
    except Exception:
        return None
    title = (row.get("title") or "").strip()
    url = (row.get("url") or row.get("href") or "").strip()
    text = (row.get("text") or "").strip()
    if text and len(text) > 500:
        text = text[:500] + "...(truncated)"
    out = {"sid": sid}
    if title:
        out["title"] = title
    if url:
        out["url"] = url
    if text:
        out["text"] = text
    published = row.get("published_time_iso")
    if published:
        out["published_time_iso"] = published
    favicon = row.get("favicon")
    if favicon:
        out["favicon"] = favicon
    return out


def _compact_sources_pool_for_index(sources_pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    for row in (sources_pool or []):
        item = _compact_source_row(row)
        if item:
            compact.append(item)
    return compact


def _collect_blocks(timeline: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(timeline, dict):
        return []
    blocks = timeline.get("blocks")
    if isinstance(blocks, list):
        return [b for b in blocks if isinstance(b, dict)]
    return []


def _parse_meta_json(text: str) -> Dict[str, Any]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def parse_sources_pool_ref(path: str) -> tuple[Optional[str], str]:
    """
    Parse a sources-pool reference.

    Supported forms:
      - conv:so:sources_pool[1,3-5]
      - sources_pool[1,3-5]
      - conv:so:conv_<conversation_id>.sources_pool[1,3-5]

    Returns (conversation_id, selector). conversation_id is None for the
    currently loaded timeline.
    """
    raw = str(path or "").strip()
    if raw.startswith("conv:so:"):
        raw = raw[len("conv:so:"):]
    if raw.startswith("sources_pool["):
        return None, raw
    if raw.startswith("conv_"):
        marker = ".sources_pool["
        marker_at = raw.find(marker)
        if marker_at > len("conv_") and raw.endswith("]"):
            conversation_id = raw[len("conv_"):marker_at].strip()
            selector = "sources_pool[" + raw[marker_at + len(marker):]
            if conversation_id and selector.endswith("]"):
                return conversation_id, selector
    return None, ""


def resolve_sources_pool_selector(timeline: Dict[str, Any], selector: str) -> List[Dict[str, Any]]:
    """
    selector: sources_pool[<sid>,<sid>]
    """
    if not selector.startswith("sources_pool[") or not selector.endswith("]"):
        return []
    raw = selector[len("sources_pool["):-1]
    sids: List[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        # Support ranges like "1-5"
        range_sep = ":" if ":" in tok else "-" if "-" in tok else ""
        if range_sep:
            parts = [p.strip() for p in tok.split(range_sep, 1)]
            if len(parts) == 2:
                try:
                    start = int(parts[0])
                    end = int(parts[1])
                    if start <= end:
                        sids.extend(list(range(start, end + 1)))
                    else:
                        sids.extend(list(range(end, start + 1)))
                    continue
                except Exception:
                    pass
        try:
            sids.append(int(tok))
        except Exception:
            continue
    pool = timeline.get("sources_pool") or []
    if not sids:
        return []
    return [row for row in pool if isinstance(row, dict) and row.get("sid") in sids]


def extract_source_sids(sources: Any) -> List[int]:
    out: List[int] = []
    if isinstance(sources, list):
        for item in sources:
            if isinstance(item, dict):
                sid = item.get("sid")
                if isinstance(sid, (int, float)):
                    out.append(int(sid))
            elif isinstance(item, (int, float)):
                out.append(int(item))
    return out


def extract_sources_used_from_blocks(blocks: List[Dict[str, Any]]) -> List[int]:
    """
    Gather unique sources_used SIDs from block meta or from react.tool.result meta blocks.
    """
    used: List[int] = []
    seen: set[int] = set()
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        btype = b.get("type") or ""
        if btype in {"assistant.completion", "assistant.completion.attempt"}:
            text_val = b.get("text")
            if isinstance(text_val, str) and text_val.strip():
                for sid in citations_module.extract_citation_sids_any(text_val):
                    if sid not in seen:
                        seen.add(sid)
                        used.append(sid)
        meta = b.get("meta")
        if isinstance(meta, dict):
            for sid in extract_source_sids(meta.get("sources_used")):
                if sid not in seen:
                    seen.add(sid)
                    used.append(sid)
        if (b.get("type") or "") == "react.tool.result" and (b.get("mime") or "") == "application/json":
            txt = b.get("text")
            if isinstance(txt, str):
                meta_obj = _parse_meta_json(txt)
                for sid in extract_source_sids(meta_obj.get("sources_used")):
                    if sid not in seen:
                        seen.add(sid)
                        used.append(sid)
    return used


def extract_assistant_completion_texts_from_blocks(blocks: List[Dict[str, Any]]) -> List[str]:
    canonical: List[str] = []
    attempts: List[str] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        btype = b.get("type") or ""
        if btype not in {"assistant.completion", "assistant.completion.attempt"}:
            continue
        text = str(b.get("text") or "").strip()
        if not text:
            continue
        if btype == "assistant.completion":
            canonical.append(text)
        else:
            attempts.append(text)
    return canonical or attempts


def extract_user_prompt_block(blocks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") == "user.prompt":
            return b
    return None


def extract_assistant_completion_block(blocks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for b in reversed(blocks or []):
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") == "assistant.completion":
            return b
    return None


def extract_assistant_completion_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") == "assistant.completion":
            items.append(b)
    return items


TURN_INDEX_SUFFIX = ".react.turn.index"


def parse_turn_index_path(path: str) -> Optional[str]:
    """
    Extract the turn_id from a turn-index path. Accepts both the current
    conversation form `conv:ar:turn_<id>.react.turn.index` and the
    cross-conversation form `conv:ar:conv_<id>.turn_<id>.react.turn.index`.
    In the cross-conv case the
    conv_<id> prefix is stripped silently; callers that need the
    conversation_id can call `parse_turn_index_ref` instead.
    """
    p = str(path or "").strip()
    if not p.startswith("conv:ar:") or not p.endswith(TURN_INDEX_SUFFIX):
        return None
    body = p[len("conv:ar:") : -len(TURN_INDEX_SUFFIX)].strip()
    if body.startswith("conv_"):
        _, sep, rest = body.partition(".")
        if sep and rest:
            body = rest.strip()
    return body or None


def parse_turn_index_ref(path: str) -> Optional[tuple[str, str]]:
    """
    Like `parse_turn_index_path` but returns `(conversation_id, turn_id)`.
    `conversation_id` is empty when the path has no `conv_<id>.` prefix.
    Returns `None` when the path is not a turn-index path.
    """
    p = str(path or "").strip()
    if not p.startswith("conv:ar:") or not p.endswith(TURN_INDEX_SUFFIX):
        return None
    body = p[len("conv:ar:") : -len(TURN_INDEX_SUFFIX)].strip()
    conv_id = ""
    if body.startswith("conv_"):
        seg, sep, rest = body.partition(".")
        if sep and rest:
            conv_id = seg[len("conv_") :].strip()
            body = rest.strip()
    if not body:
        return None
    return conv_id, body


def _compact_hint(value: Any, *, max_chars: int = 180) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            value = str(value)
    text = " ".join(str(value or "").replace("\n", " ").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _block_turn_id(block: Dict[str, Any], fallback: str = "") -> str:
    if not isinstance(block, dict):
        return fallback
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    return str(block.get("turn_id") or block.get("turn") or meta.get("turn_id") or fallback or "").strip()


def _block_call_id(block: Dict[str, Any]) -> str:
    if not isinstance(block, dict):
        return ""
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    return str(block.get("call_id") or meta.get("tool_call_id") or meta.get("call_id") or "").strip()


def _tool_call_payload(block: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(block, dict):
        return {}
    txt = block.get("text")
    if isinstance(txt, str) and txt.strip():
        obj = _maybe_parse_json(txt)
        if isinstance(obj, dict):
            return obj
    return {}


def _tool_id_from_call_or_result(block: Dict[str, Any], call_block: Optional[Dict[str, Any]] = None) -> str:
    for candidate in (block, call_block):
        if not isinstance(candidate, dict):
            continue
        meta = candidate.get("meta") if isinstance(candidate.get("meta"), dict) else {}
        val = str(candidate.get("tool_id") or meta.get("tool_id") or "").strip()
        if val:
            return val
        payload = _tool_call_payload(candidate)
        val = str(payload.get("tool_id") or "").strip()
        if val:
            return val
    return ""


def _tool_error_code_message(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "error", _compact_hint(payload)
    err = payload.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or payload.get("code") or "error").strip() or "error"
        msg = str(err.get("message") or payload.get("message") or "").strip()
        return code, msg
    if err is not None:
        return str(payload.get("code") or "error").strip() or "error", _compact_hint(err)
    return (
        str(payload.get("code") or "error").strip() or "error",
        str(payload.get("message") or "").strip(),
    )


def _tool_status_and_hint(result_block: Optional[Dict[str, Any]]) -> tuple[str, str]:
    if not isinstance(result_block, dict):
        return "", ""
    meta = result_block.get("meta") if isinstance(result_block.get("meta"), dict) else {}
    status = str(meta.get("status") or "").strip()
    hint = str(meta.get("summary") or meta.get("description") or "").strip()
    txt = result_block.get("text")
    parsed = _maybe_parse_json(txt) if isinstance(txt, str) else None
    if isinstance(parsed, dict):
        if not status:
            raw_status = parsed.get("status")
            if raw_status:
                status = str(raw_status).strip()
            elif parsed.get("ok") is True:
                status = "success"
            elif parsed.get("ok") is False or parsed.get("error") or parsed.get("code"):
                status = "error"
        if not hint:
            for key in ("message", "summary", "error", "code"):
                val = parsed.get(key)
                if val:
                    hint = _compact_hint(val)
                    break
            if not hint and parsed.get("artifact_path"):
                hint = f"artifact metadata for {parsed.get('artifact_path')}"
    if not hint and isinstance(txt, str):
        hint = _compact_hint(txt)
    return status, hint


def _default_message_path(turn_id: str, btype: str, index: int = 1, total: int = 1) -> str:
    if btype == "user.prompt":
        return f"conv:ar:{turn_id}.user.prompt"
    if btype == "assistant.completion":
        return f"conv:ar:{turn_id}.assistant.completion" if index == total else f"conv:ar:{turn_id}.assistant.completion.{index}"
    return ""


def _format_index_row(label: str, path: str = "", *, attrs: Optional[Dict[str, Any]] = None, hint: str = "") -> List[str]:
    label = str(label or "item").strip()
    path = str(path or "").strip()
    first = f"- {label}: {path}" if path else f"- {label}"
    lines = [first]
    attrs = attrs if isinstance(attrs, dict) else {}
    for key, val in attrs.items():
        if val is None:
            continue
        sval = str(val).strip()
        if not sval:
            continue
        lines.append(f"  {key}: {sval}")
    hint = _compact_hint(hint, max_chars=220)
    if hint:
        lines.append(f"  hint: {hint}")
    return lines


def build_turn_index_text(
    *,
    turn_id: str,
    blocks: List[Dict[str, Any]],
    sources_pool: Optional[List[Dict[str, Any]]] = None,
) -> str:
    tid = str(turn_id or "").strip()
    filtered = [
        b for b in (blocks or [])
        if isinstance(b, dict) and (not tid or _block_turn_id(b, tid) == tid)
    ]
    if not tid:
        tids = extract_turn_ids_from_blocks(filtered)
        tid = tids[0] if tids else ""
    path = f"conv:ar:{tid}.react.turn.index" if tid else "conv:ar:turn_<id>.react.turn.index"
    ts_vals = [_block_ts(b) for b in filtered if _block_ts(b)]
    started_at = ts_vals[0] if ts_vals else ""
    ended_at = ts_vals[-1] if ts_vals else ""

    lines: List[str] = [
        "[TURN INDEX]",
        f"[path: {path}]",
        f"turn_id: {tid}",
    ]
    if started_at:
        lines.append(f"started_at: {started_at}")
    if ended_at and ended_at != started_at:
        lines.append(f"ended_at: {ended_at}")
    lines.append("")

    summary_blocks = [
        b for b in filtered
        if (b.get("type") or "").strip() == "conv.working.summary"
        and isinstance(b.get("text"), str)
        and str(b.get("text") or "").strip()
    ]
    if summary_blocks:
        lines.append("summaries:")
        latest = summary_blocks[-1]
        latest_path = (latest.get("path") or "").strip()
        if tid:
            lines.extend(_format_index_row(
                "latest working summary",
                f"conv:ws:{tid}.conv.working.summary",
                attrs={"source": latest_path} if latest_path else {},
                hint=latest.get("text") or "",
            ))
        for idx, blk in enumerate(summary_blocks, start=1):
            bpath = (blk.get("path") or "").strip()
            if not bpath:
                continue
            lines.extend(_format_index_row(
                f"working summary attempt {idx}",
                bpath,
                hint=blk.get("text") or "",
            ))
        lines.append("")

    user_like_types = {
        "user.prompt": "user prompt",
        "user.followup": "user followup",
        "user.followup.preserved": "preserved user followup",
        "user.steer": "user steer",
        "user.steer.preserved": "preserved user steer",
        "event.external": "external event",
        "event.external.preserved": "preserved external event",
    }
    user_like_blocks = [
        b for b in filtered
        if (b.get("type") or "").strip() in user_like_types
    ]
    assistant_blocks = [
        b for b in filtered
        if (b.get("type") or "").strip() == "assistant.completion"
    ]
    if user_like_blocks or assistant_blocks:
        lines.append("messages:")
        prompt_seen = 0
        for blk in user_like_blocks:
            btype = (blk.get("type") or "").strip()
            if btype == "user.prompt":
                prompt_seen += 1
            label = user_like_types.get(btype, btype)
            bpath = (blk.get("path") or "").strip()
            if not bpath and btype == "user.prompt":
                bpath = _default_message_path(tid, btype)
            attrs = {"ts": blk.get("ts")} if blk.get("ts") else {}
            lines.extend(_format_index_row(label, bpath, attrs=attrs, hint=blk.get("text") or ""))
        total_assistants = len(assistant_blocks)
        for idx, blk in enumerate(assistant_blocks, start=1):
            bpath = (blk.get("path") or "").strip() or _default_message_path(tid, "assistant.completion", idx, total_assistants)
            attrs = {"ts": blk.get("ts")} if blk.get("ts") else {}
            label = "assistant completion" if total_assistants == 1 else f"assistant completion {idx}/{total_assistants}"
            lines.extend(_format_index_row(label, bpath, attrs=attrs, hint=blk.get("text") or ""))
        lines.append("")

    event_blocks = [
        b for b in filtered
        if (b.get("type") or "").strip().startswith("external.")
        or (isinstance(b.get("meta"), dict) and b.get("meta", {}).get("event_kind"))
    ]
    if event_blocks:
        lines.append("events:")
        for blk in event_blocks:
            btype = (blk.get("type") or "").strip()
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            kind = str(meta.get("event_kind") or btype or "event").strip()
            bpath = (blk.get("path") or "").strip()
            attrs = {}
            for key in ("message_id", "sequence", "source"):
                if meta.get(key) is not None and str(meta.get(key)).strip():
                    attrs[key] = meta.get(key)
            lines.extend(_format_index_row(kind, bpath, attrs=attrs, hint=blk.get("text") or meta.get("payload") or ""))
        lines.append("")

    call_blocks: Dict[str, Dict[str, Any]] = {}
    result_blocks: Dict[str, List[Dict[str, Any]]] = {}
    for blk in filtered:
        btype = (blk.get("type") or "").strip()
        call_id = _block_call_id(blk)
        if not call_id:
            continue
        if btype == "react.tool.call":
            call_blocks[call_id] = blk
        elif btype == "react.tool.result":
            result_blocks.setdefault(call_id, []).append(blk)
    call_ids = []
    for blk in filtered:
        call_id = _block_call_id(blk)
        if call_id and call_id not in call_ids and (call_id in call_blocks or call_id in result_blocks):
            call_ids.append(call_id)
    if call_ids:
        lines.append("tools:")
        for call_id in call_ids:
            call_blk = call_blocks.get(call_id)
            results = result_blocks.get(call_id) or []
            result_blk = next(
                (b for b in reversed(results) if str(b.get("path") or "").strip().startswith(CONV_TC_PREFIX)),
                None,
            ) or (results[-1] if results else None)
            tool_id = _tool_id_from_call_or_result(result_blk or {}, call_blk)
            call_path = (call_blk.get("path") or "").strip() if isinstance(call_blk, dict) else ""
            result_path = (result_blk.get("path") or "").strip() if isinstance(result_blk, dict) else ""
            if not call_path:
                call_path = f"{CONV_TC_PREFIX}{tid}.{call_id}.call"
            if not result_path:
                result_path = f"{CONV_TC_PREFIX}{tid}.{call_id}.result"
            status, hint = _tool_status_and_hint(result_blk)
            label = tool_id or "tool"
            attrs = {
                "tool": tool_id,
                "status": status,
                "call": call_path,
                "result": result_path,
            }
            lines.extend(_format_index_row(label, "", attrs=attrs, hint=hint))
        lines.append("")

    artifacts: Dict[str, Dict[str, Any]] = {}
    for blk in filtered:
        bpath = (blk.get("path") or "").strip()
        if bpath.startswith(REACT_FILE_REF_PREFIX):
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            entry = artifacts.setdefault(bpath, {"path": bpath})
            if blk.get("mime"):
                entry["mime"] = blk.get("mime")
            for key in ("tool_call_id", "tool_id", "kind", "visibility", "summary", "description", "filename"):
                if meta.get(key) and not entry.get(key):
                    entry[key] = meta.get(key)
            if blk.get("text") and not entry.get("hint"):
                entry["hint"] = blk.get("text")
        if (blk.get("type") or "").strip() == "react.tool.result" and (blk.get("mime") or "").strip() == "application/json":
            parsed = _maybe_parse_json(blk.get("text") or "") if isinstance(blk.get("text"), str) else None
            if not isinstance(parsed, dict):
                continue
            apath = str(parsed.get("artifact_path") or "").strip()
            if not apath or not apath.startswith(REACT_FILE_REF_PREFIX):
                continue
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            entry = artifacts.setdefault(apath, {"path": apath})
            for key in ("mime", "kind", "visibility", "summary", "description", "filename"):
                if parsed.get(key) and not entry.get(key):
                    entry[key] = parsed.get(key)
            for key in ("tool_call_id", "tool_id"):
                if meta.get(key) and not entry.get(key):
                    entry[key] = meta.get(key)
                elif parsed.get(key) and not entry.get(key):
                    entry[key] = parsed.get(key)
    if artifacts:
        lines.append("artifacts:")
        for apath, meta in artifacts.items():
            label = str(meta.get("filename") or "").strip()
            if not label:
                try:
                    label = pathlib.Path(apath).name
                except Exception:
                    label = ""
            label = label or "artifact"
            attrs = {
                "mime": meta.get("mime"),
                "kind": meta.get("kind"),
                "source_tool": meta.get("tool_call_id") or meta.get("tool_id"),
                "visibility": meta.get("visibility"),
            }
            hint = meta.get("summary") or meta.get("description") or meta.get("hint") or label
            lines.extend(_format_index_row(label, apath, attrs=attrs, hint=hint))
        lines.append("")

    used_sids = extract_sources_used_from_blocks(filtered)
    if sources_pool and used_sids:
        lines.append("sources:")
        for row in materialize_sources_by_sids(sources_pool or [], used_sids):
            if not isinstance(row, dict):
                continue
            sid = row.get("sid")
            if sid is None:
                continue
            selector = f"{CONV_SO_PREFIX}sources_pool[{int(sid)}]" if isinstance(sid, (int, float)) else f"{CONV_SO_PREFIX}sources_pool[{sid}]"
            attrs = {
                "title": row.get("title"),
                "url": row.get("url"),
            }
            lines.extend(_format_index_row("source", selector, attrs=attrs, hint=row.get("text") or row.get("content") or ""))
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).rstrip() + "\n"


def extract_followups_from_blocks(blocks: List[Dict[str, Any]]) -> List[str]:
    items: List[str] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") != "stage.suggested_followups":
            continue
        meta = b.get("meta") if isinstance(b.get("meta"), dict) else {}
        vals = meta.get("items") if isinstance(meta, dict) else None
        if isinstance(vals, list):
            for v in vals:
                if isinstance(v, str) and v.strip():
                    items.append(v.strip())
    return items


def extract_clarification_questions_from_blocks(blocks: List[Dict[str, Any]]) -> List[str]:
    items: List[str] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") != "stage.clarification":
            continue
        meta = b.get("meta") if isinstance(b.get("meta"), dict) else {}
        vals = meta.get("questions") if isinstance(meta, dict) else None
        if isinstance(vals, list):
            for v in vals:
                if isinstance(v, str) and v.strip():
                    items.append(v.strip())
    return items


def _attachment_name_from_path(path: str) -> str:
    path = (path or "").rstrip("/")
    if "/" in path:
        return path.rsplit("/", 1)[-1]
    return path


def extract_user_attachments_from_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_path: Dict[str, Dict[str, Any]] = {}
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        btype = (b.get("type") or "")
        if btype not in ("user.attachment.meta", "user.attachment"):
            continue
        path = (b.get("path") or "").strip()
        if not path:
            continue
        entry = by_path.setdefault(path, {"path": path})
        ts = (b.get("ts") or "").strip() if isinstance(b.get("ts"), str) else ""
        if ts and not entry.get("ts"):
            entry["ts"] = ts
        if btype == "user.attachment.meta":
            meta = b.get("meta") if isinstance(b.get("meta"), dict) else {}
            entry["meta"] = dict(meta or {})
            entry["text"] = b.get("text") if isinstance(b.get("text"), str) else entry.get("text")
        else:
            entry["mime"] = (b.get("mime") or "").strip()
    out: List[Dict[str, Any]] = []
    for path, entry in by_path.items():
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        filename = _attachment_name_from_path(path)
        mime = (entry.get("mime") or meta.get("mime") or "").strip() or "application/octet-stream"
        payload = {
            "filename": filename,
            "mime": mime,
            "artifact_path": path,
        }
        if entry.get("ts"):
            payload["ts"] = entry.get("ts")
        for key in ("rn", "hosted_uri", "key", "physical_path"):
            if meta.get(key):
                payload[key] = meta.get(key)
        if not payload.get("physical_path") and meta.get("local_path"):
            payload["physical_path"] = meta.get("local_path")
        if meta.get("summary") or meta.get("description"):
            payload["summary"] = meta.get("summary") or meta.get("description")
        for key in ("event_kind", "event_type", "is_continuation", "message_id", "sequence"):
            if meta.get(key) is not None:
                payload[key] = meta.get(key)
        out.append(payload)
    return out


def extract_assistant_files_from_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    file_rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") != "react.tool.result":
            continue
        if (b.get("mime") or "").strip() != "application/json":
            continue
        txt = b.get("text")
        if not isinstance(txt, str):
            continue
        meta = _parse_meta_json(txt)
        if isinstance(meta, dict) and meta.get("error"):
            continue
        if meta.get("visibility") != "external":
            continue
        if (meta.get("kind") or "").strip() != "file":
            continue
        if not (meta.get("hosted_uri") or meta.get("rn") or meta.get("key") or meta.get("physical_path") or meta.get("local_path")):
            continue
        p = (meta.get("artifact_path") or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        file_rows.append({
            "path": p,
            "ts": (b.get("ts") or "").strip() if isinstance(b.get("ts"), str) else "",
            "meta": meta,
        })
    out: List[Dict[str, Any]] = []
    for row in file_rows:
        p = str(row.get("path") or "").strip()
        art = resolve_artifact_from_timeline({"blocks": blocks, "sources_pool": []}, p)
        if not isinstance(art, dict):
            continue
        filename = art.get("filename") or art.get("filepath") or ""
        if not filename:
            phys = art.get("physical_path") or ""
            if isinstance(phys, str) and phys.strip():
                try:
                    import pathlib
                    filename = pathlib.Path(phys).name
                except Exception:
                    filename = ""
        payload = {
            "filename": filename,
            "mime": art.get("mime") or "application/octet-stream",
            "artifact_path": p,
        }
        if row.get("ts"):
            payload["ts"] = row.get("ts")
        for key in ("rn", "hosted_uri", "key", "physical_path"):
            if art.get(key):
                payload[key] = art.get(key)
        if not payload.get("physical_path") and art.get("local_path"):
            payload["physical_path"] = art.get("local_path")
        if art.get("summary") or art.get("description"):
            payload["summary"] = art.get("summary") or art.get("description")
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        for key in ("tool_id", "tool_call_id", "call_id", "sub_type"):
            if meta.get(key):
                payload[key] = meta.get(key)
        out.append(payload)
    return out


def _build_turn_view(
    *,
    turn_id: str,
    blocks: List[Dict[str, Any]],
    sources_pool: Optional[List[Dict[str, Any]]] = None,
    render_thinking: bool = True,
) -> Dict[str, Any]:
    sources_pool = list(sources_pool or [])
    user_block = extract_user_prompt_block(blocks)
    assistant_block = extract_assistant_completion_block(blocks)
    assistant_blocks = extract_assistant_completion_blocks(blocks)
    attachments = extract_user_attachments_from_blocks(blocks)
    files = extract_assistant_files_from_blocks(blocks)
    used_sids = extract_sources_used_from_blocks(blocks)
    suggested_followups = extract_followups_from_blocks(blocks)
    clarifications = extract_clarification_questions_from_blocks(blocks)
    used_sources = materialize_sources_by_sids(sources_pool, used_sids)
    timeline_text_items = _extract_timeline_text_items(blocks, turn_id)
    thinking_items = _extract_thinking_items(blocks, turn_id) if render_thinking else []
    return {
        "turn_id": turn_id,
        "user": {
            "text": user_block.get("text") if isinstance(user_block, dict) else "",
            "ts": user_block.get("ts") if isinstance(user_block, dict) else "",
        },
        "assistant": {
            "text": assistant_block.get("text") if isinstance(assistant_block, dict) else "",
            "ts": assistant_block.get("ts") if isinstance(assistant_block, dict) else "",
        },
        "assistants": [
            {
                "text": blk.get("text") if isinstance(blk.get("text"), str) else "",
                "ts": blk.get("ts") if isinstance(blk.get("ts"), str) else "",
                "path": blk.get("path") if isinstance(blk.get("path"), str) else "",
                "meta": blk.get("meta") if isinstance(blk.get("meta"), dict) else {},
            }
            for blk in assistant_blocks
            if isinstance(blk.get("text"), str) and str(blk.get("text") or "").strip()
        ],
        "attachments": attachments,
        "files": files,
        "citations": used_sources,
        "timeline_text": timeline_text_items,
        "thinking": thinking_items,
        "followups": suggested_followups,
        "clarification_questions": clarifications,
    }


def _extract_timeline_text_items(blocks: List[Dict[str, Any]], turn_id: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not blocks or not turn_id:
        return items
    text_by_path: Dict[str, Dict[str, Any]] = {}
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("turn_id") != turn_id:
            continue
        path = (b.get("path") or "").strip()
        if path and isinstance(b.get("text"), str):
            text_by_path[path] = b

    def _to_ms(ts_val: str) -> Optional[int]:
        if not ts_val:
            return None
        try:
            sec = ts_key(ts_val)
            if sec == float("-inf"):
                return None
            return int(sec * 1000)
        except Exception:
            return None

    idx = 0
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("turn_id") != turn_id:
            continue
        btype = b.get("type") or ""
        if btype == "react.notes":
            text = b.get("text") or ""
            if not isinstance(text, str) or not text.strip():
                continue
            ts_val = (b.get("ts") or "").strip()
            ts_ms = _to_ms(ts_val)
            item = {
                "artifact_name": f"timeline_text.react.notes.{idx}",
                "text": text,
            }
            if ts_ms is not None:
                item["ts_first"] = ts_ms
                item["ts_last"] = ts_ms
            items.append(item)
            idx += 1
            continue
        if btype != "react.tool.result":
            continue
        if (b.get("mime") or "").strip() != "application/json":
            continue
        meta = _parse_meta_json(b.get("text") or "")
        if not isinstance(meta, dict):
            continue
        if (meta.get("channel") or "").strip() != "timeline_text":
            continue
        ap = (meta.get("artifact_path") or "").strip()
        if not ap:
            continue
        content_block = text_by_path.get(ap)
        if not content_block:
            continue
        content = content_block.get("text")
        if not isinstance(content, str) or not content.strip():
            continue
        ts_val = (content_block.get("ts") or b.get("ts") or "").strip()
        ts_ms = _to_ms(ts_val)
        item = {
            "artifact_name": f"timeline_text.{turn_id}.{idx}",
            "text": content,
        }
        if ts_ms is not None:
            item["ts_first"] = ts_ms
            item["ts_last"] = ts_ms
        items.append(item)
        idx += 1
    return items


def _extract_thinking_items(blocks: List[Dict[str, Any]], turn_id: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not blocks or not turn_id:
        return items
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        if blk.get("turn_id") != turn_id:
            continue
        if (blk.get("type") or "") != "react.thinking":
            continue
        txt = blk.get("text")
        if not isinstance(txt, str) or not txt.strip():
            continue
        meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
        title = (meta.get("title") or "").strip() or "react"
        ts_val = blk.get("ts") or ""
        ts_ms = None
        try:
            sec = ts_key(ts_val)
            if sec != float("-inf"):
                ts_ms = int(sec * 1000)
        except Exception:
            ts_ms = None
        item = {
            "agent": title,
            "text": txt,
        }
        if ts_ms is not None:
            item["ts_first"] = ts_ms
            item["ts_last"] = ts_ms
        items.append(item)
    return items


def materialize_sources_by_sids(pool: List[Dict[str, Any]], sids: List[int]) -> List[Dict[str, Any]]:
    if not sids or not pool:
        return []
    wanted = set(int(s) for s in sids if isinstance(s, (int, float)))
    return [row for row in pool if isinstance(row, dict) and int(row.get("sid") or 0) in wanted]


def ensure_sources_in_pool(pool: List[Dict[str, Any]], sources: Any) -> tuple[List[Dict[str, Any]], List[int]]:
    normalized = citations_module.normalize_sources_any(sources)
    if not normalized:
        return pool, []
    merged = citations_module.dedupe_sources_by_url(pool, normalized)
    return merged, extract_source_sids(merged)


def resolve_artifact_from_timeline(timeline: Dict[str, Any], path: str) -> Optional[Dict[str, Any]]:
    """
    Build a canonical artifact dict from timeline blocks.
    Returns None if no matching blocks found.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    p = path.strip()
    source_conversation_id, source_selector = parse_sources_pool_ref(p)
    if source_selector:
        if source_conversation_id:
            return None
        return {"kind": "sources_pool", "items": resolve_sources_pool_selector(timeline, source_selector)}
    lookup_paths = {p}
    logical_from_physical = physical_path_to_logical_path(p)
    if logical_from_physical:
        lookup_paths.add(logical_from_physical)

    blocks = _collect_blocks(timeline)
    working_summary_suffix = ".conv.working.summary"
    if p.startswith(CONV_WS_PREFIX) and p.endswith(working_summary_suffix):
        body = p[len(CONV_WS_PREFIX) : -len(working_summary_suffix)].strip()
        if body.startswith("conv_"):
            _, sep, rest = body.partition(".")
            if sep and rest:
                body = rest.strip()
        turn_id = body
        if turn_id:
            latest_summary = None
            for b in reversed(blocks):
                if not isinstance(b, dict):
                    continue
                if (b.get("type") or "").strip() != "conv.working.summary":
                    continue
                if (b.get("turn_id") or "").strip() != turn_id:
                    continue
                if not isinstance(b.get("text"), str) or not b.get("text").strip():
                    continue
                latest_summary = b
                break
            if isinstance(latest_summary, dict):
                meta = latest_summary.get("meta") if isinstance(latest_summary.get("meta"), dict) else {}
                art: Dict[str, Any] = {
                    "path": p,
                    "kind": "display",
                    "mime": (latest_summary.get("mime") or "text/markdown").strip(),
                    "sources_used": [],
                    "text": latest_summary.get("text") or "",
                    "source_path": (latest_summary.get("path") or "").strip(),
                    "alias": True,
                }
                if latest_summary.get("ts"):
                    art["ts"] = latest_summary.get("ts")
                for key, val in meta.items():
                    if key in art or val is None:
                        continue
                    art[key] = val
                return art
    if p.startswith(f"{CONV_AR_PREFIX}plan.latest:"):
        plan_id = p[len(f"{CONV_AR_PREFIX}plan.latest:") :].strip()
        latest_block = latest_plan_block_by_id(blocks, plan_id, include_preserved=True)
        if not isinstance(latest_block, dict):
            return None
        meta = latest_block.get("meta") if isinstance(latest_block.get("meta"), dict) else {}
        art: Dict[str, Any] = {
            "path": p,
            "kind": "display",
            "mime": (latest_block.get("mime") or "application/json").strip(),
            "sources_used": [],
            "text": latest_block.get("text") if isinstance(latest_block.get("text"), str) else "",
            "source_path": (latest_block.get("path") or "").strip(),
        }
        if latest_block.get("ts"):
            art["ts"] = latest_block.get("ts")
        for key, val in meta.items():
            if key in art or val is None:
                continue
            art[key] = val
        return art

    meta: Dict[str, Any] = {}
    # meta may be encoded in react.tool.result json blocks
    meta_block_meta: Dict[str, Any] = {}
    for b in reversed(blocks):
        if (b.get("type") or "") != "react.tool.result":
            continue
        if (b.get("mime") or "").strip() != "application/json":
            continue
        txt = b.get("text")
        if not isinstance(txt, str):
            continue
        meta_obj = _parse_meta_json(txt)
        if meta_obj.get("artifact_path") in lookup_paths or meta_obj.get("physical_path") in lookup_paths:
            meta = meta_obj
            bmeta = b.get("meta")
            if isinstance(bmeta, dict) and bmeta:
                meta_block_meta = dict(bmeta)
            break

    matching = [b for b in blocks if (b.get("path") or "") in lookup_paths]
    if not matching and not meta:
        return None

    # fall back to block-level meta (e.g. user attachments)
    if not meta:
        for b in matching:
            bmeta = b.get("meta")
            if isinstance(bmeta, dict) and bmeta:
                meta = dict(bmeta)
                break

    # choose latest content block
    latest_block = next((b for b in reversed(matching)), None)
    text_block = next((b for b in reversed(matching) if isinstance(b.get("text"), str)), None)
    bin_block = next((b for b in reversed(matching) if b.get("base64")), None)

    mime = (meta.get("mime") or "").strip()
    if not mime:
        if text_block:
            mime = (text_block.get("mime") or "text/plain").strip()
        elif bin_block:
            mime = (bin_block.get("mime") or "").strip()

    kind = (meta.get("kind") or "").strip() or ("file" if bin_block else "display")
    sources_used = meta.get("sources_used") or []
    filepath = meta.get("physical_path") or ""

    art: Dict[str, Any] = {
        "path": p,
        "kind": kind,
        "mime": mime,
        "sources_used": sources_used,
    }
    if isinstance(latest_block, dict) and latest_block.get("ts"):
        art["ts"] = latest_block.get("ts")
    if filepath:
        art["filepath"] = filepath
    for key, val in meta.items():
        if key in art:
            continue
        art[key] = val
    if meta_block_meta:
        for key, val in meta_block_meta.items():
            if key in art:
                continue
            art[key] = val
    # Merge any block-level meta (e.g., hosting info on content blocks)
    for b in matching:
        bmeta = b.get("meta")
        if not isinstance(bmeta, dict):
            continue
        for key, val in bmeta.items():
            if key in art or val is None:
                continue
            art[key] = val
    if isinstance(latest_block, dict) and latest_block.get("payload") is not None:
        art["payload"] = latest_block.get("payload")
    if text_block and isinstance(text_block.get("text"), str):
        art["text"] = text_block.get("text")
    if bin_block and bin_block.get("base64"):
        art["base64"] = bin_block.get("base64")
    if not art.get("physical_path") and art.get("local_path"):
        art["physical_path"] = art.get("local_path")
    art.pop("local_path", None)
    return art


def materialize_show_artifacts(timeline: Dict[str, Any], show_paths: List[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for raw_path in (show_paths or []):
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        path = raw_path.strip()
        if path.startswith(CONV_TC_PREFIX):
            # Prefer the canonical resolved artifact so conv:tc:<...>.result reads back the
            # actual inline tool payload when present, not just the envelope/meta block.
            val = resolve_artifact_from_timeline(timeline, path)
            if isinstance(val, dict) and (
                (isinstance(val.get("text"), str) and val.get("text").strip())
                or val.get("base64")
            ):
                text = val.get("text")
                base64 = val.get("base64")
                mime = (val.get("mime") or "").strip()
                fmt = "text"
                if mime == "application/json":
                    fmt = "json"
                elif mime == "text/html":
                    fmt = "html"
                artifact: Dict[str, Any] = {"format": fmt}
                if mime:
                    artifact["mime"] = mime
                if isinstance(text, str) and text.strip():
                    artifact["text"] = text
                if base64:
                    artifact["base64"] = base64
                items.append({
                    "context_path": path,
                    "artifact": artifact,
                })
                continue
        if path.startswith(CONV_SO_PREFIX):
            path = path[len(CONV_SO_PREFIX):]
        if path.startswith("sources_pool["):
            val = {"kind": "sources_pool", "items": resolve_sources_pool_selector(timeline, path)}
        elif path.startswith("sk:"):
            try:
                from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import get_skill_by_id
                skill_id = path[len("sk:"):].strip()
                skill_obj = get_skill_by_id(skill_id)
                if skill_obj and getattr(skill_obj, "content", None):
                    val = {"kind": "skill", "text": skill_obj.content, "mime": "text/markdown"}
                else:
                    val = None
            except Exception:
                val = None
        else:
            val = resolve_artifact_from_timeline(timeline, path)
        if not isinstance(val, dict):
            continue
        text = val.get("text")
        base64 = val.get("base64")
        mime = (val.get("mime") or "").strip()
        fmt = "text"
        if mime == "application/json":
            fmt = "json"
        elif mime == "text/html":
            fmt = "html"
        artifact: Dict[str, Any] = {"format": fmt}
        if mime:
            artifact["mime"] = mime
        if isinstance(text, str) and text.strip():
            artifact["text"] = text
        if base64:
            artifact["base64"] = base64
        items.append({
            "context_path": path,
            "artifact": artifact,
        })
    return items


@dataclass
class Timeline:
    runtime: RuntimeCtx
    svc: Optional[Any] = None
    _lock: Optional["asyncio.Lock"] = field(default=None, init=False, repr=False)
    _cache_trace_prev: Optional[Dict[str, Any]] = field(default=None, init=False, repr=False)
    _cache_ttl_bootstrap: bool = field(default=False, init=False, repr=False)
    _suppress_prev_turn_cache: bool = field(default=False, init=False, repr=False)
    _feedback_seen: Dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _feedback_updates: List[Dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _feedback_updates_integrated: bool = field(default=False, init=False, repr=False)
    last_rendered_event_cursor: TimelineEventCursor = field(default_factory=TimelineEventCursor, init=False, repr=False)
    version: int = 1
    ts: str = ""
    blocks: List[Dict[str, Any]] = None
    sources_pool: List[Dict[str, Any]] = None
    announce_blocks: List[Dict[str, Any]] = None
    current_turn_offset: Optional[int] = None
    conversation_title: str = ""
    conversation_started_at: str = ""
    last_external_event_id: str = ""
    last_external_event_seq: Optional[int] = None
    cache_last_touch_at: Optional[int] = None
    cache_last_ttl_seconds: Optional[int] = None
    # Last APPLIED per-user agent selection for this conversation (deny-list +
    # model pick). Compared against the stored selection each turn to classify
    # cache-colding deltas; persists with the conversation.
    agent_selection_snapshot: Optional[Dict[str, Any]] = None
    last_known_feedback_ts: str = ""

    def __post_init__(self) -> None:
        self.blocks = list(self.blocks or [])
        self.sources_pool = list(self.sources_pool or [])
        self.announce_blocks = list(self.announce_blocks or [])
        if not self.ts:
            self.ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if self._lock is None:
            try:
                import asyncio
                self._lock = asyncio.Lock()
            except Exception:
                self._lock = None
        self._cache_ttl_bootstrap = self.cache_last_ttl_seconds is not None

    @property
    def last_render_processed_event_timestamp(self) -> str:
        return self.last_rendered_event_cursor.timestamp

    def note_processed_external_event(
        self,
        *,
        timestamp: str,
        event_id: str = "",
        sequence: Optional[int] = None,
    ) -> None:
        """Advance the rendered-event cursor for an external event the handler
        fully processed without producing timeline blocks.

        The event-bus close gate settles when the handler-processed cursor
        reaches the lane state's last processed event. A no-block event can
        never advance the cursor through a render pass (there is nothing to
        render), so it counts as handler-processed at application time —
        otherwise every subsequent `complete` is deferred with
        ``new_events_after_handler_snapshot`` for the rest of the turn.
        """
        ts = str(timestamp or "").strip()
        if not ts:
            return
        seq_val: Optional[int] = None
        if sequence is not None:
            try:
                seq_val = int(sequence)
            except Exception:
                seq_val = None
        candidate = TimelineEventCursor(
            timestamp=ts,
            event_id=str(event_id or "").strip(),
            sequence=seq_val,
        )
        self.last_rendered_event_cursor = _later_timeline_event_cursor(
            self.last_rendered_event_cursor,
            candidate,
        )

    @classmethod
    def from_payload(cls, payload: Dict[str, Any], *, runtime: RuntimeCtx, svc: Optional[Any] = None) -> "Timeline":
        parsed = parse_timeline_payload(payload or {})
        timeline = cls(
            runtime=runtime,
            svc=svc,
            version=int(parsed.get("version") or 1),
            ts=str(parsed.get("ts") or ""),
            blocks=list(parsed.get("blocks") or []),
            sources_pool=list(parsed.get("sources_pool") or []),
            conversation_title=str(parsed.get("conversation_title") or ""),
            conversation_started_at=str(parsed.get("conversation_started_at") or ""),
            last_external_event_id=str(parsed.get("last_external_event_id") or ""),
            last_external_event_seq=parsed.get("last_external_event_seq"),
            cache_last_touch_at=parsed.get("cache_last_touch_at"),
            cache_last_ttl_seconds=parsed.get("cache_last_ttl_seconds"),
            agent_selection_snapshot=parsed.get("agent_selection_snapshot"),
            last_known_feedback_ts=parsed.get("last_known_feedback_ts") or "",
        )
        cursor = parsed.get("last_rendered_event_cursor")
        if isinstance(cursor, TimelineEventCursor):
            timeline.last_rendered_event_cursor = cursor
        return timeline

    def to_payload(self) -> Dict[str, Any]:
        return build_timeline_payload(
            blocks=list(self.blocks or []),
            sources_pool=list(self.sources_pool or []),
            conversation_title=self.conversation_title,
            conversation_started_at=self.conversation_started_at,
            last_external_event_id=self.last_external_event_id,
            last_external_event_seq=self.last_external_event_seq,
            last_rendered_event_cursor=self.last_rendered_event_cursor,
            cache_last_touch_at=self.cache_last_touch_at,
            cache_last_ttl_seconds=self.cache_last_ttl_seconds,
            agent_selection_snapshot=self.agent_selection_snapshot,
            last_known_feedback_ts=self.last_known_feedback_ts,
        )

    def _blocks_for_persist(self) -> List[Dict[str, Any]]:
        """Persist only the post-compaction window (from the latest summary onward)."""
        return self._slice_after_compaction_summary(list(self.blocks or []))

    def update_timestamp(self) -> None:
        self.ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def write_local(self) -> None:
        outdir = (self.runtime.outdir or "").strip()
        if not outdir:
            return
        try:
            import pathlib
            out_path = pathlib.Path(outdir) / TIMELINE_FILENAME
            payload = build_timeline_payload(
                blocks=self._blocks_for_persist(),
                sources_pool=list(self.sources_pool or []),
                conversation_title=self.conversation_title,
                conversation_started_at=self.conversation_started_at,
                last_external_event_id=self.last_external_event_id,
                last_external_event_seq=self.last_external_event_seq,
                last_rendered_event_cursor=self.last_rendered_event_cursor,
                cache_last_touch_at=self.cache_last_touch_at,
                cache_last_ttl_seconds=self.cache_last_ttl_seconds,
                agent_selection_snapshot=self.agent_selection_snapshot,
                last_known_feedback_ts=self.last_known_feedback_ts,
                include_sources_pool=True,
            )
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def contribute(self, blocks: List[Dict[str, Any]]) -> int:
        added = 0
        for blk in (blocks or []):
            if not isinstance(blk, dict):
                continue
            if not blk.get("ts"):
                try:
                    blk["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                except Exception:
                    blk["ts"] = ""
            self.blocks.append(blk)
            added += 1
        if added:
            self.update_timestamp()
        return added

    async def contribute_async(self, blocks: List[Dict[str, Any]]) -> int:
        if self._lock is None:
            return self.contribute(blocks)
        async with self._lock:
            return self.contribute(blocks)

    def resolve_artifact(self, path: str) -> Optional[Dict[str, Any]]:
        return resolve_artifact_from_timeline(
            {"blocks": self.blocks, "sources_pool": self.sources_pool}, path
        )

    def resolve_sources_pool(self, selector: str) -> List[Dict[str, Any]]:
        return resolve_sources_pool_selector(
            {"blocks": self.blocks, "sources_pool": self.sources_pool}, selector
        )

    def set_sources_pool(self, sources_pool: List[Dict[str, Any]]) -> None:
        normalized: List[Dict[str, Any]] = []
        for row in (sources_pool or []):
            if not isinstance(row, dict):
                continue
            r = dict(row)
            if not r.get("physical_path") and r.get("local_path"):
                r["physical_path"] = r.get("local_path")
            r.pop("local_path", None)
            normalized.append(r)
        self.sources_pool = normalized

    def set_conversation_title(self, title: str) -> None:
        self.conversation_title = (title or "").strip()

    def set_conversation_started_at(self, ts: str) -> None:
        self.conversation_started_at = (ts or "").strip()

    def set_announce(self, blocks: Optional[List[Dict[str, Any]]]) -> None:
        if not blocks:
            self.announce_blocks = []
        else:
            self.announce_blocks = [b for b in (blocks or []) if isinstance(b, dict)]

    def _event_sources(self) -> Any:
        return getattr(self.runtime, "event_sources", None)

    def _event_source_pipeline_enabled(self) -> bool:
        return bool(event_source_pipeline_enabled(self.runtime) and self._event_sources() is not None)

    def _timeline_segment_for_render(self, block: Dict[str, Any]) -> str:
        turn_id = str(block.get("turn_id") or block.get("turn") or "").strip()
        current_turn_id = str(getattr(self.runtime, "turn_id", "") or "").strip()
        if current_turn_id and turn_id == current_turn_id:
            return "current"
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        hidden_scope = str(meta.get("hidden_prune_scope") or "").strip()
        if hidden_scope == "cold_recent":
            return "recent"
        if hidden_scope == "old_turn":
            return "old"
        return ""

    def _clone_blocks_for_policy_view(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return a shallow policy/render view that does not mutate timeline storage."""
        out: List[Dict[str, Any]] = []
        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            cloned = dict(block)
            meta = block.get("meta")
            if isinstance(meta, dict):
                cloned["meta"] = dict(meta)
            out.append(cloned)
        return out

    def _apply_event_source_timeline_projection(
        self,
        blocks: List[Dict[str, Any]],
        *,
        timeline_blocks: Optional[List[Dict[str, Any]]] = None,
        **context: Any,
    ) -> List[Dict[str, Any]]:
        blocks = self._clone_blocks_for_policy_view(blocks)
        if not self._event_source_pipeline_enabled() or not blocks:
            return blocks
        try:
            full_timeline_blocks = self._clone_blocks_for_policy_view(timeline_blocks or self._collect_blocks())
            patch_timeline_segment_marks(blocks, timeline_segment_fn=self._timeline_segment_for_render)
            apply_event_source_transformers(
                event_sources=self._event_sources(),
                react_phase="timeline_projection",
                timeline_blocks=blocks,
                current_turn_id=str(getattr(self.runtime, "turn_id", "") or ""),
                full_timeline_blocks=full_timeline_blocks,
                **context,
            )
            return blocks
        except Exception:
            logger.debug("[react.event_source.timeline_projection_failed]", exc_info=True)
            return blocks
        finally:
            try:
                clear_timeline_segment_marks(blocks)
            except Exception:
                pass

    async def _apply_event_source_timeline_projection_async(
        self,
        blocks: List[Dict[str, Any]],
        *,
        timeline_blocks: Optional[List[Dict[str, Any]]] = None,
        **context: Any,
    ) -> List[Dict[str, Any]]:
        blocks = self._clone_blocks_for_policy_view(blocks)
        if not self._event_source_pipeline_enabled() or not blocks:
            return blocks
        try:
            full_timeline_blocks = self._clone_blocks_for_policy_view(timeline_blocks or self._collect_blocks())
            patch_timeline_segment_marks(blocks, timeline_segment_fn=self._timeline_segment_for_render)
            await apply_event_source_transformers_async(
                event_sources=self._event_sources(),
                react_phase="timeline_projection",
                timeline_blocks=blocks,
                current_turn_id=str(getattr(self.runtime, "turn_id", "") or ""),
                full_timeline_blocks=full_timeline_blocks,
                **context,
            )
            return blocks
        except Exception:
            logger.debug("[react.event_source.timeline_projection_failed]", exc_info=True)
            return blocks
        finally:
            try:
                clear_timeline_segment_marks(blocks)
            except Exception:
                pass

    def _apply_event_source_compaction_projection(
        self,
        blocks: List[Dict[str, Any]],
        *,
        timeline_blocks: Optional[List[Dict[str, Any]]] = None,
        **context: Any,
    ) -> List[Dict[str, Any]]:
        blocks = self._clone_blocks_for_policy_view(blocks)
        if not self._event_source_pipeline_enabled() or not blocks:
            return blocks
        try:
            full_timeline_blocks = self._clone_blocks_for_policy_view(timeline_blocks or self._collect_blocks())
            patch_timeline_segment_marks(blocks, timeline_segment_fn=self._timeline_segment_for_render)
            apply_event_source_transformers(
                event_sources=self._event_sources(),
                react_phase="compaction_projection",
                timeline_blocks=blocks,
                current_turn_id=str(getattr(self.runtime, "turn_id", "") or ""),
                full_timeline_blocks=full_timeline_blocks,
                **context,
            )
            return blocks
        except Exception:
            logger.debug("[react.event_source.compaction_projection_failed]", exc_info=True)
            return blocks
        finally:
            try:
                clear_timeline_segment_marks(blocks)
            except Exception:
                pass

    def is_cache_hot(self, *, buffer_seconds: Optional[int] = None) -> bool:
        """
        Best-effort check: whether cache TTL is active and has not expired since last touch.
        Used to decide if we can mutate historical blocks without invalidating cache.
        """
        try:
            session = getattr(self.runtime, "session", None)
            ttl_seconds = getattr(session, "cache_ttl_seconds", None) if session is not None else None
            if ttl_seconds is None:
                ttl_seconds = getattr(self.runtime, "cache_ttl_seconds", None)
            ttl = int(ttl_seconds or 0)
        except Exception:
            ttl = 0
        if ttl <= 0:
            return False
        buf = 0
        if buffer_seconds is not None:
            try:
                buf = max(0, int(buffer_seconds or 0))
            except Exception:
                buf = 0
        else:
            try:
                session = getattr(self.runtime, "session", None)
                buf = int(getattr(session, "cache_ttl_prune_buffer_seconds", 0) or 0) if session is not None else 0
            except Exception:
                buf = 0
        effective_ttl = max(0, ttl - buf)
        if effective_ttl <= 0:
            return False
        last_touch = self.cache_last_touch_at
        try:
            last_touch_val = int(last_touch) if last_touch is not None else None
        except Exception:
            last_touch_val = None
        if last_touch_val is None:
            return False
        try:
            return (int(time.time()) - last_touch_val) < effective_ttl
        except Exception:
            return False

    def feedback_updates(self) -> List[Dict[str, Any]]:
        ret = list(self._feedback_updates or [])
        return ret

    def feedback_updates_integrated(self) -> bool:
        return bool(self._feedback_updates_integrated)

    async def refresh_feedbacks(self, *, ctx_client: Any, days: int = 365) -> None:
        if not ctx_client:
            self._feedback_updates = []
            self._feedback_updates_integrated = False
            return
        log = logger
        from kdcube_ai_app.apps.chat.sdk.solutions.react.feeback import Feedback
        self._feedback_updates = []
        self._feedback_updates_integrated = False
        try:
            turn_ids = extract_turn_ids_from_blocks(self.blocks or [])
        except Exception:
            try:
                log.error(f"[timeline.refresh_feedbacks]: failed to extract turn ids from blocks. {traceback.format_exc()}")
            except Exception:
                pass
            turn_ids = []
        if not turn_ids:
            return

        def _ts_to_epoch(ts_val: Any) -> Optional[float]:
            if isinstance(ts_val, (int, float)):
                val = float(ts_val)
                if val > 1e12:
                    val = val / 1000.0
                return val
            if isinstance(ts_val, str):
                s = ts_val.strip()
                if not s:
                    return None
                try:
                    if s.endswith("Z"):
                        s = s[:-1] + "+00:00"
                    dt = _dt.datetime.fromisoformat(s)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=_dt.timezone.utc)
                    return dt.timestamp()
                except Exception:
                    try:
                        val = float(s)
                        if val > 1e12:
                            val = val / 1000.0
                        return val
                    except Exception:
                        return None
            return None

        def _epoch_to_iso(val: float) -> str:
            try:
                return _dt.datetime.fromtimestamp(val, tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
            except Exception:
                return ""

        def _earliest_turn_ts() -> str:
            earliest_val: Optional[float] = None
            for blk in (self.blocks or []):
                if not isinstance(blk, dict):
                    continue
                if (blk.get("turn_id") or "").strip() == "":
                    continue
                ts_val = blk.get("ts")
                epoch = _ts_to_epoch(ts_val)
                if epoch is None:
                    continue
                if earliest_val is None or epoch < earliest_val:
                    earliest_val = epoch
            return _epoch_to_iso(earliest_val) if earliest_val is not None else ""

        fb = Feedback(ctx_client=ctx_client, logger_obj=log)
        since_ts = (self.last_known_feedback_ts or "").strip()
        if not since_ts:
            since_ts = _earliest_turn_ts()

        try:
            log.info(
                f"[timeline.refresh_feedbacks] query: user={self.runtime.user_id} "
                f"conv={self.runtime.conversation_id} turn_ids={len(turn_ids)} "
                f"since_ts={since_ts or ''} days={days}"
            )
        except Exception:
            pass

        recent_by_turn = await fb.collect_recent(
            user_id=self.runtime.user_id,
            conversation_id=self.runtime.conversation_id,
            turn_ids=turn_ids,
            since_ts=since_ts or None,
            days=days,
        )
        try:
            count_recent = sum(len(v or []) for v in (recent_by_turn or {}).values())
            log.info(
                f"[timeline.refresh_feedbacks] query results: turns={len(recent_by_turn or {})} "
                f"items={count_recent}"
            )
        except Exception:
            pass

        updates, seen_map = fb.diff_updates(
            feedbacks_by_turn=recent_by_turn,
            seen_map=self._feedback_seen,
        )
        pre_existing_digests = fb.existing_feedback_digests(self.blocks or [])

        integrated = False
        full_by_turn = recent_by_turn
        cache_hot = False
        try:
            cache_hot = bool(self.is_cache_hot())
            session = getattr(self.runtime, "session", None)
            ttl_seconds = getattr(session, "cache_ttl_seconds", None) if session is not None else None
            if ttl_seconds is None:
                ttl_seconds = getattr(self.runtime, "cache_ttl_seconds", None)
            buf_seconds = getattr(session, "cache_ttl_prune_buffer_seconds", 0) if session is not None else 0
            log.info(
                "[timeline.refresh_feedbacks] cache_hot="
                f"{cache_hot} ttl={ttl_seconds} buffer={buf_seconds} last_touch={self.cache_last_touch_at}"
            )
        except Exception:
            pass

        # Only inject on cold cache. No additional fetches here; use already collected results.
        if not cache_hot:
            try:
                integrated = fb.ensure_blocks(timeline=self, feedbacks_by_turn=full_by_turn)
                if integrated:
                    self.write_local()
            except Exception:
                try:
                    log.error(f"[timeline.refresh_feedbacks]: failed to ensure_blocks. {traceback.format_exc()}")
                except Exception:
                    pass
                integrated = False

        # Build updates for announce (only when changed since last seen).
        updates_payload: List[Dict[str, Any]] = []
        if updates:
            turn_ts_map: Dict[str, str] = {}
            for blk in (self.blocks or []):
                if not isinstance(blk, dict):
                    continue
                tid = (blk.get("turn_id") or "").strip()
                if not tid or tid in turn_ts_map:
                    continue
                ts_val = blk.get("ts")
                ts = ts_val.strip() if isinstance(ts_val, str) else ""
                if ts:
                    turn_ts_map[tid] = ts

            for item in updates:
                path = fb._path_for(item)
                if pre_existing_digests.get(path) == fb._digest_item(item):
                    continue
                updates_payload.append({
                    "turn_id": item.turn_id,
                    "turn_ts": turn_ts_map.get(item.turn_id, ""),
                    "feedback_ts": item.ts,
                    "origin": item.origin,
                    "reaction": item.reaction,
                    "text": item.text,
                })

        try:
            log.info(
                f"[timeline.refresh_feedbacks] announce: items={len(updates_payload)} "
                f"integrated={integrated} cache_hot={cache_hot}"
            )
        except Exception:
            pass

        # Update last-known feedback timestamp ONLY for feedbacks that are incorporated into the timeline.
        if not cache_hot and updates:
            incorporated: List[Any] = []
            if integrated:
                incorporated = list(updates)
            else:
                for item in updates:
                    path = fb._path_for(item)
                    if pre_existing_digests.get(path) == fb._digest_item(item):
                        incorporated.append(item)
            if incorporated:
                last_ts = fb.max_ts(incorporated)
                if last_ts:
                    self.last_known_feedback_ts = last_ts

        self._feedback_seen = seen_map
        self._feedback_updates = updates_payload
        self._feedback_updates_integrated = bool(integrated)
    def visible_paths(self) -> set[str]:
        paths: set[str] = set()
        blocks = self._apply_hidden_replacements(self._collect_blocks())
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            p = (blk.get("path") or "").strip()
            if not p:
                continue
            paths.add(p)
            if p.startswith(CONV_SO_PREFIX):
                paths.add(p[len(CONV_SO_PREFIX):])
        return paths

    def materialize_show_artifacts(self, show_paths: List[str]) -> List[Dict[str, Any]]:
        return materialize_show_artifacts(
            {"blocks": self.blocks, "sources_pool": self.sources_pool}, show_paths
        )

    def bind_params_with_refs(
        self,
        *,
        base_params: Dict[str, Any],
        tool_id: Optional[str] = None,
        visible_paths: Optional[set[str]] = None,
    ) -> tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
        params = dict(base_params or {})
        violations: List[Dict[str, Any]] = []
        content_lineage: List[str] = []

        def _read_text_from_file(rel_path: str) -> Optional[str]:
            outdir = (self.runtime.outdir or "").strip()
            if not outdir or not rel_path:
                return None
            try:
                import pathlib
                from kdcube_ai_app.apps.chat.sdk.runtime.workspace import resolve_artifact_path
                fp = resolve_artifact_path(pathlib.Path(outdir), rel_path).resolve()
                if not fp.exists():
                    return None
                return fp.read_text(encoding="utf-8")
            except Exception:
                return None

        def _current_turn_logical_ref(ref_value: str) -> str:
            raw = (ref_value or "").strip().lstrip("/")
            if raw.startswith(REACT_FILE_REF_PREFIX):
                raw = raw[len(REACT_FILE_REF_PREFIX):].strip().lstrip("/")
            turn_id = (self.runtime.turn_id or "").strip()
            if not turn_id:
                return ""
            for namespace in ("files", "git/projects", "git/snapshots", "attachments"):
                prefix = f"{namespace}/"
                if raw.startswith(prefix):
                    rel = raw[len(prefix):].strip().lstrip("/")
                    if rel:
                        if namespace == "files":
                            return f"{REACT_FILE_REF_PREFIX}{turn_id}.files/{rel}"
                        if namespace == "git/projects":
                            return f"{REACT_FILE_REF_PREFIX}{turn_id}.git/projects/{rel}"
                        if namespace == "git/snapshots":
                            return f"{REACT_FILE_REF_PREFIX}{turn_id}.git/snapshots/{rel}"
                        return f"{REACT_FILE_REF_PREFIX}{turn_id}.user.attachments/{rel}"
            return ""

        def _visible_ref_exists(ref_value: str) -> bool:
            if not ref_value:
                return False
            if visible_paths is not None and ref_value not in visible_paths:
                return False
            return self.resolve_artifact(ref_value) is not None or (
                visible_paths is not None and ref_value in visible_paths
            )

        def _record_ref_rewrite_warning(*, original: str, resolved: str, param_name: Optional[str]) -> None:
            if not original or not resolved or original == resolved:
                return
            violations.append({
                "code": "ref_path_normalized",
                "severity": "warning",
                "path": original,
                "param": param_name,
                "resolved_ref": resolved,
                "message": (
                    f"Accepted shorthand `ref:{original}` because it matched visible artifact "
                    f"`ref:{resolved}`. Prefer the canonical ref next time."
                ),
            })

        def _resolve_ref(val: Any, param_name: Optional[str] = None):
            if not isinstance(val, str) or not val.startswith("ref:"):
                return val
            is_rendering_content_ref = (
                (tool_id or "").startswith("rendering_tools.")
                and (param_name or "") == "content"
            )
            ref = val[len("ref:"):].strip()
            original_ref = ref
            if (
                ref
                and not ref.startswith((*CONV_NAMESPACE_PREFIXES, "ks:", "sk:", "sources_pool["))
            ):
                current_turn_ref = _current_turn_logical_ref(ref)
                if current_turn_ref and _visible_ref_exists(current_turn_ref):
                    _record_ref_rewrite_warning(
                        original=original_ref,
                        resolved=current_turn_ref,
                        param_name=param_name,
                    )
                    ref = current_turn_ref
                else:
                    logical_ref = physical_path_to_logical_path(ref)
                    if logical_ref:
                        ref = logical_ref
            if visible_paths is not None and ref not in visible_paths:
                logical_ref = physical_path_to_logical_path(ref)
                if logical_ref:
                    ref = logical_ref
            if visible_paths is not None and ref not in visible_paths:
                logical_ref = physical_path_to_logical_path(ref)
                current_turn_ref = _current_turn_logical_ref(original_ref)
                if logical_ref and logical_ref in visible_paths:
                    ref = logical_ref
                elif current_turn_ref and _visible_ref_exists(current_turn_ref):
                    _record_ref_rewrite_warning(
                        original=original_ref,
                        resolved=current_turn_ref,
                        param_name=param_name,
                    )
                    ref = current_turn_ref
            if visible_paths is not None and ref not in visible_paths:
                resolved = self.resolve_artifact(ref)
                visibility = ""
                if isinstance(resolved, dict):
                    visibility = (resolved.get("visibility") or "").strip()
                logical_ref = physical_path_to_logical_path(original_ref)
                if not logical_ref and original_ref and not original_ref.startswith((*CONV_NAMESPACE_PREFIXES, "ks:", "sk:", "sources_pool[")):
                    logical_ref = f"{REACT_FILE_REF_PREFIX}{original_ref.lstrip('/')}"
                if visibility == "internal":
                    violations.append({
                        "code": "ref_internal_not_visible",
                        "path": original_ref,
                        "param": param_name,
                        "visibility": visibility,
                        **({"suggested_ref": logical_ref} if logical_ref else {}),
                        "message": (
                            "channel=internal artifacts are private and are not visible to this tool call. "
                            "For rendering_tools.write_* source refs, recreate the source as an external artifact "
                            "with react.write channel=canvas or exec visibility=external."
                        ),
                    })
                else:
                    violations.append({
                        "code": "ref_not_visible",
                        "path": original_ref,
                        "param": param_name,
                        **({"suggested_ref": logical_ref} if logical_ref else {}),
                        **({"message": "ref: bindings use logical artifact paths such as conv:fi:<turn>.files/<file>, not physical turn/<namespace>/<file> paths."} if logical_ref else {}),
                    })
                return None
            if param_name == "sources_list" and not (ref.startswith(CONV_SO_PREFIX) or ref.startswith("sources_pool[")):
                violations.append({"code": "sources_list_requires_sources_pool", "path": ref, "param": param_name})
                return None
            # resolve via timeline
            if ref.startswith(CONV_SO_PREFIX) or ref.startswith("sources_pool["):
                resolved = self.resolve_sources_pool(ref if ref.startswith("sources_pool[") else ref[len(CONV_SO_PREFIX):])
                if is_rendering_content_ref:
                    violations.append({
                        "code": "renderer_content_ref_not_text",
                        "path": ref,
                        "param": param_name,
                        "message": "Renderer content refs must resolve to text in the renderer's requested input format.",
                    })
                    return None
                return resolved
            resolved = self.resolve_artifact(ref)
            if isinstance(resolved, dict):
                fp = resolved.get("filepath") or ""
                if fp:
                    txt = _read_text_from_file(fp)
                    if isinstance(txt, str):
                        return txt
                if ref.startswith(REACT_FILE_REF_PREFIX):
                    encoded = resolved.get("base64")
                    mime = (resolved.get("mime") or "").strip()
                    if encoded:
                        if is_rendering_content_ref:
                            if is_text_mime_type(mime):
                                try:
                                    import base64 as _base64
                                    return _base64.b64decode(str(encoded)).decode("utf-8")
                                except Exception:
                                    pass
                            violations.append({
                                "code": "renderer_content_ref_not_text",
                                "path": ref,
                                "param": param_name,
                                "message": "Renderer content refs must resolve to text in the renderer's requested input format.",
                            })
                            return None
                        return encoded
                    violations.append({
                        "code": "fi_ref_not_materialized",
                        "path": ref,
                        "param": param_name,
                        "message": (
                            "This conv:fi: ref is visible but its artifact file is not materialized locally. "
                            "ref:conv:fi bindings must consume artifact bytes/text, not timeline-rendered previews."
                        ),
                    })
                    return None
                text_value = resolved.get("text")
                if isinstance(text_value, str):
                    return text_value
                encoded = resolved.get("base64")
                if encoded:
                    mime = (resolved.get("mime") or "").strip()
                    if is_rendering_content_ref:
                        if is_text_mime_type(mime):
                            try:
                                import base64 as _base64
                                return _base64.b64decode(str(encoded)).decode("utf-8")
                            except Exception:
                                pass
                        violations.append({
                            "code": "renderer_content_ref_not_text",
                            "path": ref,
                            "param": param_name,
                            "message": "Renderer content refs must resolve to text in the renderer's requested input format.",
                        })
                        return None
                    return encoded
                if is_rendering_content_ref:
                    violations.append({
                        "code": "renderer_content_ref_not_text",
                        "path": ref,
                        "param": param_name,
                        "message": "Renderer content refs must resolve to text in the renderer's requested input format.",
                    })
                    return None
            return resolved

        def _walk(obj: Any, param_name: Optional[str] = None):
            if isinstance(obj, dict):
                return {k: _walk(v, param_name=str(k)) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_walk(v, param_name=param_name) for v in obj]
            return _resolve_ref(obj, param_name=param_name)

        params = _walk(params)
        return params, content_lineage, violations

    def set_current_turn_offset(self, offset: int) -> None:
        if isinstance(offset, int) and offset >= 0:
            self.current_turn_offset = offset

    def ensure_turn_header(self, *, turn_id: str, ts: Optional[str]) -> None:
        """
        Ensure the current turn header block exists once at turn start.
        """
        if not turn_id:
            return
        last = self.blocks[-1] if self.blocks else None
        if isinstance(last, dict) and last.get("type") == "turn.header" and last.get("turn_id") == turn_id:
            return
        # New turn: reset hide-driven cache suppression.
        self._suppress_prev_turn_cache = False
        self.blocks.append(self._block(
            type="turn.header",
            author="system",
            turn_id=turn_id,
            ts=ts or "",
            text="",
        ))

    def get_turn_blocks(self) -> List[Dict[str, Any]]:
        if self.current_turn_offset is None:
            return []
        return list(self.blocks[self.current_turn_offset :])

    def get_history_blocks(self) -> List[Dict[str, Any]]:
        if self.current_turn_offset is None:
            return list(self.blocks or [])
        return list(self.blocks[: self.current_turn_offset])

    def blocks_to_text(self, blocks: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for b in blocks or []:
            if not isinstance(b, dict):
                continue
            text = b.get("text")
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
            elif b.get("base64"):
                mime = (b.get("mime") or "").strip()
                lines.append(f"[binary block]{' ' + mime if mime else ''}")
        return "\n".join(lines).strip()

    def build_embedding_presentation(self, blocks: List[Dict[str, Any]]) -> str:
        """
        Build a compact presentation for semantic indexing.
        Only include external artifacts (file/display) from react tool results.
        """
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

    def build_turn_view(
        self,
        *,
        turn_id: Optional[str] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        sources_pool: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Build a normalized turn view from blocks + sources pool.
        Defaults to this timeline's blocks/sources_pool.
        """
        return _build_turn_view(
            turn_id=turn_id or (self.runtime.turn_id or ""),
            blocks=blocks if blocks is not None else list(self.blocks or []),
            sources_pool=sources_pool if sources_pool is not None else list(self.sources_pool or []),
            render_thinking=getattr(self.runtime, "render_thinking", True),
        )

    def _block(
        self,
        *,
        type: str,
        author: str,
        turn_id: str,
        ts: Optional[str],
        mime: str = "text/markdown",
        text: Optional[str] = None,
        base64: Optional[str] = None,
        path: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        blk: Dict[str, Any] = {
            "type": type,
            "author": author,
            "turn_id": turn_id,
            "ts": ts or "",
            "mime": mime,
        }
        if path:
            blk["path"] = path
        if text is not None:
            blk["text"] = text
        if base64 is not None:
            blk["base64"] = base64
        meta_out = dict(meta or {})
        if turn_id:
            meta_out.setdefault("turn_id", turn_id)
        if meta_out:
            blk["meta"] = meta_out
        return blk

    def block(
        self,
        *,
        type: str,
        author: str,
        turn_id: str,
        ts: Optional[str],
        mime: str = "text/markdown",
        text: Optional[str] = None,
        base64: Optional[str] = None,
        path: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._block(
            type=type,
            author=author,
            turn_id=turn_id,
            ts=ts,
            mime=mime,
            text=text,
            base64=base64,
            path=path,
            meta=meta,
        )

    def _estimate_blocks_tokens(self, blocks: List[Dict[str, Any]]) -> int:
        total = 0
        for b in blocks or []:
            total += self._estimate_block_tokens(b)
        return total

    def _estimate_model_message_tokens(self, blocks: List[Dict[str, Any]]) -> int:
        total = 0
        for b in blocks or []:
            if not isinstance(b, dict):
                continue
            text = b.get("text")
            if isinstance(text, str) and text.strip():
                try:
                    total += token_count(text)
                except Exception:
                    total += max(1, int(len(text) / 4))
            base64_data = b.get("base64") or b.get("data")
            media_type = b.get("mime") or b.get("media_type")
            source = b.get("source") if isinstance(b.get("source"), dict) else {}
            if not base64_data and source.get("type") == "base64":
                base64_data = source.get("data")
            if not media_type and source.get("type") == "base64":
                media_type = source.get("media_type")
            total += self._estimate_base64_model_tokens(base64_data, media_type)
        return total

    def _estimate_block_tokens(self, block: Dict[str, Any]) -> int:
        if not isinstance(block, dict):
            return 0
        text = block.get("text")
        base64_data = block.get("base64") or block.get("data")
        media_type = block.get("mime") or block.get("media_type")
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        btype = (block.get("type") or "").strip()
        if block.get("hidden") or meta.get("hidden"):
            base64_data = None
            try:
                text = self._hidden_retrieval_stub(block)
            except Exception:
                replacement = block.get("replacement_text") or meta.get("replacement_text")
                if isinstance(replacement, str) and replacement.strip():
                    text = replacement
        mime_norm = str(media_type or "").strip().lower()
        tool_id = str(meta.get("tool_id") or "").strip()
        if (
            base64_data
            and btype == "react.tool.result"
            and mime_norm in MODALITY_DOC_MIME
            and tool_id != "react.read"
            and not bool(meta.get("attach_to_model") or meta.get("model_visible_binary"))
        ):
            base64_data = None
        total = 0
        if isinstance(text, str) and text.strip():
            try:
                total += token_count(text)
            except Exception:
                total += max(1, int(len(text) / 4))
        total += self._estimate_base64_model_tokens(base64_data, media_type)
        return total

    @staticmethod
    def _estimate_base64_model_tokens(base64_data: Any, media_type: Any) -> int:
        if not isinstance(base64_data, str) or not base64_data:
            return 0
        mime_norm = str(media_type or "").strip().lower()
        try:
            if mime_norm in MODALITY_IMAGE_MIME:
                return max(1, int(estimate_image_tokens_from_base64(base64_data)))
            if mime_norm in MODALITY_DOC_MIME:
                return max(1, int(estimate_pdf_tokens_from_base64(base64_data)))
        except Exception:
            pass
        return max(1, int(len(base64_data) / 4))

    def _is_compaction_summary_block(self, block: Dict[str, Any]) -> bool:
        if not isinstance(block, dict):
            return False
        return (block.get("type") or "") == "conv.range.summary"

    def _is_turn_start_block(self, block: Dict[str, Any]) -> bool:
        if not isinstance(block, dict):
            return False
        btype = (block.get("type") or "").strip()
        if btype in {"turn.header", "user.prompt"}:
            return True
        author = (block.get("author") or block.get("role") or "").strip().lower()
        return author == "user"

    def _is_cut_point_block(self, block: Dict[str, Any]) -> bool:
        if not isinstance(block, dict):
            return False
        btype = (block.get("type") or "").strip()
        if btype in {"react.tool.result", "conv.range.summary"}:
            return False
        if btype in {"user.prompt", "assistant.completion", "assistant.completion.attempt", "react.tool.call", "turn.header"}:
            return True
        author = (block.get("author") or block.get("role") or "").strip().lower()
        if author in {"user", "assistant"}:
            return True
        if author and author not in {"system", "tool"}:
            return True
        return False

    def _is_message_block(self, block: Dict[str, Any]) -> bool:
        if not isinstance(block, dict):
            return False
        btype = (block.get("type") or "").strip()
        if btype == "react.tool.result":
            return True
        return self._is_cut_point_block(block)

    def _working_summary_blocks_for_turns(
        self,
        *,
        blocks: List[Dict[str, Any]],
        turn_ids: List[str],
        exclude_blocks: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        turn_set = {str(t or "").strip() for t in (turn_ids or []) if str(t or "").strip()}
        if not turn_set:
            return []
        exclude_paths = {
            str(b.get("path") or "").strip()
            for b in (exclude_blocks or [])
            if isinstance(b, dict) and str(b.get("path") or "").strip()
        }
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for blk in blocks or []:
            if not isinstance(blk, dict):
                continue
            if (blk.get("type") or "").strip() != "conv.working.summary":
                continue
            turn_id = (blk.get("turn_id") or blk.get("turn") or "").strip()
            if turn_id not in turn_set:
                continue
            text = blk.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            path = (blk.get("path") or "").strip()
            if path and path in exclude_paths:
                continue
            key = path or f"{turn_id}:{len(out)}"
            if key in seen:
                continue
            seen.add(key)
            out.append(blk)
        return out

    def _find_last_summary_index(self, blocks: List[Dict[str, Any]]) -> int:
        for idx in range(len(blocks) - 1, -1, -1):
            if self._is_compaction_summary_block(blocks[idx]):
                return idx
        return -1

    def _find_turn_start_index(
        self,
        blocks: List[Dict[str, Any]],
        entry_index: int,
        start_index: int,
    ) -> int:
        if entry_index < start_index:
            return -1
        turn_id = ""
        entry = blocks[entry_index] if 0 <= entry_index < len(blocks) else None
        if isinstance(entry, dict):
            turn_id = (entry.get("turn_id") or entry.get("turn") or "").strip()
        found_turn = False
        for idx in range(entry_index, start_index - 1, -1):
            blk = blocks[idx]
            if not isinstance(blk, dict):
                continue
            blk_turn = (blk.get("turn_id") or blk.get("turn") or "").strip()
            if turn_id:
                if blk_turn != turn_id:
                    if found_turn:
                        break
                    continue
                found_turn = True
            if self._is_turn_start_block(blk):
                return idx
        return -1

    def _find_turn_extent_start_index(
        self,
        blocks: List[Dict[str, Any]],
        turn_start_index: int,
        start_index: int,
        turn_id: str,
    ) -> int:
        """
        Return the earliest contiguous block for ``turn_id`` immediately before
        ``turn_start_index``. This protects a current turn whose start was
        detected at the user block while a preceding turn.header has the same
        turn_id.
        """
        if turn_start_index < start_index:
            return turn_start_index
        tid = (turn_id or "").strip()
        if not tid:
            return turn_start_index
        first = turn_start_index
        for idx in range(turn_start_index - 1, start_index - 1, -1):
            blk = blocks[idx]
            if not isinstance(blk, dict):
                break
            blk_turn = (blk.get("turn_id") or blk.get("turn") or "").strip()
            if blk_turn != tid:
                break
            first = idx
        return first

    @staticmethod
    def _is_meaningful_compaction_history_block(block: Dict[str, Any]) -> bool:
        if not isinstance(block, dict):
            return False
        btype = (block.get("type") or "").strip()
        if btype in {"turn.header", "conv.range.summary"}:
            return False
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            return True
        if block.get("base64") or block.get("data"):
            return True
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        return bool(meta)

    def _payload_shape_hint(self, payload: Any, *, max_items: int = 8) -> str:
        if isinstance(payload, list):
            return f"list[{len(payload)}]"
        if not isinstance(payload, dict):
            return type(payload).__name__
        parts: List[str] = []
        for key, value in list(payload.items())[:max_items]:
            if isinstance(value, list):
                parts.append(f"{key}=list[{len(value)}]")
            elif isinstance(value, dict):
                parts.append(f"{key}=dict[{len(value)}]")
            else:
                parts.append(f"{key}={type(value).__name__}")
        if len(payload) > max_items:
                parts.append(f"... keys={len(payload)}")
        return ", ".join(parts)

    @staticmethod
    def _payload_scalar_shape(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return f"str[{len(value)}]"
        if isinstance(value, bytes):
            return f"bytes[{len(value)}]"
        return type(value).__name__

    def _payload_shape_tree(
        self,
        value: Any,
        *,
        depth: int = 0,
        max_depth: int = TOOL_RESULT_PREVIEW_SHAPE_DEPTH,
        max_items: int = TOOL_RESULT_PREVIEW_SHAPE_MAX_ITEMS,
    ) -> Any:
        if depth >= max_depth:
            return self._payload_scalar_shape(value)

        if isinstance(value, dict):
            fields: Dict[str, Any] = {}
            for key, child in list(value.items())[:max_items]:
                fields[str(key)] = self._payload_shape_tree(
                    child,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                )
            if len(value) > max_items:
                fields["..."] = f"+{len(value) - max_items} keys"
            return {
                "type": f"dict[{len(value)}]",
                "fields": fields,
            }

        if isinstance(value, list):
            out: Dict[str, Any] = {"type": f"list[{len(value)}]"}
            if value:
                out["sample"] = self._payload_shape_tree(
                    value[0],
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                )
            if len(value) > 1:
                out["more_items"] = len(value) - 1
            return out

        return self._payload_scalar_shape(value)

    def _tool_result_preview_max_text_symbols(self) -> int:
        raw = getattr(self.runtime, "tool_result_preview_max_text_symbols", None)
        try:
            value = int(raw)
        except Exception:
            value = DEFAULT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS
        if value <= 0:
            value = DEFAULT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS
        return value

    def _tool_result_payload_text_for_prompt(
        self,
        *,
        raw_text: str,
        payload: Any,
        path: str,
        tool_id: str,
        preview_label: str = "[TOOL RESULT PREVIEW TRUNCATED]",
        recovery_lines: Optional[List[str]] = None,
        line_number_visible_text: bool = False,
        display_path: str = "",
        preformatted_preview: bool = False,
    ) -> str:
        text = raw_text if isinstance(raw_text, str) else str(raw_text or "")
        if (tool_id or "").strip() == "react.read":
            return text
        if (path or "").strip().startswith(f"{CONV_SO_PREFIX}sources_pool["):
            return text
        cap = self._tool_result_preview_max_text_symbols()
        is_structured_file_preview = (
            bool(preformatted_preview)
            or text.startswith("[TEXT FILE PREVIEW]")
        )
        if len(text) <= cap:
            if line_number_visible_text:
                if is_structured_file_preview:
                    return text
                total_lines = len(text.splitlines())
                line_numbers_mode = normalize_line_numbers_mode(
                    getattr(self.runtime, "line_numbers_mode", LINE_NUMBERS_LINES),
                    default=LINE_NUMBERS_LINES,
                )
                line_window = visible_line_window(
                    text,
                    source_truncated=False,
                    total_line_count=total_lines,
                )
                numbered_text = (
                    line_number_text(text, line_numbers=line_numbers_mode)
                    if total_lines and line_numbers_mode != LINE_NUMBERS_DISABLED
                    else text
                )
                lines = [
                    f"lines: {format_visible_line_window(line_window)}",
                    f"line_numbers: {line_numbers_mode if total_lines else LINE_NUMBERS_DISABLED}",
                    "content:",
                    numbered_text,
                ]
                return "\n".join(lines).strip()
            return text

        preview = text[:cap].rstrip()
        line_numbers_mode = normalize_line_numbers_mode(
            getattr(self.runtime, "line_numbers_mode", LINE_NUMBERS_LINES),
            default=LINE_NUMBERS_LINES,
        )
        line_window = visible_line_window(
            preview,
            source_truncated=True,
            total_line_count=len(text.splitlines()),
        )
        numbered_preview = (
            line_number_text(preview, line_numbers=line_numbers_mode)
            if preview and not is_structured_file_preview and line_numbers_mode != LINE_NUMBERS_DISABLED
            else preview
        )
        tokens_estimate = max(1, len(text) // 4)
        try:
            byte_count = len(text.encode("utf-8"))
        except Exception:
            byte_count = len(text)

        shape_payload = payload
        if shape_payload is None:
            parsed = _maybe_parse_json(text)
            shape_payload = parsed if parsed is not None else text

        lines = [
            preview_label,
            f"full_text_chars: {len(text)}",
            f"full_text_bytes: {byte_count}",
            f"tokens_estimate: {tokens_estimate}",
            f"visible_preview_chars: {len(preview)}",
            f"preview_cap_text_symbols: {cap}",
            f"preview_lines: {format_visible_line_window(line_window)}",
            f"line_numbers: {line_numbers_mode if line_window.get('visible_lines') and not is_structured_file_preview else LINE_NUMBERS_DISABLED}",
            f"shape_depth: {TOOL_RESULT_PREVIEW_SHAPE_DEPTH}",
        ]
        if line_window.get("partial_line") is not None:
            lines.append(f"partial_line: {line_window.get('partial_line')}")
        if tool_id:
            lines.append(f"tool: {tool_id}")
        shown_path = (display_path or path or "").strip()
        if shown_path:
            path_label = "path" if split_physical_artifact_path(shown_path)[0] else "logical_path"
            lines.append(f"{path_label}: {shown_path}")
        lines.append("shape:")
        lines.append(json.dumps(self._payload_shape_tree(shape_payload), ensure_ascii=False, indent=2))
        lines.append("preview:")
        lines.append(numbered_preview if numbered_preview else preview)
        lines.append("...[truncated]")
        if recovery_lines is not None:
            if recovery_lines:
                lines.append("recovery:")
                lines.extend(recovery_lines)
        elif path:
            lines.append("recovery:")
            lines.append(f"- react.read(paths=[\"{path}\"]) returns a bounded visible preview.")
            lines.append(f"- For large text, use react.read stats_only and then ranged react.read items against \"{path}\".")
            lines.append("- Exec output is capped; use exec only to compute or create smaller derived artifacts.")
        else:
            lines.append("recovery:")
            lines.append("- exact result remains stored in the timeline block; no logical path was supplied.")
        return "\n".join(lines).strip()

    @staticmethod
    def _large_text_recovery_lines(*, path: str, physical_path: str = "") -> List[str]:
        lines: List[str] = []
        if path.startswith(REACT_FILE_REF_PREFIX):
            lines.append("- Use react.rg on the file to find relevant regions before editing.")
            lines.append("- Pass react.rg read_item ranges to react.read(items=[...]) for exact visible regions.")
            if physical_path:
                lines.append("- For exec, derive the physical OUT_DIR path from logical_path only when computation needs the file.")
        elif path.startswith(CONV_TC_PREFIX):
            lines.append("- Use react.read for another bounded visible preview.")
            lines.append("- For large text, use react.read stats_only and then ranged react.read items.")
        elif path:
            lines.append("- Use react.read on this logical_path with bounded ranges/previews when supported.")
        return lines

    @staticmethod
    def _format_sources_pool_selector_from_sids(sids: List[int]) -> str:
        vals = sorted({int(s) for s in (sids or []) if isinstance(s, int) and s > 0})
        if not vals:
            return ""
        ranges: List[tuple[int, int]] = []
        start = prev = vals[0]
        for sid in vals[1:]:
            if sid == prev + 1:
                prev = sid
                continue
            ranges.append((start, prev))
            start = prev = sid
        ranges.append((start, prev))
        parts = [str(a) if a == b else f"{a}-{b}" for a, b in ranges]
        return f"{CONV_SO_PREFIX}sources_pool[{', '.join(parts)}]"

    @staticmethod
    def _extract_fi_paths_from_payload(value: Any, *, limit: int = 20) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()

        def visit(obj: Any, depth: int = 0) -> None:
            if len(out) >= limit or depth > 6:
                return
            if isinstance(obj, dict):
                for key in ("artifact_path", "logical_path", "path"):
                    val = obj.get(key)
                    if isinstance(val, str) and val.startswith(REACT_FILE_REF_PREFIX) and val not in seen:
                        seen.add(val)
                        out.append(val)
                        if len(out) >= limit:
                            return
                for val in obj.values():
                    visit(val, depth + 1)
                    if len(out) >= limit:
                        return
            elif isinstance(obj, list):
                for item in obj:
                    visit(item, depth + 1)
                    if len(out) >= limit:
                        return

        visit(value)
        return out

    @staticmethod
    def _source_sids_from_selector(path: str) -> List[int]:
        raw = str(path or "").strip()
        if raw.startswith(CONV_SO_PREFIX):
            raw = raw[len(CONV_SO_PREFIX):]
        if not raw.startswith("sources_pool[") or not raw.endswith("]"):
            return []
        body = raw[len("sources_pool["):-1]
        out: List[int] = []
        for part in body.split(","):
            item = part.strip()
            if not item:
                continue
            if "-" in item:
                left, right = item.split("-", 1)
                try:
                    a = int(left.strip())
                    b = int(right.strip())
                except Exception:
                    continue
                if a <= b:
                    out.extend(range(a, b + 1))
                continue
            try:
                out.append(int(item))
            except Exception:
                continue
        return out

    def _source_sids_from_block(self, block: Dict[str, Any], payload: Any = None) -> List[int]:
        sids: List[int] = []
        seen: set[int] = set()

        def add_many(values: Any) -> None:
            for sid in extract_source_sids(values):
                if sid not in seen:
                    seen.add(sid)
                    sids.append(sid)

        path = (block.get("path") or "").strip()
        add_many(self._source_sids_from_selector(path))
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        add_many(meta.get("sources_used"))
        if isinstance(payload, dict):
            add_many(payload.get("sources_used"))
            add_many(payload.get("source_sids"))
            add_many(payload.get("sources"))
            result = payload.get("result")
            if isinstance(result, dict):
                add_many(result.get("sources_used"))
                add_many(result.get("source_sids"))
                add_many(result.get("sources"))
        elif isinstance(payload, list):
            add_many(payload)
        return sids

    def _current_turn_engineering_ref(self, block: Dict[str, Any], *, tokens: int) -> Dict[str, Any]:
        btype = (block.get("type") or "").strip() or "block"
        path = (block.get("path") or "").strip()
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        call_id = (block.get("call_id") or meta.get("tool_call_id") or meta.get("call_id") or "").strip()
        if not call_id:
            source_tool = str(meta.get("source_tool") or "").strip()
            if source_tool.startswith("tc_") or source_tool.startswith("tc-"):
                call_id = source_tool
        if not call_id and path:
            call_id = self._call_id_from_path_for_stub(path)
        tool_id = (block.get("tool_id") or meta.get("tool_id") or "").strip()
        text = block.get("text")
        payload = _maybe_parse_json(text or "") if isinstance(text, str) else None
        if isinstance(payload, dict):
            tool_id = tool_id or str(payload.get("tool_id") or "").strip()
            call_id = call_id or str(payload.get("tool_call_id") or "").strip()

        if path.startswith(REACT_FILE_REF_PREFIX):
            return {
                "kind": "file",
                "path": path,
                "call_id": call_id,
                "tool_id": tool_id,
                "mime": (block.get("mime") or block.get("media_type") or meta.get("mime") or "").strip(),
                "hint": self._one_line_preview(text, limit=220),
            }

        if path.startswith(CONV_SO_PREFIX):
            return {
                "kind": "sources",
                "path": path,
                "call_id": call_id,
                "tool_id": tool_id,
                "source_sids": self._source_sids_from_block(block, payload),
                "hint": self._one_line_preview(text, limit=260),
            }

        if btype == "react.tool.call":
            params = payload.get("params") if isinstance(payload, dict) else None
            params_text = _compact_hint(params, max_chars=1800)
            return {
                "kind": "tool_call",
                "path": path,
                "call_id": call_id,
                "tool_id": tool_id,
                "tokens_estimate": tokens,
                "params": params_text,
            }

        if btype == "react.tool.result":
            hint = self._tool_result_hint_for_stub(payload) if payload is not None else self._one_line_preview(text, limit=300)
            shape = self._payload_shape_hint(payload) if payload is not None else ""
            return {
                "kind": "tool_result",
                "path": path,
                "call_id": call_id,
                "tool_id": tool_id,
                "tokens_estimate": tokens,
                "shape": shape,
                "hint": hint,
                "files": self._extract_fi_paths_from_payload(payload),
                "source_sids": self._source_sids_from_block(block, payload),
            }

        if btype == "react.current_turn.compaction_checkpoint":
            return {
                "kind": "previous_mid_turn_compaction",
                "path": path,
                "text": text if isinstance(text, str) else "",
            }

        kind = btype.replace(".", "_")
        preview = self._one_line_preview(text, limit=300)
        return {
            "kind": kind,
            "path": path,
            "call_id": call_id,
            "tool_id": tool_id,
            "tokens_estimate": tokens,
            "hint": preview,
        }

    @staticmethod
    def _append_unique(values: List[Any], value: Any) -> None:
        if value in (None, ""):
            return
        if value not in values:
            values.append(value)

    def _format_mid_turn_engineering_ledger(self, refs: List[Dict[str, Any]]) -> List[str]:
        groups: List[Dict[str, Any]] = []
        by_call: Dict[str, Dict[str, Any]] = {}
        loose_files: List[Dict[str, Any]] = []
        loose_sids: List[int] = []

        def add_file(target: List[Dict[str, Any]], item: Any) -> None:
            if isinstance(item, str):
                entry = {"path": item}
            elif isinstance(item, dict):
                entry = dict(item)
            else:
                return
            path = str(entry.get("path") or "").strip()
            if not path:
                return
            for existing in target:
                if isinstance(existing, dict) and str(existing.get("path") or "").strip() == path:
                    for key, value in entry.items():
                        if value and not existing.get(key):
                            existing[key] = value
                    return
            target.append(entry)

        def group_for(call_id: str, tool_id: str = "") -> Dict[str, Any]:
            cid = str(call_id or "").strip()
            if not cid:
                cid = f"unknown_{len(groups) + 1}"
            group = by_call.get(cid)
            if group is None:
                group = {
                    "call_id": cid,
                    "tool_id": str(tool_id or "").strip(),
                    "call": None,
                    "result": None,
                    "files": [],
                    "source_sids": [],
                    "other": [],
                }
                by_call[cid] = group
                groups.append(group)
            elif tool_id and not group.get("tool_id"):
                group["tool_id"] = str(tool_id).strip()
            return group

        for ref in refs or []:
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or "").strip()
            call_id = str(ref.get("call_id") or "").strip()
            tool_id = str(ref.get("tool_id") or "").strip()
            if kind == "previous_mid_turn_compaction":
                continue
            if kind == "tool_call":
                group_for(call_id, tool_id)["call"] = ref
                continue
            if kind == "tool_result":
                group = group_for(call_id, tool_id)
                group["result"] = ref
                for path in ref.get("files") or []:
                    add_file(group["files"], path)
                for sid in ref.get("source_sids") or []:
                    self._append_unique(group["source_sids"], sid)
                continue
            if kind == "file":
                if call_id:
                    add_file(group_for(call_id, tool_id)["files"], ref)
                else:
                    add_file(loose_files, ref)
                continue
            if kind == "sources":
                sids = [int(s) for s in (ref.get("source_sids") or []) if isinstance(s, int)]
                if call_id:
                    group = group_for(call_id, tool_id)
                    for sid in sids:
                        self._append_unique(group["source_sids"], sid)
                    if not sids and ref.get("path"):
                        group.setdefault("source_paths", [])
                        self._append_unique(group["source_paths"], ref.get("path"))
                else:
                    for sid in sids:
                        self._append_unique(loose_sids, sid)
                continue
            if call_id:
                self._append_unique(group_for(call_id, tool_id)["other"], ref)

        lines: List[str] = []
        for group in groups:
            call = group.get("call") or {}
            result = group.get("result") or {}
            call_id = str(group.get("call_id") or "").strip()
            tool_id = str(group.get("tool_id") or call.get("tool_id") or result.get("tool_id") or "").strip()
            lines.append(f"- tool_call_id: {call_id}")
            if tool_id:
                lines.append(f"  tool: {tool_id}")
            call_path = str(call.get("path") or "").strip()
            if call_path:
                lines.append(f"  call: {call_path}")
            if call.get("params"):
                lines.append(f"  params: {json.dumps(call.get('params'), ensure_ascii=False)}")
            result_path = str(result.get("path") or "").strip()
            if result_path:
                lines.append(f"  result: {result_path}")
            if result.get("tokens_estimate"):
                lines.append(f"  result_tokens_estimate: {result.get('tokens_estimate')}")
            if result.get("shape"):
                lines.append(f"  result_shape: {json.dumps(result.get('shape'), ensure_ascii=False)}")
            if result.get("hint"):
                lines.append(f"  result_hint: {json.dumps(result.get('hint'), ensure_ascii=False)}")
            files = group.get("files") or []
            if files:
                lines.append("  files:")
                for item in files:
                    if isinstance(item, dict):
                        fpath = str(item.get("path") or "").strip()
                        if not fpath:
                            continue
                        suffix = ""
                        mime = str(item.get("mime") or "").strip()
                        if mime:
                            suffix = f" mime={mime}"
                        lines.append(f"  - {fpath}{suffix}")
                    elif isinstance(item, str) and item:
                        lines.append(f"  - {item}")
            selector = self._format_sources_pool_selector_from_sids(group.get("source_sids") or [])
            source_paths = group.get("source_paths") or []
            if selector or source_paths:
                lines.append("  sources:")
                if selector:
                    lines.append(f"  - {selector}")
                for source_path in source_paths:
                    lines.append(f"  - {source_path}")
            for other in group.get("other") or []:
                opath = str(other.get("path") or "").strip()
                okind = str(other.get("kind") or "block").strip()
                if opath:
                    lines.append(f"  {okind}: {opath}")

        if loose_files:
            lines.append("- files_without_tool_call:")
            for item in loose_files:
                fpath = str(item.get("path") or "").strip()
                if fpath:
                    lines.append(f"  - {fpath}")
        loose_selector = self._format_sources_pool_selector_from_sids(loose_sids)
        if loose_selector:
            lines.append("- sources_without_tool_call:")
            lines.append(f"  - {loose_selector}")
        return lines

    def _compact_current_turn_prefix_blocks_in_place(
        self,
        *,
        blocks: List[Dict[str, Any]],
        turn_id: str,
        start_index: int,
        end_index: int,
        min_user_tokens: int = 12000,
    ) -> Dict[str, Any]:
        """
        Compact only the rendered presentation of a current-turn prefix.

        The original block remains in ``blocks`` with its original logical path
        and text/base64 payload so react.read/fetch_ctx can still resolve it
        before the turn has been finalized into immutable history.
        """
        tid = (turn_id or "").strip()
        if not tid:
            return {"blocks_hidden": 0, "tokens_hidden": 0, "refs": [], "hidden_paths": []}
        hidden = 0
        tokens_hidden = 0
        refs: List[Dict[str, Any]] = []
        hidden_paths: List[str] = []
        upper = min(max(end_index, 0), len(blocks))
        lower = max(start_index, 0)
        progress_types = {
            "react.round.start",
            "react.thinking",
            "react.notes",
            "react.note",
            "react.notice",
            "react.decision.raw",
            "react.current_turn.compaction_checkpoint",
            "assistant.completion",
            "assistant.completion.attempt",
            "react.tool.call",
            "react.tool.result",
            "react.tool.code",
        }
        user_visible_types = {
            "turn.header",
            "user.prompt",
            "user.attachment",
            "user.attachment.meta",
            "user.attachment.text",
            "user.followup",
            "user.steer",
            "user.followup.preserved",
            "user.steer.preserved",
            "event.external",
            "event.external.preserved",
        }
        for idx in range(lower, upper):
            blk = blocks[idx]
            if not isinstance(blk, dict):
                continue
            blk_turn = (blk.get("turn_id") or blk.get("turn") or "").strip()
            if blk_turn != tid:
                continue
            btype = (blk.get("type") or "").strip()
            path = (blk.get("path") or "").strip()
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            already_current_compacted = bool(
                meta.get("current_turn_prefix_compacted")
                or meta.get("current_turn_compacted")
                or meta.get("kind") == "current_turn_compacted"
            )
            if (blk.get("hidden") or meta.get("hidden")) and not already_current_compacted:
                continue
            try:
                tokens = int(meta.get("original_tokens_estimate") or 0) or self._estimate_block_tokens(blk)
            except Exception:
                tokens = 0

            should_hide = already_current_compacted or btype in progress_types
            if not should_hide and path.startswith((REACT_FILE_REF_PREFIX, CONV_SO_PREFIX)):
                should_hide = True
            if not should_hide and btype in user_visible_types and tokens >= min_user_tokens:
                should_hide = True
            if not should_hide:
                continue
            tool_id = (blk.get("tool_id") or meta.get("tool_id") or "").strip()
            call_id = (blk.get("call_id") or meta.get("tool_call_id") or "").strip()
            if not tool_id and btype in {"react.tool.call", "react.tool.result", "react.tool.code"}:
                parsed = _maybe_parse_json(blk.get("text") or "") if isinstance(blk.get("text"), str) else None
                if isinstance(parsed, dict):
                    tool_id = (parsed.get("tool_id") or "").strip()
                    call_id = call_id or (parsed.get("tool_call_id") or "").strip()
            ref = self._current_turn_engineering_ref(blk, tokens=tokens)
            new_meta = dict(meta)
            new_meta["kind"] = "current_turn_compacted"
            new_meta["current_turn_compacted"] = True
            new_meta["current_turn_prefix_compacted"] = True
            new_meta["original_tokens_estimate"] = tokens
            if tool_id:
                new_meta["tool_id"] = tool_id
            if call_id:
                new_meta["tool_call_id"] = call_id
            blk["meta"] = new_meta
            blk["hidden"] = True
            try:
                blk["replacement_text"] = json.dumps(ref, ensure_ascii=False)
            except Exception:
                blk["replacement_text"] = self._hidden_retrieval_stub(blk, include_path=True)
            if path:
                hidden_paths.append(path)
            if ref and ref.get("kind") != "previous_mid_turn_compaction":
                refs.append(ref)
            hidden += 1
            tokens_hidden += tokens
        return {"blocks_hidden": hidden, "tokens_hidden": tokens_hidden, "refs": refs, "hidden_paths": hidden_paths}

    def _build_mid_turn_compaction_checkpoint_block(
        self,
        *,
        turn_id: str,
        semantic_summary: str,
        refs: List[Dict[str, Any]],
        hidden_paths: List[str],
        ts: str,
        sequence: int = 1,
    ) -> Optional[Dict[str, Any]]:
        tid = (turn_id or "").strip()
        rows = [r for r in refs or [] if isinstance(r, dict)]
        if not tid:
            return None
        marker_index = max(1, int(sequence or 1))
        lines = [
            f"[MID-TURN COMPACTION {marker_index}]",
            f"turn_id: {tid}",
            "position: current-turn prefix compacted here; newer timeline blocks below are normal",
            "use: continue from the timeline below; this is not prior conversation memory",
            "recovery: exact source blocks remain recoverable by logical path; use react.read(paths=[path]), stats_only, and ranged react.read items",
            "",
            "semantic_progress:",
        ]
        summary_text = str(semantic_summary or "").strip()
        if summary_text:
            lines.append(summary_text)
        else:
            lines.append("- Semantic summary was unavailable; use the engineering ledger and logical paths.")
        lines.extend(["", "engineering_ledger:"])
        paths: List[str] = [str(p or "").strip() for p in hidden_paths or [] if str(p or "").strip()]
        for ref in rows:
            path = str(ref.get("path") or "").strip()
            if path and path not in paths:
                paths.append(path)
            for file_path in ref.get("files") or []:
                file_path = str(file_path or "").strip()
                if file_path and file_path not in paths:
                    paths.append(file_path)
        ledger_lines = self._format_mid_turn_engineering_ledger(rows)
        if ledger_lines:
            lines.extend(ledger_lines)
        else:
            lines.append("- (no path-addressable blocks were compacted)")
        lines.append(f"[/MID-TURN COMPACTION {marker_index}]")
        return self._block(
            type="react.current_turn.compaction_checkpoint",
            author="react",
            turn_id=tid,
            ts=ts or "",
            text="\n".join(lines).strip(),
            path=f"{CONV_AR_PREFIX}{tid}.react.mid_turn.compaction.{marker_index}",
            meta={
                "current_turn_compaction_checkpoint": True,
                "marker_index": marker_index,
                "contains_paths": paths,
            },
        )

    def _find_recent_turn_start_index(
        self,
        blocks: List[Dict[str, Any]],
        start_index: int,
        end_index: int,
        keep_recent_turns: int,
    ) -> int:
        if keep_recent_turns <= 0:
            return end_index
        seen: set[str] = set()
        for idx in range(end_index - 1, start_index - 1, -1):
            blk = blocks[idx]
            if not isinstance(blk, dict):
                continue
            turn_id = (blk.get("turn_id") or blk.get("turn") or "").strip()
            if not turn_id:
                continue
            if turn_id in seen:
                continue
            seen.add(turn_id)
            if len(seen) >= keep_recent_turns:
                turn_start = self._find_turn_start_index(blocks, idx, start_index)
                return turn_start if turn_start != -1 else idx
        return start_index

    def _find_compaction_cut_point(
        self,
        blocks: List[Dict[str, Any]],
        start_index: int,
        end_index: int,
        keep_recent_tokens: int,
    ) -> tuple[int, int, bool]:
        cut_points: List[int] = []
        for idx in range(start_index, end_index):
            if self._is_cut_point_block(blocks[idx]):
                cut_points.append(idx)
        if not cut_points:
            return start_index, -1, False

        accumulated = 0
        cut_index = cut_points[0]
        for idx in range(end_index - 1, start_index - 1, -1):
            blk = blocks[idx]
            if not self._is_message_block(blk):
                continue
            accumulated += self._estimate_block_tokens(blk)
            if accumulated >= keep_recent_tokens:
                for cp in cut_points:
                    if cp >= idx:
                        cut_index = cp
                        break
                break

        while cut_index > start_index:
            prev = blocks[cut_index - 1]
            if self._is_compaction_summary_block(prev):
                break
            if self._is_message_block(prev):
                break
            cut_index -= 1

        cut_block = blocks[cut_index] if 0 <= cut_index < len(blocks) else None
        is_turn_start = self._is_turn_start_block(cut_block) if isinstance(cut_block, dict) else False
        turn_start_index = -1 if is_turn_start else self._find_turn_start_index(blocks, cut_index, start_index)
        is_split_turn = (not is_turn_start) and turn_start_index != -1
        return cut_index, turn_start_index, is_split_turn

    def _slice_after_compaction_summary(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not blocks:
            return []
        idx = self._find_last_summary_index(blocks)
        return list(blocks[idx:]) if idx >= 0 else list(blocks)

    def _restore_missing_turn_headers_for_render(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not blocks:
            return []
        out: List[Dict[str, Any]] = []
        seen_turn_headers: set[str] = set()
        last_turn_id = ""
        for blk in blocks:
            if not isinstance(blk, dict):
                out.append(blk)
                continue
            btype = (blk.get("type") or "").strip()
            turn_id = (blk.get("turn_id") or blk.get("turn") or "").strip()
            if btype == "conv.range.summary":
                out.append(blk)
                continue
            if btype == "turn.header":
                if turn_id:
                    seen_turn_headers.add(turn_id)
                    last_turn_id = turn_id
                out.append(blk)
                continue
            if (
                turn_id
                and turn_id != last_turn_id
                and turn_id not in seen_turn_headers
                and btype not in {"conv.range.summary"}
            ):
                out.append({
                    "type": "turn.header",
                    "author": "system",
                    "turn_id": turn_id,
                    "ts": (blk.get("ts") or "").strip(),
                    "text": f"[TURN {turn_id}]",
                    "meta": {"synthetic_after_compaction": True},
                })
                seen_turn_headers.add(turn_id)
                last_turn_id = turn_id
            elif turn_id:
                last_turn_id = turn_id
            out.append(blk)
        return out

    def _clone_preserved_external_event_block(
        self,
        *,
        block: Dict[str, Any],
        preserved_path: str,
        source_path: str,
    ) -> Dict[str, Any]:
        cloned = dict(block or {})
        meta = dict(cloned.get("meta") or {})
        meta.pop("replacement_text", None)
        meta["source_path"] = source_path
        meta["preserved_by_compaction"] = True
        btype = (cloned.get("type") or "").strip()
        if btype == "user.followup":
            cloned["type"] = "user.followup.preserved"
        elif btype == "user.steer":
            cloned["type"] = "user.steer.preserved"
        elif btype == "event.external":
            cloned["type"] = "event.external.preserved"
        cloned["path"] = preserved_path
        cloned["hidden"] = False
        cloned.pop("replacement_text", None)
        cloned["meta"] = meta
        return cloned

    def _build_compacted_external_event_blocks(
        self,
        *,
        blocks: List[Dict[str, Any]],
        turn_id: str,
    ) -> List[Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}
        for order, blk in enumerate(blocks or []):
            if not isinstance(blk, dict):
                continue
            btype = (blk.get("type") or "").strip()
            if btype not in {
                "user.followup",
                "user.followup.preserved",
                "user.steer",
                "user.steer.preserved",
                "event.external",
                "event.external.preserved",
            }:
                continue
            text = (blk.get("text") or "").strip() if isinstance(blk.get("text"), str) else ""
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            source_path = (meta.get("source_path") or blk.get("path") or "").strip()
            message_id = str(meta.get("message_id") or "").strip()
            key = message_id or source_path or f"{btype}:{text}"
            entries[key] = {
                "order": order,
                "block": blk,
                "source_path": source_path,
            }
        if not entries:
            return []
        ordered = sorted(entries.values(), key=lambda item: int(item.get("order") or 0))
        preserved: List[Dict[str, Any]] = []
        for idx, entry in enumerate(ordered, start=1):
            blk = entry.get("block") if isinstance(entry.get("block"), dict) else None
            if not blk:
                continue
            btype = (blk.get("type") or "").strip()
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            suffix = str(meta.get("event_kind") or "").strip().lower()
            if not suffix:
                suffix = "followup" if "followup" in btype else "steer" if "steer" in btype else "event"
            preserved_path = f"{CONV_AR_PREFIX}{turn_id}.external.{suffix}.preserved.{idx}" if turn_id else ""
            preserved.append(
                self._clone_preserved_external_event_block(
                    block=blk,
                    preserved_path=preserved_path,
                    source_path=str(entry.get("source_path") or "").strip(),
                )
            )
        return preserved

    def _build_compacted_round_blocks(
        self,
        *,
        blocks: List[Dict[str, Any]],
        turn_id: str,
        split_turn_id: str,
    ) -> List[Dict[str, Any]]:
        if not split_turn_id:
            return []

        rows_by_key: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        def _remember(row: Dict[str, Any]) -> None:
            call_id = str(row.get("tool_call_id") or "").strip()
            key = call_id or str(row.get("result_path") or row.get("call_path") or "").strip()
            if not key:
                return
            prior = rows_by_key.get(key)
            if prior:
                merged = dict(prior)
                for k, v in row.items():
                    if v not in (None, "", [], {}):
                        merged[k] = v
                rows_by_key[key] = merged
                return
            rows_by_key[key] = dict(row)
            order.append(key)

        call_blocks: Dict[str, Dict[str, Any]] = {}
        result_blocks: Dict[str, List[Dict[str, Any]]] = {}

        for blk in blocks or []:
            if not isinstance(blk, dict):
                continue
            btype = (blk.get("type") or "").strip()
            blk_turn_id = _block_turn_id(blk)
            if blk_turn_id and blk_turn_id != split_turn_id:
                continue

            if btype == "react.rounds.compacted":
                payload = _maybe_parse_json(blk.get("text") or "") if isinstance(blk.get("text"), str) else None
                rounds = payload.get("rounds") if isinstance(payload, dict) else None
                if isinstance(rounds, list):
                    for row in rounds:
                        if isinstance(row, dict):
                            _remember(row)
                continue

            call_id = _block_call_id(blk)
            if not call_id:
                continue
            if btype == "react.tool.call":
                call_blocks[call_id] = blk
            elif btype == "react.tool.result":
                result_blocks.setdefault(call_id, []).append(blk)

        call_ids: List[str] = []
        for blk in blocks or []:
            if not isinstance(blk, dict):
                continue
            blk_turn_id = _block_turn_id(blk)
            if blk_turn_id and blk_turn_id != split_turn_id:
                continue
            call_id = _block_call_id(blk)
            if call_id and call_id not in call_ids and (call_id in call_blocks or call_id in result_blocks):
                call_ids.append(call_id)

        for call_id in call_ids:
            call_blk = call_blocks.get(call_id)
            results = result_blocks.get(call_id) or []
            result_blk = next(
                (b for b in reversed(results) if str(b.get("path") or "").strip().startswith(CONV_TC_PREFIX)),
                None,
            ) or (results[-1] if results else None)
            tool_id = _tool_id_from_call_or_result(result_blk or {}, call_blk)
            call_path = (call_blk.get("path") or "").strip() if isinstance(call_blk, dict) else ""
            result_path = (result_blk.get("path") or "").strip() if isinstance(result_blk, dict) else ""
            if not call_path:
                call_path = f"{CONV_TC_PREFIX}{split_turn_id}.{call_id}.call"
            if not result_path:
                result_path = f"{CONV_TC_PREFIX}{split_turn_id}.{call_id}.result"

            payload = _tool_call_payload(call_blk or {})
            params = payload.get("params") if isinstance(payload, dict) else None
            status, hint = _tool_status_and_hint(result_blk)
            tokens = self._estimate_block_tokens(result_blk) if isinstance(result_blk, dict) else 0
            large_result = bool(tokens >= 12000)
            read_paths: List[str] = []
            if isinstance(result_blk, dict) and isinstance(result_blk.get("text"), str):
                parsed = _maybe_parse_json(result_blk.get("text") or "")
                if isinstance(parsed, dict):
                    if not status and parsed.get("paths") is not None:
                        status = "success"
                    if isinstance(parsed.get("paths"), list):
                        for row in parsed.get("paths") or []:
                            if not isinstance(row, dict):
                                continue
                            p = str(row.get("path") or "").strip()
                            if p:
                                tok = row.get("tokens")
                                row_status = str(row.get("status") or "").strip()
                                suffix = []
                                if tok:
                                    suffix.append(f"tokens={tok}")
                                if row_status:
                                    suffix.append(f"status={row_status}")
                                read_paths.append(p + (f" ({', '.join(suffix)})" if suffix else ""))
                    if parsed.get("error") and not status:
                        status = "error"
            row = {
                "tool_call_id": call_id,
                "tool_id": tool_id,
                "status": status or ("success" if result_blk is not None else "pending"),
                "call_path": call_path,
                "result_path": result_path,
                "params": _compact_hint(params, max_chars=220) if params is not None else "",
                "hint": hint,
                "result_tokens": tokens or None,
                "large_result": large_result or None,
                "read_paths": read_paths[:8],
            }
            if large_result and result_path:
                row["recover_with"] = f"react.read(paths=['{result_path}'], stats_only=true), then ranged react.read items if text is large"
            _remember(row)

        rows = [rows_by_key[key] for key in order if key in rows_by_key]
        rows_by_call_id = {
            str(row.get("tool_call_id") or "").strip(): row
            for row in rows
            if str(row.get("tool_call_id") or "").strip()
        }

        messages: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []
        turn_started_at = _first_block_ts(blocks)
        user_like_types = {
            "user.prompt": "USER MESSAGE",
            "user.followup": "FOLLOWUP DURING TURN",
            "user.followup.preserved": "FOLLOWUP DURING TURN",
            "user.steer": "STEER DURING TURN",
            "user.steer.preserved": "STEER DURING TURN",
            "event.external": "EXTERNAL EVENT",
            "event.external.preserved": "EXTERNAL EVENT",
        }
        for blk in blocks or []:
            if not isinstance(blk, dict):
                continue
            blk_turn_id = _block_turn_id(blk)
            if blk_turn_id and blk_turn_id != split_turn_id:
                continue
            btype = (blk.get("type") or "").strip()
            txt = blk.get("text")
            text = _compact_hint(txt, max_chars=700) if isinstance(txt, str) and txt.strip() else ""
            call_id = _block_call_id(blk)
            path = str(blk.get("path") or "").strip()
            ts = _block_ts(blk)

            if btype in user_like_types:
                messages.append({
                    "label": user_like_types[btype],
                    "path": path,
                    "ts": ts,
                    "text": text,
                })
                continue
            if btype == "turn.header":
                continue
            if btype == "react.thinking":
                # Current-turn compaction is a recovery summary, not the live
                # round trace. Keep concise actions/results there and render
                # thinking only from the original live blocks.
                continue
            if btype == "react.notes":
                events.append({"kind": "notes", "path": path, "ts": ts, "text": text, "tool_call_id": call_id})
                continue
            if btype in {"react.note", "react.note.preserved"}:
                events.append({"kind": "internal_note", "path": path, "ts": ts, "text": text})
                continue
            if btype == "react.tool.call":
                payload = _tool_call_payload(blk)
                tool_id = str(payload.get("tool_id") or blk.get("tool_id") or "").strip()
                params = payload.get("params") if isinstance(payload, dict) else None
                events.append({
                    "kind": "tool_call",
                    "tool_id": tool_id,
                    "tool_call_id": call_id,
                    "path": path or (f"{CONV_TC_PREFIX}{split_turn_id}.{call_id}.call" if call_id else ""),
                    "ts": ts,
                    "params": _compact_hint(params, max_chars=500) if params is not None else "",
                })
                continue
            if btype == "react.tool.result":
                row = rows_by_call_id.get(call_id) or {}
                tool_id = str(row.get("tool_id") or _tool_id_from_call_or_result(blk, call_blocks.get(call_id)) or "").strip()
                events.append({
                    "kind": "tool_result",
                    "tool_id": tool_id,
                    "tool_call_id": call_id,
                    "path": path or str(row.get("result_path") or "").strip(),
                    "ts": ts,
                    "status": row.get("status") or "",
                    "hint": row.get("hint") or _compact_hint(text, max_chars=260),
                    "result_tokens": row.get("result_tokens"),
                    "large_result": bool(row.get("large_result")),
                    "read_paths": row.get("read_paths") if isinstance(row.get("read_paths"), list) else [],
                    "recover_with": row.get("recover_with") or "",
                })
                continue
            if btype == "react.notice":
                events.append({"kind": "notice", "path": path, "ts": ts, "text": text, "tool_call_id": call_id})
                continue
            if btype == "react.tool.code":
                events.append({"kind": "code", "path": path, "ts": ts, "text": text, "tool_call_id": call_id})
                continue
            if btype == "assistant.completion":
                events.append({"kind": "assistant", "path": path, "ts": ts, "text": text})
                continue
            if btype == "assistant.completion.attempt":
                events.append({"kind": "assistant_attempt", "path": path, "ts": ts, "text": text})

        if not rows and not messages and not events:
            return []

        payload = {
            "turn_id": split_turn_id,
            "origin": "current turn prefix compacted before the turn completed",
            "rounds": rows[-30:],
            "messages": messages[-8:],
            "events": events[-80:],
        }
        if turn_started_at:
            payload["turn_started_at"] = turn_started_at
        return [
            self._block(
                type="react.rounds.compacted",
                author="system",
                turn_id=turn_id,
                ts=_last_block_ts(blocks),
                mime="application/json",
                text=json.dumps(payload, ensure_ascii=False, indent=2),
                path=f"{CONV_AR_PREFIX}{turn_id}.react.rounds.compacted" if turn_id else "",
                meta={
                    "preserved_by_compaction": True,
                    "source_turn_id": split_turn_id,
                    "artifact_kind": "react.rounds.compacted",
                },
            )
        ]

    @staticmethod
    def _one_line_preview(value: Any, *, limit: int = 160) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                value = str(value)
        text = " ".join(str(value).split())
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def _path_basename_for_stub(path: str) -> str:
        raw = (path or "").strip().rstrip("/")
        if not raw:
            return ""
        raw = raw.split(":", 1)[-1] if ":" in raw else raw
        return raw.rsplit("/", 1)[-1] if "/" in raw else raw

    def _tool_call_hint_for_stub(self, payload: Dict[str, Any]) -> str:
        params = payload.get("params")
        if not isinstance(params, dict):
            return self._one_line_preview(params, limit=160)

        bits: List[str] = []

        def add_value(label: str, value: Any, *, limit: int = 90) -> None:
            preview = self._one_line_preview(value, limit=limit)
            if preview:
                bits.append(f"{label}={json.dumps(preview, ensure_ascii=False)}")

        for key in ("account", "mailbox", "message_id", "task_id", "automation_id", "path", "filename"):
            if params.get(key):
                add_value(key, params.get(key), limit=80)

        for key in ("query", "search_query", "q", "title", "subject"):
            if params.get(key):
                add_value(key, params.get(key), limit=120)
                break

        for key in ("queries", "urls", "paths"):
            val = params.get(key)
            if isinstance(val, list) and val:
                shown = val[:2]
                suffix = f" (+{len(val) - 2})" if len(val) > 2 else ""
                add_value(key, f"{shown}{suffix}", limit=130)
                break

        if params.get("content") and not any(bit.startswith("content=") for bit in bits):
            add_value("content", params.get("content"), limit=120)

        if not bits:
            keys = [str(k) for k in params.keys()][:8]
            if keys:
                bits.append("params=" + ",".join(keys))

        return " ".join(bits[:5])

    def _tool_result_hint_for_stub(self, payload: Any) -> str:
        if isinstance(payload, list):
            return f"items={len(payload)}"
        if not isinstance(payload, dict):
            return self._one_line_preview(payload, limit=160)

        bits: List[str] = []

        def add_value(label: str, value: Any, *, limit: int = 100) -> None:
            preview = self._one_line_preview(value, limit=limit)
            if preview:
                bits.append(f"{label}={json.dumps(preview, ensure_ascii=False)}")

        err = payload.get("error")
        if err is not None:
            code, msg = _tool_error_code_message(payload)
            bits.append("status=error")
            add_value("error", f"{code} {msg}".strip(), limit=120)
        elif payload.get("code") and payload.get("message"):
            bits.append("status=error")
            add_value("error", f"{payload.get('code')} {payload.get('message')}", limit=120)
        elif "ok" in payload:
            bits.append(f"ok={str(bool(payload.get('ok'))).lower()}")
        elif payload.get("artifact_path"):
            bits.append("status=success")

        if payload.get("artifact_path"):
            add_value("artifact", payload.get("artifact_path"), limit=130)
        if payload.get("file_count") is not None:
            bits.append(f"file_count={payload.get('file_count')}")
        if payload.get("total_tokens") is not None:
            bits.append(f"tokens={payload.get('total_tokens')}")

        paths = payload.get("paths")
        if isinstance(paths, list):
            bits.append(f"paths={len(paths)}")
        visible = payload.get("exists_in_visible_context")
        if isinstance(visible, list) and visible:
            add_value("already_visible", visible[:3], limit=130)

        result = payload.get("result")
        if isinstance(result, dict):
            for key in ("message", "summary", "status", "new_count", "count", "file_count"):
                if result.get(key) is not None:
                    add_value(key, result.get(key), limit=100)
                    break
            messages = result.get("messages")
            if isinstance(messages, list):
                bits.append(f"messages={len(messages)}")
        elif result is not None:
            add_value("result", result, limit=120)

        if not bits:
            for key in ("message", "summary", "status"):
                if payload.get(key):
                    add_value(key, payload.get(key), limit=120)
                    break
        if not bits:
            keys = [str(k) for k in payload.keys()][:8]
            if keys:
                bits.append("keys=" + ",".join(keys))

        return " ".join(bits[:6])

    def _hidden_retrieval_stub(self, block: Dict[str, Any], *, include_path: bool = True) -> str:
        path = (block.get("path") or "").strip()
        btype = (block.get("type") or "").strip() or "block"
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        call_id = (block.get("call_id") or meta.get("tool_call_id") or "").strip()
        if not call_id and path:
            call_id = self._call_id_from_path_for_stub(path)
        tool_id = (block.get("tool_id") or meta.get("tool_id") or "").strip()
        payload = None
        if not tool_id and btype in {"react.tool.call", "react.tool.result"}:
            payload = _maybe_parse_json(block.get("text") or "") if isinstance(block.get("text"), str) else None
            if isinstance(payload, dict):
                tool_id = (payload.get("tool_id") or "").strip()
        if btype == "user.prompt":
            kind = "user"
        elif btype in {"assistant.completion", "assistant.completion.attempt"}:
            kind = "assistant"
        elif btype == "react.tool.call":
            kind = "tool_call"
        elif btype == "react.tool.result":
            kind = "tool_result"
        elif path.startswith(REACT_FILE_REF_PREFIX):
            kind = "file"
        elif path.startswith("sk:"):
            kind = "skill"
        elif path.startswith(CONV_SO_PREFIX):
            kind = "source"
        else:
            kind = btype.replace(".", "_")

        parts = [f"{kind}:"]
        if include_path and path:
            parts.append(f"path={path}")
        if tool_id:
            parts.append(f"tool={tool_id}")
        if call_id:
            parts.append(f"call_id={call_id}")
        mime = (block.get("mime") or block.get("media_type") or meta.get("mime") or "").strip()
        if include_path and mime and path.startswith(REACT_FILE_REF_PREFIX):
            parts.append(f"mime={mime}")
        if include_path and path.startswith(REACT_FILE_REF_PREFIX):
            filename = self._path_basename_for_stub(path)
            if filename:
                parts.append(f"file={json.dumps(filename, ensure_ascii=False)}")

        hint = ""
        if btype == "react.tool.call":
            if payload is None:
                payload = _maybe_parse_json(block.get("text") or "") if isinstance(block.get("text"), str) else None
            if isinstance(payload, dict):
                hint = self._tool_call_hint_for_stub(payload)
        elif btype == "react.tool.result":
            if payload is None:
                payload = _maybe_parse_json(block.get("text") or "") if isinstance(block.get("text"), str) else None
            if payload is not None:
                hint = self._tool_result_hint_for_stub(payload)
        elif btype in {"user.prompt", "assistant.completion", "assistant.completion.attempt"} or path.startswith(("sk:", CONV_SO_PREFIX, REACT_FILE_REF_PREFIX)):
            hint = self._one_line_preview(block.get("text"), limit=180)
        else:
            hint = self._one_line_preview(block.get("text"), limit=140)
        if hint:
            parts.append(f"hint={json.dumps(hint, ensure_ascii=False)}")
        return " ".join(parts)

    def _hidden_structured_replacement_text(self, block: Dict[str, Any], replacement_text: str) -> str:
        path = (block.get("path") or "").strip()
        btype = (block.get("type") or "").strip() or "block"
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        call_id = (block.get("call_id") or meta.get("tool_call_id") or "").strip()
        if not call_id and path:
            call_id = self._call_id_from_path_for_stub(path)
        tool_id = (block.get("tool_id") or meta.get("tool_id") or "").strip()
        if not tool_id and btype in {"react.tool.call", "react.tool.result"}:
            payload = _maybe_parse_json(block.get("text") or "") if isinstance(block.get("text"), str) else None
            if isinstance(payload, dict):
                tool_id = (payload.get("tool_id") or "").strip()
        if not tool_id and isinstance(replacement_text, str):
            replacement_payload = _maybe_parse_json(replacement_text)
            if isinstance(replacement_payload, dict):
                tool_id = (replacement_payload.get("tool_id") or "").strip()

        if btype == "react.tool.result":
            header = f"[TOOL RESULT {call_id or 'tc_????'}].pruned"
        elif btype == "react.tool.call":
            header = f"[TOOL CALL {call_id or 'tc_????'}].pruned"
        else:
            header = f"[PRUNED {btype.replace('.', '_') or 'block'}]"
        if tool_id:
            header += f" {tool_id}"

        lines = [header]
        if path:
            key = "result_hidden" if btype == "react.tool.result" else "path_hidden"
            lines.append(f"{key}: {path}")
        repl = str(replacement_text or "").strip()
        if repl:
            lines.append(repl)
        return "\n".join(lines).strip()

    @staticmethod
    def _call_id_from_path_for_stub(path: str) -> str:
        if not path:
            return ""
        try:
            if ".tool_calls." in path:
                tail = path.split(".tool_calls.", 1)[1]
                return tail.split(".", 1)[0]
            if path.startswith(CONV_TC_PREFIX):
                tail = path[len(CONV_TC_PREFIX):]
                parts = tail.split(".")
                if len(parts) >= 3:
                    return parts[1]
        except Exception:
            return ""
        return ""

    @staticmethod
    def _is_turn_finalize_stats_block(block: Dict[str, Any]) -> bool:
        if not isinstance(block, dict):
            return False
        btype = (block.get("type") or "").strip()
        if btype == "react.turn.finalize":
            return True
        text = block.get("text")
        if not isinstance(text, str):
            return False
        return "Turn completed with these stats" in text and "[BUDGET]" in text

    @staticmethod
    def _parse_json_block_text(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        text = block.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        try:
            payload = json.loads(text)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _extract_finalize_section_lines(text: str, section: str, *, keep_prefixes: Tuple[str, ...]) -> List[str]:
        if not isinstance(text, str) or not text:
            return []
        target = f"[{section}]"
        lines = text.splitlines()
        out: List[str] = []
        active = False
        for raw in lines:
            stripped = raw.strip()
            if stripped == target:
                active = True
                continue
            if active and stripped.startswith("[") and stripped.endswith("]"):
                break
            if not active or not stripped:
                continue
            normalized = stripped.lstrip("-• ").strip()
            if any(normalized.startswith(prefix) for prefix in keep_prefixes):
                out.append(normalized)
        return out

    def _build_turn_status_stub(self, *, turn_id: str, blocks: List[Dict[str, Any]]) -> str:
        turn_id = (turn_id or "").strip()
        state: Dict[str, Any] = {}
        exit_payload: Dict[str, Any] = {}
        publish_payload: Dict[str, Any] = {}
        finalize_text = ""

        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            if (block.get("turn_id") or "").strip() != turn_id:
                continue
            btype = (block.get("type") or "").strip()
            if btype == "react.state":
                payload = self._parse_json_block_text(block) or {}
                if payload:
                    state = payload
            elif btype == "react.exit":
                payload = self._parse_json_block_text(block) or {}
                if payload:
                    exit_payload = payload
            elif btype == "react.workspace.publish":
                payload = self._parse_json_block_text(block) or {}
                if payload:
                    publish_payload = payload
            elif self._is_turn_finalize_stats_block(block):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    finalize_text = text

        lines = ["[TURN STATUS]"]
        if turn_id:
            lines.append(f"turn_id: {turn_id}")

        iteration = state.get("iteration")
        max_iterations = state.get("max_iterations")
        try:
            round_used = int(iteration) + 1
            round_total = int(max_iterations)
            if round_total > 0:
                lines.append(f"rounds: {max(1, min(round_used, round_total))}/{round_total}")
        except Exception:
            pass

        reason = (
            state.get("exit_reason")
            or exit_payload.get("reason")
            or state.get("reason")
            or ""
        )
        if str(reason or "").strip():
            lines.append(f"exit_reason: {str(reason).strip()}")

        error = state.get("error") or exit_payload.get("error") or ""
        if str(error or "").strip() and str(error).strip().lower() not in {"none", "null"}:
            lines.append(f"error: {self._one_line_preview(error, limit=180)}")

        budget_lines = self._extract_finalize_section_lines(
            finalize_text,
            "BUDGET",
            keep_prefixes=("time_elapsed_in_turn", "iterations"),
        )
        for item in budget_lines:
            if item.startswith("time_elapsed_in_turn"):
                lines.append(item.replace("   ", ": ", 1) if ": " not in item else item)
                break

        plan_lines = self._extract_finalize_section_lines(
            finalize_text,
            "OPEN PLANS",
            keep_prefixes=("plans:", "plan_id=", "snapshot_ref=", "last_update_turn="),
        )
        if plan_lines:
            lines.append("plans:")
            for item in plan_lines[:5]:
                lines.append(f"  - {item}")

        workspace_lines = self._extract_finalize_section_lines(
            finalize_text,
            "WORKSPACE",
            keep_prefixes=(
                "implementation:",
                "current_turn_root:",
                "current_turn_publish:",
                "last_published_turn:",
                "publish_error:",
                "repo_mode:",
                "repo_status:",
                "checked_out_from:",
            ),
        )
        if publish_payload:
            status = str(publish_payload.get("status") or "").strip()
            impl = str(publish_payload.get("workspace_implementation") or "").strip()
            if impl and not any(item.startswith("implementation:") for item in workspace_lines):
                workspace_lines.insert(0, f"implementation: {impl}")
            if status and not any(item.startswith("current_turn_publish:") for item in workspace_lines):
                workspace_lines.append(f"current_turn_publish: {status}")
            msg = str(publish_payload.get("message") or publish_payload.get("error") or "").strip()
            if msg:
                workspace_lines.append(f"publish_error: {self._one_line_preview(msg, limit=160)}")
        if workspace_lines:
            lines.append("workspace:")
            for item in workspace_lines[:8]:
                lines.append(f"  - {item}")

        return "\n".join(lines).strip()

    def _apply_hidden_replacements(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not blocks:
            return []
        session_cfg = getattr(self.runtime, "session", None)
        summary_mode = str(getattr(session_cfg, "pruned_turn_summary_mode", "working_summary") or "working_summary").strip().lower()
        use_working_summaries = summary_mode in {"working_summary", "summary", "on", "true", "1"}
        working_summaries_by_turn: Dict[str, List[Dict[str, Any]]] = {}
        visible_working_summary_turns: set[str] = set()
        if use_working_summaries:
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                if (blk.get("type") or "").strip() != "conv.working.summary":
                    continue
                turn_id = (blk.get("turn_id") or "").strip()
                text = str(blk.get("text") or "").strip()
                if turn_id and text:
                    working_summaries_by_turn.setdefault(turn_id, []).append(blk)
                    meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
                    if not (blk.get("hidden") or meta.get("hidden")):
                        visible_working_summary_turns.add(turn_id)
        turn_status_blocks: Dict[str, List[Dict[str, Any]]] = {}
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            btype = (blk.get("type") or "").strip()
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            if not (blk.get("hidden") or meta.get("hidden")):
                continue
            if btype not in {"react.state", "react.exit", "react.workspace.publish", "react.turn.finalize"} and not self._is_turn_finalize_stats_block(blk):
                continue
            turn_id = (blk.get("turn_id") or "").strip()
            if turn_id:
                turn_status_blocks.setdefault(turn_id, []).append(blk)
        out: List[Dict[str, Any]] = []
        hidden_seen: set[str] = set()
        emitted_working_summary_paths: set[str] = set()
        emitted_turn_status: set[str] = set()
        explicit_current_turn_compacted_paths: set[str] = set()
        latest_mid_turn_checkpoint_by_turn: Dict[str, str] = {}

        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            blk_type = (blk.get("type") or "").strip()
            blk_turn = (blk.get("turn_id") or blk.get("turn") or "").strip()
            blk_path = (blk.get("path") or "").strip()
            if blk_type == "react.current_turn.compaction_checkpoint" and blk_turn and blk_path:
                latest_mid_turn_checkpoint_by_turn[blk_turn] = blk_path
            if blk_type not in {"react.current_turn.compacted_data", "react.current_turn.compaction_checkpoint"}:
                continue
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            for path in meta.get("contains_paths") or []:
                path_str = str(path or "").strip()
                if path_str:
                    explicit_current_turn_compacted_paths.add(path_str)
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            btype = (blk.get("type") or "").strip()
            turn_id = (blk.get("turn_id") or "").strip()
            blk_path = (blk.get("path") or "").strip()
            if btype == "conv.working.summary" and blk_path in emitted_working_summary_paths:
                continue
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            hidden_prune_scope = str(meta.get("hidden_prune_scope") or "").strip()
            is_cold_recent_hidden = hidden_prune_scope == "cold_recent"
            if (
                btype == "react.current_turn.compaction_checkpoint"
                and turn_id
                and blk_path
                and latest_mid_turn_checkpoint_by_turn.get(turn_id)
                and latest_mid_turn_checkpoint_by_turn.get(turn_id) != blk_path
            ):
                continue
            if not (blk.get("hidden") or meta.get("hidden")):
                if btype == "conv.working.summary" and blk_path:
                    emitted_working_summary_paths.add(blk_path)
                out.append(blk)
                continue
            path = (blk.get("path") or "").strip()
            is_turn_status_source = (
                turn_id
                and turn_id in turn_status_blocks
                and (
                    btype in {"react.state", "react.exit", "react.workspace.publish", "react.turn.finalize"}
                    or self._is_turn_finalize_stats_block(blk)
                )
            )
            if not path or path in hidden_seen:
                if is_turn_status_source and turn_id not in working_summaries_by_turn and turn_id not in emitted_turn_status:
                    status_text = self._build_turn_status_stub(turn_id=turn_id, blocks=turn_status_blocks.get(turn_id) or [])
                    if status_text:
                        out.append({
                            "type": "react.pruned.turn_status",
                            "author": "react",
                            "turn_id": turn_id,
                            "path": f"{CONV_AR_PREFIX}{turn_id}.react.turn.status",
                            "text": status_text,
                            "hidden": False,
                            "meta": {"pruned_turn_status": True},
                        })
                        emitted_turn_status.add(turn_id)
                continue
            if turn_id and turn_id in working_summaries_by_turn and not is_cold_recent_hidden:
                if turn_id in visible_working_summary_turns:
                    hidden_seen.add(path)
                    continue
                for summary_src in working_summaries_by_turn.get(turn_id) or []:
                    summary_path = str(summary_src.get("path") or "").strip()
                    if summary_path and summary_path in emitted_working_summary_paths:
                        continue
                    summary_blk = dict(summary_src)
                    summary_blk.pop("base64", None)
                    summary_blk.pop("replacement_text", None)
                    summary_meta = summary_blk.get("meta") if isinstance(summary_blk.get("meta"), dict) else {}
                    summary_meta = dict(summary_meta)
                    summary_meta["pruned_turn_summary"] = True
                    summary_blk["meta"] = summary_meta
                    summary_blk["hidden"] = False
                    out.append(summary_blk)
                    if summary_path:
                        emitted_working_summary_paths.add(summary_path)
                hidden_seen.add(path)
                continue
            if is_turn_status_source:
                if turn_id not in emitted_turn_status:
                    status_text = self._build_turn_status_stub(turn_id=turn_id, blocks=turn_status_blocks.get(turn_id) or [])
                    if status_text:
                        repl_blk = dict(blk)
                        repl_blk.pop("base64", None)
                        repl_blk["type"] = "react.pruned.turn_status"
                        repl_blk["path"] = f"{CONV_AR_PREFIX}{turn_id}.react.turn.status"
                        repl_blk["text"] = status_text
                        repl_blk["hidden"] = False
                        repl_blk["ts"] = ""
                        repl_meta = dict(meta)
                        repl_meta["pruned_turn_status"] = True
                        repl_blk["meta"] = repl_meta
                        out.append(repl_blk)
                        emitted_turn_status.add(turn_id)
                hidden_seen.add(path)
                continue
            repl = (blk.get("replacement_text") or meta.get("replacement_text") or "").strip()
            if not repl:
                continue
            if meta.get("kind") == "cache_ttl_pruned" and not is_cold_recent_hidden:
                hidden_seen.add(path)
                continue
            current_turn_compacted = bool(
                meta.get("current_turn_compacted")
                or meta.get("current_turn_compaction_checkpoint")
                or meta.get("kind") == "current_turn_compacted"
            )
            hidden_seen.add(path)
            if current_turn_compacted and path in explicit_current_turn_compacted_paths:
                continue
            repl_blk = dict(blk)
            repl_blk.pop("base64", None)
            repl_meta = dict(meta)
            if current_turn_compacted:
                repl_meta["current_turn_compacted_ref"] = True
            elif is_cold_recent_hidden:
                repl_meta["pruned_structured_replacement"] = True
            else:
                repl_meta["pruned_retrieval_stub"] = True
            repl_blk["meta"] = repl_meta
            if is_cold_recent_hidden:
                repl_blk["text"] = self._hidden_structured_replacement_text(blk, repl)
            else:
                repl_blk["text"] = self._hidden_retrieval_stub(blk, include_path=True)
            repl_blk["hidden"] = False
            if current_turn_compacted:
                repl_blk["type"] = "react.current_turn.compacted_ref"
            elif is_cold_recent_hidden and btype == "react.tool.result":
                repl_blk["type"] = "react.pruned.tool_result"
            else:
                repl_blk["type"] = "react.pruned.ref"
            repl_blk["ts"] = ""
            out.append(repl_blk)
        return out

    def apply_hidden_replacements(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._apply_hidden_replacements(blocks)


    def _collect_blocks(self) -> List[Dict[str, Any]]:
        return list(self.blocks or [])

    def _produce_dynamic_announce_blocks(
        self,
        *,
        timeline_blocks: Optional[List[Dict[str, Any]]] = None,
        render_blocks: Optional[List[Dict[str, Any]]] = None,
        **context: Any,
    ) -> List[Dict[str, Any]]:
        if not self._event_source_pipeline_enabled():
            return []
        source_blocks = self._clone_blocks_for_policy_view(timeline_blocks or self._collect_blocks())
        if not source_blocks:
            return []
        try:
            produced = produce_event_source_announce_blocks(
                event_sources=self._event_sources(),
                timeline_blocks=source_blocks,
                current_turn_id=str(getattr(self.runtime, "turn_id", "") or ""),
                sources_pool=list(self.sources_pool or []),
                render_blocks=self._clone_blocks_for_policy_view(render_blocks or []),
                **context,
            )
            return [dict(block) for block in (produced or []) if isinstance(block, dict)]
        except Exception:
            logger.debug("[react.event_source.announce_production_failed]", exc_info=True)
            return []

    def _append_tail_blocks(
        self,
        *,
        blocks: List[Dict[str, Any]],
        include_sources: bool,
        include_announce: bool,
        timeline_blocks: Optional[List[Dict[str, Any]]] = None,
        **context: Any,
    ) -> List[Dict[str, Any]]:
        if not include_sources and not include_announce:
            return blocks
        tail: List[Dict[str, Any]] = []
        if include_sources:
            try:
                sources_text = build_sources_pool_text(sources_pool=list(self.sources_pool or []))
            except Exception:
                sources_text = ""
            if sources_text:
                tail.append({"text": sources_text})
        if include_announce:
            tail.extend(self.announce_blocks or [])
            tail.extend(
                self._produce_dynamic_announce_blocks(
                    timeline_blocks=timeline_blocks,
                    render_blocks=blocks,
                    **context,
                )
            )
        return list(blocks or []) + tail


    def hide_paths(
        self,
        paths: List[str],
        replacement_text: str,
        *,
        hidden_prune_scope: str = "",
        hidden_reason: str = "",
    ) -> Dict[str, Any]:
        if not paths:
            return {"status": "not_found", "blocks_hidden": 0, "tokens_hidden": 0}
        path_set = {p.strip() for p in paths if isinstance(p, str) and p.strip()}
        if not path_set:
            return {"status": "not_found", "blocks_hidden": 0, "tokens_hidden": 0}
        replaced = 0
        tokens_hidden = 0
        replacement_assigned = False
        for blk in self._collect_blocks():
            if not isinstance(blk, dict):
                continue
            path = (blk.get("path") or "").strip()
            if not path or path not in path_set:
                continue
            blk["hidden"] = True
            if hidden_prune_scope or hidden_reason:
                meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
                meta = dict(meta)
                if hidden_prune_scope:
                    meta["hidden_prune_scope"] = hidden_prune_scope
                if hidden_reason:
                    meta["hidden_reason"] = hidden_reason
                blk["meta"] = meta
            original_text = blk.get("text") if isinstance(blk.get("text"), str) else ""
            replacement_for_block = replacement_text if not replacement_assigned else ""
            if not replacement_assigned:
                replacement_assigned = True
            try:
                original_tokens = token_count(original_text or "")
            except Exception:
                original_tokens = 0
            blk["replacement_text"] = replacement_for_block or ""
            try:
                replacement_tokens = token_count(replacement_for_block or "") if replacement_for_block else 0
            except Exception:
                replacement_tokens = 0
            delta = original_tokens - replacement_tokens
            tokens_hidden += delta
            if delta < 0:
                try:
                    grew_by = replacement_tokens - original_tokens
                    log = logger.warning if grew_by > max(64, original_tokens) else logger.debug
                    log(
                        "[timeline.hide_paths] replacement longer than original: path=%s tool_call_id=%s original_tokens=%s replacement_tokens=%s",
                        path,
                        (blk.get("meta") or {}).get("tool_call_id"),
                        original_tokens,
                        replacement_tokens,
                    )
                except Exception:
                    pass
            replaced += 1
        status = "ok" if replaced else "not_found"
        if replaced:
            self.update_timestamp()
            self._suppress_prev_turn_cache = True
        return {"status": status, "blocks_hidden": replaced, "tokens_hidden": tokens_hidden}

    def tail_tokens_from_path(self, path: str) -> Optional[int]:
        """
        Return the estimated token count from the target path to the end of the
        static timeline (post-compaction, pre-tail). Used to enforce editable tail
        windows for react.hide.
        """
        if not isinstance(path, str) or not path.strip():
            return None
        blocks = self._slice_after_compaction_summary(self._collect_blocks())
        target_idx = -1
        for idx in range(len(blocks) - 1, -1, -1):
            blk = blocks[idx]
            if not isinstance(blk, dict):
                continue
            if (blk.get("path") or "").strip() == path.strip():
                target_idx = idx
                break
        if target_idx == -1:
            return None
        return self._estimate_blocks_tokens(blocks[target_idx:])

    def tail_rounds_from_path(self, path: str) -> Optional[int]:
        """
        Return the number of tool-call rounds between the target path and the end of the
        static timeline (post-compaction, pre-tail). Includes the final completion round.
        Used to enforce editable tail windows for react.hide.
        """
        if not isinstance(path, str) or not path.strip():
            return None
        blocks = self._slice_after_compaction_summary(self._collect_blocks())
        return cache_tail_rounds_from_path(blocks, path)

    def unhide_paths(self, paths: List[str]) -> int:
        if not paths:
            return 0
        path_set = {p.strip() for p in paths if isinstance(p, str) and p.strip()}
        if not path_set:
            return 0
        changed = 0
        for blk in self._collect_blocks():
            if not isinstance(blk, dict):
                continue
            path = (blk.get("path") or "").strip()
            if not path or path not in path_set:
                continue
            if blk.pop("hidden", None) is not None:
                changed += 1
        return changed

    async def sanitize_context_blocks(
        self,
        *,
        system_text: str,
        blocks: List[Dict[str, Any]],
        max_tokens: int,
        keep_recent_turns: int = 6,
        force: bool = False,
        trigger_reason: Optional[str] = None,
        trigger_tokens_estimate: Optional[int] = None,
        trigger_visible_block_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not blocks:
            return []
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.plan import (
                build_compacted_plan_history_blocks,
                build_plan_carry_block,
                latest_current_plan_snapshot,
                latest_plan_snapshot_by_id,
            )
        except Exception:
            build_compacted_plan_history_blocks = None
            build_plan_carry_block = None
            latest_current_plan_snapshot = None
            latest_plan_snapshot_by_id = None
        sys_est = max(1, int(len(system_text or "") / 4))
        threshold_tokens = int(max_tokens)
        visible_estimate_blocks = self._slice_after_compaction_summary(blocks)
        before_visible_tokens = self._estimate_blocks_tokens(visible_estimate_blocks)
        before_visible_blocks = len(visible_estimate_blocks)
        fallback_trigger_estimate = sys_est + before_visible_tokens
        try:
            input_tokens_estimate = int(trigger_tokens_estimate) if trigger_tokens_estimate is not None else fallback_trigger_estimate
        except Exception:
            input_tokens_estimate = fallback_trigger_estimate
        prompt_tokens_estimate = input_tokens_estimate
        try:
            trigger_block_count = int(trigger_visible_block_count) if trigger_visible_block_count is not None else before_visible_blocks
        except Exception:
            trigger_block_count = before_visible_blocks

        async def _emit_before_compaction(payload: Dict[str, Any]) -> None:
            if not self.runtime.on_before_compaction:
                return
            try:
                await self.runtime.on_before_compaction(payload)
            except Exception:
                logger.debug("[context.compaction:hook_failed] phase=before", exc_info=True)

        async def _emit_after_compaction(payload: Dict[str, Any]) -> None:
            if not self.runtime.on_after_compaction:
                return
            try:
                await self.runtime.on_after_compaction(payload)
            except Exception:
                logger.debug("[context.compaction:hook_failed] phase=after", exc_info=True)

        if not force and input_tokens_estimate <= threshold_tokens:
            logger.info(
                "[context.compaction:skip] reason=within_budget force=%s input_est=%s visible_est=%s system_est=%s max_tokens=%s threshold=%s blocks=%s",
                force,
                input_tokens_estimate,
                before_visible_tokens,
                sys_est,
                max_tokens,
                threshold_tokens,
                len(blocks),
            )
            return blocks

        boundary_start = self._find_last_summary_index(blocks) + 1
        boundary_end = len(blocks)
        if boundary_start >= boundary_end:
            logger.warning(
                "[context.compaction:no_effect] reason=no_boundary_room boundary_start=%s boundary_end=%s blocks=%s force=%s input_est=%s visible_est=%s system_est=%s max_tokens=%s",
                boundary_start,
                boundary_end,
                len(blocks),
                force,
                input_tokens_estimate,
                before_visible_tokens,
                sys_est,
                max_tokens,
            )
            return blocks

        context_budget = max(1, int(max_tokens - sys_est))
        keep_recent_tokens = max(1, int(context_budget * 0.7))
        recent_start: Optional[int] = None
        if keep_recent_turns > 0:
            recent_start = self._find_recent_turn_start_index(
                blocks,
                boundary_start,
                boundary_end,
                keep_recent_turns,
            )
            recent_tokens = self._estimate_blocks_tokens(blocks[recent_start:boundary_end])
            if recent_tokens <= context_budget:
                keep_recent_tokens = max(keep_recent_tokens, recent_tokens)

        cut_index, turn_start_index, is_split_turn = self._find_compaction_cut_point(
            blocks,
            boundary_start,
            boundary_end,
            keep_recent_tokens,
        )
        if force and cut_index <= boundary_start and recent_start is not None and recent_start > boundary_start:
            cut_index = recent_start
            turn_start_index = -1
            is_split_turn = False
        if cut_index <= boundary_start:
            logger.warning(
                "[context.compaction:no_effect] reason=no_cut_point boundary_start=%s cut_index=%s recent_start=%s keep_recent_tokens=%s force=%s input_est=%s visible_est=%s system_est=%s max_tokens=%s",
                boundary_start,
                cut_index,
                recent_start,
                keep_recent_tokens,
                force,
                input_tokens_estimate,
                before_visible_tokens,
                sys_est,
                max_tokens,
            )
            return blocks

        if is_split_turn and turn_start_index < boundary_start:
            turn_start_index = boundary_start
        if is_split_turn and turn_start_index != -1:
            split_turn_id_candidate = (
                blocks[turn_start_index].get("turn_id")
                or blocks[turn_start_index].get("turn")
                or ""
            ).strip()
            current_turn_id = (self.runtime.turn_id or "").strip()
            if split_turn_id_candidate and current_turn_id and split_turn_id_candidate != current_turn_id:
                next_turn_start_index = -1
                for idx in range(turn_start_index + 1, boundary_end):
                    idx_turn_id = (
                        blocks[idx].get("turn_id")
                        or blocks[idx].get("turn")
                        or ""
                    ).strip()
                    if idx_turn_id != split_turn_id_candidate and self._is_turn_start_block(blocks[idx]):
                        next_turn_start_index = idx
                        break
                logger.info(
                    "[context.compaction:retry] reason=split_non_current_turn action=compact_full_non_current_turn "
                    "split_turn_id=%s current_turn_id=%s old_cut_index=%s turn_start_index=%s next_turn_start_index=%s",
                    split_turn_id_candidate,
                    current_turn_id,
                    cut_index,
                    turn_start_index,
                    next_turn_start_index,
                )
                if next_turn_start_index <= boundary_start:
                    logger.warning(
                        "[context.compaction:no_effect] reason=split_non_current_turn_no_next_boundary "
                        "split_turn_id=%s current_turn_id=%s boundary_start=%s cut_index=%s turn_start_index=%s next_turn_start_index=%s",
                        split_turn_id_candidate,
                        current_turn_id,
                        boundary_start,
                        cut_index,
                        turn_start_index,
                        next_turn_start_index,
                    )
                    return blocks
                cut_index = next_turn_start_index
                turn_start_index = -1
                is_split_turn = False
        split_turn_id = ""
        protected_turn_start_index = turn_start_index
        if is_split_turn and turn_start_index != -1:
            split_turn_id = (blocks[turn_start_index].get("turn_id") or blocks[turn_start_index].get("turn") or "").strip()
            protected_turn_start_index = self._find_turn_extent_start_index(
                blocks,
                turn_start_index,
                boundary_start,
                split_turn_id,
            )

        current_turn_id = (self.runtime.turn_id or "").strip()
        split_current_turn = bool(is_split_turn and split_turn_id and current_turn_id and split_turn_id == current_turn_id)
        compaction_kind = (
            "current_turn_prefix"
            if split_current_turn
            else ("history_with_split_turn" if is_split_turn else "history")
        )
        compaction_id = f"{current_turn_id or split_turn_id or 'turn'}:{int(time.time() * 1000)}"
        history_blocks_estimated = max(0, (turn_start_index if is_split_turn else cut_index) - boundary_start)

        logger.info(
            "[context.compaction:start] force=%s blocks=%s input_est=%s visible_est=%s system_est=%s max_tokens=%s boundary_start=%s cut_index=%s turn_start_index=%s split_turn=%s history_blocks_estimated=%s keep_recent_tokens=%s",
            force,
            len(blocks),
            input_tokens_estimate,
            before_visible_tokens,
            sys_est,
            max_tokens,
            boundary_start,
            cut_index,
            turn_start_index,
            is_split_turn,
            history_blocks_estimated,
            keep_recent_tokens,
        )
        await _emit_before_compaction({
            "compaction_id": compaction_id,
            "status": "started",
            "kind": compaction_kind,
            "force": bool(force),
            "trigger_reason": (trigger_reason or ("forced" if force else "token_budget")),
            "blocks": len(blocks),
            "before_tokens": before_visible_tokens,
            "input_tokens_estimate": input_tokens_estimate,
            "visible_tokens_estimate": before_visible_tokens,
            "system_tokens_estimate": sys_est,
            "prompt_tokens_estimate": prompt_tokens_estimate,
            "max_tokens": max_tokens,
            "threshold_tokens": threshold_tokens,
            "trigger_visible_blocks": trigger_block_count,
            "boundary_start": boundary_start,
            "cut_index": cut_index,
            "turn_start_index": turn_start_index,
            "split_turn": bool(is_split_turn),
            "split_turn_id": split_turn_id,
            "current_turn": bool(split_current_turn),
            "history_blocks_estimated": history_blocks_estimated,
            "keep_recent_tokens": keep_recent_tokens,
        })

        current_turn_compaction: Dict[str, Any] = {"blocks_hidden": 0, "tokens_hidden": 0, "refs": [], "hidden_paths": []}
        if split_current_turn:
            original_cut_index = cut_index
            prefix_summary = ""
            prefix_blocks_for_summary = copy.deepcopy(blocks[protected_turn_start_index:original_cut_index])
            prefix_blocks_for_summary = self._apply_event_source_compaction_projection(
                prefix_blocks_for_summary,
                timeline_blocks=blocks,
                compaction_kind=compaction_kind,
                compaction_id=compaction_id,
                compaction_slice="current_turn_prefix",
                split_turn_id=split_turn_id,
            )
            if prefix_blocks_for_summary:
                try:
                    prefix_working_summary_blocks = self._working_summary_blocks_for_turns(
                        blocks=blocks,
                        turn_ids=extract_turn_ids_from_blocks(prefix_blocks_for_summary),
                        exclude_blocks=prefix_blocks_for_summary,
                    )
                    from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import summarize_turn_prefix_progressive
                    prefix_summary = await summarize_turn_prefix_progressive(
                        svc=self.svc,
                        blocks=prefix_blocks_for_summary,
                        max_tokens=900,
                        working_summary_blocks=prefix_working_summary_blocks,
                    ) or ""
                except Exception:
                    logger.exception(
                        "[context.compaction:current_turn_prefix_summary_failed] split_turn_id=%s prefix_blocks=%s",
                        split_turn_id,
                        len(prefix_blocks_for_summary),
                    )
                    prefix_summary = ""
            current_turn_compaction = self._compact_current_turn_prefix_blocks_in_place(
                blocks=blocks,
                turn_id=split_turn_id,
                start_index=protected_turn_start_index,
                end_index=original_cut_index,
            )
            compacted_data_sequence = 1 + sum(
                1
                for blk in blocks
                if isinstance(blk, dict)
                and (blk.get("type") or "").strip() == "react.current_turn.compaction_checkpoint"
                and (blk.get("turn_id") or blk.get("turn") or "").strip() == split_turn_id
            )
            compacted_data_block = self._build_mid_turn_compaction_checkpoint_block(
                turn_id=split_turn_id,
                semantic_summary=prefix_summary,
                refs=current_turn_compaction.get("refs") or [],
                hidden_paths=current_turn_compaction.get("hidden_paths") or [],
                ts=_last_block_ts(blocks[protected_turn_start_index:original_cut_index]),
                sequence=compacted_data_sequence,
            )
            if compacted_data_block:
                blocks.insert(original_cut_index, compacted_data_block)
                boundary_end += 1
            # Current-turn data has not yet been finalized into immutable turn
            # history. The compaction boundary must therefore never move past
            # any current-turn block. We compact only prior history here; large
            # current-turn blocks stay in the timeline, are hidden only in the
            # rendered projection, and get one explicit retrieval block at the
            # original cut point.
            cut_index = protected_turn_start_index
            turn_start_index = protected_turn_start_index
            is_split_turn = False

        previous_summary: Optional[str] = None
        previous_summary_meta: Dict[str, Any] = {}
        for idx in range(boundary_start - 1, -1, -1):
            blk = blocks[idx]
            if not isinstance(blk, dict):
                continue
            if not self._is_compaction_summary_block(blk):
                continue
            text = blk.get("text")
            if isinstance(text, str) and text.strip():
                previous_summary = text.strip()
            if isinstance(blk.get("meta"), dict):
                previous_summary_meta = dict(blk.get("meta") or {})
            break

        history_end = turn_start_index if is_split_turn else cut_index
        raw_history_blocks = [
            blk
            for blk in blocks[boundary_start:history_end]
            if not self._is_compaction_summary_block(blk)
        ]
        history_has_meaningful_blocks = any(
            self._is_meaningful_compaction_history_block(blk)
            for blk in raw_history_blocks
        )
        history_blocks = raw_history_blocks if history_has_meaningful_blocks else []
        if history_blocks:
            history_blocks = self._apply_event_source_compaction_projection(
                history_blocks,
                timeline_blocks=blocks,
                compaction_kind=compaction_kind,
                compaction_id=compaction_id,
                compaction_slice="history",
                boundary_start=boundary_start,
                cut_index=cut_index,
            )

        if split_current_turn and not history_blocks:
            self.blocks = list(blocks)
            blocks_hidden = int(current_turn_compaction.get("blocks_hidden") or 0)
            if blocks_hidden > 0:
                self.update_timestamp()
            logger.info(
                "[context.compaction:current_turn_only] split_turn_id=%s current_blocks_preserved=true blocks_hidden=%s tokens_hidden=%s",
                split_turn_id,
                current_turn_compaction.get("blocks_hidden"),
                current_turn_compaction.get("tokens_hidden"),
            )
            after_visible_tokens = self._estimate_blocks_tokens(self._slice_after_compaction_summary(blocks))
            after_visible_blocks = len(self._slice_after_compaction_summary(blocks))
            tokens_hidden = int(current_turn_compaction.get("tokens_hidden") or 0)
            reduced_tokens = tokens_hidden or max(0, before_visible_tokens - after_visible_tokens)
            reduced_visible_blocks = max(0, before_visible_blocks - after_visible_blocks)
            current_turn_compacted = bool(reduced_tokens > 0)
            await _emit_after_compaction({
                "compaction_id": compaction_id,
                "status": "completed" if current_turn_compacted else "skipped",
                "kind": compaction_kind,
                "force": bool(force),
                "trigger_reason": (trigger_reason or ("forced" if force else "token_budget")),
                "before_tokens": before_visible_tokens,
                "after_tokens": after_visible_tokens,
                "compacted_tokens": reduced_tokens,
                "input_tokens_estimate": input_tokens_estimate,
                "visible_tokens_estimate": before_visible_tokens,
                "system_tokens_estimate": sys_est,
                "prompt_tokens_estimate": prompt_tokens_estimate,
                "max_tokens": max_tokens,
                "threshold_tokens": threshold_tokens,
                "trigger_visible_blocks": trigger_block_count,
                "before_visible_blocks": before_visible_blocks,
                "after_visible_blocks": after_visible_blocks,
                "compacted_visible_blocks": reduced_visible_blocks,
                **({"reason": "no_visible_token_reduction"} if not current_turn_compacted else {}),
                "blocks_hidden": blocks_hidden,
                "tokens_hidden": tokens_hidden,
                "split_turn": True,
                "split_turn_id": split_turn_id,
                "current_turn": True,
                "history_compacted": False,
            })
            return blocks

        history_working_summary_blocks = self._working_summary_blocks_for_turns(
            blocks=blocks,
            turn_ids=extract_turn_ids_from_blocks(history_blocks),
            exclude_blocks=history_blocks,
        )
        summary: Optional[str] = None
        if not history_blocks and previous_summary:
            summary = previous_summary
        elif history_blocks:
            from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import summarize_context_blocks_progressive
            summary = await summarize_context_blocks_progressive(
                svc=self.svc,
                blocks=history_blocks,
                max_tokens=800,
                previous_summary=previous_summary,
                working_summary_blocks=history_working_summary_blocks,
            )

        turn_prefix_blocks: List[Dict[str, Any]] = []
        if is_split_turn and turn_start_index != -1 and cut_index > turn_start_index:
            turn_prefix_blocks = blocks[turn_start_index:cut_index]

        prefix_summary: Optional[str] = None
        if turn_prefix_blocks:
            turn_prefix_blocks = self._apply_event_source_compaction_projection(
                turn_prefix_blocks,
                timeline_blocks=blocks,
                compaction_kind=compaction_kind,
                compaction_id=compaction_id,
                compaction_slice="split_turn_prefix",
                split_turn_id=split_turn_id,
            )
            prefix_working_summary_blocks = self._working_summary_blocks_for_turns(
                blocks=blocks,
                turn_ids=extract_turn_ids_from_blocks(turn_prefix_blocks),
                exclude_blocks=turn_prefix_blocks,
            )
            from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import summarize_turn_prefix_progressive
            prefix_summary = await summarize_turn_prefix_progressive(
                svc=self.svc,
                blocks=turn_prefix_blocks,
                max_tokens=400,
                working_summary_blocks=prefix_working_summary_blocks,
            )
            if not prefix_summary:
                if turn_start_index > boundary_start:
                    logger.warning(
                        "[context.compaction:retry] reason=empty_turn_prefix_summary action=compact_history_only "
                        "prefix_blocks=%s old_cut_index=%s new_cut_index=%s turn_start_index=%s",
                        len(turn_prefix_blocks),
                        cut_index,
                        turn_start_index,
                        turn_start_index,
                    )
                    cut_index = turn_start_index
                    is_split_turn = False
                    turn_prefix_blocks = []
                    prefix_summary = None
                else:
                    logger.warning(
                        "[context.compaction:no_effect] reason=empty_turn_prefix_summary_no_safe_history_cut "
                        "prefix_blocks=%s cut_index=%s turn_start_index=%s boundary_start=%s",
                        len(turn_prefix_blocks),
                        cut_index,
                        turn_start_index,
                        boundary_start,
                    )
                    await _emit_after_compaction({
                        "compaction_id": compaction_id,
                        "status": "skipped",
                        "kind": compaction_kind,
                        "force": bool(force),
                        "before_tokens": before_visible_tokens,
                        "after_tokens": before_visible_tokens,
                        "compacted_tokens": 0,
                        "input_tokens_estimate": input_tokens_estimate,
                        "visible_tokens_estimate": before_visible_tokens,
                        "system_tokens_estimate": sys_est,
                        "prompt_tokens_estimate": prompt_tokens_estimate,
                        "max_tokens": max_tokens,
                        "threshold_tokens": threshold_tokens,
                        "reason": "empty_turn_prefix_summary_no_safe_history_cut",
                        "split_turn": bool(is_split_turn),
                        "split_turn_id": split_turn_id,
                        "current_turn": bool(split_current_turn),
                    })
                    return blocks

        if summary is None and not history_blocks and not previous_summary:
            summary = "No prior history."

        if summary is None:
            logger.warning(
                "[context.compaction:no_effect] reason=empty_history_summary history_blocks=%s previous_summary=%s cut_index=%s",
                len(history_blocks),
                bool(previous_summary),
                cut_index,
            )
            await _emit_after_compaction({
                "compaction_id": compaction_id,
                "status": "skipped",
                "kind": compaction_kind,
                "force": bool(force),
                "trigger_reason": (trigger_reason or ("forced" if force else "token_budget")),
                "before_tokens": before_visible_tokens,
                "after_tokens": before_visible_tokens,
                "compacted_tokens": 0,
                "input_tokens_estimate": input_tokens_estimate,
                "visible_tokens_estimate": before_visible_tokens,
                "system_tokens_estimate": sys_est,
                "prompt_tokens_estimate": prompt_tokens_estimate,
                "max_tokens": max_tokens,
                "threshold_tokens": threshold_tokens,
                "reason": "empty_history_summary",
                "history_blocks": len(history_blocks),
                "split_turn": bool(is_split_turn),
                "split_turn_id": split_turn_id,
                "current_turn": bool(split_current_turn),
            })
            return blocks

        if prefix_summary:
            summary = f"{summary}\n\n---\n\n**Turn Context (split turn):**\n\n{prefix_summary}"

        compacted_blocks = blocks[boundary_start:cut_index]

        from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import build_compaction_digest
        digest = build_compaction_digest(compacted_blocks)
        previous_covered_turn_ids = previous_summary_meta.get("covered_turn_ids")
        covered_turn_ids = _merge_turn_ids(previous_covered_turn_ids, extract_turn_ids_from_blocks(compacted_blocks))
        compacted_range_start_ts = (
            str(previous_summary_meta.get("compacted_range_start_ts") or "").strip()
            or _first_block_ts(compacted_blocks)
        )
        compacted_range_end_ts = _last_block_ts(compacted_blocks) or str(
            previous_summary_meta.get("compacted_range_end_ts") or ""
        ).strip()
        conversation_first_message_ts = (
            str(previous_summary_meta.get("conversation_first_message_ts") or "").strip()
            or _first_user_message_ts(blocks)
        )
        split_turn_id = ""
        if is_split_turn and turn_start_index != -1:
            split_turn_id = (blocks[turn_start_index].get("turn_id") or blocks[turn_start_index].get("turn") or "").strip()

        summary_turn_id = ""
        if 0 <= cut_index < len(blocks):
            summary_turn_id = (blocks[cut_index].get("turn_id") or blocks[cut_index].get("turn") or "").strip()
        if not summary_turn_id:
            summary_turn_id = self.runtime.turn_id or ""

        meta: Dict[str, Any] = {
            "covered_turn_ids": covered_turn_ids,
            "compaction_digest": digest,
        }
        if compacted_range_start_ts:
            meta["compacted_range_start_ts"] = compacted_range_start_ts
        if compacted_range_end_ts:
            meta["compacted_range_end_ts"] = compacted_range_end_ts
        if conversation_first_message_ts:
            meta["conversation_first_message_ts"] = conversation_first_message_ts
        if is_split_turn and split_turn_id:
            meta["split_turn_id"] = split_turn_id

        internal_note_compaction = build_internal_note_compaction_result(
            blocks=compacted_blocks,
            turn_id=summary_turn_id or self.runtime.turn_id or "",
            summary_text=summary,
        )

        summary_block = self._block(
            type="conv.range.summary",
            author="system",
            turn_id=summary_turn_id,
            ts="",
            text=internal_note_compaction.summary_text,
            path=(f"{CONV_SU_PREFIX}{summary_turn_id}.conv.range.summary" if summary_turn_id else ""),
            meta=meta,
        )

        note_preserved_blocks = internal_note_compaction.preserved_blocks
        external_event_preserved_blocks = self._build_compacted_external_event_blocks(
            blocks=compacted_blocks,
            turn_id=summary_turn_id or self.runtime.turn_id or "",
        )
        round_preserved_blocks = self._build_compacted_round_blocks(
            blocks=compacted_blocks,
            turn_id=summary_turn_id or self.runtime.turn_id or "",
            split_turn_id=split_turn_id,
        )

        active_plan_before = None
        if latest_current_plan_snapshot is not None:
            try:
                active_plan_before = latest_current_plan_snapshot(blocks)
            except Exception:
                active_plan_before = None

        history_index_block = None
        history_preserved_blocks: List[Dict[str, Any]] = []
        if build_compacted_plan_history_blocks is not None:
            try:
                excluded_plan_ids = {active_plan_before.plan_id} if active_plan_before and active_plan_before.plan_id else set()
                history_index_block, history_preserved_blocks = build_compacted_plan_history_blocks(
                    blocks=compacted_blocks,
                    turn_id=summary_turn_id or self.runtime.turn_id or "",
                    ts=summary_block.get("ts") or "",
                    exclude_plan_ids=excluded_plan_ids,
                )
            except Exception:
                history_index_block = None
                history_preserved_blocks = []

        updated_blocks = list(blocks)
        inserted_blocks = 1
        updated_blocks.insert(cut_index, summary_block)
        insert_pos = cut_index + 1
        if note_preserved_blocks:
            for preserved_note in note_preserved_blocks:
                updated_blocks.insert(insert_pos, preserved_note)
                insert_pos += 1
            inserted_blocks += len(note_preserved_blocks)
        if external_event_preserved_blocks:
            for preserved_event in external_event_preserved_blocks:
                updated_blocks.insert(insert_pos, preserved_event)
                insert_pos += 1
            inserted_blocks += len(external_event_preserved_blocks)
        if round_preserved_blocks:
            for preserved_round in round_preserved_blocks:
                updated_blocks.insert(insert_pos, preserved_round)
                insert_pos += 1
            inserted_blocks += len(round_preserved_blocks)
        if history_index_block:
            updated_blocks.insert(insert_pos, history_index_block)
            insert_pos += 1
            inserted_blocks += 1
        if history_preserved_blocks:
            for preserved_block in history_preserved_blocks:
                updated_blocks.insert(insert_pos, preserved_block)
                insert_pos += 1
            inserted_blocks += len(history_preserved_blocks)
        if active_plan_before and build_plan_carry_block is not None:
            try:
                retained_snap = (
                    latest_plan_snapshot_by_id(updated_blocks[insert_pos:], active_plan_before.plan_id)
                    if latest_plan_snapshot_by_id is not None
                    else None
                )
            except Exception:
                retained_snap = None
            if not retained_snap or retained_snap.plan_id != active_plan_before.plan_id:
                carry_turn_id = summary_turn_id or self.runtime.turn_id or active_plan_before.last_turn_id or active_plan_before.origin_turn_id
                carry_ts = active_plan_before.last_ts or active_plan_before.created_ts or ""
                updated_blocks.insert(
                    insert_pos,
                    build_plan_carry_block(
                        snap=active_plan_before,
                        turn_id=carry_turn_id or "",
                        ts=carry_ts,
                    ),
                )
                inserted_blocks += 1
        after_visible_tokens = self._estimate_blocks_tokens(self._slice_after_compaction_summary(updated_blocks))
        after_visible_blocks = len(self._slice_after_compaction_summary(updated_blocks))
        compacted_tokens = max(0, before_visible_tokens - after_visible_tokens)
        compacted_visible_blocks = max(0, before_visible_blocks - after_visible_blocks)
        if compacted_tokens <= 0:
            logger.info(
                "[context.compaction:no_effect] reason=no_visible_token_reduction compacted_blocks=%s inserted_blocks=%s "
                "before_tokens=%s after_tokens=%s before_visible_blocks=%s after_visible_blocks=%s "
                "force=%s split_turn=%s split_turn_id=%s",
                len(compacted_blocks),
                inserted_blocks,
                before_visible_tokens,
                after_visible_tokens,
                before_visible_blocks,
                after_visible_blocks,
                force,
                bool(is_split_turn),
                split_turn_id,
            )
            await _emit_after_compaction({
                "compaction_id": compaction_id,
                "status": "skipped",
                "kind": compaction_kind,
                "force": bool(force),
                "before_tokens": before_visible_tokens,
                "after_tokens": after_visible_tokens,
                "compacted_tokens": 0,
                "input_tokens_estimate": input_tokens_estimate,
                "visible_tokens_estimate": before_visible_tokens,
                "system_tokens_estimate": sys_est,
                "prompt_tokens_estimate": prompt_tokens_estimate,
                "max_tokens": max_tokens,
                "threshold_tokens": threshold_tokens,
                "trigger_visible_blocks": trigger_block_count,
                "before_visible_blocks": before_visible_blocks,
                "after_visible_blocks": after_visible_blocks,
                "compacted_visible_blocks": compacted_visible_blocks,
                "reason": "no_visible_token_reduction",
                "compacted_blocks": len(compacted_blocks),
                "inserted_blocks": inserted_blocks,
                "covered_turns": len(covered_turn_ids),
                "covered_turn_ids": covered_turn_ids,
                "split_turn": bool(is_split_turn),
                "split_turn_id": split_turn_id,
                "current_turn": bool(split_current_turn),
                "summary_chars": len(summary or ""),
                "before_blocks": len(blocks),
                "after_blocks": len(updated_blocks),
                "compacted_range_start_ts": compacted_range_start_ts,
                "compacted_range_end_ts": compacted_range_end_ts,
                "conversation_first_message_ts": conversation_first_message_ts,
            })
            return blocks
        self.blocks = list(updated_blocks)
        self.update_timestamp()
        if self.current_turn_offset is not None and cut_index <= self.current_turn_offset:
            self.current_turn_offset += inserted_blocks

        if self.runtime.save_summary:
            try:
                await self.runtime.save_summary({
                    "summary": summary,
                    "covered_turn_ids": covered_turn_ids,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "blocks_count": len(compacted_blocks),
                    "split_turn": bool(is_split_turn),
                    "split_turn_id": split_turn_id,
                    "compacted_range_start_ts": compacted_range_start_ts,
                    "compacted_range_end_ts": compacted_range_end_ts,
                    "conversation_first_message_ts": conversation_first_message_ts,
                    "compaction_digest": digest,
                })
            except Exception:
                pass
        logger.info(
            "[context.compaction:applied] compacted_blocks=%s inserted_blocks=%s covered_turns=%s split_turn=%s split_turn_id=%s summary_chars=%s before_blocks=%s after_blocks=%s",
            len(compacted_blocks),
            inserted_blocks,
            len(covered_turn_ids),
            bool(is_split_turn),
            split_turn_id,
            len(summary or ""),
            len(blocks),
            len(updated_blocks),
        )
        await _emit_after_compaction({
            "compaction_id": compaction_id,
            "status": "completed",
            "kind": compaction_kind,
            "force": bool(force),
            "trigger_reason": (trigger_reason or ("forced" if force else "token_budget")),
            "before_tokens": before_visible_tokens,
            "after_tokens": after_visible_tokens,
            "compacted_tokens": compacted_tokens,
            "input_tokens_estimate": input_tokens_estimate,
            "visible_tokens_estimate": before_visible_tokens,
            "system_tokens_estimate": sys_est,
            "prompt_tokens_estimate": prompt_tokens_estimate,
            "max_tokens": max_tokens,
            "threshold_tokens": threshold_tokens,
            "trigger_visible_blocks": trigger_block_count,
            "before_visible_blocks": before_visible_blocks,
            "after_visible_blocks": after_visible_blocks,
            "compacted_visible_blocks": compacted_visible_blocks,
            "compacted_blocks": len(compacted_blocks),
            "inserted_blocks": inserted_blocks,
            "covered_turns": len(covered_turn_ids),
            "covered_turn_ids": covered_turn_ids,
            "split_turn": bool(is_split_turn),
            "split_turn_id": split_turn_id,
            "current_turn": bool(split_current_turn),
            "summary_chars": len(summary or ""),
            "before_blocks": len(blocks),
            "after_blocks": len(updated_blocks),
            "compacted_range_start_ts": compacted_range_start_ts,
            "compacted_range_end_ts": compacted_range_end_ts,
            "conversation_first_message_ts": conversation_first_message_ts,
        })
        return updated_blocks

    async def render(
        self,
        *,
        cache_last: bool = True,
        system_text: str = "",
        include_sources: bool = False,
        include_announce: bool = False,
        force_sanitize: bool = False,
        keep_recent_turns: int = 6,
        debug_log: Optional[Callable[[str], None]] = None,
        debug_print: bool = False,
        debug_cache_trace: bool = False,
    ) -> List[Dict[str, Any]]:
        if self._lock is None:
            return await self._render_locked(
                cache_last=cache_last,
                system_text=system_text,
                include_sources=include_sources,
                include_announce=include_announce,
                force_sanitize=force_sanitize,
                keep_recent_turns=keep_recent_turns,
                debug_log=debug_log,
                debug_print=debug_print,
                debug_cache_trace=debug_cache_trace,
            )
        async with self._lock:
            return await self._render_locked(
                cache_last=cache_last,
                system_text=system_text,
                include_sources=include_sources,
                include_announce=include_announce,
                force_sanitize=force_sanitize,
                keep_recent_turns=keep_recent_turns,
                debug_log=debug_log,
                debug_print=debug_print,
                debug_cache_trace=debug_cache_trace,
            )

    async def _render_locked(
        self,
        *,
        cache_last: bool = False,
        system_text: str = "",
        include_sources: bool = False,
        include_announce: bool = False,
        force_sanitize: bool = False,
        keep_recent_turns: int = 6,
        debug_log: Optional[Callable[[str], None]] = None,
        debug_print: bool = False,
        debug_cache_trace: bool = False,
    ) -> List[Dict[str, Any]]:
        self.apply_session_cache_ttl_pruning()
        blocks = self._collect_blocks()
        render_event_cursor = _last_rendered_external_event_cursor(
            self._restore_missing_turn_headers_for_render(
                self._slice_after_compaction_summary(blocks)
            )
        )
        if self.runtime.max_tokens:
            render_forces_sanitize = bool(force_sanitize)
            sanitize_trigger_reasons: List[str] = ["forced"] if force_sanitize else []
            rendered_tokens: Optional[int] = None
            prompt_rendered_tokens: Optional[int] = None
            trigger_visible_block_count: Optional[int] = None
            try:
                visible_probe = await self._prepare_visible_blocks_for_render_async(
                    blocks,
                    cache_last=cache_last,
                    include_sources=include_sources,
                    include_announce=include_announce,
                    provider_render=False,
                )
                visible_probe = self._append_tail_blocks(
                    blocks=visible_probe,
                    include_sources=include_sources,
                    include_announce=include_announce,
                    timeline_blocks=blocks,
                    cache_last=bool(cache_last),
                    render_probe=True,
                )
                msg_probe = self._blocks_to_message_blocks(visible_probe)
                sys_probe_tokens = max(1, int(len(system_text or "") / 4))
                rendered_tokens = self._estimate_model_message_tokens(msg_probe)
                prompt_rendered_tokens = sys_probe_tokens + rendered_tokens
                trigger_visible_block_count = len(msg_probe)
                rendered_limit = int(self.runtime.max_tokens or 0)
                if prompt_rendered_tokens > rendered_limit:
                    sanitize_trigger_reasons.append("render_token_limit")
                if sanitize_trigger_reasons:
                    render_forces_sanitize = True
                    try:
                        logging.getLogger("kdcube.react.cache").info(
                            "[compaction:render_probe] forcing sanitize visible_rendered_tokens=%s "
                            "prompt_rendered_tokens=%s system_tokens=%s limit=%s message_blocks=%s",
                            rendered_tokens,
                            prompt_rendered_tokens,
                            sys_probe_tokens,
                            rendered_limit,
                            trigger_visible_block_count,
                        )
                    except Exception:
                        pass
            except Exception:
                pass
            blocks = await self.sanitize_context_blocks(
                system_text=system_text or "",
                blocks=blocks,
                max_tokens=int(self.runtime.max_tokens or 28000),
                keep_recent_turns=keep_recent_turns,
                force=render_forces_sanitize,
                trigger_reason=", ".join(dict.fromkeys(sanitize_trigger_reasons)) if render_forces_sanitize else None,
                trigger_tokens_estimate=prompt_rendered_tokens,
                trigger_visible_block_count=trigger_visible_block_count,
            )
        visible_blocks = await self._prepare_visible_blocks_for_render_async(
            blocks,
            cache_last=cache_last,
            include_sources=include_sources,
            include_announce=include_announce,
        )
        self._apply_cache_markers(visible_blocks, cache_last=cache_last)
        if debug_cache_trace:
            self._emit_cache_trace(
                blocks=visible_blocks,
                debug_log=debug_log,
                debug_print=debug_print,
                label="pre_tail",
            )

        visible_blocks = self._append_tail_blocks(
            blocks=visible_blocks,
            include_sources=include_sources,
            include_announce=include_announce,
            timeline_blocks=blocks,
            cache_last=bool(cache_last),
        )
        msg_blocks = self._blocks_to_message_blocks(visible_blocks)
        self.last_rendered_event_cursor = _later_timeline_event_cursor(
            self.last_rendered_event_cursor,
            render_event_cursor,
        )
        if getattr(self.runtime, "debug_timeline", False):
            try:
                self._write_render_debug(
                    msg_blocks,
                    system_text=system_text,
                    include_sources=include_sources,
                    include_announce=include_announce,
                )
            except Exception:
                pass
        if debug_log is not None:
            try:
                debug_log(self._format_message_blocks_debug(msg_blocks))
            except Exception:
                pass
        if debug_print:
            try:
                print(self._format_message_blocks_debug(msg_blocks))
            except Exception:
                pass
        return msg_blocks

    def _write_render_debug(
        self,
        msg_blocks: List[Dict[str, Any]],
        *,
        system_text: str = "",
        include_sources: bool,
        include_announce: bool,
    ) -> None:
        root = self._render_debug_root()
        if root is None:
            return
        user_id = self._render_debug_name_part(
            (getattr(self.runtime, "user_id", "") or "user").strip() or "user",
            limit=64,
        )
        conversation_id = self._render_debug_name_part(
            (getattr(self.runtime, "conversation_id", "") or "conv").strip() or "conv",
            limit=64,
        )
        turn_id = self._render_debug_name_part((self.runtime.turn_id or "turn").strip() or "turn", limit=96)
        ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        unique = time.time_ns()
        flags = []
        flags.append("src" if include_sources else "nosrc")
        flags.append("ann" if include_announce else "noann")
        name = f"rendered-{user_id}-{conversation_id}-{turn_id}-{ts}-{unique}-{'-'.join(flags)}.txt"
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        text = self._format_message_blocks_disk(msg_blocks)
        sys_text = str(system_text or "")
        msg_tokens = self._estimate_model_message_tokens(msg_blocks)
        cache_markers = sum(1 for b in (msg_blocks or []) if isinstance(b, dict) and b.get("cache"))
        if sys_text:
            sys_tokens = token_count(sys_text)
            system_debug = "\n".join([
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "SYSTEM PROMPT (separate model input)",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"chars: {len(sys_text)}",
                f"tokens_estimate: {sys_tokens}",
                sys_text,
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "USER MESSAGE BLOCKS",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"model_visible_tokens_estimate: {msg_tokens}",
                f"message_blocks: {len(msg_blocks or [])}",
                f"cache_markers: {cache_markers}",
            ])
            text = system_debug + "\n" + text
        else:
            text = "\n".join([
                f"model_visible_tokens_estimate: {msg_tokens}",
                f"message_blocks: {len(msg_blocks or [])}",
                f"cache_markers: {cache_markers}",
                "",
                text,
            ]).rstrip()
        path.write_text(text, encoding="utf-8")
        self._prune_render_debug(root)

    def _render_debug_root(self) -> Optional[pathlib.Path]:
        raw = getattr(self.runtime, "debug_timeline_root", None) or os.environ.get("REACT_DEBUG_ROOT")
        if not raw:
            host_root = os.environ.get("HOST_REACT_DEBUG_PATH")
            host_text = str(host_root or "").strip()
            if host_text and pathlib.Path(host_text).expanduser().exists():
                raw = host_text
        text = str(raw).strip()
        if not text:
            return None
        return pathlib.Path(text).expanduser()

    def _render_debug_keep_files(self) -> int:
        raw = getattr(self.runtime, "debug_timeline_keep_files", None)
        if raw is None:
            raw = os.environ.get("REACT_DEBUG_KEEP_FILES")
        try:
            keep = int(raw)
        except Exception:
            keep = 100
        return keep if keep > 0 else 100

    @staticmethod
    def _render_debug_name_part(value: str, *, limit: int = 120) -> str:
        text = str(value or "").strip() or "value"
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text)
        return safe[: max(1, int(limit or 120))] or "value"

    @staticmethod
    def _message_block_base64_and_media(block: Dict[str, Any]) -> Tuple[Any, Any]:
        data = block.get("base64") or block.get("data")
        media = block.get("mime") or block.get("media_type")
        source = block.get("source") if isinstance(block.get("source"), dict) else {}
        if not data and source.get("type") == "base64":
            data = source.get("data")
        if not media and source.get("type") == "base64":
            media = source.get("media_type")
        return data, media

    def _prune_render_debug(self, root: pathlib.Path) -> None:
        keep = self._render_debug_keep_files()
        files = [p for p in root.glob("rendered-*.txt") if p.is_file()]
        if len(files) <= keep:
            return
        def _sort_key(path: pathlib.Path) -> Tuple[int, str]:
            try:
                return path.stat().st_mtime_ns, path.name
            except FileNotFoundError:
                return 0, path.name

        files.sort(key=_sort_key)
        for path in files[: max(0, len(files) - keep)]:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _format_message_blocks_disk(self, msg_blocks: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        cache_idx = 0
        for b in (msg_blocks or []):
            if not isinstance(b, dict):
                continue
            prefix = ""
            if b.get("cache"):
                cache_idx += 1
                prefix = f"=>[{cache_idx}] "
            btype = b.get("type") or "text"
            if btype == "text":
                lines.append(prefix + (b.get("text") or ""))
            elif btype in {"image", "document"}:
                data, media = self._message_block_base64_and_media(b)
                media = media or ("image/png" if btype == "image" else "application/pdf")
                data_len = len(data) if isinstance(data, str) else 0
                estimated_tokens = self._estimate_base64_model_tokens(data, media)
                estimate_tail = f" provider_tokens_estimate={estimated_tokens}" if estimated_tokens else ""
                lines.append(prefix + f"<{btype} media_type={media} b64_len={data_len}{estimate_tail}> ...BASE64")
            else:
                lines.append(prefix + f"<{btype}>")
        return "\n".join(lines).rstrip()

    def render_base(self, *, cache_last: bool = False) -> List[Dict[str, Any]]:
        source_blocks = self._collect_blocks()
        blocks = self._prepare_visible_blocks_for_render(
            source_blocks,
            cache_last=cache_last,
            include_sources=False,
            include_announce=False,
        )
        self._apply_cache_markers(blocks, cache_last=cache_last)
        return blocks

    def _prepare_visible_blocks_for_render(
        self,
        blocks: List[Dict[str, Any]],
        *,
        cache_last: bool,
        include_sources: bool,
        include_announce: bool,
    ) -> List[Dict[str, Any]]:
        visible_blocks = self._slice_after_compaction_summary(blocks)
        visible_blocks = self._restore_missing_turn_headers_for_render(visible_blocks)
        visible_blocks = self._apply_event_source_timeline_projection(
            visible_blocks,
            timeline_blocks=blocks,
            cache_last=bool(cache_last),
            include_sources=bool(include_sources),
            include_announce=bool(include_announce),
        )
        visible_blocks = self._apply_hidden_replacements(visible_blocks)
        visible_blocks = self._apply_render_directives_before_cache(visible_blocks)
        return visible_blocks

    async def _prepare_visible_blocks_for_render_async(
        self,
        blocks: List[Dict[str, Any]],
        *,
        cache_last: bool,
        include_sources: bool,
        include_announce: bool,
        provider_render: bool = True,
    ) -> List[Dict[str, Any]]:
        visible_blocks = self._slice_after_compaction_summary(blocks)
        visible_blocks = self._restore_missing_turn_headers_for_render(visible_blocks)
        visible_blocks = await self._apply_event_source_timeline_projection_async(
            visible_blocks,
            timeline_blocks=blocks,
            cache_last=bool(cache_last),
            include_sources=bool(include_sources),
            include_announce=bool(include_announce),
            provider_render=bool(provider_render),
        )
        visible_blocks = self._apply_hidden_replacements(visible_blocks)
        visible_blocks = self._apply_render_directives_before_cache(visible_blocks)
        return visible_blocks

    def apply_session_cache_ttl_pruning(self) -> Dict[str, Any]:
        session = getattr(self.runtime, "session", None)
        runtime_ttl: Optional[int] = getattr(session, "cache_ttl_seconds", None)
        if runtime_ttl is None:
            runtime_ttl = self.runtime.cache_ttl_seconds
        ttl_seconds: Optional[int]
        if self._cache_ttl_bootstrap and self.cache_last_ttl_seconds is not None:
            # First render after load: use the previously stored TTL for pruning.
            ttl_seconds = self.cache_last_ttl_seconds
        else:
            ttl_seconds = runtime_ttl if runtime_ttl is not None else self.cache_last_ttl_seconds
        self._cache_ttl_bootstrap = False
        buffer_seconds = getattr(session, "cache_ttl_prune_buffer_seconds", 0) if session is not None else 0
        if not ttl_seconds:
            try:
                self.cache_last_touch_at = int(time.time())
            except Exception:
                pass
            return {"status": "disabled"}
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.session import (
                apply_cache_ttl_pruning,
            )
            keep_recent_turns = getattr(session, "keep_recent_turns", 10) if session is not None else 10
            keep_recent_intact_turns = (
                getattr(session, "keep_recent_intact_turns", 2) if session is not None else 2
            )
            res = apply_cache_ttl_pruning(
                timeline=self,
                ttl_seconds=int(ttl_seconds or 0),
                buffer_seconds=int(buffer_seconds or 0),
                keep_recent_turns=int(keep_recent_turns or 0),
                keep_recent_intact_turns=int(keep_recent_intact_turns or 0),
            )
            if runtime_ttl is not None and runtime_ttl != ttl_seconds:
                # After the first render, sync TTL to the current runtime setting.
                self.cache_last_ttl_seconds = runtime_ttl
            return res
        except Exception:
            print(f"Error applying cache TTL pruning {traceback.format_exc()}")
            return {"status": "error"}


    def _apply_cache_markers(self, blocks: List[Dict[str, Any]], *, cache_last: bool) -> None:
        if not blocks:
            return
        for b in blocks:
            if isinstance(b, dict):
                b.pop("cache", None)
        cache_cfg = getattr(self.runtime, "cache", None)
        min_rounds = 2
        offset = 2
        if cache_cfg is not None:
            try:
                min_rounds = int(getattr(cache_cfg, "cache_point_min_rounds", 2) or 2)
            except Exception:
                min_rounds = 2
            try:
                offset = int(getattr(cache_cfg, "cache_point_offset_rounds", 2) or 2)
            except Exception:
                offset = 2
        # Cache points: previous-turn tail, pre-tail, tail (max 3).
        indices = cache_point_indices(
            blocks,
            current_turn_id=getattr(self.runtime, "turn_id", None),
            min_rounds=min_rounds,
            offset=offset,
            prefer_pre_tail_for_prev_turn=bool(self._suppress_prev_turn_cache),
        )
        for idx in indices:
            if 0 <= idx < len(blocks):
                blocks[idx]["cache"] = True
        if cache_last and blocks and not indices:
            blocks[-1]["cache"] = True

    def _apply_render_directives_before_cache(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Normalize model-visible blocks before choosing cache points. Otherwise a
        block that is skipped at render time can steal a cache marker, and a block
        rewritten at render time can be selected using a different pre-render text.
        """
        out: List[Dict[str, Any]] = []
        for blk in blocks or []:
            if not isinstance(blk, dict):
                continue
            directive = build_timeline_render_directive(block=blk)
            if directive.get("skip"):
                continue
            if isinstance(directive.get("text"), str):
                normalized = dict(blk)
                normalized["text"] = directive.get("text") or ""
                out.append(normalized)
                continue
            out.append(blk)
        return out

    def _block_trace_sig(self, blk: Dict[str, Any]) -> Dict[str, Any]:
        def _short_hash(val: str) -> str:
            return hashlib.sha1(val.encode("utf-8", errors="ignore")).hexdigest()[:12]

        text = blk.get("text")
        base64 = blk.get("base64") or blk.get("data")
        meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
        replacement = blk.get("replacement_text") or meta.get("replacement_text")

        text_hash = _short_hash(text) if isinstance(text, str) else ""
        base64_hash = _short_hash(base64) if isinstance(base64, str) else ""
        meta_hash = _short_hash(json.dumps(meta, sort_keys=True, default=str)) if meta else ""

        sig = {
            "type": blk.get("type") or "",
            "author": blk.get("author") or "",
            "turn_id": blk.get("turn_id") or "",
            "ts": (blk.get("ts") or ""),
            "mime": blk.get("mime") or blk.get("media_type") or "",
            "path": (blk.get("path") or ""),
            "hidden": bool(blk.get("hidden") or meta.get("hidden")),
            "replacement": bool(replacement),
            "text_len": len(text) if isinstance(text, str) else 0,
            "text_hash": text_hash,
            "base64_len": len(base64) if isinstance(base64, str) else 0,
            "base64_hash": base64_hash,
            "meta_hash": meta_hash,
            "cache": bool(blk.get("cache")),
        }
        sig["sig"] = _short_hash(json.dumps(sig, sort_keys=True, default=str))
        return sig

    def _build_cache_trace(self, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        sigs: List[Dict[str, Any]] = []
        sig_hashes: List[str] = []
        cache_idx: List[int] = []
        for idx, blk in enumerate(blocks or []):
            if not isinstance(blk, dict):
                continue
            sig = self._block_trace_sig(blk)
            sigs.append(sig)
            sig_hashes.append(sig["sig"])
            if blk.get("cache"):
                cache_idx.append(idx)

        prefix_digests: List[Dict[str, Any]] = []
        if cache_idx:
            for idx in cache_idx:
                prefix = "".join(sig_hashes[: idx + 1])
                digest = hashlib.sha1(prefix.encode("utf-8", errors="ignore")).hexdigest()[:12]
                prefix_digests.append({"idx": idx, "digest": digest})

        return {
            "count": len(sigs),
            "sigs": sigs,
            "sig_hashes": sig_hashes,
            "cache_idx": cache_idx,
            "prefix_digests": prefix_digests,
        }

    def _emit_cache_trace(
        self,
        *,
        blocks: List[Dict[str, Any]],
        debug_log: Optional[Callable[[str], None]] = None,
        debug_print: bool = False,
        label: str = "pre_tail",
    ) -> None:
        if not debug_log and not debug_print:
            return
        trace = self._build_cache_trace(blocks)
        lines: List[str] = []
        lines.append(
            f"[cache_trace:{label}] blocks={trace['count']} cache_idx={trace['cache_idx']} "
            f"prefix_digests={trace['prefix_digests']}"
        )

        prev = self._cache_trace_prev
        if prev:
            if prev.get("cache_idx") != trace.get("cache_idx"):
                lines.append(
                    f"[cache_trace:{label}] cache_idx_changed prev={prev.get('cache_idx')} "
                    f"now={trace.get('cache_idx')}"
                )
            if prev.get("sig_hashes") != trace.get("sig_hashes"):
                prev_hashes = prev.get("sig_hashes") or []
                curr_hashes = trace.get("sig_hashes") or []
                min_len = min(len(prev_hashes), len(curr_hashes))
                first_diff = None
                for i in range(min_len):
                    if prev_hashes[i] != curr_hashes[i]:
                        first_diff = i
                        break
                if first_diff is None and len(prev_hashes) != len(curr_hashes):
                    first_diff = min_len
                if first_diff is not None:
                    prev_sig = (prev.get("sigs") or [])[first_diff] if first_diff < len(prev_hashes) else {}
                    curr_sig = (trace.get("sigs") or [])[first_diff] if first_diff < len(curr_hashes) else {}
                    changed_keys = sorted(
                        {k for k in set(prev_sig.keys()) | set(curr_sig.keys()) if prev_sig.get(k) != curr_sig.get(k)}
                    )
                    prev_brief = {
                        "type": prev_sig.get("type"),
                        "path": prev_sig.get("path"),
                        "ts": prev_sig.get("ts"),
                    }
                    curr_brief = {
                        "type": curr_sig.get("type"),
                        "path": curr_sig.get("path"),
                        "ts": curr_sig.get("ts"),
                    }
                    lines.append(
                        f"[cache_trace:{label}] first_diff idx={first_diff} keys={changed_keys} "
                        f"prev={prev_brief} now={curr_brief}"
                    )

        self._cache_trace_prev = trace
        for line in lines:
            if debug_log:
                try:
                    debug_log(line)
                except Exception:
                    pass
            if debug_print:
                try:
                    print(line)
                except Exception:
                    pass

    def _blocks_to_message_blocks(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert internal timeline blocks to model message blocks
        (text/image/document) while preserving caching on the last emitted
        block per source block.
        """
        def _short_tc_id(raw: str) -> str:
            if not raw:
                return "tc_????"
            return raw

        def _call_id_from_path(p: str) -> str:
            if not p:
                return ""
            try:
                # conv:tc:turn_<id>.tool_calls.<call_id>.out.json
                if ".tool_calls." in p:
                    tail = p.split(".tool_calls.", 1)[1]
                    call_id = tail.split(".", 1)[0]
                    return call_id
                # conv:tc:turn_<id>.<call_id>.call|result
                if p.startswith(CONV_TC_PREFIX):
                    tail = p[len(CONV_TC_PREFIX):]
                    if tail.startswith("conv_"):
                        _, sep, rest = tail.partition(".")
                        if sep and rest:
                            tail = rest
                    parts = tail.split(".")
                    if len(parts) >= 3:
                        return parts[1]
            except Exception:
                pass
            return ""

        def _ts_line(val: str) -> str:
            return f"[ts: {val}]" if val else ""

        def _derive_file_context_path(*, turn_id: str, raw_path: str) -> str:
            path = (raw_path or "").strip()
            if not path:
                return ""
            if path.startswith(REACT_FILE_REF_PREFIX):
                return path
            tid, namespace, rel = split_physical_artifact_path(path)
            if tid and namespace == ARTIFACT_NAMESPACE_FILES and rel:
                return f"{REACT_FILE_REF_PREFIX}{tid}.files/{rel}"
            if "/files/" in path and ".files/" in path:
                # already logical-ish
                return f"{REACT_FILE_REF_PREFIX}{path}"
            if turn_id:
                return f"{REACT_FILE_REF_PREFIX}{turn_id}.files/{path.lstrip('/')}"
            return path

        def _sources_pool_range(rows: List[Dict[str, Any]]) -> str:
            sids = [int(r.get("sid") or 0) for r in rows if isinstance(r, dict) and r.get("sid") is not None]
            sids = [s for s in sids if s > 0]
            if not sids:
                return ""
            return f"{CONV_SO_PREFIX}sources_pool[{sids[0]}-{sids[-1]}]"

        out: List[Dict[str, Any]] = []
        current_round_id: Optional[str] = None
        current_round_accepts_external = False
        round_idx = 0
        call_id_to_tool_id: Dict[str, str] = {}
        call_id_to_iteration: Dict[str, int] = {}

        def _round_header(idx: int) -> str:
            return f"┌──────── ROUND {idx} ────────┐"

        def _round_footer() -> str:
            return "└────────────────────────┘"

        def _indent_text_block(val: str) -> str:
            if not val:
                return val
            return "\n".join(("  " + line) if line else "  " for line in val.splitlines())

        def _close_current_round() -> None:
            nonlocal current_round_id, current_round_accepts_external
            if not current_round_id:
                return
            out.append({"type": "text", "text": _round_footer()})
            current_round_id = None
            current_round_accepts_external = False

        def _open_round(round_id: str, *, idx: int) -> None:
            nonlocal current_round_id, current_round_accepts_external
            out.append({"type": "text", "text": _round_header(idx)})
            current_round_id = round_id
            current_round_accepts_external = True

        def _format_compacted_turns(value: Any) -> str:
            if not isinstance(value, list):
                return ""
            turn_ids = [str(t).strip() for t in value if str(t or "").strip()]
            if not turn_ids:
                return ""
            if len(turn_ids) <= 8:
                return ", ".join(turn_ids)
            return (
                f"{turn_ids[0]}, {turn_ids[1]}, ... "
                f"{turn_ids[-2]}, {turn_ids[-1]} (count={len(turn_ids)})"
            )

        def _append_base64_model_block(
            emitted: List[Dict[str, Any]],
            *,
            base64_data: Any,
            mime: Optional[str],
            path: str,
            source_style: bool = False,
            include_provider_payload: bool = True,
            omitted_reason: str = "",
        ) -> None:
            if not base64_data:
                return
            mime_norm = (mime or "").strip().lower()
            estimated_tokens = self._estimate_base64_model_tokens(base64_data, mime_norm)
            if not include_provider_payload:
                lines = [
                    "[BINARY FILE NOT ATTACHED DIRECTLY TO MODEL]",
                    f"media_type: {mime_norm or 'unknown'}",
                ]
                if path:
                    lines.append(f"path: {path}")
                if estimated_tokens:
                    lines.append(f"provider_tokens_estimate_if_attached: {estimated_tokens}")
                lines.append(
                    "reason: "
                    + (
                        omitted_reason
                        or "binary artifact is available by path; use react.read when exact content is needed"
                    )
                )
                emitted.append({"type": "text", "text": "\n".join(lines)})
                return
            if mime_norm in MODALITY_IMAGE_MIME:
                if source_style:
                    emitted.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime_norm, "data": base64_data},
                    })
                else:
                    emitted.append({"type": "image", "data": base64_data, "media_type": mime_norm})
                return
            if mime_norm in MODALITY_DOC_MIME:
                if source_style:
                    emitted.append({
                        "type": "document",
                        "source": {"type": "base64", "media_type": mime_norm, "data": base64_data},
                    })
                else:
                    emitted.append({"type": "document", "data": base64_data, "media_type": mime_norm})
                return

            lines = [
                "[BINARY FILE NOT ATTACHED DIRECTLY TO MODEL]",
                f"media_type: {mime_norm or 'unknown'}",
            ]
            if path:
                lines.append(f"path: {path}")
            if estimated_tokens:
                lines.append(f"provider_tokens_estimate_if_attached: {estimated_tokens}")
            lines.append("reason: direct model attachments support images and PDFs only")
            emitted.append({"type": "text", "text": "\n".join(lines)})

        def _coerce_iteration(value: Any) -> Optional[int]:
            try:
                iteration = int(value)
            except Exception:
                return None
            return iteration if iteration >= 0 else None

        def _block_call_id(blk: Dict[str, Any]) -> str:
            meta_local = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            rid = (blk.get("call_id") or meta_local.get("tool_call_id") or meta_local.get("call_id") or "").strip()
            if not rid:
                rid = _call_id_from_path((blk.get("path") or "").strip())
            return rid

        def _block_iteration(blk: Dict[str, Any]) -> Optional[int]:
            meta_local = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            iteration = _coerce_iteration(meta_local.get("iteration"))
            if iteration is not None:
                return iteration
            rid = _block_call_id(blk)
            if rid:
                return call_id_to_iteration.get(rid)
            return None

        def _extract_round_id(blk: Dict[str, Any]) -> Optional[str]:
            btype_local = (blk.get("type") or "")
            round_types = {
                "react.round.start",
                "react.thinking",
                "react.notes",
                "react.tool.call",
                "react.tool.result",
                "react.notice",
                "react.tool.code",
                "react.decision.raw",
                "assistant.completion.attempt",
            }
            if btype_local not in round_types:
                return None
            if btype_local == "react.notes":
                meta_local = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
                if (meta_local.get("action") or "").strip() != "call_tool":
                    return None
            iteration = _block_iteration(blk)
            if iteration is not None:
                return f"iteration:{iteration}"
            rid = _block_call_id(blk)
            if rid:
                return rid
            return None

        def _round_display_index(round_id: str) -> Optional[int]:
            if not round_id.startswith("iteration:"):
                return None
            iteration = _coerce_iteration(round_id.split(":", 1)[1])
            if iteration is None:
                return None
            return iteration + 1

        def _tool_call_payload_info(blk: Dict[str, Any]) -> Tuple[str, str]:
            payload = _maybe_parse_json(blk.get("text") or "") if (blk.get("mime") or "").strip() == "application/json" else None
            if not isinstance(payload, dict):
                return "", ""
            return (payload.get("tool_id") or "").strip(), (payload.get("tool_call_id") or "").strip()
            return None

        def _is_round_passthrough_block(blk: Dict[str, Any]) -> bool:
            btype_local = (blk.get("type") or "").strip()
            meta_local = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            if btype_local in {
                "user.followup",
                "user.steer",
                "user.followup.preserved",
                "user.steer.preserved",
                "event.external",
                "event.external.preserved",
            }:
                return True
            if btype_local in {"user.attachment.meta", "user.attachment", "user.attachment.text"}:
                event_type = str(meta_local.get("event_type") or "").strip().lower()
                return bool(meta_local.get("is_continuation")) or event_type in {"event.user.followup", "event.user.steer"}
            if btype_local == "react.thinking":
                return True
            return False

        def _is_round_terminal_block(blk: Dict[str, Any]) -> bool:
            return (blk.get("type") or "").strip() in {
                "react.notice",
                "react.tool.result",
                "assistant.completion.attempt",
            }

        def _batch_id_for_render(blk: Dict[str, Any]) -> str:
            meta_local = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            return str(meta_local.get("batch_id") or blk.get("batch_id") or "").strip()

        def _is_user_control_block_for_render(blk: Dict[str, Any]) -> bool:
            btype_local = str(blk.get("type") or "").strip()
            if btype_local in {
                "user.followup",
                "user.steer",
                "user.followup.preserved",
                "user.steer.preserved",
            }:
                return True
            meta_local = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            event_type = str(meta_local.get("event_type") or "").strip()
            return event_type in {"event.user.followup", "event.user.steer"}

        def _clone_block_for_render(blk: Dict[str, Any], *, meta_update: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            cloned = dict(blk)
            meta_local = dict(cloned.get("meta") if isinstance(cloned.get("meta"), dict) else {})
            if meta_update:
                meta_local.update(meta_update)
            cloned["meta"] = meta_local
            return cloned

        def _coalesce_followup_batches_for_render(input_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            rendered_blocks: List[Dict[str, Any]] = []
            idx = 0
            total = len(input_blocks or [])
            while idx < total:
                block = input_blocks[idx]
                batch_id = _batch_id_for_render(block)
                if not batch_id:
                    rendered_blocks.append(block)
                    idx += 1
                    continue
                group: List[Dict[str, Any]] = []
                while idx < total and _batch_id_for_render(input_blocks[idx]) == batch_id:
                    group.append(input_blocks[idx])
                    idx += 1
                control_blocks = [candidate for candidate in group if _is_user_control_block_for_render(candidate)]
                if len(group) <= 1 or not control_blocks:
                    rendered_blocks.extend(group)
                    continue

                first_control = control_blocks[0]
                control_kind = (
                    "steer"
                    if "steer" in str(first_control.get("type") or "").strip()
                    else "followup"
                )
                header = _clone_block_for_render(
                    first_control,
                    meta_update={
                        "render_batch_header_only": True,
                        "batch_id": batch_id,
                        "batch_event_count": len(group),
                    },
                )
                header["text"] = ""
                rendered_blocks.append(header)

                for candidate in group:
                    if _is_user_control_block_for_render(candidate):
                        continue
                    rendered_blocks.append(candidate)

                for candidate in control_blocks:
                    if not str(candidate.get("text") or "").strip():
                        continue
                    rendered_blocks.append(_clone_block_for_render(
                        candidate,
                        meta_update={
                            "render_batch_text_only": True,
                            "batch_control_kind": control_kind,
                            "batch_id": batch_id,
                        },
                    ))
            return rendered_blocks

        blocks_for_render = _coalesce_followup_batches_for_render([
            b for b in (blocks or []) if isinstance(b, dict)
        ])
        for b in (blocks or []):
            if not isinstance(b, dict):
                continue
            call_id = _block_call_id(b)
            iteration = _block_iteration(b)
            if call_id and iteration is not None:
                call_id_to_iteration.setdefault(call_id, iteration)
            if (b.get("type") or "") == "react.tool.call":
                _, payload_call_id = _tool_call_payload_info(b)
                if payload_call_id and iteration is not None:
                    call_id_to_iteration.setdefault(payload_call_id, iteration)

        committed_assistant_turns = {
            str(b.get("turn_id") or "").strip()
            for b in (blocks or [])
            if isinstance(b, dict)
            and (b.get("type") or "").strip() == "assistant.completion"
            and str(b.get("turn_id") or "").strip()
        }

        for b in (blocks or []):
            if not isinstance(b, dict):
                continue
            if (b.get("type") or "") != "react.tool.call":
                continue
            tool_id = ""
            tool_call_id = ""
            tool_id, tool_call_id = _tool_call_payload_info(b)
            if not tool_call_id:
                tool_call_id = _block_call_id(b)
            if tool_call_id and tool_id:
                call_id_to_tool_id[tool_call_id] = tool_id

        for b in blocks_for_render:
            if not isinstance(b, dict):
                continue
            cache = bool(b.get("cache"))
            text = b.get("text")
            btype = (b.get("type") or "")
            ts = _block_ts(b)
            path = (b.get("path") or "").strip()
            meta = b.get("meta") if isinstance(b.get("meta"), dict) else {}
            if btype == "assistant.completion.attempt" and str(b.get("turn_id") or "").strip() in committed_assistant_turns:
                continue
            extra_text_blocks: List[str] = []
            directive = build_timeline_render_directive(
                block=b,
                call_id_to_tool_id=call_id_to_tool_id,
            )
            if directive.get("skip"):
                continue
            if isinstance(directive.get("text"), str):
                text = directive.get("text")
            # Round boundaries (open/close)
            round_id = _extract_round_id(b)
            if round_id and round_id != current_round_id:
                _close_current_round()
                display_idx = _round_display_index(round_id)
                if display_idx is None:
                    round_idx += 1
                    display_idx = round_idx
                else:
                    round_idx = max(round_idx, display_idx)
                _open_round(round_id, idx=display_idx)
            elif current_round_id and not round_id and not (current_round_accepts_external and _is_round_passthrough_block(b)):
                _close_current_round()

            if meta.get("render_as") == "raw":
                base64 = b.get("base64") or b.get("data")
                mime = (b.get("mime") or b.get("media_type") or "").strip() or None
                emitted_raw: List[Dict[str, Any]] = []
                if isinstance(text, str) and text:
                    if current_round_id:
                        text = _indent_text_block(str(text))
                    emitted_raw.append({"type": "text", "text": str(text)})
                _append_base64_model_block(
                    emitted_raw,
                    base64_data=base64,
                    mime=mime,
                    path=path,
                    source_style=True,
                )
                out.extend(emitted_raw)
                continue

            if btype == "turn.header":
                # Close any open round at turn boundary.
                _close_current_round()
                round_idx = 0
                turn_id = b.get("turn_id") or ""
                started_at = ts
                text = build_turn_header_text(turn_id=turn_id, started_at=started_at)
            elif btype == "user.prompt":
                lines = []
                ts_line = _ts_line(ts)
                if ts_line:
                    lines.append(ts_line)
                lines.append("[USER MESSAGE]")
                if path:
                    lines.append(f"[path: {path}]")
                if text:
                    lines.append(text)
                text = "\n".join(lines).strip()
            elif btype in {
                "user.followup",
                "user.steer",
                "user.followup.preserved",
                "user.steer.preserved",
                "event.external",
                "event.external.preserved",
            }:
                lines = []
                ts_line = _ts_line(ts)
                render_batch_text_only = bool(isinstance(meta, dict) and meta.get("render_batch_text_only"))
                render_batch_header_only = bool(isinstance(meta, dict) and meta.get("render_batch_header_only"))
                if ts_line and not render_batch_text_only:
                    lines.append(ts_line)
                if render_batch_text_only:
                    if "steer" in btype:
                        lines.append("[STEER MESSAGE]")
                    elif "followup" in btype:
                        lines.append("[FOLLOWUP MESSAGE]")
                    else:
                        lines.append("[EXTERNAL EVENT MESSAGE]")
                elif "followup" in btype:
                    lines.append("[FOLLOWUP DURING TURN]")
                elif "steer" in btype:
                    lines.append("[STEER DURING TURN]")
                else:
                    lines.append("[EXTERNAL EVENT]")
                if path:
                    lines.append(f"[path: {path}]")
                if isinstance(meta, dict) and not render_batch_text_only:
                    target_turn = str(meta.get("target_turn_id") or "").strip()
                    if target_turn:
                        lines.append(f"[target_turn_id: {target_turn}]")
                if text and not render_batch_header_only:
                    lines.append(text)
                text = "\n".join(lines).strip()
            elif btype in {"assistant.completion", "assistant.completion.attempt"}:
                is_attempt = btype == "assistant.completion.attempt"
                lines = ["[ASSISTANT MESSAGE ATTEMPT]" if is_attempt else "[ASSISTANT MESSAGE]"]
                if ts:
                    lines.append(f"[ts: {ts}]")
                if path:
                    lines.append(f"[path: {path}]")
                sources_used = []
                if isinstance(meta, dict):
                    if is_attempt:
                        attempt_index = meta.get("completion_attempt_index")
                        if attempt_index is not None:
                            lines.append(f"[attempt: {attempt_index}]")
                        lines.append("[status: provisional; supersede if later tool work or user input changes the outcome]")
                    else:
                        completion_index = meta.get("completion_index")
                        completion_count = meta.get("completion_count")
                        if completion_index is not None and completion_count is not None:
                            lines.append(f"[completion: {completion_index}/{completion_count}]")
                    try:
                        sources_used = citations_module.extract_source_sids(meta.get("sources_used"))
                    except Exception:
                        sources_used = []
                if sources_used:
                    lines.append(f"[sources_used: {sources_used}]")
                if text:
                    lines.append(text)
                text = "\n".join(lines).strip()
            elif btype == "assistant.completion.attempt.outcome":
                lines = ["[ASSISTANT MESSAGE ATTEMPT OUTCOME]"]
                if ts:
                    lines.append(f"[ts: {ts}]")
                if path:
                    lines.append(f"[path: {path}]")
                if isinstance(meta, dict):
                    attempt_index = meta.get("completion_attempt_index")
                    if attempt_index is not None:
                        lines.append(f"[attempt: {attempt_index}]")
                    outcome = str(meta.get("completion_attempt_outcome") or "").strip()
                    if outcome:
                        lines.append(f"[outcome: {outcome}]")
                if text:
                    lines.append(text)
                text = "\n".join(lines).strip()
            elif btype == "conv.working.summary":
                lines = ["[WORKING SUMMARY]"]
                if path:
                    lines.append(f"[path: {path}]")
                if text:
                    lines.append(text)
                text = "\n".join(lines).strip()
            elif btype == "conv.range.summary":
                lines = ["[COMPACTED PRIOR CONVERSATION MEMORY]"]
                if path:
                    lines.append(f"[path: {path}]")
                covered_turns = meta.get("covered_turn_ids") if isinstance(meta, dict) else None
                covered_turns_text = _format_compacted_turns(covered_turns)
                if covered_turns_text:
                    lines.append("covered_turns: " + covered_turns_text)
                range_start = str(meta.get("compacted_range_start_ts") or "").strip() if isinstance(meta, dict) else ""
                range_end = str(meta.get("compacted_range_end_ts") or "").strip() if isinstance(meta, dict) else ""
                if range_start and range_end:
                    lines.append(f"compacted_time_range: {range_start} -> {range_end}")
                elif range_start:
                    lines.append(f"compacted_time_range_start: {range_start}")
                elif range_end:
                    lines.append(f"compacted_time_range_end: {range_end}")
                first_message_ts = str(meta.get("conversation_first_message_ts") or "").strip() if isinstance(meta, dict) else ""
                if first_message_ts:
                    lines.append(f"conversation_first_message_ts: {first_message_ts}")
                split_turn_id = (meta.get("split_turn_id") or "").strip() if isinstance(meta, dict) else ""
                if split_turn_id:
                    lines.append(f"split_turn_id: {split_turn_id}")
                lines.append("origin: model-generated compaction of older timeline blocks removed from the visible stream")
                lines.append("use: treat this as prior conversation state; newer visible turns below may supersede it")
                lines.append("recovery: use logical paths from the summary or react.memsearch/react.read when exact old content is needed")
                if text:
                    lines.append(str(text).strip())
                lines.append("[END COMPACTED PRIOR CONVERSATION MEMORY]")
                text = "\n".join(lines).strip()
            elif btype == "react.rounds.compacted":
                payload = _maybe_parse_json(text or "") if isinstance(text, str) else None
                rounds = payload.get("rounds") if isinstance(payload, dict) else None
                events = payload.get("events") if isinstance(payload, dict) else None
                messages = payload.get("messages") if isinstance(payload, dict) else None
                source_turn = str((payload or {}).get("turn_id") or meta.get("source_turn_id") or "").strip() if isinstance(payload, dict) else str(meta.get("source_turn_id") or "").strip()
                turn_started_at = str((payload or {}).get("turn_started_at") or "").strip() if isinstance(payload, dict) else ""
                lines = ["[COMPACTED CURRENT TURN PREFIX]"]
                if path:
                    lines.append(f"[path: {path}]")
                if source_turn:
                    lines.append(build_turn_header_text(turn_id=source_turn, started_at=turn_started_at))
                lines.append("[compacted current-turn prefix: continue from this timeline; do not assume the turn is blank]")

                if isinstance(messages, list):
                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue
                        msg_lines: List[str] = []
                        mts = str(msg.get("ts") or "").strip()
                        if mts:
                            msg_lines.append(f"[ts: {mts}]")
                        label = str(msg.get("label") or "USER MESSAGE").strip()
                        msg_lines.append(f"[{label}]")
                        mpath = str(msg.get("path") or "").strip()
                        if mpath:
                            msg_lines.append(f"[path: {mpath}]")
                        mtext = str(msg.get("text") or "").strip()
                        if mtext:
                            msg_lines.append(mtext)
                        if msg_lines:
                            lines.append("\n".join(msg_lines))

                def _event_ts_line(event: Dict[str, Any]) -> str:
                    ets = str(event.get("ts") or "").strip()
                    return f"[ts: {ets}]" if ets else ""

                def _append_round_event(round_lines: List[str], event: Dict[str, Any]) -> None:
                    kind = str(event.get("kind") or "").strip()
                    ets_line = _event_ts_line(event)
                    if kind == "thinking":
                        # Backward compatibility for old compacted payloads:
                        # do not render thinking from pruned/compacted sections.
                        return
                    if kind == "notes":
                        if ets_line:
                            round_lines.append(ets_line)
                        etext = str(event.get("text") or "").strip()
                        round_lines.append(f"[AI Agent say]: {etext}" if etext else "[AI Agent say]")
                        return
                    if kind == "internal_note":
                        if ets_line:
                            round_lines.append(ets_line)
                        round_lines.append("[INTERNAL NOTE]")
                        etext = str(event.get("text") or "").strip()
                        if etext:
                            round_lines.append(etext)
                        return
                    if kind == "tool_call":
                        if ets_line:
                            round_lines.append(ets_line)
                        call_id = str(event.get("tool_call_id") or "").strip()
                        short_id = _short_tc_id(call_id)
                        tool_id = str(event.get("tool_id") or "").strip()
                        header = f"[TOOL CALL {short_id}].call"
                        if tool_id:
                            header += f" {tool_id}"
                        round_lines.append(header)
                        epath = str(event.get("path") or "").strip()
                        if epath:
                            round_lines.append(epath)
                        params_hint = str(event.get("params") or "").strip()
                        if params_hint:
                            round_lines.append("Params:\n" + params_hint)
                        return
                    if kind == "tool_result":
                        if ets_line:
                            round_lines.append(ets_line)
                        call_id = str(event.get("tool_call_id") or "").strip()
                        short_id = _short_tc_id(call_id)
                        tool_id = str(event.get("tool_id") or "").strip()
                        header = f"[TOOL RESULT {short_id}].result"
                        if tool_id:
                            header += f" {tool_id}"
                        round_lines.append(header)
                        epath = str(event.get("path") or "").strip()
                        if epath:
                            round_lines.append(f"logical_path: {epath}")
                        status = str(event.get("status") or "").strip()
                        if status:
                            round_lines.append(f"Status: {status}")
                        tokens = event.get("result_tokens")
                        large_result = bool(event.get("large_result"))
                        if tokens:
                            round_lines.append(f"result_tokens: {tokens}")
                        if large_result:
                            round_lines.append("result: compacted large result; exact content is recoverable by logical_path")
                        read_paths = event.get("read_paths")
                        if isinstance(read_paths, list) and read_paths:
                            round_lines.append("read_paths:")
                            for p in read_paths:
                                if p:
                                    round_lines.append(f"- {p}")
                        recover = str(event.get("recover_with") or "").strip()
                        if recover:
                            round_lines.append(f"recover_with: {recover}")
                        hint = str(event.get("hint") or "").strip()
                        if hint and not large_result:
                            round_lines.append(f"hint: {_compact_hint(hint, max_chars=260)}")
                        return
                    if kind == "notice":
                        if ets_line:
                            round_lines.append(ets_line)
                        round_lines.append("[REACT NOTICE]")
                        etext = str(event.get("text") or "").strip()
                        if etext:
                            round_lines.append(etext)
                        return
                    if kind == "code":
                        if ets_line:
                            round_lines.append(ets_line)
                        epath = str(event.get("path") or "").strip()
                        round_lines.append(f"[AI Agent wrote code] {epath}:" if epath else "[AI Agent wrote code]:")
                        etext = str(event.get("text") or "").strip()
                        if etext:
                            round_lines.append(etext)
                        return
                    if kind == "assistant":
                        if ets_line:
                            round_lines.append(ets_line)
                        round_lines.append("[ASSISTANT MESSAGE]")
                        epath = str(event.get("path") or "").strip()
                        if epath:
                            round_lines.append(f"[path: {epath}]")
                        etext = str(event.get("text") or "").strip()
                        if etext:
                            round_lines.append(etext)

                if isinstance(events, list) and events:
                    compact_round_idx = 0
                    current_round_lines: List[str] = []
                    current_round_has_terminal = False

                    def _flush_compact_round() -> None:
                        nonlocal current_round_lines, current_round_has_terminal, compact_round_idx
                        if not current_round_lines:
                            return
                        compact_round_idx += 1
                        lines.append(f"┌──────── COMPACTED ROUND {compact_round_idx} ────────┐")
                        lines.append(_indent_text_block("\n".join(current_round_lines)))
                        lines.append("└────────────────────────┘")
                        current_round_lines = []
                        current_round_has_terminal = False

                    for event in events:
                        if not isinstance(event, dict):
                            continue
                        kind = str(event.get("kind") or "").strip()
                        if kind == "thinking" and current_round_lines and current_round_has_terminal:
                            _flush_compact_round()
                        _append_round_event(current_round_lines, event)
                        if kind in {"tool_result", "notice", "assistant"}:
                            current_round_has_terminal = True
                    _flush_compact_round()
                elif isinstance(rounds, list) and rounds:
                    # Backward-compatible renderer for old compacted-round payloads.
                    lines.append("rounds:")
                    for row in rounds:
                        if not isinstance(row, dict):
                            continue
                        tool_id = str(row.get("tool_id") or "tool").strip()
                        call_id = str(row.get("tool_call_id") or "").strip()
                        status = str(row.get("status") or "").strip()
                        lines.append(f"- tool={tool_id} call_id={call_id} status={status}".strip())
                text = "\n".join(lines).strip()
            elif btype == "turn.feedback":
                lines = []
                origin = (meta.get("origin") or "").strip().lower() if isinstance(meta, dict) else ""
                if origin == "user":
                    lines.append("[USER FEEDBACK]")
                elif origin:
                    lines.append("[AUTO FEEDBACK]")
                else:
                    lines.append("[FEEDBACK]")
                if ts:
                    lines.append(f"[ts: {ts}]")
                reaction = meta.get("reaction") if isinstance(meta, dict) else None
                if reaction is not None:
                    lines.append(f"reaction: {reaction}")
                if text:
                    lines.append(text)
                text = "\n".join(lines).strip()
            elif btype == "react.notes":
                if isinstance(text, str):
                    ts_line = _ts_line(ts)
                    if ts_line:
                        text = f"{ts_line}\n[AI Agent say]: {text}".strip()
                    else:
                        text = f"[AI Agent say]: {text}".strip()
            elif btype == "react.round.start":
                text = ""
            elif btype == "react.thinking":
                if not getattr(self.runtime, "render_thinking", True):
                    continue
                lines = []
                ts_line = _ts_line(ts)
                if ts_line:
                    lines.append(ts_line)
                lines.append("[thinking]")
                if text:
                    lines.append(str(text).strip())
                text = "\n".join(lines).strip()
            elif btype == "react.decision.raw":
                interrupted = bool(isinstance(meta, dict) and meta.get("interrupted"))
                if not interrupted and not getattr(self.runtime, "render_decision_raw", False):
                    continue
                if isinstance(text, str):
                    lines = []
                    ts_line = _ts_line(ts)
                    if ts_line:
                        lines.append(ts_line)
                    lines.append("[REACT DECISION RAW INTERRUPTED]" if interrupted else "[REACT DECISION RAW]")
                    reason = ""
                    if isinstance(meta, dict):
                        reason = (meta.get("reason") or "").strip()
                    if reason:
                        lines.append(f"reason: {reason}")
                    if interrupted and isinstance(meta, dict):
                        checkpoint = (meta.get("checkpoint") or "").strip()
                        cancelled_phase = (meta.get("cancelled_phase") or "").strip()
                        if checkpoint:
                            lines.append(f"checkpoint: {checkpoint}")
                        if cancelled_phase:
                            lines.append(f"cancelled_phase: {cancelled_phase}")
                    lines.append(text)
                    text = "\n".join([l for l in lines if l]).strip()
            elif btype == "react.notice":
                payload = _maybe_parse_json(text) if (b.get("mime") or "").strip() == "application/json" else None
                if isinstance(payload, dict):
                    code = str(payload.get("code") or "").strip()
                    message = str(payload.get("message") or "").strip()
                    lines = []
                    ts_line = _ts_line(ts)
                    if ts_line:
                        lines.append(ts_line)
                    if code.startswith("protocol_violation."):
                        lines.append("[PROTOCOL VIOLATION]")
                    else:
                        lines.append("[REACT NOTICE]")
                    if code:
                        lines.append(f"code: {code}")
                    if message:
                        lines.append(message)
                    extra_payload = {k: v for k, v in payload.items() if k not in {"code", "message"}}
                    if extra_payload:
                        lines.append(json.dumps(extra_payload, ensure_ascii=False, indent=2))
                    text = "\n".join([l for l in lines if l]).strip()
            elif btype == "react.state":
                if not getattr(self.runtime, "render_react_state", False):
                    continue
            elif btype == "react.exit":
                if not getattr(self.runtime, "render_react_exit", False):
                    continue
            elif btype == "system.message":
                prefix = f"[SYSTEM MESSAGE]"
                if ts:
                    prefix += f"\n[ts: {ts}]"
                if path:
                    if isinstance(meta, dict) and meta.get("kind") == "cache_ttl_pruned":
                        path = ""
                    if path:
                        prefix += f"\n[path: {path}]"
                text = (prefix + "\n" + (text or "")).strip()
            elif btype == "user.attachment.meta":
                name = (meta.get("filename") or "").strip() or _attachment_name_from_path(path)
                mime = (meta.get("mime") or "").strip()
                prefix = f"[USER ATTACHMENT] {name or '(attachment)'}"
                if mime:
                    prefix += f" | {mime}"
                if ts:
                    prefix += f"\n[ts: {ts}]"
                phys = (meta.get("physical_path") or "").strip()
                if path:
                    prefix += f"\n[logical_path: {path}]"
                if phys:
                    prefix += "\n[physical_path: exists (derive)]"
                summary = (meta.get("summary") or "").strip()
                if summary:
                    prefix += f"\nsummary: {summary}"
                text = prefix
            elif btype == "user.attachment.text":
                prefix = "[USER ATTACHMENT TEXT]"
                if path:
                    prefix += f"\n[path: {path}]"
                text = (prefix + "\n" + (text or "")).strip()
            elif btype == "react.tool.code" and isinstance(text, str):
                tool_call_id = (b.get("call_id") or "").strip()
                if not tool_call_id:
                    tool_call_id = _call_id_from_path(path)
                lines = []
                ts_line = _ts_line(ts)
                if ts_line:
                    lines.append(ts_line)
                code_path = path or (f"{REACT_FILE_REF_PREFIX}{turn_id}.code.{tool_call_id}" if turn_id and tool_call_id else "")
                if code_path:
                    lines.append(f"[AI Agent wrote code] {code_path}:")
                else:
                    lines.append("[AI Agent wrote code]:")
                lines.append(self._tool_result_payload_text_for_prompt(
                    raw_text=text,
                    payload=None,
                    path=code_path,
                    tool_id="",
                    preview_label="[CODE PREVIEW TRUNCATED]",
                    recovery_lines=[],
                ))
                text = "\n".join([l for l in lines if l]).strip()
            elif btype == "react.tool.call" and isinstance(text, str):
                payload = _maybe_parse_json(text) if (b.get("mime") or "").strip() == "application/json" else None
                tool_id = ""
                tool_call_id = ""
                params = None
                if isinstance(directive.get("text"), str):
                    text = directive.get("text")
                elif isinstance(payload, dict):
                    tool_id = (payload.get("tool_id") or "").strip()
                    tool_call_id = (payload.get("tool_call_id") or "").strip()
                    params = payload.get("params")
                    meta_local = b.get("meta") if isinstance(b.get("meta"), dict) else {}
                    if not tool_call_id:
                        tool_call_id = (meta_local.get("tool_call_id") or b.get("call_id") or "").strip()
                    if not tool_call_id:
                        tool_call_id = _call_id_from_path(path)
                    short_id = _short_tc_id(tool_call_id)
                    lines = [""]
                    ts_line = _ts_line(ts)
                    if ts_line:
                        lines.append(ts_line)
                    header = f"[TOOL CALL {short_id}].call"
                    if tool_id:
                        header += f" {tool_id}"
                    lines.append(header)
                    if path:
                        lines.append(path)
                    if isinstance(params, dict):
                        params_out = dict(params)
                        if isinstance(params_out.get("content"), str):
                            content_val = params_out.get("content") or ""
                            content_lower = content_val.lower()
                            has_see_ref = any(
                                token in content_lower
                                for token in ("see conv:fi:", "see conv:so:", "see sk:", "see conv:ar:", "see conv:tc:")
                            )
                            if (not has_see_ref) and len(content_val) > 100:
                                file_path = _derive_file_context_path(
                                    turn_id=str(b.get("turn_id") or ""),
                                    raw_path=str(params_out.get("path") or ""),
                                )
                                suffix = f"... [truncated; see {file_path}]" if file_path else "... [truncated]"
                                params_out["content"] = content_val[:100] + suffix
                        lines.append("Params:\n" + json.dumps(params_out, ensure_ascii=False, indent=2))
                    elif params is not None:
                        lines.append("Params:\n" + json.dumps(params, ensure_ascii=False, indent=2))
                    text = "\n".join([l for l in lines if l is not None]).strip()
            elif btype == "react.tool.result":
                mime_val = (b.get("mime") or "").strip()
                meta_local = b.get("meta") if isinstance(b.get("meta"), dict) else {}
                tool_call_id = (b.get("call_id") or meta_local.get("tool_call_id") or "").strip()
                if not tool_call_id:
                    tool_call_id = _call_id_from_path(path)
                short_id = _short_tc_id(tool_call_id)
                handled = False
                render_role = (meta_local.get("render_role") or "").strip().lower()
                if render_role == "summary" and isinstance(text, str):
                    tool_id = call_id_to_tool_id.get(tool_call_id, "")
                    lines = []
                    ts_line = _ts_line(ts)
                    if ts_line:
                        lines.append(ts_line)
                    header = f"[TOOL RESULT {short_id}].summary"
                    if tool_id:
                        header += f" {tool_id}"
                    lines.append(header)
                    lines.append(text)
                    text = "\n".join([l for l in lines if l]).strip()
                    handled = True
                if (not handled) and mime_val == "application/json" and isinstance(text, str):
                    payload = _maybe_parse_json(text)
                    if isinstance(payload, dict) and payload.get("artifact_path"):
                        tool_id = (payload.get("tool_id") or "").strip()
                        if not tool_id and tool_call_id:
                            tool_id = call_id_to_tool_id.get(tool_call_id, "")
                        if tool_id in {"react.read", "web_tools.web_search", "web_tools.web_fetch"}:
                            text = ""
                            handled = True
                            continue
                        lines = []
                        ts_line = _ts_line(ts)
                        if ts_line:
                            lines.append(ts_line)
                        header = f"[TOOL RESULT {short_id}].summary"
                        if tool_id:
                            header += f" {tool_id}"
                        lines.append(header)
                        err_present = "error" in payload and payload.get("error") is not None
                        status_is_error = str(payload.get("status") or "").strip().lower() == "error"
                        if err_present or status_is_error or (payload.get("code") and payload.get("message")):
                            code, msg = _tool_error_code_message(payload)
                            lines.append(f"Status: error — {code} {msg}".strip())
                        else:
                            lines.append("Status: success")
                        ap = (payload.get("artifact_path") or "").strip()
                        kind = (payload.get("kind") or "").strip()
                        visibility = (payload.get("visibility") or "").strip()
                        channel = (payload.get("channel") or "").strip()
                        tokens = payload.get("tokens")
                        sources_used = payload.get("sources_used")
                        text_symbols = payload.get("text_symbols")
                        size_bytes = payload.get("size_bytes")
                        if ap:
                            phys = (payload.get("physical_path") or "").strip()
                            meta_bits = []
                            if phys:
                                meta_bits.append("physical_path: exists (derive)")
                            if kind:
                                meta_bits.append(f"kind: {kind}")
                            if visibility:
                                meta_bits.append(f"visibility: {visibility}")
                            if channel:
                                meta_bits.append(f"channel: {channel}")
                            if text_symbols is not None:
                                meta_bits.append(f"text_symbols: {text_symbols}")
                            if size_bytes is not None:
                                meta_bits.append(f"size_bytes: {size_bytes}")
                            if tokens is not None:
                                meta_bits.append(f"tokens: {tokens}")
                            if isinstance(sources_used, list) and sources_used:
                                meta_bits.append(f"sources_used: {sources_used}")
                            meta_tail = f" | " + " | ".join(meta_bits) if meta_bits else ""
                            lines.append("Artifacts:")
                            lines.append(f"- logical_path: {ap}{meta_tail}")
                        text = "\n".join(lines).strip()
                    elif isinstance(payload, dict) and "paths" in payload and "total_tokens" in payload:
                        lines = []
                        ts_line = _ts_line(ts)
                        if ts_line:
                            lines.append(ts_line)
                        header = f"[TOOL RESULT {short_id}].result"
                        tool_id = call_id_to_tool_id.get(tool_call_id, "")
                        if tool_id:
                            header += f" {tool_id}"
                        lines.append(header)
                        if path:
                            lines.append(f"logical_path: {path}")
                        if mime_val:
                            lines.append(f"mime: {mime_val}")
                        paths = payload.get("paths") or []
                        if isinstance(paths, list) and paths:
                            for row in paths:
                                if not isinstance(row, dict):
                                    continue
                                p = row.get("path")
                                if not p:
                                    continue
                                tok = row.get("tokens")
                                status = (row.get("status") or "").strip()
                                if tok:
                                    suffix = f"tokens={tok}"
                                    if status == "exists_in_visible_context":
                                        suffix += ", already visible"
                                    elif status:
                                        suffix += f", status={status}"
                                    lines.append(f"- {p} ({suffix})")
                                elif status == "exists_in_visible_context":
                                    lines.append(f"- {p} (already visible in current context)")
                                elif status:
                                    lines.append(f"- {p} (status={status})")
                                else:
                                    lines.append(f"- {p}")
                        visible = payload.get("exists_in_visible_context") or []
                        visible_refs = payload.get("visible_context_refs") or {}
                        if isinstance(visible, list) and visible:
                            lines.append(
                                "Already visible in current context; no new content was loaded for these paths:"
                            )
                            for p in visible:
                                if isinstance(p, str) and p:
                                    ref = visible_refs.get(p) if isinstance(visible_refs, dict) else None
                                    if isinstance(ref, dict):
                                        visible_at = (ref.get("visible_at") or "").strip()
                                        tool_result_path = (ref.get("tool_result_path") or "").strip()
                                        bits = []
                                        if visible_at:
                                            bits.append(visible_at)
                                        if tool_result_path:
                                            bits.append(f"see {tool_result_path}")
                                        if bits:
                                            lines.append(f"- {p} (already visible at {'; '.join(bits)})")
                                            continue
                                    lines.append(f"- {p}")
                        missing = payload.get("missing") or []
                        if missing:
                            lines.append(f"missing: {missing}")
                        text = "\n".join(lines).strip()
                    elif isinstance(payload, list):
                        range_path = _sources_pool_range(payload)
                        lines = []
                        ts_line = _ts_line(ts)
                        if ts_line:
                            lines.append(ts_line)
                        header = f"[TOOL RESULT {short_id}].result"
                        tool_id = call_id_to_tool_id.get(tool_call_id, "")
                        if tool_id:
                            header += f" {tool_id}"
                        lines.append(header)
                        if range_path:
                            lines.append(f"logical_path: {range_path}")
                        elif path:
                            lines.append(f"logical_path: {path}")
                        if mime_val:
                            lines.append(f"mime: {mime_val}")
                        items_stats = meta.get("items_stats") if isinstance(meta, dict) else None
                        if (
                            isinstance(items_stats, dict)
                            and items_stats
                            and _should_render_items_stats(
                                tool_id=tool_id,
                                path=range_path or path,
                                meta=meta,
                            )
                        ):
                            lines.append("items_stats:")
                            lines.append(json.dumps(items_stats, ensure_ascii=False, indent=2))
                        lines.append("payload:")
                        lines.append(self._tool_result_payload_text_for_prompt(
                            raw_text=text,
                            payload=payload,
                            path=range_path or path,
                            tool_id=tool_id,
                        ))
                        text = "\n".join([l for l in lines if l]).strip()
                    elif isinstance(payload, dict):
                        # Generic JSON payload (e.g. legacy fetch map): add tool result header.
                        lines = []
                        ts_line = _ts_line(ts)
                        if ts_line:
                            lines.append(ts_line)
                        header = f"[TOOL RESULT {short_id}].result"
                        tool_id = call_id_to_tool_id.get(tool_call_id, "")
                        if tool_id:
                            header += f" {tool_id}"
                        lines.append(header)
                        if path:
                            lines.append(f"logical_path: {path}")
                        if mime_val:
                            lines.append(f"mime: {mime_val}")
                        items_stats = meta.get("items_stats") if isinstance(meta, dict) else None
                        if (
                            isinstance(items_stats, dict)
                            and items_stats
                            and _should_render_items_stats(tool_id=tool_id, path=path, meta=meta)
                        ):
                            lines.append("items_stats:")
                            lines.append(json.dumps(items_stats, ensure_ascii=False, indent=2))
                        lines.append("payload:")
                        lines.append(self._tool_result_payload_text_for_prompt(
                            raw_text=text,
                            payload=payload,
                            path=path,
                            tool_id=tool_id,
                        ))
                        text = "\n".join([l for l in lines if l]).strip()
                elif isinstance(text, str):
                    # Non-JSON content blocks: render as artifact when path looks like an artifact.
                    lines = []
                    ts_line = _ts_line(ts)
                    if ts_line:
                        lines.append(ts_line)
                    tool_id = call_id_to_tool_id.get(tool_call_id, "")
                    is_artifact = path.startswith((REACT_FILE_REF_PREFIX, CONV_AR_PREFIX, "sk:", CONV_SO_PREFIX))
                    if is_artifact:
                        header = f"[TOOL RESULT {short_id}].artifact"
                        if tool_id:
                            header += f" {tool_id}"
                        lines.append(header)
                        physical_path = ""
                        if isinstance(meta, dict):
                            physical_path = str(meta.get("physical_path") or "").strip()
                        if path:
                            path_line = f"logical_path: {path}"
                            if isinstance(meta, dict) and meta.get("text_symbols") is not None:
                                path_line += f" | text_symbols: {meta.get('text_symbols')}"
                            if isinstance(meta, dict) and meta.get("size_bytes") is not None:
                                path_line += f" | size_bytes: {meta.get('size_bytes')}"
                            lines.append(path_line)
                        if physical_path:
                            lines.append("physical_path: exists (derive)")
                        header_text = "\n".join([l for l in lines if l]).strip()
                        projection_meta = meta.get("projection") if isinstance(meta, dict) else None
                        preformatted_preview = bool(
                            isinstance(projection_meta, dict)
                            and projection_meta.get("already_rendered")
                            and str(projection_meta.get("format") or "").strip() in {
                                "text_file_preview.v1",
                            }
                        )
                        extra_text_blocks.append(self._tool_result_payload_text_for_prompt(
                            raw_text=text,
                            payload=None,
                            path=path,
                            tool_id=tool_id,
                            preview_label="[ARTIFACT PREVIEW TRUNCATED]",
                            recovery_lines=self._large_text_recovery_lines(path=path, physical_path=physical_path),
                            line_number_visible_text=True,
                            display_path=path,
                            preformatted_preview=preformatted_preview,
                        ))
                        text = header_text
                    else:
                        header = f"[TOOL RESULT {short_id}].result"
                        if tool_id:
                            header += f" {tool_id}"
                        lines.append(header)
                        if path:
                            lines.append(f"logical_path: {path}")
                        if mime_val:
                            lines.append(f"mime: {mime_val}")
                        lines.append("payload:")
                        lines.append(self._tool_result_payload_text_for_prompt(
                            raw_text=text,
                            payload=None,
                            path=path,
                            tool_id=tool_id,
                        ))
                        text = "\n".join([l for l in lines if l]).strip()
            base64 = b.get("base64") or b.get("data")
            mime = (b.get("mime") or b.get("media_type") or "").strip() or None
            if btype in {"react.note", "react.note.preserved"} and isinstance(text, str):
                text = "[INTERNAL NOTE]\n" + self._tool_result_payload_text_for_prompt(
                    raw_text=text,
                    payload=None,
                    path=path,
                    tool_id="",
                    preview_label="[INTERNAL NOTE PREVIEW TRUNCATED]",
                    recovery_lines=self._large_text_recovery_lines(path=path),
                )

            emitted: List[Dict[str, Any]] = []
            if text:
                if current_round_id:
                    text = _indent_text_block(str(text))
                emitted.append({"type": "text", "text": str(text)})

            if extra_text_blocks:
                for extra_text in extra_text_blocks:
                    if not extra_text:
                        continue
                    if current_round_id:
                        extra_text = _indent_text_block(str(extra_text))
                    emitted.append({"type": "text", "text": str(extra_text)})

            mime_norm = (mime or "").strip().lower()
            attach_binary = True
            omitted_reason = ""
            current_tool_call_id = (b.get("call_id") or meta.get("tool_call_id") or "").strip()
            if not current_tool_call_id:
                current_tool_call_id = _call_id_from_path(path)
            current_tool_id = (call_id_to_tool_id.get(current_tool_call_id, "") or meta.get("tool_id") or "").strip()
            if (
                base64
                and btype == "react.tool.result"
                and mime_norm in MODALITY_DOC_MIME
                and current_tool_id != "react.read"
                and not bool(meta.get("attach_to_model") or meta.get("model_visible_binary"))
            ):
                attach_binary = False
                omitted_reason = (
                    "generated PDF/tool artifact; the file is already addressable by logical path, "
                    "so attach it with react.read only when the PDF bytes are needed"
                )
            _append_base64_model_block(
                emitted,
                base64_data=base64,
                mime=mime,
                path=path,
                include_provider_payload=attach_binary,
                omitted_reason=omitted_reason,
            )

            if not emitted:
                continue

            if cache:
                emitted[-1]["cache"] = True
            out.extend(emitted)
            if current_round_id and _is_round_terminal_block(b):
                current_round_accepts_external = False
        _close_current_round()
        return out

    def _format_message_blocks_debug(self, msg_blocks: List[Dict[str, Any]]) -> str:
        """
        Render message blocks as text for debugging.
        Marks cache points with numbered arrows.
        """
        def _clip(text: str, limit: int = 200) -> str:
            s = str(text or "")
            if len(s) <= limit:
                return s
            return s[:limit] + "…"

        lines: List[str] = []
        cache_idx = 0
        for b in (msg_blocks or []):
            if not isinstance(b, dict):
                continue
            prefix = "   "
            if b.get("cache"):
                cache_idx += 1
                prefix = f"=>[{cache_idx}] "
            btype = b.get("type") or "text"
            if btype == "text":
                text = b.get("text") or ""
                lines.append(prefix + _clip(text))
            elif btype == "image":
                data, media = self._message_block_base64_and_media(b)
                media = media or "image/png"
                data_len = len(data) if isinstance(data, str) else 0
                estimated_tokens = self._estimate_base64_model_tokens(data, media)
                estimate_tail = f" provider_tokens_estimate={estimated_tokens}" if estimated_tokens else ""
                lines.append(prefix + f"<image media_type={media} b64_len={data_len}{estimate_tail}>")
            elif btype == "document":
                data, media = self._message_block_base64_and_media(b)
                media = media or "application/pdf"
                data_len = len(data) if isinstance(data, str) else 0
                estimated_tokens = self._estimate_base64_model_tokens(data, media)
                estimate_tail = f" provider_tokens_estimate={estimated_tokens}" if estimated_tokens else ""
                lines.append(prefix + f"<document media_type={media} b64_len={data_len}{estimate_tail}>")
            else:
                lines.append(prefix + f"<{btype}>")
        return "\n".join(lines).rstrip()

    async def persist(self, ctx_client: Optional["ContextRAGClient"]) -> None:
        if not ctx_client:
            return
        if self._lock is None:
            await self._persist_locked(ctx_client)
            return
        async with self._lock:
            await self._persist_locked(ctx_client)

    async def _persist_locked(self, ctx_client: Optional["ContextRAGClient"]) -> None:
        if not ctx_client:
            return
        payload = build_timeline_payload(
            blocks=self._blocks_for_persist(),
            sources_pool=list(self.sources_pool or []),
            conversation_title=self.conversation_title,
            conversation_started_at=self.conversation_started_at,
            last_external_event_id=self.last_external_event_id,
            last_external_event_seq=self.last_external_event_seq,
            last_rendered_event_cursor=self.last_rendered_event_cursor,
            cache_last_touch_at=self.cache_last_touch_at,
            cache_last_ttl_seconds=self.cache_last_ttl_seconds,
            agent_selection_snapshot=self.agent_selection_snapshot,
            last_known_feedback_ts=self.last_known_feedback_ts,
            include_sources_pool=True,
        )
        turn_ids = payload.get("turn_ids") or []
        extra_tags = [f"turn:{tid}" for tid in turn_ids if isinstance(tid, str) and tid]
        # Keep index text minimal to avoid bloating conv_messages.text.
        compact_text = json.dumps(
            {
                "conversation_title": self.conversation_title or "",
                "conversation_started_at": self.conversation_started_at or "",
                "last_activity_at": payload.get("last_activity_at") or "",
                "blocks_count": len(payload.get("blocks") or []),
                "sources_pool_count": len(self.sources_pool or []),
                "sources_pool": _compact_sources_pool_for_index(self.sources_pool or []),
                "turn_ids": turn_ids,
            },
            ensure_ascii=False,
        )
        await ctx_client.save_artifact(
            kind=TIMELINE_KIND,
            tenant=self.runtime.tenant or "",
            project=self.runtime.project or "",
            user_id=self.runtime.user_id or "",
            conversation_id=self.runtime.conversation_id or "",
            user_type=self.runtime.user_type or "",
            turn_id=self.runtime.turn_id or "",
            bundle_id=self.runtime.bundle_id,
            agent_id=index_agent_id(self.runtime.agent_id),
            content=payload,
            content_str=compact_text,
            extra_tags=extra_tags or None,
        )
        try:
            sources_payload = {"sources_pool": list(self.sources_pool or [])}
            sources_text = json.dumps(
                {
                    "sources_pool": _compact_sources_pool_for_index(self.sources_pool or []),
                    "sources_pool_count": len(self.sources_pool or []),
                    "turn_ids": turn_ids,
                    "last_activity_at": payload.get("last_activity_at") or "",
                },
                ensure_ascii=False,
            )
            await ctx_client.save_artifact(
                kind=SOURCES_POOL_KIND,
                tenant=self.runtime.tenant or "",
                project=self.runtime.project or "",
                user_id=self.runtime.user_id or "",
                conversation_id=self.runtime.conversation_id or "",
                user_type=self.runtime.user_type or "",
                turn_id=self.runtime.turn_id or "",
                bundle_id=self.runtime.bundle_id,
                agent_id=index_agent_id(self.runtime.agent_id),
                content=sources_payload,
                content_str=sources_text,
                extra_tags=extra_tags or None,
            )
        except Exception:
            pass
