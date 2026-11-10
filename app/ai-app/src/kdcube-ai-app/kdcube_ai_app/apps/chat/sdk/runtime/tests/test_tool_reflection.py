# SPDX-License-Identifier: MIT

"""Reflected tool catalogs publish declared tools only, with their declared descriptions.

A tool module without ``kernel`` or ``list_tools()`` is introspected by
walking ``dir(module)``. Module-level imports are public and callable, and
``@kernel_function(description=...)`` stores its text on
``__kernel_function_description__``. Both catalogs (the tool manager's and the
agent inventory's) must therefore skip imports and read the kernel attribute.
"""

from __future__ import annotations

import importlib
import sys
import textwrap

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime import agent_inventory
from kdcube_ai_app.apps.chat.sdk.runtime.tool_reflection import (
    declared_tool_description,
    reflected_tool_callables,
    tool_defined_in_module,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem

MODULE_NAME = "kdcube_reflection_fixture_tools"
HELPER_MODULE_NAME = "kdcube_reflection_fixture_helpers"

IMPORTED_NAMES = {
    "Annotated",
    "Any",
    "Optional",
    "kernel_function",
    "lru_cache",
    "Mapping",
    "Sequence",
    "datetime",
    "helper_not_exported",
}
DECLARED_TOOLS = {"search_notes", "plain_tool", "async_tool", "reexported_tool"}


@pytest.fixture()
def tool_module(tmp_path, monkeypatch):
    (tmp_path / f"{HELPER_MODULE_NAME}.py").write_text(textwrap.dedent('''
        def reexported_tool(x: int) -> int:
            """Re-exported through __all__."""
            return x

        def helper_not_exported(x: int) -> int:
            """Imported but not exported."""
            return x
    '''))
    (tmp_path / f"{MODULE_NAME}.py").write_text(textwrap.dedent(f'''
        from typing import Annotated, Any, Optional
        from collections.abc import Mapping, Sequence
        from functools import lru_cache
        import datetime

        from semantic_kernel.functions import kernel_function

        from {HELPER_MODULE_NAME} import helper_not_exported, reexported_tool

        __all__ = ["search_notes", "plain_tool", "async_tool", "reexported_tool"]

        @kernel_function(name="search_notes", description="Search the operator notes by keyword.")
        def search_notes(query: Annotated[str, "keyword"]) -> str:
            return query

        def plain_tool(value: Optional[int] = None) -> int:
            """Plain tool documented by its docstring."""
            return value or 0

        async def async_tool() -> None:
            """Async tool."""
            return None

        def _private_helper() -> None:
            return None
    '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in (MODULE_NAME, HELPER_MODULE_NAME):
        sys.modules.pop(name, None)
    mod = importlib.import_module(MODULE_NAME)
    try:
        yield mod
    finally:
        for name in (MODULE_NAME, HELPER_MODULE_NAME):
            sys.modules.pop(name, None)


def test_reflection_skips_imports_and_keeps_every_declared_tool(tool_module):
    names = {name for name, _fn in reflected_tool_callables(tool_module)}

    assert names == DECLARED_TOOLS
    assert not names & IMPORTED_NAMES


def test_kernel_function_decoration_keeps_the_defining_module(tool_module):
    # @kernel_function decorates in place, so the tool is still "defined here".
    assert tool_defined_in_module(tool_module.search_notes, tool_module)
    assert not tool_defined_in_module(tool_module.kernel_function, tool_module)
    assert not tool_defined_in_module(tool_module.Optional, tool_module)


def test_objects_without_a_module_are_kept():
    class _NoModule:
        __module__ = None

        def __call__(self):
            return None

    mod = type(sys)("some_tools")
    assert tool_defined_in_module(_NoModule(), mod)


def test_explicit_tools_object_is_taken_as_declared():
    mod = type(sys)("tools_owner_module")

    class _Tools:
        def imported_looking(self):
            """Declared on the tools object."""

    mod.tools = _Tools()
    assert [name for name, _fn in reflected_tool_callables(mod)] == ["imported_looking"]


def test_description_reads_kernel_metadata_before_docstring(tool_module):
    assert declared_tool_description(tool_module.search_notes) == "Search the operator notes by keyword."
    assert declared_tool_description(tool_module.plain_tool) == "Plain tool documented by its docstring."
    assert declared_tool_description(None) == ""


def test_tool_subsystem_catalog_has_declared_tools_with_purposes(tool_module):
    subsystem = object.__new__(ToolSubsystem)
    entries = subsystem._introspect_module(tool_module, MODULE_NAME, "notes", use_sk=False)
    by_name = {entry["id"].rsplit(".", 1)[-1]: entry for entry in entries}

    assert set(by_name) == DECLARED_TOOLS
    assert "Search the operator notes by keyword." in str(by_name["search_notes"])


def test_agent_inventory_matches_the_tool_manager(tool_module):
    docs = agent_inventory._module_tool_docs(MODULE_NAME)
    names = agent_inventory._module_tool_names(MODULE_NAME)

    assert set(docs) == DECLARED_TOOLS
    assert set(names) == DECLARED_TOOLS
    assert docs["search_notes"] == "Search the operator notes by keyword."
