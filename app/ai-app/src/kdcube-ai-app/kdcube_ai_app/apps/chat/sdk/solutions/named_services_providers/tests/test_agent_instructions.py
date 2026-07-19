# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.agent_instructions import (
    compose_named_service_agent_instructions,
    named_service_agent_instruction_block,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import (
    compose_named_service_react_instructions,
)


def _consumer_props(*namespaces: str) -> dict:
    return {
        "surfaces": {
            "as_consumer": {
                "agents": {
                    "main": {
                        "tools": [
                            {
                                "kind": "named_service",
                                "alias": "named_services",
                                "namespaces": {
                                    ns: {"allowed": ["provider.about", "object.search"]}
                                    for ns in namespaces
                                },
                            }
                        ]
                    }
                }
            }
        }
    }


INTROS = {
    "mem": {"intro": "Durable user memory", "label": "User memories"},
    "task": {"intro": "Issue tracker", "label": "Tasks"},
}


def test_react_surface_matches_legacy_react_composer():
    props = _consumer_props("mem", "task")
    via_agent = compose_named_service_agent_instructions(
        props, client_id="main", intros=INTROS, surface="react"
    )
    via_react = compose_named_service_react_instructions(props, client_id="main", intros=INTROS)
    assert via_agent == via_react
    assert "[NAMED SERVICES — NAMESPACE OBJECT OPERATIONS]" in via_agent
    assert "- `mem` — Durable user memory" in via_agent


def test_bridge_surface_teaches_by_operation_name_with_bundle_file_tools():
    props = _consumer_props("mem")
    block = compose_named_service_agent_instructions(
        props, client_id="main", intros=INTROS, surface="bridge",
        pull_tool="pull_files", read_tool="read_file",
    )
    # operation vocabulary, not ReAct tool ids
    for op in ("provider_about", "object_schema", "search_objects", "object_action",
               "upsert_object", "delete_object", "host_file"):
        assert op in block
    assert "react.pull" not in block
    assert "named_services.provider_about" not in block
    assert "`pull_files`" in block
    assert "`read_file`" in block
    assert "- `mem` — Durable user memory" in block
    # contract-first + delta semantics survive on the bridge surface — and the
    # bridge is honest that no platform gate enforces the order here
    assert "CONTRACT FIRST" in block
    assert "NOTHING checks this order" in block
    assert "protocol notice" not in block
    assert '"add"' in block and '"remove"' in block
    assert "dedup_key" in block


def test_account_resolution_renders_on_both_surfaces():
    """Multi-account resolution parity: both teaching surfaces carry the same
    two-step behavior — list accounts (machine `account_id`/`ref` + human
    `label`), resolve by human name, target by id — keyed to the schema
    showing `account_id`."""
    react = compose_named_service_agent_instructions(
        _consumer_props("mail"), client_id="main", surface="react", intros=INTROS
    )
    bridge = compose_named_service_agent_instructions(
        _consumer_props("mail"), client_id="main", surface="bridge", intros=INTROS
    )
    for block in (react, bridge):
        assert "`account_id`" in block
        assert "serves several connected accounts" in block
        assert "human label" in block.lower()
        assert "never from memory or construction" in block
        assert "fans out across the eligible accounts" in block
    # the trigger is what the model can inspect: the schema
    assert "`object_schema` shows an `account_id`" in react
    # bridge keeps the reactive fallback phrased as the same behavior
    assert "asks for an account choice" in bridge


def test_bridge_surface_binds_the_door_tool_names_when_given():
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.instructions import (
        NAMED_SERVICES_MCP_DOOR_TOOL_NAMES,
    )

    block = compose_named_service_agent_instructions(
        _consumer_props("slack"), client_id="main", surface="bridge",
        intros=INTROS, operations=NAMED_SERVICES_MCP_DOOR_TOOL_NAMES,
    )
    # the EXACT bound tool names, not the abstract operation vocabulary
    for t in ("named_services_list", "named_services_capabilities", "named_services_schema",
              "named_services_search", "named_services_get", "named_services_action",
              "named_services_upsert", "named_services_host_file"):
        assert f"`{t}`" in block
    assert "`object_schema`" not in block
    assert "`search_objects`" not in block
    # the door adds the list-first step; contract-first binds to the real name
    assert "source of truth for the namespaces" in block
    assert "read that namespace's `named_services_schema`" in block


def test_bridge_surface_without_read_tool_drops_read_hint():
    block = compose_named_service_agent_instructions(
        _consumer_props("mem"), client_id="main", surface="bridge",
        pull_tool="run_python", read_tool="",
    )
    assert "`run_python`" in block
    assert "read_file" not in block


def test_explicit_namespaces_override_connected_derivation():
    block = compose_named_service_agent_instructions(
        {}, client_id="main", surface="bridge", namespaces=["slack"],
        intros={"slack": {"intro": "Team messaging"}},
    )
    assert "- `slack` — Team messaging" in block


def test_empty_namespaces_compose_empty():
    assert compose_named_service_agent_instructions({}, client_id="main") == ""
    assert compose_named_service_agent_instructions(
        _consumer_props("mem"), client_id="main", namespaces=[]
    ) == ""


def test_unknown_surface_raises():
    with pytest.raises(KeyError):
        compose_named_service_agent_instructions(
            _consumer_props("mem"), client_id="main", surface="nope"
        )


@pytest.mark.asyncio
async def test_gatherer_composes_bare_roster_without_discovery():
    block = await named_service_agent_instruction_block(
        bundle_props=_consumer_props("mem"),
        client_id="main",
        surface="bridge",
    )
    assert "- `mem`" in block
    assert "[NAMED SERVICES — NAMESPACE OBJECT OPERATIONS]" in block


@pytest.mark.asyncio
async def test_gatherer_empty_without_connected_namespaces():
    assert await named_service_agent_instruction_block(
        bundle_props={}, client_id="main", surface="bridge"
    ) == ""


@pytest.mark.asyncio
async def test_gatherer_uses_prebuilt_discovery_intros():
    class _Discovery:
        async def namespace_intros(self, namespaces):
            return {"mem": {"intro": "Durable user memory", "label": "User memories"}}

    block = await named_service_agent_instruction_block(
        bundle_props=_consumer_props("mem"),
        client_id="main",
        surface="react",
        discovery=_Discovery(),
    )
    assert "- `mem` — Durable user memory" in block
