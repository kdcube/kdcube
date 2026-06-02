from __future__ import annotations

import importlib
import inspect

from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem


def test_memory_tools_module_exposes_tools_owner_for_isolated_runtime() -> None:
    module = importlib.import_module("kdcube_ai_app.apps.chat.sdk.context.memory.tools")

    assert module.tools is module
    assert module.tools.search_memory is module.search_memory
    assert module.tools.record_memory is module.record_memory


def test_memory_tools_list_uses_exposed_callables() -> None:
    module = importlib.import_module("kdcube_ai_app.apps.chat.sdk.context.memory.tools")

    listed = module.tools.list_tools()

    assert listed["search_memory"]["callable"] is module.search_memory
    assert listed["record_memory"]["callable"] is module.record_memory


def test_memory_tools_declare_react_event_sources_for_alias() -> None:
    module = importlib.import_module("kdcube_ai_app.apps.chat.sdk.context.memory.tools")

    subsystem = EventSourceSubsystem(modules=[{"mod": module, "alias": "memory"}])

    expected = {
        "memory.search_memory",
        "memory.recent_memories",
        "memory.record_memory",
        "memory.confirm_memory",
        "memory.retire_memory",
    }
    declared = {item["event_source_id"] for item in subsystem.list_sources()}
    assert expected <= declared

    for event_source_id in expected:
        source = subsystem.by_event_source_id(event_source_id)
        assert source is not None
        assert source.kind == "react.tool"
        assert [binding.event_policy_id for binding in source.react.block_production] == [
            "react.block_production.tool_default",
            "react.block_production.generic_result_item",
            "react.block_production.declared_file_items",
        ]
        assert [binding.event_policy_id for binding in source.react.timeline_projection] == [
            "react.timeline_projection.identity",
        ]
        assert [binding.event_policy_id for binding in source.react.compaction_projection] == [
            "react.compaction_projection.identity",
        ]


def test_memory_tool_signatures_do_not_expose_originator() -> None:
    module = importlib.import_module("kdcube_ai_app.apps.chat.sdk.context.memory.tools")

    agent_visible = [
        module.record_memory,
        module.confirm_memory,
        module.retire_memory,
        module.UserMemoryTools.record_memory,
        module.UserMemoryTools.confirm_memory,
        module.UserMemoryTools.retire_memory,
    ]

    for fn in agent_visible:
        assert "originator" not in inspect.signature(fn).parameters
