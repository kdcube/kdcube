# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import get_workspace_model_guide
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.agents.decision import build_decision_system_text


def test_get_workspace_model_guide_defaults_to_legacy():
    guide = get_workspace_model_guide("legacy")
    assert "EXPLICIT workspace activation" not in guide
    assert "react.pull" not in guide


def test_get_workspace_model_guide_git_pull_mentions_pull_and_binary_rule():
    guide = get_workspace_model_guide("git_pull")
    assert "react.pull(paths=[...])" in guide
    assert "EXACT file ref" in guide
    assert "binary descendants" in guide


def test_build_decision_system_text_uses_selected_workspace_model():
    text = build_decision_system_text(adapters=[], infra_adapters=[], workspace_model="git_pull")
    assert "react.pull(paths=[...])" in text
    assert "EXACT file ref" in text
    assert "binary descendants" in text
