# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The conversation record, WRITE side — the home of the "conv timeline".

Everything a turn persists for the platform-owned conversation record lives
here, beside its read side (``view.py`` / ``api.py`` / ``export.py`` in this
package): the turn-log payload builders (minimal + error), the per-turn
recorded/surfaced signals, the timeline registration, and the
``conv.artifacts.stream`` payload + persistence (canvas / tool / subsystem
delta aggregates — what makes the code-exec panel and canvas streams replay on
reload). ``runtime/turn_recording.py`` remains as a compatibility re-export.

The chat component's conversation list / fetch / reload reads the per-turn
``artifact:turn.log`` — materialized into a ``chat:assistant`` record from an
``assistant.completion`` block. The React workflow writes a rich turn log at
``finish_turn``; a bundle that serves turns with **any other framework**
(LangGraph, LangChain, raw calls) via ``execute_core`` writes none, so its
turns stream live but leave no fetchable record.

This module makes conversation persistence framework-agnostic: after any turn,
if no turn log was written, record a **minimal** one carrying the assistant's
final answer (and, optionally, its progress steps). The user message is not
recorded here — it is persisted at ingress independent of the agent, and the
turn-log materializer skips user blocks anyway.

Idempotency uses a per-turn signal carried in a mutable dict on a ContextVar
(``_TURN_STATE``): whoever writes a turn log (React, or this module) mutates
``turn_log_recorded=True``; the platform fallback records the minimal log only
when that signal is unset. The dict is MUTATED, never the ContextVar reassigned,
so the signal crosses the asyncio task boundary React persists across — a child
task's copy of the context shares the same dict object (see the note on
``_TURN_STATE``). Reset at turn start, in run()'s task, before the framework
spawns its work tasks.
"""

from __future__ import annotations

import datetime as _dt
import json
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Sequence

# The block type the turn-log materializer turns into a ``chat:assistant`` row
# (see ctx_rag ``materialize_turn``). Kept as the single source of truth here.
ASSISTANT_COMPLETION_BLOCK_TYPE = "assistant.completion"

# Per-turn signals ("a turn log was written", "the failure was surfaced") live as
# keys in ONE mutable dict bound to this ContextVar at turn start. We MUTATE the
# dict; we never reassign the ContextVar. That is the whole point: asyncio copies
# the context when a task is created, and the copy shares the SAME dict object, so
# a signal set inside a child task (React persists its rich log in one) is visible
# to run()'s fallback check in the parent task. Reassigning a ContextVar *value*
# would not cross that boundary — that was the overwrite bug. `reset_*` at turn
# start binds the fresh dict in run()'s task, before the framework spawns its work.
_TURN_STATE: ContextVar[Optional[Dict[str, bool]]] = ContextVar("kdcube_turn_state", default=None)


def _turn_state(create: bool = False) -> Optional[Dict[str, bool]]:
    st = _TURN_STATE.get()
    if st is None and create:
        st = {}
        _TURN_STATE.set(st)
    return st


def reset_turn_log_recorded() -> None:
    """Call at turn start, in run()'s task, BEFORE the framework spawns work tasks.
    Binds a fresh shared per-turn state dict they all inherit and mutate."""
    _TURN_STATE.set({"turn_log_recorded": False, "turn_error_surfaced": False})


def mark_turn_log_recorded() -> None:
    """Call from any code path that persists a turn log for the current turn.
    Mutates the shared per-turn dict, so run()'s fallback sees it even when this
    runs in a different async task than run()."""
    st = _turn_state(create=True)
    st["turn_log_recorded"] = True


def turn_log_was_recorded() -> bool:
    st = _turn_state()
    return bool(st and st.get("turn_log_recorded"))


def reset_turn_error_surfaced() -> None:
    """Call at turn start (before the bundle handles the turn)."""
    _turn_state(create=True)["turn_error_surfaced"] = False


def mark_turn_error_surfaced() -> None:
    """Call from any code path that surfaces a turn failure to the client and
    owns the failed turn's outcome (emit + rollback/record). Mutates the shared
    per-turn dict so run()'s backstop sees it across task boundaries."""
    _turn_state(create=True)["turn_error_surfaced"] = True


def turn_error_was_surfaced() -> bool:
    st = _turn_state()
    return bool(st and st.get("turn_error_surfaced"))


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _user_prompt_block(text: str, *, turn_id: str, ts: str) -> Optional[Dict[str, Any]]:
    """A ``user.prompt`` block — the reload reader (``iter_turn_user_input_entries``)
    rebuilds the ``chat:user`` bubble from it, exactly like the React turn log."""
    body = str(text or "").strip()
    if not body:
        return None
    return {
        "type": "user.prompt",
        "author": "user",
        "turn_id": turn_id,
        "turn": turn_id,
        "ts": ts,
        "mime": "text/markdown",
        "path": f"conv:ar:{turn_id}.user.prompt",
        "text": body,
        "meta": {"message_id": "m0", "event_type": "event.user.prompt", "turn_id": turn_id},
    }


def _user_attachment_meta_block(att: Dict[str, Any], *, turn_id: str, ts: str) -> Optional[Dict[str, Any]]:
    """A ``user.attachment.meta`` block for one uploaded file — reloads as an
    ``artifact:user.attachment`` row and carries a pullable ``conv:fi:`` ref, mirroring
    the React user-attachment block. Requires a real basename filename."""
    if not isinstance(att, dict):
        return None
    filename = str(att.get("filename") or "").strip()
    if not filename:
        return None
    mime = str(att.get("mime") or "application/octet-stream").strip()
    attachment_path = f"conv:fi:{turn_id}.user.attachments/{filename}"
    physical_path = str(att.get("physical_path") or att.get("local_path") or "").strip()
    digest = {
        "artifact_path": attachment_path,
        "physical_path": physical_path,
        "mime": mime,
        "kind": "file",
        "visibility": "external",
        "ts": ts,
    }
    meta: Dict[str, Any] = {"filename": filename, "mime": mime, "turn_id": turn_id, "message_id": "m0"}
    for key in ("hosted_uri", "rn", "key"):
        val = str(att.get(key) or "").strip()
        if val:
            meta[key] = val
    if physical_path:
        meta["physical_path"] = physical_path
    summary = str(att.get("summary") or "").strip()
    if summary:
        meta["summary"] = summary
    return {
        "type": "user.attachment.meta",
        "author": "user",
        "turn_id": turn_id,
        "turn": turn_id,
        "ts": ts,
        "mime": "application/json",
        "path": attachment_path,
        "text": json.dumps(digest, ensure_ascii=False),
        "meta": meta,
    }


def _assistant_file_block(row: Dict[str, Any], *, turn_id: str, ts: str, index: int) -> Optional[Dict[str, Any]]:
    """A ``react.tool.result`` JSON block for one assistant-hosted file — reloads as
    an ``artifact:assistant.file`` row via ``extract_assistant_files_from_blocks``.
    Requires an ``artifact_path`` (the ``logical_path`` / ``conv:fi:`` ref) and at
    least one stored ref (hosted_uri/rn/key/physical_path)."""
    if not isinstance(row, dict):
        return None
    artifact_path = str(row.get("logical_path") or row.get("artifact_path") or "").strip()
    hosted_uri = str(row.get("hosted_uri") or "").strip()
    rn = str(row.get("rn") or "").strip()
    key = str(row.get("key") or "").strip()
    physical_path = str(row.get("physical_path") or "").strip()
    if not artifact_path or not (hosted_uri or rn or key or physical_path):
        return None
    call_id = f"code_exec_{index}"
    meta_json: Dict[str, Any] = {
        "artifact_path": artifact_path,
        "physical_path": physical_path or str(row.get("filename") or ""),
        "mime": str(row.get("mime") or "application/octet-stream"),
        "kind": "file",
        "visibility": "external",
        "tool_call_id": call_id,
        "filename": str(row.get("filename") or ""),
        "ts": ts,
    }
    if hosted_uri:
        meta_json["hosted_uri"] = hosted_uri
    if rn:
        meta_json["rn"] = rn
    if key:
        meta_json["key"] = key
    tool_id = str(row.get("tool_id") or "").strip()
    if tool_id:
        meta_json["tool_id"] = tool_id
    return {
        "type": "react.tool.result",
        "turn_id": turn_id,
        "turn": turn_id,
        "call_id": call_id,
        "mime": "application/json",
        "path": f"conv:tc:{turn_id}.{call_id}.result",
        "text": json.dumps(meta_json, ensure_ascii=False),
        "ts": ts,
        "meta": {"tool_call_id": call_id},
    }


# ── the conv.artifacts.stream payload (canvas / tool / subsystem deltas) ────
#
# The exec widget, canvas patches, and other subsystem streams ride
# `comm.delta(marker="subsystem"|"canvas"|"tool", ...)`. The communicator
# aggregates every delta per (turn, agent, marker, artifact) with its `extra`
# (sub_type, execution_id, ...) intact; persisting those aggregates as the
# `conv.artifacts.stream` artifact is what lets a RELOADED conversation replay
# them (the client re-emits each row as a synthetic completed `chat.delta`,
# rebuilding e.g. the code-exec panel). The React workflow and the
# framework-neutral fallback both persist through THIS builder, so the stored
# shape cannot drift between them. (The read side — how the fetch view renders
# this artifact — lives in `solutions/conversation/view.py`.)

STREAM_ARTIFACT_MARKERS = ("canvas", "tool", "subsystem")


def build_stream_artifact_payload(all_deltas: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Shape delta aggregates into the `conv.artifacts.stream` artifact payload.

    Keeps the canvas/tool/subsystem aggregates that carry content, returning
    `{"content": {...}, "content_str": "..."}` — `content` holds the full rows
    (text kept, raw chunks dropped for size), `content_str` a text-size index
    the conversation search consumes. None when the turn streamed nothing to
    persist (the artifact is then not written at all)."""
    blocks = [
        d for d in (all_deltas or [])
        if isinstance(d, dict)
        and d.get("marker") in STREAM_ARTIFACT_MARKERS
        and (d.get("text") or d.get("chunks"))
    ]
    if not blocks:
        return None
    full = [
        {**{k: v for k, v in item.items() if k != "chunks"},
         "chunks_num": len(item.get("chunks") or [])}
        for item in blocks
    ]
    idx = [
        {**{k: v for k, v in item.items() if k not in ("text", "chunks")},
         "text_size": len(item.get("text") or ""),
         "chunks_num": len(item.get("chunks") or [])}
        for item in blocks
    ]
    return {
        "content": {"version": "v1", "items": full},
        "content_str": json.dumps(idx),
    }


def build_minimal_turn_log_payload(
    *,
    final_answer: str,
    turn_id: str,
    steps: Optional[Sequence[Dict[str, Any]]] = None,
    ts: Optional[str] = None,
    conversation_title: Optional[str] = None,
    user_prompt_text: str = "",
    user_attachments: Optional[Sequence[Dict[str, Any]]] = None,
    assistant_files: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The smallest valid turn-log payload. Records the assistant's final answer, and
    now also — so a run-to-completion turn reloads like React — the USER prompt, any
    USER attachment refs, and any assistant-hosted FILE refs. Block shapes match the
    shared reload reader (``Timeline.build_turn_view`` + ``iter_turn_user_input_entries``).
    Shape matches ``ctx_rag.save_turn_log_as_artifact`` (``V2TurnLog.from_dict``):
    ``{ts, blocks, blocks_count}``.

    ``conversation_title`` (first turn only) is carried on the payload for symmetry
    with the React turn log; the conversation LIST reads the title from the
    per-conversation timeline artifact, not the turn log — see
    :func:`record_conversation_timeline`.
    """
    now = ts or _utc_iso()
    blocks: List[Dict[str, Any]] = []
    # 1) the user's message (reloads the chat:user bubble)
    user_block = _user_prompt_block(user_prompt_text, turn_id=turn_id, ts=now)
    if user_block:
        blocks.append(user_block)
    # 2) the user's uploaded attachments (reload + pullable conv:fi: refs)
    for att in user_attachments or []:
        att_block = _user_attachment_meta_block(att, turn_id=turn_id, ts=now)
        if att_block:
            blocks.append(att_block)
    # 3) the agent's progress steps
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        text = str(step.get("text") or step.get("step") or "").strip()
        if not text:
            continue
        blocks.append({
            "type": "assistant.step",
            "turn": turn_id,
            "text": text,
            "ts": str(step.get("ts") or now),
            "meta": {"status": str(step.get("status") or "")},
        })
    # 4) files the agent produced (reload + pullable conv:fi: refs)
    for i, row in enumerate(assistant_files or []):
        file_block = _assistant_file_block(row, turn_id=turn_id, ts=now, index=i)
        if file_block:
            blocks.append(file_block)
    # 5) the final answer
    blocks.append({
        "type": ASSISTANT_COMPLETION_BLOCK_TYPE,
        "turn": turn_id,
        "text": str(final_answer or ""),
        "ts": now,
        "meta": {},
    })
    payload: Dict[str, Any] = {"ts": now, "end_ts": now, "blocks": blocks, "blocks_count": len(blocks)}
    title = str(conversation_title or "").strip()
    if title:
        payload["conversation_title"] = title
    return payload


def build_error_turn_log_payload(
    *,
    error_message: str,
    turn_id: str,
    error_type: Optional[str] = None,
    steps: Optional[Sequence[Dict[str, Any]]] = None,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    """A minimal turn-log payload for a **failed** turn: an ``assistant.completion``
    block carrying the error text (so the conversation saves and reloads as an
    errored turn), marked ``meta.error`` so the client can render it distinctly.
    Same shape as :func:`build_minimal_turn_log_payload`.
    """
    now = ts or _utc_iso()
    text = str(error_message or "").strip() or "An error occurred."
    blocks: List[Dict[str, Any]] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        step_text = str(step.get("text") or step.get("step") or "").strip()
        if not step_text:
            continue
        blocks.append({
            "type": "assistant.step",
            "turn": turn_id,
            "text": step_text,
            "ts": str(step.get("ts") or now),
            "meta": {"status": str(step.get("status") or "")},
        })
    blocks.append({
        "type": ASSISTANT_COMPLETION_BLOCK_TYPE,
        "turn": turn_id,
        "text": text,
        "ts": now,
        "meta": {"error": True, "error_type": str(error_type or "")},
    })
    return {"ts": now, "end_ts": now, "blocks": blocks, "blocks_count": len(blocks)}


async def record_error_turn_log_if_absent(
    *,
    conversation_client: Any,
    tenant: str,
    project: str,
    user: str,
    user_type: str,
    conversation_id: str,
    turn_id: str,
    bundle_id: str,
    error_message: str,
    agent_id: Optional[str] = None,
    error_type: Optional[str] = None,
    steps: Optional[Sequence[Dict[str, Any]]] = None,
) -> bool:
    """Record a minimal **failed** turn log when none was written this turn.

    Mirror of :func:`record_minimal_turn_log_if_absent` for the failure path: it
    persists the error as an ``assistant.completion`` block (marked ``error``) so
    the conversation is saved and reloads as an errored turn. No-op when a turn
    log was already recorded this turn. Returns whether it wrote.
    """
    if turn_log_was_recorded():
        return False
    save = getattr(conversation_client, "save_turn_log_as_artifact", None)
    if not callable(save):
        return False
    payload = build_error_turn_log_payload(
        error_message=error_message, turn_id=turn_id, error_type=error_type, steps=steps,
    )
    await save(
        tenant=tenant, project=project, user=user,
        conversation_id=conversation_id, user_type=user_type,
        turn_id=turn_id, bundle_id=bundle_id, agent_id=agent_id,
        payload=payload,
    )
    mark_turn_log_recorded()
    return True


async def _latest_conversation_timeline(
    *,
    conversation_client: Any,
    user: str,
    conversation_id: str,
    bundle_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The most-recent ``conv.timeline.v1`` index metadata for (user, conv), or
    ``None`` when there is none / it can't be read.

    Returns the parsed compact index text (``conversation_title``,
    ``conversation_started_at``, ...) so a later turn can carry the title +
    started_at forward when it refreshes the timeline (mirroring the React
    timeline, which persists the whole timeline — title included — every turn).
    """
    recent = getattr(conversation_client, "recent", None)
    if not callable(recent):
        return None
    from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import TIMELINE_KIND
    try:
        res = await recent(
            kinds=[f"artifact:{TIMELINE_KIND}"],
            roles=("artifact",),
            limit=1,
            days=3650,
            user_id=user,
            conversation_id=conversation_id,
            bundle_id=bundle_id,
        )
    except Exception:
        return None
    items = list((res or {}).get("items") or [])
    if not items:
        return None
    try:
        return json.loads(items[0].get("text") or "") or {}
    except Exception:
        # A timeline exists but its index text didn't parse — treat as present
        # (empty metadata) so the caller still refreshes without clobbering intent.
        return {}


async def record_conversation_timeline(
    *,
    conversation_client: Any,
    tenant: str,
    project: str,
    user: str,
    user_type: str,
    conversation_id: str,
    turn_id: str,
    bundle_id: str,
    conversation_title: str = "",
    conversation_started_at: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> bool:
    """Register / refresh a conversation on the conversation list for this turn.

    The chat component's conversation LIST is built from the per-conversation
    ``conv.timeline.v1`` artifact (``ctx_rag.list_conversations`` reads ONLY that
    artifact's index text — never the turn log), the title comes from the same
    artifact (key ``conversation_title``), and the list sorts by its
    ``last_activity_at``. The React workflow persists that artifact every turn from
    its timeline, so a React conversation always lists and floats up on activity. A
    bundle that serves turns without the React timeline (LangGraph, LangChain, raw
    calls) writes none, so its conversations would never list — even after
    recording a turn log. This writes the minimal timeline artifact those readers
    parse, once per recorded turn, so a run-to-completion conversation lists exactly
    like a React one.

    Title / started_at are carried forward: a first turn supplies the generated
    title; a later turn (or a first turn whose title generation failed) supplies
    none, so the prior timeline's title + started_at are re-read and re-persisted —
    the refresh advances ``last_activity_at`` without ever shadowing an earlier
    title with a blank one.

    Reuses the React timeline primitives (the single source of truth for the
    artifact kind + payload shape). No-op on a client without ``save_artifact``.
    Returns whether it wrote.
    """
    save = getattr(conversation_client, "save_artifact", None)
    if not callable(save):
        return False
    title = str(conversation_title or "").strip()
    started_at = str(conversation_started_at or "").strip()
    # Carry the title + started_at forward from any existing timeline so a later,
    # title-less turn refreshes recency without dropping the title.
    prior = await _latest_conversation_timeline(
        conversation_client=conversation_client,
        user=user, conversation_id=conversation_id, bundle_id=bundle_id,
    )
    if prior is None:
        # No existing timeline. If this turn also carries no title, we can't read
        # one back — but we must still register the conversation so it lists, so
        # write a (title-less) timeline now; a later titled turn upgrades it.
        prior = {}
    if not title:
        title = str(prior.get("conversation_title") or "").strip()
    if not started_at:
        started_at = str(prior.get("conversation_started_at") or "").strip() or _utc_iso()
    # Reuse the React timeline primitives (the single source of truth for the
    # artifact kind + payload shape) rather than re-deriving the schema here.
    from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import (
        TIMELINE_KIND,
        build_timeline_payload,
    )
    payload = build_timeline_payload(
        blocks=[],
        conversation_title=title,
        conversation_started_at=started_at,
        include_sources_pool=True,
    )
    # The index text is what the conversation list/detail json-parse; keep it
    # compact (mirrors the React timeline's own compact index text). The title key
    # is present-but-empty when untitled — the list simply omits the title then.
    content_str = json.dumps(
        {
            "conversation_title": title,
            "conversation_started_at": started_at,
            "last_activity_at": payload.get("last_activity_at") or _utc_iso(),
            "blocks_count": 0,
            "sources_pool_count": 0,
            "turn_ids": [turn_id] if turn_id else [],
        },
        ensure_ascii=False,
    )
    await save(
        kind=TIMELINE_KIND,
        tenant=tenant, project=project, user_id=user,
        conversation_id=conversation_id, user_type=user_type,
        turn_id=turn_id, bundle_id=bundle_id, agent_id=agent_id,
        content=payload, content_str=content_str,
        extra_tags=[f"turn:{turn_id}"] if turn_id else None,
    )
    return True


async def record_minimal_turn_log_if_absent(
    *,
    conversation_client: Any,
    tenant: str,
    project: str,
    user: str,
    user_type: str,
    conversation_id: str,
    turn_id: str,
    bundle_id: str,
    final_answer: str,
    agent_id: Optional[str] = None,
    steps: Optional[Sequence[Dict[str, Any]]] = None,
    conversation_title: Optional[str] = None,
    user_prompt_text: str = "",
    user_attachments: Optional[Sequence[Dict[str, Any]]] = None,
    assistant_files: Optional[Sequence[Dict[str, Any]]] = None,
) -> bool:
    """Record the minimal turn log when none was written this turn.

    No-op when a turn log was already recorded (React path) or there is no
    final answer to record. Returns whether it wrote. ``conversation_client`` is
    the platform's ``ContextRAGClient`` (has ``save_turn_log_as_artifact``);
    the caller owns it (the processor / turn lifecycle), not the bundle.

    Every recorded turn also registers/refreshes the conversation on the
    conversation list via :func:`record_conversation_timeline` — the list is built
    ONLY from the ``conv.timeline.v1`` artifact, so without this a run-to-completion
    conversation records its turn log yet never appears in the list. The title
    (first turn) rides on that same registration when present; a later turn (or a
    first turn whose title generation failed) carries the prior title forward while
    advancing recency. Best-effort: a registration failure never undoes the
    recorded answer.
    """
    if turn_log_was_recorded():
        return False
    answer = str(final_answer or "").strip()
    if not answer:
        return False
    save = getattr(conversation_client, "save_turn_log_as_artifact", None)
    if not callable(save):
        return False
    payload = build_minimal_turn_log_payload(
        final_answer=answer, turn_id=turn_id, steps=steps,
        conversation_title=conversation_title,
        user_prompt_text=user_prompt_text,
        user_attachments=user_attachments,
        assistant_files=assistant_files,
    )
    await save(
        tenant=tenant, project=project, user=user,
        conversation_id=conversation_id, user_type=user_type,
        turn_id=turn_id, bundle_id=bundle_id, agent_id=agent_id,
        payload=payload,
    )
    mark_turn_log_recorded()
    try:
        await record_conversation_timeline(
            conversation_client=conversation_client,
            tenant=tenant, project=project, user=user, user_type=user_type,
            conversation_id=conversation_id, turn_id=turn_id, bundle_id=bundle_id,
            conversation_title=conversation_title or "", agent_id=agent_id,
        )
    except Exception:
        # The answer is already recorded; a registration failure is non-fatal.
        pass
    return True


async def persist_stream_artifacts(
    *,
    comm: Any,
    ctx_client: Any,
    tenant: str,
    project: str,
    user_id: str,
    user_type: str,
    conversation_id: str,
    turn_id: str,
    bundle_id: str,
    agent_id: Optional[str] = None,
) -> bool:
    """Persist this turn's canvas/tool/subsystem delta aggregates as the
    ``conv.artifacts.stream`` artifact, and clear the aggregates.

    The reusable write: any framework door calls it with its identity (the
    React workflow persists through the same payload builder inside
    ``execute_core``; the framework-neutral fallback calls this after the
    turn). Returns True when an artifact was written; False when the turn
    streamed nothing to persist (nothing is written, aggregates untouched)."""
    all_deltas = comm.get_delta_aggregates(
        conversation_id=conversation_id, turn_id=turn_id, merge_text=True
    )
    payload = build_stream_artifact_payload(all_deltas)
    if payload is None:
        return False
    await ctx_client.save_artifact(
        kind="conv.artifacts.stream",
        tenant=tenant, project=project,
        turn_id=turn_id,
        user_id=user_id,
        conversation_id=conversation_id,
        bundle_id=bundle_id,
        agent_id=agent_id,
        user_type=user_type,
        content=payload["content"],
        content_str=payload["content_str"],
        extra_tags=["conversation", "stream", "canvas"],
    )
    comm.clear_delta_aggregates(conversation_id=conversation_id, turn_id=turn_id)
    return True
