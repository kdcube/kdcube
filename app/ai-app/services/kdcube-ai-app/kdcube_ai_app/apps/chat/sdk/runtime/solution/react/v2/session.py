# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import time
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import ToolCallView
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.util import token_count

logger = logging.getLogger(__name__)


DEFAULT_MAX_TEXT_CHARS = 4000
DEFAULT_MAX_FIELD_CHARS = 1000
DEFAULT_MAX_LIST_ITEMS = 50
DEFAULT_MAX_DICT_KEYS = 80
DEFAULT_MAX_BASE64_CHARS = 4000
DEFAULT_KEEP_RECENT_IMAGES = 2
DEFAULT_KEEP_INTACT_TURNS = 2
DEFAULT_MAX_IMAGE_PDF_B64_SUM = 1_000_000


@dataclass
class TruncationConfig:
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS
    max_list_items: int = DEFAULT_MAX_LIST_ITEMS
    max_dict_keys: int = DEFAULT_MAX_DICT_KEYS
    max_base64_chars: int = DEFAULT_MAX_BASE64_CHARS
    keep_recent_images: int = DEFAULT_KEEP_RECENT_IMAGES
    max_image_pdf_b64_sum: int = DEFAULT_MAX_IMAGE_PDF_B64_SUM


def build_truncation_config(runtime: Any, cfg: Optional[TruncationConfig] = None) -> TruncationConfig:
    cfg = cfg or TruncationConfig()
    if runtime is None:
        return cfg
    session = getattr(runtime, "session", None)

    def _maybe_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except Exception:
            return None

    def _apply(name: str, minimum: int = 1) -> None:
        raw = getattr(session, name, None) if session is not None else None
        if raw is None:
            raw = getattr(runtime, name, None)
        if raw is None:
            return
        val = _maybe_int(raw)
        if val is None:
            return
        if val < minimum:
            return
        attr = name.replace("cache_truncation_", "")
        setattr(cfg, attr, val)

    _apply("cache_truncation_max_text_chars")
    _apply("cache_truncation_max_field_chars")
    _apply("cache_truncation_max_list_items")
    _apply("cache_truncation_max_dict_keys")
    _apply("cache_truncation_max_base64_chars")
    _apply("cache_truncation_keep_recent_images", minimum=0)
    _apply("cache_truncation_max_image_pdf_b64_sum")
    return cfg


def _parse_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except Exception:
        return None


def _truncate_str(text: str, limit: int) -> Tuple[str, bool]:
    if not isinstance(text, str):
        return str(text), True
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit] + "...", True


def _is_ref_str(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("ref:")


def _truncate_value(
    value: Any,
    *,
    max_chars: int,
    max_list_items: int,
    max_dict_keys: int,
    skip_ref: bool,
) -> Tuple[Any, bool]:
    truncated = False
    if isinstance(value, str):
        if skip_ref and _is_ref_str(value):
            return value, False
        out, did = _truncate_str(value, max_chars)
        return out, did
    if isinstance(value, list):
        out_list: List[Any] = []
        for idx, item in enumerate(value):
            if idx >= max_list_items:
                out_list.append(f"... ({len(value) - max_list_items} more)")
                truncated = True
                break
            item_out, did = _truncate_value(
                item,
                max_chars=max_chars,
                max_list_items=max_list_items,
                max_dict_keys=max_dict_keys,
                skip_ref=skip_ref,
            )
            if did:
                truncated = True
            out_list.append(item_out)
        return out_list, truncated
    if isinstance(value, dict):
        out_dict: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= max_dict_keys:
                out_dict["..."] = f"{len(value) - max_dict_keys} more keys"
                truncated = True
                break
            item_out, did = _truncate_value(
                item,
                max_chars=max_chars,
                max_list_items=max_list_items,
                max_dict_keys=max_dict_keys,
                skip_ref=skip_ref,
            )
            if did:
                truncated = True
            out_dict[key] = item_out
        return out_dict, truncated
    return value, False


def _mark_truncated(payload: Any) -> Any:
    if isinstance(payload, dict):
        out = dict(payload)
        out["truncated"] = True
        return out
    return payload


def _format_json(payload: Any, truncated: bool) -> str:
    if truncated:
        payload = _mark_truncated(payload)
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        return str(payload)


def _truncate_params(params: Any, cfg: TruncationConfig) -> Tuple[Any, bool]:
    return _truncate_value(
        params,
        max_chars=cfg.max_field_chars,
        max_list_items=cfg.max_list_items,
        max_dict_keys=cfg.max_dict_keys,
        skip_ref=True,
    )


def _truncate_payload(payload: Any, cfg: TruncationConfig) -> Tuple[Any, bool]:
    return _truncate_value(
        payload,
        max_chars=cfg.max_text_chars,
        max_list_items=cfg.max_list_items,
        max_dict_keys=cfg.max_dict_keys,
        skip_ref=False,
    )


def _truncate_text_block(text: str, cfg: TruncationConfig) -> Tuple[str, bool]:
    if not isinstance(text, str):
        return str(text), True
    if len(text) <= cfg.max_text_chars:
        return text, False
    return text[: cfg.max_text_chars] + "...", True


def _coerce_tool_id(block: Dict[str, Any], payload: Optional[Dict[str, Any]]) -> str:
    tool_id = (block.get("tool_id") or "").strip()
    if not tool_id and isinstance(payload, dict):
        tool_id = str(payload.get("tool_id") or "").strip()
    return tool_id


class ReactReadView(ToolCallView):
    tool_id = "react.read"

    def build_call_replacement(
        self,
        *,
        tool_call_block: Dict[str, Any],
        payload: Dict[str, Any],
        cfg: Optional[TruncationConfig] = None,
    ) -> str:
        params = payload.get("params")
        if params is None and isinstance(payload.get("paths"), list):
            params = {"paths": payload.get("paths")}
        if not isinstance(params, dict):
            params = {"paths": params or []}
        cfg = cfg or TruncationConfig()
        truncated_params, did = _truncate_params(params, cfg)
        out = {
            "tool_id": self.tool_id,
            "tool_call_id": payload.get("tool_call_id") or tool_call_block.get("call_id"),
            "params": truncated_params,
        }
        return _format_json(out, did)

    def build_result_replacement(
        self,
        *,
        tool_result_block: Dict[str, Any],
        payload: Any,
        cfg: Optional[TruncationConfig] = None,
    ) -> str:
        cfg = cfg or TruncationConfig()
        truncated_payload, did = _truncate_payload(payload, cfg)
        out = {
            "tool_id": self.tool_id,
            "tool_call_id": tool_result_block.get("call_id"),
            "result": truncated_payload,
        }
        return _format_json(out, did)


class ReactWriteView(ToolCallView):
    tool_id = "react.write"

    def build_call_replacement(
        self,
        *,
        tool_call_block: Dict[str, Any],
        payload: Dict[str, Any],
        cfg: Optional[TruncationConfig] = None,
    ) -> str:
        params = payload.get("params") if isinstance(payload, dict) else None
        if not isinstance(params, dict):
            params = {}
        cfg = cfg or TruncationConfig()
        truncated_params, did = _truncate_params(params, cfg)
        out = {
            "tool_id": self.tool_id,
            "tool_call_id": payload.get("tool_call_id") or tool_call_block.get("call_id"),
            "params": truncated_params,
        }
        return _format_json(out, did)

    def build_result_replacement(
        self,
        *,
        tool_result_block: Dict[str, Any],
        payload: Any,
        cfg: Optional[TruncationConfig] = None,
    ) -> str:
        cfg = cfg or TruncationConfig()
        truncated_payload, did = _truncate_payload(payload, cfg)
        out = {
            "tool_id": self.tool_id,
            "tool_call_id": tool_result_block.get("call_id"),
            "result": truncated_payload,
        }
        return _format_json(out, did)


class ReactPatchView(ToolCallView):
    tool_id = "react.patch"

    def build_call_replacement(
        self,
        *,
        tool_call_block: Dict[str, Any],
        payload: Dict[str, Any],
        cfg: Optional[TruncationConfig] = None,
    ) -> str:
        params = payload.get("params") if isinstance(payload, dict) else None
        if not isinstance(params, dict):
            params = {}
        cfg = cfg or TruncationConfig()
        truncated_params, did = _truncate_params(params, cfg)
        out = {
            "tool_id": self.tool_id,
            "tool_call_id": payload.get("tool_call_id") or tool_call_block.get("call_id"),
            "params": truncated_params,
        }
        return _format_json(out, did)

    def build_result_replacement(
        self,
        *,
        tool_result_block: Dict[str, Any],
        payload: Any,
        cfg: Optional[TruncationConfig] = None,
    ) -> str:
        cfg = cfg or TruncationConfig()
        truncated_payload, did = _truncate_payload(payload, cfg)
        out = {
            "tool_id": self.tool_id,
            "tool_call_id": tool_result_block.get("call_id"),
            "result": truncated_payload,
        }
        return _format_json(out, did)


class DefaultToolView(ToolCallView):
    def __init__(self, tool_id: Optional[str] = None) -> None:
        super().__init__(tool_id=tool_id)

    def build_call_replacement(
        self,
        *,
        tool_call_block: Dict[str, Any],
        payload: Dict[str, Any],
        cfg: Optional[TruncationConfig] = None,
    ) -> str:
        params = payload.get("params") if isinstance(payload, dict) else None
        if not isinstance(params, dict) and isinstance(params, list):
            params = {"items": params}
        if not isinstance(params, dict):
            params = {}
        cfg = cfg or TruncationConfig()
        truncated_params, did = _truncate_params(params, cfg)
        out = {
            "tool_id": self.tool_id,
            "tool_call_id": payload.get("tool_call_id") or tool_call_block.get("call_id"),
            "params": truncated_params,
        }
        notes = payload.get("notes")
        if isinstance(notes, str) and notes.strip():
            note_out, note_trunc = _truncate_str(notes, cfg.max_field_chars)
            out["notes"] = note_out
            did = did or note_trunc
        return _format_json(out, did)

    def build_result_replacement(
        self,
        *,
        tool_result_block: Dict[str, Any],
        payload: Any,
        cfg: Optional[TruncationConfig] = None,
    ) -> str:
        cfg = cfg or TruncationConfig()

        if tools_insights.is_search_tool(self.tool_id) and isinstance(payload, list):
            trimmed: List[Dict[str, Any]] = []
            truncated = False
            for item in payload[: cfg.max_list_items]:
                if not isinstance(item, dict):
                    continue
                entry = {
                    "sid": item.get("sid"),
                    "url": item.get("url") or item.get("link"),
                    "title": item.get("title"),
                    "text": item.get("text"),
                }
                if isinstance(entry.get("text"), str):
                    entry["text"], did = _truncate_str(entry["text"], cfg.max_text_chars)
                    truncated = truncated or did
                trimmed.append(entry)
            if len(payload) > cfg.max_list_items:
                trimmed.append({"...": f"{len(payload) - cfg.max_list_items} more"})
                truncated = True
            out = {
                "tool_id": self.tool_id,
                "tool_call_id": tool_result_block.get("call_id"),
                "result": trimmed,
            }
            return _format_json(out, truncated)

        if tools_insights.is_fetch_uri_content_tool(self.tool_id) and isinstance(payload, dict):
            trimmed: Dict[str, Any] = {}
            truncated = False
            for url, val in payload.items():
                if not isinstance(val, dict):
                    continue
                entry = {
                    "url": url,
                    "title": val.get("title"),
                    "content": val.get("content"),
                }
                if isinstance(entry.get("content"), str):
                    entry["content"], did = _truncate_str(entry["content"], cfg.max_text_chars)
                    truncated = truncated or did
                trimmed[url] = entry
                if len(trimmed) >= cfg.max_dict_keys:
                    truncated = True
                    break
            out = {
                "tool_id": self.tool_id,
                "tool_call_id": tool_result_block.get("call_id"),
                "result": trimmed,
            }
            return _format_json(out, truncated)

        truncated_payload, did = _truncate_payload(payload, cfg)
        out = {
            "tool_id": self.tool_id,
            "tool_call_id": tool_result_block.get("call_id"),
            "result": truncated_payload,
        }
        return _format_json(out, did)


VIEW_REGISTRY: Dict[str, ToolCallView] = {
    "react.read": ReactReadView(),
    "react.write": ReactWriteView(),
    "react.patch": ReactPatchView(),
}


def _get_view(tool_id: str) -> ToolCallView:
    return VIEW_REGISTRY.get(tool_id) or DefaultToolView(tool_id=tool_id)


def _extract_turn_id(block: Dict[str, Any]) -> str:
    return str(block.get("turn_id") or block.get("turn") or "").strip()


def _recent_turn_ids(blocks: List[Dict[str, Any]], keep_recent_turns: int) -> set[str]:
    if keep_recent_turns <= 0:
        return set()
    seen: List[str] = []
    for blk in reversed(blocks):
        tid = _extract_turn_id(blk)
        if not tid or tid in seen:
            continue
        seen.append(tid)
        if len(seen) >= keep_recent_turns:
            break
    return set(seen)


def _estimate_text_tokens(text: str) -> int:
    if not isinstance(text, str):
        return 0
    try:
        return token_count(text)
    except Exception:
        return max(1, int(len(text) / 4))


def _build_file_replacement(block: Dict[str, Any]) -> str:
    path = (block.get("path") or "").strip()
    mime = (block.get("mime") or "").strip()
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    size = 0
    if isinstance(block.get("text"), str):
        size = len(block.get("text") or "")
    elif isinstance(block.get("base64"), str):
        size = len(block.get("base64") or "")
    if not mime:
        mime = (meta.get("mime") or "").strip()
    summary = (meta.get("summary") or meta.get("description") or "").strip()
    parts = [
        "[TRUNCATED FILE]",
        f"path={path}" if path else "",
        f"mime={mime}" if mime else "",
        f"size={size}" if size else "",
    ]
    parts = [p for p in parts if p]
    if summary:
        parts.append(f"summary={summary}")
    return " ".join(parts)


def _build_generic_replacement(block: Dict[str, Any], cfg: TruncationConfig) -> str:
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        trimmed, _ = _truncate_text_block(text, cfg)
        return "[TRUNCATED] " + trimmed
    return "[TRUNCATED]"


def _build_prune_message_text(ttl_seconds: int) -> str:
    return (
        "[SYSTEM MESSAGE] Context was pruned because the session TTL "
        f"({ttl_seconds}s) was exceeded. Some blocks were hidden. "
        "Use react.read(path) to restore a logical path (fi:/ar:/so:/sk:)."
    )


def apply_cache_ttl_pruning(
    *,
    timeline: Any,
    ttl_seconds: int,
    buffer_seconds: int = 0,
    keep_recent_turns: int = 10,
    keep_recent_intact_turns: int = DEFAULT_KEEP_INTACT_TURNS,
    cfg: Optional[TruncationConfig] = None,
) -> Dict[str, Any]:
    """
    TTL-aware pruning for timeline blocks.
    - Last <keep_recent_turns> turns are kept visible.
    - Older turns are hidden with replacement text.
    """
    cfg = build_truncation_config(getattr(timeline, "runtime", None), cfg)
    ttl_seconds = int(ttl_seconds or 0)
    buffer_seconds = max(0, int(buffer_seconds or 0))
    if not timeline:
        return {"status": "disabled"}
    try:
        timeline.cache_last_ttl_seconds = ttl_seconds
    except Exception:
        pass
    if ttl_seconds <= 0:
        return {"status": "disabled"}

    now = int(time.time())
    last_touch = getattr(timeline, "cache_last_touch_at", None)
    try:
        last_touch_val = int(last_touch) if last_touch is not None else None
    except Exception:
        last_touch_val = None

    # Arm cache tracking on first use.
    if last_touch_val is None:
        timeline.cache_last_touch_at = now
        return {"status": "armed"}

    effective_ttl = max(0, ttl_seconds - buffer_seconds)
    expired = (now - last_touch_val) >= effective_ttl
    if not expired:
        timeline.cache_last_touch_at = now
        return {"status": "fresh"}

    blocks: List[Dict[str, Any]] = list(getattr(timeline, "blocks", []) or [])
    if not blocks:
        timeline.cache_last_touch_at = now
        return {"status": "empty"}

    all_turns = _recent_turn_ids(blocks, len(blocks) or 0)
    turn_count = len(all_turns)
    skip_old_turns = bool(ttl_seconds and keep_recent_turns and turn_count and keep_recent_turns >= turn_count)

    def _estimate_blocks_tokens_safe(b: List[Dict[str, Any]]) -> int:
        try:
            return int(getattr(timeline, "_estimate_blocks_tokens")(b))
        except Exception:
            total = 0
            for blk in b or []:
                if not isinstance(blk, dict):
                    continue
                txt = blk.get("text")
                if isinstance(txt, str):
                    total += len(txt)
                base64 = blk.get("base64")
                if isinstance(base64, str):
                    total += len(base64)
            return max(1, int(total / 4))

    before_blocks = len(blocks)
    before_tokens = _estimate_blocks_tokens_safe(blocks)
    if skip_old_turns:
        try:
            logger.info(
                "[cache_ttl_prune] ttl=%ss buffer=%ss last_touch=%s now=%s "
                "blocks=%s turns=%s keep_recent_turns=%s >= turns, skipping old-turn pruning",
                int(ttl_seconds or 0),
                int(buffer_seconds or 0),
                last_touch_val,
                now,
                len(blocks),
                turn_count,
                int(keep_recent_turns or 0),
            )
        except Exception:
            pass

    recent_turns = _recent_turn_ids(blocks, keep_recent_turns)
    intact_turns = _recent_turn_ids(blocks, keep_recent_intact_turns)
    if intact_turns:
        recent_turns = set(recent_turns) | set(intact_turns)

    call_meta: Dict[str, Dict[str, Any]] = {}
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "") != "react.tool.call":
            continue
        payload = _parse_json(blk.get("text") or "") or {}
        call_id = (blk.get("call_id") or payload.get("tool_call_id") or "").strip()
        tool_id = _coerce_tool_id(blk, payload)
        if call_id:
            call_meta[call_id] = {"tool_id": tool_id, "payload": payload}

    # Image/PDF keep set (only within recent turns)
    image_candidates: List[Tuple[int, str, int]] = []
    for idx, blk in enumerate(blocks):
        if not isinstance(blk, dict):
            continue
        path = (blk.get("path") or "").strip()
        if not path or not path.startswith("fi:"):
            continue
        if _extract_turn_id(blk) not in recent_turns:
            continue
        base64 = blk.get("base64")
        if not isinstance(base64, str):
            continue
        mime = (blk.get("mime") or "").strip().lower()
        if mime.startswith("image/") or mime == "application/pdf":
            image_candidates.append((idx, path, len(base64)))

    image_candidates.sort(key=lambda it: it[0], reverse=True)
    keep_image_paths: set[str] = set()
    total_b64 = 0
    for _, path, size in image_candidates:
        if path in keep_image_paths:
            continue
        if len(keep_image_paths) >= cfg.keep_recent_images:
            break
        if keep_image_paths and total_b64 + size > cfg.max_image_pdf_b64_sum:
            break
        keep_image_paths.add(path)
        total_b64 += size

    skip_types = {"turn.header", "conv.range.summary"}

    # Build replacements for pruned turns (reverse order for most recent per path).
    path_replacements: Dict[str, str] = {}
    for blk in reversed(blocks):
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "") in skip_types:
            continue
        path = (blk.get("path") or "").strip()
        if not path or path in path_replacements:
            continue
        turn_id = _extract_turn_id(blk)
        if skip_old_turns:
            break
        if turn_id and turn_id in recent_turns:
            continue

        btype = (blk.get("type") or "").strip()
        if btype == "react.tool.call":
            payload = _parse_json(blk.get("text") or "") or {}
            tool_id = _coerce_tool_id(blk, payload)
            view = _get_view(tool_id)
            rep = view.build_call_replacement(tool_call_block=blk, payload=payload, cfg=cfg)
        elif btype == "react.tool.result" and path.startswith("tc:"):
            payload = _parse_json(blk.get("text") or "") if isinstance(blk.get("text"), str) else None
            call_id = (blk.get("call_id") or (payload or {}).get("tool_call_id") or "").strip()
            tool_id = ""
            if call_id and call_id in call_meta:
                tool_id = call_meta[call_id].get("tool_id") or ""
            if not tool_id and isinstance(payload, dict):
                tool_id = str(payload.get("tool_id") or "")
            view = _get_view(tool_id)
            rep = view.build_result_replacement(tool_result_block=blk, payload=payload or {}, cfg=cfg)
        elif path.startswith("fi:"):
            rep = _build_file_replacement(blk)
        else:
            rep = _build_generic_replacement(blk, cfg)

        path_replacements[path] = rep

    hidden_paths: List[str] = []
    if not skip_old_turns:
        for path, rep in path_replacements.items():
            try:
                timeline.hide_paths([path], rep)
                hidden_paths.append(path)
            except Exception:
                pass

    hidden_recent_paths: set[str] = set()

    # Hide oversized images/PDFs in recent-but-not-intact turns.
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "") in skip_types:
            continue
        if blk.get("hidden") or (isinstance(blk.get("meta"), dict) and blk.get("meta", {}).get("hidden")):
            continue
        path = (blk.get("path") or "").strip()
        if not path:
            continue
        turn_id = _extract_turn_id(blk)
        if not skip_old_turns:
            if turn_id and turn_id not in recent_turns:
                continue
        if turn_id and turn_id in intact_turns:
            continue

        base64 = blk.get("base64")
        mime = (blk.get("mime") or "").strip().lower()
        if not isinstance(base64, str):
            continue
        if mime.startswith("image/") or mime == "application/pdf":
            if path not in keep_image_paths and path not in hidden_recent_paths:
                rep = _build_file_replacement(blk)
                try:
                    timeline.hide_paths([path], rep)
                    hidden_recent_paths.add(path)
                except Exception:
                    pass
            continue
        if len(base64) > cfg.max_base64_chars and path not in hidden_recent_paths:
            rep = _build_file_replacement(blk)
            try:
                timeline.hide_paths([path], rep)
                hidden_recent_paths.add(path)
            except Exception:
                pass
            continue

    if hidden_recent_paths:
        for path in sorted(hidden_recent_paths):
            if path not in hidden_paths:
                hidden_paths.append(path)

    try:
        timeline.blocks = list(blocks)
        timeline.update_timestamp()
    except Exception:
        pass

    timeline.cache_last_touch_at = now
    after_blocks = len(blocks)
    after_tokens = _estimate_blocks_tokens_safe(blocks)
    try:
        ttl_msg = _build_prune_message_text(ttl_seconds)
        pruned_tokens = max(0, (before_tokens or 0) - (after_tokens or 0))
        had_effect = (
            bool(hidden_paths)
            or bool(hidden_recent_paths)
            or before_blocks != after_blocks
            or pruned_tokens > 0
        )
        if not had_effect:
            return {
                "status": "no_effect",
                "hidden_paths": hidden_paths,
                "skip_old_turns": bool(skip_old_turns),
                "truncated_blocks": 0,
            }
        # One-time announce message (after budget in announce stack).
        if isinstance(getattr(timeline, "announce_blocks", None), list):
            timeline.announce_blocks.append({"text": ttl_msg, "type": "system.message"})
        # Persist a timeline block describing the prune event.
        turn_id = (getattr(getattr(timeline, "runtime", None), "turn_id", "") or "")
        should_add = True
        for blk in reversed(blocks):
            if not isinstance(blk, dict):
                continue
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            if meta.get("kind") == "cache_ttl_pruned" and blk.get("turn_id") == turn_id:
                should_add = False
                break
        if should_add:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            blocks.append({
                "type": "system.message",
                "author": "system",
                "turn_id": turn_id,
                "ts": ts,
                "mime": "text/markdown",
                "path": f"ar:{turn_id}.system.message.cache_pruned" if turn_id else "",
                "text": ttl_msg,
                "meta": {
                    "kind": "cache_ttl_pruned",
                    "ttl_seconds": int(ttl_seconds or 0),
                },
            })
            timeline.blocks = list(blocks)
            timeline.update_timestamp()
    except Exception:
        pass
    try:
        logger.info(
            "[cache_ttl_prune] ttl=%ss buffer=%ss last_touch=%s now=%s "
            "blocks=%s->%s tokens=%s->%s hidden_paths=%s hidden_recent=%s "
            "keep_recent_turns=%s keep_intact=%s skip_old_turns=%s",
            int(ttl_seconds or 0),
            int(buffer_seconds or 0),
            last_touch_val,
            now,
            before_blocks,
            after_blocks,
            before_tokens,
            after_tokens,
            len(hidden_paths),
            len(hidden_recent_paths),
            int(keep_recent_turns or 0),
            int(keep_recent_intact_turns or 0),
            bool(skip_old_turns),
        )
    except Exception:
        pass
    return {
        "status": "pruned_light" if skip_old_turns else "pruned",
        "hidden_paths": hidden_paths,
        "skip_old_turns": bool(skip_old_turns),
        "truncated_blocks": 0,
    }
