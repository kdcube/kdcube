# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube discovery and account-store bindings for Connection Hub consent denials."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Mapping, Sequence


_core = import_module("connection_hub.delegated_credentials.consent_denial")

delegated_credential_view = _core.delegated_credential_view
agent_client_id_from_request = _core.agent_client_id_from_request
granted_resource_from_request = _core.granted_resource_from_request
connection_hub_grant_url = _core.connection_hub_grant_url
agent_grant_consent_denial = _core.agent_grant_consent_denial
card_capability_consent_denial = _core.card_capability_consent_denial

AGENT_CLIENT_PREFIX = _core.AGENT_CLIENT_PREFIX
CONSENT_NEEDED_CODE = _core.CONSENT_NEEDED_CODE
DELEGATED_CONSENT_REQUIRED = _core.DELEGATED_CONSENT_REQUIRED


async def _kdcube_requirements_loader(
    *, namespace: str, tenant: str, project: str
) -> Sequence[Mapping[str, Any]]:
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import (
        RedisNamedServiceDiscovery,
        _redis_client_from_settings,
    )

    discovery = RedisNamedServiceDiscovery(
        _redis_client_from_settings(),
        tenant=tenant,
        project=project,
    )
    entries = await discovery.entries_for_namespace(namespace)
    requirements: list[Mapping[str, Any]] = []
    for entry in entries or ():
        spec = getattr(entry, "spec", None)
        metadata = dict(getattr(spec, "metadata", None) or {})
        raw = metadata.get("connected_accounts")
        if isinstance(raw, (list, tuple)):
            requirements.extend(
                item for item in raw if isinstance(item, Mapping)
            )
    return requirements


async def _kdcube_accounts_loader(
    *, grantor_user_id: str, provider_id: str
) -> Sequence[Any]:
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_to_kdcube.store import (
        DelegatedToKdcubeStore,
    )

    return await DelegatedToKdcubeStore(
        user_id=grantor_user_id
    ).list_accounts(provider_id=provider_id)


def _kdcube_consent_payload_builder(**kwargs: Any) -> dict[str, Any]:
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_to_kdcube.preflight import (
        connected_account_consent_payload,
    )

    return connected_account_consent_payload(**kwargs)


def _kdcube_connector_app_resolver(provider_id: str) -> str:
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connector_app_resolution import (
        resolve_connector_app_id,
    )

    return resolve_connector_app_id(provider_id)


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
    return await _core.connect_first_denial_for_identity(
        grantor_user_id=grantor_user_id,
        agent_client_id=agent_client_id,
        agent_resource=agent_resource,
        namespace=namespace,
        tool=tool,
        operation=operation,
        required=required,
        missing=missing,
        tenant=tenant,
        project=project,
        hub_bundle_id=hub_bundle_id,
        requirements=requirements,
        requirements_loader=_kdcube_requirements_loader,
        accounts_loader=_kdcube_accounts_loader,
        consent_payload_builder=_kdcube_consent_payload_builder,
        connector_app_resolver=_kdcube_connector_app_resolver,
    )


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


__all__ = list(_core.__all__)
