# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.skills.instructions.instructions_extra_lite import (
    REACT_XLITE_PROFILE_BLOCKS,
    compose_extra_lite_instruction_blocks,
    default_extra_lite_system_instruction,
    get_extra_lite_instruction_block,
    resolve_extra_lite_item,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.agents.decision import build_decision_system_text


def test_profiles_mirror_lite_profile_names():
    assert set(REACT_XLITE_PROFILE_BLOCKS) == {
        "core", "workspace", "workspace_exec", "document", "web", "all_capabilities",
    }


def test_hard_signals_survive_distillation():
    text = default_extra_lite_system_instruction("all_capabilities")
    # grammar-critical signals that must never be lost in compression
    signals = [
        # path grammar + conversion
        "conv:ar:conv_<conversation_id>.turn_<id>.react.turn.index",
        "conv:ar:conv_<conversation_id>.plan.latest:<plan_id>",
        "conv:so:conv_<conversation_id>.sources_pool[1,3]",
        "turn_<id>/external/<event_kind>/attachments/<event_id>/<rel>",
        # strict param orders
        "path, channel, content, kind, then optional scratchpad",
        "path, channel, patch, kind",
        # citation forms
        "[[S:1,3]]",
        '<sup class="cite" data-sids="1,3">',
        '"citations": [{"path": "<json pointer>", "sids": [1,3]}]',
        # plan ack markers
        "✓ [1]", "✗ [1]", "… [2]",
        # exec contract semantics
        "params.contract",
        "BYTE-IDENTICAL",
        "agent_io_tools.tool_call",
        "FLIP YOUR DEFAULT",
        # workspace hard rule
        "EACH TURN STARTS BLANK",
        # fetch_ctx namespace limits
        "`conv:ar:`/`conv:tc:`/`conv:so:`",
        # canvas extensions
        ".mermaid/.mmd",
        # announce budget form
        "reactive bonus",
        # hide window
        "last 4 rounds",
        # press skills
        "sk:public.pdf-press",
    ]
    for signal in signals:
        assert signal in text, f"distillation lost signal: {signal!r}"


def test_git_mode_appends_addendum_after_workspace():
    custom = default_extra_lite_system_instruction("workspace")
    git = default_extra_lite_system_instruction("workspace", workspace_implementation="git")
    assert "[GIT WORKSPACE MODE]" not in custom
    assert "[GIT WORKSPACE MODE]" in git
    assert git.index("[VIRTUAL WORKSPACE]") < git.index("[GIT WORKSPACE MODE]")


def test_unknown_profile_and_block_raise_with_known_lists():
    with pytest.raises(KeyError):
        default_extra_lite_system_instruction("nope")
    with pytest.raises(KeyError):
        get_extra_lite_instruction_block("REACT_XLITE_NOPE")


def test_resolve_item_handles_blocks_profiles_and_literals():
    assert resolve_extra_lite_item("REACT_XLITE_SKILLS").startswith("[SKILLS]")
    assert resolve_extra_lite_item("xlite:core") == default_extra_lite_system_instruction("core")
    assert resolve_extra_lite_item("just literal text") is None
    composed = compose_extra_lite_instruction_blocks(["REACT_XLITE_ATTACHMENTS", "literal rule"])
    assert "[ATTACHMENTS]" in composed and "literal rule" in composed


def test_profile_expansion_can_drop_unavailable_capability_blocks():
    text = default_extra_lite_system_instruction(
        "all_capabilities",
        exclude_blocks={
            "REACT_XLITE_EXEC",
            "REACT_XLITE_DOCUMENTS_RENDERING",
            "REACT_XLITE_WEB",
        },
    )
    assert "[EXEC — exec_tools.execute_code_python]" not in text
    assert "[DOCUMENTS & RENDERING]" not in text
    assert "[WEB]" not in text
    assert "[IDENTITY & TRUST]" in text


def test_decision_build_resolves_xlite_names_and_profiles():
    exec_adapter = {"id": "exec_tools.execute_code_python", "doc": {}}
    via_body = build_decision_system_text(
        adapters=[exec_adapter], multi_action_mode="on",
        instruction_body=default_extra_lite_system_instruction("workspace_exec"),
    )
    via_blocks = build_decision_system_text(
        adapters=[exec_adapter], multi_action_mode="on",
        instruction_blocks=["xlite:workspace_exec"],
    )
    assert via_blocks == via_body
    mixed = build_decision_system_text(
        adapters=[], multi_action_mode="on",
        instruction_blocks=["REACT_XLITE_IDENTITY_AND_GUARDS", "REACT_LITE_SKILLS", "Custom literal."],
    )
    assert "[IDENTITY & TRUST]" in mixed
    assert "Custom literal." in mixed


def test_extra_lite_body_is_dramatically_smaller_than_default_body():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.decision_prompt import (
        build_default_decision_instruction_body,
    )
    default_body = build_default_decision_instruction_body(
        module_label="ReAct Action Module v3",
        workspace_implementation="custom",
    )
    xlite_body = default_extra_lite_system_instruction("all_capabilities")
    assert len(xlite_body) < len(default_body) / 3
