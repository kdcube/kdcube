# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.read import TOOL_SPEC as READ_SPEC, handle_react_read
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.pull import TOOL_SPEC as PULL_SPEC, handle_react_pull
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.checkout import TOOL_SPEC as CHECKOUT_SPEC, handle_react_checkout
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.write import TOOL_SPEC as WRITE_SPEC, handle_react_write
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.patch import TOOL_SPEC as PATCH_SPEC, handle_react_patch
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.memsearch import TOOL_SPEC as MEMSEARCH_SPEC, handle_react_memsearch
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.hide import TOOL_SPEC as HIDE_SPEC, handle_react_hide
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.rg import TOOL_SPEC as RG_SPEC, handle_react_rg
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.plan import TOOL_SPEC as PLAN_SPEC, handle_react_plan
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.delegate import TOOL_SPEC as DELEGATE_SPEC, handle_react_delegate
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.contribute import TOOL_SPEC as CONTRIBUTE_SPEC, handle_react_contribute
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.external import TOOL_SPEC as EXTERNAL_SPEC, handle_external_tool

"""
Shared React tools package.

This module stays lazy to avoid circular imports between artifacts/workspace helpers
and tool handlers.
"""

__all__ = [
    "READ_SPEC",
    "PULL_SPEC",
    "CHECKOUT_SPEC",
    "WRITE_SPEC",
    "PATCH_SPEC",
    "MEMSEARCH_SPEC",
    "HIDE_SPEC",
    "RG_SPEC",
    "PLAN_SPEC",
    "DELEGATE_SPEC",
    "CONTRIBUTE_SPEC",
    "EXTERNAL_SPEC",
    "handle_react_read",
    "handle_react_pull",
    "handle_react_checkout",
    "handle_react_write",
    "handle_react_patch",
    "handle_react_memsearch",
    "handle_react_hide",
    "handle_react_rg",
    "handle_react_plan",
    "handle_react_delegate",
    "handle_react_contribute",
    "handle_external_tool",
]

_LAZY_ATTRS = {
    "READ_SPEC": ("read", "TOOL_SPEC"),
    "handle_react_read": ("read", "handle_react_read"),
    "PULL_SPEC": ("pull", "TOOL_SPEC"),
    "handle_react_pull": ("pull", "handle_react_pull"),
    "CHECKOUT_SPEC": ("checkout", "TOOL_SPEC"),
    "handle_react_checkout": ("checkout", "handle_react_checkout"),
    "WRITE_SPEC": ("write", "TOOL_SPEC"),
    "handle_react_write": ("write", "handle_react_write"),
    "PATCH_SPEC": ("patch", "TOOL_SPEC"),
    "handle_react_patch": ("patch", "handle_react_patch"),
    "MEMSEARCH_SPEC": ("memsearch", "TOOL_SPEC"),
    "handle_react_memsearch": ("memsearch", "handle_react_memsearch"),
    "HIDE_SPEC": ("hide", "TOOL_SPEC"),
    "handle_react_hide": ("hide", "handle_react_hide"),
    "RG_SPEC": ("rg", "TOOL_SPEC"),
    "handle_react_rg": ("rg", "handle_react_rg"),
    "PLAN_SPEC": ("plan", "TOOL_SPEC"),
    "handle_react_plan": ("plan", "handle_react_plan"),
    "DELEGATE_SPEC": ("delegate", "TOOL_SPEC"),
    "handle_react_delegate": ("delegate", "handle_react_delegate"),
    "CONTRIBUTE_SPEC": ("contribute", "TOOL_SPEC"),
    "handle_react_contribute": ("contribute", "handle_react_contribute"),
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
