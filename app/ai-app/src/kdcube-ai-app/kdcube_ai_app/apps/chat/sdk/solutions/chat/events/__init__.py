# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from .resolver import (
    CONVERSATION_OBJECT_NAMESPACE,
    CONVERSATION_RESOLVER_NAME,
    conversation_id_from_ref,
    conversation_ref_capabilities,
    parse_conversation_ref,
    resolve_conversation_ref_action,
)

__all__ = [
    "CONVERSATION_OBJECT_NAMESPACE",
    "CONVERSATION_RESOLVER_NAME",
    "conversation_id_from_ref",
    "conversation_ref_capabilities",
    "parse_conversation_ref",
    "resolve_conversation_ref_action",
]
