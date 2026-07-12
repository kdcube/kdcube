# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import traceback
from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.charter import (
    configured_max_rounds,
    parse_charter,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.events import ParentLaneAddress
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.fork import (
    build_fork_marker_block,
    build_fork_projection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.launch import SubagentLaunchRequest
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    add_block,
    notice_block,
    tc_result_path,
    tool_call_block,
)

# The spec is fully static: identical for every user and configuration (it
# lives in the cached system instruction). Situational identity — the
# available helper aliases, your own strength class, live delegations — is
# rendered per round in the announce block's DELEGATION section.
TOOL_SPEC = {
    "id": "react.delegate",
    "purpose": (
        "Hand a self-contained assignment to a separate agent similar to you. "
        "It runs in parallel in its own conversation: it opens with a copy of "
        "your context summary plus your assignment, inherits your tools, and "
        "reports back onto this conversation's timeline — contributions along "
        "the way, then a final converged (or failed) report. If you have "
        "already finished your turn, the final report arrives as a follow-up "
        "turn. The call returns immediately with the child conversation ref, "
        "and a marker block records the spawn on your timeline; keep working "
        "and finish your turn whenever you are done, even with helpers still "
        "running. Contributed refs are pullable with react.pull as written. "
        "Delegate when the assignment needs more capability than yours — "
        "deeper reasoning, broader knowledge, work whose quality depends on "
        "conducting real research (forming queries, recognizing incomplete "
        "results, re-querying, critically analyzing, compiling) — or when a "
        "sizable self-contained piece can genuinely run alongside your own "
        "work. Do quick work yourself: a delegation costs like a full agent "
        "of the chosen strength. A helper completes its own assignment and "
        "works alone (it cannot spawn helpers or ask you or the user "
        "questions)."
    ),
    "args": {
        "charter": (
            "str. The self-contained assignment prompt: the goal and what to "
            "send back (deliverables, contribution expectations)."
        ),
        "agent_alias": (
            "str, optional. Which helper runs the assignment. The available "
            "aliases, each with its strength class and what it is good for, "
            "are listed in the DELEGATION section of the announce block — read "
            "them there. Omit to use the configured default."
        ),
    },
    "returns": (
        "launch ticket {child_conversation_id, child_conversation_ref, child_turn_id, "
        "status} — the helper runs as its own scheduled turn after this returns"
    ),
}


async def handle_react_delegate(
    *, react: Any = None, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str
) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.delegate"
    params = tool_call.get("params") or {}
    runtime_ctx = getattr(ctx_browser, "runtime_ctx", None)
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "")

    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "params": params,
        },
    )

    def _fail(code: str, message: str) -> Dict[str, Any]:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code=code,
            message=message,
            rel="result",
        )
        payload = {"status": "rejected", "code": code, "message": message}
        add_block(ctx_browser, {
            "turn": turn_id,
            "type": "react.tool.result",
            "call_id": tool_call_id,
            "tool_id": tool_id,
            "mime": "application/json",
            "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
            "text": json.dumps(payload, ensure_ascii=False, indent=2),
            "meta": {"tool_call_id": tool_call_id},
        })
        state["last_tool_result"] = payload
        return state

    subagent_defaults = getattr(runtime_ctx, "subagent_defaults", None)
    subagent_defaults = dict(subagent_defaults) if isinstance(subagent_defaults, dict) else {}
    charter, err = parse_charter(
        params,
        max_rounds=configured_max_rounds(subagent_defaults),
    )
    if charter is None:
        return _fail(
            "delegate_missing_goal",
            "react.delegate requires the charter — the assignment prompt, self-contained.",
        )
    depth = int(getattr(runtime_ctx, "subagent_depth", 0) or 0)
    if depth >= 1:
        return _fail(
            "delegate_depth_limit",
            "A subagent completes its own charter; it does not spawn subagents.",
        )
    spawner = getattr(runtime_ctx, "subagent_spawner", None)
    if spawner is None or not hasattr(spawner, "spawn"):
        return _fail(
            "delegate_unavailable",
            "Subagent spawning is not wired for this runtime.",
        )

    parent = ParentLaneAddress.from_runtime_ctx(runtime_ctx)
    try:
        timeline = getattr(ctx_browser, "timeline", None)
        parent_blocks = list(getattr(timeline, "blocks", None) or [])
        current_turn_blocks = list(ctx_browser.current_turn_blocks() or [])
    except Exception:
        parent_blocks = []
        current_turn_blocks = []

    request = SubagentLaunchRequest(
        charter=charter,
        parent=parent,
        fork_blocks=[],  # filled below with the child conversation id unknown yet
        allowed_plugins=list(getattr(react, "_run_allowed_plugins", None) or []) or None,
        allowed_tool_names_by_alias=getattr(react, "_run_allowed_tool_names_by_alias", None),
        parent_depth=depth,
        tool_call_id=tool_call_id,
    )
    # The projection is child-id independent (refs are qualified with the
    # PARENT conversation id), so it is built once here.
    request.fork_blocks = build_fork_projection(
        parent_blocks=parent_blocks,
        parent_current_turn_blocks=current_turn_blocks,
        parent_conversation_id=parent.conversation_id,
        parent_turn_id=parent.turn_id,
        child_conversation_id="",
    )

    try:
        ticket = await spawner.spawn(request)
    except Exception as exc:
        try:
            log = getattr(react, "log", None)
            if log is not None:
                log.log(f"[react.delegate] spawn failed: {traceback.format_exc()}", level="ERROR")
        except Exception:
            pass
        from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.schedule import (
            SubagentEnqueueRejected,
        )

        if isinstance(exc, SubagentEnqueueRejected):
            return _fail(
                "delegate_queue_saturated",
                f"The task queue declined the subagent right now ({exc.reason}). "
                "Continue with your own work and delegate again later if still worthwhile.",
            )
        return _fail("delegate_spawn_failed", f"Subagent spawn failed: {exc}")

    # The effective helper identity (alias + strength class) is resolved from
    # the same config the child resolves against; the marker records it so
    # the announce's DELEGATION section can state what the helper runs as.
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
        resolve_subagent_alias,
        subagent_alias_map,
        subagent_default_pick,
    )

    alias_map = subagent_alias_map(subagent_defaults)
    if charter.agent_alias:
        effective_alias, _pick = resolve_subagent_alias(charter.agent_alias, alias_map)
    else:
        effective_alias, _pick = subagent_default_pick(subagent_defaults, alias_map)
    effective_class = (
        str((alias_map.get(effective_alias) or {}).get("class") or "")
        if effective_alias
        else ""
    )
    marker = build_fork_marker_block(
        parent_turn_id=parent.turn_id,
        child_conversation_id=ticket.child_conversation_id,
        child_turn_id=ticket.child_turn_id,
        charter_summary=charter.summary_line(),
        max_rounds=charter.max_rounds,
        agent_alias=effective_alias or "",
        agent_class=effective_class,
        tool_call_id=tool_call_id,
    )
    add_block(ctx_browser, marker)

    payload = ticket.to_dict()
    payload["charter"] = charter.to_dict()
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "tool_id": tool_id,
        "mime": "application/json",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
        "meta": {"tool_call_id": tool_call_id},
    })
    state["last_tool_result"] = payload
    return state
