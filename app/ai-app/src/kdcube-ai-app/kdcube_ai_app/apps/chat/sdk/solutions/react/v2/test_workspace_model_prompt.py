# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import get_workspace_implementation_guide
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.agents.decision import build_decision_system_text


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
    assert "ls workspace" in guide
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
    assert "ls workspace" in text


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
    assert "Output protocol (strict): you must produce content which represents one round" in text
    assert "In a single round, include exactly one <channel:thinking>, one <channel:code>, and one or more <channel:ReactDecisionOutV2> channel instances." in text
    assert "The optional <channel:summary> may appear exactly once, and only when the response contains a single complete/exit action and no tool-call actions." in text
    assert "Do NOT emit <channel:summary> in code execution rounds." in text
    assert "For call_tool-only rounds, omit <channel:summary> entirely" in text
    assert "For complete/exit rounds, include exactly one <channel:summary>" in text
    assert "If you need multiple actions in one round, repeat only <channel:ReactDecisionOutV2>." in text
    assert "Never put > 1 JSON objects, > 1 fenced JSON blocks, or prose after the JSON inside one <channel:ReactDecisionOutV2> instance." in text
    assert "Final answer shape only when action is complete or exit" in text
    assert "Goal, Outcome, Key facts, Refs" in text
    assert "If you emit multiple tool-call actions, each action must be in its own separate <channel:ReactDecisionOutV2>...</channel:ReactDecisionOutV2> instance." in text
    assert "Use <channel:code> only when the single action is exec_tools.execute_code_python" in text
