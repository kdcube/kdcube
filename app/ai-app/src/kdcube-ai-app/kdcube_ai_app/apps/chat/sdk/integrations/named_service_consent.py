# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Consent/reconnect error shaping shared by integration named services.

Integration named-service providers (``mail``, ``slack``, future namespaces)
are adapters: claim resolution belongs to the delegated-to-KDCube broker and
the consent payload belongs to Connection Hub preflight. This module carries
those contract fields — ``reason``, ``retry_hint``, labeled ``candidates``,
the Connection Hub URL — verbatim into ``NamedServiceResponse`` errors so
chat, API, and MCP clients all see the same actionable shape.
"""

from __future__ import annotations

from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    CREDENTIAL_ACTIVE,
    CREDENTIAL_MISSING,
    CREDENTIAL_REVOKED,
    REASON_ACCOUNT_REQUIRED,
    REASON_CLAIM_UPGRADE_REQUIRED,
    REASON_CONNECT_REQUIRED,
    REASON_RECONNECT_REQUIRED,
    STATUS_REVOKED,
    ClaimResolution,
    ConnectedAccount,
    as_str,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.preflight import (
    CONSENT_NEEDED_CODE,
    connected_account_consent_payload,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
    NamedServiceResponse,
)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


# Shared schema language. Every integration namespace advertises the same
# account-selection behavior and the same consent-error shape, so an agent
# that learned one namespace can operate the next one unchanged.
ACCOUNT_SELECTION_CONTRACT = {
    "list": (
        "object.list returns every connected account with label, email/workspace, "
        "approved claims, and credential_status."
    ),
    "search": (
        "object.search with no account_id fans out across every account holding the "
        "required claim; every hit carries account_id and account_label. "
        "Pass filters.account_id to search one account."
    ),
    "get": "Object refs embed the account, so object.get needs no account context beyond the ref itself.",
    "action": (
        "Actions use the account embedded in the object_ref or payload.account_id. When several "
        "accounts match, the call fails with reason=account_required and labeled candidates; "
        "an account is never picked silently."
    ),
}

CONSENT_ERROR_CONTRACT = {
    "code": CONSENT_NEEDED_CODE,
    "status": 403,
    "reasons": {
        REASON_CONNECT_REQUIRED: "No eligible connected account. Open connection_hub_url to connect one, then retry.",
        REASON_CLAIM_UPGRADE_REQUIRED: (
            "An account is connected but has not approved the required claim. "
            "Approve it via connection_hub_url, then retry."
        ),
        REASON_RECONNECT_REQUIRED: (
            "The account's stored credential no longer works. Reconnect it via connection_hub_url, then retry."
        ),
        REASON_ACCOUNT_REQUIRED: (
            "Several connected accounts match. Resend the same call with account_id set to one of "
            "candidates[].account_id."
        ),
    },
    "fields": [
        "reason",
        "retry_hint",
        "provider_id",
        "connector_app_id",
        "claims",
        "account_id",
        "candidates",
        "connection_hub_url",
        "action_label",
        "instructions",
    ],
    "candidates": "Labeled account summaries: {account_id, label, email, workspace, status, claims}.",
    "retry_hint": (
        "true when retrying the same call after the Connection Hub action "
        "(or resending with account_id) should succeed."
    ),
    "instructions": (
        "Agent-facing next step for this exact failure: show the user "
        "connection_hub_url, name the claims, retry the same call after approval."
    ),
}


def _consent_instructions(
    *,
    reason: str,
    provider_id: str,
    claims: list[str],
    connection_hub_url: str,
) -> str:
    """The agent-facing next step for one consent failure.

    This surface has no chat banner: the consent loop is this response, the
    Connection Hub link, and a retry. The wording tells the agent exactly
    that — per reason, scoped to the attempted action's claims."""
    provider_label = (provider_id[:1].upper() + provider_id[1:]) if provider_id else "the provider"
    claim_text = ", ".join(claims) if claims else "the required access"
    # Only an ABSOLUTE URL is openable by the user this text reaches (an
    # external agent relays it verbatim, outside the app origin). A relative
    # path names the destination instead of handing out a dead link.
    link_is_openable = connection_hub_url.startswith(("http://", "https://"))
    link_part = (
        f"Share this Connection Hub link with the user: {connection_hub_url}"
        if link_is_openable
        else "Ask the user to open Connection Hub in the KDCube app"
    )
    if reason == REASON_ACCOUNT_REQUIRED:
        return (
            f"Several {provider_label} accounts match. Resend the SAME call with "
            "account_id set to one of candidates[].account_id — no consent action needed."
        )
    if reason == REASON_RECONNECT_REQUIRED:
        return (
            f"The connected {provider_label} account's credential no longer works. "
            f"{link_part} to reconnect it ({claim_text}), then retry this exact call."
        )
    if reason == REASON_CLAIM_UPGRADE_REQUIRED:
        return (
            f"A {provider_label} account is connected but has not approved {claim_text}. "
            f"{link_part} to approve exactly those claims, then retry this exact call."
        )
    return (
        f"No {provider_label} account is connected for {claim_text}. "
        f"{link_part} to connect one and approve those claims, then retry this exact call."
    )


def account_credential_status(account: ConnectedAccount) -> str:
    """Credential health for one connected account, without reading credentials.

    Health transitions (refresh failure, live provider rejection, revoke) are
    persisted in account metadata as ``credential_status`` by the Connection
    Hub store; adapters read that. Accounts with no recorded transition fall
    back to their lifecycle state.
    """
    persisted = as_str((account.metadata or {}).get("credential_status"))
    if persisted:
        return persisted
    if account.status == STATUS_REVOKED:
        return CREDENTIAL_REVOKED
    if not account.credential_id:
        return CREDENTIAL_MISSING
    return CREDENTIAL_ACTIVE


def consent_details(consent: Mapping[str, Any]) -> dict[str, Any]:
    """Hoist the actionable contract fields out of one consent block.

    MCP clients read these without digging into the nested consent payload:
    which provider, which claims, why it failed, where to fix it, and whether
    retrying after the fix should work.
    """
    data = _as_dict(consent)
    candidates = data.get("candidates")
    reason = as_str(data.get("reason"))
    provider_id = as_str(data.get("provider_id"))
    claims = [as_str(item) for item in data.get("claims") or [] if as_str(item)]
    connection_hub_url = as_str(data.get("url"))
    return {
        "reason": reason,
        "retry_hint": bool(data.get("retry_hint")),
        "provider_id": provider_id,
        "connector_app_id": as_str(data.get("connector_app_id")),
        "claims": claims,
        "account_id": as_str(data.get("account_id")),
        "candidates": [dict(item) for item in candidates if isinstance(item, Mapping)] if isinstance(candidates, list) else [],
        "connection_hub_url": connection_hub_url,
        "action_label": as_str(data.get("action_label")),
        "instructions": _consent_instructions(
            reason=reason,
            provider_id=provider_id,
            claims=claims,
            connection_hub_url=connection_hub_url,
        ),
    }


def resolution_consent_payload(
    *,
    resolution: ClaimResolution,
    ctx: NamedServiceContext,
    connection_hub_bundle_id: str,
    tool_name: str,
) -> dict[str, Any]:
    """Build the shipped Connection Hub consent payload for one failed resolution."""
    return connected_account_consent_payload(
        tenant=ctx.tenant,
        project=ctx.project,
        connection_hub_bundle_id=connection_hub_bundle_id,
        missing=[
            {
                "ok": False,
                "tool_name": tool_name,
                "failures": [resolution.to_dict(include_credential=False)],
            }
        ],
    )


def consent_error_response(
    *,
    resolution: ClaimResolution,
    ctx: NamedServiceContext,
    request: NamedServiceRequest,
    namespace: str,
    provider_identity: Mapping[str, Any],
    connection_hub_bundle_id: str,
    tool_name: str,
) -> NamedServiceResponse:
    """Translate a failed ``ClaimResolution`` into a named-service error.

    The error code is ``needs_connected_account_consent`` — the shipped chat
    contract — and the details carry the resolution fields verbatim plus the
    full consent block (Connection Hub URL, action label, candidates).
    """
    payload = resolution_consent_payload(
        resolution=resolution,
        ctx=ctx,
        connection_hub_bundle_id=connection_hub_bundle_id,
        tool_name=tool_name,
    )
    consent = _as_dict(payload.get("consent"))
    message = as_str(_as_dict(payload.get("error")).get("message")) or as_str(resolution.message) or (
        "Connect or reconnect the required external account in Connection Hub."
    )
    details = consent_details(consent)
    details["consent"] = consent
    return NamedServiceResponse.error_response(
        code=CONSENT_NEEDED_CODE,
        message=message,
        status=403,
        details=details,
        provider=dict(provider_identity),
        namespace=request.namespace or namespace,
        object_ref=request.object_ref,
    )


def _tool_consent_block(result: Mapping[str, Any], error: Mapping[str, Any], ret: Any) -> dict[str, Any]:
    consent = result.get("consent") or error.get("consent")
    if not consent and isinstance(ret, Mapping):
        consent = ret.get("consent")
    return _as_dict(consent)


def tool_error_response(
    result: Mapping[str, Any],
    *,
    request: NamedServiceRequest,
    namespace: str,
    provider_identity: Mapping[str, Any],
    default_code: str,
    fallback_message: str,
    extra_details: Mapping[str, Any] | None = None,
) -> NamedServiceResponse:
    """Translate one integration tool error envelope into a named-service error.

    Consent/reconnect envelopes minted by the tool layer (the broker already
    chose the reason and candidates) pass through with status 403 and the
    contract fields hoisted; every other failure is a plain 400.
    """
    error = _as_dict(result.get("error"))
    ret = result.get("ret") if result.get("ret") is not None else {}
    code = as_str(error.get("code")) or (as_str(result.get("error")) if not isinstance(result.get("error"), Mapping) else "") or default_code
    message = as_str(error.get("message")) or as_str(result.get("message")) or fallback_message
    details: dict[str, Any] = {"ret": ret}
    if extra_details:
        details.update(dict(extra_details))
    consent = _tool_consent_block(result, error, ret)
    status = 400
    if consent or code == CONSENT_NEEDED_CODE:
        status = 403
        details.update(consent_details(consent))
        details["consent"] = consent
    return NamedServiceResponse.error_response(
        code=code,
        message=message,
        status=status,
        details=details,
        provider=dict(provider_identity),
        namespace=request.namespace or namespace,
        object_ref=request.object_ref,
    )


__all__ = [
    "ACCOUNT_SELECTION_CONTRACT",
    "CONSENT_ERROR_CONTRACT",
    "CONSENT_NEEDED_CODE",
    "account_credential_status",
    "consent_details",
    "consent_error_response",
    "resolution_consent_payload",
    "tool_error_response",
]
