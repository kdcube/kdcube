# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad


def _make_ctx() -> ReactContext:
    scratchpad = TurnScratchpad(user="u1", conversation_id="c1", turn_id="t1", text="hi")
    return ReactContext(history_turns=[], scratchpad=scratchpad)


def test_materialize_show_artifacts_handles_list_value():
    ctx = _make_ctx()
    ctx.artifacts["medical_math_advances_search_1"] = {
        "artifact_id": "medical_math_advances_search_1",
        "artifact_kind": "inline",
        "artifact_type": None,
        "tool_id": "generic_tools.web_search",
        "timestamp": 1234567890.0,
        "summary": "summary",
        "value": [
            {"sid": 1, "title": "Title 1", "url": "https://example.com/1"},
            {"sid": 2, "title": "Title 2", "url": "https://example.com/2"},
        ],
    }

    items = ctx.materialize_show_artifacts(
        ["current_turn.artifacts.medical_math_advances_search_1"]
    )

    assert len(items) == 1
    artifact = items[0]["artifact"]
    assert artifact["kind"] == "search"
    assert artifact["format"] == "json"
    assert "Title 1" in artifact["text"]
