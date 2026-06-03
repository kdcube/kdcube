# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.events import event_source_declaration

REACT_FOLLOWUP_EVENT_SOURCE_ID = "react.followup"
REACT_STEER_EVENT_SOURCE_ID = "react.steer"
REACT_MESSAGE_EVENT_SOURCE_ID = "react.message"
REACT_USER_ATTACHMENT_EVENT_SOURCE_ID = "react.user_attachment"
REACT_EXTERNAL_EVENT_SOURCE_ID = "react.external_event"
REACT_WRITE_EVENT_SOURCE_ID = "react.write"
REACT_MEMSEARCH_EVENT_SOURCE_ID = "react.memsearch"


def _block_production_policy(event_policy_id: str) -> dict[str, str]:
    return {
        "react_phase": "block_production",
        "event_policy_id": event_policy_id,
    }


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
    if value in {"message", "regular"}:
        return REACT_MESSAGE_EVENT_SOURCE_ID
    if value == "followup":
        return REACT_FOLLOWUP_EVENT_SOURCE_ID
    if value == "steer":
        return REACT_STEER_EVENT_SOURCE_ID
    if value == "external_event":
        return REACT_EXTERNAL_EVENT_SOURCE_ID
    return f"react.external.{value}" if value else "react.external"


def list_event_sources():
    """Declare built-in ReAct external events for discovery.

    The declarations are intentionally conservative. They make followup/steer
    policy-addressable without changing the existing `user.followup` and
    `user.steer` block shapes.
    """
    return [
        event_source_declaration(
            event_source_id=REACT_MESSAGE_EVENT_SOURCE_ID,
            policies=[_block_production_policy("react.block_production.user_prompt_default")],
            description="User message events that start or contribute prompt blocks to a ReAct turn timeline.",
            kind="react.external",
            reactive=True,
        ),
        event_source_declaration(
            event_source_id=REACT_USER_ATTACHMENT_EVENT_SOURCE_ID,
            policies=[_block_production_policy("react.block_production.user_attachment_default")],
            description="User attachment events materialized as ReAct attachment blocks.",
            kind="react.external",
            reactive=False,
        ),
        event_source_declaration(
            event_source_id=REACT_FOLLOWUP_EVENT_SOURCE_ID,
            policies=[_block_production_policy("react.block_production.user_followup_default")],
            description="User followup events appended to the active ReAct timeline while a turn is running.",
            kind="react.external",
            reactive=True,
        ),
        event_source_declaration(
            event_source_id=REACT_STEER_EVENT_SOURCE_ID,
            policies=[_block_production_policy("react.block_production.user_steer_default")],
            description="User steer events appended to the active ReAct timeline while a turn is running.",
            kind="react.external",
            reactive=False,
            iteration_credit=0,
        ),
        event_source_declaration(
            event_source_id=REACT_EXTERNAL_EVENT_SOURCE_ID,
            policies=[_block_production_policy("react.block_production.event_default")],
            description="Generic bundle-authored external events transported over chat ingress.",
            kind="react.external",
            reactive=False,
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
