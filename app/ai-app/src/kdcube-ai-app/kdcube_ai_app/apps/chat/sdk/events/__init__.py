# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Event-source declarations and discovery for chat SDK runtimes."""

from kdcube_ai_app.apps.chat.sdk.events.decorator import (
    EVENT_SOURCE_ATTR,
    EventSourceDeclaration,
    event_source,
    event_source_declaration,
    get_event_source_declaration,
)
from kdcube_ai_app.apps.chat.sdk.events.subsystem import (
    EventSourceSubsystem,
    ResolvedEventSource,
    resolve_event_source_specs,
)

__all__ = [
    "EVENT_SOURCE_ATTR",
    "EventSourceDeclaration",
    "EventSourceSubsystem",
    "ResolvedEventSource",
    "event_source",
    "event_source_declaration",
    "get_event_source_declaration",
    "resolve_event_source_specs",
]
