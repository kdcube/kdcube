from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.sites.registry import (
    ApplicationSiteCatalog,
    build_application_site_catalog,
)
from kdcube_ai_app.infra.namespaces import CONFIG


logger = logging.getLogger(__name__)

_PUBLISH_CATALOG_LUA = """
local generation = redis.call('INCR', KEYS[2])
local payload = cjson.decode(ARGV[2])
payload['generation'] = generation
local encoded = cjson.encode(payload)
redis.call('SET', KEYS[1], encoded)
redis.call('PUBLISH', ARGV[1], encoded)
return encoded
"""


def site_catalog_key(*, tenant: str, project: str) -> str:
    return CONFIG.SITES.CATALOG_KEY_FMT.format(tenant=tenant, project=project)


def site_catalog_update_channel(*, tenant: str, project: str) -> str:
    return CONFIG.SITES.UPDATE_CHANNEL_FMT.format(tenant=tenant, project=project)


def site_catalog_generation_key(*, tenant: str, project: str) -> str:
    return CONFIG.SITES.GENERATION_KEY_FMT.format(tenant=tenant, project=project)


def _decode_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not raw:
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


class ApplicationSiteCatalogRuntime:
    """Thread-safe holder for the immutable request-time site catalog."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._catalog: Optional[ApplicationSiteCatalog] = None

    def replace(self, catalog: ApplicationSiteCatalog) -> bool:
        with self._lock:
            previous = self._catalog
            if previous is not None:
                same_scope = (
                    previous.tenant == catalog.tenant
                    and previous.project == catalog.project
                )
                if same_scope and catalog.generation <= previous.generation:
                    return False
            self._catalog = catalog
            return True

    def snapshot(self) -> Optional[ApplicationSiteCatalog]:
        with self._lock:
            return self._catalog

    def clear(self) -> None:
        with self._lock:
            self._catalog = None


application_site_catalog_runtime = ApplicationSiteCatalogRuntime()


async def load_application_site_catalog(
    redis: Any,
    *,
    tenant: str,
    project: str,
) -> Optional[ApplicationSiteCatalog]:
    raw = await redis.get(site_catalog_key(tenant=tenant, project=project))
    if not raw:
        return None
    return ApplicationSiteCatalog.from_dict(_decode_json_object(raw))


async def publish_application_site_catalog(
    redis: Any,
    catalog: ApplicationSiteCatalog,
) -> ApplicationSiteCatalog:
    payload = catalog.to_dict()
    payload["generation"] = 0
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    projected_raw = await redis.eval(
        _PUBLISH_CATALOG_LUA,
        2,
        site_catalog_key(tenant=catalog.tenant, project=catalog.project),
        site_catalog_generation_key(tenant=catalog.tenant, project=catalog.project),
        site_catalog_update_channel(tenant=catalog.tenant, project=catalog.project),
        encoded,
    )
    return ApplicationSiteCatalog.from_dict(_decode_json_object(projected_raw))


async def refresh_application_site_catalog(
    redis: Any,
    *,
    tenant: str,
    project: str,
    applications: Mapping[str, Any],
    runtime: Optional[ApplicationSiteCatalogRuntime] = None,
    route_prefix: Any = None,
) -> ApplicationSiteCatalog:
    """Project authoritative app properties into the distributed site catalog.

    Publishing is skipped, and the stored catalog kept, when this process's
    allowed_bundle_ids predate the assembly descriptor: its registry and props
    reads omit apps the deployment now allows. A catalog whose revision equals
    the stored one is not published again, so periodic refreshes add no
    generations.
    """

    from kdcube_ai_app.infra.plugin.bundle_store import (
        allowed_bundle_ids_are_current,
        get_bundle_props,
    )

    if not hasattr(applications, "items"):
        raise TypeError("applications must be a mapping of application id to runtime spec")
    application_specs: dict[str, dict[str, Any]] = {}
    for raw_application_id, raw_spec in applications.items():
        application_id = str(raw_application_id or "").strip()
        if not application_id:
            continue
        if hasattr(raw_spec, "model_dump"):
            raw_spec = raw_spec.model_dump()
        if not isinstance(raw_spec, dict):
            raise TypeError(f"application runtime spec must be a mapping: {application_id}")
        application_specs[application_id] = dict(raw_spec)

    application_props: dict[str, dict[str, Any]] = {}
    for application_id in sorted(application_specs):
        application_props[application_id] = await get_bundle_props(
            redis,
            tenant=tenant,
            project=project,
            bundle_id=application_id,
        )

    catalog = build_application_site_catalog(
        tenant=tenant,
        project=project,
        application_props=application_props,
        application_specs=application_specs,
        route_prefix=route_prefix,
    )
    try:
        stored = await load_application_site_catalog(redis, tenant=tenant, project=project)
    except Exception:
        logger.warning(
            "Stored application site catalog is unreadable; publishing a replacement: tenant=%s project=%s",
            tenant,
            project,
            exc_info=True,
        )
        stored = None
    if not allowed_bundle_ids_are_current():
        logger.warning(
            "Application site catalog not published: this process's allowed_bundle_ids predate "
            "the assembly descriptor; restart it to project the current apps. "
            "tenant=%s project=%s kept_revision=%s",
            tenant,
            project,
            stored.revision if stored is not None else None,
        )
        if stored is not None:
            if runtime is not None:
                runtime.replace(stored)
            return stored
        return catalog
    if stored is not None and stored.revision == catalog.revision:
        if runtime is not None:
            runtime.replace(stored)
        return stored
    projected = await publish_application_site_catalog(redis, catalog)
    if runtime is not None:
        runtime.replace(projected)
    return projected


async def subscribe_application_site_catalog_updates(
    redis: Any,
    *,
    tenant: str,
    project: str,
    runtime: ApplicationSiteCatalogRuntime,
    stop_event: asyncio.Event,
) -> None:
    """Keep one proc's hot catalog synchronized without request-time Redis reads."""

    channel = site_catalog_update_channel(tenant=tenant, project=project)
    backoff = 0.5
    while not stop_event.is_set():
        pubsub = None
        try:
            pubsub = redis.pubsub()
            await pubsub.subscribe(channel)

            # Pub/sub is not durable. Load once after subscribing so a revision
            # published immediately before this subscription cannot be missed.
            current = await load_application_site_catalog(
                redis,
                tenant=tenant,
                project=project,
            )
            if current is not None:
                runtime.replace(current)
            logger.info(
                "Application site catalog subscribed: tenant=%s project=%s revision=%s sites=%s",
                tenant,
                project,
                current.revision if current else None,
                len(current.sites) if current else 0,
            )
            backoff = 0.5

            while not stop_event.is_set():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.05)
                    continue
                raw_type = message.get("type")
                if isinstance(raw_type, bytes):
                    raw_type = raw_type.decode("utf-8", "replace")
                if raw_type != "message":
                    continue
                catalog = ApplicationSiteCatalog.from_dict(
                    _decode_json_object(message.get("data"))
                )
                if catalog.tenant != tenant or catalog.project != project:
                    continue
                changed = runtime.replace(catalog)
                if changed:
                    logger.info(
                        "Application site catalog applied: tenant=%s project=%s revision=%s sites=%s",
                        tenant,
                        project,
                        catalog.revision,
                        len(catalog.sites),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Application site catalog listener failed: tenant=%s project=%s",
                tenant,
                project,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10.0)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.close()
                except Exception:
                    logger.debug("Application site catalog pubsub cleanup failed", exc_info=True)


__all__ = [
    "ApplicationSiteCatalogRuntime",
    "application_site_catalog_runtime",
    "load_application_site_catalog",
    "publish_application_site_catalog",
    "refresh_application_site_catalog",
    "site_catalog_generation_key",
    "site_catalog_key",
    "site_catalog_update_channel",
    "subscribe_application_site_catalog_updates",
]
