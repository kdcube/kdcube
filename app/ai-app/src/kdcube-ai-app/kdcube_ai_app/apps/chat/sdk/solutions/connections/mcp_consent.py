# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Consent middleware for callers of a KDCube-exposed MCP surface.

An agent calling a KDCube `@mcp` surface presents its per-agent delegated-client
credential (the agent IS a "Delegated By KDCube" client entity, like Claude
Code). When the user has NOT consented to the claims that call needs, the surface
denies with a plain 403 (`authority_mismatch` / `delegated_client`) — it does NOT
speak a bespoke consent protocol. This module is the RECOMMENDED client-side
wrapper that turns that denial into:

  * a **consent-demand** the chat surface can bubble (the SAME `CONSENT_NEEDED_CODE`
    contract the connected-account/Slack path uses — a Connection Hub URL, the
    required claims, the resource), so the user can go grant the claims; and
  * an **agent-explainable** result — a short message the model reads and can act
    on ("this needs your consent to <claims>; the user grants it in Connection
    Hub"), instead of an opaque 403.

Wrap any KDCube-MCP call/load with `raise_for_mcp_consent(...)` (or catch the
denial and call `mcp_consent_from_denial(...)`); recommended whenever a bundle
binds a KDCube-served MCP tool.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.preflight import (
    CONSENT_NEEDED_CODE,
)

# The guard reasons a KDCube @mcp surface returns when the caller's per-agent
# delegated credential has no consent grant for the resource/claims. Any of these
# on a 403 means "user consent needed", not "wrong credentials".
_CONSENT_DENIAL_REASONS = {
    "authority_mismatch", "missing_role", "missing_permission",
    "credential_resource_missing", "resource_mismatch",
    "missing_bearer", "invalid_bearer",
}


class MCPConsentRequired(Exception):
    """A KDCube MCP call was denied for missing user consent on its claims.

    Carries a chat-bubbleable consent payload (`.consent`) and an
    agent-explainable message (`str(self)` / `.agent_message`)."""

    def __init__(self, *, resource: str, claims: List[str], consent: Dict[str, Any], agent_message: str) -> None:
        super().__init__(agent_message)
        self.resource = resource
        self.claims = list(claims)
        self.consent = dict(consent)
        self.agent_message = agent_message

    def to_tool_result(self) -> Dict[str, Any]:
        """The agent-facing tool result — explains the block so the model can tell
        the user, and carries the consent block for the chat surface to bubble."""
        return {
            "ok": False,
            "error": {"code": CONSENT_NEEDED_CODE, "message": self.agent_message},
            "consent": self.consent,
        }

    def chat_event_payload(self) -> Dict[str, Any]:
        """The consent event the chat renders — the SAME nested shape the
        connected-account (Slack) consent uses (`{error:{code}, consent:{…}}`), so
        one banner path serves both. The consent block carries the claims, the
        Connection Hub deep link, and (for a per-agent grant) the one-click `grant`
        action + the agent identity."""
        c = self.consent
        consent_block: Dict[str, Any] = {
            "kind": c.get("kind") or "delegated_to_kdcube.mcp",
            "reason": c.get("reason") or "consent_required",
            "claims": list(self.claims),
            "tool_id": c.get("tool_name") or "",
            "tool_label": c.get("tool_name") or "",
            "resource": self.resource,
            "url": c.get("connection_hub_url") or "",
            "action_label": "Grant access" if c.get("grant") else "Open Connection Hub",
        }
        if c.get("agent_client_id"):
            consent_block["agent_client_id"] = c["agent_client_id"]
        if c.get("namespace"):
            consent_block["namespace"] = c["namespace"]
        if c.get("operation"):
            consent_block["operation"] = c["operation"]
        if c.get("grant"):
            consent_block["grant"] = c["grant"]
        return {
            "ok": False,
            "error": {"code": CONSENT_NEEDED_CODE, "message": self.agent_message},
            "consent": consent_block,
            "tools": [c["tool_name"]] if c.get("tool_name") else [],
        }


def _status_of(denial: Any) -> Optional[int]:
    for attr in ("status_code", "status", "code"):
        v = getattr(denial, attr, None)
        if isinstance(v, int):
            return v
    if isinstance(denial, Mapping):
        for key in ("status_code", "status", "code"):
            v = denial.get(key)
            if isinstance(v, int):
                return v
    return None


def _reason_of(denial: Any) -> str:
    if isinstance(denial, Mapping):
        return str(denial.get("reason") or denial.get("error") or "").strip()
    return str(getattr(denial, "reason", "") or "").strip()


def is_kdcube_mcp_consent_denial(denial: Any, *, resource: str = "") -> bool:
    """True when a denial from a KDCube `@mcp` surface means the user must consent.

    A 403 is the signal; the guard reason (when present) narrows it to a
    consent/authority denial rather than an unrelated error. A bare 403 with no
    reason on a KDCube mcp resource is treated as consent-needed (fail toward
    surfacing consent rather than swallowing the block)."""
    if _status_of(denial) not in (401, 403):
        return False
    reason = _reason_of(denial)
    if reason and reason not in _CONSENT_DENIAL_REASONS:
        return False
    return True


# The REST operation (Connection Hub) that grants a hosted agent access to a
# resource — the consent action behind a pending agent MCP demand.
AGENT_GRANT_CREATE_OPERATION = "delegated_agent_grant_create"
# Marks a demand as a per-agent DELEGATED grant (the user grants THIS agent), so
# a consent surface renders a "Grant" action rather than a connect-an-account flow.
CONSENT_KIND_AGENT_GRANT = "delegated_agent_grant"


def mcp_consent_from_denial(
    denial: Any,
    *,
    resource: str,
    claims: Iterable[str],
    connection_hub_url: str = "",
    tool_name: str = "",
    agent_client_id: str = "",
    namespace: str = "",
    operation: str = "",
) -> MCPConsentRequired:
    """Build the `MCPConsentRequired` from a KDCube-MCP denial + the connection's
    declared claims. The claims come from the caller (the `kind: mcp` connection's
    `scopes`); the surface's 403 doesn't enumerate them.

    ``agent_client_id`` (the calling agent's ``kdcube-agent:<app>:<agent>``
    identity) makes the demand actionable: the payload carries a ``grant`` block —
    the Connection Hub operation + args — a consent surface POSTs to grant THIS
    agent the claims, distinct from a connect-an-account flow.

    ``namespace`` / ``operation`` name the inner capability the call wanted.
    Approval grants that operation, not every operation its claims allow."""
    claim_list = [str(c).strip() for c in (claims or []) if str(c).strip()]
    label = tool_name or resource.rsplit("/", 1)[-1] or "this tool"
    claims_str = ", ".join(claim_list) or "the required access"
    asked = f"the operation {operation}" if operation else ""
    if asked and claim_list:
        asked = f"{asked} and {claims_str}"
    asked = asked or claims_str
    agent_message = (
        f"{label} needs the user's consent to {asked}. It is blocked until the "
        f"user grants it in Connection Hub (Delegated by KDCube). Tell the user you "
        f"need their approval for {asked}; do not retry until they grant it."
    )
    consent: Dict[str, Any] = {
        "code": CONSENT_NEEDED_CODE,
        "reason": _reason_of(denial) or "consent_required",
        "resource": resource,
        "claims": claim_list,
    }
    if connection_hub_url:
        consent["connection_hub_url"] = connection_hub_url
    if tool_name:
        consent["tool_name"] = tool_name
    if namespace:
        consent["namespace"] = namespace
    if operation:
        consent["operation"] = operation
    if agent_client_id:
        # The one-click grant action for this demand (user grants THIS agent).
        consent["kind"] = CONSENT_KIND_AGENT_GRANT
        consent["agent_client_id"] = agent_client_id
        grant_payload: Dict[str, Any] = {
            "client_id": agent_client_id,
            "resource": resource,
            "claims": claim_list,
        }
        if namespace and operation:
            grant_payload["named_service_operations"] = {namespace: [operation]}
        consent["grant"] = {
            "operation": AGENT_GRANT_CREATE_OPERATION,
            "payload": grant_payload,
        }
    return MCPConsentRequired(
        resource=resource, claims=claim_list, consent=consent, agent_message=agent_message,
    )


async def announce_agent_consent(consent: "MCPConsentRequired") -> None:
    """Raise ONE pending per-agent consent as the standard chat consent banner.

    Call this at the tool ATTEMPT (consent is demand-driven per tool). Records
    the demand once per conversation; on later attempts, while the block is
    still real, the same consent event re-emits directly — the chat UI keeps
    one banner per identical demand and honors a dismissal, so what the user
    sees always matches what the agent says. Best effort; never raises."""
    import logging

    log = logging.getLogger(__name__)
    try:
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
            announce_consent_demand,
        )

        payload = consent.chat_event_payload()
        announced = await announce_consent_demand(
            payload=payload,
            provider_id="kdcube",
            # The connector slot carries the agent's client id so the granted
            # event on approval closes THIS agent's demand, not every agent's.
            connector_app_id=str(consent.consent.get("agent_client_id") or ""),
            claims=list(consent.claims or []),
            tool_name=str(consent.consent.get("tool_name") or ""),
        )
        if announced:
            log.info(
                "[agent-consent] demand announced as banner: claims=%s tool=%s agent=%s",
                consent.claims, consent.consent.get("tool_name"), consent.consent.get("agent_client_id"),
            )
        else:
            # Not newly recorded (re-attempt of a pending demand, or no full
            # conversation address) — re-emit the event directly so the chat
            # surface still shows/refreshes the banner.
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_comm

            communicator = get_comm()
            event = getattr(communicator, "event", None) if communicator is not None else None
            if callable(event):
                result = event(
                    agent="connection-hub",
                    type="chat.step",
                    route="chat.step",
                    title="Access consent needed",
                    step="delegated_to_kdcube.consent",
                    data=dict(payload or {}),
                    status="completed",
                    broadcast=False,
                )
                if hasattr(result, "__await__"):
                    await result
                log.info(
                    "[agent-consent] demand re-emitted directly (not newly recorded): claims=%s tool=%s",
                    consent.claims, consent.consent.get("tool_name"),
                )
            else:
                log.warning(
                    "[agent-consent] demand NOT announced: no communicator bound and record path declined "
                    "(claims=%s tool=%s agent=%s) — the user sees no banner",
                    consent.claims, consent.consent.get("tool_name"), consent.consent.get("agent_client_id"),
                )
    except Exception:
        log.warning("agent consent announce failed (non-fatal)", exc_info=True)


def raise_for_mcp_consent(
    denial: Any,
    *,
    resource: str,
    claims: Iterable[str],
    connection_hub_url: str = "",
    tool_name: str = "",
    agent_client_id: str = "",
) -> None:
    """Raise `MCPConsentRequired` when `denial` is a KDCube-MCP consent denial;
    return silently otherwise. Call this around a KDCube-MCP call/load with the
    error (or a status/reason mapping) you caught."""
    if is_kdcube_mcp_consent_denial(denial, resource=resource):
        raise mcp_consent_from_denial(
            denial, resource=resource, claims=claims,
            connection_hub_url=connection_hub_url, tool_name=tool_name,
            agent_client_id=agent_client_id,
        )
