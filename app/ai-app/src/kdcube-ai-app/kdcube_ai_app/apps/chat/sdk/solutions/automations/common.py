from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context import (
    ScopedBundleConfig,
    bind_integrations,
    error,
    host_files,
    log_tool_error,
    log_tool_start,
    log_tool_success,
    ok,
    scope,
)


def extract_automation_execution_context(bundle_call_context: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Return a flat automation execution context from an app call context.

    Direct automation execution binds automation ids at the top level. Generic
    background jobs keep app-owned job data under ``payload`` because not all
    background jobs are automations. This helper gives automation tools one
    stable read shape without leaking processor envelope details into app code.
    """
    context = dict(bundle_call_context or {}) if isinstance(bundle_call_context, Mapping) else {}
    if str(context.get("kind") or "") != "background_job":
        return context

    payload = context.get("payload") if isinstance(context.get("payload"), Mapping) else {}
    merged = dict(payload)
    merged.update({key: value for key, value in context.items() if key != "payload"})
    return merged


def extract_automation_execution_context_from_scope(scope_context: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Return a flat automation execution context from the current tool scope."""
    raw = scope_context.get("bundle_call_context") if isinstance(scope_context, Mapping) else {}
    return extract_automation_execution_context(raw if isinstance(raw, Mapping) else {})


__all__ = [
    "ScopedBundleConfig",
    "bind_integrations",
    "error",
    "extract_automation_execution_context",
    "extract_automation_execution_context_from_scope",
    "host_files",
    "log_tool_error",
    "log_tool_start",
    "log_tool_success",
    "ok",
    "scope",
]
