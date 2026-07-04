# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation memory-realm: searching what was actually said.

This package owns conversation search as a first-class SDK capability,
independent of the ReAct tool that first hosted it. Conversations are one of
the user's memory realms — what the user said, what the assistant said, and the
user's uploaded attachments, this conversation or across earlier ones —
alongside durable memories (`mem`) and context boards (`cnv`).

`api.py` is the orchestration entry point. It takes an EXPLICIT calling context
(`ConversationSearchContext`) rather than reading ambient contextvars, so a
future public/site API can search a user's conversations by setting the context
explicitly. `named_service.py` exposes the same capability as a search
named-service provider; `instructions.py` carries the realm-trait intro.
"""

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.export_records import (
    collapse_turn,
    export_conversations,
    normalize_conversation,
    normalize_turn,
    source_for_user,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.export import (
    ConversationExportRequest,
    ConversationExportService,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.mcp_export import (
    ConversationReadServiceFactory,
    CurrentUserIdFactory,
    export_current_user_conversations,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.instructions import (
    CONVERSATION_NAMED_SERVICE_NAMESPACE,
    CONVERSATION_NAMESPACE_INTRO,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import (
    DEFAULT_EXPORT_LIMIT,
    MAX_EXPORT_LIMIT,
    ConversationExportScope,
    ConversationGetRequest,
    ConversationListRequest,
    ConversationReadScope,
    ConversationReadService,
    ConversationScopeError,
    build_conversation_ctx_client,
    make_conversation_read_service,
)

__all__ = [
    "CONVERSATION_NAMED_SERVICE_NAMESPACE",
    "CONVERSATION_NAMESPACE_INTRO",
    "DEFAULT_EXPORT_LIMIT",
    "MAX_EXPORT_LIMIT",
    "ConversationExportRequest",
    "ConversationExportScope",
    "ConversationExportService",
    "ConversationGetRequest",
    "ConversationListRequest",
    "ConversationReadServiceFactory",
    "ConversationReadScope",
    "ConversationReadService",
    "ConversationScopeError",
    "CurrentUserIdFactory",
    "build_conversation_ctx_client",
    "collapse_turn",
    "export_current_user_conversations",
    "export_conversations",
    "make_conversation_read_service",
    "normalize_conversation",
    "normalize_turn",
    "source_for_user",
]
