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

from .instructions import (
    CONVERSATION_NAMED_SERVICE_NAMESPACE,
    CONVERSATION_NAMESPACE_INTRO,
)

__all__ = [
    "CONVERSATION_NAMED_SERVICE_NAMESPACE",
    "CONVERSATION_NAMESPACE_INTRO",
]
