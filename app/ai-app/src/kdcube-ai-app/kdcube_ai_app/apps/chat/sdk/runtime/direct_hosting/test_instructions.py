from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.instructions import (
    DirectInstructionSelection,
    PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE,
    compose_provider_native_instructions,
    configured_instruction_selection,
    native_react_instruction_blocks,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.decision_prompt import (
    normalize_instruction_blocks,
)


def test_configured_selection_keeps_profile_and_customization_separate() -> None:
    selection = configured_instruction_selection(
        {
            "agent": {
                "instructions": {"profile": "lite:core"},
                "additional_instructions": "Preserve public source URLs.",
            }
        },
        default_profile="full",
    )

    assert selection == DirectInstructionSelection(
        profile="lite:core",
        additional_instructions="Preserve public source URLs.",
    )


def test_legacy_scalar_becomes_additive_instead_of_replacing_profile() -> None:
    selection = configured_instruction_selection(
        {"agent": {"instructions": "Legacy domain guidance."}},
        default_profile="lite:core",
    )

    assert selection.profile == "lite:core"
    assert selection.additional_instructions == "Legacy domain guidance."


def test_legacy_scalar_cannot_conflict_with_current_customization_field() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        configured_instruction_selection(
            {
                "agent": {
                    "instructions": "Legacy domain guidance.",
                    "additional_instructions": "Current domain guidance.",
                }
            },
            default_profile="lite:core",
        )


def test_native_react_adds_only_enabled_standard_capability_blocks() -> None:
    blocks = native_react_instruction_blocks(
        DirectInstructionSelection(profile="lite:core"),
        enabled_tool_ids=(
            "web_tools.web_search",
            "exec_tools.execute_code_python",
            "rendering_tools.write_pdf",
        ),
    )

    assert blocks == (
        "lite:core",
        "REACT_LITE_EXEC_TOOL",
        "REACT_LITE_RENDERING_TOOLS",
        "REACT_LITE_WEB_TOOLS",
    )
    body = normalize_instruction_blocks(blocks)
    assert "[VIRTUAL WORKSPACE MODEL]" in body
    assert "[EXEC TOOL]" in body
    assert "[RENDERING TOOLS]" in body
    assert "[WEB TOOLS]" in body


def test_native_react_does_not_duplicate_capabilities_in_broad_profile() -> None:
    blocks = native_react_instruction_blocks(
        DirectInstructionSelection(profile="instr:profile:lite"),
        enabled_tool_ids=(
            "web_tools.web_search",
            "exec_tools.execute_code_python",
            "rendering_tools.write_pdf",
        ),
    )

    assert blocks == ("instr:profile:lite",)


def test_native_react_rejects_literal_text_as_a_profile() -> None:
    with pytest.raises(ValueError, match="unknown ReAct instruction profile"):
        native_react_instruction_blocks(
            DirectInstructionSelection(profile="You are an agent."),
            enabled_tool_ids=(),
        )


def test_provider_native_profile_teaches_direct_workspace_and_capabilities() -> None:
    text = compose_provider_native_instructions(
        DirectInstructionSelection(
            profile=PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE,
            additional_instructions="Preserve public source URLs.",
        ),
        exec_tool="execute_python",
        rendering_tools=("write_pdf", "write_docx"),
        web_search_tool="web_search",
        web_fetch_tool="web_fetch",
        skill_instructions="[ACTIVE SKILLS]\nFollow the research brief skill.",
    )

    assert "[KDCUBE DIRECT AGENT HARNESS]" in text
    assert "[CURRENT-TURN ARTIFACT WORKSPACE]" in text
    assert "[CODE IS YOUR HANDS — execute_python]" in text
    assert "[ISOLATED PYTHON EXECUTION]" in text
    assert "never call `asyncio.run()`" in text
    assert "await agent_io_tools.tool_call" in text
    assert "`OUTPUT_DIR` is already injected as the artifact root" in text
    assert "Path(OUTPUT_DIR)" in text
    assert 'Path(OUTPUT_DIR) / "files/research/report.xlsx"' in text
    assert "keep the leading `files/` or `git/projects/` namespace" in text
    assert "already materialized in the current-turn workspace" in text
    assert "pull_files" not in text
    assert "[DOCUMENT RENDERING - `write_pdf`, `write_docx`]" in text
    assert "[WEB RESEARCH - web_search + web_fetch]" in text
    assert "use `web_fetch` to inspect at least one selected source page" in text
    assert "consume the result rows from `ret`" in text
    assert "[ACTIVE SKILLS]" in text
    assert "[START AGENT ADMIN CUSTOMIZATION - HARD OVERRIDE]" in text
    assert text.endswith("[END AGENT ADMIN CUSTOMIZATION]")
    assert text.index("[ACTIVE SKILLS]") < text.index("Preserve public source URLs.")


def test_provider_native_profile_can_point_to_native_skills() -> None:
    text = compose_provider_native_instructions(
        DirectInstructionSelection(profile=PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE),
        native_skill_ids=("demo.research-brief",),
    )

    assert "[SELECTED KDCUBE SKILLS]" in text
    assert "`demo.research-brief`" in text
    assert "native skill surface" in text


def test_provider_native_profile_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError, match="unknown provider-native instruction profile"):
        compose_provider_native_instructions(
            DirectInstructionSelection(profile="full"),
        )
