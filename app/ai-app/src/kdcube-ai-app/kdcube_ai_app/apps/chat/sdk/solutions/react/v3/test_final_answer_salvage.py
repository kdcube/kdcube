# SPDX-License-Identifier: MIT

"""Untagged final-answer salvage: what is recovered and what is refused."""

from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.final_answer_salvage import (
    MAX_SALVAGEABLE_ANSWER_CHARS,
    salvage_untagged_final_answer,
)

ANSWER = (
    "**Release 1.1.0** adds web login and single-session admin access, "
    "expanding access options beyond the prior release."
)


def test_prose_after_a_closed_thinking_channel_is_salvaged():
    raw = f"<channel:thinking>reasoning</channel:thinking>\n\n{ANSWER}\n"
    assert salvage_untagged_final_answer(raw) == ANSWER


def test_prose_after_an_orphan_closing_tag_is_salvaged():
    # The thinking opener can be consumed as a stream prefix, leaving only the closer.
    raw = f"reasoning text</channel:thinking>\n\n{ANSWER}"
    assert ANSWER in salvage_untagged_final_answer(raw)


def test_text_inside_an_unclosed_channel_is_not_salvaged():
    raw = f"<channel:thinking>reasoning</channel:thinking><channel:action>{ANSWER}"
    assert salvage_untagged_final_answer(raw) == ""


def test_protocol_shaped_text_is_refused():
    assert salvage_untagged_final_answer('{"action": "complete", "final_answer": "x"}') == ""
    assert salvage_untagged_final_answer('[{"action": "call_tool"}]') == ""
    assert salvage_untagged_final_answer("```json\n{\"action\": \"exit\"}\n```") == ""


def test_short_or_letterless_text_is_refused():
    assert salvage_untagged_final_answer("Done.") == ""
    assert salvage_untagged_final_answer("1234567890 1234567890 1234567890") == ""
    assert salvage_untagged_final_answer("") == ""
    assert salvage_untagged_final_answer(None) == ""


def test_salvage_is_bounded():
    raw = "A" * (MAX_SALVAGEABLE_ANSWER_CHARS + 500)
    assert len(salvage_untagged_final_answer(raw)) == MAX_SALVAGEABLE_ANSWER_CHARS
