# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
    assistant_completion_texts,
    build_assistant_completion_blocks,
    build_assistant_completion_attempt_blocks,
    build_working_summary_attempt_blocks,
    build_working_summary_blocks,
    latest_assistant_completion_text,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import (
    extract_assistant_completion_texts_from_blocks,
    extract_sources_used_from_blocks,
)


def _block_factory(**kwargs):
    return dict(kwargs)


class _Scratchpad:
    pass


def test_latest_assistant_completion_text_returns_latest_non_empty_entry():
    scratchpad = _Scratchpad()
    scratchpad.assistant_completion_attempts = [
        {"text": "First answer"},
        {"text": "   "},
        {"text": "Latest answer"},
    ]

    assert latest_assistant_completion_text(scratchpad) == "Latest answer"
    assert assistant_completion_texts(scratchpad) == ["First answer", "Latest answer"]


def test_build_assistant_completion_blocks_numbers_earlier_entries_and_keeps_latest_alias():
    runtime = RuntimeCtx(turn_id="turn_1", started_at="2026-04-26T10:00:00Z")

    blocks = build_assistant_completion_blocks(
        runtime=runtime,
        completion_entries=[
            {"text": "First answer", "ts": "2026-04-26T10:01:00Z"},
            {"text": "Second answer", "ts": "2026-04-26T10:02:00Z"},
            {"text": "Final answer", "ts": "2026-04-26T10:03:00Z"},
        ],
        final_answer_text="Final answer",
        ended_at="2026-04-26T10:03:00Z",
        block_factory=_block_factory,
    )

    assert [b["path"] for b in blocks] == [
        "ar:turn_1.assistant.completion.1",
        "ar:turn_1.assistant.completion.2",
        "ar:turn_1.assistant.completion",
    ]
    assert blocks[-1]["text"] == "Final answer"
    assert blocks[-1]["meta"]["completion_index"] == 3
    assert blocks[-1]["meta"]["completion_count"] == 3


def test_build_assistant_completion_blocks_appends_settled_answer_when_latest_attempt_differs():
    runtime = RuntimeCtx(turn_id="turn_2", started_at="2026-04-26T10:00:00Z")

    blocks = build_assistant_completion_blocks(
        runtime=runtime,
        completion_entries=[
            {"text": "Visible draft", "ts": "2026-04-26T10:01:00Z", "iteration": 4},
        ],
        final_answer_text="Settled answer",
        ended_at="2026-04-26T10:02:00Z",
        block_factory=_block_factory,
    )

    assert [b["text"] for b in blocks] == ["Visible draft", "Settled answer"]
    assert [b["path"] for b in blocks] == [
        "ar:turn_2.assistant.completion.1",
        "ar:turn_2.assistant.completion",
    ]


def test_build_assistant_completion_blocks_keeps_latest_alias_at_first_visible_timestamp_when_text_matches_last_attempt():
    runtime = RuntimeCtx(turn_id="turn_3", started_at="2026-04-26T10:00:00Z")

    blocks = build_assistant_completion_blocks(
        runtime=runtime,
        completion_entries=[
            {"text": "Same final answer", "ts": "2026-04-26T10:01:00Z"},
        ],
        final_answer_text="Same final answer",
        ended_at="2026-04-26T10:05:00Z",
        block_factory=_block_factory,
    )

    assert len(blocks) == 1
    assert blocks[0]["path"] == "ar:turn_3.assistant.completion"
    assert blocks[0]["ts"] == "2026-04-26T10:01:00Z"


def test_build_working_summary_blocks_does_not_persist_canonical_alias():
    runtime = RuntimeCtx(turn_id="turn_4", started_at="2026-04-26T10:00:00Z")

    blocks = build_working_summary_blocks(
        runtime=runtime,
        summary_text="Goal: test\nOutcome: done",
        ended_at="2026-04-26T10:04:00Z",
        block_factory=_block_factory,
    )

    assert blocks == []


def test_build_assistant_completion_attempt_blocks_marks_attempt_provisional():
    runtime = RuntimeCtx(turn_id="turn_attempt", started_at="2026-04-26T10:00:00Z")

    blocks = build_assistant_completion_attempt_blocks(
        runtime=runtime,
        entry={
            "text": "Provisional answer",
            "ts": "2026-04-26T10:01:00Z",
            "iteration": 4,
            "sources_used": [1, 2],
        },
        attempt_index=2,
        block_factory=_block_factory,
    )

    assert len(blocks) == 1
    assert blocks[0]["type"] == "assistant.completion.attempt"
    assert blocks[0]["path"] == "ar:turn_attempt.assistant.completion.attempt.2"
    assert blocks[0]["text"] == "Provisional answer"
    assert blocks[0]["meta"]["completion_attempt_index"] == 2
    assert blocks[0]["meta"]["provisional"] is True
    assert blocks[0]["meta"]["iteration"] == 4
    assert blocks[0]["meta"]["sources_used"] == [1, 2]


def test_extract_assistant_completion_texts_from_blocks_prefers_canonical_blocks():
    blocks = [
        {
            "type": "assistant.completion.attempt",
            "text": "Visible attempt",
        },
        {
            "type": "assistant.completion",
            "text": "Canonical answer",
        },
    ]

    assert extract_assistant_completion_texts_from_blocks(blocks) == ["Canonical answer"]


def test_extract_sources_used_from_blocks_scans_attempt_answer_text():
    blocks = [
        {
            "type": "assistant.completion.attempt",
            "text": "Provisional answer cites a source [[S:2]].",
            "meta": {"sources_used": [3]},
        },
        {
            "type": "assistant.completion",
            "text": "Final answer cites another source [[S:4]].",
        },
    ]

    assert extract_sources_used_from_blocks(blocks) == [2, 3, 4]


def test_build_working_summary_attempt_blocks_uses_stable_attempt_paths():
    runtime = RuntimeCtx(turn_id="turn_5", started_at="2026-04-26T10:00:00Z")

    blocks = build_working_summary_attempt_blocks(
        runtime=runtime,
        summary_text="Goal: second\nOutcome: done",
        attempt_index=2,
        attempt_count=3,
        iteration=6,
        ts="2026-04-26T10:02:00Z",
        block_factory=_block_factory,
    )

    assert len(blocks) == 1
    assert blocks[0]["path"] == "ws:turn_5.conv.working.summary.attempt.2"
    assert blocks[0]["text"] == "Goal: second\nOutcome: done"
    assert blocks[0]["meta"]["summary_scope"] == "completion_attempt"
    assert blocks[0]["meta"]["assistant_completion_attempt_index"] == 2
    assert blocks[0]["meta"]["assistant_completion_attempt_count"] == 3
    assert blocks[0]["meta"]["iteration"] == 6
