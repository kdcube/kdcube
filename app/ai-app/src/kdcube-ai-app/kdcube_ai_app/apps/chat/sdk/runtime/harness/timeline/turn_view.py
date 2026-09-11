# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Framework-neutral projection of persisted blocks into a client turn view."""

from __future__ import annotations

import json
import pathlib
from typing import Any

from kdcube_ai_app.apps.chat.sdk.tools import citations as citation_utils
from kdcube_ai_app.apps.chat.sdk.util import ts_key


def _meta_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_source_sids(sources: Any) -> list[int]:
    out: list[int] = []
    if not isinstance(sources, list):
        return out
    for item in sources:
        if isinstance(item, dict):
            sid = item.get("sid")
            if isinstance(sid, (int, float)):
                out.append(int(sid))
        elif isinstance(item, (int, float)):
            out.append(int(item))
    return out


def extract_sources_used_from_blocks(
    blocks: list[dict[str, Any]],
) -> list[int]:
    """Collect unique source IDs referenced by completion or result blocks."""
    used: list[int] = []
    seen: set[int] = set()
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type in {
            "assistant.completion",
            "assistant.completion.attempt",
        }:
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                for sid in citation_utils.extract_citation_sids_any(text):
                    if sid not in seen:
                        seen.add(sid)
                        used.append(sid)
        meta = block.get("meta")
        if isinstance(meta, dict):
            for sid in extract_source_sids(meta.get("sources_used")):
                if sid not in seen:
                    seen.add(sid)
                    used.append(sid)
        if (
            block_type == "react.tool.result"
            and str(block.get("mime") or "") == "application/json"
        ):
            result_meta = _meta_json(block.get("text"))
            for sid in extract_source_sids(result_meta.get("sources_used")):
                if sid not in seen:
                    seen.add(sid)
                    used.append(sid)
    return used


def extract_user_prompt_block(
    blocks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for block in blocks or []:
        if isinstance(block, dict) and block.get("type") == "user.prompt":
            return block
    return None


def extract_assistant_completion_block(
    blocks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for block in reversed(blocks or []):
        if (
            isinstance(block, dict)
            and block.get("type") == "assistant.completion"
        ):
            return block
    return None


def extract_assistant_completion_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        block
        for block in blocks or []
        if isinstance(block, dict)
        and block.get("type") == "assistant.completion"
    ]


def extract_assistant_completion_texts_from_blocks(
    blocks: list[dict[str, Any]],
) -> list[str]:
    """Prefer canonical completions; fall back to rejected/partial attempts."""
    canonical: list[str] = []
    attempts: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type not in {
            "assistant.completion",
            "assistant.completion.attempt",
        }:
            continue
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if block_type == "assistant.completion":
            canonical.append(text)
        else:
            attempts.append(text)
    return canonical or attempts


def extract_followups_from_blocks(
    blocks: list[dict[str, Any]],
) -> list[str]:
    items: list[str] = []
    for block in blocks or []:
        if (
            not isinstance(block, dict)
            or block.get("type") != "stage.suggested_followups"
        ):
            continue
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        values = meta.get("items")
        if isinstance(values, list):
            items.extend(
                value.strip()
                for value in values
                if isinstance(value, str) and value.strip()
            )
    return items


def extract_clarification_questions_from_blocks(
    blocks: list[dict[str, Any]],
) -> list[str]:
    items: list[str] = []
    for block in blocks or []:
        if (
            not isinstance(block, dict)
            or block.get("type") != "stage.clarification"
        ):
            continue
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        values = meta.get("questions")
        if isinstance(values, list):
            items.extend(
                value.strip()
                for value in values
                if isinstance(value, str) and value.strip()
            )
    return items


def _attachment_name(path: str) -> str:
    return str(path or "").rstrip("/").rsplit("/", 1)[-1]


def extract_user_attachments_from_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type not in {"user.attachment.meta", "user.attachment"}:
            continue
        path = str(block.get("path") or "").strip()
        if not path:
            continue
        entry = by_path.setdefault(path, {"path": path})
        timestamp = (
            str(block.get("ts") or "").strip()
            if isinstance(block.get("ts"), str)
            else ""
        )
        if timestamp and not entry.get("ts"):
            entry["ts"] = timestamp
        if block_type == "user.attachment.meta":
            meta = (
                block.get("meta")
                if isinstance(block.get("meta"), dict)
                else {}
            )
            entry["meta"] = dict(meta)
        else:
            entry["mime"] = str(block.get("mime") or "").strip()

    out: list[dict[str, Any]] = []
    for path, entry in by_path.items():
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        payload: dict[str, Any] = {
            "filename": _attachment_name(path),
            "mime": (
                str(entry.get("mime") or meta.get("mime") or "").strip()
                or "application/octet-stream"
            ),
            "artifact_path": path,
        }
        if entry.get("ts"):
            payload["ts"] = entry["ts"]
        for key in ("rn", "hosted_uri", "key", "physical_path"):
            if meta.get(key):
                payload[key] = meta[key]
        if not payload.get("physical_path") and meta.get("local_path"):
            payload["physical_path"] = meta["local_path"]
        summary = meta.get("summary") or meta.get("description")
        if summary:
            payload["summary"] = summary
        for key in (
            "event_kind",
            "event_type",
            "is_continuation",
            "message_id",
            "sequence",
        ):
            if meta.get(key) is not None:
                payload[key] = meta[key]
        out.append(payload)
    return out


def extract_assistant_files_from_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project the latest externally visible file record for each path."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in reversed(blocks or []):
        if not isinstance(block, dict):
            continue
        if (
            block.get("type") != "react.tool.result"
            or str(block.get("mime") or "").strip() != "application/json"
        ):
            continue
        meta = _meta_json(block.get("text"))
        if meta.get("error"):
            continue
        if str(meta.get("visibility") or "").strip() != "external":
            continue
        if str(meta.get("kind") or "").strip() != "file":
            continue
        if not any(
            meta.get(key)
            for key in (
                "hosted_uri",
                "rn",
                "key",
                "physical_path",
                "local_path",
            )
        ):
            continue
        artifact_path = str(meta.get("artifact_path") or "").strip()
        if not artifact_path or artifact_path in seen:
            continue
        seen.add(artifact_path)
        physical_path = str(
            meta.get("physical_path") or meta.get("local_path") or ""
        ).strip()
        filename = str(meta.get("filename") or "").strip()
        if not filename:
            filename = pathlib.PurePosixPath(
                physical_path or artifact_path
            ).name
        payload: dict[str, Any] = {
            "filename": filename,
            "mime": (
                str(meta.get("mime") or "").strip()
                or "application/octet-stream"
            ),
            "artifact_path": artifact_path,
        }
        timestamp = (
            str(block.get("ts") or "").strip()
            if isinstance(block.get("ts"), str)
            else ""
        )
        if timestamp:
            payload["ts"] = timestamp
        for key in (
            "rn",
            "hosted_uri",
            "key",
            "physical_path",
            "tool_id",
            "tool_call_id",
            "call_id",
            "sub_type",
            "content_sha256",
            "size",
            "size_bytes",
        ):
            value = meta.get(key)
            if value:
                payload[key] = value
        if not payload.get("physical_path") and meta.get("local_path"):
            payload["physical_path"] = meta["local_path"]
        summary = meta.get("summary") or meta.get("description")
        if summary:
            payload["summary"] = summary
        out.append(payload)
    out.reverse()
    return out


def _timestamp_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        seconds = ts_key(str(value))
    except Exception:
        return None
    if seconds == float("-inf"):
        return None
    return int(seconds * 1000)


def _timeline_text_items(
    blocks: list[dict[str, Any]],
    turn_id: str,
) -> list[dict[str, Any]]:
    if not blocks or not turn_id:
        return []
    text_by_path = {
        str(block.get("path") or "").strip(): block
        for block in blocks
        if isinstance(block, dict)
        and block.get("turn_id") == turn_id
        and str(block.get("path") or "").strip()
        and isinstance(block.get("text"), str)
    }
    items: list[dict[str, Any]] = []
    index = 0
    for block in blocks:
        if not isinstance(block, dict) or block.get("turn_id") != turn_id:
            continue
        block_type = str(block.get("type") or "")
        if block_type == "react.notes":
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            item: dict[str, Any] = {
                "artifact_name": f"timeline_text.react.notes.{index}",
                "text": text,
            }
            timestamp = _timestamp_ms(block.get("ts"))
            if timestamp is not None:
                item["ts_first"] = timestamp
                item["ts_last"] = timestamp
            items.append(item)
            index += 1
            continue
        if (
            block_type != "react.tool.result"
            or str(block.get("mime") or "").strip() != "application/json"
        ):
            continue
        meta = _meta_json(block.get("text"))
        if str(meta.get("channel") or "").strip() != "timeline_text":
            continue
        artifact_path = str(meta.get("artifact_path") or "").strip()
        content_block = text_by_path.get(artifact_path)
        content = content_block.get("text") if content_block else None
        if not isinstance(content, str) or not content.strip():
            continue
        item = {
            "artifact_name": f"timeline_text.{turn_id}.{index}",
            "text": content,
        }
        timestamp = _timestamp_ms(
            content_block.get("ts") or block.get("ts")
        )
        if timestamp is not None:
            item["ts_first"] = timestamp
            item["ts_last"] = timestamp
        items.append(item)
        index += 1
    return items


def _thinking_items(
    blocks: list[dict[str, Any]],
    turn_id: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not blocks or not turn_id:
        return items
    for block in blocks:
        if not isinstance(block, dict) or block.get("turn_id") != turn_id:
            continue
        if block.get("type") != "react.thinking":
            continue
        text = block.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        item: dict[str, Any] = {
            "agent": str(meta.get("title") or "").strip() or "react",
            "text": text,
        }
        timestamp = _timestamp_ms(block.get("ts"))
        if timestamp is not None:
            item["ts_first"] = timestamp
            item["ts_last"] = timestamp
        items.append(item)
    return items


def materialize_sources_by_sids(
    pool: list[dict[str, Any]],
    source_ids: list[int],
) -> list[dict[str, Any]]:
    if not source_ids or not pool:
        return []
    wanted = {
        int(source_id)
        for source_id in source_ids
        if isinstance(source_id, (int, float))
    }
    return [
        row
        for row in pool
        if isinstance(row, dict) and int(row.get("sid") or 0) in wanted
    ]


def build_turn_view(
    *,
    turn_id: str,
    blocks: list[dict[str, Any]],
    sources_pool: list[dict[str, Any]] | None = None,
    render_thinking: bool = True,
) -> dict[str, Any]:
    """Build the normalized client view shared by all agent frameworks."""
    pool = list(sources_pool or [])
    user_block = extract_user_prompt_block(blocks)
    assistant_block = extract_assistant_completion_block(blocks)
    assistant_blocks = extract_assistant_completion_blocks(blocks)
    source_ids = extract_sources_used_from_blocks(blocks)
    return {
        "turn_id": turn_id,
        "user": {
            "text": (
                user_block.get("text")
                if isinstance(user_block, dict)
                else ""
            ),
            "ts": (
                user_block.get("ts")
                if isinstance(user_block, dict)
                else ""
            ),
        },
        "assistant": {
            "text": (
                assistant_block.get("text")
                if isinstance(assistant_block, dict)
                else ""
            ),
            "ts": (
                assistant_block.get("ts")
                if isinstance(assistant_block, dict)
                else ""
            ),
        },
        "assistants": [
            {
                "text": (
                    block.get("text")
                    if isinstance(block.get("text"), str)
                    else ""
                ),
                "ts": (
                    block.get("ts")
                    if isinstance(block.get("ts"), str)
                    else ""
                ),
                "path": (
                    block.get("path")
                    if isinstance(block.get("path"), str)
                    else ""
                ),
                "meta": (
                    block.get("meta")
                    if isinstance(block.get("meta"), dict)
                    else {}
                ),
            }
            for block in assistant_blocks
            if isinstance(block.get("text"), str)
            and str(block.get("text") or "").strip()
        ],
        "attachments": extract_user_attachments_from_blocks(blocks),
        "files": extract_assistant_files_from_blocks(blocks),
        "citations": materialize_sources_by_sids(pool, source_ids),
        "timeline_text": _timeline_text_items(blocks, turn_id),
        "thinking": (
            _thinking_items(blocks, turn_id) if render_thinking else []
        ),
        "followups": extract_followups_from_blocks(blocks),
        "clarification_questions": (
            extract_clarification_questions_from_blocks(blocks)
        ),
    }
