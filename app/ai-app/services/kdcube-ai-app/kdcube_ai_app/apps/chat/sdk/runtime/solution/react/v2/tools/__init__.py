# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from .read import TOOL_SPEC as READ_SPEC, handle_react_read
from .write import TOOL_SPEC as WRITE_SPEC, handle_react_write
from .patch import TOOL_SPEC as PATCH_SPEC, handle_react_patch
from .memory_read import TOOL_SPEC as MEMORY_READ_SPEC, handle_react_memory_read
from .memory_hide import TOOL_SPEC as MEMORY_HIDE_SPEC, handle_react_memory_hide
from .search_files import TOOL_SPEC as SEARCH_FILES_SPEC, handle_react_search_files
from .plan import TOOL_SPEC as PLAN_SPEC, handle_react_plan
from .external import TOOL_SPEC as EXTERNAL_SPEC, handle_external_tool

__all__ = [
    "READ_SPEC",
    "WRITE_SPEC",
    "PATCH_SPEC",
    "MEMORY_READ_SPEC",
    "MEMORY_HIDE_SPEC",
    "SEARCH_FILES_SPEC",
    "PLAN_SPEC",
    "EXTERNAL_SPEC",
    "handle_react_read",
    "handle_react_write",
    "handle_react_patch",
    "handle_react_memory_read",
    "handle_react_memory_hide",
    "handle_react_search_files",
    "handle_react_plan",
    "handle_external_tool",
]
