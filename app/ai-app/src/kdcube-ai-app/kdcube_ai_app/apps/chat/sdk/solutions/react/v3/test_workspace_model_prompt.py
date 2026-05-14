# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import get_workspace_implementation_guide
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.agents.decision import build_decision_system_text


def test_get_workspace_implementation_guide_custom_mentions_hosting_backed_mode():
    guide = get_workspace_implementation_guide("custom")
    assert "react.pull(paths=[...])" in guide
    assert 'react.checkout(mode="replace", paths=[...])' in guide or 'react.checkout(mode="replace", paths=["fi:' in guide
    assert "mode=\"overlay\"" in guide
    assert "EXACT file ref" in guide
    assert "binary descendants" in guide
    assert "CUSTOM mode" in guide
    assert "not from git" in guide


def test_get_workspace_implementation_guide_git_mentions_git_backed_mode():
    guide = get_workspace_implementation_guide("git")
    assert "react.pull(paths=[...])" in guide
    assert "EXACT file ref" in guide
    assert "binary descendants" in guide
    assert "GIT mode" in guide
    assert "git-backed workspace lineage snapshot" in guide
    assert "local git repo" in guide
    assert "git pull" in guide
    assert "active lineage workspace" in guide
    assert "historical snapshot view" in guide
    assert 'react.checkout(mode="replace", paths=[...])' in guide or 'react.checkout(mode="replace", paths=["fi:' in guide
    assert "runnable/searchable/testable project snapshot" in guide
    assert "mode=\"overlay\"" in guide
    assert "<current_turn_id>/files/..." in guide
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
    assert "EXACT file ref" in text
    assert "binary descendants" in text
    assert "local git repo" in text
    assert "Workspace activation is explicit" in text
    assert "do NOT auto-materialize old files" in text
    assert 'react.checkout(mode="replace", paths=[fi:...])' in text or 'react.checkout(mode="replace", paths=["fi:' in text
    assert "runnable/searchable/testable project snapshot" in text
    assert "mode=\"overlay\"" in text
    assert "<current_turn_id>/files/..." in text
    assert "existing top-level scope" in text
    assert "previous saved workspace paths" in text
    assert "current editable workspace" in text


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
    assert text.index("CRITICAL: you have 4 channel types") < text.index("[CORE RESPONSIBILITIES]")
    assert "CRITICAL: you are the agent which must for in custom protocol which you must obey." in text
    assert "Output protocol (strict): you must produce content which represents one round" in text
    assert "In a single round, exactly one occurrence of <channel:thinking>, <channel:ReactDecisionOutV2>, and <channel:code> can be included in your response." in text
    assert "<channel:ReactDecisionOutV2> is the action channel" in text
    assert "The optional <channel:summary> may appear exactly once, and only when the ReactDecisionOutV2 action is complete or exit." in text
    assert "Do NOT emit <channel:summary> in code execution rounds." in text
    assert "For call_tool actions, omit <channel:summary> entirely" in text
    assert "For complete/exit actions, include exactly one <channel:summary>" in text
    assert "DO NOT DO THIS: Your typical error is that you make sequence of channel groups" in text
    assert "Final answer shape only when action is complete or exit" in text
    assert "Goal, Outcome, Key facts, Refs" in text
    assert "Generating the second instance of any channel in the same response means you do not understand the contract and violate it." in text
    assert "Use <channel:code> only when this round contains exactly one exec_tools.execute_code_python action" in text
    assert "After </channel:code>, STOP." not in text


def test_build_decision_system_text_safe_fanout_explains_no_intermediate_review_and_exec_completion_rule():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        multi_action_mode="safe_fanout",
    )
    assert "Output protocol (strict): you must produce content which represents one round" in text
    assert "In a single round, include exactly one <channel:thinking>, one <channel:code>, and one or more <channel:ReactDecisionOutV2> channel instances." in text
    assert "The optional <channel:summary> may appear exactly once, and only when the response contains a single complete/exit action and no tool-call actions." in text
    assert "<channel:ReactDecisionOutV2> is the action channel" in text
    assert "If you need multiple actions in one round, repeat only <channel:ReactDecisionOutV2>." in text
    assert "One <channel:ReactDecisionOutV2> ... </channel:ReactDecisionOutV2> channel instance means exactly one action." in text
    assert "If you need multiple actions in one round, use this shape:" in text
    assert "<channel:thinking>...short status for the whole round...</channel:thinking>" in text
    assert "<channel:code></channel:code>" in text
    assert "Never put > 1 JSON objects, > 1 fenced JSON blocks, or prose after the JSON inside one <channel:ReactDecisionOutV2> instance." in text
    assert "For call_tool-only rounds, omit <channel:summary> entirely" in text
    assert "For complete/exit rounds, include exactly one <channel:summary>" in text
    assert "Never put > 1 actions into one ReactDecisionOutV2 channel instance." in text
    assert "The runtime executes the actions sequentially and you do NOT review intermediate results in the middle" in text
    assert "action B must not depend on action A's result." in text
    assert "If you need to inspect or assess the first result before deciding the next action, split the work into separate rounds." in text
    assert "If action B needs an artifact, source path, search result, or output created by action A, split them into separate rounds." in text
    assert "Do NOT schedule search/fetch first and then a later action in the same round that depends on what that retrieval will return." in text
    assert "Exec in multi-action: you may include exactly one exec_tools.execute_code_python action together with other actions" in text
    assert "complete params.contract and complete Python in <channel:code>" in text
    assert "Otherwise exec must be the only action in the round." in text
    assert "Do NOT mix complete/exit with tool calls in the same multi-action response." in text
    assert "After </channel:code>, STOP." not in text


def test_build_decision_system_text_on_enables_multi_action_protocol():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
        multi_action_mode="on",
    )
    assert "In a single round, include exactly one <channel:thinking>, one <channel:code>, and one or more <channel:ReactDecisionOutV2> channel instances." in text
    assert "If you need multiple actions in one round, repeat only <channel:ReactDecisionOutV2>." in text
    assert "Exec in multi-action: you may include exactly one exec_tools.execute_code_python action together with other actions" in text
    assert "render PDF, PPTX, and DOCX from already visible source artifacts" in text
    assert "ref:<artifact_path_or_visible_file_path>" in text
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
    assert "Preferred: then call the renderer with `content=\"ref:<external artifact path>\"`." in text
    assert "Inline renderer content is still valid when needed." in text
    assert "internal\n  artifacts into rendering_tools.write_*" in text
    assert "exec output with" in text and "visibility=external" in text
    assert "if generated content is meant for the user to see, download, approve, or use as a renderer source, make it external" in text
    assert "Do not use `channel=\"internal\"` refs as rendering_tools.write_* source." in text
    assert "draft shape is wrong" in text
    assert "Use the input type documented by the target rendering tool." in text
    assert "Use HTML source for PDF" not in text
    assert "Do not use exec to call `rendering_tools.write_pdf`, `write_pptx`, or `write_docx`" in text
    assert "Do not load a product/domain skill merely because the topic is adjacent" in text
