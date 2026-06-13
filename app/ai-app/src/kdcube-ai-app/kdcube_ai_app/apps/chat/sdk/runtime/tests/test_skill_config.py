# SPDX-License-Identifier: MIT

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import (
    agent_skill_config_from_bundle_props,
)


def test_agent_skill_config_resolves_agent_root_and_consumers(tmp_path):
    props = {
        "surfaces": {
            "as_consumer": {
                "agents": {
                    "main": {
                        "skills": {
                            "custom_root": "skills",
                            "consumers": {
                                "solver.react.v2.decision.v2.strong": {
                                    "enabled": ["product.preferences"],
                                },
                            },
                        },
                    },
                },
            },
        },
    }

    resolved = agent_skill_config_from_bundle_props(
        props,
        "main",
        bundle_root=tmp_path,
    )

    assert resolved.custom_skills_root == tmp_path / "skills"
    assert resolved.agents_config == {
        "solver.react.v2.decision.v2.strong": {
            "enabled": ["product.preferences"],
        },
    }


def test_agent_skill_config_can_disable_bundle_local_skill_root(tmp_path):
    props = {
        "surfaces": {
            "as_consumer": {
                "agents": {
                    "main": {
                        "skills": {"enabled": False},
                    },
                },
            },
        },
    }

    resolved = agent_skill_config_from_bundle_props(
        props,
        "main",
        bundle_root=tmp_path,
    )

    assert resolved.custom_skills_root == ""
    assert resolved.agents_config == {}


def test_agent_skill_config_direct_visibility_applies_to_agent_keys():
    props = {
        "surfaces": {
            "as_consumer": {
                "agents": {
                    "main": {
                        "skills": {
                            "enabled": ["public.*"],
                        },
                    },
                },
            },
        },
    }

    resolved = agent_skill_config_from_bundle_props(props, "main")

    assert resolved.agents_config["main"] == {"enabled": ["public.*"]}
    assert resolved.agents_config["default_agent"] == {"enabled": ["public.*"]}
