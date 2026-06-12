# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Event-source declarations and discovery for chat SDK runtimes."""

from kdcube_ai_app.apps.chat.sdk.events.decorator import (
    ARTIFACT_NAMESPACE_REHOSTER_ATTR,
    EVENT_SOURCE_ATTR,
    EVENT_SOURCE_READER_ATTR,
    EVENT_SOURCE_RESOLVER_ATTR,
    ArtifactNamespaceRehosterDeclaration,
    EventSourceDeclaration,
    EventSourceReaderDeclaration,
    EventSourceResolverDeclaration,
    artifact_namespace_rehoster,
    artifact_namespace_rehoster_declaration,
    event_source,
    event_source_declaration,
    event_source_reader,
    event_source_reader_declaration,
    event_source_resolver,
    event_source_resolver_declaration,
    get_artifact_namespace_rehoster_declaration,
    get_event_source_declaration,
    get_event_source_reader_declaration,
    get_event_source_resolver_declaration,
)
from kdcube_ai_app.apps.chat.sdk.events.constants import (
    DEFAULT_REACT_AGENT_ID,
    normalize_agent_id,
    safe_event_lane_part,
)
from kdcube_ai_app.apps.chat.sdk.events.subsystem import (
    EventSourceSubsystem,
    ResolvedArtifactNamespaceRehoster,
    ResolvedEventSource,
    ResolvedEventSourceReader,
    ResolvedEventSourceResolver,
    resolve_event_source_specs,
)
from kdcube_ai_app.apps.chat.sdk.events.external import (
    EventTimelineIdentityCard,
    ExternalEventMaterializationCtx,
    ExternalEventMaterializationResult,
)

__all__ = [
    "EVENT_SOURCE_ATTR",
    "ARTIFACT_NAMESPACE_REHOSTER_ATTR",
    "EVENT_SOURCE_READER_ATTR",
    "EVENT_SOURCE_RESOLVER_ATTR",
    "ArtifactNamespaceRehosterDeclaration",
    "DEFAULT_REACT_AGENT_ID",
    "EventSourceDeclaration",
    "EventSourceReaderDeclaration",
    "EventSourceResolverDeclaration",
    "EventSourceSubsystem",
    "EventTimelineIdentityCard",
    "ExternalEventMaterializationCtx",
    "ExternalEventMaterializationResult",
    "ResolvedArtifactNamespaceRehoster",
    "ResolvedEventSource",
    "ResolvedEventSourceReader",
    "ResolvedEventSourceResolver",
    "artifact_namespace_rehoster",
    "artifact_namespace_rehoster_declaration",
    "event_source",
    "event_source_declaration",
    "event_source_reader",
    "event_source_reader_declaration",
    "event_source_resolver",
    "event_source_resolver_declaration",
    "get_artifact_namespace_rehoster_declaration",
    "get_event_source_declaration",
    "get_event_source_reader_declaration",
    "get_event_source_resolver_declaration",
    "normalize_agent_id",
    "resolve_event_source_specs",
    "safe_event_lane_part",
]
