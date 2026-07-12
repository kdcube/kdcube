# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""ReAct subagents: charter-scoped child conversations.

A subagent is a full ReAct agent running its own conversation, opened from a
parent turn with a fork of the parent's visible context and a written charter.
The child reports back through the conversation event lane (the same authored
external-event primitive user followups and consent grants ride on).

Modules:
- ``charter``     — the charter contract (the assignment prompt, the configured
                    budget, the helper alias).
- ``events``      — authoring ``subagent.*`` events into a conversation lane.
- ``fork``        — the fork projection (current-turn blocks + working summaries)
                    and the fork marker block on the parent timeline.
- ``launch``      — the launch request/handle contract between the react tool
                    and the host workflow's spawner.
- ``schedule``    — the child as a fair-scheduled turn: seed persist, the
                    promotable charter/completion task payloads, lane wakeups.
- ``child_turn``  — the child side: assignment detection on a promoted turn,
                    runtime/accounting overrides, the terminal report.
- ``comm_policy`` — the child communicator policy (chosen in one place).
"""

from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.charter import (
    SubagentCharter,
    parse_charter,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.events import (
    SUBAGENT_CONTRIBUTION_EVENT_KIND,
    SUBAGENT_CONVERGED_EVENT_KIND,
    SUBAGENT_EVENT_SOURCE_ID,
    SUBAGENT_EVENT_TRANSPORT_KIND,
    SUBAGENT_FAILED_EVENT_KIND,
    ParentLaneAddress,
    publish_subagent_event,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.launch import (
    SubagentLaunchRequest,
    SubagentLaunchTicket,
)

__all__ = [
    "SubagentCharter",
    "parse_charter",
    "SUBAGENT_CONTRIBUTION_EVENT_KIND",
    "SUBAGENT_CONVERGED_EVENT_KIND",
    "SUBAGENT_FAILED_EVENT_KIND",
    "SUBAGENT_EVENT_SOURCE_ID",
    "SUBAGENT_EVENT_TRANSPORT_KIND",
    "ParentLaneAddress",
    "publish_subagent_event",
    "SubagentLaunchRequest",
    "SubagentLaunchTicket",
]
