# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.event_identity import normalize_agent_id


NAMED_SERVICE_TOOLS_MODULE = "kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.tools"
NAMED_SERVICE_TOOLS_ALIAS = "named_services"
DEFAULT_AGENT_TOOLS_ID = "default_agent"


def _get_path(data: Mapping[str, Any] | None, path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _safe_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_")


def client_config_keys(client_id: Any) -> list[str]:
    normalized = normalize_agent_id(client_id)
    keys: list[str] = []
    for key in (normalized, _safe_key(normalized)):
        if key and key not in keys:
            keys.append(key)
    for key in ("default_client", "default"):
        if key not in keys:
            keys.append(key)
    return keys


def named_services_config(bundle_props: Mapping[str, Any] | None) -> Mapping[str, Any]:
    raw = _get_path(bundle_props or {}, "named_services", {})
    return raw if isinstance(raw, Mapping) else {}


def _as_consumer_config(bundle_props: Mapping[str, Any] | None) -> Mapping[str, Any]:
    raw = _get_path(bundle_props or {}, "surfaces.as_consumer", {})
    return raw if isinstance(raw, Mapping) else {}


def _as_consumer_agents(bundle_props: Mapping[str, Any] | None) -> Mapping[str, Any]:
    raw = _get_path(_as_consumer_config(bundle_props), "agents", {})
    return raw if isinstance(raw, Mapping) else {}


def _agent_config_keys(client_id: Any) -> list[str]:
    keys: list[str] = []
    raw = str(client_id or "").strip()
    if raw:
        keys.append(raw)
    normalized = normalize_agent_id(client_id)
    if normalized and normalized not in keys:
        keys.append(normalized)
    safe = _safe_key(normalized)
    if safe and safe not in keys:
        keys.append(safe)
    if DEFAULT_AGENT_TOOLS_ID not in keys:
        keys.append(DEFAULT_AGENT_TOOLS_ID)
    if "main" not in keys:
        keys.append("main")
    if "default" not in keys:
        keys.append("default")
    return keys


def _as_consumer_agent_config(
    bundle_props: Mapping[str, Any] | None,
    *,
    client_id: Any,
) -> Mapping[str, Any]:
    agents = _as_consumer_agents(bundle_props)
    for key in _agent_config_keys(client_id):
        raw = agents.get(key)
        if isinstance(raw, Mapping):
            return raw
    return {}


def _agent_tool_connections_from_surfaces(
    bundle_props: Mapping[str, Any] | None,
    *,
    client_id: Any,
) -> list[Mapping[str, Any]]:
    raw = _as_consumer_agent_config(bundle_props, client_id=client_id).get("tools")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _agent_event_sources_from_surfaces(
    bundle_props: Mapping[str, Any] | None,
    *,
    client_id: Any,
) -> list[Mapping[str, Any]]:
    raw = _as_consumer_agent_config(bundle_props, client_id=client_id).get("event_sources")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _ui_canvas_resolvers_from_surfaces(bundle_props: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    raw = _get_path(_as_consumer_config(bundle_props), "ui.canvas.resolvers", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _namespace_from_value(value: Any) -> str:
    return str(value or "").strip().lower().rstrip(":")


def _merge_namespace_config(
    target: dict[str, dict[str, Any]],
    namespace: str,
    raw: Mapping[str, Any] | None = None,
) -> None:
    ns = _namespace_from_value(namespace)
    if not ns:
        return
    existing = target.setdefault(ns, {})
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if key not in existing:
                existing[key] = value
    existing.setdefault("_source", "surfaces.as_consumer")


def named_service_namespaces(bundle_props: Mapping[str, Any] | None) -> Mapping[str, Any]:
    raw = _get_path(named_services_config(bundle_props), "namespaces", {})
    namespaces: dict[str, dict[str, Any]] = {
        _namespace_from_value(namespace): dict(config)
        for namespace, config in (raw.items() if isinstance(raw, Mapping) else [])
        if _namespace_from_value(namespace) and isinstance(config, Mapping)
    }
    for agent in _as_consumer_agents(bundle_props).values():
        if not isinstance(agent, Mapping):
            continue
        tools = agent.get("tools")
        if isinstance(tools, list):
            for connection in tools:
                if not isinstance(connection, Mapping):
                    continue
                if str(connection.get("kind") or "python").strip().lower() != "named_service":
                    continue
                raw_namespaces = connection.get("namespaces")
                if not isinstance(raw_namespaces, Mapping):
                    continue
                for namespace, namespace_cfg in raw_namespaces.items():
                    _merge_namespace_config(
                        namespaces,
                        str(namespace),
                        namespace_cfg if isinstance(namespace_cfg, Mapping) else {},
                    )
        event_sources = agent.get("event_sources")
        if isinstance(event_sources, list):
            for event_source in event_sources:
                if not isinstance(event_source, Mapping):
                    continue
                if str(event_source.get("kind") or "").strip().lower() != "named_service":
                    continue
                _merge_namespace_config(namespaces, str(event_source.get("namespace") or ""), event_source)
    for resolver in _ui_canvas_resolvers_from_surfaces(bundle_props):
        if str(resolver.get("kind") or "").strip().lower() != "named_service":
            continue
        _merge_namespace_config(namespaces, str(resolver.get("namespace") or ""), resolver)
    return namespaces


def named_service_namespace_config(
    bundle_props: Mapping[str, Any] | None,
    *,
    namespace: str,
) -> Mapping[str, Any]:
    namespaces = named_service_namespaces(bundle_props)
    raw = namespaces.get(str(namespace or "").strip().lower().rstrip(":"))
    return raw if isinstance(raw, Mapping) else {}


def named_service_namespace_provider_configs_from_config(namespace_cfg: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    raw = (namespace_cfg or {}).get("providers") if isinstance(namespace_cfg, Mapping) else None
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []


def named_service_namespace_provider_configs(
    bundle_props: Mapping[str, Any] | None,
    *,
    namespace: str,
) -> list[Mapping[str, Any]]:
    return named_service_namespace_provider_configs_from_config(
        named_service_namespace_config(bundle_props, namespace=namespace)
    )


def _agent_tool_connection_keys(client_id: Any) -> list[str]:
    keys: list[str] = []
    raw = str(client_id or "").strip()
    if raw:
        keys.append(raw)
    if DEFAULT_AGENT_TOOLS_ID not in keys:
        keys.append(DEFAULT_AGENT_TOOLS_ID)
    return keys


def _has_as_consumer_config(bundle_props: Mapping[str, Any] | None) -> bool:
    return bool(_as_consumer_config(bundle_props))


def _policy_mode(config: Mapping[str, Any], name: str) -> str:
    policies = config.get("policies")
    if not isinstance(policies, Mapping):
        return ""
    policy = policies.get(name)
    if not isinstance(policy, Mapping):
        return ""
    return str(policy.get("mode") or "").strip().lower()


def _policy_provider_enabled(config: Mapping[str, Any], name: str) -> bool:
    return _policy_mode(config, name) == "provider"


def _named_service_namespace_agent_tools_config(
    bundle_props: Mapping[str, Any] | None,
    *,
    namespace: str,
    client_id: Any,
) -> Mapping[str, Any]:
    namespace_key = str(namespace or "").strip().lower().rstrip(":")
    for connection in _agent_tool_connections_from_surfaces(bundle_props, client_id=client_id):
        if str(connection.get("kind") or "python").strip().lower() != "named_service":
            continue
        namespaces = connection.get("namespaces")
        if not isinstance(namespaces, Mapping):
            continue
        raw = namespaces.get(namespace_key)
        if not isinstance(raw, Mapping):
            continue
        allowed_operations = raw.get("allowed") or raw.get("allowed_operations") or raw.get("operations")
        if isinstance(allowed_operations, Sequence) and not isinstance(allowed_operations, (str, bytes)):
            return {"allowed_operations": [str(item) for item in allowed_operations if str(item or "").strip()]}
        return {}

    agents = _get_path(bundle_props or {}, "tools.agents", {})
    if not isinstance(agents, Mapping):
        return {}
    for key in _agent_tool_connection_keys(client_id):
        connections = agents.get(key)
        if not isinstance(connections, list):
            continue
        for connection in connections:
            if not isinstance(connection, Mapping):
                continue
            if str(connection.get("kind") or "python").strip().lower() != "named_service":
                continue
            namespaces = connection.get("namespaces")
            if not isinstance(namespaces, Mapping):
                continue
            raw = namespaces.get(namespace_key)
            if not isinstance(raw, Mapping):
                continue
            allowed_operations = raw.get("allowed") or raw.get("allowed_operations") or raw.get("operations")
            if isinstance(allowed_operations, Sequence) and not isinstance(allowed_operations, (str, bytes)):
                return {"allowed_operations": [str(item) for item in allowed_operations if str(item or "").strip()]}
            return {}
    return {}


def named_service_namespace_client_tools_config(
    bundle_props: Mapping[str, Any] | None,
    *,
    namespace: str,
    client_id: Any,
) -> Mapping[str, Any]:
    configured = _named_service_namespace_agent_tools_config(
        bundle_props,
        namespace=namespace,
        client_id=client_id,
    )
    if configured:
        return configured
    namespace_cfg = named_service_namespace_config(bundle_props, namespace=namespace)
    clients = namespace_cfg.get("clients")
    if not isinstance(clients, Mapping):
        return {}
    for key in client_config_keys(client_id):
        raw = clients.get(key)
        if isinstance(raw, Mapping) and isinstance(raw.get("tools"), Mapping):
            return raw["tools"]
    return {}


def named_service_namespace_client_resolver_config(
    bundle_props: Mapping[str, Any] | None,
    *,
    namespace: str,
    client_id: Any,
) -> Mapping[str, Any]:
    if normalize_agent_id(client_id) == "canvas":
        namespace_key = str(namespace or "").strip().lower().rstrip(":")
        for resolver in _ui_canvas_resolvers_from_surfaces(bundle_props):
            if str(resolver.get("kind") or "").strip().lower() != "named_service":
                continue
            if _namespace_from_value(resolver.get("namespace")) != namespace_key:
                continue
            if resolver.get("enabled") is False:
                return {}
            return {
                "enabled": True,
                "allowed_operations": list(
                    resolver.get("allowed")
                    or resolver.get("allowed_operations")
                    or resolver.get("operations")
                    or ()
                ),
            }

    namespace_cfg = named_service_namespace_config(bundle_props, namespace=namespace)
    clients = namespace_cfg.get("clients")
    if not isinstance(clients, Mapping):
        return {}
    for key in client_config_keys(client_id):
        raw = clients.get(key)
        if isinstance(raw, Mapping) and isinstance(raw.get("resolver"), Mapping):
            return raw["resolver"]
    return {}


def client_has_named_service_tools(
    bundle_props: Mapping[str, Any] | None,
    *,
    client_id: Any,
) -> bool:
    for namespace in named_service_namespaces(bundle_props):
        tools = named_service_namespace_client_tools_config(
            bundle_props,
            namespace=namespace,
            client_id=client_id,
        )
        if isinstance(tools, Mapping) and bool(tools):
            return True
    return False


def named_service_agent_event_source_namespaces(
    bundle_props: Mapping[str, Any] | None,
    *,
    client_id: Any,
) -> Mapping[str, Any]:
    """Return named-service namespaces with an enabled agent event-source surface."""

    legacy = named_service_namespaces(bundle_props)
    event_sources = _agent_event_sources_from_surfaces(bundle_props, client_id=client_id)
    if not event_sources:
        return {} if _has_as_consumer_config(bundle_props) else legacy
    selected: dict[str, dict[str, Any]] = {}
    for event_source in event_sources:
        if str(event_source.get("kind") or "").strip().lower() != "named_service":
            continue
        if event_source.get("enabled") is False:
            continue
        if not _policy_provider_enabled(event_source, "block_production"):
            continue
        namespace = _namespace_from_value(event_source.get("namespace"))
        if not namespace:
            continue
        merged = dict(legacy.get(namespace) or {})
        merged.update(dict(event_source))
        selected[namespace] = merged
    return selected


def named_service_agent_pull_namespaces(
    bundle_props: Mapping[str, Any] | None,
    *,
    client_id: Any,
) -> Mapping[str, Any]:
    """Return named-service namespaces whose event-source pull policy is enabled."""

    selected: dict[str, dict[str, Any]] = {}
    event_sources = _agent_event_sources_from_surfaces(bundle_props, client_id=client_id)
    if not event_sources:
        return {} if _has_as_consumer_config(bundle_props) else named_service_namespaces(bundle_props)
    legacy = named_service_namespaces(bundle_props)
    for event_source in event_sources:
        if str(event_source.get("kind") or "").strip().lower() != "named_service":
            continue
        if event_source.get("enabled") is False:
            continue
        if not _policy_provider_enabled(event_source, "pull"):
            continue
        namespace = _namespace_from_value(event_source.get("namespace"))
        if not namespace:
            continue
        config = {**dict(legacy.get(namespace) or {}), **dict(event_source)}
        policies = config.get("policies")
        if not isinstance(policies, Mapping):
            continue
        pull = policies.get("pull")
        if not isinstance(pull, Mapping) or pull.get("mode") != "provider":
            continue
        merged = dict(config)
        merged["pull"] = dict(pull)
        selected[namespace] = merged
    return selected


def named_service_canvas_resolver_namespaces(bundle_props: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return named-service namespaces with an enabled canvas resolver surface."""

    legacy_namespaces = named_service_namespaces(bundle_props)
    resolvers = _ui_canvas_resolvers_from_surfaces(bundle_props)
    if not resolvers:
        return {} if _has_as_consumer_config(bundle_props) else legacy_namespaces
    selected: dict[str, dict[str, Any]] = {}
    for resolver in resolvers:
        if str(resolver.get("kind") or "").strip().lower() != "named_service":
            continue
        if resolver.get("enabled") is False:
            continue
        namespace = _namespace_from_value(resolver.get("namespace"))
        if not namespace:
            continue
        merged = dict(legacy_namespaces.get(namespace) or {})
        merged.update(dict(resolver))
        clients = merged.setdefault("clients", {})
        if not isinstance(clients, dict):
            clients = {}
            merged["clients"] = clients
        canvas = clients.setdefault("canvas", {})
        if not isinstance(canvas, dict):
            canvas = {}
            clients["canvas"] = canvas
        resolver_cfg = canvas.setdefault("resolver", {})
        if not isinstance(resolver_cfg, dict):
            resolver_cfg = {}
            canvas["resolver"] = resolver_cfg
        resolver_cfg.setdefault("enabled", True)
        if resolver.get("allowed") is not None:
            resolver_cfg.setdefault("allowed_operations", list(resolver.get("allowed") or []))
        selected[namespace] = merged
    return selected


def named_service_tool_spec(alias: str = NAMED_SERVICE_TOOLS_ALIAS) -> dict[str, Any]:
    return {
        "module": NAMED_SERVICE_TOOLS_MODULE,
        "alias": alias,
        "use_sk": False,
    }


def extend_tool_specs_for_named_services(
    base_specs: Sequence[Mapping[str, Any]] | None,
    *,
    bundle_props: Mapping[str, Any] | None,
    client_id: Any,
    alias: str = NAMED_SERVICE_TOOLS_ALIAS,
) -> list[dict[str, Any]]:
    specs = [dict(spec) for spec in (base_specs or []) if isinstance(spec, Mapping)]
    if not client_has_named_service_tools(bundle_props, client_id=client_id):
        return specs
    if not any(str(spec.get("alias") or "").strip() == alias for spec in specs):
        specs.append(named_service_tool_spec(alias=alias))
    return specs


__all__ = [
    "NAMED_SERVICE_TOOLS_ALIAS",
    "NAMED_SERVICE_TOOLS_MODULE",
    "client_config_keys",
    "client_has_named_service_tools",
    "extend_tool_specs_for_named_services",
    "named_service_agent_event_source_namespaces",
    "named_service_agent_pull_namespaces",
    "named_service_canvas_resolver_namespaces",
    "named_service_namespace_client_tools_config",
    "named_service_namespace_client_resolver_config",
    "named_service_namespace_config",
    "named_service_namespace_provider_configs",
    "named_service_namespace_provider_configs_from_config",
    "named_service_namespaces",
    "named_services_config",
    "named_service_tool_spec",
]
