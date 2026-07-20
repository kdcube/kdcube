from __future__ import annotations

"""Regression tests for _answer_texts_from_timeline.

A turn has exactly ONE final answer to deliver, but `finish_turn` persists the
full completion history (one `assistant.completion` block per ReAct iteration)
plus provisional `assistant.completion.attempt` blocks. Callers that render
straight from the timeline (without `prefer_react_turn_answer`) must therefore
see only the single, final answer — otherwise every draft is resent as its own
Telegram message ("same reply 3-7x").
"""


def _completion_block(text: str, *, path: str, block_type: str = "assistant.completion") -> dict:
    return {"type": block_type, "path": path, "text": text}


def test_multi_draft_timeline_yields_only_last_final_answer():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    timeline = {
        "blocks": [
            _completion_block("first draft", path="scratchpad.assistant_completion_attempts.0"),
            _completion_block("second draft", path="scratchpad.assistant_completion_attempts.1"),
            _completion_block("FINAL ANSWER", path="scratchpad.answer"),
        ]
    }

    # Exactly one text, and it is the last completion block in timeline order.
    assert _answer_texts_from_timeline(timeline) == ["FINAL ANSWER"]


def test_attempt_blocks_are_never_delivered():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    timeline = {
        "blocks": [
            _completion_block("settled draft", path="scratchpad.assistant_completion_attempts.0"),
            # Provisional / in-flight draft recorded per ReAct iteration.
            _completion_block(
                "IN-FLIGHT DRAFT",
                path="scratchpad.assistant_completion.attempt.1",
                block_type="assistant.completion.attempt",
            ),
            _completion_block("FINAL ANSWER", path="scratchpad.answer"),
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
            _completion_block("FINAL ANSWER", path="scratchpad.answer"),
            _completion_block("PATH-FLAGGED ATTEMPT", path="scratchpad.assistant.completion.attempt.2"),
        ]
    }

    result = _answer_texts_from_timeline(timeline)
    assert result == ["FINAL ANSWER"]
    assert "PATH-FLAGGED ATTEMPT" not in result


def test_single_completion_turn_is_unchanged():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _answer_texts_from_timeline,
    )

    # Behavior-preserving: a turn that settled on its answer in one shot is
    # byte-identical to the pre-fix output.
    timeline = {
        "blocks": [
            _completion_block("the only answer", path="scratchpad.answer"),
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
