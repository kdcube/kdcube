# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The fork projection: what a child conversation opens with.

A fork is a projection copy, not a shared timeline: the parent's CURRENT-TURN
blocks (what the parent is amid) plus the parent conversation's WORKING
SUMMARIES (the compaction machinery's durable per-turn digests, including the
range summary when the parent has compacted) become the child conversation's
pre-existing history. Copied blocks keep their text, authorship, turn ids and
timestamps; the one mechanical rewrite is conversation-qualifying their refs.
Refs minted after qualification-at-birth already carry their home
``conv_<parent id>.`` scope segment (the rewrite is idempotent); the rewrite
pins any legacy conversation-local ref persisted before that, so every ref in
the copy names the parent conversation and resolves from the child.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    qualify_conversation_ref,
    qualify_conversation_refs_in_text,
)

FORK_HEADER_BLOCK_TYPE = "subagent.fork.header"
FORK_MARKER_BLOCK_TYPE = "react.subagent.fork"

WORKING_SUMMARY_BLOCK_TYPE = "conv.working.summary"
RANGE_SUMMARY_BLOCK_TYPE = "conv.range.summary"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def qualify_file_refs(block: Dict[str, Any], *, conversation_id: str) -> Dict[str, Any]:
    """Return a copy of ``block`` whose conversation-scoped refs carry the
    ``conv_<conversation_id>.`` scope segment (idempotent).

    Structured fields (``path``, ``refs``, ``meta.path``) and refs embedded in
    the block text are qualified with the block's home conversation, so a
    fork-copied block stays self-describing wherever it is rendered.
    """

    def _qualify(ref: Any) -> Any:
        if not isinstance(ref, str):
            return ref
        return qualify_conversation_ref(ref, conversation_id)

    out = copy.deepcopy(block)
    if out.get("path"):
        out["path"] = _qualify(out.get("path"))
    if isinstance(out.get("refs"), list):
        out["refs"] = [_qualify(r) for r in out["refs"]]
    meta = out.get("meta")
    if isinstance(meta, dict) and meta.get("path"):
        meta["path"] = _qualify(meta.get("path"))
    if isinstance(out.get("text"), str) and out.get("text"):
        out["text"] = qualify_conversation_refs_in_text(out["text"], conversation_id)
    return out


def build_fork_projection(
    *,
    parent_blocks: List[Dict[str, Any]],
    parent_current_turn_blocks: List[Dict[str, Any]],
    parent_conversation_id: str,
    parent_turn_id: str,
    child_conversation_id: str,
) -> List[Dict[str, Any]]:
    """Assemble the child conversation's seed blocks.

    Order: latest range summary (when present) first — the timeline persist
    window starts AT the newest ``conv.range.summary``, so anything placed
    before it would be sliced away — then the fork header, all working
    summaries (deduped by path, original order), then the parent's
    current-turn blocks. Every copied block keeps its content; ``conv:fi:``
    refs get the parent conversation scope segment.
    """
    fork_header: Dict[str, Any] = {
            "type": FORK_HEADER_BLOCK_TYPE,
            "author": "system",
            "turn_id": parent_turn_id,
            "ts": _now_iso(),
            "mime": "text/markdown",
            "path": f"conv:ar:{parent_turn_id}.subagent.fork.header",
            "text": (
                "[FORK]\n"
                f"This conversation opened as a fork of conversation "
                f"conv_{parent_conversation_id} at turn {parent_turn_id}.\n"
                "The blocks that follow are a copy of what the delegating agent "
                "saw: the conversation's working summaries, then its in-progress "
                "turn. They are context. The assignment arrives as the "
                "[SUBAGENT CHARTER] event after them.\n"
                f"Refs in these blocks carry their home conversation segment "
                f"(conv_{parent_conversation_id}.) and resolve as written — "
                "file refs are pullable with react.pull exactly as they appear."
            ),
            "meta": {
                "fork_of_conversation_id": parent_conversation_id,
                "fork_of_turn_id": parent_turn_id,
                "child_conversation_id": child_conversation_id,
            },
    }
    seed: List[Dict[str, Any]] = []

    current_paths = {
        str(b.get("path") or "")
        for b in parent_current_turn_blocks
        if isinstance(b, dict) and b.get("path")
    }

    range_summary: Optional[Dict[str, Any]] = None
    working_summaries: List[Dict[str, Any]] = []
    seen_summary_paths: set = set()
    for block in parent_blocks or []:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "").strip()
        if btype == RANGE_SUMMARY_BLOCK_TYPE:
            range_summary = block  # keep the LAST one
            continue
        if btype != WORKING_SUMMARY_BLOCK_TYPE:
            continue
        path = str(block.get("path") or "")
        if path and (path in seen_summary_paths or path in current_paths):
            continue
        seen_summary_paths.add(path)
        working_summaries.append(block)

    if range_summary is not None:
        seed.append(qualify_file_refs(range_summary, conversation_id=parent_conversation_id))
    seed.append(fork_header)
    for block in working_summaries:
        seed.append(qualify_file_refs(block, conversation_id=parent_conversation_id))
    for block in parent_current_turn_blocks or []:
        if not isinstance(block, dict):
            continue
        seed.append(qualify_file_refs(block, conversation_id=parent_conversation_id))
    return seed


def build_fork_marker_block(
    *,
    parent_turn_id: str,
    child_conversation_id: str,
    child_turn_id: str,
    charter_summary: str,
    max_rounds: int,
    agent_alias: str = "",
    agent_class: str = "",
    agent_title: str = "",
    tool_call_id: str = "",
) -> Dict[str, Any]:
    """The parent-timeline record of the spawn: child ref + charter caption +
    the helper identity (alias/class) the child runs as, plus the helper's
    human display title (``agent_title``) so a client can name the helper."""
    lines = [
        "[SUBAGENT FORKED]",
        f"child_conversation: conv_{child_conversation_id} (turn {child_turn_id})",
        f"charter: {charter_summary}",
    ]
    if agent_alias:
        alias_line = f"runs as: {agent_alias}"
        if agent_class:
            alias_line += f" [{agent_class}]"
        lines.append(alias_line)
    lines.append(f"budget: {int(max_rounds or 0)} rounds")
    lines.append(
        "The subagent works silently in its own conversation. Its reports arrive "
        "on this conversation's event lane as subagent.contribution events and a "
        "final subagent.converged (or subagent.failed) event. Contributed refs "
        "are pullable with react.pull as written."
    )
    block: Dict[str, Any] = {
        "type": FORK_MARKER_BLOCK_TYPE,
        "author": "assistant",
        "turn_id": parent_turn_id,
        "ts": _now_iso(),
        "mime": "text/markdown",
        "path": f"conv:ar:{parent_turn_id}.subagent.fork.{child_conversation_id}",
        "text": "\n".join(lines),
        "meta": {
            "child_conversation_id": child_conversation_id,
            "child_turn_id": child_turn_id,
            "charter_summary": charter_summary,
            "max_rounds": int(max_rounds or 0),
            **({"agent_alias": agent_alias} if agent_alias else {}),
            **({"agent_class": agent_class} if agent_class else {}),
            **({"agent_title": agent_title} if agent_title else {}),
        },
    }
    if tool_call_id:
        block["call_id"] = tool_call_id
        block["meta"]["tool_call_id"] = tool_call_id
    return block
