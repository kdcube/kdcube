# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.event_identity import normalize_agent_id


NAMED_SERVICE_TOOLS_MODULE = "kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.tools"
NAMED_SERVICE_TOOLS_ALIAS = "named_services"


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


def named_service_namespaces(bundle_props: Mapping[str, Any] | None) -> Mapping[str, Any]:
    raw = _get_path(named_services_config(bundle_props), "namespaces", {})
    return raw if isinstance(raw, Mapping) else {}


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


def named_service_namespace_client_tools_config(
    bundle_props: Mapping[str, Any] | None,
    *,
    namespace: str,
    client_id: Any,
) -> Mapping[str, Any]:
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
    "named_service_namespace_client_tools_config",
    "named_service_namespace_client_resolver_config",
    "named_service_namespace_config",
    "named_service_namespace_provider_configs",
    "named_service_namespace_provider_configs_from_config",
    "named_service_namespaces",
    "named_services_config",
    "named_service_tool_spec",
]
