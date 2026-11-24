"""W40: a turn without its turn token tells the model why the connected-service tools are missing."""

from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_announce_turn_tools_lines
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx


def test_no_line_when_the_turn_has_its_token():
    assert build_announce_turn_tools_lines(runtime_ctx=RuntimeCtx()) == []


def test_the_update_window_is_explained_plainly():
    lines = build_announce_turn_tools_lines(runtime_ctx=RuntimeCtx(resident_turn_token_refusal="agent_authority_updating"))
    assert lines[0] == "[TOOLS]" and "being updated" in lines[1] and "try again shortly" in lines[1]


def test_another_refusal_names_its_code():
    lines = build_announce_turn_tools_lines(runtime_ctx=RuntimeCtx(resident_turn_token_refusal="conversation_not_owned"))
    assert "conversation_not_owned" in lines[1]
