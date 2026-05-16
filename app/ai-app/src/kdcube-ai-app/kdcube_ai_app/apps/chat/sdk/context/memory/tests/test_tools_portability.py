from __future__ import annotations

import importlib


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
