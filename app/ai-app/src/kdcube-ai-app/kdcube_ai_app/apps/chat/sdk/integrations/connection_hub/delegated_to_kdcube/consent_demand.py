# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube persistence and event bindings for Connection Hub consent demands."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_to_kdcube.store import (
    DelegatedToKdcubeStore,
)


_core = import_module("connection_hub.delegated_to_kdcube.consent_demand")


class _KdcubeUserPropertyStore:
    async def get_user_prop(self, key: str, **kwargs: Any) -> Any:
        return await sdk_config.get_user_prop(key, **kwargs)

    async def set_user_prop(self, key: str, value: Any, **kwargs: Any) -> None:
        await sdk_config.set_user_prop(key, value, **kwargs)

    async def delete_user_prop(self, key: str, **kwargs: Any) -> None:
        await sdk_config.delete_user_prop(key, **kwargs)


_PROPERTY_STORE = _KdcubeUserPropertyStore()

PENDING_CONSENT_KEY = _core.PENDING_CONSENT_KEY
PENDING_DEMANDS_REGISTRY_KEY = _core.PENDING_DEMANDS_REGISTRY_KEY
CONSENT_GRANTED_EVENT_KIND = _core.CONSENT_GRANTED_EVENT_KIND
CONSENT_GRANTED_EVENT_TRANSPORT_KIND = _core.CONSENT_GRANTED_EVENT_TRANSPORT_KIND
CONSENT_GRANTED_EVENT_SOURCE_ID = _core.CONSENT_GRANTED_EVENT_SOURCE_ID
consent_granted_event_text = _core.consent_granted_event_text
pending_consent_delta = _core.pending_consent_delta


async def read_pending_consent(
    *, user_id: str, bundle_id: str, conversation_id: str
) -> list:
    return await _core.read_pending_consent(
        user_id=user_id,
        bundle_id=bundle_id,
        conversation_id=conversation_id,
        property_store=_PROPERTY_STORE,
    )


async def write_pending_consent(
    *,
    user_id: str,
    bundle_id: str,
    conversation_id: str,
    providers: list,
) -> None:
    await _core.write_pending_consent(
        user_id=user_id,
        bundle_id=bundle_id,
        conversation_id=conversation_id,
        providers=providers,
        property_store=_PROPERTY_STORE,
    )


async def record_consent_demand(**kwargs: Any) -> bool:
    return await _core.record_consent_demand(
        **kwargs,
        property_store=_PROPERTY_STORE,
    )


async def author_consent_granted_events(
    *,
    redis: Any,
    user_id: str,
    provider_id: str,
    granted_claims: list | tuple,
    granted_account_scope: Mapping[str, Any] | None = None,
    granted_named_service_operations: Any = None,
    granted_resource_operations: Any = None,
    granted_resource: str = "",
    connector_app_id: str = "",
    account_id: str = "",
    invocation_policy_mode: str = "",
    invocation_policy_revision: int = 0,
    invocation_change_id: str = "",
    connection_hub_bundle_id: str = "",
    source_factory: Any = None,
) -> int:
    if source_factory is None:
        def source_factory(entry: dict[str, Any]) -> Any:
            from kdcube_ai_app.apps.chat.external_events import (
                build_conversation_external_event_source,
            )

            return build_conversation_external_event_source(
                redis=redis,
                tenant=str(entry.get("tenant") or ""),
                project=str(entry.get("project") or ""),
                conversation_id=str(entry.get("conversation_id") or ""),
                user_id=user_id,
                agent_id=str(entry.get("agent_id") or "") or "main",
            )
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.consent_denial import (
        _kdcube_requirements_loader,
    )
    async def completion_requirements(**kwargs: Any) -> Any:
        return await _kdcube_requirements_loader(**kwargs, require_registered=True)

    return await _core.author_consent_granted_events(
        redis=redis,
        user_id=user_id,
        provider_id=provider_id,
        granted_claims=granted_claims,
        granted_account_scope=granted_account_scope,
        requirements_loader=completion_requirements,
        granted_named_service_operations=granted_named_service_operations,
        granted_resource_operations=granted_resource_operations,
        granted_resource=granted_resource,
        connector_app_id=connector_app_id,
        account_id=account_id,
        invocation_policy_mode=invocation_policy_mode,
        invocation_policy_revision=invocation_policy_revision,
        invocation_change_id=invocation_change_id,
        connection_hub_bundle_id=connection_hub_bundle_id,
        source_factory=source_factory,
        property_store=_PROPERTY_STORE,
    )


def _current_agent_id() -> str:
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
            get_current_request_context,
        )

        context = get_current_request_context()
        return str(
            getattr(getattr(context, "event", None), "agent_id", "") or ""
        ).strip()
    except Exception:
        return ""


async def announce_consent_demand(
    *,
    comm: Any = None,
    payload: Any,
    provider_id: str,
    connector_app_id: str = "",
    claims: list | tuple = (),
    tool_name: str = "",
    resource: str = "",
    namespace: str = "",
    operation: str = "",
    outer_operation: str = "",
    identity: Any = None,
    connection_hub_bundle_id: str = "",
) -> bool:
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
        get_comm,
        get_current_user_identity,
    )

    return await _core.announce_consent_demand(
        comm=comm,
        payload=payload,
        provider_id=provider_id,
        connector_app_id=connector_app_id,
        claims=claims,
        tool_name=tool_name,
        resource=resource,
        namespace=namespace,
        operation=operation,
        outer_operation=outer_operation,
        identity=identity,
        connection_hub_bundle_id=connection_hub_bundle_id,
        identity_provider=get_current_user_identity,
        communicator_provider=get_comm,
        agent_id=_current_agent_id(),
        property_store=_PROPERTY_STORE,
    )


async def claim_coverage_for_policies(
    *,
    user_id: str,
    policies: list,
    connection_hub_bundle_id: str = "",
) -> dict:
    return await _core.claim_coverage_for_policies(
        user_id=user_id,
        policies=policies,
        connection_hub_bundle_id=connection_hub_bundle_id,
        store_factory=DelegatedToKdcubeStore,
    )


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))
