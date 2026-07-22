# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import get_workspace_implementation_guide
from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions_lite import default_lite_system_instruction
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.agents.decision import build_decision_system_text


def test_get_workspace_implementation_guide_custom_mentions_hosting_backed_mode():
    guide = get_workspace_implementation_guide("custom")
    assert "react.pull(paths=[...])" in guide
    assert 'react.checkout(mode="replace", paths=[...])' in guide or 'react.checkout(mode="replace", paths=["conv:fi:' in guide
    assert "mode=\"overlay\"" in guide
    assert "exact file refs" in guide
    assert "hosted binaries require exact file refs" in guide
    assert "HOSTED ARTIFACT-HISTORY MODE" in guide
    assert "hosting-backed artifact state" in guide


def test_lite_workspace_profile_teaches_generic_snapshot_paths_without_story_snapshot_block_by_default():
    text = default_lite_system_instruction("workspace")
    assert "[STORY SNAPSHOTS]" not in text
    assert "conv:fi:conv_<conversation_id>.turn_<id>.git/snapshots/<name>" in text


def test_lite_story_snapshots_block_is_explicit_opt_in():
    text = default_lite_system_instruction(
        "workspace",
        extra_blocks=["REACT_LITE_STORY_SNAPSHOTS"],
    )
    assert "[STORY SNAPSHOTS]" in text
    assert "conv:fi:conv_<conversation_id>.turn_<id>.git/snapshots/<name>" in text


def test_get_workspace_implementation_guide_git_mentions_git_backed_mode():
    guide = get_workspace_implementation_guide("git")
    assert "react.pull(paths=[...])" in guide
    assert "exact file refs" in guide
    assert "hosted binaries require exact file refs" in guide
    assert "GIT-BACKED ARTIFACT-HISTORY MODE" in guide
    assert "git-backed workspace lineage" in guide
    assert "local git repo" in guide
    assert "active lineage workspace" in guide
    assert "historical reference view" in guide
    assert 'react.checkout(mode="replace", paths=[...])' in guide or 'react.checkout(mode="replace", paths=["conv:fi:' in guide
    assert "editable project state" in guide
    assert "mode=\"overlay\"" in guide
    assert "turn_<current>/files/..." in guide
    assert "previous saved workspace paths" in guide
    assert "current editable workspace" in guide
    assert "existing top-level scope" in guide


def test_build_decision_system_text_uses_selected_workspace_implementation():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="git",
    )
    assert "react.pull(paths=[...])" in text
    assert "exact file refs" in text
    assert "hosted binaries require exact file refs" in text
    assert "local git repo" in text
    assert "Workspace activation is explicit" in text
    assert "EACH TURN STARTS BLANK" in text
    assert 'react.checkout(mode="replace", paths=[...])' in text or 'react.checkout(mode="replace", paths=["conv:fi:' in text
    assert "editable project state" in text
    assert "mode=\"overlay\"" in text
    assert "turn_<current>/files/..." in text
    assert "existing top-level scope" in text
    assert "previous saved workspace paths" in text
    assert "current editable workspace" in text


def test_default_decision_system_text_keeps_story_snapshot_block_opt_in():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
    )
    assert "[STORY SNAPSHOTS]" not in text
    assert "conv:fi:conv_<conversation_id>.turn_<id>.git/snapshots/<name>" in text


def test_build_decision_system_text_appends_agent_admin_customization():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        additional_instructions="Always prefer the product knowledge skill before web search.",
    )
    assert "[START AGENT ADMIN CUSTOMIZATION - HARD OVERRIDE]" in text
    assert "[END AGENT ADMIN CUSTOMIZATION]" in text
    assert "the entire START/END block as system-level customization for this agent" in text
    assert "Always prefer the product knowledge skill before web search." in text


def test_build_decision_system_text_single_action_mode_uses_action_channel_wording():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        multi_action_mode="off",
    )
    assert text.lstrip().startswith("CRITICAL: you are the agent which must")
    assert "Never emit legacy <thinking>...</thinking> tags." in text
    assert text.index("You have 4 channel types") < text.index("[CORE RESPONSIBILITIES]")
    assert "Output protocol (strict): one round = exactly one <channel:thinking>, exactly one <channel:action>" in text
    assert "<channel:action> carries one action" in text
    assert "The optional <channel:summary> may appear exactly once, and only when the action is complete or exit." in text
    assert "For call_tool rounds, omit <channel:summary> entirely" in text
    assert "For complete/exit rounds, include exactly one <channel:summary>" in text
    assert "CRITICAL: This protocol is SINGLE-ACTION" in text
    assert "Final answer shape (only when action is complete or exit)" in text
    assert "Goal, Outcome, Key facts, Refs" in text
    assert "Emit EXACTLY ONE <channel:action> per response" in text
    assert "[SINGLE-ACTION CAUSALITY — HARD]" in text
    assert "The runtime executes that action only after this response ends" in text
    assert "[STRATEGY TRAITS — WHAT MAY SHARE A ROUND]" not in text
    assert "exploration followed by exploitation" not in text
    protocol = text[: text.index("[ReAct Action Module v3]")]
    assert "multi-action" not in protocol.lower()
    assert "Use non-empty <channel:code> only immediately after an exec_tools.execute_code_python action" in text
    assert "After </channel:code>, STOP." not in text


def test_build_decision_system_text_safe_fanout_explains_no_intermediate_review_and_exec_completion_rule():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        multi_action_mode="safe_fanout",
    )
    assert "Output protocol (strict): one round = at least one <channel:thinking>, one or more <channel:action>" in text
    assert "The optional <channel:summary> may appear exactly once, and only when the response contains a single complete/exit action and no tool-call actions." in text
    assert "<channel:action> carries one action" in text
    assert "If one of the actions is exec_tools.execute_code_python, put its <channel:code> immediately after that exec action" in text
    assert "One <channel:action>...</channel:action> instance means exactly one action." in text
    assert "When multi-action is enabled, emit each action in its own separate <channel:action> instance." in text
    assert "<channel:thinking> ... </channel:thinking>" in text
    assert "<channel:code></channel:code>" in text
    assert "Never put > 1 JSON objects, > 1 fenced JSON blocks, or prose after the JSON inside one <channel:action> instance." in text
    assert "For call_tool-only rounds, omit <channel:summary> entirely" in text
    assert "For complete/exit rounds, include exactly one <channel:summary>" in text
    assert "Never put > 1 actions into one <channel:action> instance." in text
    assert "When multi-action is enabled, emit each action in its own separate <channel:action> instance." in text
    assert "\"Already visible\" means visible in the timeline BEFORE your current response begins." in text
    assert "Anything you produce, retrieve, load, validate, render, or change in this same response is NOT already visible" in text
    assert "if action B's success or content depends on action A's result, A and B cannot share a round" in text
    assert "RETRIEVE + CONSUME the retrieval" in text
    assert "Exec completeness: an exec_tools.execute_code_python action runs only when it has BOTH params.contract" in text
    assert "Exec follows the strategy matrix like any other tool" in text
    assert "complete/exit may share its round only with a NEUTRAL tool action" in text
    assert "After </channel:code>, STOP." not in text


def test_build_decision_system_text_on_enables_multi_action_protocol():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        multi_action_mode="on",
    )
    assert "one round = at least one <channel:thinking>, one or more <channel:action>" in text
    assert "put its <channel:code> immediately after that exec action" in text
    assert "Exec follows the strategy matrix like any other tool" in text
    assert "Render final PDF/PPTX/DOCX/PNG deliverables with `rendering_tools.write_*`." in text
    assert "ref:<visible_logical_path>" in text
    assert "ref:<bound artifact path>" not in text
    assert "Default write rule: reports, briefs, HTML, Markdown, slide source" in text
    assert "must be written with react.write channel=canvas" in text


def test_build_decision_system_text_prefers_direct_document_renderers():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        multi_action_mode="on",
    )
    assert "Prefer generating source content with `react.write`." in text
    assert "Render final PDF/PPTX/DOCX/PNG deliverables with `rendering_tools.write_*`." in text
    assert "Preferred: then call the renderer with `content=\"ref:<visible text source ref>\"`" in text
    assert "Inline renderer content is accepted when needed." in text
    assert "Do not bind physical paths,\n  external owner refs, or internal artifacts into rendering_tools.write_*" in text
    assert "exec output with" in text and "visibility=external" in text
    assert "If generated content is meant for the user to see, download, approve, or use" in text
    assert "Internal text artifacts are still valid for private notes" in text
    assert "shape is wrong" in text
    assert "Use the input type documented by the target rendering tool." in text
    assert "Use HTML source for PDF" not in text
    assert "Do not use exec to call `rendering_tools.write_pdf`, `write_pptx`, or `write_docx`" in text
    assert "Do not load a product/domain skill merely because the topic is adjacent" in text


def test_build_decision_system_text_accepts_custom_instruction_body_without_replacing_protocol():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        instruction_body="[CUSTOM DEMO BODY]\nAnswer from the visible KDCube docs only.",
        include_tool_catalog=False,
        include_skill_gallery=False,
    )
    assert text.lstrip().startswith("CRITICAL: you are the agent which must")
    assert "[CUSTOM DEMO BODY]" in text
    assert "Answer from the visible KDCube docs only." in text
    assert "[ReAct Decision Module v3]" not in text
    assert "Prefer generating source content with `react.write`." not in text
    assert "[AVAILABLE REACT TOOLS]" not in text
    assert "[SKILL CATALOG]" not in text


def test_build_decision_system_text_composes_lite_blocks_without_optional_exec_guidance():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        instruction_blocks=[
            "REACT_LITE_IDENTITY",
            "REACT_LITE_TOOL_USE_BASE",
            "REACT_LITE_FINALIZATION",
        ],
        include_tool_catalog=False,
        include_skill_gallery=False,
    )
    assert "[REACT IDENTITY]" in text
    assert "[TOOLS - BASE RULES]" in text
    assert "[FINALIZATION]" in text
    assert "[EXEC TOOL]" not in text
    assert "Write contracted files under `OUTPUT_DIR`" not in text
    assert "[RENDERING TOOLS]" not in text


def test_effective_tool_selection_prunes_capability_instruction_blocks():
    without_optional_tools = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        instruction_blocks=["xlite:all_capabilities"],
        include_tool_catalog=False,
        include_skill_gallery=False,
        multi_action_mode="off",
    )
    assert "[EXEC — exec_tools.execute_code_python]" not in without_optional_tools
    assert "[DOCUMENTS & RENDERING]" not in without_optional_tools
    assert "[WEB]" not in without_optional_tools

    with_optional_tools = build_decision_system_text(
        adapters=[
            {"id": "exec_tools.execute_code_python", "doc": {}},
            {"id": "rendering_tools.write_pdf", "doc": {}},
            {"id": "web_tools.web_search", "doc": {}},
        ],
        infra_adapters=[],
        instruction_blocks=["xlite:all_capabilities"],
        include_tool_catalog=False,
        include_skill_gallery=False,
        multi_action_mode="off",
    )
    assert "[EXEC — exec_tools.execute_code_python]" in with_optional_tools
    assert "[DOCUMENTS & RENDERING]" in with_optional_tools
    assert "[WEB]" in with_optional_tools


def test_extra_lite_single_action_prompt_has_no_fanout_strategy_teaching():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        instruction_blocks=["xlite:workspace"],
        include_tool_catalog=False,
        include_skill_gallery=False,
        multi_action_mode="off",
    )
    lowered = text.lower()
    assert "multi-action" not in lowered
    assert "exploration" not in lowered
    assert "exploitation" not in lowered
    assert "strategy matrix" not in lowered


def test_compact_tool_catalog_keeps_exact_ids_and_params_without_long_examples():
    text = build_decision_system_text(
        adapters=[
            {
                "id": "demo.search",
                "is_async": True,
                "doc": {
                    "purpose": "Search a demo corpus. " + ("Long explanation. " * 80),
                    "args": {
                        "query": "Annotated[str, 'Search phrase']",
                        "limit": "Annotated[int, 'Maximum rows'] (default=5)",
                        "cursor": "Annotated[Optional[str], 'Pagination cursor'] (default=)",
                    },
                    "examples": [{"description": "large example", "code": "x" * 1000}],
                },
                "tool_traits": {"strategy": ["exploration"]},
            }
        ],
        instruction_blocks=["REACT_XLITE_IDENTITY_AND_GUARDS"],
        include_skill_gallery=False,
        tool_catalog_detail="compact",
        multi_action_mode="off",
    )
    assert "[AVAILABLE COMMON TOOLS] [COMPACT]" in text
    assert "Parameter names, types, and descriptions below are exact." in text
    assert "- demo.search [async]" in text
    assert "query:str — Search phrase" in text
    assert "limit:int=5 — Maximum rows" in text
    assert "cursor:str? — Pagination cursor" in text
    assert "Annotated[" not in text
    assert "large example" not in text
    assert "exploration" not in text.lower()
    assert "exploitation" not in text.lower()
    assert len(text) < 25_000


def test_build_decision_system_text_includes_exec_guidance_only_when_lite_exec_block_selected():
    text = build_decision_system_text(
        adapters=[{"id": "exec_tools.execute_code_python", "doc": {}}],
        infra_adapters=[],
        workspace_implementation="custom",
        instruction_blocks=[
            "REACT_LITE_IDENTITY",
            "REACT_LITE_EXEC_TOOL",
        ],
        include_tool_catalog=False,
        include_skill_gallery=False,
    )
    assert "[EXEC TOOL]" in text
    assert "include this block only" not in text.lower()
    assert 'artifact_rel = "turn_<current>/files/<scope>/<name>"' in text
    assert "artifact_path = Path(OUTPUT_DIR) / artifact_rel" in text
    assert "artifact_path.parent.mkdir(parents=True, exist_ok=True)" in text
    assert "Never write `artifact_rel` directly" in text


def test_build_decision_system_text_can_hide_tool_catalog_but_keep_skill_gallery():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        instruction_blocks=["REACT_LITE_IDENTITY"],
        include_tool_catalog=False,
        include_skill_gallery=True,
    )
    assert "[REACT IDENTITY]" in text
    assert "[AVAILABLE REACT TOOLS]" not in text
    assert "[AVAILABLE COMMON TOOLS]" not in text
    assert "[SKILL CATALOG]" in text


def test_default_lite_system_instruction_workspace_profile_is_usable_as_body():
    body = default_lite_system_instruction("workspace")
    assert "[VISIBLE TIMELINE CONTEXT]" in body
    assert "[VIRTUAL WORKSPACE MODEL]" in body
    assert "[WORKSPACE MATERIALIZATION - PULL/CHECKOUT]" in body
    assert "[PATCHING]" in body
    assert "[EXEC TOOL]" not in body
    assert "include this block only" not in body.lower()

    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        instruction_body=body,
        include_tool_catalog=False,
        include_skill_gallery=False,
    )
    assert text.lstrip().startswith("CRITICAL: you are the agent which must")
    assert "[VISIBLE TIMELINE CONTEXT]" in text
    assert "[VIRTUAL WORKSPACE MODEL]" in text
    assert "[ReAct Decision Module v3]" not in text


def test_default_lite_system_instruction_workspace_exec_profile_adds_exec_guidance():
    body = default_lite_system_instruction("workspace_exec")
    assert "[EXEC TOOL]" in body
    assert "OUTPUT_DIR/" in body
    assert "Keep it relative in the action" in body
    assert "artifact_path = Path(OUTPUT_DIR) / artifact_rel" in body
    assert "artifact_path.parent.mkdir(parents=True, exist_ok=True)" in body
    assert "process working directory" in body


def test_multi_action_protocol_teaches_strategy_trait_contract():
    """The multi-action protocol must teach the trait harness contract:
    max two actions, strategy traits from the catalog, neutral tools may pair
    with a final close, namespace-specific neutral overrides are honored, and a separate complete action is distinct from an embedded
    final_answer. It must NOT carry the old "memory solo / state-change /
    complete-is-exploitation / tool+complete always forbidden" language.
    """
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        multi_action_mode="safe_fanout",
    )

    # Positive: the new contract is taught.
    assert "A round may hold AT MOST TWO actions" in text
    assert "shown in the tool catalog" in text
    assert "row is the action already accepted earlier" in text
    assert "column is the following candidate action" in text
    assert "explor               ok      no       ok       no" in text  # the strategy matrix
    assert "exploit              ok      ok       ok       no" in text
    assert "staged work" in text
    assert "effective namespace trait marks the concrete operation" in text
    assert "memory.record_memory" not in text
    assert "share its round only with a NEUTRAL tool action" in text
    # Embedded final_answer in a tool JSON is distinct from a separate complete.
    assert "emitted as a SEPARATE second <channel:action>" in text
    assert "embedding final_answer inside a tool" in text

    # Negative: none of the stale concepts survive.
    assert "memory writes must run alone" not in text
    assert "Memory writes are state changes" not in text
    assert "neutral state-change solo" not in text
    assert "complete/exit is exploitation" not in text
    assert "NO FIXED KIND" not in text
    # "complete must be the only action" is correct ONLY in single-action mode;
    # it must not appear in the multi-action (safe_fanout) protocol.
    assert "must be the ONLY action" not in text


def test_single_action_mode_keeps_complete_alone_rule():
    """Single-action mode runs one action per round, so a final close is
    necessarily alone there — that rule is correct and must stay in off mode."""
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        multi_action_mode="off",
    )
    assert "must be the ONLY action in its round" in text
