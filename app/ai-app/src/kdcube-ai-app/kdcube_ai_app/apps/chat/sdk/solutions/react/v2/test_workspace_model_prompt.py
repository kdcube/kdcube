# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import get_workspace_implementation_guide
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.agents.decision import build_decision_system_text


def test_get_workspace_implementation_guide_custom_mentions_hosting_backed_mode():
    guide = get_workspace_implementation_guide("custom")
    assert "react.pull(paths=[...])" in guide
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
