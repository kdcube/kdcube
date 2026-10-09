# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Non-secret Redis projections and events for bundle secret inventory."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from typing import Any

from kdcube_ai_app.infra import namespaces

LOGGER = logging.getLogger("kdcube.secrets.projections")


def bundle_secret_inventory_key(
    *,
    tenant: str,
    project: str,
    bundle_id: str,
) -> str:
    return namespaces.CONFIG.BUNDLES.SECRETS_KEYS_FMT.format(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )


def user_bundle_secret_inventory_key(
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    user_id: str,
) -> str:
    return namespaces.CONFIG.BUNDLES.USER_SECRETS_KEYS_FMT.format(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        user_id=user_id,
    )


def bundle_secret_update_channel(*, tenant: str, project: str) -> str:
    return namespaces.CONFIG.BUNDLES.SECRETS_UPDATE_CHANNEL.format(
        tenant=tenant,
        project=project,
    )


async def invalidate_bundle_secret_inventory(
    redis: Any,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    user_id: str | None = None,
) -> None:
    if redis is None:
        return
    key = (
        user_bundle_secret_inventory_key(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            user_id=user_id,
        )
        if user_id
        else bundle_secret_inventory_key(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
    )
    try:
        await redis.delete(key)
    except Exception:
        LOGGER.warning(
            "Failed to invalidate bundle secret inventory projection",
            exc_info=True,
        )


def apply_bundle_secret_update(data: Any, *, tenant: str, project: str) -> int:
    """Clear this process's secret cache for one bundles.secrets.update event; the count of entries cleared.

    The same targeted invalidation the processor's config listener performs (bundle, user, keys), for
    processes that listen through another existing listener (ingress: PlatformSettingsUpdateListener).
    An event for another tenant/project, or an unreadable one, clears nothing.
    """
    from kdcube_ai_app.apps.chat.sdk.config_cache import clear_secret_cache

    try:
        event = json.loads(data.decode("utf-8") if isinstance(data, bytes) else str(data))
    except Exception:
        return 0
    if (not isinstance(event, dict) or str(event.get("tenant") or "") != str(tenant or "")
            or str(event.get("project") or "") != str(project or "")):
        return 0
    keys = [str(key) for key in (event.get("keys") or []) if str(key)]
    return clear_secret_cache(
        tenant=tenant,
        project=project,
        bundle_id=str(event.get("bundle_id") or "").strip() or None,
        user_id=str(event.get("user_id") or "").strip() or None,
        keys=keys,
    )


async def publish_bundle_secret_update(
    redis: Any,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    scope: str,
    mode: str,
    keys: Iterable[str],
    actor: str | None = None,
    user_id: str | None = None,
) -> None:
    if redis is None:
        return
    payload: dict[str, Any] = {
        "type": "bundles.secrets.update",
        "tenant": tenant,
        "project": project,
        "bundle_id": bundle_id,
        "scope": scope,
        "mode": mode,
        "keys": sorted(str(key) for key in keys),
        "ts": time.time(),
    }
    if actor:
        payload["updated_by"] = actor
    if user_id:
        payload["user_id"] = user_id
    try:
        await redis.publish(
            bundle_secret_update_channel(tenant=tenant, project=project),
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        LOGGER.warning(
            "Failed to publish bundle secret update",
            exc_info=True,
        )


__all__ = [
    "bundle_secret_inventory_key",
    "bundle_secret_update_channel",
    "invalidate_bundle_secret_inventory",
    "publish_bundle_secret_update",
    "user_bundle_secret_inventory_key",
]
