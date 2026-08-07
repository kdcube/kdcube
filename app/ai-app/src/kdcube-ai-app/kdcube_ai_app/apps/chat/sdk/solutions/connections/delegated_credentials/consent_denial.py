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

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
    AUTOMATION_CLIENT_PREFIX,
)
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
    # The pending pane's only save is delegated_agent_grant_create, which serves
    # hosted agents and OAuth clients. A manual automation record is keyed by a
    # random access_id that path cannot reach, so opening the pane would hand
    # the user a save that always fails. Focus the existing card instead: the
    # manual access is edited in place under its own update operation.
    if client_id.startswith(f"{AUTOMATION_CLIENT_PREFIX}:"):
        access_id = client_id.split(":", 2)[1]
        query = {
            "tab": "delegated_by_kdcube",
            "manual_access_id": access_id,
            "resource": resource,
            "claims": ",".join(str(c) for c in claims if str(c or "").strip()),
        }
        if str(account_id or "").strip():
            query["account_id"] = str(account_id).strip()
        if str(account_claim or "").strip():
            query["account_claim"] = str(account_claim).strip()
        return (
            f"{base}/api/integrations/bundles/"
            f"{quote(str(tenant), safe='')}/{quote(str(project), safe='')}/"
            f"{quote(hub_bundle_id, safe='')}/widgets/connections_settings?"
            f"{urlencode(query)}"
        )
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
    # An external OAuth client (Claude Code) takes the SAME focused path: the
    # deep link lands on its card with the missing claims pre-checked, and its
    # record extends through extend_client_access.
    #
    # A manual automation client does not. Its record is keyed by a random
    # access_id that the deterministic extend path cannot reach, so the pending
    # pane's save would fail; it is edited by hand, which is how it is issued.
    external_client_id = "" if client_id else view.client_id
    resource = view.resource
    # connection_hub_grant_url decides which client families reach the pending
    # pane; an automation client lands on the tab instead.
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


async def connect_first_denial(
    request: Any,
    *,
    namespace: str,
    tool: str,
    operation: str,
    required: Sequence[str],
    missing: Sequence[str],
    tenant: str,
    project: str,
    hub_bundle_id: str = "connection-hub@1-0",
) -> dict[str, Any] | None:
    """Demand ordering for an account-backed operation: connect comes first.

    A gate-1 (agent grant) miss on an operation whose realm requires a
    connected account is meaningless to fix while the grantor has ZERO
    accounts on the backing provider - granting an agent access to a provider
    with no accounts binds nothing. In that state the demand LEADS with the
    connect flow (the guided plan, which ends in the agent-grant hand-off);
    the agent-grant demand is raised only when an account exists and the
    agent is merely unbound.

    Returns the gate-2 ``needs_connected_account_consent``/``connect_required``
    denial to send instead of the gate-1 denial, or None when ordering does
    not apply (no account-backed realm behind this operation, an account
    exists, or state cannot be read) - the caller then falls back to the
    gate-1 denial unchanged.
    """
    view = delegated_credential_view(request)
    return await connect_first_denial_for_identity(
        grantor_user_id=str(view.grantor_user_id or "").strip(),
        agent_client_id=view.agent_client_id,
        agent_resource=view.resource,
        namespace=namespace,
        tool=tool,
        operation=operation,
        required=required,
        missing=missing,
        tenant=tenant,
        project=project,
        hub_bundle_id=hub_bundle_id,
    )


async def connect_first_denial_for_identity(
    *,
    grantor_user_id: str,
    agent_client_id: str,
    agent_resource: str,
    namespace: str,
    tool: str,
    operation: str,
    required: Sequence[str],
    missing: Sequence[str],
    tenant: str,
    project: str,
    hub_bundle_id: str = "connection-hub@1-0",
    requirements: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """Core of :func:`connect_first_denial` for callers that already hold the
    identity explicitly (the workspace-side native-tools agent gate shapes its
    demand BEFORE any door call, so there is no request credential to view).

    ``requirements`` lets a caller that already KNOWS its connected-account
    requirements pass them directly - each mapping is the provider
    self-description shape ``{provider_id, claims, claims_by_operation?}``.
    When provided, named-service discovery is skipped entirely, which makes
    this helper usable from a plain MCP tool that declares its own
    requirements (no named-service registration behind it). The per-operation
    scoping, flat-claims narrowing to ``missing``, and the
    unmapped-operation-falls-back-to-declared-claims behavior are identical
    on both paths. An empty ``requirements`` keeps the discovery path."""
    grantor = str(grantor_user_id or "").strip()
    LOGGER.info(
        "[connect-first] enter namespace=%s operation=%s grantor=%s tenant=%s project=%s missing=%s explicit_reqs=%s",
        namespace, operation, grantor or "<EMPTY>", tenant or "<EMPTY>",
        project or "<EMPTY>", list(missing), len(list(requirements or ())),
    )
    if not grantor:
        LOGGER.info("[connect-first] -> None (no grantor)")
        return None
    explicit = [dict(item) for item in (requirements or ()) if isinstance(item, Mapping)]
    if explicit:
        requirements = explicit
    else:
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import (
                RedisNamedServiceDiscovery,
                _redis_client_from_settings,
            )

            discovery = RedisNamedServiceDiscovery(
                _redis_client_from_settings(), tenant=tenant, project=project,
            )
            entries = await discovery.entries_for_namespace(namespace)
        except Exception:
            LOGGER.warning(
                "[connect-first] -> None (provider discovery raised, namespace=%s)",
                namespace, exc_info=True,
            )
            return None
        discovered: list[Mapping[str, Any]] = []
        for entry in entries or []:
            spec = getattr(entry, "spec", None)
            metadata = dict(getattr(spec, "metadata", None) or {})
            raw = metadata.get("connected_accounts")
            if isinstance(raw, (list, tuple)):
                discovered.extend(item for item in raw if isinstance(item, Mapping))
        LOGGER.info(
            "[connect-first] discovery namespace=%s entries=%s requirements=%s providers=%s",
            namespace, len(list(entries or [])), len(discovered),
            [r.get("provider_id") for r in discovered],
        )
        requirements = discovered
    if not requirements:
        LOGGER.info("[connect-first] -> None (no account-backed requirements for namespace=%s)", namespace)
        return None
    op = str(operation or "").strip()
    for req in requirements:
        provider_id = str(req.get("provider_id") or "").strip()
        if not provider_id:
            continue
        # Only operations that actually need a PROVIDER claim lead with connect.
        # A metadata operation (object.schema, provider.about, capabilities)
        # needs only door admission (named_services:use); it must NOT ask the
        # user to connect an account. When the operation-specific provider
        # claims are empty, this provider is not in play for this call - skip
        # it (the missing door claim falls to a plain agent-grant instead).
        by_op = req.get("claims_by_operation")
        if isinstance(by_op, Mapping) and by_op:
            # A differentiated realm (mail) maps each account-touching operation
            # to its claims. An operation with no mapping needs no account.
            provider_claims = [
                str(c).strip() for c in (by_op.get(op) or []) if str(c or "").strip()
            ]
            if not provider_claims:
                continue
        else:
            # A flat realm (slack) declares its whole vocabulary. The operation
            # needs only the claims that are actually missing on this attempt;
            # if none of them are this provider's claims, the operation does not
            # touch the account.
            vocabulary = {
                str(c).strip() for c in (req.get("claims") or []) if str(c or "").strip()
            }
            provider_claims = [c for c in (str(m).strip() for m in missing) if c in vocabulary]
            if not provider_claims:
                continue
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.store import (
                DelegatedToKdcubeStore,
            )

            accounts = await DelegatedToKdcubeStore(user_id=grantor).list_accounts(
                provider_id=provider_id,
            )
        except Exception:
            LOGGER.warning(
                "[connect-first] -> None (account store raised, provider=%s)",
                provider_id, exc_info=True,
            )
            return None
        connected = [a for a in accounts if a.connected]
        LOGGER.info(
            "[connect-first] provider=%s ask=%s accounts_total=%s connected=%s",
            provider_id, provider_claims, len(accounts), len(connected),
        )
        # A USABLE account is what decides the order - not any record. A
        # disconnected or revoked account (a prior connect the user removed)
        # leaves a record but cannot back the claim, so connect must still
        # lead. Filtering to `connected` keeps the ordering honest across
        # connect/disconnect cycles.
        if connected:
            LOGGER.info(
                "[connect-first] provider=%s has a connected account -> agent-grant leads",
                provider_id,
            )
            continue
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.preflight import (
            connected_account_consent_payload,
        )

        from kdcube_ai_app.apps.chat.sdk.solutions.connections.connector_app_resolution import (
            resolve_connector_app_id,
        )

        # One rule: the guarded service's declaration, or provider-wide.
        connector_app_id = resolve_connector_app_id(provider_id)
        payload = connected_account_consent_payload(
            tenant=tenant,
            project=project,
            connection_hub_bundle_id=hub_bundle_id,
            missing=[{
                "tool_name": namespace,
                "failures": [
                    {
                        "provider_id": provider_id,
                        "connector_app_id": connector_app_id,
                        "claim": claim,
                        "reason": "connect_required",
                        "retry_hint": True,
                    }
                    for claim in provider_claims
                ],
            }],
            agent_client_id=agent_client_id,
            agent_resource=agent_resource,
            # The DOOR claims the hand-off grant must cover (they can differ
            # from the provider connect claims: mail:* vs gmail:*).
            agent_claims=sorted({str(c) for c in missing if str(c or "").strip()}),
        )
        error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
        consent = dict(payload.get("consent") or {})
        consent.setdefault("namespace", namespace)
        consent.setdefault(
            "agent_claims", sorted({str(c) for c in missing if str(c or "").strip()})
        )
        url = str(consent.get("url") or "").strip()
        denial: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "needs_connected_account_consent",
                "message": str(error.get("message") or (
                    f"Connect a {provider_id} account in Connection Hub to continue."
                )),
            },
            "reason": "connect_required",
            "retry_hint": True,
            "namespace": namespace,
            "tool": tool,
            "operation": op,
            "provider_id": provider_id,
            "required_grants": sorted({str(c) for c in required if str(c or "").strip()}),
            "missing_grants": sorted({str(c) for c in missing if str(c or "").strip()}),
            "consent": consent,
            "next_step": (
                f"Connect a {provider_id} account in Connection Hub first; the guided "
                "plan then hands off to granting THIS agent the access it asked for. "
                "Retry the same call after both steps."
            ),
        }
        if url:
            denial["connection_hub_url"] = url
        LOGGER.info(
            "[connect-first] gate-2 connect leads (namespace=%s operation=%s provider=%s grantor=%s agent=%s)",
            namespace, op, provider_id, grantor, agent_client_id,
        )
        return denial
    return None


__all__ = [
    "agent_client_id_from_request",
    "agent_grant_consent_denial",
    "connect_first_denial",
    "granted_resource_from_request",
    "CONSENT_NEEDED_CODE",
    "DELEGATED_CONSENT_REQUIRED",
]
