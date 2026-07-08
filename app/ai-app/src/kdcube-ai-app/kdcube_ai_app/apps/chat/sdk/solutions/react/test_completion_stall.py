# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Completion stall regression suite.

Covers the runtime-layer contracts introduced after the provisional-completion
livelock (turn_2026-07-08-11-07-27-129): equivalent re-attempts of the same
completion are detected cheaply, collapsed to one answer at assembly, and a
deferred attempt always gets a rendered verdict block.
"""

from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
    build_assistant_completion_deferred_blocks,
    collapse_equivalent_completion_texts,
    completion_attempt_texts_equivalent,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx


def _block_factory(**kwargs):
    return dict(kwargs)


# Trimmed from the real looping turn: consecutive attempts differed only in
# trivial wording ("I still need additional permissions" vs "I need additional
# permissions") while re-stating the same two-section answer.
_ATTEMPT_A = (
    "## About the Slack Post Images\n\n"
    "I still need additional permissions to read the full message details and attachments "
    "from that Slack post. Your Slack account is connected, but it needs to approve the "
    "**slack:history** permission.\n\n"
    "## Latest 2 Science News\n\n"
    "### 1. Creatine May Help Fight Cancer [[S:1]]\n"
    "**July 8, 2026** — Scientists have discovered that creatine may strengthen one of the "
    "immune system's most important cancer-fighting pathways.\n\n"
    "### 2. Intermittent Fasting as a Weight Loss Alternative [[S:1]]\n"
    "**July 8, 2026** — Intermittent fasting helped people lose as much weight as calorie restriction."
)
_ATTEMPT_B = _ATTEMPT_A.replace(
    "I still need additional permissions to read the full message details and attachments",
    "I need additional permissions to read the full message details and check for images",
)
_DIFFERENT_ANSWER = (
    "Here are the images from the Slack post: two screenshots of the deployment dashboard, "
    "attached below with their captions."
)


def test_equivalence_detects_identical_and_trivially_reworded_attempts():
    assert completion_attempt_texts_equivalent(_ATTEMPT_A, _ATTEMPT_A) is True
    # Whitespace/casing variations of the same text are the same attempt.
    assert completion_attempt_texts_equivalent(_ATTEMPT_A, "  " + _ATTEMPT_A.upper() + "\n") is True
    # Trivial wording variation between re-emits (the observed loop shape).
    assert completion_attempt_texts_equivalent(_ATTEMPT_A, _ATTEMPT_B) is True


def test_equivalence_keeps_distinct_answers_distinct():
    assert completion_attempt_texts_equivalent(_ATTEMPT_A, _DIFFERENT_ANSWER) is False
    assert completion_attempt_texts_equivalent("", _ATTEMPT_A) is False
    assert completion_attempt_texts_equivalent(_ATTEMPT_A, "") is False


def test_collapse_reduces_the_observed_thirteen_attempt_run_to_one_answer():
    attempts = [_ATTEMPT_A if i % 2 == 0 else _ATTEMPT_B for i in range(13)]
    collapsed = collapse_equivalent_completion_texts(attempts)
    assert len(collapsed) == 1
    # The LAST text of the run is the one delivered.
    assert collapsed[0] == attempts[-1]


def test_collapse_keeps_one_answer_per_accepted_completion():
    # A turn with a mid-turn followup legitimately produces two answers; the
    # trivially reworded re-attempt of the second collapses into it.
    second_retry = _DIFFERENT_ANSWER.replace("attached below", "shown below")
    texts = [_ATTEMPT_A, _DIFFERENT_ANSWER, second_retry]
    collapsed = collapse_equivalent_completion_texts(texts)
    assert len(collapsed) == 2
    assert collapsed[0] == _ATTEMPT_A
    assert collapsed[1] == second_retry


def test_collapse_drops_blank_entries_and_preserves_order():
    assert collapse_equivalent_completion_texts(["", "  ", _ATTEMPT_A]) == [_ATTEMPT_A]
    assert collapse_equivalent_completion_texts([]) == []


def test_deferred_attempt_always_gets_a_rendered_verdict_block():
    """No attempt marker without an outcome: a deferred completion contributes
    a verdict block telling the model why it re-loops and what to do."""
    runtime = RuntimeCtx(turn_id="turn_stall", started_at="2026-07-08T11:07:27Z")
    blocks = build_assistant_completion_deferred_blocks(
        runtime=runtime,
        attempt_index=3,
        reason="external_events_arrived",
        ts="2026-07-08T11:08:23Z",
        block_factory=_block_factory,
    )
    assert len(blocks) == 1
    block = blocks[0]
    assert block["type"] == "assistant.completion.attempt.outcome"
    assert block["path"] == "conv:ar:turn_stall.assistant.completion.attempt.3.outcome"
    assert block["meta"]["completion_attempt_index"] == 3
    assert block["meta"]["completion_attempt_outcome"] == "deferred"
    assert block["meta"]["completion_attempt_defer_reason"] == "external_events_arrived"
    assert "attempt 3" in block["text"]
    assert "complete" in block["text"]


def test_deferred_verdict_block_covers_the_close_gate_reason_too():
    runtime = RuntimeCtx(turn_id="turn_stall", started_at="2026-07-08T11:07:27Z")
    blocks = build_assistant_completion_deferred_blocks(
        runtime=runtime,
        attempt_index=1,
        reason="event_lane_close_deferred",
        ts="2026-07-08T11:08:23Z",
        block_factory=_block_factory,
    )
    assert len(blocks) == 1
    assert blocks[0]["meta"]["completion_attempt_defer_reason"] == "event_lane_close_deferred"
    assert "valid final answer" in blocks[0]["text"]


def test_deferred_verdict_block_needs_a_turn():
    runtime = RuntimeCtx(turn_id="", started_at="2026-07-08T11:07:27Z")
    assert build_assistant_completion_deferred_blocks(
        runtime=runtime,
        attempt_index=1,
        reason="external_events_arrived",
        ts=None,
        block_factory=_block_factory,
    ) == []
