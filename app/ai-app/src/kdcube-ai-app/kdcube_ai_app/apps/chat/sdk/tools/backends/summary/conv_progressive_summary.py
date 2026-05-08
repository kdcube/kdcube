# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# apps/chat/sdk/tools/backends/summary/conv_progressive_summary.py

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

import json
import logging

from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import (
    create_cached_system_message,
    create_cached_human_message,
)
import kdcube_ai_app.apps.chat.sdk.viz.logging_helpers as logging_helpers
from kdcube_ai_app.apps.chat.sdk.tools import tools_insights


log = logging.getLogger(__name__)


SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI coding assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""

SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- ✓ [Completed tasks/changes]

### In Progress
- … [Current work]

### Not Started
- □ [Planned but not started]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

If you see protocol violations, repeated tool errors, or missing files, summarize them under **Blocked** or **Critical Context** so the next agent avoids the same mistake.
Internal notes may appear as `react.note` blocks and are tagged:
- [P] personal/preferences
- [D] decisions/rationale
- [S] specs/structure/technical details
- [A] achievements, completed milestones, or project-level accomplishment notes
- [K] key artifacts/anchors with logical path and why they matter
Treat these as high-signal. Preserve them in the appropriate sections:
- [P] -> Constraints & Preferences
- [D] -> Key Decisions
- [S] -> Critical Context
- [A] -> Progress (usually Done; if still relevant, also Next Steps or Critical Context)
- [K] -> Critical Context (preserve the path and the one-line explanation)
Live turn events may appear as `user.followup` or `user.steer` blocks:
- `user.followup` = additional user input for the same running turn
- `user.steer` = user redirection/stop signal for the same running turn
Treat both as high-priority user intent updates. Preserve them in Goal, Constraints & Preferences, Key Decisions, Next Steps, or Critical Context wherever they materially changed what the agent should do next.

If exact tool-result or artifact content is large enough that it will not remain
visible after compaction, do not say the exact data is still "loaded", "in
memory", or "available" without naming the recovery path. Preserve the logical
path and state that exact bulk processing should use exec code with
`ctx_tools.fetch_ctx(path=...)` instead of repeated `react.read`.

Keep each section concise. Preserve exact file paths, function names, and error messages."""

UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- ✓ [Include previously done items AND newly completed items]

### In Progress
- … [Current work - update based on progress]

### Not Started
- □ [Planned but not started]

### Blocked
- [Current blockers - remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

If you see protocol violations, repeated tool errors, or missing files, summarize them under **Blocked** or **Critical Context** so the next agent avoids the same mistake.
Internal notes may appear as `react.note` blocks and are tagged:
- [P] personal/preferences
- [D] decisions/rationale
- [S] specs/structure/technical details
- [A] achievements, completed milestones, or project-level accomplishment notes
- [K] key artifacts/anchors with logical path and why they matter
Treat these as high-signal. Preserve them in the appropriate sections:
- [P] -> Constraints & Preferences
- [D] -> Key Decisions
- [S] -> Critical Context
- [A] -> Progress (usually Done; if still relevant, also Next Steps or Critical Context)
- [K] -> Critical Context (preserve the path and the one-line explanation)
Live turn events may appear as `user.followup` or `user.steer` blocks:
- `user.followup` = additional user input for the same running turn
- `user.steer` = user redirection/stop signal for the same running turn
Treat both as high-priority user intent updates. Preserve them in Goal, Constraints & Preferences, Key Decisions, Next Steps, or Critical Context wherever they materially changed what the agent should do next.

If exact tool-result or artifact content is large enough that it will not remain
visible after compaction, do not say the exact data is still "loaded", "in
memory", or "available" without naming the recovery path. Preserve the logical
path and state that exact bulk processing should use exec code with
`ctx_tools.fetch_ctx(path=...)` instead of repeated `react.read`.

Keep each section concise. Preserve exact file paths, function names, and error messages."""

TURN_PREFIX_SUMMARIZATION_PROMPT = """This is the PREFIX of a turn that was too large to keep. The SUFFIX (recent work) is retained.

Summarize the prefix to provide context for the retained suffix:

## Original Request
[What did the user ask for in this turn?]

## Early Progress
- [Key decisions and work done in the prefix]

## Context for Suffix
- [Information needed to understand the retained recent work]

Internal notes may appear as `react.note` blocks and are tagged [P]/[D]/[S]/[A]/[K].
[A] means achievements, completed milestones, or project-level accomplishment notes.
[K] means key artifacts/anchors with logical path and why they matter.
Live turn events may appear as `user.followup` or `user.steer` blocks. They are real user control input for the same turn and should be preserved if they explain why the retained suffix changed direction, scope, or stopping behavior.
Preserve their substance if they are relevant, especially when they explain why the retained suffix exists, what was already completed earlier in the turn, or which artifact the future agent should reopen first.

If exact tool-result or artifact content is large enough that it will not remain
visible after compaction, do not say the exact data is still "loaded", "in
memory", or "available" without naming the recovery path. Preserve the logical
path and state that exact bulk processing should use exec code with
`ctx_tools.fetch_ctx(path=...)` instead of repeated `react.read`.

Be concise. Focus on what's needed to understand the kept suffix."""


def _format_tool_call(tool_id: str, params: Any) -> str:
    if not tool_id:
        tool_id = "tool"
    if isinstance(params, dict):
        args_str = ", ".join(
            f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in params.items()
        )
    elif params is None:
        args_str = ""
    else:
        try:
            args_str = json.dumps(params, ensure_ascii=False)
        except Exception:
            args_str = str(params)
    return f"{tool_id}({args_str})" if args_str else f"{tool_id}()"


def _parse_json(text: str) -> Dict[str, Any]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _format_header(parts: List[str]) -> str:
    return " | ".join(p for p in parts if str(p or "").strip())


def _textish(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _serialize_context_blocks_for_compaction(blocks: List[dict]) -> str:
    parts: List[str] = []
    for blk in blocks or []:
        if not isinstance(blk, dict):
            continue
        btype = _textish(blk.get("type")) or "block"
        author = _textish(blk.get("author") or blk.get("role"))
        turn_id = _textish(blk.get("turn_id") or blk.get("turn"))
        ts = _textish(blk.get("ts"))
        path = _textish(blk.get("path"))
        mime = _textish(blk.get("mime"))
        call_id = _textish(blk.get("call_id"))
        tool_id = _textish(blk.get("tool_id"))
        tool_call_id = _textish(blk.get("tool_call_id"))
        text = blk.get("text")
        meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
        hidden = bool(blk.get("hidden") or meta.get("hidden"))
        if hidden:
            header_parts = ["hidden=true"]
            repl = blk.get("replacement_text") or meta.get("replacement_text")
            if isinstance(repl, str) and repl.strip():
                text = repl
            else:
                text = ""
        else:
            header_parts = []

        header_parts = [f"type={btype}"] + header_parts
        if author:
            header_parts.append(f"author={author}")
        if turn_id:
            header_parts.append(f"turn={turn_id}")
        if ts:
            header_parts.append(f"ts={ts}")
        if not path:
            path = _textish(meta.get("artifact_path") or meta.get("physical_path"))
        if path:
            header_parts.append(f"path={path}")
        if mime:
            header_parts.append(f"mime={mime}")
        if call_id:
            header_parts.append(f"call_id={call_id}")
        if tool_id:
            header_parts.append(f"tool_id={tool_id}")
        if tool_call_id:
            header_parts.append(f"tool_call_id={tool_call_id}")

        if btype == "conv.working.summary":
            for key in (
                "summary_scope",
                "assistant_completion_attempt_index",
                "assistant_completion_attempt_count",
                "assistant_completion_path",
                "iteration",
                "source_channel",
            ):
                val = meta.get(key)
                if val not in (None, ""):
                    header_parts.append(f"{key}={json.dumps(val, ensure_ascii=False)}")
            header = _format_header(header_parts)
            if isinstance(text, str) and text.strip():
                parts.append(f"[Working Summary]: ({header})\n{text.strip()}")
            else:
                parts.append(f"[Working Summary]: ({header})")
            continue

        if btype in {"user.followup", "user.followup.preserved"}:
            if isinstance(text, str) and text.strip():
                parts.append(f"[User Followup During Turn]: {text.strip()}")
            else:
                parts.append(f"[User Followup During Turn]: ({_format_header(header_parts)})")
            continue
        if btype in {"user.steer", "user.steer.preserved"}:
            if isinstance(text, str) and text.strip():
                parts.append(f"[User Steer During Turn]: {text.strip()}")
            else:
                parts.append(f"[User Steer During Turn]: ({_format_header(header_parts)})")
            continue
        if btype == "user.prompt" or author == "user":
            if isinstance(text, str) and text.strip():
                parts.append(f"[User]: {text.strip()}")
            else:
                parts.append(f"[User]: ({_format_header(header_parts)})")
            continue
        if btype in {"react.note", "react.note.preserved"}:
            if isinstance(text, str) and text.strip():
                parts.append(f"[Internal Note]: {text.strip()}")
            else:
                parts.append(f"[Internal Note]: ({_format_header(header_parts)})")
            continue

        if btype == "assistant.completion" or author == "assistant":
            if isinstance(text, str) and text.strip():
                parts.append(f"[Assistant]: {text.strip()}")
            else:
                parts.append(f"[Assistant]: ({_format_header(header_parts)})")
            continue

        if btype == "react.tool.call":
            payload: Dict[str, Any] = {}
            if isinstance(text, str) and text.strip():
                try:
                    payload = json.loads(text)
                except Exception:
                    payload = {}
            params = payload.get("params") if isinstance(payload, dict) else None
            tool_name = tool_id or (payload.get("tool_id") if isinstance(payload, dict) else "") or "tool"
            parts.append(f"[Assistant tool calls]: {_format_tool_call(str(tool_name), params)}")
            continue

        if btype == "react.tool.result":
            if isinstance(text, str) and text.strip():
                parts.append(f"[Tool result]: {text.strip()}")
            else:
                parts.append(f"[Tool result]: ({_format_header(header_parts)})")
            continue

        header = "[Block] " + _format_header(header_parts)
        if isinstance(text, str) and text.strip():
            parts.append(f"{header}\n{text.strip()}")
        else:
            parts.append(header)

        if blk.get("base64") and not hidden:
            binary = "[Binary block]"
            if mime:
                binary += f" mime={mime}"
            if not path:
                path = _textish(meta.get("artifact_path") or meta.get("physical_path"))
            if path:
                binary += f" path={path}"
            parts.append(binary)

    return "\n\n".join(parts).strip()


def _approx_tokens(text: str) -> int:
    return max(0, int(len(text or "") / 4))


def _extract_file_ops_from_blocks(blocks: List[dict]) -> Tuple[Set[str], Set[str]]:
    read_files: Set[str] = set()
    modified_files: Set[str] = set()

    def _collect_paths(value: Any) -> List[str]:
        paths: List[str] = []
        if isinstance(value, str):
            val = value.strip()
            if val:
                paths.append(val)
            return paths
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    paths.append(item.strip())
            return paths
        if isinstance(value, dict):
            for key in ("path", "paths", "filepath", "file", "files"):
                if key not in value:
                    continue
                paths.extend(_collect_paths(value.get(key)))
        return paths

    for blk in blocks or []:
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "") != "react.tool.call":
            continue
        payload: Dict[str, Any] = {}
        text = blk.get("text")
        if isinstance(text, str) and text.strip():
            try:
                payload = json.loads(text)
            except Exception:
                payload = {}
        tool_name = (blk.get("tool_id") or "").strip()
        if not tool_name and isinstance(payload, dict):
            tool_name = str(payload.get("tool_id") or "").strip()
        params = payload.get("params") if isinstance(payload, dict) else None
        paths = _collect_paths(params)
        if not tool_name or not paths:
            continue
        suffix = tool_name.rsplit(".", 1)[-1]
        if suffix == "read":
            read_files.update(paths)
        elif suffix in {"write", "patch", "apply_patch", "edit"}:
            modified_files.update(paths)

    return read_files, modified_files


def _format_file_ops_summary(read_files: Set[str], modified_files: Set[str]) -> str:
    if not read_files and not modified_files:
        return ""
    modified = set(modified_files)
    read_only = sorted(f for f in read_files if f not in modified)
    modified_list = sorted(modified)
    sections: List[str] = []
    if read_only:
        sections.append("<read-files>\n" + "\n".join(read_only) + "\n</read-files>")
    if modified_list:
        sections.append("<modified-files>\n" + "\n".join(modified_list) + "\n</modified-files>")
    return "\n\n" + "\n\n".join(sections)


def _large_tool_result_recovery_rows(blocks: List[dict], *, min_tokens: int = 12_000) -> List[Dict[str, Any]]:
    tool_calls: Dict[str, Dict[str, Any]] = {}
    for blk in blocks or []:
        if not isinstance(blk, dict) or (blk.get("type") or "") != "react.tool.call":
            continue
        payload = _parse_json(blk.get("text") or "") if isinstance(blk.get("text"), str) else {}
        call_id = str(payload.get("tool_call_id") or blk.get("call_id") or "").strip()
        if not call_id:
            continue
        tool_calls[call_id] = {
            "tool_id": str(payload.get("tool_id") or blk.get("tool_id") or "").strip(),
            "turn_id": blk.get("turn_id") or blk.get("turn") or "",
            "ts": blk.get("ts") or "",
        }

    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for blk in blocks or []:
        if not isinstance(blk, dict) or (blk.get("type") or "") != "react.tool.result":
            continue
        text = blk.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        approx_tokens = _approx_tokens(text)
        if approx_tokens < min_tokens:
            continue
        call_id = str(blk.get("call_id") or "").strip()
        tool_meta = tool_calls.get(call_id) or {}
        turn_id = str(blk.get("turn_id") or blk.get("turn") or tool_meta.get("turn_id") or "").strip()
        path = str(blk.get("path") or "").strip()
        if not path and turn_id and call_id:
            path = f"tc:{turn_id}.{call_id}.result"
        key = path or f"{turn_id}:{call_id}"
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append({
            "path": path,
            "turn_id": turn_id,
            "tool_call_id": call_id,
            "tool_id": str((blk.get("meta") or {}).get("tool_id") or tool_meta.get("tool_id") or "").strip()
            if isinstance(blk.get("meta"), dict) else str(tool_meta.get("tool_id") or "").strip(),
            "approx_tokens": approx_tokens,
        })
        if len(rows) >= 20:
            break
    return rows


def _format_large_tool_result_recovery(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    lines = ["<recoverable-tool-results>"]
    for row in rows:
        parts = []
        path = str(row.get("path") or "").strip()
        if path:
            parts.append(f"path={path}")
        tool_id = str(row.get("tool_id") or "").strip()
        if tool_id:
            parts.append(f"tool_id={tool_id}")
        call_id = str(row.get("tool_call_id") or "").strip()
        if call_id:
            parts.append(f"tool_call_id={call_id}")
        approx_tokens = row.get("approx_tokens")
        if approx_tokens:
            parts.append(f"approx_tokens={approx_tokens}")
        parts.append("exact_content_compacted=true")
        parts.append("use=exec_tools.execute_code_python + ctx_tools.fetch_ctx(path) for bulk processing")
        parts.append("avoid=repeated react.read")
        lines.append("- " + " ".join(parts))
    lines.append("</recoverable-tool-results>")
    return "\n\n" + "\n".join(lines)


def build_compaction_digest(blocks: List[dict]) -> Dict[str, Any]:
    tool_calls: Dict[str, Dict[str, Any]] = {}
    artifacts: List[Dict[str, Any]] = []
    streamed: List[Dict[str, Any]] = []
    written: List[Dict[str, Any]] = []
    patched: List[Dict[str, Any]] = []
    exec_outputs: List[Dict[str, Any]] = []
    memsearches: List[Dict[str, Any]] = []
    hidden_blocks: List[Dict[str, Any]] = []
    seen_artifacts: set[str] = set()

    for blk in blocks or []:
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "") != "react.tool.call":
            continue
        text = blk.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        payload = _parse_json(text)
        tool_call_id = str(payload.get("tool_call_id") or blk.get("call_id") or "").strip()
        tool_id = str(payload.get("tool_id") or blk.get("tool_id") or "").strip()
        if not tool_call_id:
            continue
        tool_calls[tool_call_id] = {
            "tool_id": tool_id,
            "params": payload.get("params") if isinstance(payload, dict) else None,
            "turn_id": blk.get("turn_id") or blk.get("turn") or "",
            "ts": blk.get("ts") or "",
        }

    for blk in blocks or []:
        if not isinstance(blk, dict):
            continue
        meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
        if blk.get("hidden") or meta.get("hidden"):
            hidden_blocks.append({
                "path": blk.get("path") or meta.get("artifact_path") or meta.get("physical_path") or "",
                "type": blk.get("type") or "",
                "turn_id": blk.get("turn_id") or blk.get("turn") or "",
                "ts": blk.get("ts") or "",
                "mime": blk.get("mime") or "",
                "replacement_text": blk.get("replacement_text") or meta.get("replacement_text") or "",
            })

    for blk in blocks or []:
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "") != "react.tool.result":
            continue
        mime = (blk.get("mime") or "").strip()
        text = blk.get("text")
        call_id = (blk.get("call_id") or "").strip()
        tool_meta = tool_calls.get(call_id) or {}
        tool_id = str(tool_meta.get("tool_id") or "").strip()
        turn_id = blk.get("turn_id") or blk.get("turn") or ""
        ts = blk.get("ts") or ""

        if mime == "application/json" and isinstance(text, str) and text.strip():
            payload = _parse_json(text)
            if payload.get("artifact_path") or payload.get("physical_path"):
                artifact_path = (payload.get("artifact_path") or "").strip()
                physical_path = (payload.get("physical_path") or "").strip()
                key = artifact_path or physical_path or (blk.get("path") or "").strip()
                if key and key in seen_artifacts:
                    continue
                if key:
                    seen_artifacts.add(key)
                artifact = {
                    "turn_id": turn_id,
                    "ts": ts,
                    "artifact_path": artifact_path,
                    "physical_path": physical_path,
                    "mime": payload.get("mime") or mime or "",
                    "kind": payload.get("kind") or "",
                    "visibility": payload.get("visibility") or "",
                    "tool_id": payload.get("tool_id") or tool_id,
                    "tool_call_id": payload.get("tool_call_id") or call_id,
                    "sources_used": payload.get("sources_used") or [],
                }
                artifacts.append(artifact)
                visibility = str(artifact.get("visibility") or "").strip().lower()
                kind = str(artifact.get("kind") or "").strip().lower()
                if visibility == "external" and kind in {"display", "file"}:
                    streamed.append(artifact)
                tool_name = str(artifact.get("tool_id") or tool_id)
                if tools_insights.is_write_tool(tool_name):
                    written.append(artifact)
                if tool_name.endswith(".patch") or tool_name.endswith(".apply_patch"):
                    patched.append(artifact)
                if tools_insights.is_exec_tool(tool_name):
                    exec_outputs.append(artifact)
                continue

            if tool_id == "react.memsearch":
                hits = payload.get("hits") if isinstance(payload, dict) else None
                hit_paths: List[str] = []
                hit_turns: List[str] = []
                if isinstance(hits, list):
                    for h in hits:
                        if not isinstance(h, dict):
                            continue
                        tid = (h.get("turn_id") or "").strip()
                        if tid and tid not in hit_turns:
                            hit_turns.append(tid)
                        for snip in h.get("snippets") or []:
                            if not isinstance(snip, dict):
                                continue
                            path = (snip.get("path") or "").strip()
                            if path and path not in hit_paths:
                                hit_paths.append(path)
                params = tool_meta.get("params") if isinstance(tool_meta, dict) else None
                memsearches.append({
                    "tool_call_id": call_id,
                    "tool_id": tool_id,
                    "turn_id": turn_id,
                    "ts": ts,
                    "query": (params or {}).get("query") if isinstance(params, dict) else None,
                    "targets": (params or {}).get("targets") if isinstance(params, dict) else None,
                    "top_k": (params or {}).get("top_k") if isinstance(params, dict) else None,
                    "days": (params or {}).get("days") if isinstance(params, dict) else None,
                    "hits_count": len(hits) if isinstance(hits, list) else 0,
                    "turn_ids": hit_turns[:20],
                    "paths": hit_paths[:20],
                })

    return {
        "artifacts": artifacts,
        "streamed_artifacts": streamed,
        "written_files": written,
        "patched_files": patched,
        "exec_outputs": exec_outputs,
        "memsearches": memsearches,
        "hidden_blocks": hidden_blocks,
    }


def _build_compaction_prompt_text(
    *,
    conversation_text: str,
    previous_summary: Optional[str],
    working_summaries_text: Optional[str],
    custom_instructions: Optional[str],
) -> str:
    base_prompt = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
    if custom_instructions:
        base_prompt = f"{base_prompt}\n\nAdditional focus: {custom_instructions}"

    prompt_text = ""
    if working_summaries_text:
        prompt_text += (
            "<working-summaries>\n"
            f"{working_summaries_text.strip()}\n"
            "</working-summaries>\n\n"
            "The working summaries above are durable, high-signal summaries previously produced by the ReAct agent for turns covered by the conversation slice. Use them as retrieval anchors and continuity context; do not treat them as user-facing messages.\n\n"
        )
    prompt_text += f"<conversation>\n{conversation_text}\n</conversation>\n\n"
    if previous_summary:
        prompt_text += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    prompt_text += base_prompt
    return prompt_text


def _build_turn_prefix_prompt_text(
    *,
    conversation_text: str,
    working_summaries_text: Optional[str],
    custom_instructions: Optional[str],
) -> str:
    prompt_text = ""
    if working_summaries_text:
        prompt_text += (
            "<working-summaries>\n"
            f"{working_summaries_text.strip()}\n"
            "</working-summaries>\n\n"
            "The working summaries above are durable, high-signal summaries previously produced by the ReAct agent for this turn. Use them to decide what prefix context matters for the retained suffix.\n\n"
        )
    prompt_text += f"<conversation>\n{conversation_text}\n</conversation>\n\n"
    base = TURN_PREFIX_SUMMARIZATION_PROMPT
    if custom_instructions:
        base = f"{base}\n\nAdditional focus: {custom_instructions}"
    prompt_text += base
    return prompt_text


async def summarize_context_blocks_progressive(
    *,
    svc: Any,
    blocks: List[dict],
    max_tokens: int = 800,
    previous_summary: Optional[str] = None,
    working_summary_blocks: Optional[List[dict]] = None,
    custom_instructions: Optional[str] = None,
) -> Optional[str]:
    if svc is None:
        return None
    try:
        from kdcube_ai_app.apps.chat.sdk.streaming.streaming import stream_agent_to_json

        conversation_text = _serialize_context_blocks_for_compaction(blocks)
        if not conversation_text:
            return None
        working_summaries_text = _serialize_context_blocks_for_compaction(working_summary_blocks or [])
        log.info(
            "[context.compaction.summary:start] role=context.compaction.summary blocks=%s chars=%s approx_tokens=%s previous_summary=%s working_summaries=%s max_tokens=%s",
            len(blocks or []),
            len(conversation_text),
            _approx_tokens(conversation_text),
            bool((previous_summary or "").strip()),
            len(working_summary_blocks or []),
            max_tokens,
        )

        prompt_text = _build_compaction_prompt_text(
            conversation_text=conversation_text,
            previous_summary=(previous_summary or "").strip() or None,
            working_summaries_text=working_summaries_text,
            custom_instructions=(custom_instructions or "").strip() or None,
        )
        system_msg = create_cached_system_message(SUMMARIZATION_SYSTEM_PROMPT, cache_last=True)
        user_message = create_cached_human_message(prompt_text)
        role = "context.compaction.summary"
        token_cap = max(1, int(max_tokens * 0.8)) if max_tokens else max_tokens
        async with with_accounting("context.compaction", agent=role, metadata={"agent": role}):
            result = await stream_agent_to_json(
                svc,
                client_name=role,
                client_role=role,
                sys_prompt=system_msg,
                messages=[user_message],
                schema_model=None,
                temperature=0.2,
                max_tokens=token_cap,
            )
        logging_helpers.log_agent_packet(role, "summary", result)
        summary = (result.get("agent_response") or "").strip()
        if not summary:
            log.warning(
                "[context.compaction.summary:empty] role=%s blocks=%s prompt_chars=%s raw_chars=%s error=%s service_error=%s",
                role,
                len(blocks or []),
                len(prompt_text),
                len(str((result.get("log") or {}).get("raw_data") or "")),
                (result.get("log") or {}).get("error"),
                (result.get("log") or {}).get("service_error"),
            )
            return None
        else:
            log.info(
                "[context.compaction.summary:result] role=%s blocks=%s output_chars=%s approx_output_tokens=%s",
                role,
                len(blocks or []),
                len(summary),
                _approx_tokens(summary),
            )
        read_files, modified_files = _extract_file_ops_from_blocks(blocks)
        summary += _format_file_ops_summary(read_files, modified_files)
        summary += _format_large_tool_result_recovery(_large_tool_result_recovery_rows(blocks))
        return summary
    except Exception:
        log.exception("[context.compaction.summary:error] blocks=%s", len(blocks or []))
        return None


async def summarize_turn_prefix_progressive(
    *,
    svc: Any,
    blocks: List[dict],
    max_tokens: int = 500,
    working_summary_blocks: Optional[List[dict]] = None,
    custom_instructions: Optional[str] = None,
) -> Optional[str]:
    if svc is None:
        return None
    try:
        from kdcube_ai_app.apps.chat.sdk.streaming.streaming import stream_agent_to_json

        conversation_text = _serialize_context_blocks_for_compaction(blocks)
        if not conversation_text:
            return None
        working_summaries_text = _serialize_context_blocks_for_compaction(working_summary_blocks or [])
        log.info(
            "[context.compaction.turn_prefix:start] role=context.compaction.turn_prefix blocks=%s chars=%s approx_tokens=%s working_summaries=%s max_tokens=%s",
            len(blocks or []),
            len(conversation_text),
            _approx_tokens(conversation_text),
            len(working_summary_blocks or []),
            max_tokens,
        )

        prompt_text = _build_turn_prefix_prompt_text(
            conversation_text=conversation_text,
            working_summaries_text=working_summaries_text,
            custom_instructions=(custom_instructions or "").strip() or None,
        )
        system_msg = create_cached_system_message(SUMMARIZATION_SYSTEM_PROMPT, cache_last=True)
        user_message = create_cached_human_message(prompt_text)
        role = "context.compaction.turn_prefix"
        token_cap = max(1, int(max_tokens * 0.8)) if max_tokens else max_tokens
        async with with_accounting("context.compaction", agent=role, metadata={"agent": role}):
            result = await stream_agent_to_json(
                svc,
                client_name=role,
                client_role=role,
                sys_prompt=system_msg,
                messages=[user_message],
                schema_model=None,
                temperature=0.2,
                max_tokens=token_cap,
            )
        logging_helpers.log_agent_packet(role, "summary", result)
        summary = (result.get("agent_response") or "").strip()
        if not summary:
            log.warning(
                "[context.compaction.turn_prefix:empty] role=%s blocks=%s prompt_chars=%s raw_chars=%s error=%s service_error=%s",
                role,
                len(blocks or []),
                len(prompt_text),
                len(str((result.get("log") or {}).get("raw_data") or "")),
                (result.get("log") or {}).get("error"),
                (result.get("log") or {}).get("service_error"),
            )
            return None
        log.info(
            "[context.compaction.turn_prefix:result] role=%s blocks=%s output_chars=%s approx_output_tokens=%s",
            role,
            len(blocks or []),
            len(summary),
            _approx_tokens(summary),
        )
        summary += _format_large_tool_result_recovery(_large_tool_result_recovery_rows(blocks))
        return summary
    except Exception:
        log.exception("[context.compaction.turn_prefix:error] blocks=%s", len(blocks or []))
        return None
