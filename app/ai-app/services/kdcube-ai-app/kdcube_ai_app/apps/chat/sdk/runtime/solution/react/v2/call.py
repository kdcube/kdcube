# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Dict, Any, List

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools import (
    READ_SPEC,
    WRITE_SPEC,
    PATCH_SPEC,
    MEMORY_READ_SPEC,
    MEMORY_HIDE_SPEC,
    SEARCH_FILES_SPEC,
    PLAN_SPEC,
    handle_react_read,
    handle_react_write,
    handle_react_patch,
    handle_react_memory_read,
    handle_react_memory_hide,
    handle_react_search_files,
    handle_react_plan,
    handle_external_tool,
)


def get_react_tools_catalog() -> List[Dict[str, object]]:
    return [
        READ_SPEC,
        WRITE_SPEC,
        PATCH_SPEC,
        MEMORY_READ_SPEC,
        MEMORY_HIDE_SPEC,
        SEARCH_FILES_SPEC,
        PLAN_SPEC,
    ]


__all__ = [
    "get_react_tools_catalog",
    "handle_react_read",
    "handle_react_write",
    "handle_react_patch",
    "handle_react_memory_read",
    "handle_react_memory_hide",
    "handle_react_search_files",
    "handle_react_plan",
    "handle_external_tool",
]
