# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/pub/service.py
"""Platform-side public content service.

Glue between the app surface and the SDK primitives:

- resolves the per-alias config from app props (``public_content.<alias>``);
- constructs the tiered :class:`PublicContentRegistry` for an app;
- ensures hot indexes on app load (Moment A — many workers race; guarded);
- serves the crawlable artifacts for the reserved public route
  ``public/__content__/…``: item pages, per-alias ``sitemap.xml``, and the
  machine-readable sitemap descriptor list a host uses to federate its own
  top-level sitemap index;
- optional Data Bus change notification. The Data Bus message is a
  notification hook only — the durable registry/generation marker stays
  authoritative and consumers must resync from durable records when they miss
  messages.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.pub.model import (
    PublicContentAliasConfig,
    PublicContentItem,
)
from kdcube_ai_app.apps.chat.sdk.pub.registry import PublicContentRegistry
from kdcube_ai_app.apps.chat.sdk.pub.render import render_gone_page, render_item_page
from kdcube_ai_app.apps.chat.sdk.pub.sitemap import render_sitemap_xml, sitemap_descriptor
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import BundleBinaryResponse
from kdcube_ai_app.apps.chat.sdk.storage.bundle_artifact_storage import BundleArtifactStorage

_log = logging.getLogger("kdcube.sdk.pub.service")

DATA_BUS_SUBJECT = "public_content.changed"
CONTENT_ROUTE_SEGMENT = "__content__"

_HTML = "text/html; charset=utf-8"
_XML = "application/xml; charset=utf-8"
_JSON = "application/json; charset=utf-8"


def resolve_alias_configs(props: Optional[Dict[str, Any]]) -> Dict[str, PublicContentAliasConfig]:
    """Read the ``public_content`` block from app props.

    Exposure is explicit: an alias missing from the block, or present with
    ``enabled: false``, is not public.
    """
    block = (props or {}).get("public_content") or {}
    configs: Dict[str, PublicContentAliasConfig] = {}
    if not isinstance(block, dict):
        return configs
    for alias, raw in block.items():
        if not isinstance(raw, dict):
            continue
        try:
            configs[str(alias)] = PublicContentAliasConfig(alias=str(alias), **raw)
        except Exception:
            _log.warning("[pub.service] invalid public_content config for alias=%s (skipped)", alias)
    return configs


def build_registry(
    *,
    alias: str,
    tenant: str,
    project: str,
    bundle_id: str,
    hot_root: Any,
    logger: Optional[Any] = None,
    notifier: Optional[Any] = None,
) -> PublicContentRegistry:
    durable = BundleArtifactStorage(tenant=tenant, project=project, bundle_id=bundle_id)
    return PublicContentRegistry(
        alias=alias,
        durable=durable,
        hot_root=hot_root,
        logger=logger,
        notifier=notifier,
    )


def make_databus_notifier(
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    redis: Any | None = None,
):
    """Build a change notifier that publishes ``public_content.changed``.

    Notification only: failures are swallowed by the registry, and consumers
    (submission/syndication workers, when they land) must treat the durable
    registry as the source of truth and resync when messages are missed.
    """
    from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.publisher import DataBusPublisher

    publisher = DataBusPublisher(redis=redis, tenant=tenant, project=project, bundle_id=bundle_id)

    async def _notify(op: str, item: PublicContentItem) -> None:
        await publisher.publish(
            subject=DATA_BUS_SUBJECT,
            payload={
                "op": op,
                "alias": item.alias,
                "slug": item.slug,
                "state": item.state,
                "lastmod": item.lastmod,
            },
            idempotency_key=f"{item.alias}:{item.slug}:{item.lastmod}:{op}",
        )

    return _notify


def _manifest_aliases(workflow: Any, *, bundle_id: str) -> Dict[str, Any]:
    from kdcube_ai_app.infra.plugin.bundle_loader import discover_bundle_interface_manifest

    manifest = discover_bundle_interface_manifest(workflow, bundle_id=bundle_id)
    return {spec.alias: spec for spec in getattr(manifest, "public_content", ()) or ()}


async def ensure_public_content_ready(
    *,
    workflow: Any,
    tenant: str,
    project: str,
    bundle_id: str,
    props: Optional[Dict[str, Any]],
    hot_root: Any,
    logger: Optional[Any] = None,
) -> None:
    """Bring hot indexes current for every declared+enabled alias (app load).

    Moment A: many workers across many instances call this concurrently; the
    registry's once-per-signature guard makes it one rebuild per fleet.
    """
    if not hot_root:
        return
    declared = _manifest_aliases(workflow, bundle_id=bundle_id)
    configs = resolve_alias_configs(props)
    for alias, config in configs.items():
        if not config.enabled or alias not in declared:
            continue
        registry = build_registry(
            alias=alias,
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            hot_root=hot_root,
            logger=logger,
        )
        try:
            await registry.ensure_hot_index()
        except Exception:
            # App load must not fail because a content index is momentarily
            # unbuildable; serving falls back to durable reads per item.
            _log.warning("[pub.service] ensure_hot_index failed alias=%s bundle=%s", alias, bundle_id, exc_info=True)


def _binary(content: str, media_type: str, status_code: int = 200) -> BundleBinaryResponse:
    return BundleBinaryResponse(
        content=content.encode("utf-8"),
        media_type=media_type,
        status_code=status_code,
    )


def _not_found(detail: str) -> BundleBinaryResponse:
    return _binary(json.dumps({"detail": detail}), _JSON, status_code=404)


async def serve_public_content(
    *,
    workflow: Any,
    tenant: str,
    project: str,
    bundle_id: str,
    props: Optional[Dict[str, Any]],
    hot_root: Any,
    path_tail: str,
    serving_base_url: str,
    logger: Optional[Any] = None,
) -> BundleBinaryResponse:
    """Serve the reserved ``public/__content__/…`` route for one app.

    ``path_tail`` shapes:

    - ``""`` — machine-readable descriptor list of all enabled alias sitemaps
      (what a host reads to build its top-level sitemap index);
    - ``<alias>/sitemap.xml`` — the per-alias sitemap;
    - ``<alias>/<slug…>`` — the crawlable item page (410 when retracted).

    ``serving_base_url`` is the absolute URL of the ``__content__`` route
    root; it is the canonical fallback when the alias does not configure
    ``canonical_base``.
    """
    declared = _manifest_aliases(workflow, bundle_id=bundle_id)
    configs = resolve_alias_configs(props)
    base_url = (serving_base_url or "").rstrip("/")

    def _alias_base(alias: str) -> str:
        return f"{base_url}/{alias}" if base_url else ""

    tail = str(path_tail or "").strip().strip("/")

    if not tail:
        descriptors: List[Dict[str, Any]] = []
        for alias, config in sorted(configs.items()):
            if not config.enabled or alias not in declared or not config.sitemap:
                continue
            registry = build_registry(
                alias=alias, tenant=tenant, project=project, bundle_id=bundle_id,
                hot_root=hot_root, logger=logger,
            )
            index = await registry.read_index()
            if index is None:
                await registry.ensure_hot_index()
                index = await registry.read_index()
            if index is None:
                continue
            descriptors.append(
                sitemap_descriptor(
                    config=config,
                    sitemap_url=f"{_alias_base(alias)}/sitemap.xml",
                    index=index,
                )
            )
        return _binary(json.dumps({"sitemaps": descriptors}), _JSON)

    parts = tail.split("/")
    alias = parts[0]
    config = configs.get(alias)
    if config is None or not config.enabled or alias not in declared:
        return _not_found(f"Public content alias {alias} is not available")

    registry = build_registry(
        alias=alias, tenant=tenant, project=project, bundle_id=bundle_id,
        hot_root=hot_root, logger=logger,
    )
    rest = "/".join(parts[1:])

    if rest == "sitemap.xml":
        if not config.sitemap:
            return _not_found(f"Sitemap is not enabled for alias {alias}")
        index = await registry.read_index()
        if index is None:
            # Cold hot-tier (fresh instance): build once, guarded fleet-wide.
            await registry.ensure_hot_index()
            index = await registry.read_index()
        if index is None:
            return _not_found(f"No content index for alias {alias}")
        xml = render_sitemap_xml(index, config=config, fallback_base_url=_alias_base(alias))
        return _binary(xml, _XML)

    if not rest:
        return _not_found("Missing content slug")

    try:
        item = await registry.get_item(rest)
    except ValueError:
        return _not_found(f"Invalid content path {rest}")
    if item is None:
        return _not_found(f"No content at {rest}")
    if item.state == "retracted":
        return _binary(render_gone_page(item.slug), _HTML, status_code=410)
    page = render_item_page(
        item,
        config=config,
        fallback_canonical_url=f"{_alias_base(alias)}/{item.slug}",
    )
    return _binary(page, _HTML)
