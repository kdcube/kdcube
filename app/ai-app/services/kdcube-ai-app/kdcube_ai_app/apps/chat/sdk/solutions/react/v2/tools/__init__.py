# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .read import TOOL_SPEC as READ_SPEC, handle_react_read
    from .write import TOOL_SPEC as WRITE_SPEC, handle_react_write
    from .patch import TOOL_SPEC as PATCH_SPEC, handle_react_patch
    from .memsearch import TOOL_SPEC as MEMSEARCH_SPEC, handle_react_memsearch
    from .hide import TOOL_SPEC as HIDE_SPEC, handle_react_hide
    from .search_files import TOOL_SPEC as SEARCH_FILES_SPEC, handle_react_search_files
    from .plan import TOOL_SPEC as PLAN_SPEC, handle_react_plan
    from .external import TOOL_SPEC as EXTERNAL_SPEC, handle_external_tool

"""
React v2 tools package.

Why this file is lazy:
----------------------
`artifacts.py` imports `tools.common`, which requires importing the `tools` package.
If we eagerly import every tool here, `tools.read` will import `artifacts.py` again
while it is still initializing → circular import.

To avoid that, this module exposes tool specs/handlers via `__getattr__` and lazy
imports. Static type checkers are satisfied via `TYPE_CHECKING` imports above.
"""

__all__ = [
    "READ_SPEC",
    "WRITE_SPEC",
    "PATCH_SPEC",
    "MEMSEARCH_SPEC",
    "HIDE_SPEC",
    "SEARCH_FILES_SPEC",
    "PLAN_SPEC",
    "EXTERNAL_SPEC",
    "handle_react_read",
    "handle_react_write",
    "handle_react_patch",
    "handle_react_memsearch",
    "handle_react_hide",
    "handle_react_search_files",
    "handle_react_plan",
    "handle_external_tool",
]

_LAZY_ATTRS = {
    "READ_SPEC": ("read", "TOOL_SPEC"),
    "handle_react_read": ("read", "handle_react_read"),
    "WRITE_SPEC": ("write", "TOOL_SPEC"),
    "handle_react_write": ("write", "handle_react_write"),
    "PATCH_SPEC": ("patch", "TOOL_SPEC"),
    "handle_react_patch": ("patch", "handle_react_patch"),
    "MEMSEARCH_SPEC": ("memsearch", "TOOL_SPEC"),
    "handle_react_memsearch": ("memsearch", "handle_react_memsearch"),
    "HIDE_SPEC": ("hide", "TOOL_SPEC"),
    "handle_react_hide": ("hide", "handle_react_hide"),
    "SEARCH_FILES_SPEC": ("search_files", "TOOL_SPEC"),
    "handle_react_search_files": ("search_files", "handle_react_search_files"),
    "PLAN_SPEC": ("plan", "TOOL_SPEC"),
    "handle_react_plan": ("plan", "handle_react_plan"),
    "EXTERNAL_SPEC": ("external", "TOOL_SPEC"),
    "handle_external_tool": ("external", "handle_external_tool"),
}


def __getattr__(name: str):
    target = _LAZY_ATTRS.get(name)
    if not target:
        raise AttributeError(name)
    mod_name, attr_name = target
    from importlib import import_module
    mod = import_module(f"{__name__}.{mod_name}")
    value = getattr(mod, attr_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY_ATTRS.keys()))
