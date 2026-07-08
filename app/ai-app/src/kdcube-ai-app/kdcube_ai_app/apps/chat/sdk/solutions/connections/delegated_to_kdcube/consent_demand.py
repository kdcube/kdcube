# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Demand-driven consent bookkeeping.

Which tools a turn needs only becomes clear as the agent works, so consent
raises at the ATTEMPT: the tool invocation that hits an unmet claim records a
consent demand here and the chat renders the scoped banner. The record is one
small user-prop per user+bundle holding the LAST conversation's pending
demands — the next turn's transition check reads it, resolves ONLY those
tools' claims, and announces the ones the user satisfied
(``[CONNECTED ACCOUNTS UPDATE]``). Transition detection matters within one
conversation, so a new conversation simply overwrites the record.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

LOGGER = logging.getLogger("kdcube.connections.delegated_to_kdcube")

# One record per user+bundle: {"conversation_id": …, "providers": [group…]}
# where a group is {provider_id, provider_label?, connector_app_id?, claims,
# tools} — the same shape the ANNOUNCE composer reads.
PENDING_CONSENT_KEY = "delegated_to_kdcube.blocked_snapshot"

# Hub-addressed registry (per user, under the Connection Hub bundle): every
# open demand with its FULL conversation address, so consent completion in the
# hub can author the granted event back into the right conversation lane.
# Value: {"demands": [{conversation_id, tenant, project, bundle_id, agent_id,
# provider_id, connector_app_id, claims, tools, recorded_at}]}.
PENDING_DEMANDS_REGISTRY_KEY = "delegated_to_kdcube.consent_demands"

CONSENT_GRANTED_EVENT_KIND = "connections.consent.granted"
CONSENT_GRANTED_EVENT_SOURCE_ID = "connection_hub.consent"


async def read_pending_consent(*, user_id: str, bundle_id: str, conversation_id: str) -> list:
    """The conversation's pending consent-demand groups (empty otherwise).

    The user-props client is synchronous (psycopg2); the read runs in a worker
    thread so async turn/op paths never block their event loop on it."""
    if not user_id or not bundle_id or not conversation_id:
        return []
    try:
        from kdcube_ai_app.apps.chat.sdk import config as sdk_config

        raw = await asyncio.to_thread(
            sdk_config.get_user_prop,
            PENDING_CONSENT_KEY,
            user_id=user_id,
            bundle_id=bundle_id,
            default=None,
        )
    except Exception:
        return []
    if not isinstance(raw, dict) or str(raw.get("conversation_id") or "") != conversation_id:
        return []
    providers = raw.get("providers")
    return [g for g in providers if isinstance(g, dict)] if isinstance(providers, list) else []


async def write_pending_consent(*, user_id: str, bundle_id: str, conversation_id: str, providers: list) -> None:
    if not user_id or not bundle_id or not conversation_id:
        return
    try:
        from kdcube_ai_app.apps.chat.sdk import config as sdk_config

        if providers:
            await asyncio.to_thread(
                sdk_config.set_user_prop,
                PENDING_CONSENT_KEY,
                {"conversation_id": conversation_id, "providers": providers},
                user_id=user_id,
                bundle_id=bundle_id,
            )
        else:
            await asyncio.to_thread(
                sdk_config.delete_user_prop, PENDING_CONSENT_KEY, user_id=user_id, bundle_id=bundle_id,
            )
    except Exception:
        LOGGER.debug("pending-consent write unavailable", exc_info=True)


async def record_consent_demand(
    *,
    user_id: str,
    bundle_id: str,
    conversation_id: str,
    provider_id: str,
    connector_app_id: str = "",
    provider_label: str = "",
    claims: list | tuple = (),
    tool_name: str = "",
    tenant: str = "",
    project: str = "",
    agent_id: str = "",
    connection_hub_bundle_id: str = "",
) -> bool:
    """Record one attempted tool's consent demand.

    Returns True when this (provider, claims, tool) is NEW for the
    conversation — the caller emits the chat consent event exactly once per
    demand; retries of the same tool in the same conversation stay quiet
    server-side (the banner reducer's signature dedupe covers client replays).
    """
    provider_key = str(provider_id or "").strip()
    tool_key = str(tool_name or "").strip()
    claim_list = [str(c).strip() for c in (claims or []) if str(c or "").strip()]
    if not provider_key or not tool_key:
        return False
    pending = await read_pending_consent(user_id=user_id, bundle_id=bundle_id, conversation_id=conversation_id)
    for group in pending:
        if str(group.get("provider_id") or "") != provider_key:
            continue
        known_tools = {str(t) for t in (group.get("tools") or [])}
        known_claims = {str(c) for c in (group.get("claims") or [])}
        if tool_key in known_tools and set(claim_list) <= known_claims:
            return False
        group["tools"] = sorted(known_tools | {tool_key})
        group["claims"] = sorted(known_claims | set(claim_list))
        if provider_label and not group.get("provider_label"):
            group["provider_label"] = provider_label
        await write_pending_consent(
            user_id=user_id, bundle_id=bundle_id, conversation_id=conversation_id, providers=pending,
        )
        await _register_demand_address(
            user_id=user_id,
            bundle_id=bundle_id,
            conversation_id=conversation_id,
            provider_id=provider_key,
            connector_app_id=str(connector_app_id or "").strip(),
            claims=claim_list,
            tool_name=tool_key,
            tenant=tenant,
            project=project,
            agent_id=agent_id,
            connection_hub_bundle_id=connection_hub_bundle_id,
        )
        return True
    pending.append({
        "provider_id": provider_key,
        "provider_label": str(provider_label or "").strip(),
        "connector_app_id": str(connector_app_id or "").strip(),
        "claims": sorted(set(claim_list)),
        "tools": [tool_key],
    })
    await write_pending_consent(
        user_id=user_id, bundle_id=bundle_id, conversation_id=conversation_id, providers=pending,
    )
    await _register_demand_address(
        user_id=user_id,
        bundle_id=bundle_id,
        conversation_id=conversation_id,
        provider_id=provider_key,
        connector_app_id=str(connector_app_id or "").strip(),
        claims=claim_list,
        tool_name=tool_key,
        tenant=tenant,
        project=project,
        agent_id=agent_id,
        connection_hub_bundle_id=connection_hub_bundle_id,
    )
    return True


def _hub_bundle_id(value: str = "") -> str:
    if str(value or "").strip():
        return str(value).strip()
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
        CONNECTION_HUB_BUNDLE_ID,
    )

    return CONNECTION_HUB_BUNDLE_ID


async def _register_demand_address(
    *,
    user_id: str,
    bundle_id: str,
    conversation_id: str,
    provider_id: str,
    connector_app_id: str,
    claims: list,
    tool_name: str,
    tenant: str,
    project: str,
    agent_id: str,
    connection_hub_bundle_id: str,
) -> None:
    """Append this demand (with its full conversation address) to the
    hub-addressed registry, so consent completion can author the granted
    event back into the conversation. One entry per (conversation, provider,
    tool). Best-effort."""
    try:
        import time

        from kdcube_ai_app.apps.chat.sdk import config as sdk_config

        hub_bundle = _hub_bundle_id(connection_hub_bundle_id)
        raw = await asyncio.to_thread(
            sdk_config.get_user_prop,
            PENDING_DEMANDS_REGISTRY_KEY,
            user_id=user_id,
            bundle_id=hub_bundle,
            default=None,
        )
        demands = list(raw.get("demands") or []) if isinstance(raw, dict) else []
        for entry in demands:
            if (
                isinstance(entry, dict)
                and str(entry.get("conversation_id") or "") == conversation_id
                and str(entry.get("provider_id") or "") == provider_id
                and str(entry.get("tool_name") or "") == tool_name
            ):
                entry["claims"] = sorted({*(entry.get("claims") or []), *claims})
                break
        else:
            demands.append({
                "conversation_id": conversation_id,
                "tenant": str(tenant or "").strip(),
                "project": str(project or "").strip(),
                "bundle_id": bundle_id,
                "agent_id": str(agent_id or "").strip(),
                "provider_id": provider_id,
                "connector_app_id": connector_app_id,
                "claims": sorted(set(claims)),
                "tool_name": tool_name,
                "recorded_at": time.time(),
            })
        await asyncio.to_thread(
            sdk_config.set_user_prop,
            PENDING_DEMANDS_REGISTRY_KEY,
            {"demands": demands},
            user_id=user_id,
            bundle_id=hub_bundle,
        )
    except Exception:
        LOGGER.debug("consent demand address registration unavailable", exc_info=True)


def consent_granted_event_text(*, provider_label: str, claims: list, tools: list) -> str:
    """The timeline-facing sentence of the granted event."""
    claim_text = ", ".join(claims)
    tool_text = ", ".join(sorted({str(t).rsplit(".", 1)[-1] for t in tools if str(t or "").strip()}))
    return (
        f"The user approved {provider_label} access ({claim_text}). "
        f"The tools that needed it ({tool_text}) are usable now — retry what "
        "was blocked if it is still wanted."
    )


async def author_consent_granted_events(
    *,
    redis: Any,
    user_id: str,
    provider_id: str,
    granted_claims: list | tuple,
    connector_app_id: str = "",
    account_id: str = "",
    connection_hub_bundle_id: str = "",
    source_factory: Any = None,
) -> int:
    """Author `connections.consent.granted` conversation events for every
    pending demand this grant satisfies — the closing symmetry of
    demand-driven consent (the ask was an event; the grant is one too).

    Authored through the INGRESS EVENT MECHANISM: event inception writes a
    ConversationExternalEvent into the per-conversation event lane via
    `RedisConversationExternalEventSource.publish` — the same primitive
    `ingress_core._publish_external_event_batch` uses for authored events
    (the hub process holds the same redis lane client, so the write needs no
    HTTP hop). The external-event timeline protocol handles the rest: a LIVE
    turn folds the event through the lane watcher; with no live turn it
    resides in the lane as passive context the next user-initiated turn folds
    in. It is NOT a reactive event and is never modeled as one: published
    WITHOUT a task payload, the promoter permanently acks it
    (`claim_next_promotable` skips `task_payload is None`), so it can never
    start anything resembling a turn and the `@on_reactive_event` chat-handler
    surface plays no role.

    One event per demand entry: authored entries leave the registry AND their
    tools leave the conversation's pending snapshot (so the turn-start
    announce stays silent — the event is the stronger, factual record).
    Entries whose publish fails stay recorded; the announce covers them as
    the fallback. Returns the number of events authored."""
    provider_key = str(provider_id or "").strip()
    granted = {str(c).strip() for c in (granted_claims or []) if str(c or "").strip()}
    clean_user = str(user_id or "").strip()
    if not provider_key or not granted or not clean_user:
        return 0
    try:
        from kdcube_ai_app.apps.chat.sdk import config as sdk_config

        hub_bundle = _hub_bundle_id(connection_hub_bundle_id)
        raw = await asyncio.to_thread(
            sdk_config.get_user_prop,
            PENDING_DEMANDS_REGISTRY_KEY,
            user_id=clean_user,
            bundle_id=hub_bundle,
            default=None,
        )
        demands = list(raw.get("demands") or []) if isinstance(raw, dict) else []
        if not demands:
            return 0
        remaining: list = []
        authored = 0
        for entry in demands:
            if not isinstance(entry, dict):
                continue
            entry_claims = {str(c) for c in (entry.get("claims") or [])}
            matches = (
                str(entry.get("provider_id") or "") == provider_key
                and entry_claims
                and entry_claims <= granted
            )
            if not matches:
                remaining.append(entry)
                continue
            conversation_id = str(entry.get("conversation_id") or "")
            tenant = str(entry.get("tenant") or "")
            project = str(entry.get("project") or "")
            tool_name = str(entry.get("tool_name") or "")
            try:
                if source_factory is not None:
                    source = source_factory(entry)
                else:
                    from kdcube_ai_app.apps.chat.external_events import (
                        build_conversation_external_event_source,
                    )

                    source = build_conversation_external_event_source(
                        redis=redis,
                        tenant=tenant,
                        project=project,
                        conversation_id=conversation_id,
                        user_id=clean_user,
                        agent_id=str(entry.get("agent_id") or "") or "main",
                    )
                provider_label = (provider_key[:1].upper() + provider_key[1:])
                claims_list = sorted(entry_claims)
                await source.publish(
                    kind=CONSENT_GRANTED_EVENT_KIND,
                    source="connection_hub",
                    event_source_id=CONSENT_GRANTED_EVENT_SOURCE_ID,
                    text=consent_granted_event_text(
                        provider_label=provider_label,
                        claims=claims_list,
                        tools=[tool_name],
                    ),
                    payload={
                        "provider_id": provider_key,
                        "connector_app_id": str(entry.get("connector_app_id") or connector_app_id or ""),
                        "claims": claims_list,
                        "account_id": str(account_id or ""),
                        "tools": [tool_name],
                    },
                    # Passive by construction: no task payload means the
                    # promoter acks the event; it can never start a turn.
                    task_payload=None,
                )
                authored += 1
                LOGGER.info(
                    "[delegated.consent] granted event authored: conversation=%s provider=%s claims=%s tool=%s user=%s",
                    conversation_id, provider_key, ",".join(claims_list), tool_name, clean_user,
                )
                await _drop_tools_from_snapshot(
                    user_id=clean_user,
                    bundle_id=str(entry.get("bundle_id") or ""),
                    conversation_id=conversation_id,
                    provider_id=provider_key,
                    tools=[tool_name],
                )
            except Exception:
                LOGGER.warning(
                    "[delegated.consent] granted event publish failed (announce fallback keeps the record): conversation=%s provider=%s",
                    conversation_id, provider_key, exc_info=True,
                )
                remaining.append(entry)
        await asyncio.to_thread(
            sdk_config.set_user_prop,
            PENDING_DEMANDS_REGISTRY_KEY,
            {"demands": remaining},
            user_id=clean_user,
            bundle_id=hub_bundle,
        ) if remaining else await asyncio.to_thread(
            sdk_config.delete_user_prop,
            PENDING_DEMANDS_REGISTRY_KEY,
            user_id=clean_user,
            bundle_id=hub_bundle,
        )
        return authored
    except Exception:
        LOGGER.debug("consent granted authoring unavailable", exc_info=True)
        return 0


async def _drop_tools_from_snapshot(
    *,
    user_id: str,
    bundle_id: str,
    conversation_id: str,
    provider_id: str,
    tools: list,
) -> None:
    """Remove event-covered tools from the conversation's pending snapshot so
    the turn-start announce stays silent for them (the authored event is the
    stronger record)."""
    try:
        pending = await read_pending_consent(
            user_id=user_id, bundle_id=bundle_id, conversation_id=conversation_id,
        )
        if not pending:
            return
        drop = {str(t) for t in tools}
        next_groups: list = []
        for group in pending:
            if str(group.get("provider_id") or "") != provider_id:
                next_groups.append(group)
                continue
            kept = [t for t in (group.get("tools") or []) if str(t) not in drop]
            if kept:
                next_groups.append({**group, "tools": kept})
        await write_pending_consent(
            user_id=user_id, bundle_id=bundle_id, conversation_id=conversation_id, providers=next_groups,
        )
    except Exception:
        LOGGER.debug("pending snapshot trim unavailable", exc_info=True)


async def announce_consent_demand(
    *,
    comm: Any = None,
    payload: Any,
    provider_id: str,
    connector_app_id: str = "",
    claims: list | tuple = (),
    tool_name: str = "",
    identity: Any = None,
    connection_hub_bundle_id: str = "",
) -> bool:
    """Record one attempted tool's consent demand and emit the scoped chat
    consent event ONCE per (provider, claims, tool) demand per conversation.

    ``tool_name`` is the entry as the user sees it in the composer menu
    (``alias.tool`` for python tools, the bare namespace for named-service
    tools) — the banner's turn-off spotlight targets exactly that. Identity
    (user / bundle / conversation) comes from the bound request context;
    ``comm`` defaults to the context communicator. Best-effort: never raises.
    """
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
            get_comm,
            get_current_user_identity,
        )

        if identity is None:
            identity = get_current_user_identity() or {}
        user_id = str(identity.get("user_id") or "").strip()
        bundle_id = str(identity.get("bundle_id") or "").strip()
        conversation_id = str(identity.get("conversation_id") or "").strip()
        provider_key = str(provider_id or "").strip()
        agent_id = ""
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_request_context

            ctx = get_current_request_context()
            agent_id = str(getattr(getattr(ctx, "event", None), "agent_id", "") or "").strip()
        except Exception:
            agent_id = ""
        newly_recorded = await record_consent_demand(
            user_id=user_id,
            bundle_id=bundle_id,
            conversation_id=conversation_id,
            provider_id=provider_key,
            connector_app_id=str(connector_app_id or "").strip(),
            provider_label=(provider_key[:1].upper() + provider_key[1:]) if provider_key else "",
            claims=list(claims or []),
            tool_name=str(tool_name or "").strip(),
            tenant=str(identity.get("tenant_id") or "").strip(),
            project=str(identity.get("project_id") or "").strip(),
            agent_id=agent_id,
            connection_hub_bundle_id=connection_hub_bundle_id,
        )
        if not newly_recorded:
            return False
        communicator = comm if comm is not None else get_comm()
        event = getattr(communicator, "event", None) if communicator is not None else None
        if not callable(event):
            return False
        result = event(
            agent="connection-hub",
            type="chat.step",
            route="chat.step",
            title="Account consent needed",
            step="delegated_to_kdcube.consent",
            data=dict(payload or {}),
            status="completed",
            broadcast=False,
        )
        if hasattr(result, "__await__"):
            await result
        return True
    except Exception:
        LOGGER.debug("consent demand announce unavailable", exc_info=True)
        return False


async def claim_coverage_for_policies(
    *,
    user_id: str,
    policies: list,
    connection_hub_bundle_id: str = "",
) -> dict:
    """READ-ONLY per-tool claim coverage for a picker UI.

    Answers "which of this tool's declared claims does the user's connected
    account set already hold" from the account records alone — zero credential
    reads, zero consent events, zero health probes (a menu render must ask
    nothing). A claim counts covered when ANY connected account of the
    requirement's provider (and connector app, when named) holds it; the
    consent plan and the tool attempt stay the authorities on health and
    account choice.

    Returns {tool_name: {provider_id, connector_app_id, claims, unmet,
    covered}} for every policy carrying connected-account requirements.
    """
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
        CONNECTION_HUB_BUNDLE_ID,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.store import (
        DelegatedToKdcubeStore,
    )

    clean_user = str(user_id or "").strip()
    coverage: dict = {}
    if not clean_user:
        return coverage
    store = DelegatedToKdcubeStore(
        user_id=clean_user,
        bundle_id=str(connection_hub_bundle_id or "").strip() or CONNECTION_HUB_BUNDLE_ID,
    )
    try:
        # Await natively: the store offloads its synchronous storage client to
        # a worker thread itself (never a nested event loop over shared
        # clients).
        accounts = await store.list_accounts()
    except Exception:
        LOGGER.debug("claim coverage account read unavailable", exc_info=True)
        return coverage
    for policy in policies or []:
        tool_name = str(getattr(policy, "tool_name", "") or "").strip()
        requirements = list(getattr(policy, "connected_accounts", ()) or ())
        if not tool_name or not requirements:
            continue
        declared: list = []
        unmet: list = []
        provider_id = ""
        connector_app_id = ""
        for requirement in requirements:
            provider_id = provider_id or str(getattr(requirement, "provider_id", "") or "")
            connector_app_id = connector_app_id or str(getattr(requirement, "connector_app_id", "") or "")
            req_provider = str(getattr(requirement, "provider_id", "") or "")
            req_connector = str(getattr(requirement, "connector_app_id", "") or "")
            eligible = [
                account for account in accounts
                if account.provider_id == req_provider
                and account.connected
                and (not req_connector or not account.connector_app_id or account.connector_app_id == req_connector)
            ]
            for claim in getattr(requirement, "claims", ()) or ():
                claim_key = str(claim or "").strip()
                if not claim_key or claim_key in declared:
                    continue
                declared.append(claim_key)
                if not any(account.allows(claim_key) for account in eligible):
                    unmet.append(claim_key)
        coverage[tool_name] = {
            "provider_id": provider_id,
            "connector_app_id": connector_app_id,
            "claims": declared,
            "unmet": unmet,
            "covered": not unmet,
        }
    return coverage


def pending_consent_delta(previous: list, current: list) -> list:
    """Provider groups whose tools left the pending set (consent satisfied)."""
    pending_now: dict = {}
    for group in current or []:
        if isinstance(group, dict):
            key = str(group.get("provider_id") or "")
            pending_now.setdefault(key, set()).update(
                str(t) for t in (group.get("tools") or []) if str(t or "").strip()
            )
    satisfied: list = []
    for group in previous or []:
        if not isinstance(group, dict):
            continue
        key = str(group.get("provider_id") or "")
        freed = [
            str(t) for t in (group.get("tools") or [])
            if str(t or "").strip() and str(t) not in pending_now.get(key, set())
        ]
        if freed:
            satisfied.append({**group, "tools": freed})
    return satisfied
