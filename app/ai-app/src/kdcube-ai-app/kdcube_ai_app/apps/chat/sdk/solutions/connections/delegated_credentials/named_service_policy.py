# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Named-service boundary narrowing, shared by grant issuance and the managed guard.

Imports nothing from the delegated-credential package: both callers import it at
module level.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = clean_text(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def operation_grants(policy: Mapping[str, Any], fallback: Mapping[str, Any]) -> set[str]:
    return set(
        as_string_list(
            policy.get("grants")
            or policy.get("scopes")
            or fallback.get("grants")
            or fallback.get("scopes")
        )
    )


def configured_named_service_operations(
    config: Mapping[str, Any] | None,
) -> dict[str, set[str]]:
    """``namespace -> operations`` a named-service descriptor configures.

    Namespace keys are normalized the way a selection normalizes them;
    operations keep the descriptor's own spelling.
    """
    namespaces = config.get("namespaces") if isinstance(config, Mapping) else None
    if not isinstance(namespaces, Mapping):
        return {}
    out: dict[str, set[str]] = {}
    for namespace, raw_policy in namespaces.items():
        name = clean_text(namespace).lower().rstrip(":")
        if not name or not isinstance(raw_policy, Mapping):
            continue
        operations = out.setdefault(name, set())
        raw_tools = raw_policy.get("tools")
        raw_tools = dict(raw_tools or {}) if isinstance(raw_tools, Mapping) else {}
        for tool_name, raw_tool_policy in raw_tools.items():
            if not isinstance(raw_tool_policy, Mapping):
                continue
            operation_policies = raw_tool_policy.get("operations")
            if isinstance(operation_policies, Mapping) and operation_policies:
                for operation in operation_policies:
                    value = clean_text(operation)
                    if value:
                        operations.add(value)
                continue
            value = clean_text(raw_tool_policy.get("operation") or tool_name)
            if value:
                operations.add(value)
    return out


def narrow_named_service_config(
    *,
    config: Mapping[str, Any],
    selected: Mapping[str, list[str]],
    grants: Iterable[str],
    resource: str,
) -> dict[str, Any]:
    """Keep only explicitly selected, grant-authorized namespace operations.

    The returned object retains the existing descriptor schema. The named-
    service bridge therefore enforces this selection through its normal
    ``NamedServiceBoundaryCatalog`` path.

    Raises ``ValueError`` when the selection names a namespace or operation the
    descriptor does not configure, or an operation whose declared grants the
    selection does not carry.
    """

    raw_namespaces = config.get("namespaces")
    if not isinstance(raw_namespaces, Mapping):
        if any(selected.values()):
            raise ValueError(
                f"resource {resource!r} does not configure named-service namespaces"
            )
        narrowed = copy.deepcopy(dict(config))
        narrowed["namespaces"] = {}
        return narrowed

    configured_by_name = {
        clean_text(namespace).lower().rstrip(":"): (namespace, raw_policy)
        for namespace, raw_policy in raw_namespaces.items()
        if clean_text(namespace) and isinstance(raw_policy, Mapping)
    }
    unknown_namespaces = sorted(set(selected) - set(configured_by_name))
    if unknown_namespaces:
        raise ValueError(
            f"unknown named-service namespace(s) for {resource!r}: "
            + ", ".join(unknown_namespaces)
        )

    available_grants = set(as_string_list(list(grants)))
    narrowed_namespaces: dict[str, Any] = {}
    for namespace, requested_operations in selected.items():
        requested = set(as_string_list(requested_operations))
        if not requested:
            continue
        raw_namespace, raw_policy = configured_by_name[namespace]
        raw_tools = raw_policy.get("tools")
        raw_tools = dict(raw_tools or {}) if isinstance(raw_tools, Mapping) else {}
        configured_operations: set[str] = set()
        authorized_operations: set[str] = set()
        narrowed_tools: dict[str, Any] = {}

        for tool_name, raw_tool_policy in raw_tools.items():
            if not isinstance(raw_tool_policy, Mapping):
                continue
            tool_policy = dict(raw_tool_policy)
            operation_policies = tool_policy.get("operations")
            if isinstance(operation_policies, Mapping) and operation_policies:
                narrowed_operations: dict[str, Any] = {}
                for operation, raw_operation_policy in operation_policies.items():
                    operation_value = clean_text(operation)
                    if not operation_value:
                        continue
                    configured_operations.add(operation_value)
                    operation_policy = (
                        dict(raw_operation_policy)
                        if isinstance(raw_operation_policy, Mapping)
                        else {}
                    )
                    required = operation_grants(operation_policy, tool_policy)
                    if operation_value in requested and required.issubset(available_grants):
                        narrowed_operations[operation] = copy.deepcopy(raw_operation_policy)
                        authorized_operations.add(operation_value)
                if narrowed_operations:
                    narrowed_tool = copy.deepcopy(tool_policy)
                    narrowed_tool["operations"] = narrowed_operations
                    narrowed_tools[tool_name] = narrowed_tool
                continue

            operation_value = clean_text(tool_policy.get("operation") or tool_name)
            if not operation_value:
                continue
            configured_operations.add(operation_value)
            required = operation_grants(tool_policy, {})
            if operation_value in requested and required.issubset(available_grants):
                narrowed_tools[tool_name] = copy.deepcopy(raw_tool_policy)
                authorized_operations.add(operation_value)

        unknown_operations = sorted(requested - configured_operations)
        if unknown_operations:
            raise ValueError(
                f"unknown named-service operation(s) for {resource!r}/{namespace}: "
                + ", ".join(unknown_operations)
            )
        unauthorized_operations = sorted(requested - authorized_operations)
        if unauthorized_operations:
            raise ValueError(
                f"named-service operation(s) lack selected grants for "
                f"{resource!r}/{namespace}: " + ", ".join(unauthorized_operations)
            )
        if narrowed_tools:
            narrowed_policy = copy.deepcopy(dict(raw_policy))
            narrowed_policy["tools"] = narrowed_tools
            narrowed_namespaces[raw_namespace] = narrowed_policy

    narrowed = copy.deepcopy(dict(config))
    narrowed["namespaces"] = narrowed_namespaces
    return narrowed


def merge_named_service_configs(
    target: dict[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    if not target:
        return copy.deepcopy(dict(source))
    merged = copy.deepcopy(target)
    for key, value in source.items():
        if key == "namespaces" and isinstance(value, Mapping):
            namespaces = merged.setdefault("namespaces", {})
            if isinstance(namespaces, dict):
                namespaces.update(copy.deepcopy(dict(value)))
            continue
        merged.setdefault(key, copy.deepcopy(value))
    return merged


def named_service_policy_for_resource(
    *,
    named_services: Mapping[str, Any] | None,
    resource: str,
    selection: Mapping[str, Any] | None,
    grants: Iterable[str],
) -> dict[str, Any]:
    """The boundary policy one configured resource contributes to a credential.

    ``selection`` is the whole resource -> namespace -> operations map, as the
    create request sends it and the registry card stores it:

        None            -> full descriptor policy
        resource listed -> narrowed to its operations
        resource absent -> narrowed to nothing, an empty map included
    """
    if not isinstance(named_services, Mapping) or not named_services:
        return {}
    if selection is None:
        return copy.deepcopy(dict(named_services))
    return narrow_named_service_config(
        config=named_services,
        selected=dict(selection.get(resource) or {}),
        grants=grants,
        resource=resource,
    )


__all__ = [
    "as_string_list",
    "clean_text",
    "configured_named_service_operations",
    "merge_named_service_configs",
    "named_service_policy_for_resource",
    "narrow_named_service_config",
    "operation_grants",
]
