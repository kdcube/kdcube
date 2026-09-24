# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube host bindings for Connection Hub delegated-access automation cards."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Mapping

from connection_hub.delegated_credentials.authority_config import (
    AUTHORITY_BACKEND_REDIS_MIGRATION_SOURCE,
)


_core = import_module("connection_hub.delegated_credentials.automation_access")


def _kdcube_authority_factory(*, tenant: str, project: str) -> Any:
    from kdcube_ai_app.auth.bundle import get_bundle_session_authority

    return get_bundle_session_authority(tenant=tenant, project=project)


def _kdcube_named_service_discovery_factory(
    *, redis: Any, tenant: str, project: str
) -> Any:
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import (
        RedisNamedServiceDiscovery,
    )

    return RedisNamedServiceDiscovery(redis, tenant=tenant, project=project)


def _kdcube_relay_factory() -> Any:
    from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator

    return ChatRelayCommunicator()


class AutomationAccessService(_core.AutomationAccessService):
    """Compose Connection Hub's delegated-access service with KDCube host ports."""

    def __init__(
        self,
        *,
        redis: Any,
        tenant: str,
        project: str,
        config: Any,
        grant_store: Any | None = None,
        authority_backend: str = AUTHORITY_BACKEND_REDIS_MIGRATION_SOURCE,
        authority: Any | None = None,
        catalog_resolver: Any | None = None,
        card_persistence: Any | None = None,
        minter: Any | None = None,
        named_service_discovery: Any | None = None,
        authority_factory: Any | None = None,
        named_service_discovery_factory: Any | None = None,
        relay_factory: Any | None = None,
        resource_overlay_provider: Any | None = None,
        invocation_policy_service: Any | None = None,
    ) -> None:
        super().__init__(
            redis=redis,
            tenant=tenant,
            project=project,
            config=config,
            grant_store=grant_store,
            authority_backend=authority_backend,
            authority=authority,
            catalog_resolver=catalog_resolver,
            card_persistence=card_persistence,
            minter=minter,
            named_service_discovery=named_service_discovery,
            authority_factory=authority_factory or _kdcube_authority_factory,
            named_service_discovery_factory=(
                named_service_discovery_factory
                or _kdcube_named_service_discovery_factory
            ),
            relay_factory=relay_factory or _kdcube_relay_factory,
            resource_overlay_provider=resource_overlay_provider,
            invocation_policy_service=invocation_policy_service,
        )


async def notify_delegated_access_changed(
    redis: Any,
    *,
    tenant: str,
    project: str,
    grantor_subject: str,
    action: str,
    access: Mapping[str, Any] | None = None,
    access_id: str = "",
    relay: Any = None,
    relay_factory: Any | None = None,
) -> None:
    """Emit a Connection Hub registry mutation through KDCube's chat relay."""

    await _core.notify_delegated_access_changed(
        redis,
        tenant=tenant,
        project=project,
        grantor_subject=grantor_subject,
        action=action,
        access=access,
        access_id=access_id,
        relay=relay,
        relay_factory=relay_factory or _kdcube_relay_factory,
    )


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


__all__ = list(_core.__all__)
