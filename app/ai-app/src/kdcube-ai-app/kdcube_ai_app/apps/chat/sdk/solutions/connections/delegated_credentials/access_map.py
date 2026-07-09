"""Read-only resolution of the delegated-access grants map.

One admin question answered from live config: WHICH named services does this
deployment expose to external delegated clients, UNDER which OAuth resources,
WITH which grants — and, for provider-backed realms, which connected-account
claims exist beside them. Everything here is a projection of
``config.connections.*`` (``delegated_credentials.oauth.capabilities`` /
``.resources`` and ``delegated_to_kdcube.providers``); nothing is invented
and nothing is written. Two-way editing would additionally need a validated
merge-write over ``delegated_credentials.oauth.resources`` (bundle-props
admin write + grant-vocabulary integrity checks); this module deliberately
stays the read side.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

__all__ = ["build_delegated_access_map"]


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def _grant_rows(capabilities: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(capabilities, (list, tuple)):
        return rows
    for raw in capabilities:
        if not isinstance(raw, Mapping):
            continue
        grant = _norm(raw.get("grant"))
        if not grant:
            continue
        row: Dict[str, Any] = {
            "grant": grant,
            "label": _norm(raw.get("label")),
            "description": _norm(raw.get("description")),
            "delegable_roles": _string_list(raw.get("delegable_roles")),
            "delegable_permissions": _string_list(raw.get("delegable_permissions")),
        }
        if raw.get("admin_only"):
            row["admin_only"] = True
        rows.append(row)
    return rows


def _tool_rows(tools: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(tools, Mapping):
        return rows
    for name in tools:
        cfg = tools.get(name)
        if not isinstance(cfg, Mapping):
            continue
        rows.append({
            "name": _norm(name),
            "label": _norm(cfg.get("label")),
            "description": _norm(cfg.get("description")),
            "grants": _string_list(cfg.get("grants")),
        })
    return rows


def _namespace_rows(namespaces: Any) -> List[Dict[str, Any]]:
    """Per-namespace exposure: the flattened operation entries with their
    grants. Generic ``call`` blocks contribute their per-operation grants to
    the namespace's grant union without duplicating the entry rows."""
    rows: List[Dict[str, Any]] = []
    if not isinstance(namespaces, Mapping):
        return rows
    for namespace in namespaces:
        cfg = namespaces.get(namespace)
        if not isinstance(cfg, Mapping):
            continue
        entries: List[Dict[str, Any]] = []
        grant_union: set[str] = set()
        tools = cfg.get("tools")
        tools = tools if isinstance(tools, Mapping) else {}
        for tool_name in tools:
            tool_cfg = tools.get(tool_name)
            if not isinstance(tool_cfg, Mapping):
                continue
            direct_grants = _string_list(tool_cfg.get("grants"))
            grant_union.update(direct_grants)
            nested_ops = tool_cfg.get("operations")
            if isinstance(nested_ops, Mapping):
                for op_cfg in nested_ops.values():
                    if isinstance(op_cfg, Mapping):
                        grant_union.update(_string_list(op_cfg.get("grants")))
            operation = _norm(tool_cfg.get("operation"))
            if not operation:
                # Generic dispatch entries (``call``) enumerate operations but
                # expose no single-operation row of their own.
                continue
            entries.append({
                "tool": _norm(tool_name),
                "operation": operation,
                "label": _norm(tool_cfg.get("label")),
                "description": _norm(tool_cfg.get("description")),
                "grants": direct_grants,
            })
        rows.append({
            "namespace": _norm(namespace),
            "label": _norm(cfg.get("label")),
            "description": _norm(cfg.get("description")),
            "authority_id": _norm(cfg.get("authority_id")),
            "entries": entries,
            "grants": sorted(grant_union),
        })
    return rows


def _resource_rows(resources: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(resources, (list, tuple)):
        return rows
    for raw in resources:
        if not isinstance(raw, Mapping):
            continue
        resource = _norm(raw.get("resource"))
        if not resource:
            continue
        tools = _tool_rows(raw.get("tools"))
        namespaces = _namespace_rows(raw.get("namespaces"))
        union: set[str] = set(_string_list(raw.get("grants")))
        for tool in tools:
            union.update(tool["grants"])
        for namespace in namespaces:
            union.update(namespace["grants"])
        row: Dict[str, Any] = {
            "resource": resource,
            "label": _norm(raw.get("label")),
            "description": _norm(raw.get("description")),
            "grants": _string_list(raw.get("grants")),
            "tools": tools,
            "namespaces": namespaces,
            "grant_union": sorted(union),
        }
        if raw.get("admin_only"):
            row["admin_only"] = True
        rows.append(row)
    return rows


def _provider_rows(providers: Any) -> List[Dict[str, Any]]:
    """Provider-backed claim vocabulary from ``delegated_to_kdcube.providers``
    — the connected-account side that sits beside the grant vocabulary for
    realms like mail/slack. Secret material never leaves the config (only
    labels, enablement, claim names/labels/descriptions)."""
    rows: List[Dict[str, Any]] = []
    if not isinstance(providers, Mapping):
        return rows
    for provider_id in providers:
        cfg = providers.get(provider_id)
        if not isinstance(cfg, Mapping):
            continue
        connector_apps: List[Dict[str, Any]] = []
        raw_apps = cfg.get("connector_apps")
        if isinstance(raw_apps, Mapping):
            for app_id in raw_apps:
                app_cfg = raw_apps.get(app_id)
                if not isinstance(app_cfg, Mapping):
                    continue
                connector_apps.append({
                    "id": _norm(app_id),
                    "label": _norm(app_cfg.get("label")),
                    "enabled": bool(app_cfg.get("enabled", True)),
                    "allowed_claims": _string_list(app_cfg.get("allowed_claims")),
                })
        claims: List[Dict[str, Any]] = []
        raw_claims = cfg.get("claims")
        if isinstance(raw_claims, Mapping):
            for claim in raw_claims:
                claim_cfg = raw_claims.get(claim)
                claim_cfg = claim_cfg if isinstance(claim_cfg, Mapping) else {}
                claims.append({
                    "claim": _norm(claim),
                    "label": _norm(claim_cfg.get("label")),
                    "description": _norm(claim_cfg.get("description")),
                })
        rows.append({
            "provider_id": _norm(provider_id),
            "label": _norm(cfg.get("label")),
            "enabled": bool(cfg.get("enabled", True)),
            "connector_apps": connector_apps,
            "claims": claims,
        })
    return rows


def build_delegated_access_map(connections: Mapping[str, Any] | None) -> Dict[str, Any]:
    """The resolved read-only map: grant vocabulary, resource exposure
    (resource -> tools / named-service namespaces -> grants), and the
    provider-backed claim vocabulary. ``unknown_grants`` flags grants that
    resources reference but the vocabulary never declares — an honest
    integrity signal for the admin reviewing the mapping."""
    connections = connections if isinstance(connections, Mapping) else {}
    delegated = connections.get("delegated_credentials")
    delegated = delegated if isinstance(delegated, Mapping) else {}
    oauth = delegated.get("oauth")
    oauth = oauth if isinstance(oauth, Mapping) else {}
    grants = _grant_rows(oauth.get("capabilities"))
    resources = _resource_rows(oauth.get("resources"))
    dtk = connections.get("delegated_to_kdcube")
    dtk = dtk if isinstance(dtk, Mapping) else {}
    providers = _provider_rows(dtk.get("providers"))
    declared = {row["grant"] for row in grants}
    referenced: set[str] = set()
    for resource in resources:
        referenced.update(resource["grant_union"])
    return {
        "enabled": bool(oauth.get("enabled")),
        "grants": grants,
        "resources": resources,
        "providers": providers,
        "unknown_grants": sorted(referenced - declared),
    }
