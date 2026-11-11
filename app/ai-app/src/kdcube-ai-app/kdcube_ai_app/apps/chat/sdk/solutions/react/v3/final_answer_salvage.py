# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Recover a final answer the decision model wrote outside every channel.

The decision agent answers in tagged channels (``<channel:thinking>`` and the
structured action channel). On the last ReAct iteration a model sometimes
closes its thinking channel and then writes the finished, user-ready answer as
bare prose with no channel wrapper::

    <channel:thinking>...</channel:thinking>

    **Release 1.1.0** adds web login and single-session admin access.

The channel parser harvests only text inside a declared channel, so the
decision parses to ``{}`` and validation fails. With no retry left on the last
iteration the runtime used to exit with its internal fallback string, while
the answer the model actually wrote was discarded.

``salvage_untagged_final_answer`` returns that untagged prose, or ``""`` when
nothing safe can be salvaged. It is pure: the runtime decides what to do with
the result and records that it happened.
"""

from __future__ import annotations

import re

_CHANNEL_BLOCK_RE = re.compile(
    r"<\s*channel:[^<>]*>.*?<\s*/\s*channel:[^<>]*>", re.DOTALL | re.IGNORECASE
)
_CHANNEL_OPEN_RE = re.compile(r"<\s*channel:[^<>]*>", re.IGNORECASE)
_CHANNEL_CLOSE_RE = re.compile(r"<\s*/\s*channel:[^<>]*>", re.IGNORECASE)
_FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)

# Below this, untagged text is a stray token (a lone "Done.", a tool id), not an answer.
MIN_SALVAGEABLE_ANSWER_CHARS = 24
MAX_SALVAGEABLE_ANSWER_CHARS = 8000


def salvage_untagged_final_answer(raw: str) -> str:
    """Return the user-facing prose the model wrote outside every channel.

    Rules, in order:

    1. complete channel blocks are dropped (their content is internal
       reasoning or the structured decision that already failed);
    2. text after an unclosed channel opener belongs to that channel and is
       dropped;
    3. a closing tag without an opener is dropped and the prose after it kept
       (the observed shape: the thinking opener was emitted as a prefix the
       parser consumed);
    4. fenced code blocks are dropped (a decision dumped as markdown);
    5. the remainder is rejected when it starts like JSON, is shorter than
       ``MIN_SALVAGEABLE_ANSWER_CHARS``, or contains no letters.
    """
    text = str(raw or "")
    if not text.strip():
        return ""
    text = _CHANNEL_BLOCK_RE.sub("\n", text)
    open_match = _CHANNEL_OPEN_RE.search(text)
    if open_match:
        text = text[: open_match.start()]
    text = _CHANNEL_CLOSE_RE.sub("\n", text)
    text = _FENCED_BLOCK_RE.sub("\n", text)
    candidate = text.strip()
    if not candidate:
        return ""
    if candidate[0] in "{[":
        return ""
    if len(candidate) < MIN_SALVAGEABLE_ANSWER_CHARS:
        return ""
    if not any(ch.isalpha() for ch in candidate):
        return ""
    return candidate[:MAX_SALVAGEABLE_ANSWER_CHARS].strip()
