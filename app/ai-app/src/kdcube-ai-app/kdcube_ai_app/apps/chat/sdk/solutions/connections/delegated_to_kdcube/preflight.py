# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Runtime preflight for tool-declared delegated-to-KDCube claims."""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import quote, urlencode

from kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges import DEFAULT_CONNECTION_HUB_BUNDLE_ID
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.client import DelegatedToKdcubeClient
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    ToolClaimPolicy,
    as_str,
)


CONSENT_NEEDED_CODE = "needs_connected_account_consent"
PREFLIGHT_SCHEMA = "connection_hub.delegated_to_kdcube.tool_claim_preflight.v1"


def _clean_list(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = as_str(value)
        if text and text not in out:
            out.append(text)
    return out


def _connection_hub_widget_url(
    *,
    tenant: str,
    project: str,
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    provider_id: str = "",
    connector_app_id: str = "",
    claims: Iterable[str] = (),
    tool_name: str = "",
) -> str:
    if not tenant or not project:
        return ""
    query = {
        "tab": "delegated_to_kdcube",
    }
    if provider_id:
        query["provider_id"] = provider_id
    if connector_app_id:
        query["connector_app_id"] = connector_app_id
    claim_list = _clean_list(claims)
    if claim_list:
        query["claims"] = ",".join(claim_list)
    if tool_name:
        query["tool_name"] = tool_name
    return (
        "/api/integrations/bundles/"
        f"{quote(tenant, safe='')}/{quote(project, safe='')}/"
        f"{quote(connection_hub_bundle_id or DEFAULT_CONNECTION_HUB_BUNDLE_ID, safe='')}/"
        f"widgets/connections_settings?{urlencode(query)}"
    )


def _first_failure(missing: list[dict[str, Any]]) -> dict[str, Any]:
    for tool_result in missing:
        failures = tool_result.get("failures")
        if isinstance(failures, list):
            for failure in failures:
                if isinstance(failure, dict):
                    return {
                        "tool_name": as_str(tool_result.get("tool_name")),
                        **failure,
                    }
    return {}


def _bare_tool_label(tool_name: str) -> str:
    text = as_str(tool_name)
    return text.rsplit(".", 1)[-1] if "." in text else text


def _provider_label(provider_id: str) -> str:
    text = as_str(provider_id)
    return text[:1].upper() + text[1:] if text else "external"


def unavailable_tools_by_provider(missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group unmet-claim tool results by provider.

    Returns ``[{provider_id, provider_label, connector_app_id, claims, tools}]``
    so callers (banner text, agent notice) can name things concretely.
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for tool_result in missing:
        if not isinstance(tool_result, dict):
            continue
        tool_name = as_str(tool_result.get("tool_name"))
        failures = tool_result.get("failures")
        failure_list = [f for f in failures if isinstance(f, dict)] if isinstance(failures, list) else []
        if not failure_list:
            # user_required path carries raw policy dicts (connected_accounts).
            accounts = tool_result.get("connected_accounts")
            failure_list = [a for a in accounts if isinstance(a, dict)] if isinstance(accounts, list) else [{}]
        for failure in failure_list:
            provider_id = as_str(failure.get("provider_id"))
            connector_app_id = as_str(failure.get("connector_app_id"))
            key = (provider_id, connector_app_id)
            group = groups.setdefault(key, {
                "provider_id": provider_id,
                "provider_label": _provider_label(provider_id),
                "connector_app_id": connector_app_id,
                "claims": [],
                "tools": [],
            })
            claim = as_str(failure.get("claim"))
            if claim and claim not in group["claims"]:
                group["claims"].append(claim)
            for raw_claim in failure.get("claims") if isinstance(failure.get("claims"), list) else []:
                claim = as_str(raw_claim)
                if claim and claim not in group["claims"]:
                    group["claims"].append(claim)
            if tool_name and tool_name not in group["tools"]:
                group["tools"].append(tool_name)
    return list(groups.values())


def unavailable_tools_message(missing: list[dict[str, Any]]) -> str:
    """A user-facing notice that NAMES the provider and affected tools."""
    parts: list[str] = []
    for group in unavailable_tools_by_provider(missing):
        label = group["provider_label"]
        tools = _clean_list(_bare_tool_label(t) for t in group["tools"])
        shown = ", ".join(tools[:4]) + (", …" if len(tools) > 4 else "")
        parts.append(
            f"{label} tools are inactive ({shown}) — connect your {label} account in Connection Hub to use them."
        )
    return " ".join(parts) or (
        "Some tools are inactive until their account is connected in Connection Hub."
    )


def connected_account_consent_payload(
    *,
    tenant: str,
    project: str,
    connection_hub_bundle_id: str,
    missing: list[dict[str, Any]],
) -> dict[str, Any]:
    failure = _first_failure(missing)
    tool_names = _clean_list(item.get("tool_name") for item in missing)
    claims = _clean_list(
        failure.get("claim")
        for tool_result in missing
        for failure in (tool_result.get("failures") if isinstance(tool_result.get("failures"), list) else [])
        if isinstance(failure, dict)
    )
    provider_id = as_str(failure.get("provider_id"))
    connector_app_id = as_str(failure.get("connector_app_id"))
    tool_name = as_str(failure.get("tool_name"))
    url = _connection_hub_widget_url(
        tenant=tenant,
        project=project,
        connection_hub_bundle_id=connection_hub_bundle_id,
        provider_id=provider_id,
        connector_app_id=connector_app_id,
        claims=claims,
        tool_name=tool_name,
    )
    message = unavailable_tools_message(missing)
    return {
        "ok": False,
        "schema": PREFLIGHT_SCHEMA,
        "error": {
            "code": CONSENT_NEEDED_CODE,
            "message": message,
        },
        "consent": {
            "kind": "delegated_to_kdcube.connected_account",
            "provider_id": provider_id,
            "connector_app_id": connector_app_id,
            "claims": claims,
            "tool_id": tool_name,
            "tool_label": tool_name,
            "url": url,
            "action_label": "Open Connection Hub",
        },
        "tools": tool_names,
        "missing": missing,
    }


async def preflight_tool_claim_policies(
    *,
    entrypoint: Any,
    user_id: str,
    policies: Iterable[ToolClaimPolicy],
    tenant: str = "",
    project: str = "",
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
) -> dict[str, Any]:
    policy_list = [policy for policy in policies if policy.connected_accounts]
    if not policy_list:
        return {"ok": True, "schema": PREFLIGHT_SCHEMA, "checked": 0}
    if not as_str(user_id):
        return {
            "ok": False,
            "schema": PREFLIGHT_SCHEMA,
            "error": {
                "code": "user_required",
                "message": "A platform user is required before external account consent can be checked.",
            },
            "missing": [policy.to_dict() for policy in policy_list],
        }

    client = await DelegatedToKdcubeClient.from_connection_hub(
        entrypoint,
        user_id=user_id,
        tenant=tenant,
        project=project,
        connection_hub_bundle_id=connection_hub_bundle_id,
    )
    missing: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    for policy in policy_list:
        result = await client.ensure_tool_claims(policy=policy)
        if result.get("ok") is True:
            resolved.append(result)
        else:
            missing.append(result)
    if missing:
        return connected_account_consent_payload(
            tenant=tenant,
            project=project,
            connection_hub_bundle_id=connection_hub_bundle_id,
            missing=missing,
        )
    return {
        "ok": True,
        "schema": PREFLIGHT_SCHEMA,
        "checked": len(policy_list),
        "resolved": resolved,
    }


__all__ = [
    "CONSENT_NEEDED_CODE",
    "PREFLIGHT_SCHEMA",
    "connected_account_consent_payload",
    "preflight_tool_claim_policies",
    "unavailable_tools_by_provider",
    "unavailable_tools_message",
]
