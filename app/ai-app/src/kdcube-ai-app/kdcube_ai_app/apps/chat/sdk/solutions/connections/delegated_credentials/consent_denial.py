# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The consent breakdown ANY KDCube-served MCP surface returns on a per-agent
grant miss.

A surface guarded by the delegated-client boundary denies an operation whose
grants the caller's bearer lacks. The denial is a CONTRACT, uniform across
surfaces: it names the exact missing grants, and for a hosted-agent caller
(`kdcube-agent:<app>:<agent>`) it carries the full consent block — agent
identity, the granted resource, the claims, and the one-click
`delegated_agent_grant_create` action — so the caller's chat surface raises
the scoped demand without knowing anything about the specific service. The
agent identity and resource come from the REQUEST's own bound credential
(the grant record's client id, or the delegate subject; the credential's
granted resource), so every surface answers identically with one call.

External OAuth clients (Claude Code) keep the reconnect / incremental-consent
guidance instead — their consent loop is the OAuth journey, not a chat banner.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.credential_view import (
    delegated_credential_view,
)

LOGGER = logging.getLogger(__name__)

AGENT_CLIENT_PREFIX = "kdcube-agent:"
CONSENT_NEEDED_CODE = "connections.consent_needed"
DELEGATED_CONSENT_REQUIRED = "delegated_consent_required"


def agent_client_id_from_request(request: Any) -> str:
    """The caller's ``kdcube-agent:<app>:<agent>`` identity, or "" for other
    client families. Thin over the one canonical credential view."""
    return delegated_credential_view(request).agent_client_id


def granted_resource_from_request(request: Any) -> str:
    """The delegated-resource id this bearer was granted under, or "". Thin over
    the one canonical credential view (which knows both envelope shapes)."""
    return delegated_credential_view(request).resource


def connection_hub_grant_url(
    *,
    tenant: str,
    project: str,
    client_id: str,
    resource: str,
    claims: Sequence[str],
    hub_bundle_id: str = "connection-hub@1-0",
    account_id: str = "",
    account_claim: str = "",
) -> str:
    """An absolute Connection Hub deep link that lands on the Delegated by
    KDCube tab with THIS client's access request focused (the pending pane:
    missing claims pre-checked, one-click grant).

    ``account_id`` / ``account_claim`` focus a PER-ACCOUNT ask (the connected
    account can do it, the agent is not bound): the card explains why the user
    landed there, names the exact account+claim to tick, and opens that
    provider's section — ticking is always the user's own action, nothing is
    pre-checked.

    Openable outside the app origin — an external agent (Claude Code) relays
    it verbatim; the user signs in with their platform credentials and sees the
    focused card. Empty when the deployment's public base URL is unknown."""
    from urllib.parse import quote, urlencode

    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.public_base import (
        connection_hub_public_base_url,
    )

    base = connection_hub_public_base_url()
    if not base or not tenant or not project or not client_id or not resource:
        return ""
    query: dict[str, str] = {
        "tab": "delegated_by_kdcube",
        "pending_agent_grant": "1",
        "agent_client_id": client_id,
        "resource": resource,
        "claims": ",".join(str(c) for c in claims if str(c or "").strip()),
    }
    if str(account_id or "").strip():
        query["account_id"] = str(account_id).strip()
    if str(account_claim or "").strip():
        query["account_claim"] = str(account_claim).strip()
    params = urlencode(query)
    return (
        f"{base}/api/integrations/bundles/"
        f"{quote(str(tenant), safe='')}/{quote(str(project), safe='')}/"
        f"{quote(hub_bundle_id, safe='')}/widgets/connections_settings?{params}"
    )


def agent_grant_consent_denial(
    request: Any,
    *,
    namespace: str,
    tool: str,
    operation: str,
    required: Sequence[str],
    missing: Sequence[str],
    available: Sequence[str],
    message: str = "",
    tenant: str = "",
    project: str = "",
) -> dict[str, Any]:
    """The uniform per-agent grant denial for a KDCube-served MCP surface.

    Always names the exact grants; a hosted-agent caller additionally gets the
    full consent block + Connection Hub next step, other client families get
    the reconnect guidance."""
    missing_list = sorted({str(c) for c in missing if str(c or "").strip()})
    denial: dict[str, Any] = {
        "ok": False,
        "error": DELEGATED_CONSENT_REQUIRED,
        "message": message or (
            f"'{tool}' on '{namespace}' requires additional delegated consent."
        ),
        "namespace": namespace,
        "tool": tool,
        "operation": operation,
        "required_grants": sorted({str(c) for c in required if str(c or "").strip()}),
        "missing_grants": missing_list,
        "available_grants": sorted({str(c) for c in available if str(c or "").strip()}),
        "next_step": (
            "Reconnect this MCP resource and approve the missing grant if the "
            "client supports incremental consent. Otherwise connect a resource "
            "whose initial consent includes this grant."
        ),
    }
    view = delegated_credential_view(request)
    client_id = view.agent_client_id
    # Any OTHER delegated client (an external app connected via OAuth — Claude
    # Code) gets the SAME focused path: the hub deep link below lands on its
    # card with the missing claims pre-checked.
    external_client_id = "" if client_id else view.client_id
    resource = view.resource
    hub_url = connection_hub_grant_url(
        tenant=tenant,
        project=project,
        client_id=client_id or external_client_id,
        resource=resource,
        claims=missing_list,
    )
    if not client_id and not external_client_id:
        LOGGER.info(
            "[agent-consent-denial] caller carries no delegated client identity; "
            "reconnect guidance kept (namespace=%s tool=%s)",
            namespace, tool,
        )
        return denial
    denial["code"] = CONSENT_NEEDED_CODE
    consent: dict[str, Any] = {
        "kind": "delegated_agent_grant",
        "reason": DELEGATED_CONSENT_REQUIRED,
        "agent_client_id": client_id or external_client_id,
        "resource": resource,
        "claims": missing_list,
        "tool_name": namespace,
        # Self-describing contract: the block names its namespace so a consumer
        # never re-derives it.
        "namespace": namespace,
    }
    if hub_url:
        consent["connection_hub_url"] = hub_url
        denial["connection_hub_url"] = hub_url
    if client_id:
        # The one-click grant action rides only for hosted agents today; an
        # external client's approval flows through the hub link (its record
        # extension is the hub's own operation).
        consent["grant"] = {
            "operation": "delegated_agent_grant_create",
            "payload": {"client_id": client_id, "resource": resource, "claims": missing_list},
        }
        denial["next_step"] = (
            "The user extends this agent's grant with the missing access in "
            "Connection Hub (Delegated by KDCube); the chat consent card carries "
            "the one-click grant."
        )
    else:
        denial["next_step"] = (
            "Give the user this link: they sign in with their platform account "
            "and approve the missing access for this client in Connection Hub "
            "(Delegated by KDCube). Reconnecting with incremental consent also "
            "works when this client supports it."
            if hub_url
            else denial["next_step"]
        )
        denial["instructions"] = (
            f"Ask the user to open {hub_url} and approve: {', '.join(missing_list)}. "
            "Then retry the same call."
        ) if hub_url else denial.get("instructions", "")
    denial["consent"] = consent
    if not resource:
        LOGGER.warning(
            "[agent-consent-denial] consent block has NO resource (client=%s namespace=%s) — "
            "the caller must fill it from its connection declaration",
            client_id or external_client_id, namespace,
        )
    return denial


__all__ = [
    "agent_client_id_from_request",
    "agent_grant_consent_denial",
    "granted_resource_from_request",
    "CONSENT_NEEDED_CODE",
    "DELEGATED_CONSENT_REQUIRED",
]
