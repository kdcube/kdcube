# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Assemble one delegated catalog from app-owned descriptor declarations."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


CATALOG_DECLARATION_KEY = "delegated_catalog"
CATALOG_DECLARATION_VERSION = "1"
CONNECTION_HUB_BUNDLE_ID = "connection-hub@1-0"


@dataclass(frozen=True)
class DelegatedCatalogAssembly:
    connections: dict[str, Any]
    contributors: tuple[str, ...]


class CatalogAssemblyError(ValueError):
    """An app-owned declaration cannot join the deployment catalog safely."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str = "",
        owners: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.owners = owners

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "message": str(self),
                "path": self.path,
                "owners": list(self.owners),
            }.items()
            if value not in ("", (), [])
        }


def _catalog_oauth(connections: dict[str, Any]) -> dict[str, Any]:
    delegated = connections.setdefault("delegated_credentials", {})
    if not isinstance(delegated, dict):
        raise CatalogAssemblyError(
            "invalid_base_catalog",
            "connections.delegated_credentials must be a mapping",
            path="connections.delegated_credentials",
            owners=(CONNECTION_HUB_BUNDLE_ID,),
        )
    oauth = delegated.setdefault("oauth", {})
    if not isinstance(oauth, dict):
        raise CatalogAssemblyError(
            "invalid_base_catalog",
            "connections.delegated_credentials.oauth must be a mapping",
            path="connections.delegated_credentials.oauth",
            owners=(CONNECTION_HUB_BUNDLE_ID,),
        )
    return oauth


def _keyed_rows(
    value: Any,
    *,
    key: str,
    path: str,
    owner: str,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CatalogAssemblyError(
            "invalid_catalog_declaration",
            f"{path} must be a list",
            path=path,
            owners=(owner,),
        )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        row_path = f"{path}[{index}]"
        if not isinstance(raw, Mapping):
            raise CatalogAssemblyError(
                "invalid_catalog_declaration",
                f"{row_path} must be a mapping",
                path=row_path,
                owners=(owner,),
            )
        row = copy.deepcopy(dict(raw))
        identity = str(row.get(key) or "").strip()
        if not identity:
            raise CatalogAssemblyError(
                "invalid_catalog_declaration",
                f"{row_path}.{key} must be a non-empty string",
                path=f"{row_path}.{key}",
                owners=(owner,),
            )
        if identity in seen:
            raise CatalogAssemblyError(
                f"duplicate_{key}",
                f"{owner} declares {key} {identity!r} more than once",
                path=f"{path}[{identity}]",
                owners=(owner, owner),
            )
        seen.add(identity)
        rows.append(row)
    return rows


def _declaration(
    props: Mapping[str, Any] | None,
    *,
    owner: str,
) -> dict[str, list[dict[str, Any]]] | None:
    raw = props.get(CATALOG_DECLARATION_KEY) if isinstance(props, Mapping) else None
    if raw is None:
        return None
    path = f"bundles[{owner}].config.{CATALOG_DECLARATION_KEY}"
    if not isinstance(raw, Mapping):
        raise CatalogAssemblyError(
            "invalid_catalog_declaration",
            f"{path} must be a mapping",
            path=path,
            owners=(owner,),
        )
    unknown = sorted(
        set(raw).difference(
            {"version", "capabilities", "resources", "named_service_namespaces"}
        )
    )
    if unknown:
        raise CatalogAssemblyError(
            "invalid_catalog_declaration",
            f"{path} has unsupported keys: {', '.join(unknown)}",
            path=path,
            owners=(owner,),
        )
    version = str(raw.get("version") or "").strip()
    if version != CATALOG_DECLARATION_VERSION:
        raise CatalogAssemblyError(
            "unsupported_catalog_declaration_version",
            f"{path}.version must be {CATALOG_DECLARATION_VERSION!r}",
            path=f"{path}.version",
            owners=(owner,),
        )
    return {
        "capabilities": _keyed_rows(
            raw.get("capabilities"),
            key="grant",
            path=f"{path}.capabilities",
            owner=owner,
        ),
        "resources": _keyed_rows(
            raw.get("resources"),
            key="resource",
            path=f"{path}.resources",
            owner=owner,
        ),
        "named_service_namespaces": _keyed_rows(
            raw.get("named_service_namespaces"),
            key="resource",
            path=f"{path}.named_service_namespaces",
            owner=owner,
        ),
    }


def _claim(
    owners: dict[str, str],
    identity: str,
    *,
    owner: str,
    kind: str,
    path: str,
) -> None:
    previous = owners.get(identity)
    if previous is not None:
        raise CatalogAssemblyError(
            f"duplicate_{kind}",
            f"Delegated catalog {kind} {identity!r} is declared by both "
            f"{previous!r} and {owner!r}",
            path=path,
            owners=(previous, owner),
        )
    owners[identity] = owner


def _base_rows(
    oauth: dict[str, Any],
    *,
    field: str,
    key: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    rows = _keyed_rows(
        oauth.get(field),
        key=key,
        path=f"connections.delegated_credentials.oauth.{field}",
        owner=CONNECTION_HUB_BUNDLE_ID,
    )
    oauth[field] = rows
    indexed: dict[str, dict[str, Any]] = {}
    owners: dict[str, str] = {}
    for row in rows:
        identity = str(row[key])
        _claim(
            owners,
            identity,
            owner=CONNECTION_HUB_BUNDLE_ID,
            kind=key,
            path=f"connections.delegated_credentials.oauth.{field}[{identity}]",
        )
        indexed[identity] = row
    return rows, indexed, owners


def _namespace_map(
    resource: dict[str, Any],
    *,
    resource_id: str,
) -> dict[str, Any]:
    named_services = resource.setdefault("named_services", {})
    path = (
        "connections.delegated_credentials.oauth.resources"
        f"[{resource_id}].named_services"
    )
    if not isinstance(named_services, dict):
        raise CatalogAssemblyError(
            "invalid_base_catalog",
            f"{path} must be a mapping",
            path=path,
            owners=(CONNECTION_HUB_BUNDLE_ID,),
        )
    namespaces = named_services.setdefault("namespaces", {})
    if not isinstance(namespaces, dict):
        raise CatalogAssemblyError(
            "invalid_base_catalog",
            f"{path}.namespaces must be a mapping",
            path=f"{path}.namespaces",
            owners=(CONNECTION_HUB_BUNDLE_ID,),
        )
    return namespaces


def assemble_delegated_catalog(
    *,
    base_connections: Mapping[str, Any] | None,
    app_props: Mapping[str, Mapping[str, Any] | None],
) -> DelegatedCatalogAssembly:
    """Merge all app declarations into one exact Connection Hub catalog body."""
    connections = copy.deepcopy(dict(base_connections or {}))
    oauth = _catalog_oauth(connections)
    capabilities, _capability_rows, capability_owners = _base_rows(
        oauth,
        field="capabilities",
        key="grant",
    )
    resources, resources_by_id, resource_owners = _base_rows(
        oauth,
        field="resources",
        key="resource",
    )

    declarations: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for owner in sorted(str(value).strip() for value in app_props if str(value).strip()):
        if owner == CONNECTION_HUB_BUNDLE_ID:
            continue
        declaration = _declaration(app_props.get(owner), owner=owner)
        if declaration is not None:
            declarations[owner] = declaration

    for owner, declaration in declarations.items():
        for row in declaration["capabilities"]:
            grant = str(row["grant"])
            _claim(
                capability_owners,
                grant,
                owner=owner,
                kind="capability",
                path=f"connections.delegated_credentials.oauth.capabilities[{grant}]",
            )
            capabilities.append(row)
        for row in declaration["resources"]:
            resource_id = str(row["resource"])
            _claim(
                resource_owners,
                resource_id,
                owner=owner,
                kind="resource",
                path=f"connections.delegated_credentials.oauth.resources[{resource_id}]",
            )
            resources.append(row)
            resources_by_id[resource_id] = row

    namespace_owners: dict[tuple[str, str], str] = {}
    for resource_id, resource in resources_by_id.items():
        for namespace in _namespace_map(resource, resource_id=resource_id):
            namespace_owners[(resource_id, str(namespace))] = resource_owners[resource_id]

    for owner, declaration in declarations.items():
        for extension in declaration["named_service_namespaces"]:
            resource_id = str(extension["resource"])
            resource = resources_by_id.get(resource_id)
            extension_path = (
                f"bundles[{owner}].config.{CATALOG_DECLARATION_KEY}"
                f".named_service_namespaces[{resource_id}]"
            )
            if resource is None:
                raise CatalogAssemblyError(
                    "named_service_resource_not_declared",
                    f"{owner!r} extends named services on unknown resource {resource_id!r}",
                    path=extension_path,
                    owners=(owner,),
                )
            unknown = sorted(set(extension).difference({"resource", "namespaces"}))
            if unknown:
                raise CatalogAssemblyError(
                    "invalid_catalog_declaration",
                    f"{extension_path} has unsupported keys: {', '.join(unknown)}",
                    path=extension_path,
                    owners=(owner,),
                )
            raw_namespaces = extension.get("namespaces")
            if not isinstance(raw_namespaces, Mapping) or not raw_namespaces:
                raise CatalogAssemblyError(
                    "invalid_catalog_declaration",
                    f"{extension_path}.namespaces must be a non-empty mapping",
                    path=f"{extension_path}.namespaces",
                    owners=(owner,),
                )
            target = _namespace_map(resource, resource_id=resource_id)
            for namespace, raw_config in raw_namespaces.items():
                namespace_id = str(namespace or "").strip()
                namespace_path = f"{extension_path}.namespaces[{namespace_id}]"
                if not namespace_id or not isinstance(raw_config, Mapping):
                    raise CatalogAssemblyError(
                        "invalid_catalog_declaration",
                        f"{namespace_path} must name a namespace mapping",
                        path=namespace_path,
                        owners=(owner,),
                    )
                previous = namespace_owners.get((resource_id, namespace_id))
                if previous is not None:
                    raise CatalogAssemblyError(
                        "duplicate_named_service_namespace",
                        f"Named-service namespace {namespace_id!r} on {resource_id!r} "
                        f"is declared by both {previous!r} and {owner!r}",
                        path=namespace_path,
                        owners=(previous, owner),
                    )
                namespace_owners[(resource_id, namespace_id)] = owner
                target[namespace_id] = copy.deepcopy(dict(raw_config))

    return DelegatedCatalogAssembly(
        connections=connections,
        contributors=tuple(declarations),
    )


def catalog_connection_differences(
    *,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Report every structural difference in the catalog served to requests."""
    differences: list[dict[str, Any]] = []

    def keyed(items: list[Any], identity_key: str, path: str, side: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for index, item in enumerate(items):
            identity = str(item.get(identity_key) or "").strip()
            item_path = f"{path}[{identity or index}]"
            if not identity:
                differences.append(
                    {
                        "kind": "invalid",
                        "path": item_path,
                        "expected": "a non-empty identity" if side == "actual" else item,
                        "actual": item if side == "actual" else None,
                    }
                )
                continue
            if identity in result:
                differences.append(
                    {
                        "kind": "duplicate",
                        "path": item_path,
                        "expected": "one declaration",
                        "actual": "multiple declarations",
                    }
                )
                continue
            result[identity] = item
        return result

    def compare(left: Any, right: Any, path: str) -> None:
        if isinstance(left, Mapping):
            if not isinstance(right, Mapping):
                differences.append(
                    {"kind": "type_mismatch", "path": path, "expected": left, "actual": right}
                )
                return
            for key in sorted(set(left).union(right)):
                child = f"{path}.{key}" if path else str(key)
                if key not in right:
                    differences.append(
                        {"kind": "missing", "path": child, "expected": left[key], "actual": None}
                    )
                elif key not in left:
                    differences.append(
                        {"kind": "extra", "path": child, "expected": None, "actual": right[key]}
                    )
                else:
                    compare(left[key], right[key], child)
            return
        if isinstance(left, list):
            if not isinstance(right, list):
                differences.append(
                    {"kind": "type_mismatch", "path": path, "expected": left, "actual": right}
                )
                return
            identity_key = "grant" if path.endswith(".capabilities") else (
                "resource" if path.endswith(".resources") else ""
            )
            if identity_key and all(isinstance(item, Mapping) for item in left + right):
                left_map = keyed(left, identity_key, path, "expected")
                right_map = keyed(right, identity_key, path, "actual")
                compare(left_map, right_map, path)
                return
            common = min(len(left), len(right))
            for index in range(common):
                compare(left[index], right[index], f"{path}[{index}]")
            for index in range(common, len(left)):
                differences.append(
                    {
                        "kind": "missing",
                        "path": f"{path}[{index}]",
                        "expected": left[index],
                        "actual": None,
                    }
                )
            for index in range(common, len(right)):
                differences.append(
                    {
                        "kind": "extra",
                        "path": f"{path}[{index}]",
                        "expected": None,
                        "actual": right[index],
                    }
                )
            return
        if type(left) is not type(right) or left != right:
            differences.append(
                {"kind": "value_mismatch", "path": path, "expected": left, "actual": right}
            )

    compare(dict(expected), dict(actual), "connections")
    return differences


__all__ = [
    "CATALOG_DECLARATION_KEY",
    "CATALOG_DECLARATION_VERSION",
    "CONNECTION_HUB_BUNDLE_ID",
    "CatalogAssemblyError",
    "DelegatedCatalogAssembly",
    "assemble_delegated_catalog",
    "catalog_connection_differences",
]
