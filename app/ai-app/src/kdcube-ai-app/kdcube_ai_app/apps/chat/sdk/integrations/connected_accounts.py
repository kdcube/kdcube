# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connected-account helpers for SDK integration tools.

Application tool modules use this layer to resolve provider credentials for the
current platform user. The helper delegates registry/storage work to Connection
Hub and returns the same consent-needed envelope the chat UI understands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_user_identity
from kdcube_ai_app.apps.chat.sdk.runtime.tool_module_bindings import get_bound_context
from kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges import DEFAULT_CONNECTION_HUB_BUNDLE_ID
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
    ClaimResolution,
    DelegatedToKdcubeClient,
    connected_account_consent_payload,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _consent_url(consent: Mapping[str, Any]) -> str:
    return _clean(
        consent.get("url")
        or consent.get("consent_url")
        or consent.get("connect_url")
        or consent.get("action_url")
    )


class _ToolEntrypoint:
    def __init__(self, *, service: Any, registry: Mapping[str, Any], comm_context: Any) -> None:
        self.service = service
        self.registry = dict(registry or {})
        self.redis = self.registry.get("redis") or getattr(service, "redis", None)
        self.bundle_props = self.registry.get("bundle_props") or getattr(service, "bundle_props", None)
        self.comm_context = comm_context or self.registry.get("comm_context") or getattr(service, "comm_context", None)

    def runtime_identity(self) -> dict[str, str]:
        actor = getattr(self.comm_context, "actor", None)
        return {
            "tenant": _clean(getattr(actor, "tenant_id", "")),
            "project": _clean(getattr(actor, "project_id", "")),
        }


@dataclass(frozen=True)
class ConnectedAccountCredential:
    ok: bool
    access_token: str = ""
    raw_credential: dict[str, Any] = field(default_factory=dict)
    account_id: str = ""
    provider_id: str = ""
    connector_app_id: str = ""
    claim: str = ""
    tool_name: str = ""
    tenant: str = ""
    project: str = ""
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID
    error_payload: dict[str, Any] = field(default_factory=dict)

    def error_envelope(self, *, where: str) -> dict[str, Any]:
        error = _as_dict(self.error_payload.get("error"))
        consent = _as_dict(self.error_payload.get("consent"))
        provider = _clean(self.provider_id) or _clean(consent.get("provider_id"))
        provider_label = (provider[:1].upper() + provider[1:]) if provider else ""
        tool = _clean(self.tool_name).rsplit(".", 1)[-1]
        fallback = (
            f"{provider_label} tools are inactive"
            + (f" ({tool})" if tool else "")
            + f" — connect your {provider_label} account in Connection Hub to use them."
        ) if provider_label else "Connect the account this tool uses in Connection Hub."
        message = _clean(error.get("message")) or fallback
        action_url = _consent_url(consent)
        action_label = _clean(consent.get("action_label")) or "Open Connection Hub"
        envelope = {
            "ok": False,
            "error": {
                "code": _clean(error.get("code")) or "needs_connected_account_consent",
                "message": message,
                "where": where,
                "managed": True,
                "consent": consent,
                "action_label": action_label,
                "action_url": action_url,
            },
            "ret": self.error_payload or {
                "ok": False,
                "message": message,
            },
        }
        if consent:
            envelope["consent"] = consent
        if action_url:
            envelope["action_label"] = action_label
            envelope["action_url"] = action_url
            ret = envelope.get("ret")
            if isinstance(ret, dict):
                ret["action_label"] = action_label
                ret["action_url"] = action_url
        return envelope

    def consent_required_envelope(self, *, where: str, message: str = "") -> dict[str, Any]:
        """Build the standard Connection Hub consent envelope for a live tool failure."""

        if self.error_payload:
            return self.error_envelope(where=where)
        text = _clean(message) or "Reconnect or approve the required external account in Connection Hub."
        payload = connected_account_consent_payload(
            tenant=self.tenant,
            project=self.project,
            connection_hub_bundle_id=self.connection_hub_bundle_id,
            missing=[
                {
                    "ok": False,
                    "tool_name": self.tool_name or where,
                    "failures": [
                        {
                            "ok": False,
                            "provider_id": self.provider_id,
                            "connector_app_id": self.connector_app_id,
                            "claim": self.claim,
                            "account_id": self.account_id,
                            "error": "provider_authorization_required",
                            "message": text,
                        }
                    ],
                }
            ],
        )
        payload["error"] = {
            **_as_dict(payload.get("error")),
            "code": "needs_connected_account_consent",
            "message": text,
        }
        return ConnectedAccountCredential(
            ok=False,
            account_id=self.account_id,
            provider_id=self.provider_id,
            connector_app_id=self.connector_app_id,
            claim=self.claim,
            tool_name=self.tool_name or where,
            tenant=self.tenant,
            project=self.project,
            connection_hub_bundle_id=self.connection_hub_bundle_id,
            error_payload=payload,
        ).error_envelope(where=where)


def _scope(source: Mapping[str, Any] | Any) -> tuple[_ToolEntrypoint, str, str, str]:
    bound = get_bound_context(source)
    registry = _as_dict(getattr(bound.tool_subsystem, "registry", None))
    comm_context = registry.get("comm_context") or getattr(bound.service, "comm_context", None)
    identity = get_current_user_identity()
    if not identity:
        actor = getattr(comm_context, "actor", None)
        user = getattr(comm_context, "user", None)
        identity = {
            "tenant_id": getattr(actor, "tenant_id", None),
            "project_id": getattr(actor, "project_id", None),
            "user_id": getattr(user, "user_id", None),
        }
    tenant = _clean(identity.get("tenant_id"))
    project = _clean(identity.get("project_id"))
    user_id = _clean(identity.get("user_id"))
    entrypoint = _ToolEntrypoint(service=bound.service, registry=registry, comm_context=comm_context)
    return entrypoint, tenant, project, user_id


async def resolve_connected_account_claim(
    source: Mapping[str, Any] | Any,
    *,
    provider_id: str,
    connector_app_id: str,
    claim: str,
    tool_name: str,
    account_id: str = "",
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
) -> ConnectedAccountCredential:
    """Resolve one provider claim for the current tool invocation."""

    entrypoint, tenant, project, user_id = _scope(source)
    if not user_id:
        payload = connected_account_consent_payload(
            tenant=tenant,
            project=project,
            connection_hub_bundle_id=connection_hub_bundle_id,
            missing=[
                {
                    "ok": False,
                    "tool_name": tool_name,
                    "failures": [
                        {
                            "ok": False,
                            "provider_id": provider_id,
                            "connector_app_id": connector_app_id,
                            "claim": claim,
                            "account_id": account_id,
                            "error": "user_required",
                            "message": "A platform user is required before external account credentials can be used.",
                        }
                    ],
                }
            ],
        )
        return ConnectedAccountCredential(
            ok=False,
            provider_id=provider_id,
            connector_app_id=connector_app_id,
            claim=claim,
            tool_name=tool_name,
            tenant=tenant,
            project=project,
            connection_hub_bundle_id=connection_hub_bundle_id,
            error_payload=payload,
        )

    client = await DelegatedToKdcubeClient.from_connection_hub(
        entrypoint,
        user_id=user_id,
        tenant=tenant,
        project=project,
        connection_hub_bundle_id=connection_hub_bundle_id,
    )
    result: ClaimResolution = await client.ensure_claim(
        provider_id=provider_id,
        connector_app_id=connector_app_id,
        claim=claim,
        account_id=account_id,
    )
    if not result.ok or result.credential is None:
        payload = connected_account_consent_payload(
            tenant=tenant,
            project=project,
            connection_hub_bundle_id=connection_hub_bundle_id,
            missing=[
                {
                    "ok": False,
                    "tool_name": tool_name,
                    "failures": [result.to_dict(include_credential=False)],
                }
            ],
        )
        return ConnectedAccountCredential(
            ok=False,
            account_id=result.account_id,
            provider_id=result.provider_id,
            connector_app_id=result.connector_app_id,
            claim=result.claim,
            tool_name=tool_name,
            tenant=tenant,
            project=project,
            connection_hub_bundle_id=connection_hub_bundle_id,
            error_payload=payload,
        )

    raw = dict(result.credential.credential or {})
    return ConnectedAccountCredential(
        ok=True,
        access_token=_clean(raw.get("access_token") or raw.get("token")),
        raw_credential=raw,
        account_id=result.account_id,
        provider_id=result.provider_id,
        connector_app_id=result.connector_app_id,
        claim=result.claim,
        tool_name=tool_name,
        tenant=tenant,
        project=project,
        connection_hub_bundle_id=connection_hub_bundle_id,
    )


__all__ = [
    "ConnectedAccountCredential",
    "resolve_connected_account_claim",
]
