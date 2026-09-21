# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube storage and descriptor bindings for Connection Hub serving readers."""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Any


_core = import_module("connection_hub.delegated_credentials.serving")
_LOGGER = logging.getLogger(__name__)

SERVING_RESOLVERS_ATTR = _core.SERVING_RESOLVERS_ATTR
DelegatedServingResolvers = _core.DelegatedServingResolvers
delegated_serving_resolvers = _core.delegated_serving_resolvers


def connection_hub_app_id() -> str:
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.authentication_surface import (
        connection_hub_app_id as resolve,
    )

    return resolve()


def _kdcube_storage_root_resolver(**kwargs: Any) -> Any:
    from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir

    return bundle_storage_dir(**kwargs)


async def _kdcube_bundle_props_loader(**kwargs: Any) -> Any:
    from kdcube_ai_app.infra.plugin.bundle_store import (
        get_bundle_props_from_authority,
    )

    return await get_bundle_props_from_authority(**kwargs)


async def build_delegated_serving_resolvers(
    *,
    redis: Any,
    tenant: str,
    project: str,
    bundle_id: str = "",
) -> Any:
    return await _core.build_delegated_serving_resolvers(
        redis=redis,
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        app_id_resolver=connection_hub_app_id,
        storage_root_resolver=_kdcube_storage_root_resolver,
        bundle_props_loader=_kdcube_bundle_props_loader,
    )


def delegated_card_store(*, tenant: str, project: str) -> Any | None:
    """The durable Card store over Connection Hub's shared storage, or ``None``.

    Live-grant callers without a request (the Data Bus) pass it so a Card
    whose Redis projection is missing reads through to its committed revision
    instead of reading as revoked. The store is a stateless view over files,
    so building it per lookup costs nothing.
    """
    from connection_hub.delegated_credentials.cards.store import (
        BundleStorageDelegatedCardStore,
    )

    scope_tenant = str(tenant or "").strip()
    scope_project = str(project or "").strip()
    app_id = connection_hub_app_id()
    if not app_id or not scope_tenant or not scope_project:
        return None
    try:
        root = _kdcube_storage_root_resolver(
            bundle_id=app_id,
            tenant=scope_tenant,
            project=scope_project,
            ensure=False,
        )
    except Exception:
        _LOGGER.warning(
            "[connection-hub.delegated-serving] durable Card store unavailable "
            "tenant=%s project=%s",
            scope_tenant,
            scope_project,
            exc_info=True,
        )
        return None
    return BundleStorageDelegatedCardStore(root)


async def install_delegated_serving_resolvers(
    app: Any,
    *,
    redis: Any,
    tenant: str,
    project: str,
) -> bool:
    return await _core.install_delegated_serving_resolvers(
        app,
        redis=redis,
        tenant=tenant,
        project=project,
        app_id_resolver=connection_hub_app_id,
        storage_root_resolver=_kdcube_storage_root_resolver,
        bundle_props_loader=_kdcube_bundle_props_loader,
    )


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


__all__ = [
    "SERVING_RESOLVERS_ATTR",
    "DelegatedServingResolvers",
    "build_delegated_serving_resolvers",
    "connection_hub_app_id",
    "delegated_card_store",
    "delegated_serving_resolvers",
    "install_delegated_serving_resolvers",
]
