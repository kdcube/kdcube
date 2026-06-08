# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""First-party ReAct event-source declarations and block helpers."""

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.common import (
    block_event_id,
    block_event_source_id,
    block_matches_event_source,
    event_source_pipeline_enabled,
    event_identity_fields,
    stamp_event_identity,
    stamp_event_identity_many,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.artifact_production import (
    emit_policy_artifact_blocks,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.core import (
    REACT_FOLLOWUP_EVENT_SOURCE_ID,
    REACT_MEMSEARCH_EVENT_SOURCE_ID,
    REACT_MESSAGE_EVENT_SOURCE_ID,
    REACT_STEER_EVENT_SOURCE_ID,
    REACT_USER_ATTACHMENT_EVENT_SOURCE_ID,
    REACT_WRITE_EVENT_SOURCE_ID,
    event_source_id_for_external_kind,
    list_event_sources,
    native_react_tool_policies,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.exploration import (
    composite_artifact_source_policies,
    default_tool_event_policies,
    exploration_source_policies,
    structured_result_source_policies,
    write_tool_source_policies,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.listener import (
    LiveExternalEventOwnerLease,
    acquire_live_external_event_owner,
    release_live_external_event_owner,
    run_live_external_event_listener_loop,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.projection import (
    TIMELINE_SEGMENT_META_KEY,
    apply_event_source_transformers,
    clear_timeline_segment_marks,
    event_source_ids_from_timeline,
    patch_timeline_segment_marks,
    produce_event_source_announce_blocks,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
    DEFAULT_REACT_EVENT_POLICIES,
    REACT_POLICY_PHASES,
    ReactEventPolicies,
    ReactEventPolicy,
    ReactEventPolicyBinding,
    announce_event_policy,
    block_production_policy,
    compaction_event_policy,
    discover_react_event_policies,
    get_react_event_policies,
    react_event_policy,
    react_event_policy_definition,
    timeline_projection_policy,
    tool_call_validation_policy,
    unknown_policy_paths,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.resolver import (
    canonicalize_event_ref_for_context,
    read_event_ref_bytes,
    resolve_event_ref_action,
)


def __getattr__(name: str):
    if name == "render_external_events_dry_run":
        from kdcube_ai_app.apps.chat.sdk.solutions.react.events.simulator import (
            render_external_events_dry_run,
        )

        return render_external_events_dry_run
    raise AttributeError(name)


__all__ = [
    "DEFAULT_REACT_EVENT_POLICIES",
    "REACT_FOLLOWUP_EVENT_SOURCE_ID",
    "REACT_MEMSEARCH_EVENT_SOURCE_ID",
    "REACT_MESSAGE_EVENT_SOURCE_ID",
    "REACT_POLICY_PHASES",
    "REACT_STEER_EVENT_SOURCE_ID",
    "REACT_USER_ATTACHMENT_EVENT_SOURCE_ID",
    "REACT_WRITE_EVENT_SOURCE_ID",
    "ReactEventPolicy",
    "ReactEventPolicies",
    "ReactEventPolicyBinding",
    "LiveExternalEventOwnerLease",
    "TIMELINE_SEGMENT_META_KEY",
    "acquire_live_external_event_owner",
    "announce_event_policy",
    "apply_event_source_transformers",
    "block_event_id",
    "block_event_source_id",
    "block_matches_event_source",
    "canonicalize_event_ref_for_context",
    "block_production_policy",
    "clear_timeline_segment_marks",
    "compaction_event_policy",
    "composite_artifact_source_policies",
    "default_tool_event_policies",
    "discover_react_event_policies",
    "event_identity_fields",
    "event_source_id_for_external_kind",
    "event_source_ids_from_timeline",
    "event_source_pipeline_enabled",
    "emit_policy_artifact_blocks",
    "exploration_source_policies",
    "get_react_event_policies",
    "list_event_sources",
    "native_react_tool_policies",
    "patch_timeline_segment_marks",
    "produce_event_source_announce_blocks",
    "react_event_policy",
    "react_event_policy_definition",
    "release_live_external_event_owner",
    "render_external_events_dry_run",
    "read_event_ref_bytes",
    "resolve_event_ref_action",
    "run_live_external_event_listener_loop",
    "stamp_event_identity",
    "stamp_event_identity_many",
    "structured_result_source_policies",
    "timeline_projection_policy",
    "tool_call_validation_policy",
    "unknown_policy_paths",
    "write_tool_source_policies",
]
