# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Authoring ``subagent.*`` events into a conversation's event lane.

The pipe is the sanctioned inception primitive consent grants ride on: a
``ConversationExternalEvent`` published into the per-conversation lane via
``RedisConversationExternalEventSource.publish``. The transport ``kind`` is
uniformly ``"external_event"``; the semantic type rides nested in
``payload.event.type``.

Every ``subagent.*`` event keeps ``reactive: False`` in the nested event: a
live turn folds it without buying iteration credit. Promotability is the
separate axis, selected per event by the ``task_payload``:

- ``task_payload=None`` — passive (``subagent.contribution``): the promoter
  acks it; a LIVE turn folds it through the lane watcher, an idle lane holds
  it as context the next turn folds in.
- ``task_payload`` set — promotable (the charter on the child lane, the
  ``subagent.converged``/``subagent.failed`` completions on the parent
  lane): when no turn is live on the lane, the promoter starts the described
  turn; a live turn that folds it first consumes it, and the promoter acks
  instead (exactly-once).

The react timeline fold renders every folded event and advances the lane
cursor (fold totality), so no ``subagent.*`` type needs bespoke render
support.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import qualify_conversation_ref

LOGGER = logging.getLogger("kdcube.react.subagents")

SUBAGENT_EVENT_TRANSPORT_KIND = "external_event"
SUBAGENT_EVENT_SOURCE_ID = "react.subagent"
SUBAGENT_CHARTER_EVENT_KIND = "subagent.charter"
SUBAGENT_CONTRIBUTION_EVENT_KIND = "subagent.contribution"
SUBAGENT_CONVERGED_EVENT_KIND = "subagent.converged"
SUBAGENT_FAILED_EVENT_KIND = "subagent.failed"


def build_subagent_stamp(
    *,
    child_conversation_id: str,
    parent_conversation_id: str,
    parent_turn_id: str,
    charter_goal: str,
    agent_title: str = "",
) -> Dict[str, Any]:
    """The subagent envelope stamp — one shape everywhere.

    Rides as the top-level ``subagent`` key on every thread-mode live
    emission AND inside the structured ``facts`` of every ``subagent.*``
    lane event (charter, contribution, converged/failed), so clients anchor
    subagent traffic into threads without parsing text. ``agent_title`` is the
    helper's human display name (set by the delegating agent), so a client can
    name the helper wherever it renders subagent traffic."""
    return {
        "child_conversation_id": str(child_conversation_id or ""),
        "forked_from_conversation_id": str(parent_conversation_id or ""),
        "forked_from_turn_id": str(parent_turn_id or ""),
        "charter_goal": str(charter_goal or ""),
        "agent_title": str(agent_title or ""),
    }


@dataclass
class ParentLaneAddress:
    """The full lane address a subagent reports back to."""

    tenant: str = ""
    project: str = ""
    user_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""
    agent_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant": self.tenant,
            "project": self.project,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "agent_id": self.agent_id,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ParentLaneAddress":
        raw = raw if isinstance(raw, dict) else {}
        return cls(
            tenant=str(raw.get("tenant") or ""),
            project=str(raw.get("project") or ""),
            user_id=str(raw.get("user_id") or ""),
            conversation_id=str(raw.get("conversation_id") or ""),
            turn_id=str(raw.get("turn_id") or ""),
            agent_id=str(raw.get("agent_id") or ""),
        )

    @classmethod
    def from_runtime_ctx(cls, runtime_ctx: Any) -> "ParentLaneAddress":
        return cls(
            tenant=str(getattr(runtime_ctx, "tenant", "") or ""),
            project=str(getattr(runtime_ctx, "project", "") or ""),
            user_id=str(getattr(runtime_ctx, "user_id", "") or ""),
            conversation_id=str(getattr(runtime_ctx, "conversation_id", "") or ""),
            turn_id=str(getattr(runtime_ctx, "turn_id", "") or ""),
            agent_id=str(getattr(runtime_ctx, "agent_id", "") or ""),
        )


def build_lane_source(*, redis: Any, address: ParentLaneAddress) -> Any:
    from kdcube_ai_app.apps.chat.external_events import (
        build_conversation_external_event_source,
    )

    return build_conversation_external_event_source(
        redis=redis,
        tenant=address.tenant,
        project=address.project,
        conversation_id=address.conversation_id,
        user_id=address.user_id,
        agent_id=address.agent_id or "main",
    )


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


async def prepare_subagent_event(
    *,
    lane_source: Any,
    semantic_type: str,
    text: str,
    facts: Optional[Dict[str, Any]] = None,
    author: str = "",
    target_turn_id: Optional[str] = None,
    task_payload: Optional[Dict[str, Any]] = None,
) -> Any:
    """Build one ``subagent.*`` event, prepared but not yet in the lane.

    Used by the atomic scheduling path: the gateway's atomic enqueue script
    writes the lane occurrence itself, all-or-nothing with the processor
    wakeup, so the event must arrive prepared rather than published.
    :func:`publish_subagent_event` is the direct-publish counterpart.
    """
    facts = dict(facts or {})
    event_ts = _utc_iso()
    return await lane_source.prepare_event(
        kind=SUBAGENT_EVENT_TRANSPORT_KIND,
        source=author or SUBAGENT_EVENT_SOURCE_ID,
        event_source_id=SUBAGENT_EVENT_SOURCE_ID,
        text=text,
        target_turn_id=target_turn_id,
        payload={
            "text": text,
            "event": {
                "type": semantic_type,
                "event_source_id": SUBAGENT_EVENT_SOURCE_ID,
                "reactive": False,
                "timestamp": event_ts,
                # The nested payload.event carries the model-facing sentence
                # plus the structured facts; the timeline fold surfaces it as
                # the event block's `ret` body.
                "payload": {
                    "mime": "text/markdown",
                    "event": {
                        "text": text,
                        **facts,
                    },
                },
            },
            **facts,
        },
        task_payload=task_payload,
    )


async def publish_subagent_event(
    *,
    lane_source: Any,
    semantic_type: str,
    text: str,
    facts: Optional[Dict[str, Any]] = None,
    author: str = "",
    target_turn_id: Optional[str] = None,
    task_payload: Optional[Dict[str, Any]] = None,
) -> Any:
    """Author one ``subagent.*`` event into ``lane_source``'s conversation.

    ``facts`` is the structured body (child conversation ref, contributed
    refs, charter summary...). It lands both beside the text in the nested
    model-facing event body and at the payload top level for programmatic
    consumers.

    ``task_payload`` selects the event's promotability axis. ``None`` (the
    default) authors a passive event: the promoter acks it, a live turn folds
    it, it can never start a turn (``subagent.contribution`` stays here).
    A non-None task payload (an ``ExternalEventPayload``-shaped dict) makes
    the event promotable: when no turn is live on the lane, the promoter
    starts the described turn from it (the charter on the child lane, the
    completions on the parent lane). Both kinds keep ``reactive: False`` in
    the nested event — a subagent event never buys iteration credit inside a
    live turn; promotability-when-idle is the separate axis.
    """
    event = await prepare_subagent_event(
        lane_source=lane_source,
        semantic_type=semantic_type,
        text=text,
        facts=facts,
        author=author,
        target_turn_id=target_turn_id,
        task_payload=task_payload,
    )
    await lane_source.publish_prepared_events([event])
    LOGGER.info(
        "[react.subagents] event authored: conversation=%s type=%s author=%s seq=%s",
        getattr(lane_source, "conversation_id", ""),
        semantic_type,
        author,
        getattr(event, "sequence", None),
    )
    return event


def contribution_refs_for_parent(
    *, refs: List[str], child_conversation_id: str
) -> List[str]:
    """Return the child's logical refs carrying their home conversation scope.

    Each conversation-scoped ref crosses to the parent with the
    ``conv_<child id>.`` segment right after its namespace, so resolvers in the
    parent conversation route the lookup to the child's store (react.pull
    routes by the embedded conversation id). Delegates to the canonical
    :func:`qualify_conversation_ref`, so refs that already carry a scope
    segment keep it (idempotent) and non-conversation refs pass through
    unchanged.
    """
    out: List[str] = []
    for raw in refs or []:
        ref = str(raw or "").strip()
        if not ref:
            continue
        out.append(qualify_conversation_ref(ref, child_conversation_id))
    return out
