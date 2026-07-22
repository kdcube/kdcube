# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import get_workspace_implementation_guide
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.agents.decision import build_decision_system_text


def test_get_workspace_implementation_guide_custom_mentions_hosting_backed_mode():
    guide = get_workspace_implementation_guide("custom")
    assert "react.pull(paths=[...])" in guide
    assert 'react.checkout(mode="replace", paths=[...])' in guide or 'react.checkout(mode="replace", paths=["conv:fi:' in guide
    assert "mode=\"overlay\"" in guide
    assert "exact file refs" in guide
    assert "hosted binaries require exact file refs" in guide
    assert "HOSTED ARTIFACT-HISTORY MODE" in guide
    assert "hosting-backed artifact state" in guide


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
    assert "git/projects" in guide
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
    assert "git/projects" in text
    assert "mode=\"overlay\"" in text
    assert "turn_<current>/files/..." in text
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


def test_build_decision_system_text_explains_one_response_is_one_round():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
    )
    assert text.lstrip().startswith("CRITICAL: you are the agent which must")
    assert "Never emit legacy <thinking>...</thinking> tags." in text
    assert "Output protocol (strict): one round = exactly one `channel:thinking`, exactly one `channel:action`" in text
    assert "The optional `channel:summary` may appear exactly once, and only when the action is complete or exit." in text
    assert "`channel:action` carries one action" in text
    assert "For call_tool rounds, omit `channel:summary` entirely" in text
    assert "For complete/exit rounds, include exactly one `channel:summary`" in text
    assert "Use non-empty `channel:code` only immediately after an `exec_tools.execute_code_python` action" in text
    assert "[SINGLE-ACTION CAUSALITY — HARD]" in text
    assert "The runtime executes that action only after this response ends" in text
    assert "[STRATEGY TRAITS — WHAT MAY SHARE A ROUND]" not in text
    assert "if action B's success or content depends on action A's result" not in text
    assert "include multiple JSON objects or fenced JSON blocks inside the single `channel:action` instance" in text
    assert "Final answer shape (only when action is complete or exit)" in text
    assert "Goal, Outcome, Key facts, Refs" in text
    assert "This protocol is SINGLE-ACTION: exactly one tool call per response." in text
    assert "Exec tool DOES NOT have a `code` parameter." in text
    assert "Code goes only in `channel:code`." in text


def test_build_decision_system_text_has_no_stale_single_tool_limit_hint():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
    )
    assert "TEMPORARY CURRENT LIMIT" not in text
    assert "use later rounds for the rest" not in text
    assert "Each JSON object may contain at most ONE tool_call object." in text
    assert "This protocol is SINGLE-ACTION: exactly one tool call per response." in text
    assert "ref:<visible_logical_path>" in text
    assert "ref:<bound artifact path>" not in text
    assert "Default write rule: reports, briefs, HTML, Markdown, slide source" in text
    assert "must be written with `react.write channel=canvas`" in text


def test_build_decision_system_text_prefers_visible_document_source_refs():
    text = build_decision_system_text(
        adapters=[],
        infra_adapters=[],
        workspace_implementation="custom",
    )
    assert "Prefer generating source content with `react.write`." in text
    assert "Preferred: then call the renderer with `content=\"ref:<visible text source ref>\"`" in text
    assert "Inline renderer content is accepted when needed." in text
    assert "Do not bind physical paths,\n  external owner refs, or internal artifacts into rendering_tools.write_*" in text
    assert "exec output with" in text and "visibility=external" in text
    assert "If generated content is meant for the user to see, download, approve, or use" in text
    assert "Internal text artifacts are still valid for private notes" in text
    assert "shape is wrong" in text
    assert "Use the input type documented by the target rendering tool." in text
    assert "Use HTML source for PDF" not in text
