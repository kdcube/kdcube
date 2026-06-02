# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.events import event_source_declaration

REACT_FOLLOWUP_EVENT_SOURCE_ID = "react.followup"
REACT_STEER_EVENT_SOURCE_ID = "react.steer"
REACT_WRITE_EVENT_SOURCE_ID = "react.write"
REACT_MEMSEARCH_EVENT_SOURCE_ID = "react.memsearch"


def native_react_tool_policies() -> list[dict[str, str]]:
    """Return projection-phase policies for native ReAct tool blocks.

    Native ReAct tools already produce their timeline blocks directly in their
    handlers; they do not use the external-tool block-production path. These
    declarations make those blocks policy-addressable for timeline projection
    and compaction.
    """
    return [
        {
            "react_phase": "timeline_projection",
            "event_policy_id": "react.timeline_projection.identity",
        },
        {
            "react_phase": "compaction_projection",
            "event_policy_id": "react.compaction_projection.identity",
        },
    ]


def event_source_id_for_external_kind(kind: str) -> str:
    """Map built-in external-event kinds to ReAct event-source ids."""
    value = str(kind or "").strip().lower()
    if value == "followup":
        return REACT_FOLLOWUP_EVENT_SOURCE_ID
    if value == "steer":
        return REACT_STEER_EVENT_SOURCE_ID
    return f"react.external.{value}" if value else "react.external"


def list_event_sources():
    """Declare built-in ReAct external events for discovery.

    The declarations are intentionally conservative. They make followup/steer
    policy-addressable without changing the existing `user.followup` and
    `user.steer` block shapes.
    """
    return [
        event_source_declaration(
            event_source_id=REACT_FOLLOWUP_EVENT_SOURCE_ID,
            policies=[],
            description="User followup events appended to the active ReAct timeline while a turn is running.",
            kind="react.external",
        ),
        event_source_declaration(
            event_source_id=REACT_STEER_EVENT_SOURCE_ID,
            policies=[],
            description="User steer events appended to the active ReAct timeline while a turn is running.",
            kind="react.external",
        ),
        event_source_declaration(
            event_source_id=REACT_WRITE_EVENT_SOURCE_ID,
            policies=native_react_tool_policies(),
            description="Native ReAct write tool; produces artifact metadata and visible write result blocks.",
            kind="react.native_tool.write",
        ),
        event_source_declaration(
            event_source_id=REACT_MEMSEARCH_EVENT_SOURCE_ID,
            policies=native_react_tool_policies(),
            description="Native ReAct memory search tool; produces source/recovery result blocks.",
            kind="react.native_tool.source",
        ),
    ]
