# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Compatibility shim — the conversation-record write side moved to its home,
``kdcube_ai_app.apps.chat.sdk.solutions.conversation.record``.

The conversation record (the "conv timeline") had its write machinery scattered
across runtime and solution files; it now lives in the conversation solution
beside its read side (``view.py``, ``api.py``, ``export.py``). Import from
``solutions.conversation.record``; this module re-exports for existing callers
and adds nothing."""

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.record import (  # noqa: F401
    ASSISTANT_COMPLETION_BLOCK_TYPE,
    STREAM_ARTIFACT_MARKERS,
    build_error_turn_log_payload,
    build_minimal_turn_log_payload,
    build_stream_artifact_payload,
    mark_turn_error_surfaced,
    persist_stream_artifacts,
    mark_turn_log_recorded,
    record_conversation_timeline,
    record_error_turn_log_if_absent,
    record_minimal_turn_log_if_absent,
    reset_turn_error_surfaced,
    reset_turn_log_recorded,
    turn_error_was_surfaced,
    turn_log_was_recorded,
)
