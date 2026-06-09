from __future__ import annotations

import importlib
import inspect

import pytest

from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem


def test_memory_tools_module_exposes_tools_owner_for_isolated_runtime() -> None:
    module = importlib.import_module("kdcube_ai_app.apps.chat.sdk.context.memory.tools")

    assert module.tools is module
    assert module.tools.search_memory is module.search_memory
    assert module.tools.read_memory is module.read_memory
    assert module.tools.record_memory is module.record_memory


def test_memory_tools_list_uses_exposed_callables() -> None:
    module = importlib.import_module("kdcube_ai_app.apps.chat.sdk.context.memory.tools")

    listed = module.tools.list_tools()

    assert listed["search_memory"]["callable"] is module.search_memory
    assert listed["read_memory"]["callable"] is module.read_memory
    assert listed["record_memory"]["callable"] is module.record_memory


def test_memory_tools_declare_react_event_sources_for_alias() -> None:
    module = importlib.import_module("kdcube_ai_app.apps.chat.sdk.context.memory.tools")

    subsystem = EventSourceSubsystem(modules=[{"mod": module, "alias": "memory"}])

    expected = {
        "memory.context",
        "memory.search_memory",
        "memory.recent_memories",
        "memory.read_memory",
        "memory.record_memory",
        "memory.confirm_memory",
        "memory.retire_memory",
    }
    declared = {item["event_source_id"] for item in subsystem.list_sources()}
    assert expected <= declared

    context_source = subsystem.by_event_source_id("memory.context")
    assert context_source is not None
    assert context_source.kind == "event.context"
    assert [binding.event_policy_id for binding in context_source.react.block_production] == [
        "memory.block_production.context_event",
    ]
    assert [binding.event_policy_id for binding in context_source.react.timeline_projection] == [
        "memory.timeline_projection.context_event",
    ]
    assert [binding.event_policy_id for binding in context_source.react.compaction_projection] == [
        "memory.compaction_projection.context_event",
    ]

    for event_source_id in expected - {"memory.read_memory", "memory.context"}:
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

    source = subsystem.by_event_source_id("memory.read_memory")
    assert source is not None
    assert [binding.event_policy_id for binding in source.react.block_production] == [
        "react.block_production.tool_default",
        "memory.block_production.read_result",
    ]
    reader = subsystem.event_source_reader("mem")
    assert reader is not None
    assert reader.event_source_id == "memory.read_memory"


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


@pytest.mark.asyncio
async def test_memory_event_reader_resolves_explicit_refs_across_user_memory_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("kdcube_ai_app.apps.chat.sdk.context.memory.tools")
    calls: list[dict[str, object]] = []

    async def fake_read_memory(**kwargs):
        calls.append(dict(kwargs))
        return {"ok": True, "object_ref": kwargs["object_ref"], "memory": {"id": "mem_1", "memory": "hello"}}

    monkeypatch.setattr(module, "read_memory", fake_read_memory)

    result = await module.read_memory_event_ref(ref="mem:mem_1")

    assert result["ok"] is True
    assert calls == [{
        "object_ref": "mem:mem_1",
        "scope_filter": "all_user_memories",
        "include_events": "true",
    }]
