# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONNECTION_HUB_BUNDLE_ID = "connection-hub@1-0"
CATALOG_FRAGMENT_SCHEMA = "kdcube.connection-hub.catalog-fragment.v1"
_CATALOG_PATH = ("config", "connections", "delegated_credentials", "oauth")
_SIMPLE_PATH_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_EXACT_SET_FIELDS = frozenset({"grants", "delegable_roles", "delegable_permissions"})


class CatalogFragmentError(ValueError):
    """Raised when a catalog fragment cannot be checked or applied safely."""


def _load_yaml_mapping(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CatalogFragmentError(f"Unable to read {label}: {path}\n{exc}") from exc
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise CatalogFragmentError(f"Unable to parse {label} YAML: {path}\n{exc}") from exc
    if not isinstance(value, dict):
        raise CatalogFragmentError(f"{label.capitalize()} must contain a YAML mapping: {path}")
    return value, raw


def _identity(value: Any, *, key: str, where: str) -> str:
    if not isinstance(value, dict):
        raise CatalogFragmentError(f"{where} entries must be mappings")
    identity = str(value.get(key) or "").strip()
    if not identity:
        raise CatalogFragmentError(f"{where} entries must declare {key!r}")
    return identity


def _validate_keyed_fragment_list(
    value: Any,
    *,
    key: str,
    where: str,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CatalogFragmentError(f"{where} must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        identity = _identity(item, key=key, where=where)
        if identity in seen:
            raise CatalogFragmentError(f"{where} declares {identity!r} more than once")
        seen.add(identity)
        result.append(item)
    return result


def validate_catalog_fragment(value: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(value).difference({"capabilities", "resources"}))
    if unknown:
        raise CatalogFragmentError(
            "Catalog fragment supports only capabilities and resources; "
            f"unexpected keys: {', '.join(unknown)}"
        )
    capabilities = _validate_keyed_fragment_list(
        value.get("capabilities"),
        key="grant",
        where="capabilities",
    )
    resources = _validate_keyed_fragment_list(
        value.get("resources"),
        key="resource",
        where="resources",
    )
    if not capabilities and not resources:
        raise CatalogFragmentError(
            "Catalog fragment must declare at least one capability or resource"
        )
    return {"capabilities": capabilities, "resources": resources}


def _bundle_items(descriptor: dict[str, Any]) -> list[dict[str, Any]]:
    bundles = descriptor.get("bundles")
    if not isinstance(bundles, dict):
        raise CatalogFragmentError("bundles.yaml must declare a bundles mapping")
    items = bundles.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return [
        value
        for key, value in bundles.items()
        if key not in {"version", "default_bundle_id"} and isinstance(value, dict)
    ]


def _find_bundle(descriptor: dict[str, Any], bundle_id: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    bundles = descriptor.get("bundles")
    for item in _bundle_items(descriptor):
        item_id = str(item.get("id") or "").strip()
        if not item_id and isinstance(bundles, dict):
            for key, candidate in bundles.items():
                if candidate is item:
                    item_id = str(key).strip()
                    break
        if item_id == bundle_id:
            matches.append(item)
    if not matches:
        raise CatalogFragmentError(f"bundles.yaml does not declare {bundle_id!r}")
    if len(matches) > 1:
        raise CatalogFragmentError(f"bundles.yaml declares {bundle_id!r} more than once")
    return matches[0]


def _read_catalog(bundle: dict[str, Any]) -> dict[str, Any]:
    current: Any = bundle
    path = "bundle"
    for key in _CATALOG_PATH:
        path = _mapping_path(path, key)
        if not isinstance(current, dict):
            raise CatalogFragmentError(f"{path} must be a mapping")
        if key not in current or current[key] is None:
            return {}
        current = current[key]
    if not isinstance(current, dict):
        raise CatalogFragmentError(f"{path} must be a mapping")
    return current


def _ensure_catalog(
    bundle: dict[str, Any],
    *,
    bundle_id: str,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    current: dict[str, Any] = bundle
    path = f"bundles[{bundle_id}]"
    for key in _CATALOG_PATH:
        path = _mapping_path(path, key)
        child = current.get(key)
        if child is None:
            child = {}
            current[key] = child
            changes.append({"kind": "added", "path": path, "value": {}})
        elif not isinstance(child, dict):
            raise CatalogFragmentError(
                f"Cannot apply catalog fragment because {path} is not a mapping"
            )
        current = child
    return current


def _mapping_path(parent: str, key: str) -> str:
    if _SIMPLE_PATH_KEY.fullmatch(key):
        return f"{parent}.{key}" if parent else key
    encoded = json.dumps(key, ensure_ascii=True)
    return f"{parent}[{encoded}]" if parent else f"[{encoded}]"


def _keyed_path(parent: str, identity: str) -> str:
    return f"{parent}[{identity}]"


def _same_scalar(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _is_scalar_list(value: list[Any]) -> bool:
    return all(not isinstance(item, (dict, list)) for item in value)


def _is_exact_set_path(path: str) -> bool:
    return path.rsplit(".", 1)[-1] in _EXACT_SET_FIELDS


def _difference(
    *,
    kind: str,
    path: str,
    expected: Any,
    actual: Any,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": path,
        "expected": copy.deepcopy(expected),
        "actual": copy.deepcopy(actual),
    }


def _compare_value(
    expected: Any,
    actual: Any,
    *,
    path: str,
    differences: list[dict[str, Any]],
) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            differences.append(
                _difference(kind="type_mismatch", path=path, expected=expected, actual=actual)
            )
            return
        for key, expected_value in expected.items():
            child_path = _mapping_path(path, str(key))
            if key not in actual:
                differences.append(
                    _difference(
                        kind="missing",
                        path=child_path,
                        expected=expected_value,
                        actual=None,
                    )
                )
                continue
            _compare_value(
                expected_value,
                actual[key],
                path=child_path,
                differences=differences,
            )
        return
    if isinstance(expected, list):
        if not isinstance(actual, list):
            differences.append(
                _difference(kind="type_mismatch", path=path, expected=expected, actual=actual)
            )
            return
        if not _is_scalar_list(expected) or not _is_scalar_list(actual):
            if expected != actual:
                differences.append(
                    _difference(kind="value_mismatch", path=path, expected=expected, actual=actual)
                )
            return
        for expected_item in expected:
            if not any(_same_scalar(expected_item, actual_item) for actual_item in actual):
                differences.append(
                    _difference(
                        kind="missing",
                        path=_keyed_path(path, str(expected_item)),
                        expected=expected_item,
                        actual=None,
                    )
                )
        if _is_exact_set_path(path) and any(
            not any(_same_scalar(actual_item, expected_item) for expected_item in expected)
            for actual_item in actual
        ):
            differences.append(
                _difference(kind="value_mismatch", path=path, expected=expected, actual=actual)
            )
        return
    if not _same_scalar(expected, actual):
        differences.append(
            _difference(kind="value_mismatch", path=path, expected=expected, actual=actual)
        )


def _index_target_items(
    value: Any,
    *,
    key: str,
    path: str,
    differences: list[dict[str, Any]],
) -> dict[str, dict[str, Any]] | None:
    if value is None:
        return {}
    if not isinstance(value, list):
        differences.append(
            _difference(kind="type_mismatch", path=path, expected=[], actual=value)
        )
        return None
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            differences.append(
                _difference(kind="invalid", path=item_path, expected="mapping", actual=item)
            )
            continue
        identity = str(item.get(key) or "").strip()
        if not identity:
            differences.append(
                _difference(
                    kind="invalid",
                    path=_mapping_path(item_path, key),
                    expected="non-empty string",
                    actual=item.get(key),
                )
            )
            continue
        if identity in result:
            differences.append(
                _difference(
                    kind="duplicate",
                    path=_keyed_path(path, identity),
                    expected="one declaration",
                    actual="multiple declarations",
                )
            )
            continue
        result[identity] = item
    return result


def _compare_keyed_items(
    expected: list[dict[str, Any]],
    actual: Any,
    *,
    key: str,
    path: str,
    differences: list[dict[str, Any]],
) -> None:
    indexed = _index_target_items(
        actual,
        key=key,
        path=path,
        differences=differences,
    )
    if indexed is None:
        return
    for expected_item in expected:
        identity = str(expected_item[key])
        item_path = _keyed_path(path, identity)
        actual_item = indexed.get(identity)
        if actual_item is None:
            differences.append(
                _difference(
                    kind="missing",
                    path=item_path,
                    expected=expected_item,
                    actual=None,
                )
            )
            continue
        _compare_value(
            expected_item,
            actual_item,
            path=item_path,
            differences=differences,
        )


def catalog_fragment_differences(
    *,
    catalog: dict[str, Any],
    fragment: dict[str, Any],
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    _compare_keyed_items(
        fragment["capabilities"],
        catalog.get("capabilities"),
        key="grant",
        path="capabilities",
        differences=differences,
    )
    _compare_keyed_items(
        fragment["resources"],
        catalog.get("resources"),
        key="resource",
        path="resources",
        differences=differences,
    )
    return differences


def _apply_value(
    expected: Any,
    actual: Any,
    *,
    path: str,
    changes: list[dict[str, Any]],
) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key, expected_value in expected.items():
            child_path = _mapping_path(path, str(key))
            if key not in actual:
                actual[key] = copy.deepcopy(expected_value)
                changes.append(
                    {"kind": "added", "path": child_path, "value": copy.deepcopy(expected_value)}
                )
                continue
            _apply_value(
                expected_value,
                actual[key],
                path=child_path,
                changes=changes,
            )
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if not _is_scalar_list(expected) or not _is_scalar_list(actual):
            return
        for expected_item in expected:
            if any(_same_scalar(expected_item, actual_item) for actual_item in actual):
                continue
            actual.append(copy.deepcopy(expected_item))
            changes.append(
                {
                    "kind": "added",
                    "path": _keyed_path(path, str(expected_item)),
                    "value": copy.deepcopy(expected_item),
                }
            )


def _apply_keyed_items(
    expected: list[dict[str, Any]],
    catalog: dict[str, Any],
    *,
    key: str,
    path: str,
    changes: list[dict[str, Any]],
) -> None:
    if not expected:
        return
    actual = catalog.get(path)
    if actual is None:
        actual = []
        catalog[path] = actual
    inspection_differences: list[dict[str, Any]] = []
    indexed = _index_target_items(
        actual,
        key=key,
        path=path,
        differences=inspection_differences,
    )
    if indexed is None or inspection_differences:
        return
    for expected_item in expected:
        identity = str(expected_item[key])
        item_path = _keyed_path(path, identity)
        actual_item = indexed.get(identity)
        if actual_item is None:
            actual.append(copy.deepcopy(expected_item))
            indexed[identity] = actual[-1]
            changes.append(
                {"kind": "added", "path": item_path, "value": copy.deepcopy(expected_item)}
            )
            continue
        _apply_value(
            expected_item,
            actual_item,
            path=item_path,
            changes=changes,
        )


def apply_catalog_fragment_to_descriptor(
    descriptor: dict[str, Any],
    *,
    fragment: dict[str, Any],
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updated = copy.deepcopy(descriptor)
    bundle = _find_bundle(updated, connection_hub_bundle_id)
    changes: list[dict[str, Any]] = []
    catalog = _ensure_catalog(
        bundle,
        bundle_id=connection_hub_bundle_id,
        changes=changes,
    )
    _apply_keyed_items(
        fragment["capabilities"],
        catalog,
        key="grant",
        path="capabilities",
        changes=changes,
    )
    _apply_keyed_items(
        fragment["resources"],
        catalog,
        key="resource",
        path="resources",
        changes=changes,
    )
    return updated, changes


def _catalog_inventory(value: dict[str, Any]) -> dict[str, int]:
    direct_tools = 0
    named_service_namespaces = 0
    named_service_tools = 0
    named_service_operations = 0
    resources = value.get("resources")
    if not isinstance(resources, list):
        resources = []
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        tools = resource.get("tools")
        if isinstance(tools, dict):
            direct_tools += len(tools)
        named_services = resource.get("named_services")
        if not isinstance(named_services, dict):
            continue
        namespaces = named_services.get("namespaces")
        if not isinstance(namespaces, dict):
            continue
        named_service_namespaces += len(namespaces)
        for namespace in namespaces.values():
            if not isinstance(namespace, dict):
                continue
            namespace_tools = namespace.get("tools")
            if not isinstance(namespace_tools, dict):
                continue
            named_service_tools += len(namespace_tools)
            for tool in namespace_tools.values():
                if not isinstance(tool, dict):
                    continue
                operations = tool.get("operations")
                if isinstance(operations, dict):
                    named_service_operations += len(operations)
    capabilities = value.get("capabilities")
    return {
        "capabilities": len(capabilities) if isinstance(capabilities, list) else 0,
        "resources": len(resources),
        "direct_tools": direct_tools,
        "named_service_namespaces": named_service_namespaces,
        "named_service_tools": named_service_tools,
        "named_service_operations": named_service_operations,
    }


def _keyed_lookup(value: Any, key: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        identity = str(item.get(key) or "").strip()
        if identity and identity not in result:
            result[identity] = item
    return result


def _matched_catalog_inventory(
    *,
    catalog: dict[str, Any],
    fragment: dict[str, Any],
) -> dict[str, int]:
    expected_capabilities = _keyed_lookup(fragment.get("capabilities"), "grant")
    actual_capabilities = _keyed_lookup(catalog.get("capabilities"), "grant")
    expected_resources = _keyed_lookup(fragment.get("resources"), "resource")
    actual_resources = _keyed_lookup(catalog.get("resources"), "resource")
    result = {
        "capabilities": len(set(expected_capabilities).intersection(actual_capabilities)),
        "resources": len(set(expected_resources).intersection(actual_resources)),
        "direct_tools": 0,
        "named_service_namespaces": 0,
        "named_service_tools": 0,
        "named_service_operations": 0,
    }
    for resource_id, expected_resource in expected_resources.items():
        actual_resource = actual_resources.get(resource_id)
        if not isinstance(actual_resource, dict):
            continue
        expected_tools = expected_resource.get("tools")
        actual_tools = actual_resource.get("tools")
        if isinstance(expected_tools, dict) and isinstance(actual_tools, dict):
            result["direct_tools"] += len(set(expected_tools).intersection(actual_tools))
        expected_named = expected_resource.get("named_services")
        actual_named = actual_resource.get("named_services")
        if not isinstance(expected_named, dict) or not isinstance(actual_named, dict):
            continue
        expected_namespaces = expected_named.get("namespaces")
        actual_namespaces = actual_named.get("namespaces")
        if not isinstance(expected_namespaces, dict) or not isinstance(actual_namespaces, dict):
            continue
        for namespace_id, expected_namespace in expected_namespaces.items():
            actual_namespace = actual_namespaces.get(namespace_id)
            if not isinstance(expected_namespace, dict) or not isinstance(actual_namespace, dict):
                continue
            result["named_service_namespaces"] += 1
            expected_namespace_tools = expected_namespace.get("tools")
            actual_namespace_tools = actual_namespace.get("tools")
            if not isinstance(expected_namespace_tools, dict) or not isinstance(
                actual_namespace_tools, dict
            ):
                continue
            for tool_id, expected_tool in expected_namespace_tools.items():
                actual_tool = actual_namespace_tools.get(tool_id)
                if not isinstance(expected_tool, dict) or not isinstance(actual_tool, dict):
                    continue
                result["named_service_tools"] += 1
                expected_operations = expected_tool.get("operations")
                actual_operations = actual_tool.get("operations")
                if isinstance(expected_operations, dict) and isinstance(actual_operations, dict):
                    result["named_service_operations"] += len(
                        set(expected_operations).intersection(actual_operations)
                    )
    return result


def _difference_counts(differences: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for difference in differences:
        kind = str(difference.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _atomic_write_yaml(path: Path, value: dict[str, Any], *, expected_raw: bytes) -> None:
    if path.read_bytes() != expected_raw:
        raise CatalogFragmentError(
            f"Descriptor changed while catalog fragment was being applied: {path}"
        )
    rendered = yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode("utf-8")
    existing_mode = stat.S_IMODE(path.stat().st_mode)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    temporary_path = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, existing_mode)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary_path.unlink(missing_ok=True)


def process_catalog_fragment(
    *,
    bundles_path: Path,
    fragment_path: Path,
    action: str,
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
) -> dict[str, Any]:
    if action not in {"check", "apply"}:
        raise CatalogFragmentError(f"Unsupported catalog fragment action: {action}")
    descriptor, descriptor_raw = _load_yaml_mapping(bundles_path, label="bundles descriptor")
    raw_fragment, _fragment_raw = _load_yaml_mapping(fragment_path, label="catalog fragment")
    fragment = validate_catalog_fragment(raw_fragment)
    bundle = _find_bundle(descriptor, connection_hub_bundle_id)
    catalog = _read_catalog(bundle)
    differences_before = catalog_fragment_differences(catalog=catalog, fragment=fragment)
    changes: list[dict[str, Any]] = []
    updated = descriptor
    if action == "apply":
        updated, changes = apply_catalog_fragment_to_descriptor(
            descriptor,
            fragment=fragment,
            connection_hub_bundle_id=connection_hub_bundle_id,
        )
        updated_bundle = _find_bundle(updated, connection_hub_bundle_id)
        effective_catalog = _read_catalog(updated_bundle)
        differences_after = catalog_fragment_differences(
            catalog=effective_catalog,
            fragment=fragment,
        )
        if changes:
            _atomic_write_yaml(bundles_path, updated, expected_raw=descriptor_raw)
    else:
        effective_catalog = catalog
        differences_after = differences_before

    in_sync = not differences_after
    if action == "check":
        status = "in_sync" if in_sync else "drift"
    elif in_sync:
        status = "applied" if changes else "in_sync"
    else:
        status = "partial" if changes else "conflict"
    descriptor_sha256_after = hashlib.sha256(
        bundles_path.read_bytes() if changes else descriptor_raw
    ).hexdigest()
    result = {
        "schema": CATALOG_FRAGMENT_SCHEMA,
        "action": action,
        "status": status,
        "in_sync": in_sync,
        "changed": bool(changes),
        "connection_hub_bundle_id": connection_hub_bundle_id,
        "bundles_path": str(bundles_path),
        "fragment_path": str(fragment_path),
        "declared": _catalog_inventory(fragment),
        "present": _matched_catalog_inventory(catalog=effective_catalog, fragment=fragment),
        "difference_counts": _difference_counts(differences_after),
        "differences_before_count": len(differences_before),
        "changes": changes,
        "differences": differences_after,
        "descriptor_sha256_before": hashlib.sha256(descriptor_raw).hexdigest(),
        "descriptor_sha256_after": descriptor_sha256_after,
        "reload_required": bool(changes),
    }
    return result
