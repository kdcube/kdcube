# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.runtime.tool_traits import strategy_values
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import (
    named_service_namespace_client_tools_config,
    named_service_namespace_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import OBJECT_UPSERT


MEMORY_NAMED_SERVICE_NAMESPACE = "mem"

MEMORY_NAMESPACE_INTRO = "Durable user memory — facts, preferences, and decisions to remember across conversations. Search and read memories for relevant context; save or update a memory when something is worth keeping long-term, forget one the user no longer wants, and pin a memory to the hot set (keep it always-loaded) when it should stay top-of-mind. It's one of the user's memory realms — when you need to recall what the user saved or told you, look here alongside their context boards (`cnv`) and past conversations."

MEMORY_CONTEXT_INSTRUCTIONS = """
[MEMORY CONTEXT]
`mem:record:<id>` points to one saved user memory record. Older `me:<id>` or
`mem:<id>` refs may appear in historical context; treat them as aliases and use
`mem:record:<id>` for new named-service calls, pulls, pins, and final references.

If the visible memory text is enough for the task, use it directly.

When a memory block shows `object_ref: mem:record:<id>` and exact saved memory
content is needed, import that object ref with `react.pull(paths=["mem:record:<id>"])`,
then inspect the returned `fi:` logical path or physical path.
""".strip()


DURABLE_USER_MEMORY_POLICY_INSTRUCTIONS = """
[DURABLE USER MEMORY — POLICY]
- Durable user memory is user-visible, editable, and cross-conversation.
- It is not the same as Internal Memory Beacons.
- Use durable user memory only for stable user-visible facts, preferences, durable decisions, reusable anchors, specs, milestones, or long-lived state.
- Durable memory authoring rule: `memory` = compact trigger first + rule; `context` = why this exists / provenance / examples only.
- Current user instructions and visible turn context override memory if they conflict.
- Do not create, update, or retire durable user memory unless a visible model-callable memory operation is available and the announced write policy allows it.
- If durable memory writes are disabled, do not simulate them with internal files or final-answer promises.
- Durable memory writes are neutral for same-round compatibility only when the rendered tool catalog marks the concrete operation as neutral for the memory namespace.
- After a durable memory write, inspect the visible tool result in the next round before acknowledging success. If the write failed or is not visible, do not claim it was saved.
- Do not advertise durable-memory writes in root `notes` like "saving memory" or "memory saved".
  `notes` are user-visible; repeated memory/protocol-recovery notes make the assistant look stuck.
  If the user asked you to remember something, acknowledge it once in a later clean final_answer
  only after the write result is visible and successful.
- For current-task or current-conversation recovery, use Internal Memory Beacons instead.
- If proposal-only mode is enabled, proposals are not active memory; they require user, reconciler, or policy confirmation.
- If explicit-user-request mode is enabled, write/propose durable memory only when the user explicitly asks to remember, forget, update, save, or pin something.
""".strip()


def _get_path(data: Mapping[str, Any] | None, path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _is_enabled(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "disabled"}
    return bool(value)


def _memory_namespace(bundle_props: Mapping[str, Any] | None) -> str:
    configured = _get_path(bundle_props or {}, "memory.named_service_namespace")
    text = str(configured or "").strip().lower().rstrip(":")
    return text or MEMORY_NAMED_SERVICE_NAMESPACE


def _memory_component_enabled(bundle_props: Mapping[str, Any] | None) -> bool:
    config = _get_path(bundle_props or {}, "memory", {})
    if not isinstance(config, Mapping):
        return False
    if not _is_enabled(config.get("enabled"), default=False):
        return False
    return _is_enabled(_get_path(config, "announce.enabled"), default=True) or _is_enabled(
        _get_path(config, "widget.enabled"),
        default=False,
    )


def _memory_named_service_configured(
    bundle_props: Mapping[str, Any] | None,
    *,
    namespace: str,
    client_id: Any,
) -> bool:
    tools = named_service_namespace_client_tools_config(
        bundle_props,
        namespace=namespace,
        client_id=client_id,
    )
    if tools:
        return True
    return bool(named_service_namespace_config(bundle_props, namespace=namespace))


def _operation_allowed(tools: Mapping[str, Any], operation: str) -> bool:
    raw = tools.get("allowed_operations") or tools.get("allowed") or tools.get("operations")
    if not isinstance(raw, (list, tuple, set)):
        return False
    allowed = {str(item or "").strip() for item in raw if str(item or "").strip()}
    return "*" in allowed or operation in allowed


def _upsert_neutral(tools: Mapping[str, Any]) -> bool:
    raw_traits = tools.get("tool_traits")
    if not isinstance(raw_traits, Mapping):
        return False
    upsert_traits = raw_traits.get("upsert_object")
    return "neutral" in strategy_values(upsert_traits)


def resolve_memory_react_additional_instructions(
    bundle_props: Mapping[str, Any] | None,
    *,
    client_id: Any = "main",
) -> str:
    namespace = _memory_namespace(bundle_props)
    memory_enabled = _memory_component_enabled(bundle_props)
    named_service_configured = _memory_named_service_configured(
        bundle_props,
        namespace=namespace,
        client_id=client_id or "main",
    )
    if not memory_enabled and not named_service_configured:
        return ""

    blocks = [MEMORY_CONTEXT_INSTRUCTIONS, DURABLE_USER_MEMORY_POLICY_INSTRUCTIONS]

    tools = named_service_namespace_client_tools_config(
        bundle_props,
        namespace=namespace,
        client_id=client_id or "main",
    )
    if isinstance(tools, Mapping) and _operation_allowed(tools, OBJECT_UPSERT):
        neutral_note = (
            "The rendered tool catalog should show this memory namespace override as `strategy: neutral`; "
            "when it does, the write may share a round with a separate `complete`/`exit` action."
            if _upsert_neutral(tools)
            else "Follow the rendered tool catalog strategy for this operation; do not assume it is neutral."
        )
        blocks.append(
            f"""
[DURABLE USER MEMORY — NAMED-SERVICE WRITE]
- Durable memory writes are available through `named_services.upsert_object(namespace="{namespace}", ...)`.
- Use that operation only when the user explicitly asks to remember, forget, update, save, or pin something, or when the visible memory policy explicitly allows a proposal/write.
- {neutral_note}
- Do not claim the memory was saved until the tool result is visible and successful.
""".strip()
        )

    return "\n\n".join(block for block in blocks if block.strip())


# Backward-compatible read/context block for callers that still import the
# legacy constant directly. Writable durable-memory policy is intentionally
# resolved by `resolve_memory_react_additional_instructions`.
MEMORY_REACT_ADDITIONAL_INSTRUCTIONS = MEMORY_CONTEXT_INSTRUCTIONS


__all__ = [
    "DURABLE_USER_MEMORY_POLICY_INSTRUCTIONS",
    "MEMORY_CONTEXT_INSTRUCTIONS",
    "MEMORY_NAMED_SERVICE_NAMESPACE",
    "MEMORY_NAMESPACE_INTRO",
    "MEMORY_REACT_ADDITIONAL_INSTRUCTIONS",
    "resolve_memory_react_additional_instructions",
]
