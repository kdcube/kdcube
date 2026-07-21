from __future__ import annotations

"""Regression tests for _answer_texts_from_timeline.

A turn may legitimately deliver MULTIPLE distinct final answers — the v3 ReAct
runtime emits one `react.final_answer.{iteration}.{safe_idx}` block per parallel
action, and each must be preserved. The duplication we guard against is the
COMPLETION HISTORY: `finish_turn` re-emits one `assistant.completion` block per
close-gate retry (via react/layout.py:build_assistant_completion_blocks) — the
settled answer at the UNSUFFIXED path `conv:ar:{tid}.assistant.completion` and
every earlier retry at `conv:ar:{tid}.assistant.completion.{idx}` — plus
provisional `conv:ar:{tid}.assistant.completion.attempt.{idx}` drafts. Callers
that render straight from the timeline (without `prefer_react_turn_answer`) must
collapse that history to its single final entry while keeping the distinct
`react.final_answer.*` deliverables, otherwise every draft is resent as its own
Telegram message ("same reply 3-7x").
"""

TID = "turn_1"


def _completion_block(
    text: str,
    *,
    path: str,
    block_type: str = "assistant.completion",
    meta: dict | None = None,
) -> dict:
    block: dict = {"type": block_type, "path": path, "text": text}
    if meta is not None:
        block["meta"] = meta
    return block


def test_completion_history_collapses_to_final_entry():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    # build_assistant_completion_blocks re-emits one block per close-gate retry:
    # the earlier entries at `.assistant.completion.{idx}`, the final settled
    # answer at the UNSUFFIXED `.assistant.completion` path (emitted last).
    timeline = {
        "blocks": [
            _completion_block(
                "first draft",
                path=f"conv:ar:{TID}.assistant.completion.1",
                meta={"completion_index": 1, "completion_count": 3},
            ),
            _completion_block(
                "second draft",
                path=f"conv:ar:{TID}.assistant.completion.2",
                meta={"completion_index": 2, "completion_count": 3},
            ),
            _completion_block(
                "FINAL ANSWER",
                path=f"conv:ar:{TID}.assistant.completion",
                meta={"completion_index": 3, "completion_count": 3},
            ),
        ]
    }

    # Exactly one text: the settled final completion (unsuffixed path).
    assert _answer_texts_from_timeline(timeline) == ["FINAL ANSWER"]


def test_completion_history_final_selected_by_index_when_out_of_order():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    # The final entry is identified by the unsuffixed path OR by
    # completion_index == completion_count, not merely by timeline position.
    timeline = {
        "blocks": [
            _completion_block(
                "FINAL ANSWER",
                path=f"conv:ar:{TID}.assistant.completion",
                meta={"completion_index": 2, "completion_count": 2},
            ),
            _completion_block(
                "stale draft",
                path=f"conv:ar:{TID}.assistant.completion.1",
                meta={"completion_index": 1, "completion_count": 2},
            ),
        ]
    }

    assert _answer_texts_from_timeline(timeline) == ["FINAL ANSWER"]


def test_distinct_react_final_answers_are_all_preserved():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    # One `react.final_answer.{iteration}.{safe_idx}` block per parallel action:
    # these are DISTINCT deliverables and must NOT be collapsed.
    timeline = {
        "blocks": [
            _completion_block(
                "First response.",
                path=f"tc:{TID}.react.final_answer.0.0",
                block_type="react.final_answer",
            ),
            {"type": "note", "path": f"tc:{TID}.notes", "text": "internal note"},
            _completion_block(
                "Second response.",
                path=f"tc:{TID}.react.final_answer.0.1",
                block_type="react.final_answer",
            ),
        ]
    }

    assert _answer_texts_from_timeline(timeline) == [
        "First response.",
        "Second response.",
    ]


def test_attempt_blocks_are_never_delivered():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    timeline = {
        "blocks": [
            _completion_block(
                "settled draft",
                path=f"conv:ar:{TID}.assistant.completion.1",
                meta={"completion_index": 1, "completion_count": 2},
            ),
            # Provisional / in-flight draft recorded per ReAct iteration.
            _completion_block(
                "IN-FLIGHT DRAFT",
                path=f"conv:ar:{TID}.assistant.completion.attempt.1",
                block_type="assistant.completion.attempt",
                meta={"provisional": True, "completion_attempt_index": 1},
            ),
            _completion_block(
                "FINAL ANSWER",
                path=f"conv:ar:{TID}.assistant.completion",
                meta={"completion_index": 2, "completion_count": 2},
            ),
        ]
    }

    result = _answer_texts_from_timeline(timeline)
    assert result == ["FINAL ANSWER"]
    # The .attempt block must not leak into the deliverable set.
    assert "IN-FLIGHT DRAFT" not in result


def test_attempt_detected_by_path_segment():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    # Some producers emit a plain "assistant.completion" type but flag the
    # attempt only in the path (".attempt." segment). It must still be excluded.
    timeline = {
        "blocks": [
            _completion_block(
                "FINAL ANSWER",
                path=f"conv:ar:{TID}.assistant.completion",
            ),
            _completion_block(
                "PATH-FLAGGED ATTEMPT",
                path=f"conv:ar:{TID}.assistant.completion.attempt.2",
            ),
        ]
    }

    result = _answer_texts_from_timeline(timeline)
    assert result == ["FINAL ANSWER"]
    assert "PATH-FLAGGED ATTEMPT" not in result


def test_aborted_turn_falls_back_to_last_attempt():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    # A turn that aborted before settling has only provisional attempt blocks.
    # Silence is the worst failure (stuck-turn history), so deliver the last
    # attempt text rather than nothing.
    timeline = {
        "blocks": [
            _completion_block(
                "early attempt",
                path=f"conv:ar:{TID}.assistant.completion.attempt.1",
                block_type="assistant.completion.attempt",
            ),
            _completion_block(
                "LAST ATTEMPT",
                path=f"conv:ar:{TID}.assistant.completion.attempt.2",
                block_type="assistant.completion.attempt",
            ),
        ]
    }

    assert _answer_texts_from_timeline(timeline) == ["LAST ATTEMPT"]


def test_single_completion_turn_is_unchanged():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    # Behavior-preserving: a turn that settled on its answer in one shot is
    # byte-identical to the pre-fix output.
    timeline = {
        "blocks": [
            _completion_block(
                "the only answer",
                path=f"conv:ar:{TID}.assistant.completion",
            ),
        ]
    }

    assert _answer_texts_from_timeline(timeline) == ["the only answer"]


def test_empty_timeline_yields_nothing():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    assert _answer_texts_from_timeline({}) == []
    assert _answer_texts_from_timeline({"blocks": []}) == []
    assert _answer_texts_from_timeline({"blocks": "not-a-list"}) == []
