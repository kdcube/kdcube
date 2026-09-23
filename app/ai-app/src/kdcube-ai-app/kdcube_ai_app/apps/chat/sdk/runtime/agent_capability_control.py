# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Materialize one agent descriptor as live Connection Hub Card authority."""

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
CONTROL_OVERRIDE_SCHEMA = "kdcube.agent_capability_control_override.v1"
CONTROL_OVERRIDES_PROPERTY = "agent_capability_control_overrides"

TOOL_GROUPS = "tool_groups"
TOOLS = "tools"
MCP_SERVERS = "mcp_servers"
MCP_TOOLS = "mcp_tools"
NAMED_SERVICES = "named_services"
NAMED_SERVICE_OPERATIONS = "named_service_operations"
RESOURCES = "resources"
RESOURCE_OPERATIONS = "resource_operations"
SKILLS = "skills"
MODELS = "models"
INSTRUCTION_PROFILES = "instruction_profiles"
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


def _authority_map(value: Any, *, field: str) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"agent_control_override_{field}_invalid")
    result: dict[str, list[str]] = {}
    for resource, raw_values in value.items():
        key = _text(resource)
        values = _strings(raw_values)
        if not key or (raw_values is not None and not isinstance(
            raw_values, (str, list, tuple, set, frozenset)
        )):
            raise ValueError(f"agent_control_override_{field}_invalid")
        result[key] = sorted(set(values))
    return result


def _named_authority(value: Any) -> dict[str, dict[str, list[str]]] | str:
    if value == "*":
        return "*"
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("agent_control_override_named_services_invalid")
    result: dict[str, dict[str, list[str]]] = {}
    for resource, raw_namespaces in value.items():
        if not _text(resource) or not isinstance(raw_namespaces, Mapping):
            raise ValueError("agent_control_override_named_services_invalid")
        result[_text(resource)] = {}
        for namespace, raw_operations in raw_namespaces.items():
            if not _text(namespace) or not isinstance(
                raw_operations, (str, list, tuple, set, frozenset)
            ):
                raise ValueError("agent_control_override_named_services_invalid")
            result[_text(resource)][_text(namespace)] = sorted(
                set(_strings(raw_operations))
            )
    return result


def _agent_control_override(
    bundle_props: Mapping[str, Any] | None,
    *,
    agent_id: str,
    resource: str,
    authority: AgentCapabilityPolicy,
) -> dict[str, Any] | None:
    overrides = (bundle_props or {}).get(CONTROL_OVERRIDES_PROPERTY)
    if not isinstance(overrides, Mapping) or agent_id not in overrides:
        return None
    rows = overrides.get(agent_id)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise ValueError("agent_control_override_invalid")
    raw = rows[0]
    if raw.get("schema") != CONTROL_OVERRIDE_SCHEMA:
        raise ValueError("agent_control_override_schema_mismatch")

    raw_defaults = raw.get("capability_defaults")
    defaults = (
        AgentCapabilityPolicy.from_property(raw_defaults).intersection(authority)
        if raw_defaults is not None
        else AgentCapabilityPolicy.empty(resource)
    )
    if defaults.resource != resource:
        raise ValueError("agent_control_override_resource_mismatch")

    properties = raw.get("properties")
    if properties is not None and not isinstance(properties, Mapping):
        raise ValueError("agent_control_override_properties_invalid")
    safe_properties = {
        name: copy.deepcopy(value)
        for name, value in dict(properties or {}).items()
        if name in {"kdcube.application_operations", "kdcube.conversation_targets"}
    }
    allowed_targets = set(authority.capabilities.get(CONVERSATION_TARGETS, ()))
    if "kdcube.conversation_targets" in safe_properties:
        safe_properties["kdcube.conversation_targets"] = sorted(
            set(_strings(safe_properties["kdcube.conversation_targets"]))
            & allowed_targets
        )
    return {
        "capability_defaults": defaults.to_property(),
        "resource_grants": _authority_map(
            raw.get("resource_grants"), field="resource_grants"
        ),
        "resource_operations": _authority_map(
            raw.get("resource_operations"), field="resource_operations"
        ),
        "named_service_operations": _named_authority(
            raw.get("named_service_operations")
        ),
        "properties": safe_properties,
    }


def _member(parent: str, child: str) -> str:
    return f"{quote(parent, safe='-._~@')}/{quote(child, safe='-._~@')}"


def _split_member(value: str) -> tuple[str, str] | None:
    left, separator, right = str(value or "").partition("/")
    if not separator:
        return None
    return unquote(left), unquote(right)


def _model_member(value: Mapping[str, Any] | None) -> str:
    if not isinstance(value, Mapping):
        return ""
    model = _text(value.get("model"))
    if not model:
        return ""
    return _member(_text(value.get("provider")) or "anthropic", model)


def _default_model_member(catalog: Mapping[str, Any]) -> str:
    configured = catalog.get("default_model")
    if not isinstance(configured, Mapping):
        return ""
    configured_model = _text(configured.get("model"))
    configured_provider = _text(configured.get("provider"))
    if not configured_model:
        return ""
    for row in catalog.get("supported_models") or ():
        if not isinstance(row, Mapping) or _text(row.get("model")) != configured_model:
            continue
        row_provider = _text(row.get("provider")) or "anthropic"
        if configured_provider and configured_provider != row_provider:
            continue
        return _member(row_provider, configured_model)
    return ""


def capability_preferences_from_projection(
    catalog: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve the single model and instruction defaults carried by a Card."""

    selected = _policy_sets(projection)
    selected_models = selected.get(MODELS, set())
    model = None
    for row in catalog.get("supported_models") or ():
        if not isinstance(row, Mapping) or _model_member(row) not in selected_models:
            continue
        model = {
            "provider": _text(row.get("provider")) or "anthropic",
            "model": _text(row.get("model")),
        }
        break

    selected_profiles = selected.get(INSTRUCTION_PROFILES, set())
    instructions = None
    profiles = catalog.get("instruction_profiles")
    if isinstance(profiles, Mapping):
        for row in profiles.get("options") or ():
            if not isinstance(row, Mapping):
                continue
            profile_id = _text(row.get("id"))
            if profile_id and profile_id in selected_profiles:
                instructions = profile_id
                break
    return {"model": model, "instructions": instructions}


def replace_capability_preferences(
    projection: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    replace_model: bool = False,
    model: Any = None,
    replace_instructions: bool = False,
    instructions: Any = None,
) -> dict[str, Any]:
    """Replace the single-choice Card defaults after validating the catalog."""

    policy = AgentCapabilityPolicy.from_property(projection)
    capabilities = {
        category: set(values)
        for category, values in policy.capabilities.items()
    }
    if replace_model:
        wanted = _model_member(model) if isinstance(model, Mapping) else ""
        if model is None:
            wanted = _default_model_member(catalog)
        allowed = {
            capability
            for row in catalog.get("supported_models") or ()
            if isinstance(row, Mapping)
            for capability in (_model_member(row),)
            if capability
        }
        if model is not None and (not wanted or wanted not in allowed):
            raise ValueError("agent_model_not_allowed")
        capabilities[MODELS] = {wanted} if wanted and wanted in allowed else set()
    if replace_instructions:
        wanted = _text(instructions)
        profiles = catalog.get("instruction_profiles")
        if instructions is None and isinstance(profiles, Mapping):
            wanted = _text(profiles.get("default"))
        profile_rows = (
            (profiles.get("options") or ())
            if isinstance(profiles, Mapping)
            else ()
        )
        allowed = {
            _text(row.get("id"))
            for row in profile_rows
            if isinstance(row, Mapping) and _text(row.get("id"))
        }
        if instructions is not None and (not wanted or wanted not in allowed):
            raise ValueError("agent_instruction_profile_not_allowed")
        capabilities[INSTRUCTION_PROFILES] = (
            {wanted} if wanted and wanted in allowed else set()
        )
    return _policy(policy.resource, capabilities)


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


def _operation_covers(declared: str, operation: str) -> bool:
    """Return whether one declared namespace operation covers ``operation``."""

    return operation == declared or operation.startswith(f"{declared}.")


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
        declared = _strings(raw.get("operations"))
        realm_entries = _realm_operation_entries(raw)
        if authority:
            operations = [
                *({"name": value} for value in declared),
                *(
                    entry
                    for entry in realm_entries
                    if any(
                        _operation_covers(value, _text(entry.get("name")))
                        for value in declared
                    )
                ),
            ]
        else:
            operations = [
                *({"name": value} for value in declared),
                *realm_entries,
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

    for raw in catalog.get("supported_models") or ():
        if isinstance(raw, Mapping):
            add(MODELS, _model_member(raw))

    profiles = catalog.get("instruction_profiles")
    if isinstance(profiles, Mapping):
        for raw in profiles.get("options") or ():
            if isinstance(raw, Mapping):
                add(INSTRUCTION_PROFILES, raw.get("id"))

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

    for raw in catalog.get("supported_models") or ():
        if not isinstance(raw, Mapping):
            continue
        capability = _model_member(raw)
        put(
            MODELS,
            capability,
            title=_text(raw.get("label")) or _text(raw.get("model")),
            description=" / ".join(
                value
                for value in (
                    _text(raw.get("provider")) or "anthropic",
                    _text(raw.get("model")),
                )
                if value
            ),
        )

    profiles = catalog.get("instruction_profiles")
    if isinstance(profiles, Mapping):
        for raw in profiles.get("options") or ():
            if not isinstance(raw, Mapping):
                continue
            profile_id = _text(raw.get("id"))
            put(
                INSTRUCTION_PROFILES,
                profile_id,
                title=_text(raw.get("label")) or profile_id,
                description=_text(raw.get("description")),
            )

    for raw in catalog.get("delegated_resource_families") or ():
        if not isinstance(raw, Mapping):
            continue
        family_id = _text(raw.get("id"))
        put(
            RESOURCE_FAMILIES,
            family_id,
            title=_text(raw.get("label")) or family_id,
            description=_text(raw.get("description")),
            resource_kinds=_strings(raw.get("resource_kinds")),
            authority_sources=_strings(raw.get("authority_sources")),
            transports=_strings(raw.get("transports")),
            resource_patterns=_strings(raw.get("resource_patterns")),
            allowed_tools=_strings(raw.get("allowed_tools")),
            max_resources=raw.get("max_resources"),
            max_tools_per_resource=raw.get("max_tools_per_resource"),
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
                nested = raw_tool.get("operations")
                if isinstance(nested, Mapping) and nested:
                    parent_grants = set(_strings(raw_tool.get("grants")))
                    for nested_operation, raw_nested in nested.items():
                        if not isinstance(raw_nested, Mapping):
                            continue
                        operation_grants.setdefault(
                            _text(nested_operation), set()
                        ).update(
                            parent_grants | set(_strings(raw_nested.get("grants")))
                        )
                elif operation:
                    operation_grants.setdefault(operation, set()).update(
                        _strings(raw_tool.get("grants"))
                    )
            matched = {
                candidate
                for candidate in operation_grants
                for operation in wanted
                if _operation_covers(operation, candidate)
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


def _descriptor_standard_authority(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Describe standard Card authority without owning provider catalog data.

    Consumer descriptors know the resource and requested capability names.
    Connection Hub owns the active provider catalog that resolves those names
    to exact grants and operations. Keeping this request in the descriptor
    payload avoids requiring every consumer bundle to copy a provider's
    ``delegated_catalog`` block.
    """

    resources: list[dict[str, Any]] = []
    for raw in catalog.get("mcp") or ():
        if not isinstance(raw, Mapping) or not bool(raw.get("delegated")):
            continue
        resource = _text(raw.get("resource") or raw.get("resource_id"))
        server_id = _text(raw.get("server_id"))
        if not resource or not server_id:
            continue
        operations = _strings(raw.get("tools"))
        if not operations:
            operations = [
                _text(entry.get("name"))
                for entry in raw.get("tool_entries") or ()
                if isinstance(entry, Mapping) and _text(entry.get("name"))
            ]
        resources.append(
            {
                "server_id": server_id,
                "resource": resource,
                "grants": sorted(
                    set(_strings(raw.get("claims") or raw.get("scopes")))
                ),
                "operations": sorted(set(operations)),
            }
        )

    named_services = [
        {
            "namespace": _text(raw.get("namespace")),
            "operations": sorted(set(_strings(raw.get("operations")))),
        }
        for raw in catalog.get("named_services") or ()
        if isinstance(raw, Mapping) and _text(raw.get("namespace"))
    ]
    families = [
        copy.deepcopy(dict(raw))
        for raw in catalog.get("delegated_resource_families") or ()
        if isinstance(raw, Mapping) and _text(raw.get("id"))
    ]
    return {
        "resources": resources,
        "named_services": named_services,
        "resource_families": families,
    }


def _selected_named_service_authority(
    bundle_props: Mapping[str, Any] | None,
    catalog: Mapping[str, Any],
    selected_capabilities: Mapping[str, Any],
) -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    """Project the positive capability choice into ordinary Card authority."""

    policy = AgentCapabilityPolicy.from_property(selected_capabilities)
    selected_namespaces = set(policy.capabilities.get(NAMED_SERVICES, ()))
    selected_operations: dict[str, set[str]] = {}
    for value in policy.capabilities.get(NAMED_SERVICE_OPERATIONS, ()):
        member = _split_member(value)
        if member is not None:
            selected_operations.setdefault(member[0], set()).add(member[1])

    selected_catalog = copy.deepcopy(dict(catalog))
    rows: list[dict[str, Any]] = []
    for raw in catalog.get("named_services") or ():
        if not isinstance(raw, Mapping):
            continue
        namespace = _text(raw.get("namespace"))
        if not namespace or namespace not in selected_namespaces:
            continue
        row = copy.deepcopy(dict(raw))
        row["operations"] = sorted(selected_operations.get(namespace, set()))
        rows.append(row)
    selected_catalog["named_services"] = rows
    return _declared_named_service_authority(bundle_props, selected_catalog)


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
    resource_operations: dict[str, list[str]] = {}
    standard_authority = _descriptor_standard_authority(catalog)
    authority_policy = AgentCapabilityPolicy.from_property(authority)
    override = _agent_control_override(
        bundle_props,
        agent_id=agent_id,
        resource=resource,
        authority=authority_policy,
    )
    capability_defaults = selected_capabilities_from_disabled(
        authority=authority,
        catalog=catalog,
        disabled={},
    )
    control_properties: dict[str, Any] = {}
    if override is not None:
        resource_grants = override["resource_grants"]
        resource_operations = override["resource_operations"]
        named_service_operations = override["named_service_operations"]
        capability_defaults = override["capability_defaults"]
        control_properties = override["properties"]
    descriptor = {
        "schema": DESCRIPTOR_PAYLOAD_SCHEMA,
        "resource": resource,
        "capability_authority": authority,
        "conversation_targets": list(
            authority.get("capabilities", {}).get(CONVERSATION_TARGETS, ())
        ),
        "resource_grants": resource_grants,
        "resource_operations": resource_operations,
        "named_service_operations": named_service_operations,
        "standard_authority": standard_authority,
        "standard_authority_overridden": override is not None,
        "capability_defaults": capability_defaults,
        "properties": control_properties,
    }
    encoded = json.dumps(
        descriptor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    result = {
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
        "resource_operations": resource_operations,
        "named_service_operations": named_service_operations,
        "standard_authority": standard_authority,
        "issuer_label": f"{application} / {agent_id}",
    }
    result["capability_defaults"] = capability_defaults
    if control_properties:
        result["properties"] = control_properties
        targets = control_properties.get("kdcube.conversation_targets")
        if isinstance(targets, list):
            result["conversation_target_resources"] = list(targets)
    return result


def selected_capabilities_from_disabled(
    *,
    authority: Mapping[str, Any],
    catalog: Mapping[str, Any],
    disabled: Mapping[str, Any] | None,
    existing_selection: Mapping[str, Any] | None = None,
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

    default_model = _default_model_member(catalog)
    selected[MODELS] = {default_model} if default_model in selected.get(MODELS, set()) else set()

    profiles = catalog.get("instruction_profiles")
    default_profile = _text(profiles.get("default")) if isinstance(profiles, Mapping) else ""
    selected[INSTRUCTION_PROFILES] = (
        {default_profile}
        if default_profile in selected.get(INSTRUCTION_PROFILES, set())
        else set()
    )
    if existing_selection is not None:
        existing = _policy_sets(existing_selection)
        for category in (MODELS, INSTRUCTION_PROFILES):
            selected[category] = (
                existing.get(category, set())
                & set(policy.capabilities.get(category, ()))
                if category in policy.capabilities
                else set()
            )

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
    for row in result.get("supported_models") or ():
        if isinstance(row, dict):
            row["authority_state"] = state(MODELS, _model_member(row))
    profiles = result.get("instruction_profiles")
    if isinstance(profiles, dict):
        for row in profiles.get("options") or ():
            if isinstance(row, dict):
                row["authority_state"] = state(
                    INSTRUCTION_PROFILES,
                    _text(row.get("id")),
                )
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
    descriptor_default = selected_capabilities is None
    override_active = bool(
        payload.get("descriptor_payload", {}).get(
            "standard_authority_overridden"
        )
    )
    if selected_capabilities is None:
        selected_capabilities = (
            copy.deepcopy(payload["capability_defaults"])
            if override_active
            else
            selected_capabilities_from_disabled(
                authority=payload["capability_authority"],
                catalog=catalog,
                disabled=initial_disabled or {},
            )
        )
    if selected_capabilities is not None:
        payload["selected_capabilities"] = copy.deepcopy(dict(selected_capabilities))
        if override_active and descriptor_default:
            payload["selected_resource_grants"] = copy.deepcopy(
                payload.get("resource_grants") or {}
            )
            payload["selected_resource_operations"] = copy.deepcopy(
                payload.get("resource_operations") or {}
            )
            payload["selected_named_service_operations"] = copy.deepcopy(
                payload.get("named_service_operations") or {}
            )
        elif not override_active:
            (
                selected_resource_grants,
                selected_named_service_operations,
            ) = _selected_named_service_authority(
                getattr(entrypoint, "bundle_props", None),
                catalog,
                selected_capabilities,
            )
            payload["selected_resource_grants"] = selected_resource_grants
            payload["selected_named_service_operations"] = (
                selected_named_service_operations
            )
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
    "capability_preferences_from_projection",
    "agent_capability_identity",
    "annotate_capability_states",
    "conversation_capability_projections",
    "deny_all_capabilities",
    "descriptor_capability_payload",
    "disabled_from_projection",
    "intersect_capability_projections",
    "missing_control_capabilities",
    "replace_capability_preferences",
    "selected_capabilities_from_disabled",
    "sync_agent_capability_projection",
    "unavailable_capability_states",
]
