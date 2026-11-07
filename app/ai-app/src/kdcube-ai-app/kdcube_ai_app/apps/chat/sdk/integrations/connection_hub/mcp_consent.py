# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube chat bindings for Connection Hub MCP consent contracts."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Mapping


_core = import_module("connection_hub.mcp_consent")


def _agent_card_review(consent: Any) -> Any:
    """Complete hosted native-tool demands with the same portable Card link."""
    fields = consent.consent
    client_id = str(fields.get("agent_client_id") or "")
    if not client_id.startswith("kdcube-agent:"):
        return consent
    fields["agent_card_consent"] = True
    fields.pop("grant", None)
    if fields.get("connection_hub_url"):
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        parts = urlsplit(str(fields["connection_hub_url"]))
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["agent_card_consent"] = "1"
        for key in ("invocation_policy", "invocation_change_id"):
            if key not in query and fields.get(key):
                query[key] = str(fields[key])
        fields["connection_hub_url"] = urlunsplit(parts._replace(query=urlencode(query)))
        return consent
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_user_identity
    from connection_hub.delegated_credentials.consent_denial import connection_hub_grant_url

    identity = get_current_user_identity() or {}
    fields["connection_hub_url"] = connection_hub_grant_url(
        tenant=str(identity.get("tenant_id") or ""), project=str(identity.get("project_id") or ""),
        client_id=client_id, resource=consent.resource, claims=consent.claims,
        namespace=str(fields.get("namespace") or ""), operation=str(fields.get("operation") or ""),
        outer_operation=str(fields.get("outer_operation") or ""), access_id=str(fields.get("access_id") or ""),
        account_id=str(fields.get("account_id") or ""), account_claim=str(fields.get("account_claim") or ""),
        account_provider_id=str(fields.get("account_provider_id") or ""), agent_card_consent=True,
        invocation_policy=str(fields.get("invocation_policy") or ""),
        invocation_change_id=str(fields.get("invocation_change_id") or ""),
    )
    return consent


def mcp_consent_from_denial(denial: Any, **kwargs: Any) -> Any:
    return _agent_card_review(_core.mcp_consent_from_denial(denial, **kwargs))


async def _kdcube_demand_announcer(**kwargs: Any) -> bool:
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_to_kdcube.consent_demand import (
        announce_consent_demand,
    )

    return bool(await announce_consent_demand(**kwargs))


async def _kdcube_event_emitter(payload: Mapping[str, Any]) -> bool:
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_comm

    communicator = get_comm()
    event = getattr(communicator, "event", None) if communicator is not None else None
    if not callable(event):
        return False
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
    return True


async def announce_agent_consent(consent: Any) -> None:
    await _core.announce_agent_consent(
        _agent_card_review(consent),
        demand_announcer=_kdcube_demand_announcer,
        event_emitter=_kdcube_event_emitter,
    )


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


__all__ = sorted(name for name in dir(_core) if not name.startswith("_"))
