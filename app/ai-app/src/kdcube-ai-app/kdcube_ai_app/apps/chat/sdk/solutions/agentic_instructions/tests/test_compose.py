# SPDX-License-Identifier: MIT

"""The unified instruction-composition vocabulary.

`compose_instruction_body` resolves one token list into a body, order-preserving,
with `full` | `lite:<profile>` | `xlite:<profile>` | single `REACT_LITE_*` /
`REACT_XLITE_*` blocks | literal text.
"""

from kdcube_ai_app.apps.chat.sdk.solutions.agentic_instructions import (
    compose_instruction_body,
)
from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions_lite import (
    default_lite_system_instruction,
    REACT_LITE_PROFILE_BLOCKS,
)
from kdcube_ai_app.apps.chat.sdk.skills.instructions.instructions_extra_lite import (
    default_extra_lite_system_instruction,
)


def test_lite_profile_token_equals_the_explicit_block_list():
    # the descriptor's whole moderate profile collapses to one token
    explicit = list(REACT_LITE_PROFILE_BLOCKS["all_capabilities"])
    assert compose_instruction_body(["lite:all_capabilities"]) == compose_instruction_body(explicit)
    assert compose_instruction_body(["lite:workspace_exec"]) == default_lite_system_instruction("workspace_exec")


def test_xlite_profile_token_and_git_mode():
    assert compose_instruction_body(["xlite:workspace_exec"]) == default_extra_lite_system_instruction("workspace_exec")
    git = compose_instruction_body(["xlite:workspace_exec"], workspace_implementation="git")
    assert "[GIT WORKSPACE MODE]" in git  # profile expansion honors workspace mode


def test_full_token_uses_the_injected_provider_and_ignores_suffix():
    provided = compose_instruction_body(["full"], full_body_provider=lambda: "FULL-BODY")
    assert provided == "FULL-BODY"
    # monolithic: any suffix is ignored
    assert compose_instruction_body(["full:workspace_exec"], full_body_provider=lambda: "FULL-BODY") == "FULL-BODY"
    # no provider -> the full token contributes nothing
    assert compose_instruction_body(["full"]) == ""
    assert compose_instruction_body(["full", "xlite:core"]) == compose_instruction_body(["xlite:core"])


def test_single_blocks_literal_and_order_are_preserved():
    body = compose_instruction_body(
        ["REACT_LITE_IDENTITY", "xlite:core", "[CUSTOM] answer only from visible docs."]
    )
    assert "[REACT IDENTITY]" in body
    assert "[IDENTITY & TRUST]" in body  # xlite:core expands
    assert "[CUSTOM] answer only from visible docs." in body
    assert body.index("[REACT IDENTITY]") < body.index("[IDENTITY & TRUST]") < body.index("[CUSTOM]")


def test_unknown_token_is_literal_and_empty_items_drop():
    assert compose_instruction_body(["just literal text"]) == "just literal text"
    assert compose_instruction_body([]) == ""
    assert compose_instruction_body(["", "  ", "REACT_LITE_SKILLS"]).startswith("[SKILLS]")
    # a bare string is accepted as a one-item list
    assert compose_instruction_body("REACT_LITE_SKILLS").startswith("[SKILLS]")
