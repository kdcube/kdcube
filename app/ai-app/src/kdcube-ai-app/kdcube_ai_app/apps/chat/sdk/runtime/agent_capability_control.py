# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Project one agent descriptor into its live Connection Hub Card boundary."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable, Mapping
from urllib.parse import quote, unquote

from connection_hub.delegated_credentials.agent_capability_policy import (
    AGENT_CAPABILITY_METADATA_SCHEMA,
    CAPABILITY_NOT_ALLOWED,
    AgentCapabilityPolicy,
)
from connection_hub.delegated_credentials.application_resources import (
    ApplicationResource,
    application_resource,
)


DESCRIPTOR_PAYLOAD_SCHEMA = "kdcube.agent_capability_descriptor.v1"

TOOL_GROUPS = "tool_groups"
TOOLS = "tools"
MCP_SERVERS = "mcp_servers"
MCP_TOOLS = "mcp_tools"
NAMED_SERVICES = "named_services"
NAMED_SERVICE_OPERATIONS = "named_service_operations"
RESOURCES = "resources"
RESOURCE_OPERATIONS = "resource_operations"
SKILLS = "skills"
CONVERSATION_TARGETS = "conversation_targets"
RESOURCE_FAMILIES = "resource_families"
SUBAGENTS = "subagents"


class AgentCapabilityControlUnavailable(RuntimeError):
    """The current descriptor/Card projection could not be obtained."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def agent_card_revision(capability_control: Mapping[str, Any]) -> int:
    """Return the revision of the resident Agent Card in a sync result."""

    card = capability_control.get("card")
    if not isinstance(card, Mapping):
        return 0
    try:
        return max(0, int(card.get("card_revision") or 0))
    except (TypeError, ValueError):
        return 0


def agent_capability_identity(entrypoint: Any) -> dict[str, str]:
    """Resolve the request identity used by the resident-agent Card boundary."""

    resolver = getattr(entrypoint, "_agent_selection_identity", None)
    if callable(resolver):
        return {
            str(key): _text(value)
            for key, value in dict(resolver() or {}).items()
        }
    runtime_ctx = getattr(entrypoint, "runtime_ctx", None)
    config = getattr(entrypoint, "config", None)
    return {
        "tenant": _text(getattr(runtime_ctx, "tenant", "")),
        "project": _text(getattr(runtime_ctx, "project", "")),
        "user_id": _text(getattr(runtime_ctx, "user_id", "")),
        "bundle_id": _text(
            getattr(runtime_ctx, "bundle_id", "")
            or getattr(getattr(config, "ai_bundle_spec", None), "id", "")
        ),
    }


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _member(parent: str, child: str) -> str:
    return f"{quote(parent, safe='-._~@')}/{quote(child, safe='-._~@')}"


def _split_member(value: str) -> tuple[str, str] | None:
    left, separator, right = str(value or "").partition("/")
    if not separator:
        return None
    return unquote(left), unquote(right)


def _target_resource(
    row: Mapping[str, Any],
    *,
    tenant: str,
    project: str,
) -> str:
    declared = _text(row.get("resource"))
    if declared:
        parsed = ApplicationResource.parse(declared)
        if parsed.tenant != tenant or parsed.project != project:
            raise ValueError("agent_conversation_target_deployment_mismatch")
        return parsed.resource
    bundle_id = _text(row.get("bundle_id"))
    if not bundle_id:
        return ""
    return application_resource(
        tenant=tenant,
        project=project,
        application=bundle_id,
        agent="*",
    )


def _policy(resource: str, capabilities: Mapping[str, Iterable[str]]) -> dict[str, Any]:
    return AgentCapabilityPolicy(
        resource=resource,
        capabilities={
            category: tuple(values)
            for category, values in capabilities.items()
        },
    ).to_property()


def _policy_sets(value: Mapping[str, Any] | None) -> dict[str, set[str]]:
    policy = AgentCapabilityPolicy.from_property(value or {})
    return {
        category: set(values)
        for category, values in policy.capabilities.items()
    }


def intersect_capability_projections(
    *projections: Mapping[str, Any],
) -> dict[str, Any]:
    """Intersect finite positive projections for one exact agent resource."""

    if not projections:
        raise ValueError("agent_capability_projection_required")
    effective = AgentCapabilityPolicy.from_property(projections[0])
    for projection in projections[1:]:
        effective = effective.intersection(
            AgentCapabilityPolicy.from_property(projection)
        )
    return effective.to_property()


def conversation_capability_projections(
    control_authority: Mapping[str, Any],
    agent_default_projection: Mapping[str, Any],
    conversation_selection: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the inherited value and the effective current selection.

    The descriptor-derived Control Card is the sole ceiling. The Agent Card is
    the default for a new conversation, and the stored base records that
    inherited value for provenance. A conversation may select any capability
    still allowed by the current Control Card; Control removals apply live and
    Control additions remain off until the user selects them.
    """

    if not isinstance(conversation_selection, Mapping):
        inherited = copy.deepcopy(dict(agent_default_projection))
        return inherited, intersect_capability_projections(
            control_authority,
            inherited,
        )
    base_projection = conversation_selection.get("base_projection")
    selected_projection = conversation_selection.get("projection")
    if not isinstance(base_projection, Mapping) or not isinstance(
        selected_projection,
        Mapping,
    ):
        raise ValueError("conversation_capability_projection_invalid")
    effective = intersect_capability_projections(
        control_authority,
        selected_projection,
    )
    return copy.deepcopy(dict(base_projection)), effective


def missing_control_capabilities(
    capability_control: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Name saved Agent Card choices absent from the current Control Card."""

    authority_raw = capability_control.get("authority")
    selection_raw = capability_control.get("selection")
    if not isinstance(authority_raw, Mapping) or not isinstance(
        selection_raw,
        Mapping,
    ):
        return []
    authority = AgentCapabilityPolicy.from_property(authority_raw)
    selection = AgentCapabilityPolicy.from_property(selection_raw)
    missing = selection.subtract(authority)
    return [
        {
            "category": category,
            "capability": capability,
            "reason": "missing_from_control_card",
        }
        for category, capabilities in sorted(missing.capabilities.items())
        for capability in sorted(capabilities)
    ]


def _realm_operation_entries(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    realm = row.get("realm")
    if not isinstance(realm, Mapping):
        return []
    entries: list[dict[str, Any]] = []
    for raw in realm.get("operations") or ():
        if isinstance(raw, Mapping) and _text(raw.get("name")):
            entries.append(dict(raw))
    for raw in realm.get("actions") or ():
        if not isinstance(raw, Mapping) or not _text(raw.get("name")):
            continue
        entries.append({**dict(raw), "name": f"object.action.{_text(raw.get('name'))}"})
    return entries


def _capability_inventory(
    catalog: Mapping[str, Any],
    *,
    tenant: str,
    project: str,
    authority: bool,
) -> dict[str, set[str]]:
    capabilities: dict[str, set[str]] = {}

    def add(category: str, value: Any) -> None:
        text = _text(value)
        if text:
            capabilities.setdefault(category, set()).add(text)

    for raw in catalog.get("tools") or ():
        if not isinstance(raw, Mapping) or bool(raw.get("system")):
            continue
        alias = _text(raw.get("alias"))
        if not alias:
            continue
        add(TOOL_GROUPS, alias)
        for tool in raw.get("tools") or ():
            if isinstance(tool, Mapping):
                name = _text(tool.get("name"))
                if name:
                    add(TOOLS, _member(alias, name))

    for raw in catalog.get("mcp") or ():
        if not isinstance(raw, Mapping):
            continue
        server_id = _text(raw.get("server_id"))
        if not server_id:
            continue
        add(MCP_SERVERS, server_id)
        entries = raw.get("tool_entries") or ()
        if not entries and "*" not in _strings(raw.get("tools")):
            entries = ({"name": name} for name in _strings(raw.get("tools")))
        for tool in entries:
            if isinstance(tool, Mapping):
                name = _text(tool.get("name"))
                if name:
                    add(MCP_TOOLS, _member(server_id, name))

    for raw in catalog.get("named_services") or ():
        if not isinstance(raw, Mapping):
            continue
        namespace = _text(raw.get("namespace"))
        if not namespace:
            continue
        add(NAMED_SERVICES, namespace)
        if authority:
            operations = ({"name": value} for value in _strings(raw.get("operations")))
        else:
            operations = [
                *({"name": value} for value in _strings(raw.get("operations"))),
                *_realm_operation_entries(raw),
            ]
        for operation in operations:
            if not isinstance(operation, Mapping):
                continue
            name = _text(operation.get("name"))
            if not name:
                continue
            if authority or operation.get("enabled_for_agent") is not False:
                add(NAMED_SERVICE_OPERATIONS, _member(namespace, name))
            elif not authority:
                add(NAMED_SERVICE_OPERATIONS, _member(namespace, name))

    for raw in catalog.get("resources") or ():
        if not isinstance(raw, Mapping):
            continue
        resource_id = _text(raw.get("resource_id"))
        if not resource_id:
            continue
        add(RESOURCES, resource_id)
        for tool in raw.get("tools") or ():
            if not isinstance(tool, Mapping):
                continue
            operation = _text(tool.get("operation") or tool.get("name"))
            if operation:
                add(RESOURCE_OPERATIONS, _member(resource_id, operation))

    for raw in catalog.get("skills") or ():
        if isinstance(raw, Mapping):
            add(SKILLS, raw.get("id"))

    for raw in catalog.get("conversation_targets") or ():
        if not isinstance(raw, Mapping):
            continue
        add(
            CONVERSATION_TARGETS,
            _target_resource(raw, tenant=tenant, project=project),
        )

    for raw in catalog.get("delegated_resource_families") or ():
        if isinstance(raw, Mapping):
            add(RESOURCE_FAMILIES, raw.get("id"))

    subagents = catalog.get("subagents")
    if isinstance(subagents, Mapping) and subagents.get("available"):
        add(SUBAGENTS, "enabled")

    return capabilities


def _metadata_entries(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}

    def put(category: str, capability: str, **metadata: Any) -> None:
        if not capability:
            return
        clean = {
            key: copy.deepcopy(value)
            for key, value in metadata.items()
            if value not in (None, "", [], {})
        }
        if clean:
            entries.setdefault(category, {})[capability] = clean

    for raw in catalog.get("tools") or ():
        if not isinstance(raw, Mapping) or bool(raw.get("system")):
            continue
        alias = _text(raw.get("alias"))
        put(TOOL_GROUPS, alias, title=_text(raw.get("name")) or alias)
        for tool in raw.get("tools") or ():
            if isinstance(tool, Mapping):
                name = _text(tool.get("name"))
                put(
                    TOOLS,
                    _member(alias, name) if alias and name else "",
                    title=name,
                    description=_text(tool.get("description")),
                )

    for raw in catalog.get("mcp") or ():
        if not isinstance(raw, Mapping):
            continue
        server_id = _text(raw.get("server_id"))
        put(MCP_SERVERS, server_id, title=_text(raw.get("name")) or server_id)
        for tool in raw.get("tool_entries") or ():
            if isinstance(tool, Mapping):
                name = _text(tool.get("name"))
                put(
                    MCP_TOOLS,
                    _member(server_id, name) if server_id and name else "",
                    title=name,
                    description=_text(tool.get("description")),
                )

    for raw in catalog.get("named_services") or ():
        if not isinstance(raw, Mapping):
            continue
        namespace = _text(raw.get("namespace"))
        realm = raw.get("realm") if isinstance(raw.get("realm"), Mapping) else {}
        put(
            NAMED_SERVICES,
            namespace,
            title=_text(realm.get("label")) or namespace,
            description=_text(realm.get("description")),
        )
        operation_entries = {
            _text(entry.get("name")): entry
            for entry in _realm_operation_entries(raw)
            if _text(entry.get("name"))
        }
        for operation in _strings(raw.get("operations")):
            operation_entries.setdefault(operation, {"name": operation})
        for operation, entry in operation_entries.items():
            put(
                NAMED_SERVICE_OPERATIONS,
                _member(namespace, operation),
                title=_text(entry.get("label")) or operation,
                description=_text(entry.get("description")),
            )

    return entries


def _declared_named_service_authority(
    bundle_props: Mapping[str, Any] | None,
    catalog: Mapping[str, Any],
) -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    selected = {
        _text(row.get("namespace")): set(_strings(row.get("operations")))
        for row in catalog.get("named_services") or ()
        if isinstance(row, Mapping) and _text(row.get("namespace"))
    }
    grants_by_resource: dict[str, set[str]] = {}
    operations_by_resource: dict[str, dict[str, set[str]]] = {}
    declaration = (bundle_props or {}).get("delegated_catalog")
    if not isinstance(declaration, Mapping):
        return {}, {}

    for extension in declaration.get("named_service_namespaces") or ():
        if not isinstance(extension, Mapping):
            continue
        resource = _text(extension.get("resource"))
        namespaces = extension.get("namespaces")
        if not resource or not isinstance(namespaces, Mapping):
            continue
        for namespace, raw_namespace in namespaces.items():
            namespace = _text(namespace)
            wanted = selected.get(namespace) or set()
            if not wanted or not isinstance(raw_namespace, Mapping):
                continue
            tools = raw_namespace.get("tools")
            if not isinstance(tools, Mapping):
                continue
            operation_grants: dict[str, set[str]] = {}
            for raw_tool in tools.values():
                if not isinstance(raw_tool, Mapping):
                    continue
                operation = _text(raw_tool.get("operation"))
                if operation:
                    operation_grants.setdefault(operation, set()).update(
                        _strings(raw_tool.get("grants"))
                    )
                nested = raw_tool.get("operations")
                if isinstance(nested, Mapping):
                    for nested_operation, raw_nested in nested.items():
                        if not isinstance(raw_nested, Mapping):
                            continue
                        operation_grants.setdefault(
                            _text(nested_operation), set()
                        ).update(_strings(raw_nested.get("grants")))
            matched = {
                operation
                for operation in wanted
                if operation in operation_grants
            }
            if not matched:
                continue
            grants = grants_by_resource.setdefault(resource, set())
            namespace_operations = operations_by_resource.setdefault(
                resource, {}
            ).setdefault(namespace, set())
            for operation in matched:
                grants.update(operation_grants[operation])
                namespace_operations.add(operation)

    return (
        {resource: sorted(values) for resource, values in grants_by_resource.items()},
        {
            resource: {
                namespace: sorted(values)
                for namespace, values in namespaces.items()
            }
            for resource, namespaces in operations_by_resource.items()
        },
    )


def descriptor_capability_payload(
    *,
    bundle_props: Mapping[str, Any] | None,
    catalog: Mapping[str, Any],
    tenant: str,
    project: str,
    application: str,
    agent_id: str,
) -> dict[str, Any]:
    """Build stable descriptor authority, separate metadata, and sync payload."""

    resource = application_resource(
        tenant=tenant,
        project=project,
        application=application,
        agent=agent_id,
    )
    authority = _policy(
        resource,
        _capability_inventory(
            catalog,
            tenant=tenant,
            project=project,
            authority=True,
        ),
    )
    catalog_policy = _policy(
        resource,
        _capability_inventory(
            catalog,
            tenant=tenant,
            project=project,
            authority=False,
        ),
    )
    resource_grants, named_service_operations = _declared_named_service_authority(
        bundle_props,
        catalog,
    )
    descriptor = {
        "schema": DESCRIPTOR_PAYLOAD_SCHEMA,
        "resource": resource,
        "capability_authority": authority,
        "conversation_targets": list(
            authority.get("capabilities", {}).get(CONVERSATION_TARGETS, ())
        ),
        "resource_grants": resource_grants,
        "named_service_operations": named_service_operations,
    }
    encoded = json.dumps(
        descriptor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "application": application,
        "agent_id": agent_id,
        "descriptor_revision": hashlib.sha256(encoded).hexdigest(),
        "descriptor_payload": descriptor,
        "capability_authority": authority,
        "capability_catalog": catalog_policy,
        "capability_metadata": {
            "schema": AGENT_CAPABILITY_METADATA_SCHEMA,
            "resource": resource,
            "entries": _metadata_entries(catalog),
        },
        "conversation_target_resources": list(
            authority.get("capabilities", {}).get(CONVERSATION_TARGETS, ())
        ),
        "resource_grants": resource_grants,
        "named_service_operations": named_service_operations,
        "issuer_label": f"{application} / {agent_id}",
    }


def selected_capabilities_from_disabled(
    *,
    authority: Mapping[str, Any],
    catalog: Mapping[str, Any],
    disabled: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Convert the existing deny-map wire shape into a positive Card choice."""

    policy = AgentCapabilityPolicy.from_property(authority)
    selected = {
        category: set(values)
        for category, values in policy.capabilities.items()
    }
    disabled = disabled if isinstance(disabled, Mapping) else {}

    grouped = (
        ("tools", TOOL_GROUPS, TOOLS),
        ("mcp", MCP_SERVERS, MCP_TOOLS),
        ("named_services", NAMED_SERVICES, NAMED_SERVICE_OPERATIONS),
        ("resources", RESOURCES, RESOURCE_OPERATIONS),
    )
    for disabled_key, group_category, item_category in grouped:
        raw = disabled.get(disabled_key)
        if not isinstance(raw, Mapping):
            continue
        for group, value in raw.items():
            group = _text(group)
            if not group:
                continue
            if value is True:
                selected.get(group_category, set()).discard(group)
                selected[item_category] = {
                    item
                    for item in selected.get(item_category, set())
                    if (_split_member(item) or ("", ""))[0] != group
                }
                continue
            denied_items = set(_strings(value))
            selected[item_category] = {
                item
                for item in selected.get(item_category, set())
                if not (
                    (_split_member(item) or ("", ""))[0] == group
                    and (_split_member(item) or ("", ""))[1] in denied_items
                )
            }

    denied_skills = set(_strings(disabled.get("skills")))
    selected[SKILLS] = selected.get(SKILLS, set()) - denied_skills

    denied_targets = {
        _text(target)
        for target, value in dict(disabled.get("conversation_targets") or {}).items()
        if value is True
    } if isinstance(disabled.get("conversation_targets"), Mapping) else set()
    selected[CONVERSATION_TARGETS] = {
        target
        for target in selected.get(CONVERSATION_TARGETS, set())
        if target not in denied_targets
        and ApplicationResource.parse(target).application not in denied_targets
    }

    subagents = catalog.get("subagents")
    default_on = not isinstance(subagents, Mapping) or subagents.get("default_on") is not False
    explicitly_disabled = disabled.get("subagents") if "subagents" in disabled else None
    if explicitly_disabled is True or (explicitly_disabled is None and not default_on):
        selected.get(SUBAGENTS, set()).discard("enabled")

    return _policy(policy.resource, selected)


def disabled_from_projection(
    catalog: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate a positive effective Card projection for existing runtimes."""

    selected = _policy_sets(projection)
    disabled: dict[str, Any] = {}

    def grouped(
        catalog_key: str,
        identity_key: str,
        item_key: str,
        group_category: str,
        item_category: str,
        *,
        system_aware: bool = False,
    ) -> None:
        denied_groups: dict[str, Any] = {}
        for raw in catalog.get(catalog_key) or ():
            if not isinstance(raw, Mapping):
                continue
            if system_aware and bool(raw.get("system")):
                continue
            group = _text(raw.get(identity_key))
            if not group:
                continue
            if group not in selected.get(group_category, set()):
                denied_groups[group] = True
                continue
            denied_items: list[str] = []
            entries = raw.get(item_key) or ()
            if catalog_key == "mcp" and item_key == "tool_entries" and not entries:
                entries = ({"name": name} for name in _strings(raw.get("tools")) if name != "*")
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                name = _text(entry.get("operation") or entry.get("name"))
                if name and _member(group, name) not in selected.get(item_category, set()):
                    denied_items.append(name)
            if denied_items:
                denied_groups[group] = sorted(set(denied_items))
        if denied_groups:
            disabled[catalog_key if catalog_key != "resources" else "resources"] = denied_groups

    grouped("tools", "alias", "tools", TOOL_GROUPS, TOOLS, system_aware=True)
    grouped("mcp", "server_id", "tool_entries", MCP_SERVERS, MCP_TOOLS)

    denied_namespaces: dict[str, Any] = {}
    for raw in catalog.get("named_services") or ():
        if not isinstance(raw, Mapping):
            continue
        namespace = _text(raw.get("namespace"))
        if not namespace:
            continue
        if namespace not in selected.get(NAMED_SERVICES, set()):
            denied_namespaces[namespace] = True
            continue
        operations = set(_strings(raw.get("operations")))
        operations.update(
            _text(entry.get("name"))
            for entry in _realm_operation_entries(raw)
            if _text(entry.get("name"))
        )
        denied = sorted(
            operation
            for operation in operations
            if _member(namespace, operation)
            not in selected.get(NAMED_SERVICE_OPERATIONS, set())
        )
        if denied:
            denied_namespaces[namespace] = denied
    if denied_namespaces:
        disabled["named_services"] = denied_namespaces

    grouped("resources", "resource_id", "tools", RESOURCES, RESOURCE_OPERATIONS)

    skill_ids = {
        _text(raw.get("id"))
        for raw in catalog.get("skills") or ()
        if isinstance(raw, Mapping) and _text(raw.get("id"))
    }
    denied_skills = sorted(skill_ids - selected.get(SKILLS, set()))
    if denied_skills:
        disabled["skills"] = denied_skills

    denied_targets: dict[str, bool] = {}
    for raw in catalog.get("conversation_targets") or ():
        if not isinstance(raw, Mapping):
            continue
        bundle_id = _text(raw.get("bundle_id"))
        declared_resource = _text(raw.get("resource"))
        target = next(
            (
                value
                for value in selected.get(CONVERSATION_TARGETS, set())
                if (
                    (declared_resource and value == declared_resource)
                    or (
                        bundle_id
                        and ApplicationResource.parse(value).application == bundle_id
                    )
                )
            ),
            "",
        )
        target_key = declared_resource or bundle_id
        if target_key and not target:
            denied_targets[target_key] = True
    if denied_targets:
        disabled["conversation_targets"] = denied_targets

    families = {
        _text(raw.get("id"))
        for raw in catalog.get("delegated_resource_families") or ()
        if isinstance(raw, Mapping) and _text(raw.get("id"))
    }
    denied_families = families - selected.get(RESOURCE_FAMILIES, set())
    if denied_families:
        disabled["delegated_resource_families"] = sorted(denied_families)

    subagents = catalog.get("subagents")
    if isinstance(subagents, Mapping) and subagents.get("available"):
        selected_subagents = "enabled" in selected.get(SUBAGENTS, set())
        default_on = subagents.get("default_on") is not False
        if not selected_subagents:
            disabled["subagents"] = True
        elif not default_on:
            disabled["subagents"] = False
    return disabled


def deny_all_capabilities(
    *,
    catalog: Mapping[str, Any],
    tenant: str,
    project: str,
    application: str,
    agent_id: str,
) -> dict[str, Any]:
    resource = application_resource(
        tenant=tenant,
        project=project,
        application=application,
        agent=agent_id,
    )
    return disabled_from_projection(catalog, _policy(resource, {}))


def unavailable_capability_states(
    catalog: Mapping[str, Any],
    *,
    tenant: str,
    project: str,
) -> dict[str, dict[str, str]]:
    """Mark every descriptor capability unavailable when Card lookup fails."""

    return {
        category: {
            capability: CAPABILITY_NOT_ALLOWED
            for capability in sorted(capabilities)
        }
        for category, capabilities in _capability_inventory(
            catalog,
            tenant=tenant,
            project=project,
            authority=False,
        ).items()
    }


def annotate_capability_states(
    catalog: Mapping[str, Any],
    states: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attach the three-state Card decision to the existing picker rows."""

    result = copy.deepcopy(dict(catalog or {}))
    states = states if isinstance(states, Mapping) else {}

    def state(category: str, capability: str) -> str:
        category_states = states.get(category)
        return (
            _text(category_states.get(capability))
            if isinstance(category_states, Mapping)
            else ""
        )

    for row in result.get("tools") or ():
        if not isinstance(row, dict) or row.get("system"):
            continue
        alias = _text(row.get("alias"))
        row["authority_state"] = state(TOOL_GROUPS, alias)
        for tool in row.get("tools") or ():
            if isinstance(tool, dict):
                tool["authority_state"] = state(
                    TOOLS, _member(alias, _text(tool.get("name")))
                )
    for row in result.get("mcp") or ():
        if not isinstance(row, dict):
            continue
        server_id = _text(row.get("server_id"))
        row["authority_state"] = state(MCP_SERVERS, server_id)
        for tool in row.get("tool_entries") or ():
            if isinstance(tool, dict):
                tool["authority_state"] = state(
                    MCP_TOOLS, _member(server_id, _text(tool.get("name")))
                )
    for row in result.get("named_services") or ():
        if not isinstance(row, dict):
            continue
        namespace = _text(row.get("namespace"))
        row["authority_state"] = state(NAMED_SERVICES, namespace)
        operation_states = {
            operation: state(
                NAMED_SERVICE_OPERATIONS,
                _member(namespace, operation),
            )
            for operation in _strings(row.get("operations"))
        }
        realm = row.get("realm")
        if isinstance(realm, dict):
            for operation in realm.get("operations") or ():
                if not isinstance(operation, dict):
                    continue
                name = _text(operation.get("name"))
                operation["authority_state"] = state(
                    NAMED_SERVICE_OPERATIONS,
                    _member(namespace, name),
                )
                if name:
                    operation_states[name] = operation["authority_state"]
            for action in realm.get("actions") or ():
                if not isinstance(action, dict):
                    continue
                name = _text(action.get("name"))
                operation = f"object.action.{name}" if name else ""
                action["authority_state"] = state(
                    NAMED_SERVICE_OPERATIONS,
                    _member(namespace, operation),
                )
                if operation:
                    operation_states[operation] = action["authority_state"]
        row["operation_authority_states"] = operation_states
    for row in result.get("resources") or ():
        if not isinstance(row, dict):
            continue
        resource_id = _text(row.get("resource_id"))
        row["authority_state"] = state(RESOURCES, resource_id)
        for tool in row.get("tools") or ():
            if not isinstance(tool, dict):
                continue
            operation = _text(tool.get("operation") or tool.get("name"))
            tool["authority_state"] = state(
                RESOURCE_OPERATIONS,
                _member(resource_id, operation),
            )
    for row in result.get("skills") or ():
        if isinstance(row, dict):
            row["authority_state"] = state(SKILLS, _text(row.get("id")))
    for row in result.get("conversation_targets") or ():
        if not isinstance(row, dict):
            continue
        bundle_id = _text(row.get("bundle_id"))
        declared_resource = _text(row.get("resource"))
        candidates = states.get(CONVERSATION_TARGETS)
        if isinstance(candidates, Mapping):
            row["authority_state"] = next(
                (
                    _text(value)
                    for resource, value in candidates.items()
                    if (
                        (declared_resource and resource == declared_resource)
                        or (
                            bundle_id
                            and ApplicationResource.parse(resource).application == bundle_id
                        )
                    )
                ),
                "",
            )
    for row in result.get("delegated_resource_families") or ():
        if isinstance(row, dict):
            row["authority_state"] = state(
                RESOURCE_FAMILIES,
                _text(row.get("id")),
            )
    subagents = result.get("subagents")
    if isinstance(subagents, dict) and subagents.get("available"):
        subagents["authority_state"] = state(SUBAGENTS, "enabled")
    result["capability_states"] = copy.deepcopy(dict(states))
    return result


async def sync_agent_capability_projection(
    entrypoint: Any,
    *,
    catalog: Mapping[str, Any],
    agent_id: str,
    initial_disabled: Mapping[str, Any] | None = None,
    selected_capabilities: Mapping[str, Any] | None = None,
    replace_selection: bool = False,
) -> dict[str, Any]:
    """Synchronize the descriptor Control and read the effective live projection."""

    identity = agent_capability_identity(entrypoint)
    tenant = _text(identity.get("tenant"))
    project = _text(identity.get("project"))
    application = _text(identity.get("bundle_id"))
    if not tenant or not project or not application or not agent_id:
        raise AgentCapabilityControlUnavailable("agent_capability_identity_missing")

    payload = descriptor_capability_payload(
        bundle_props=getattr(entrypoint, "bundle_props", None),
        catalog=catalog,
        tenant=tenant,
        project=project,
        application=application,
        agent_id=agent_id,
    )
    if selected_capabilities is None:
        selected_capabilities = selected_capabilities_from_disabled(
            authority=payload["capability_authority"],
            catalog=catalog,
            disabled=initial_disabled or {},
        )
    if selected_capabilities is not None:
        payload["selected_capabilities"] = copy.deepcopy(dict(selected_capabilities))
    if replace_selection:
        payload["replace_selection"] = True

    from connection_hub.contract import AGENT_CAPABILITY_SYNC, NAMESPACE
    from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
        call_bundle_named_service,
    )
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connection_edges import (
        connection_hub_bundle_id_from_entrypoint,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
        NamedServiceResponse,
    )

    result = await call_bundle_named_service(
        bundle_id=connection_hub_bundle_id_from_entrypoint(entrypoint),
        request={
            "namespace": NAMESPACE,
            "operation": AGENT_CAPABILITY_SYNC,
            "payload": payload,
        },
        tenant=tenant,
        project=project,
    )
    response = NamedServiceResponse.coerce(result.value)
    if not response.ok:
        code = _text(getattr(response.error, "code", "")) or "agent_capability_sync_failed"
        raise AgentCapabilityControlUnavailable(code)
    projection = response.object
    if not isinstance(projection.get("projection"), Mapping):
        raise AgentCapabilityControlUnavailable("agent_capability_projection_missing")
    return projection


__all__ = [
    "AgentCapabilityControlUnavailable",
    "agent_card_revision",
    "agent_capability_identity",
    "annotate_capability_states",
    "conversation_capability_projections",
    "deny_all_capabilities",
    "descriptor_capability_payload",
    "disabled_from_projection",
    "intersect_capability_projections",
    "missing_control_capabilities",
    "selected_capabilities_from_disabled",
    "sync_agent_capability_projection",
    "unavailable_capability_states",
]
