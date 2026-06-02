# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import time
import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import ToolCallView
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.common import event_source_pipeline_enabled
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.projection import (
    apply_event_source_transformers,
    clear_timeline_segment_marks,
    patch_timeline_segment_marks,
)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.util import token_count

logger = logging.getLogger(__name__)


DEFAULT_MAX_TEXT_CHARS = 4000
DEFAULT_MAX_FIELD_CHARS = 1000
DEFAULT_MAX_LIST_ITEMS = 50
DEFAULT_MAX_DICT_KEYS = 80
DEFAULT_MAX_BASE64_CHARS = 4000
DEFAULT_MAX_TOOL_TEXT_CHARS = 400
DEFAULT_KEEP_RECENT_IMAGES = 2
DEFAULT_KEEP_INTACT_TURNS = 1
DEFAULT_MAX_IMAGE_PDF_B64_SUM = 1_000_000
DEFAULT_REPLACEMENT_MAX_TOKENS = 240

SUPPRESS_OLD_TTL_REPLACEMENT_TYPES = {
    "react.round.start",
    "react.thinking",
    "react.notes",
    "react.notice",
    "stage.suggested_followups",
}


@dataclass
class TruncationConfig:
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS
    max_list_items: int = DEFAULT_MAX_LIST_ITEMS
    max_dict_keys: int = DEFAULT_MAX_DICT_KEYS
    max_base64_chars: int = DEFAULT_MAX_BASE64_CHARS
    max_tool_text_chars: int = DEFAULT_MAX_TOOL_TEXT_CHARS
    keep_recent_images: int = DEFAULT_KEEP_RECENT_IMAGES
    max_image_pdf_b64_sum: int = DEFAULT_MAX_IMAGE_PDF_B64_SUM
    replacement_max_tokens: int = DEFAULT_REPLACEMENT_MAX_TOKENS


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
    _apply("cache_truncation_max_tool_text_chars")
    _apply("cache_truncation_keep_recent_images", minimum=0)
    _apply("cache_truncation_max_image_pdf_b64_sum")
    _apply("cache_truncation_replacement_max_tokens")
    return cfg


def _tool_result_cfg(cfg: Optional[TruncationConfig]) -> TruncationConfig:
    cfg = cfg or TruncationConfig()
    try:
        max_tool = int(getattr(cfg, "max_tool_text_chars", DEFAULT_MAX_TOOL_TEXT_CHARS))
    except Exception:
        max_tool = DEFAULT_MAX_TOOL_TEXT_CHARS
    if max_tool <= 0:
        return cfg
    return replace(cfg, max_text_chars=max_tool, max_field_chars=min(cfg.max_field_chars, max_tool))


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
    return text[:limit] + "...[truncated]", True


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
        cfg = _tool_result_cfg(cfg)
        text = tool_result_block.get("text")
        if isinstance(text, str) and (
            "[LARGE READ NOT MATERIALIZED]" in text
            or "[READ PREVIEW TRUNCATED]" in text
        ):
            return _compact_replacement_lines(text, cfg) or _truncate_text_block(text, cfg)[0]
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
        cfg = _tool_result_cfg(cfg)
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
        cfg = _tool_result_cfg(cfg)
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
        cfg = _tool_result_cfg(cfg)

        if isinstance(payload, dict) and payload.get("artifact_path") and not (
            payload.get("result") or payload.get("items") or payload.get("hits") or payload.get("paths")
        ):
            truncated_payload, did = _truncate_payload(payload, cfg)
            return _format_json(truncated_payload, did)

        if self.tool_id == "react.memsearch" and isinstance(payload, dict):
            hits = payload.get("hits")
            if isinstance(hits, list):
                trimmed_hits: List[Dict[str, Any]] = []
                truncated = False
                for hit in hits[: cfg.max_list_items]:
                    if not isinstance(hit, dict):
                        continue
                    out_hit: Dict[str, Any] = {
                        "turn_id": hit.get("turn_id"),
                        "score": hit.get("score"),
                        "sim_score": hit.get("sim_score"),
                        "recency_score": hit.get("recency_score"),
                        "matched_via_role": hit.get("matched_via_role"),
                        "source_query": hit.get("source_query"),
                        "ts": hit.get("ts"),
                        "best_turn_id": hit.get("best_turn_id"),
                    }
                    snippets_out: List[Dict[str, Any]] = []
                    snippets = hit.get("snippets")
                    if isinstance(snippets, list):
                        for snip in snippets[: cfg.max_list_items]:
                            if not isinstance(snip, dict):
                                continue
                            snip_out: Dict[str, Any] = {
                                "role": snip.get("role"),
                                "path": snip.get("path"),
                                "ts": snip.get("ts"),
                            }
                            text_val = snip.get("text")
                            if isinstance(text_val, str):
                                text_val, did = _truncate_str(text_val, cfg.max_text_chars)
                                truncated = truncated or did
                                snip_out["text"] = text_val
                            snippets_out.append(snip_out)
                        if len(snippets) > cfg.max_list_items:
                            snippets_out.append({"...": f"{len(snippets) - cfg.max_list_items} more"})
                            truncated = True
                    out_hit["snippets"] = snippets_out
                    trimmed_hits.append(out_hit)
                if len(hits) > cfg.max_list_items:
                    trimmed_hits.append({"...": f"{len(hits) - cfg.max_list_items} more"})
                    truncated = True
                out = {
                    "tool_id": self.tool_id,
                    "tool_call_id": tool_result_block.get("call_id"),
                    "result": {
                        "hits": trimmed_hits,
                        "tokens": payload.get("tokens"),
                    },
                }
                return _format_json(out, truncated)

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
                }
                text_val = item.get("text")
                if isinstance(text_val, str):
                    entry["text"] = text_val
                content_val = item.get("content")
                if isinstance(content_val, str):
                    entry["content"], did = _truncate_str(content_val, cfg.max_text_chars)
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

        if tools_insights.is_fetch_uri_content_tool(self.tool_id) and isinstance(payload, list):
            trimmed_list: List[Dict[str, Any]] = []
            truncated = False
            for item in payload[: cfg.max_list_items]:
                if not isinstance(item, dict):
                    continue
                entry = {
                    "sid": item.get("sid"),
                    "url": item.get("url") or item.get("link"),
                    "title": item.get("title"),
                }
                text_val = item.get("text")
                if isinstance(text_val, str):
                    entry["text"] = text_val
                content_val = item.get("content")
                if isinstance(content_val, str):
                    entry["content"], did = _truncate_str(content_val, cfg.max_text_chars)
                    truncated = truncated or did
                trimmed_list.append(entry)
            if len(payload) > cfg.max_list_items:
                trimmed_list.append({"...": f"{len(payload) - cfg.max_list_items} more"})
                truncated = True
            out = {
                "tool_id": self.tool_id,
                "tool_call_id": tool_result_block.get("call_id"),
                "result": trimmed_list,
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
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    if meta.get("kind") == "cache_ttl_pruned":
        return "[cache prune notice hidden; logical paths still exist. Use visible content first, react.read(paths=[path, ..]) only if needed.]"
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        trimmed, _ = _truncate_text_block(text, cfg)
        return "[TRUNCATED] " + trimmed
    return "[TRUNCATED]"


def _is_turn_finalize_stats_block(block: Dict[str, Any]) -> bool:
    if not isinstance(block, dict):
        return False
    if (block.get("type") or "").strip() == "react.turn.finalize":
        return True
    text = block.get("text")
    return isinstance(text, str) and "Turn completed with these stats" in text and "[BUDGET]" in text


def _token_count_safe(text: str) -> int:
    try:
        return token_count(text or "")
    except Exception:
        return max(1, int(len(text or "") / 4))


def _ttl_retrieval_stub(
    block: Dict[str, Any],
    *,
    call_meta: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    path = (block.get("path") or "").strip()
    btype = (block.get("type") or "").strip() or "block"
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    call_id = (block.get("call_id") or meta.get("tool_call_id") or "").strip()
    tool_id = (block.get("tool_id") or meta.get("tool_id") or "").strip()
    if call_id and not tool_id and isinstance(call_meta, dict):
        tool_id = str((call_meta.get(call_id) or {}).get("tool_id") or "").strip()
    if not tool_id and btype in {"react.tool.call", "react.tool.result"}:
        payload = _parse_json(block.get("text") or "") if isinstance(block.get("text"), str) else None
        if isinstance(payload, dict):
            tool_id = str(payload.get("tool_id") or "").strip()

    if btype == "user.prompt":
        kind = "user"
    elif btype == "assistant.completion":
        kind = "assistant"
    elif btype == "react.tool.call":
        kind = "tool_call"
    elif btype == "react.tool.result":
        kind = "tool_result"
    elif path.startswith("fi:"):
        kind = "file"
    elif path.startswith("sk:"):
        kind = "skill"
    elif path.startswith("so:"):
        kind = "source"
    else:
        kind = btype.replace(".", "_") or "block"

    parts = [f"{kind}:"]
    if path:
        parts.append(f"path={path}")
    if tool_id:
        parts.append(f"tool={tool_id}")
    if call_id:
        parts.append(f"call_id={call_id}")
    mime = (block.get("mime") or block.get("media_type") or meta.get("mime") or "").strip()
    if mime:
        parts.append(f"mime={mime}")
    read_range = meta.get("read_range") if isinstance(meta.get("read_range"), dict) else None
    if read_range:
        if read_range.get("range_kind") == "lines":
            parts.append(f"range=lines:{read_range.get('line_start')}-{read_range.get('line_end')}")
        elif read_range.get("range_kind") == "text_symbols":
            offset = int(read_range.get("offset_text_symbols") or 0)
            visible = int(read_range.get("visible_text_symbols") or 0)
            parts.append(f"range=text_symbols:{offset}-{offset + max(0, visible)}")
    return " ".join(parts)


_TTL_REPLACEMENT_KEEP_KEYS = (
    "path",
    "path=",
    "tool_id",
    "tool_call_id",
    "call_id",
    "artifact_path",
    "physical_path",
    "filename",
    "mime",
    "bytes:",
    "tokens:",
    "status",
    "error",
    "code",
    "message",
    "where",
    "text_symbols:",
    "line_count:",
    "line_numbers:",
    "lines:",
    "visible_lines:",
    "read_range",
    "range=",
    "visible_read_limit",
    "url",
    "sid",
    "react.read",
)


def _compact_replacement_json(replacement: str, cfg: TruncationConfig) -> Optional[str]:
    payload = _parse_json(replacement)
    if payload is None:
        return None
    max_chars = max(80, min(int(getattr(cfg, "max_tool_text_chars", DEFAULT_MAX_TOOL_TEXT_CHARS)), 320))
    max_items = max(3, min(int(getattr(cfg, "max_list_items", DEFAULT_MAX_LIST_ITEMS)), 8))
    max_keys = max(8, min(int(getattr(cfg, "max_dict_keys", DEFAULT_MAX_DICT_KEYS)), 16))
    trimmed, did = _truncate_value(
        payload,
        max_chars=max_chars,
        max_list_items=max_items,
        max_dict_keys=max_keys,
        skip_ref=True,
    )
    return _format_json(trimmed, True if did else False)


def _compact_replacement_lines(replacement: str, cfg: TruncationConfig) -> str:
    max_line_chars = max(80, min(int(getattr(cfg, "max_tool_text_chars", DEFAULT_MAX_TOOL_TEXT_CHARS)), 320))
    kept: List[str] = []
    seen: set[str] = set()
    for raw in (replacement or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        keep = not kept or any(key in lower for key in _TTL_REPLACEMENT_KEEP_KEYS)
        if not keep:
            continue
        line, _ = _truncate_str(line, max_line_chars)
        if line in seen:
            continue
        kept.append(line)
        seen.add(line)
        if len(kept) >= 12:
            break
    return "\n".join(kept).strip()


def _bound_ttl_replacement(
    *,
    block: Dict[str, Any],
    replacement: str,
    cfg: TruncationConfig,
    call_meta: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Bound replacement text created by automatic TTL pruning.

    Explicit react.hide replacements are intentionally not routed through this
    helper. They must preserve caller-supplied text exactly.
    """
    if not isinstance(replacement, str) or not replacement:
        return replacement
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    call_id = str(block.get("call_id") or meta.get("tool_call_id") or "").strip()
    tool_id = str(block.get("tool_id") or meta.get("tool_id") or "").strip()
    if call_id and not tool_id and isinstance(call_meta, dict):
        tool_id = str((call_meta.get(call_id) or {}).get("tool_id") or "").strip()
    if (block.get("type") or "").strip() == "react.tool.result" and tool_id == "react.rg":
        return _ttl_retrieval_stub(block, call_meta=call_meta)
    try:
        max_tokens = int(getattr(cfg, "replacement_max_tokens", DEFAULT_REPLACEMENT_MAX_TOKENS))
    except Exception:
        max_tokens = DEFAULT_REPLACEMENT_MAX_TOKENS
    max_tokens = max(24, max_tokens)
    requested_tokens = _token_count_safe(replacement)
    original_text = block.get("text") if isinstance(block.get("text"), str) else ""
    original_tokens = _token_count_safe(original_text) if original_text else 0
    growth_limit = max(original_tokens + 64, original_tokens * 2)
    if requested_tokens <= max_tokens and (original_tokens <= 0 or requested_tokens <= growth_limit):
        return replacement

    candidates: List[str] = []
    compact_json = _compact_replacement_json(replacement, cfg)
    if compact_json:
        candidates.append(compact_json)
    compact_lines = _compact_replacement_lines(replacement, cfg)
    if compact_lines:
        candidates.append(compact_lines)
    candidates.append(_ttl_retrieval_stub(block, call_meta=call_meta))

    selected = candidates[-1]
    for candidate in candidates:
        if candidate and _token_count_safe(candidate) <= max_tokens:
            selected = candidate
            break

    path = (block.get("path") or "").strip()
    try:
        logger.info(
            "[cache_ttl_prune.replacement_capped] path=%s requested_tokens=%s stored_tokens=%s max_tokens=%s",
            path,
            requested_tokens,
            _token_count_safe(selected),
            max_tokens,
        )
    except Exception:
        pass
    return selected


def _build_skill_prune_message(path: str) -> str:
    label = (path or "").strip()
    kind = "artifact"
    if label.startswith("sk:"):
        kind = "skill"
    elif label.startswith("so:"):
        kind = "source"
    elif label.startswith("fi:"):
        kind = "file"
    elif label.startswith("ar:"):
        kind = "artifact"
    elif label.startswith("tc:"):
        kind = "tool result"
    if label:
        return (
            f"[content removed by pruning at this location: {kind} {label}. "
            "It may still be visible elsewhere in the current timeline; only use react.read if you cannot see it.]"
        )
    return (
        "[content removed by pruning at this location. It may still be visible elsewhere in the current timeline; "
        "only use react.read if you cannot see it.]"
    )


def _build_prune_message_text(ttl_seconds: int) -> str:
    return (
        "[SYSTEM MESSAGE] Context was pruned because the session TTL "
        f"({ttl_seconds}s) was exceeded. Some blocks were hidden. "
        "Pruning does NOT remove artifacts: their logical paths (fi:/ar:/so:/sk:) still exist. "
        "Do not assume a path must be re-read just because pruning happened: first scan the currently visible timeline. "
        "If the needed content or an ACTIVE 💡 skill block is visible, use it directly. "
        "Use react.read(paths=[path]) only when the exact needed content is not visible."
    )


def _build_search_result_replacement_compact(
    *,
    tool_id: str,
    call_id: str,
    payload: Any,
    cfg: TruncationConfig,
    items_stats: Optional[Dict[str, Any]] = None,
) -> str:
    cfg = _tool_result_cfg(cfg)
    truncated = False

    def _compact_items_meta(stats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(stats, dict) or not stats:
            return {}
        meta: Dict[str, Any] = {}
        for key in (
            "kind",
            "items_count",
            "sids",
            "content_rows",
            "total_content_symbols",
            "total_text_symbols",
            "total_size_bytes",
        ):
            if key in stats:
                meta[key] = stats.get(key)
        compact_items: List[Dict[str, Any]] = []
        for row in (stats.get("items") or [])[: cfg.max_list_items]:
            if not isinstance(row, dict):
                continue
            item: Dict[str, Any] = {}
            for key in (
                "sid",
                "title",
                "url",
                "mime",
                "source_type",
                "has_content",
                "content_symbols",
                "text_symbols",
                "content_length",
                "size_bytes",
                "text_preview",
                "content_preview",
            ):
                val = row.get(key)
                if val not in (None, ""):
                    item[key] = val
            if item:
                compact_items.append(item)
        if compact_items:
            meta["items"] = compact_items
        stats_items = stats.get("items")
        if isinstance(stats_items, list) and len(stats_items) > cfg.max_list_items:
            meta["truncated_items"] = len(stats_items) - cfg.max_list_items
        return meta

    def _compact_item(item: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal truncated
        entry: Dict[str, Any] = {
            "sid": item.get("sid"),
            "url": item.get("url") or item.get("link"),
            "title": item.get("title"),
        }
        # If item refers to a file/attachment, keep the fi: path.
        for key in ("artifact_path", "path"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                entry["path"] = val
                break
        mime = item.get("mime")
        if isinstance(mime, str) and mime.strip():
            entry["mime"] = mime
        text_val = item.get("text")
        if isinstance(text_val, str) and text_val.strip():
            text_val, did = _truncate_str(text_val, cfg.max_text_chars)
            truncated = truncated or did
            entry["text"] = text_val
        content_val = item.get("content")
        if isinstance(content_val, str) and content_val.strip():
            entry["content_symbols"] = len(content_val)
            content_preview, did = _truncate_str(content_val, min(cfg.max_text_chars, 320))
            truncated = truncated or did
            entry["content_preview"] = content_preview
        content_length = item.get("content_length")
        if isinstance(content_length, (int, float)):
            entry["content_length"] = int(content_length)
        return entry

    trimmed: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload[: cfg.max_list_items]:
            if not isinstance(item, dict):
                continue
            trimmed.append(_compact_item(item))
        if len(payload) > cfg.max_list_items:
            trimmed.append({"...": f"{len(payload) - cfg.max_list_items} more"})
            truncated = True
    elif isinstance(payload, dict):
        # some tools return dict keyed by URL
        for _, val in list(payload.items())[: cfg.max_dict_keys]:
            if not isinstance(val, dict):
                continue
            trimmed.append(_compact_item(val))
        if len(payload) > cfg.max_dict_keys:
            trimmed.append({"...": f"{len(payload) - cfg.max_dict_keys} more"})
            truncated = True

    out = {
        "tool_id": tool_id,
        "tool_call_id": call_id,
        "items_meta": _compact_items_meta(items_stats),
        "result": trimmed,
    }
    return _format_json(out, truncated)


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
        btype = (blk.get("type") or "")
        if btype == "react.tool.call":
            payload = _parse_json(blk.get("text") or "") or {}
            meta_local = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            call_id = (blk.get("call_id") or meta_local.get("tool_call_id") or payload.get("tool_call_id") or "").strip()
            tool_id = _coerce_tool_id(blk, payload)
            if call_id:
                call_meta[call_id] = {"tool_id": tool_id, "payload": payload}
            continue

    def _event_source_timeline_segment(block: Dict[str, Any]) -> str:
        turn_id = _extract_turn_id(block)
        current_turn_id = str(getattr(getattr(timeline, "runtime", None), "turn_id", "") or "").strip()
        if current_turn_id and turn_id == current_turn_id:
            return "current"
        if turn_id and turn_id in intact_turns:
            return "intact_recent"
        if turn_id and turn_id in recent_turns:
            return "recent"
        return "old"

    event_policy_applied = False
    try:
        runtime = getattr(timeline, "runtime", None)
        event_sources = getattr(runtime, "event_sources", None)
        if event_sources is not None and event_source_pipeline_enabled(runtime):
            patch_timeline_segment_marks(blocks, timeline_segment_fn=_event_source_timeline_segment)
            apply_event_source_transformers(
                event_sources=event_sources,
                react_phase="timeline_projection",
                timeline_blocks=blocks,
                recent_turns=recent_turns,
                intact_turns=intact_turns,
                keep_recent_turns=keep_recent_turns,
                keep_recent_intact_turns=keep_recent_intact_turns,
                call_meta=call_meta,
                cfg=cfg,
            )
            event_policy_applied = True
    except Exception:
        pass
    finally:
        try:
            clear_timeline_segment_marks(blocks)
        except Exception:
            pass

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

    skip_types = {
        "turn.header",
        "conv.range.summary",
        "conv.working.summary",
        "react.note",
        "react.note.preserved",
        "user.followup",
        "user.followup.preserved",
        "user.steer",
        "user.steer.preserved",
    }

    # Build replacements for pruned turns (reverse order for most recent per path).
    path_replacements: Dict[str, Tuple[Dict[str, Any], str]] = {}
    for blk in reversed(blocks):
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "") in skip_types:
            continue
        path = (blk.get("path") or "").strip()
        turn_id = _extract_turn_id(blk)
        if skip_old_turns:
            break
        if turn_id and turn_id in recent_turns:
            continue
        if not path and _is_turn_finalize_stats_block(blk) and turn_id:
            blk["type"] = "react.turn.finalize"
            blk["path"] = f"ar:{turn_id}.react.turn.finalize"
            path = (blk.get("path") or "").strip()
        if not path or path in path_replacements:
            continue

        btype = (blk.get("type") or "").strip()
        if btype == "react.plan.history":
            continue
        meta_existing = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
        if event_policy_applied and (blk.get("hidden") or meta_existing.get("hidden")):
            continue
        if btype in SUPPRESS_OLD_TTL_REPLACEMENT_TYPES:
            rep = ""
        elif path.startswith("sk:"):
            rep = _build_skill_prune_message(path)
        elif btype == "react.tool.call":
            payload = _parse_json(blk.get("text") or "") or {}
            tool_id = _coerce_tool_id(blk, payload)
            if tool_id == "react.plan":
                continue
            view = _get_view(tool_id)
            rep = view.build_call_replacement(tool_call_block=blk, payload=payload, cfg=cfg)
        elif btype == "react.tool.result" and (path.startswith("tc:") or path.startswith("so:")):
            payload = _parse_json(blk.get("text") or "") if isinstance(blk.get("text"), str) else None
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            call_id = (meta.get("tool_call_id") or blk.get("call_id") or "").strip()
            tool_id = ""
            if call_id and call_id in call_meta:
                tool_id = call_meta[call_id].get("tool_id") or ""
            if tools_insights.is_search_tool(tool_id) or tools_insights.is_fetch_uri_content_tool(tool_id):
                rep = _build_search_result_replacement_compact(
                    tool_id=tool_id,
                    call_id=call_id,
                    payload=payload or {},
                    cfg=cfg,
                    items_stats=meta.get("items_stats") if isinstance(meta.get("items_stats"), dict) else None,
                )
            else:
                view = _get_view(tool_id)
                rep = view.build_result_replacement(tool_result_block=blk, payload=payload or {}, cfg=cfg)
        elif path.startswith("fi:"):
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            call_id = (meta.get("tool_call_id") or blk.get("call_id") or "").strip()
            tool_id = ""
            if call_id and call_id in call_meta:
                tool_id = call_meta[call_id].get("tool_id") or ""
            if btype == "react.tool.result" and tool_id == "react.read":
                payload = _parse_json(blk.get("text") or "") if isinstance(blk.get("text"), str) else None
                rep = _get_view(tool_id).build_result_replacement(
                    tool_result_block=blk,
                    payload=payload or {},
                    cfg=cfg,
                )
            else:
                rep = _build_file_replacement(blk)
        else:
            rep = _build_generic_replacement(blk, cfg)

        path_replacements[path] = (blk, rep)

    hidden_paths: List[str] = []
    if not skip_old_turns:
        for path, (src_blk, rep) in path_replacements.items():
            try:
                rep = _bound_ttl_replacement(block=src_blk, replacement=rep, cfg=cfg, call_meta=call_meta)
                timeline.hide_paths(
                    [path],
                    rep,
                    hidden_prune_scope="old_turn",
                    hidden_reason="cache_ttl",
                )
                hidden_paths.append(path)
            except Exception:
                pass

    hidden_recent_paths: set[str] = set()

    # Light-truncate heavy tool results in recent-but-not-intact turns.
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "") in skip_types:
            continue
        if blk.get("hidden") or (isinstance(blk.get("meta"), dict) and blk.get("meta", {}).get("hidden")):
            continue
        path = (blk.get("path") or "").strip()
        if not path or path in hidden_recent_paths:
            continue
        turn_id = _extract_turn_id(blk)
        if not skip_old_turns:
            if turn_id and turn_id not in recent_turns:
                continue
        if turn_id and turn_id in intact_turns:
            continue
        if (blk.get("type") or "").strip() != "react.tool.result":
            continue

        payload = _parse_json(blk.get("text") or "") if isinstance(blk.get("text"), str) else None
        meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
        call_id = (meta.get("tool_call_id") or blk.get("call_id") or "").strip()
        tool_id = ""
        if call_id and call_id in call_meta:
            tool_id = call_meta[call_id].get("tool_id") or ""

        if tool_id == "react.read" and path.startswith("sk:"):
            try:
                rep = _build_skill_prune_message(path)
                rep = _bound_ttl_replacement(block=blk, replacement=rep, cfg=cfg, call_meta=call_meta)
                timeline.hide_paths(
                    [path],
                    rep,
                    hidden_prune_scope="cold_recent",
                    hidden_reason="cache_ttl_light",
                )
                hidden_recent_paths.add(path)
            except Exception:
                pass
            continue

        if (
            tool_id in {"react.memsearch", "react.search_knowledge"}
            or tools_insights.is_search_tool(tool_id)
            or tools_insights.is_fetch_uri_content_tool(tool_id)
        ):
            try:
                if tool_id in {"react.memsearch", "react.search_knowledge"} and not path.startswith(("tc:", "so:")):
                    rep = _build_skill_prune_message(path)
                elif tools_insights.is_search_tool(tool_id) or tools_insights.is_fetch_uri_content_tool(tool_id):
                    rep = _build_search_result_replacement_compact(
                        tool_id=tool_id,
                        call_id=call_id,
                        payload=payload or {},
                        cfg=cfg,
                        items_stats=meta.get("items_stats") if isinstance(meta.get("items_stats"), dict) else None,
                    )
                else:
                    view = _get_view(tool_id)
                    rep = view.build_result_replacement(tool_result_block=blk, payload=payload or {}, cfg=cfg)
                rep = _bound_ttl_replacement(block=blk, replacement=rep, cfg=cfg, call_meta=call_meta)
                timeline.hide_paths(
                    [path],
                    rep,
                    hidden_prune_scope="cold_recent",
                    hidden_reason="cache_ttl_light",
                )
                hidden_recent_paths.add(path)
            except Exception:
                pass

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
                    rep = _bound_ttl_replacement(block=blk, replacement=rep, cfg=cfg, call_meta=call_meta)
                    timeline.hide_paths(
                        [path],
                        rep,
                        hidden_prune_scope="cold_recent",
                        hidden_reason="cache_ttl_light",
                    )
                    hidden_recent_paths.add(path)
                except Exception:
                    pass
            continue
        if len(base64) > cfg.max_base64_chars and path not in hidden_recent_paths:
            rep = _build_file_replacement(blk)
            try:
                rep = _bound_ttl_replacement(block=blk, replacement=rep, cfg=cfg, call_meta=call_meta)
                timeline.hide_paths(
                    [path],
                    rep,
                    hidden_prune_scope="cold_recent",
                    hidden_reason="cache_ttl_light",
                )
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
    pruned_tokens = max(0, (before_tokens or 0) - (after_tokens or 0))
    try:
        ttl_msg = _build_prune_message_text(ttl_seconds)
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
        def _format_paths(paths: List[str], limit: int = 5) -> str:
            if not paths:
                return ""
            sample = paths[:limit]
            tail = f"...(+{len(paths) - limit})" if len(paths) > limit else ""
            return ",".join(sample) + tail

        logger.info(
            "[cache_ttl_prune] ttl=%ss buffer=%ss last_touch=%s now=%s "
            "blocks=%s->%s tokens=%s->%s pruned_tokens=%s hidden_paths=%s hidden_recent=%s "
            "paths=%s recent_paths=%s "
            "keep_recent_turns=%s keep_intact=%s skip_old_turns=%s",
            int(ttl_seconds or 0),
            int(buffer_seconds or 0),
            last_touch_val,
            now,
            before_blocks,
            after_blocks,
            before_tokens,
            after_tokens,
            pruned_tokens,
            len(hidden_paths),
            len(hidden_recent_paths),
            _format_paths(hidden_paths),
            _format_paths(sorted(hidden_recent_paths)),
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
