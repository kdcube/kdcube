# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connected-account helpers for SDK integration tools.

Application tool modules use this layer to resolve provider credentials for the
current platform user. The helper delegates registry/storage work to Connection
Hub and returns the same consent-needed envelope the chat UI understands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_user_identity
from kdcube_ai_app.apps.chat.sdk.runtime.tool_module_bindings import get_bound_context
from kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges import DEFAULT_CONNECTION_HUB_BUNDLE_ID
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
    REASON_AGENT_ACCOUNT_BINDING_REQUIRED,
    REASON_AGENT_GRANT_REQUIRED,
    REASON_RECONNECT_REQUIRED,
    ClaimResolution,
    DelegatedToKdcubeClient,
    connected_account_consent_payload,
)

logger = logging.getLogger(__name__)


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
        # Agent-facing guidance: the platform raised the ask to the user in
        # chat (the scoped consent banner); the agent's job is to keep the
        # turn productive and narrate the ask briefly.
        instructions = (
            "The platform has asked the user for this account access in chat. "
            "Tell the user briefly what the request needs, continue with the "
            "other available tools, and call this tool again after they approve."
        )
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
                "instructions": instructions,
            },
            "consent_required": True,
            "instructions": instructions,
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


async def _announce_consent_demand(
    source: Mapping[str, Any] | Any,
    *,
    payload: Mapping[str, Any],
    provider_id: str,
    connector_app_id: str,
    claims: list,
    tool_name: str,
    connection_hub_bundle_id: str = "",
) -> None:
    """Demand-driven consent: the ATTEMPT raises the ask (shared bookkeeping
    in ``delegated_to_kdcube/consent_demand.py``; the bound tool communicator
    carries the scoped chat event)."""
    try:
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
            announce_consent_demand,
        )

        comm = get_bound_context(source).communicator
        await announce_consent_demand(
            comm=comm,
            payload=payload,
            provider_id=provider_id,
            connector_app_id=connector_app_id,
            claims=claims,
            tool_name=tool_name,
            identity=get_current_user_identity() or {},
            connection_hub_bundle_id=connection_hub_bundle_id,
        )
    except Exception:
        logger.debug("consent demand announce unavailable", exc_info=True)


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


async def _announce_agent_grant_demand(
    source: Mapping[str, Any] | Any,
    *,
    result: ClaimResolution,
    claim: str,
    tool_name: str,
    tenant: str,
    project: str,
    connection_hub_bundle_id: str,
) -> dict[str, Any] | None:
    """A per-account claim the connected account CAN satisfy, but THIS agent's
    grant is not bound for — route to the agent's own grant card (Delegated by
    KDCube), where the account picker sets the per-account binding. NOT a
    connect-a-provider banner.

    A per-account claim is not a resource grant, so there is no one-click grant
    here — the banner deep-links to the focused card and the user ticks the
    permission on the account. Returns the explainable tool result, or None when
    the agent identity is unknown (caller falls back to the connect payload)."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.agent_account_scope import (
        agent_identity,
    )

    ident = agent_identity()
    client_id = _clean(ident.get("client_id"))
    resource = _clean(ident.get("resource"))
    if not client_id or not resource:
        return None
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.consent_denial import (
        connection_hub_grant_url,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_consent import (
        CONSENT_KIND_AGENT_GRANT,
        mcp_consent_from_denial,
    )

    missing = _clean(result.claim) or _clean(claim)
    account_label = _clean(result.account_id)
    hub_url = connection_hub_grant_url(
        tenant=tenant,
        project=project,
        client_id=client_id,
        resource=resource,
        claims=[],  # the per-account claim is set in the card's account picker
        hub_bundle_id=connection_hub_bundle_id or DEFAULT_CONNECTION_HUB_BUNDLE_ID,
        account_id=account_label,   # focus the card on the exact account + claim
        account_claim=missing,
    )
    # No agent_client_id passed -> mcp_consent_from_denial adds NO one-click grant
    # (that path is for resource claims). The deep link routes to the focused card.
    consent = mcp_consent_from_denial(
        {"status": 403, "reason": REASON_AGENT_ACCOUNT_BINDING_REQUIRED},
        resource=resource,
        claims=[missing] if missing else [],
        connection_hub_url=hub_url,
        tool_name=tool_name,
    )
    consent.consent["kind"] = CONSENT_KIND_AGENT_GRANT
    consent.consent["agent_client_id"] = client_id
    # The broker marks every user-fixable resolution retryable (`_user_action`).
    # This block is rebuilt from a synthetic denial, so the flag is restated
    # here; consent_details() reads it, and False would tell a caller that
    # retrying after the grant is pointless.
    consent.consent["retry_hint"] = True
    if hub_url:
        consent.consent["url"] = hub_url
    if account_label:
        consent.consent["account_id"] = account_label
    consent.agent_message = (
        f"This needs your permission to use {missing or 'this account'}"
        + (f" on account {account_label}" if account_label else "")
        + ". Grant it to THIS agent in Connection Hub -> Delegated by KDCube "
        "(open the agent's access, tick the permission on that account, Save), "
        "then ask again. Do not retry until it is granted."
    )
    # Raise the banner through the SAME bound-tool-communicator path the
    # connect-account banner uses, so it renders in chat (announce_agent_consent's
    # own fallback communicator is not bound in the native tool context, which is
    # why the agent-grant banner did not appear). The event carries the
    # needs_connected_account_consent code the chat renderer already renders.
    # Raise the banner through the SAME bound-tool communicator the connect
    # banner uses. announce_consent_demand emits ONLY when the demand is newly
    # recorded for the conversation — so if it declines (already recorded, or an
    # incomplete address), we emit the event DIRECTLY, so the banner shows for
    # THIS attempt regardless. Logged so the live path is visible, not guessed.
    event_payload = consent.chat_event_payload()
    comm = None
    try:
        comm = getattr(get_bound_context(source), "communicator", None)
    except Exception:
        comm = None
    announced = False
    try:
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
            announce_consent_demand,
        )

        announced = await announce_consent_demand(
            comm=comm,
            payload=event_payload,
            provider_id="kdcube",
            connector_app_id=client_id,
            claims=[missing] if missing else [],
            tool_name=tool_name,
            identity=get_current_user_identity() or {},
            connection_hub_bundle_id=connection_hub_bundle_id,
        )
    except Exception:
        logger.warning("agent-grant announce failed", exc_info=True)
    if not announced and comm is not None:
        try:
            emit = getattr(comm, "event", None)
            if callable(emit):
                res = emit(
                    agent="connection-hub",
                    type="chat.step",
                    route="chat.step",
                    title="Agent access needed",
                    step="delegated_to_kdcube.consent",
                    data=dict(event_payload or {}),
                    status="completed",
                    broadcast=False,
                )
                if hasattr(res, "__await__"):
                    await res
                announced = True
        except Exception:
            logger.warning("agent-grant direct emit failed", exc_info=True)
    logger.info(
        "[agent-grant-demand] announced=%s comm_bound=%s hub_url=%s client=%s account=%s claim=%s",
        announced, comm is not None, bool(hub_url), client_id, account_label, missing,
    )
    payload = consent.to_tool_result()
    # The banner is already raised above; use a code the shared chat
    # post-processor does NOT re-route (neither a connect-account nor a
    # resource-grant demand), so it stays an explainable result and raises no
    # second banner.
    err = payload.get("error")
    if isinstance(err, dict):
        err["code"] = REASON_AGENT_ACCOUNT_BINDING_REQUIRED
    return payload


async def resolve_connected_account_claim(
    source: Mapping[str, Any] | Any,
    *,
    provider_id: str,
    connector_app_id: str,
    claim: str,
    tool_name: str,
    account_id: str = "",
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    force_refresh: bool = False,
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
    # The calling agent's per-provider account binding (if any) restricts which
    # connected account may satisfy this claim. Unset / non-agent → None → no
    # restriction (unchanged).
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.agent_account_scope import (
        account_claim_scope_for,
    )

    result: ClaimResolution = await client.ensure_claim(
        provider_id=provider_id,
        connector_app_id=connector_app_id,
        claim=claim,
        account_id=account_id,
        account_claim_scope=account_claim_scope_for(provider_id),
        force_refresh=force_refresh,
    )
    if not result.ok or result.credential is None:
        # A per-account claim the account CAN satisfy but this AGENT is not bound
        # for -> route to the agent's own grant card, not a connect-a-provider
        # banner. Falls through when the agent identity is unknown.
        if _clean(getattr(result, "error", "")) == REASON_AGENT_GRANT_REQUIRED:
            try:
                agent_payload = await _announce_agent_grant_demand(
                    source,
                    result=result,
                    claim=claim,
                    tool_name=tool_name,
                    tenant=tenant,
                    project=project,
                    connection_hub_bundle_id=connection_hub_bundle_id,
                )
            except Exception:
                # Never let the consent-routing helper crash the tool call — a
                # failed agent-card demand degrades to the connect-account payload
                # below, which still tells the user (and the model) what to do.
                logger.warning("agent-grant consent demand failed; using connect payload", exc_info=True)
                agent_payload = None
            if agent_payload is not None:
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
                    error_payload=agent_payload,
                )
        # When an AGENT turn hits the provider gate (its account lacks the claim),
        # carry the agent identity into the connect deep-link so the connect panel
        # can offer a "continue -> grant it to this agent" hand-off after the
        # provider step, instead of sending the user back to chat to retry blind.
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.agent_account_scope import (
            agent_identity,
        )
        _agent = agent_identity()
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
            agent_client_id=_clean(_agent.get("client_id")),
            agent_resource=_clean(_agent.get("resource")),
        )
        if getattr(result, "retry_hint", False):
            # User-fixable: raise the ask (scoped banner + pending record).
            await _announce_consent_demand(
                source,
                payload=payload,
                provider_id=result.provider_id or provider_id,
                connector_app_id=result.connector_app_id or connector_app_id,
                claims=[result.claim or claim],
                tool_name=tool_name,
                connection_hub_bundle_id=connection_hub_bundle_id,
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


async def refresh_connected_account_claim(
    source: Mapping[str, Any] | Any,
    *,
    credential: ConnectedAccountCredential,
) -> ConnectedAccountCredential:
    """Force-refresh a credential the provider just rejected.

    Re-resolves the same claim pinned to the same account with
    ``force_refresh=True``, so a token whose stored timestamps still look
    valid gets exchanged anyway. Returns the refreshed credential on success;
    on failure the broker has already marked the account and the returned
    credential carries the reconnect payload.
    """
    return await resolve_connected_account_claim(
        source,
        provider_id=credential.provider_id,
        connector_app_id=credential.connector_app_id,
        claim=credential.claim,
        tool_name=credential.tool_name,
        account_id=credential.account_id,
        connection_hub_bundle_id=credential.connection_hub_bundle_id,
        force_refresh=True,
    )


async def provider_auth_failed(
    source: Mapping[str, Any] | Any,
    *,
    credential: ConnectedAccountCredential,
    where: str,
    provider_error: str = "",
) -> dict[str, Any]:
    """Translate a live provider rejection into KDCube state plus envelope.

    Marks the account ``reconnect_required`` in Connection Hub (best-effort —
    the envelope must reach the user even if marking fails) and returns the
    consent envelope pointing at the reconnect action.
    """
    detail = _clean(provider_error)
    entrypoint, tenant, project, user_id = _scope(source)
    tenant = tenant or credential.tenant
    project = project or credential.project
    if credential.account_id and user_id:
        try:
            client = await DelegatedToKdcubeClient.from_connection_hub(
                entrypoint,
                user_id=user_id,
                tenant=tenant,
                project=project,
                connection_hub_bundle_id=credential.connection_hub_bundle_id,
            )
            await client.mark_account_auth_failure(
                credential.account_id,
                last_error=detail or "provider rejected the stored credential",
            )
        except Exception:
            logger.exception(
                "Failed to mark connected account %s after provider auth failure",
                credential.account_id,
            )
    payload = connected_account_consent_payload(
        tenant=tenant,
        project=project,
        connection_hub_bundle_id=credential.connection_hub_bundle_id,
        missing=[
            {
                "ok": False,
                "tool_name": credential.tool_name or where,
                "failures": [
                    {
                        "ok": False,
                        "provider_id": credential.provider_id,
                        "connector_app_id": credential.connector_app_id,
                        "claim": credential.claim,
                        "account_id": credential.account_id,
                        "error": REASON_RECONNECT_REQUIRED,
                        "retry_hint": True,
                        "message": detail,
                    }
                ],
            }
        ],
    )
    return ConnectedAccountCredential(
        ok=False,
        account_id=credential.account_id,
        provider_id=credential.provider_id,
        connector_app_id=credential.connector_app_id,
        claim=credential.claim,
        tool_name=credential.tool_name or where,
        tenant=tenant,
        project=project,
        connection_hub_bundle_id=credential.connection_hub_bundle_id,
        error_payload=payload,
    ).error_envelope(where=where)


_AUTH_FAILURE_KEY = "__connected_account_auth_failure__"


def connected_account_auth_failure(
    credential: ConnectedAccountCredential,
    message: str = "",
) -> dict[str, Any]:
    """Signal that the provider rejected this credential mid-operation.

    Tool bodies return this marker instead of a final envelope; the
    surrounding :func:`run_with_connected_account_retry` translates it into
    a force-refresh retry or the reconnect envelope. The marker never
    escapes to callers.
    """
    return {_AUTH_FAILURE_KEY: {"credential": credential, "message": _clean(message)}}


def _auth_failure_of(result: Any) -> tuple[ConnectedAccountCredential | None, str]:
    if isinstance(result, dict):
        marker = result.get(_AUTH_FAILURE_KEY)
        if isinstance(marker, dict) and isinstance(marker.get("credential"), ConnectedAccountCredential):
            return marker["credential"], _clean(marker.get("message"))
    return None, ""


async def run_with_connected_account_retry(
    source: Mapping[str, Any] | Any,
    *,
    where: str,
    run: Any,
) -> Any:
    """Run a tool body with the live provider-rejection recovery contract.

    ``run`` is an async callable with no arguments that resolves its own
    connected-account credentials and returns either a final result or a
    :func:`connected_account_auth_failure` marker. On a marker, the failing
    credential is force-refreshed once (the refreshed token lands in the
    store, so the re-run picks it up by re-resolving) and ``run`` is retried
    once. A second rejection — or an unrefreshable credential — marks the
    account ``reconnect_required`` and returns the reconnect envelope.
    """
    result = await run()
    credential, message = _auth_failure_of(result)
    if credential is None:
        return result
    refreshed = await refresh_connected_account_claim(source, credential=credential)
    if not (refreshed.ok and refreshed.access_token):
        # The broker already recorded the health transition on the account.
        return refreshed.error_envelope(where=where)
    result = await run()
    credential, message = _auth_failure_of(result)
    if credential is None:
        return result
    return await provider_auth_failed(
        source,
        credential=credential,
        where=where,
        provider_error=message,
    )


__all__ = [
    "ConnectedAccountCredential",
    "connected_account_auth_failure",
    "provider_auth_failed",
    "refresh_connected_account_claim",
    "resolve_connected_account_claim",
    "run_with_connected_account_retry",
]
