from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.configuration import (
    ConfiguredAgentInput,
    configured_agent_tool_config,
    configured_agent_input,
    configured_run_directory,
    configured_tool_settings,
    configured_tool_ids,
)


def test_agent_private_state_scope_includes_user_conversation_and_agent() -> None:
    configured = ConfiguredAgentInput(
        user_id="user one",
        user_type="regular",
        session_id="shell-session",
        conversation_id="conversation/one",
    )

    first = configured.continuity_key(
        tenant="tenant",
        project="project",
        agent_id="agent-a",
    )
    second = configured.continuity_key(
        tenant="tenant",
        project="project",
        agent_id="agent-b",
    )

    assert first == "tenant/project/user%20one/conversation%2Fone/agent-a"
    assert second.endswith("/agent-b")
    assert first != second


def test_agent_private_state_scope_separates_users() -> None:
    def state_key(user_id: str) -> str:
        return ConfiguredAgentInput(
            user_id=user_id,
            user_type="regular",
            session_id="shell-session",
            conversation_id="shared-name",
        ).continuity_key(
            tenant="tenant",
            project="project",
            agent_id="agent",
        )

    assert state_key("alice") != state_key("bob")


def test_configured_agent_input_is_required_and_run_paths_are_per_invocation(
    tmp_path: Path,
) -> None:
    config = {
        "agent": {
            "input": {
                "user_id": "alice",
                "user_type": "regular",
                "session_id": "terminal-7",
                "conversation_id": "research-42",
                "recall_conversation_id": "research-recall",
            }
        }
    }
    configured = configured_agent_input(config)

    assert configured.recall_conversation_id == "research-recall"
    assert configured.run_path(tmp_path, run_id="run-a") == (
        tmp_path / "runs" / "alice" / "research-42" / "run-a"
    )
    assert configured.run_path(tmp_path, run_id="run-a") != configured.run_path(
        tmp_path,
        run_id="run-b",
    )
    del config["agent"]["input"]["conversation_id"]
    with pytest.raises(ValueError, match="conversation_id"):
        configured_agent_input(config)


def test_recall_conversation_id_accepts_an_explicit_override() -> None:
    config = {
        "agent": {
            "input": {
                "user_id": "alice",
                "user_type": "regular",
                "session_id": "terminal-7",
                "conversation_id": "research-42",
                "recall_conversation_id": "descriptor-recall",
            }
        }
    }

    configured = configured_agent_input(
        config,
        recall_conversation_id="cli-recall",
    )

    assert configured.recall_conversation_id == "cli-recall"


def test_tool_settings_are_owned_by_the_exact_tool_row(tmp_path: Path) -> None:
    config = {
        "agent": {
            "run_directory": "./runs",
            "tools": [
                {
                    "id": "web",
                    "kind": "python",
                    "module": "kdcube_ai_app.apps.chat.sdk.tools.web_tools",
                    "alias": "web_tools",
                    "allowed": ["web_search"],
                    "runtime": {"web_search": "local"},
                    "settings": {"filter": {"allowlist": ["example.org"]}},
                },
            ],
        }
    }

    tools = configured_agent_tool_config(
        config,
        agent_id="native",
        bundle_root=tmp_path,
    )

    assert tools.allowed_tool_names_by_alias == {"web_tools": ["web_search"]}
    assert configured_tool_ids(tools) == ("web_tools.web_search",)
    assert configured_tool_settings(config, connection_id="web") == {
        "filter": {"allowlist": ["example.org"]}
    }
    assert (
        configured_run_directory(
            config,
            config_path=tmp_path / "config.yaml",
        )
        == (tmp_path / "runs").resolve()
    )


def test_tool_settings_reject_unknown_id_and_non_mapping_settings() -> None:
    config = {"agent": {"tools": [{"id": "search.primary", "settings": []}]}}

    with pytest.raises(
        ValueError, match=r"agent\.tools\[0\]\.settings must be a mapping"
    ):
        configured_agent_tool_config(config, agent_id="native", bundle_root=Path.cwd())

    config = {"agent": {"tools": [{"id": "search.primary"}]}}
    with pytest.raises(ValueError, match="has no tool source with id 'search.missing'"):
        configured_tool_settings(config, connection_id="search.missing")


def test_run_directory_must_be_a_path_string(tmp_path: Path) -> None:
    config = {"agent": {"run_directory": {"directory": "./runs"}, "tools": []}}

    with pytest.raises(ValueError, match="agent.run_directory must be a path string"):
        configured_run_directory(config, config_path=tmp_path / "config.yaml")
