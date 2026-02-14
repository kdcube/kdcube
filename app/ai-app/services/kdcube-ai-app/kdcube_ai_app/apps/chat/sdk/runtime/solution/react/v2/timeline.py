# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import hashlib
import time
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.caching import apply_cache_points
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import (
    summarize_context_blocks_progressive,
    summarize_turn_prefix_progressive,
    build_compaction_digest,
)
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    dedupe_sources_by_url,
    normalize_sources_any,
)
from kdcube_ai_app.apps.chat.sdk.util import token_count, isoz, ts_key

TIMELINE_KIND = "conv.timeline.v1"
SOURCES_POOL_KIND = "conv:sources_pool"

TIMELINE_FILENAME = "timeline.json"

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


def build_timeline_payload(
    *,
    blocks: List[Dict[str, Any]],
    sources_pool: Optional[List[Dict[str, Any]]] = None,
    conversation_title: Optional[str] = None,
    conversation_started_at: Optional[str] = None,
    cache_last_touch_at: Optional[int] = None,
    cache_last_ttl_seconds: Optional[int] = None,
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
        "cache_last_touch_at": cache_last_touch_at,
        "cache_last_ttl_seconds": cache_last_ttl_seconds,
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
    return {
        "blocks": blocks,
        "sources_pool": sources_pool,
        "turn_ids": turn_ids,
        "ts": payload.get("ts"),
        "version": payload.get("version", 1),
        "conversation_title": payload.get("conversation_title") or "",
        "conversation_started_at": payload.get("conversation_started_at") or "",
        "last_activity_at": payload.get("last_activity_at") or "",
        "cache_last_touch_at": cache_last_touch_at,
        "cache_last_ttl_seconds": cache_last_ttl_seconds,
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
        if (b.get("type") or "") == "assistant.completion":
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


def extract_user_prompt_block(blocks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") == "user.prompt":
            return b
    return None


def extract_assistant_completion_block(blocks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") == "assistant.completion":
            return b
    return None


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
    marker = ".user.attachments/"
    if marker in path:
        return path.split(marker, 1)[1]
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
        }
        for key in ("rn", "hosted_uri", "key", "physical_path", "local_path"):
            if meta.get(key):
                payload[key] = meta.get(key)
        if meta.get("summary") or meta.get("description"):
            payload["summary"] = meta.get("summary") or meta.get("description")
        out.append(payload)
    return out


def extract_assistant_files_from_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    paths: List[str] = []
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
        if not (meta.get("hosted_uri") or meta.get("rn") or meta.get("key") or meta.get("local_path")):
            continue
        p = (meta.get("artifact_path") or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        paths.append(p)
    out: List[Dict[str, Any]] = []
    for p in paths:
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
        }
        for key in ("rn", "hosted_uri", "key", "physical_path", "local_path"):
            if art.get(key):
                payload[key] = art.get(key)
        if art.get("summary") or art.get("description"):
            payload["summary"] = art.get("summary") or art.get("description")
        out.append(payload)
    return out


def _build_turn_view(
    *,
    turn_id: str,
    blocks: List[Dict[str, Any]],
    sources_pool: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    sources_pool = list(sources_pool or [])
    user_block = extract_user_prompt_block(blocks)
    assistant_block = extract_assistant_completion_block(blocks)
    attachments = extract_user_attachments_from_blocks(blocks)
    files = extract_assistant_files_from_blocks(blocks)
    used_sids = extract_sources_used_from_blocks(blocks)
    suggested_followups = extract_followups_from_blocks(blocks)
    clarifications = extract_clarification_questions_from_blocks(blocks)
    used_sources = materialize_sources_by_sids(sources_pool, used_sids)
    timeline_text_items = _extract_timeline_text_items(blocks, turn_id)
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
        "attachments": attachments,
        "files": files,
        "citations": used_sources,
        "timeline_text": timeline_text_items,
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


def materialize_sources_by_sids(pool: List[Dict[str, Any]], sids: List[int]) -> List[Dict[str, Any]]:
    if not sids or not pool:
        return []
    wanted = set(int(s) for s in sids if isinstance(s, (int, float)))
    return [row for row in pool if isinstance(row, dict) and int(row.get("sid") or 0) in wanted]


def ensure_sources_in_pool(pool: List[Dict[str, Any]], sources: Any) -> tuple[List[Dict[str, Any]], List[int]]:
    normalized = normalize_sources_any(sources)
    if not normalized:
        return pool, []
    merged = dedupe_sources_by_url(pool, normalized)
    return merged, extract_source_sids(merged)


def resolve_artifact_from_timeline(timeline: Dict[str, Any], path: str) -> Optional[Dict[str, Any]]:
    """
    Build a canonical artifact dict from timeline blocks.
    Returns None if no matching blocks found.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    p = path.strip()
    if p.startswith("so:"):
        p = p[len("so:"):]
    if p.startswith("sources_pool["):
        return {"kind": "sources_pool", "items": resolve_sources_pool_selector(timeline, p)}

    blocks = _collect_blocks(timeline)
    meta: Dict[str, Any] = {}
    # meta may be encoded in react.tool.result json blocks
    for b in reversed(blocks):
        if (b.get("type") or "") != "react.tool.result":
            continue
        if (b.get("mime") or "").strip() != "application/json":
            continue
        txt = b.get("text")
        if not isinstance(txt, str):
            continue
        meta_obj = _parse_meta_json(txt)
        if meta_obj.get("artifact_path") == p:
            meta = meta_obj
            break

    matching = [b for b in blocks if (b.get("path") or "") == p]
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
    if text_block and isinstance(text_block.get("text"), str):
        art["text"] = text_block.get("text")
    if bin_block and bin_block.get("base64"):
        art["base64"] = bin_block.get("base64")
    return art


def materialize_show_artifacts(timeline: Dict[str, Any], show_paths: List[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for raw_path in (show_paths or []):
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        path = raw_path.strip()
        if path.startswith("so:"):
            path = path[len("so:"):]
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
    version: int = 1
    ts: str = ""
    blocks: List[Dict[str, Any]] = None
    sources_pool: List[Dict[str, Any]] = None
    announce_blocks: List[Dict[str, Any]] = None
    current_turn_offset: Optional[int] = None
    conversation_title: str = ""
    conversation_started_at: str = ""
    cache_last_touch_at: Optional[int] = None
    cache_last_ttl_seconds: Optional[int] = None

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

    @classmethod
    def from_payload(cls, payload: Dict[str, Any], *, runtime: RuntimeCtx, svc: Optional[Any] = None) -> "Timeline":
        parsed = parse_timeline_payload(payload or {})
        return cls(
            runtime=runtime,
            svc=svc,
            version=int(parsed.get("version") or 1),
            ts=str(parsed.get("ts") or ""),
            blocks=list(parsed.get("blocks") or []),
            sources_pool=list(parsed.get("sources_pool") or []),
            conversation_title=str(parsed.get("conversation_title") or ""),
            conversation_started_at=str(parsed.get("conversation_started_at") or ""),
            cache_last_touch_at=parsed.get("cache_last_touch_at"),
            cache_last_ttl_seconds=parsed.get("cache_last_ttl_seconds"),
        )

    def to_payload(self) -> Dict[str, Any]:
        return build_timeline_payload(
            blocks=list(self.blocks or []),
            sources_pool=list(self.sources_pool or []),
            conversation_title=self.conversation_title,
            conversation_started_at=self.conversation_started_at,
            cache_last_touch_at=self.cache_last_touch_at,
            cache_last_ttl_seconds=self.cache_last_ttl_seconds,
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
                sources_pool=_compact_sources_pool_for_index(self.sources_pool or []),
                conversation_title=self.conversation_title,
                conversation_started_at=self.conversation_started_at,
                cache_last_touch_at=self.cache_last_touch_at,
                cache_last_ttl_seconds=self.cache_last_ttl_seconds,
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
        self.sources_pool = list(sources_pool or [])

    def set_conversation_title(self, title: str) -> None:
        self.conversation_title = (title or "").strip()

    def set_conversation_started_at(self, ts: str) -> None:
        self.conversation_started_at = (ts or "").strip()

    def set_announce(self, blocks: Optional[List[Dict[str, Any]]]) -> None:
        if not blocks:
            self.announce_blocks = []
        else:
            self.announce_blocks = [b for b in (blocks or []) if isinstance(b, dict)]

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
            if p.startswith("so:"):
                paths.add(p[3:])
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
                fp = (pathlib.Path(outdir) / rel_path).resolve()
                if not fp.exists():
                    return None
                return fp.read_text(encoding="utf-8")
            except Exception:
                return None

        def _resolve_ref(val: Any, param_name: Optional[str] = None):
            if not isinstance(val, str) or not val.startswith("ref:"):
                return val
            ref = val[len("ref:"):].strip()
            if visible_paths is not None and ref not in visible_paths:
                violations.append({"code": "ref_not_visible", "path": ref, "param": param_name})
                return None
            if param_name == "sources_list" and not (ref.startswith("so:") or ref.startswith("sources_pool[")):
                violations.append({"code": "sources_list_requires_sources_pool", "path": ref, "param": param_name})
                return None
            # resolve via timeline
            if ref.startswith("so:") or ref.startswith("sources_pool["):
                resolved = self.resolve_sources_pool(ref if ref.startswith("sources_pool[") else ref[3:])
                return resolved
            resolved = self.resolve_artifact(ref)
            if isinstance(resolved, dict):
                if isinstance(resolved.get("text"), str):
                    return resolved.get("text")
                if resolved.get("base64"):
                    return resolved.get("base64")
                fp = resolved.get("filepath") or ""
                if fp:
                    txt = _read_text_from_file(fp)
                    if isinstance(txt, str):
                        return txt
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

    def _estimate_block_tokens(self, block: Dict[str, Any]) -> int:
        if not isinstance(block, dict):
            return 0
        text = block.get("text")
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        if block.get("hidden") or meta.get("hidden"):
            replacement = block.get("replacement_text") or meta.get("replacement_text")
            if isinstance(replacement, str) and replacement.strip():
                text = replacement
        if isinstance(text, str) and text.strip():
            try:
                return token_count(text)
            except Exception:
                return max(1, int(len(text) / 4))
        return 0

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
        if btype in {"user.prompt", "assistant.completion", "react.tool.call", "turn.header"}:
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

    def _apply_hidden_replacements(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not blocks:
            return []
        out: List[Dict[str, Any]] = []
        hidden_seen: set[str] = set()
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            if not (blk.get("hidden") or meta.get("hidden")):
                out.append(blk)
                continue
            path = (blk.get("path") or "").strip()
            if not path or path in hidden_seen:
                continue
            repl = (blk.get("replacement_text") or meta.get("replacement_text") or "").strip()
            if not repl:
                continue
            hidden_seen.add(path)
            repl_blk = dict(blk)
            repl_blk.pop("base64", None)
            repl_blk["text"] = repl
            repl_blk["hidden"] = False
            out.append(repl_blk)
        return out

    def apply_hidden_replacements(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._apply_hidden_replacements(blocks)


    def _collect_blocks(self) -> List[Dict[str, Any]]:
        return list(self.blocks or [])

    def _append_tail_blocks(self, *, blocks: List[Dict[str, Any]], include_sources: bool, include_announce: bool) -> List[Dict[str, Any]]:
        if not include_sources and not include_announce:
            return blocks
        tail: List[Dict[str, Any]] = []
        if include_announce:
            try:
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.plan import build_active_plan_blocks
                tail.extend(build_active_plan_blocks(
                    blocks=list(blocks or []),
                    current_turn_id=self.runtime.turn_id or "",
                    current_ts=self.runtime.started_at or "",
                ))
            except Exception:
                pass
        if include_sources:
            try:
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.layout import build_sources_pool_text
                sources_text = build_sources_pool_text(sources_pool=list(self.sources_pool or []))
            except Exception:
                sources_text = ""
            if sources_text:
                tail.append({"text": sources_text})
        if include_announce:
            tail.extend(self.announce_blocks or [])
        return list(blocks or []) + tail


    def hide_paths(self, paths: List[str], replacement_text: str) -> Dict[str, Any]:
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
            if isinstance(blk.get("text"), str):
                try:
                    tokens_hidden += token_count(blk.get("text") or "")
                except Exception:
                    pass
            if not replacement_assigned:
                blk["replacement_text"] = replacement_text or ""
                replacement_assigned = True
            else:
                blk["replacement_text"] = ""
            replaced += 1
        status = "ok" if replaced else "not_found"
        if replaced:
            self.update_timestamp()
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
    ) -> List[Dict[str, Any]]:
        if not blocks:
            return []
        sys_est = max(1, int(len(system_text or "") / 4))
        block_est = self._estimate_blocks_tokens(blocks)
        total_est = sys_est + block_est
        if not force and total_est <= int(max_tokens * 0.9):
            return blocks

        boundary_start = self._find_last_summary_index(blocks) + 1
        boundary_end = len(blocks)
        if boundary_start >= boundary_end:
            return blocks

        context_budget = max(1, int(max_tokens - sys_est))
        keep_recent_tokens = max(1, int(context_budget * 0.7))
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
        if cut_index <= boundary_start:
            return blocks

        if is_split_turn and turn_start_index < boundary_start:
            turn_start_index = boundary_start

        previous_summary: Optional[str] = None
        for idx in range(boundary_start - 1, -1, -1):
            blk = blocks[idx]
            if not isinstance(blk, dict):
                continue
            if not self._is_compaction_summary_block(blk):
                continue
            text = blk.get("text")
            if isinstance(text, str) and text.strip():
                previous_summary = text.strip()
            break

        history_end = turn_start_index if is_split_turn else cut_index
        history_blocks = [
            blk
            for blk in blocks[boundary_start:history_end]
            if not self._is_compaction_summary_block(blk)
        ]
        summary: Optional[str] = None
        if not history_blocks and previous_summary:
            summary = previous_summary
        elif history_blocks:
            summary = await summarize_context_blocks_progressive(
                svc=self.svc,
                blocks=history_blocks,
                max_tokens=800,
                previous_summary=previous_summary,
            )

        turn_prefix_blocks: List[Dict[str, Any]] = []
        if is_split_turn and turn_start_index != -1 and cut_index > turn_start_index:
            turn_prefix_blocks = blocks[turn_start_index:cut_index]

        prefix_summary: Optional[str] = None
        if turn_prefix_blocks:
            prefix_summary = await summarize_turn_prefix_progressive(
                svc=self.svc,
                blocks=turn_prefix_blocks,
                max_tokens=400,
            )
            if not prefix_summary:
                return blocks

        if summary is None and not history_blocks and not previous_summary:
            summary = "No prior history."

        if summary is None:
            return blocks

        if prefix_summary:
            summary = f"{summary}\n\n---\n\n**Turn Context (split turn):**\n\n{prefix_summary}"

        compacted_blocks = blocks[boundary_start:cut_index]
        digest = build_compaction_digest(compacted_blocks)
        covered_turn_ids = extract_turn_ids_from_blocks(compacted_blocks)
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
        if is_split_turn and split_turn_id:
            meta["split_turn_id"] = split_turn_id

        summary_block = self._block(
            type="conv.range.summary",
            author="system",
            turn_id=summary_turn_id,
            ts="",
            text=summary,
            path=(f"su:{summary_turn_id}.conv.range.summary" if summary_turn_id else ""),
            meta=meta,
        )

        updated_blocks = list(blocks)
        updated_blocks.insert(cut_index, summary_block)
        self.blocks = list(updated_blocks)
        self.update_timestamp()
        if self.current_turn_offset is not None and cut_index <= self.current_turn_offset:
            self.current_turn_offset += 1

        if self.runtime.save_summary:
            try:
                await self.runtime.save_summary({
                    "summary": summary,
                    "covered_turn_ids": covered_turn_ids,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "blocks_count": len(compacted_blocks),
                    "split_turn": bool(is_split_turn),
                    "split_turn_id": split_turn_id,
                    "compaction_digest": digest,
                })
            except Exception:
                pass
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
        debug_cache_trace: bool = True,
    ) -> List[Dict[str, Any]]:
        if debug_cache_trace and not debug_log and not debug_print:
            debug_print = True
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
        debug_cache_trace: bool = True,
    ) -> List[Dict[str, Any]]:
        if debug_cache_trace and not debug_log and not debug_print:
            debug_print = True
        self.apply_session_cache_ttl_pruning()
        blocks = self._collect_blocks()
        if self.runtime.max_tokens:
            before_tokens = None
            after_tokens = None
            compacted_tokens = None
            if self.runtime.on_before_compaction:
                try:
                    before_visible = self._slice_after_compaction_summary(blocks)
                    before_tokens = self._estimate_blocks_tokens(before_visible)
                    await self.runtime.on_before_compaction({"before_tokens": before_tokens})
                except Exception:
                    pass
            blocks = await self.sanitize_context_blocks(
                system_text=system_text or "",
                blocks=blocks,
                max_tokens=int(self.runtime.max_tokens or 28000),
                keep_recent_turns=keep_recent_turns,
                force=force_sanitize,
            )
            if self.runtime.on_after_compaction:
                try:
                    after_visible = self._slice_after_compaction_summary(blocks)
                    after_tokens = self._estimate_blocks_tokens(after_visible)
                    if before_tokens is not None and after_tokens is not None:
                        compacted_tokens = max(0, before_tokens - after_tokens)
                    await self.runtime.on_after_compaction({
                        "before_tokens": before_tokens,
                        "after_tokens": after_tokens,
                        "compacted_tokens": compacted_tokens,
                    })
                except Exception:
                    pass
        visible_blocks = self._slice_after_compaction_summary(blocks)
        visible_blocks = self._apply_hidden_replacements(visible_blocks)
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
        )
        msg_blocks = self._blocks_to_message_blocks(visible_blocks)
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

    def render_base(self, *, cache_last: bool = False) -> List[Dict[str, Any]]:
        blocks = self._collect_blocks()
        blocks = self._apply_hidden_replacements(blocks)
        self._apply_cache_markers(blocks, cache_last=cache_last)
        return blocks

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
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.session import (
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
            return {"status": "error"}


    def _apply_cache_markers(self, blocks: List[Dict[str, Any]], *, cache_last: bool) -> None:
        if not blocks:
            return
        for b in blocks:
            if isinstance(b, dict):
                b.pop("cache", None)
        # Prefer two cache points when possible (intermediate + last stable block).
        apply_cache_points(blocks, min_blocks=2, offset=2)
        if cache_last and blocks:
            blocks[-1]["cache"] = True

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
            return f"tc_{raw[:4]}"

        def _call_id_from_path(p: str) -> str:
            if not p:
                return ""
            try:
                # tc:<turn_id>.tool_calls.<call_id>.out.json
                if ".tool_calls." in p:
                    tail = p.split(".tool_calls.", 1)[1]
                    call_id = tail.split(".", 1)[0]
                    return call_id
            except Exception:
                pass
            return ""

        def _ts_line(val: str) -> str:
            return f"[ts: {val}]" if val else ""

        def _maybe_parse_json(val: str) -> Optional[Any]:
            try:
                return json.loads(val)
            except Exception:
                return None

        def _derive_file_context_path(*, turn_id: str, raw_path: str) -> str:
            path = (raw_path or "").strip()
            if not path:
                return ""
            if path.startswith("fi:"):
                return path
            if path.startswith("turn_") and "/files/" in path:
                tid, rel = path.split("/files/", 1)
                return f"fi:{tid}.files/{rel}"
            if "/files/" in path and ".files/" in path:
                # already logical-ish
                return f"fi:{path}"
            if turn_id:
                return f"fi:{turn_id}.files/{path.lstrip('/')}"
            return path

        def _sources_pool_range(rows: List[Dict[str, Any]]) -> str:
            sids = [int(r.get("sid") or 0) for r in rows if isinstance(r, dict) and r.get("sid") is not None]
            sids = [s for s in sids if s > 0]
            if not sids:
                return ""
            return f"so:sources_pool[{sids[0]}-{sids[-1]}]"

        out: List[Dict[str, Any]] = []
        for b in (blocks or []):
            if not isinstance(b, dict):
                continue
            cache = bool(b.get("cache"))
            text = b.get("text")
            btype = (b.get("type") or "")
            raw_ts = b.get("ts")
            ts = str(raw_ts).strip() if raw_ts is not None else ""
            path = (b.get("path") or "").strip()
            meta = b.get("meta") if isinstance(b.get("meta"), dict) else {}
            if btype == "turn.header":
                turn_id = b.get("turn_id") or ""
                started_at = ts
                if started_at:
                    text = f"════════\n[TURN {turn_id} (started at {started_at})]"
                else:
                    text = f"════════\n[TURN {turn_id}]"
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
            elif btype == "assistant.completion":
                prefix = f"[ASSISTANT MESSAGE]"
                if ts:
                    prefix += f"\n[ts: {ts}]"
                if path:
                    prefix += f"\n[path: {path}]"
                text = (prefix + "\n" + (text or "")).strip()
            elif btype == "react.notes":
                if isinstance(text, str):
                    ts_line = _ts_line(ts)
                    if ts_line:
                        text = f"{ts_line}\n[AI Agent say]: {text}".strip()
                    else:
                        text = f"[AI Agent say]: {text}".strip()
            elif btype == "system.message":
                prefix = f"[SYSTEM MESSAGE]"
                if ts:
                    prefix += f"\n[ts: {ts}]"
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
                if path:
                    prefix += f"\n[path: {path}]"
                phys = (meta.get("physical_path") or "").strip()
                if phys:
                    prefix += f"\n[physical_path: {phys}]"
                summary = (meta.get("summary") or "").strip()
                if summary:
                    prefix += f"\nsummary: {summary}"
                text = prefix
            elif btype == "user.attachment.text":
                prefix = "[USER ATTACHMENT TEXT]"
                if path:
                    prefix += f"\n[path: {path}]"
                text = (prefix + "\n" + (text or "")).strip()
            elif btype == "react.tool.call" and isinstance(text, str):
                payload = _maybe_parse_json(text) if (b.get("mime") or "").strip() == "application/json" else None
                tool_id = ""
                tool_call_id = ""
                params = None
                if isinstance(payload, dict):
                    tool_id = (payload.get("tool_id") or "").strip()
                    tool_call_id = (payload.get("tool_call_id") or "").strip()
                    params = payload.get("params")
                if not tool_call_id:
                    tool_call_id = (b.get("call_id") or "").strip()
                if not tool_call_id:
                    tool_call_id = _call_id_from_path(path)
                short_id = _short_tc_id(tool_call_id)
                lines = [""]
                ts_line = _ts_line(ts)
                if ts_line:
                    lines.append(ts_line)
                header = f"[TOOL CALL {short_id}]"
                if tool_id:
                    header += f" {tool_id}"
                lines.append(header)
                if isinstance(params, dict):
                    params_out = dict(params)
                    if isinstance(params_out.get("content"), str):
                        content_val = params_out.get("content") or ""
                        if len(content_val) > 100:
                            file_path = _derive_file_context_path(
                                turn_id=str(b.get("turn_id") or ""),
                                raw_path=str(params_out.get("path") or ""),
                            )
                            suffix = f"... [truncated; see {file_path}]" if file_path else "... [truncated]"
                            params_out["content"] = content_val[:100] + suffix
                    lines.append(json.dumps(params_out, ensure_ascii=False, indent=2))
                elif params is not None:
                    lines.append(json.dumps(params, ensure_ascii=False, indent=2))
                text = "\n".join([l for l in lines if l is not None]).strip()
            elif btype == "react.tool.result":
                mime_val = (b.get("mime") or "").strip()
                tool_call_id = (b.get("call_id") or "").strip()
                if not tool_call_id:
                    tool_call_id = _call_id_from_path(path)
                short_id = _short_tc_id(tool_call_id)
                if mime_val == "application/json" and isinstance(text, str):
                    payload = _maybe_parse_json(text)
                    if isinstance(payload, dict) and payload.get("artifact_path") and payload.get("tool_id"):
                        tool_id = (payload.get("tool_id") or "").strip()
                        # Skip noisy meta blocks for read/search/fetch; content blocks will carry the info.
                        if tool_id in {"react.read", "web_tools.web_search", "web_tools.web_fetch"}:
                            text = ""
                        else:
                            lines = []
                            ts_line = _ts_line(ts)
                            if ts_line:
                                lines.append(ts_line)
                            header = f"[TOOL RESULT {short_id}]"
                            if tool_id:
                                header += f" {tool_id}"
                            lines.append(header)
                            ap = (payload.get("artifact_path") or "").strip()
                            pp = (payload.get("physical_path") or "").strip()
                            kind = (payload.get("kind") or "").strip()
                            visibility = (payload.get("visibility") or "").strip()
                            description = (payload.get("description") or "").strip()
                            if kind == "file" and visibility == "external":
                                lines.append("[Produced files]")
                                file_line = pp or ap
                                if file_line:
                                    lines.append(f"- {file_line}")
                                if description:
                                    lines.append(f"Description: {description}")
                            # Emit a cleaned meta JSON (no hosted_uri/rn/key/local_path).
                            meta_out = {
                                k: v
                                for k, v in payload.items()
                                if k in {
                                    "artifact_path",
                                    "physical_path",
                                    "mime",
                                    "kind",
                                    "visibility",
                                    "channel",
                                    "tool_id",
                                    "tool_call_id",
                                    "edited",
                                    "ts",
                                    "size_bytes",
                                    "description",
                                    "write_warning",
                                    "sources_used",
                                    "tokens",
                                    "error",
                                }
                                and v is not None
                                and (not isinstance(v, str) or v.strip() != "")
                            }
                            if meta_out:
                                lines.append(json.dumps(meta_out, ensure_ascii=False, indent=2))
                            if payload.get("error"):
                                err = payload.get("error") or {}
                                code = err.get("code") or "error"
                                msg = err.get("message") or ""
                                lines.append(f"error: {code} {msg}".strip())
                            text = "\n".join(lines).strip()
                    elif isinstance(payload, dict) and "paths" in payload and "total_tokens" in payload:
                        lines = []
                        ts_line = _ts_line(ts)
                        if ts_line:
                            lines.append(ts_line)
                        lines.append(f"[TOOL RESULT {short_id}] read summary")
                        paths = payload.get("paths") or []
                        if isinstance(paths, list) and paths:
                            for row in paths:
                                if not isinstance(row, dict):
                                    continue
                                p = row.get("path")
                                if not p:
                                    continue
                                tok = row.get("tokens")
                                if tok:
                                    lines.append(f"- {p} (tokens={tok})")
                                else:
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
                        header = f"[TOOL RESULT {short_id}]"
                        if range_path:
                            header += f" artifact_path: {range_path}"
                        lines.append(header)
                        # keep payload as-is
                        lines.append(text)
                        text = "\n".join([l for l in lines if l]).strip()
                    elif isinstance(payload, dict):
                        # Generic JSON payload (e.g. legacy fetch map): add tool result header.
                        lines = []
                        ts_line = _ts_line(ts)
                        if ts_line:
                            lines.append(ts_line)
                        lines.append(f"[TOOL RESULT {short_id}]")
                        lines.append(text)
                        text = "\n".join([l for l in lines if l]).strip()
                elif isinstance(text, str):
                    # Non-JSON content blocks: prefix with tool result header.
                    lines = []
                    ts_line = _ts_line(ts)
                    if ts_line:
                        lines.append(ts_line)
                    header = f"[TOOL RESULT {short_id}]"
                    if path:
                        header += f" path: {path}"
                    lines.append(header)
                    lines.append(text)
                    text = "\n".join([l for l in lines if l]).strip()
            base64 = b.get("base64") or b.get("data")
            mime = (b.get("mime") or b.get("media_type") or "").strip() or None
            if btype == "react.note" and isinstance(text, str):
                text = "[INTERNAL NOTE]\n" + text

            emitted: List[Dict[str, Any]] = []
            if text:
                emitted.append({"type": "text", "text": str(text)})

            if base64:
                is_image = mime.startswith("image/") if mime else False
                if is_image:
                    emitted.append({"type": "image", "data": base64, "media_type": mime or "image/png"})
                else:
                    emitted.append({"type": "document", "data": base64, "media_type": mime or "application/pdf"})

            if not emitted:
                continue

            if cache:
                emitted[-1]["cache"] = True
            out.extend(emitted)
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
                media = b.get("media_type") or "image/png"
                data_len = len((b.get("data") or ""))
                lines.append(prefix + f"<image media_type={media} b64_len={data_len}>")
            elif btype == "document":
                media = b.get("media_type") or "application/pdf"
                data_len = len((b.get("data") or ""))
                lines.append(prefix + f"<document media_type={media} b64_len={data_len}>")
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
            sources_pool=_compact_sources_pool_for_index(self.sources_pool or []),
            conversation_title=self.conversation_title,
            conversation_started_at=self.conversation_started_at,
            cache_last_touch_at=self.cache_last_touch_at,
            cache_last_ttl_seconds=self.cache_last_ttl_seconds,
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
                content=sources_payload,
                content_str=sources_text,
                extra_tags=extra_tags or None,
            )
        except Exception:
            pass
