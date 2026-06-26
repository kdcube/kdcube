# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations


CONVERSATION_NAMED_SERVICE_NAMESPACE = "conv"

# Realm-trait intro, coherent with the mem (`MEMORY_NAMESPACE_INTRO`) and canvas
# (`CANVAS_NAMESPACE_INTRO`) intros: conversations are one of the user's memory
# realms — what was actually said. Positive framing: it says what the realm IS
# and what it searches (user text, assistant text, the user's uploaded
# attachment summaries).
CONVERSATION_NAMESPACE_INTRO = "Conversations — what was actually said in chat, this conversation or across earlier ones. Search them to recover what the user said (their prompts and follow-ups), what the assistant said (replies and working summaries), and the user's uploaded attachments (their indexed summaries); results come back as turn-level handles you can read or pull. Reach for it whenever a look back would help: an explicit recall request, or when the user refers to something from before, says it was clearer earlier, can't re-locate something, or resumes a dropped thread. Default scope is the current conversation; widen to the user's other conversations with you for cross-conversation recall. It's one of the user's memory realms — the said-aloud kind — alongside their durable memories (`mem`) and context boards (`cnv`)."


__all__ = [
    "CONVERSATION_NAMED_SERVICE_NAMESPACE",
    "CONVERSATION_NAMESPACE_INTRO",
]
