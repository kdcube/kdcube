# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import copy
from typing import Dict, Any, List

from kdcube_ai_app.apps.chat.sdk.solutions.react.tools import (
    READ_SPEC,
    PULL_SPEC,
    CHECKOUT_SPEC,
    WRITE_SPEC,
    PATCH_SPEC,
    MEMSEARCH_SPEC,
    HIDE_SPEC,
    RG_SPEC,
    PLAN_SPEC,
    handle_react_read,
    handle_react_pull,
    handle_react_checkout,
    handle_react_write,
    handle_react_patch,
    handle_react_memsearch,
    handle_react_hide,
    handle_react_rg,
    handle_react_plan,
    handle_external_tool,
)


def get_react_tools_catalog() -> List[Dict[str, object]]:
    specs = [
        READ_SPEC,
        PULL_SPEC,
        CHECKOUT_SPEC,
        WRITE_SPEC,
        PATCH_SPEC,
        MEMSEARCH_SPEC,
        HIDE_SPEC,
        RG_SPEC,
        PLAN_SPEC,
    ]
    strategy_by_id = {
        "react.read": ["exploration"],
        "react.pull": ["exploration"],
        "react.checkout": ["exploration"],
        "react.write": ["exploitation"],
        "react.patch": ["exploitation"],
        "react.memsearch": ["exploration"],
        "react.hide": ["neutral"],
        "react.rg": ["exploration"],
        "react.plan": ["neutral"],
    }
    out: List[Dict[str, object]] = []
    for spec in specs:
        item = copy.deepcopy(spec)
        tool_id = str(item.get("id") or "")
        if tool_id in strategy_by_id:
            item["tool_traits"] = {"strategy": strategy_by_id[tool_id]}
        out.append(item)
    return out


__all__ = [
    "get_react_tools_catalog",
    "handle_react_read",
    "handle_react_pull",
    "handle_react_checkout",
    "handle_react_write",
    "handle_react_patch",
    "handle_react_memsearch",
    "handle_react_hide",
    "handle_react_rg",
    "handle_react_plan",
    "handle_external_tool",
]
