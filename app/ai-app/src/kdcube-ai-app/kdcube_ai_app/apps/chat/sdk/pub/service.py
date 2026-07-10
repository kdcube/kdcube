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
from typing import Any, Dict, List, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.pub.model import (
    INDEX_SCHEMA,
    PublicContentAliasConfig,
    PublicContentAliasIndex,
    PublicContentCatalogConfig,
    PublicContentIndexEntry,
    PublicContentItem,
)
from kdcube_ai_app.apps.chat.sdk.pub.pages import build_item_shell, render_catalog_page
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


# ------------------ catalog helpers ------------------


def _published_under(
    index: PublicContentAliasIndex, catalog: PublicContentCatalogConfig
) -> List[PublicContentIndexEntry]:
    """Published entries of one catalog, newest first."""
    covered = [
        e for e in index.entries if e.state == "published" and catalog.covers(e.slug)
    ]
    covered.sort(key=lambda e: (e.published_at or "", e.slug), reverse=True)
    return covered


def _catalog_counts(
    index: PublicContentAliasIndex, config: PublicContentAliasConfig
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for catalog in config.catalogs:
        counts[catalog.prefix] = sum(
            1
            for e in index.entries
            if e.state == "published" and catalog.covers(e.slug)
        )
    return counts


def _lexical_search(
    entries: List[PublicContentIndexEntry], query: str
) -> List[PublicContentIndexEntry]:
    """Rank entries by naive token overlap over title/tags/summary — the
    degrade path when the app declares no search hook (or it fails)."""
    tokens = [t for t in str(query or "").lower().split() if t]
    if not tokens:
        return []
    scored: List[tuple] = []
    for entry in entries:
        title = (entry.title or "").lower()
        summary = (entry.summary or "").lower()
        tags = " ".join(entry.tags).lower()
        score = 0
        for token in tokens:
            if token in title:
                score += 3
            if token in tags:
                score += 2
            if token in summary:
                score += 1
        if score > 0:
            scored.append((score, entry.published_at or "", entry))
    # best score first; newest first among equals
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in scored]


async def _search_hook_slugs(
    *,
    workflow: Any,
    bundle_id: str,
    alias: str,
    query: str,
    prefix: str,
    limit: int,
) -> Optional[List[str]]:
    """Run the app's declared search hook; None means degrade to lexical."""
    from kdcube_ai_app.infra.plugin.bundle_loader import discover_bundle_interface_manifest

    manifest = discover_bundle_interface_manifest(workflow, bundle_id=bundle_id)
    spec = next(
        (
            s
            for s in getattr(manifest, "public_content_search", ()) or ()
            if s.alias == alias
        ),
        None,
    )
    if spec is None:
        return None
    method = getattr(workflow, spec.method_name, None)
    if not callable(method):
        return None
    try:
        raw = await method(query, prefix=prefix, limit=limit)
    except Exception:
        _log.warning(
            "[pub.service] search hook failed alias=%s prefix=%s (degrading to lexical)",
            alias, prefix, exc_info=True,
        )
        return None
    slugs: List[str] = []
    for row in raw or []:
        if isinstance(row, str):
            slugs.append(row.strip().strip("/"))
        elif isinstance(row, Mapping):
            slug = str(row.get("slug") or "").strip().strip("/")
            if slug:
                slugs.append(slug)
    return slugs


async def _current_index(registry: PublicContentRegistry) -> Optional[PublicContentAliasIndex]:
    """The hot index, rebuilding when missing or written by an older entry
    schema (the signature carries the schema, so ensure rebuilds once)."""
    index = await registry.read_index()
    if index is None or index.index_schema < INDEX_SCHEMA:
        await registry.ensure_hot_index()
        index = await registry.read_index()
    return index


def _as_offset(value: Any) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


async def _serve_catalog_page(
    *,
    workflow: Any,
    bundle_id: str,
    config: PublicContentAliasConfig,
    catalog: PublicContentCatalogConfig,
    registry: PublicContentRegistry,
    alias_base: str,
    query_params: Mapping[str, Any],
) -> BundleBinaryResponse:
    index = await _current_index(registry)
    if index is None:
        return _not_found(f"No content index for alias {config.alias}")

    def _url_for_slug(slug: str) -> str:
        return config.canonical_url(slug) or (f"{alias_base}/{slug}" if alias_base else f"/{slug}")

    def _url_for_catalog(entry: PublicContentCatalogConfig) -> str:
        return _url_for_slug(entry.prefix)

    all_entries = _published_under(index, catalog)
    counts = _catalog_counts(index, config)
    query = str(query_params.get("q") or "").strip()
    offset = _as_offset(query_params.get("offset"))
    page_size = catalog.page_size

    if query:
        slugs = await _search_hook_slugs(
            workflow=workflow,
            bundle_id=bundle_id,
            alias=config.alias,
            query=query,
            prefix=catalog.prefix,
            limit=max(50, page_size * 5),
        )
        if slugs is None:
            results = _lexical_search(all_entries, query)
        else:
            by_slug = {e.slug: e for e in all_entries}
            results = [by_slug[s] for s in slugs if s in by_slug]
        total = len(results)
        window = results[offset:offset + page_size]
        searched = True
    else:
        total = len(all_entries)
        window = all_entries[offset:offset + page_size]
        searched = False

    page = render_catalog_page(
        config=config,
        catalog=catalog,
        entries=window,
        counts=counts,
        offset=offset,
        query=query,
        catalog_url=_url_for_catalog(catalog),
        catalog_url_for=_url_for_catalog,
        item_url_for=_url_for_slug,
        total_in_catalog=total,
        searched=searched,
    )
    return _binary(page, _HTML)


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
    query_params: Optional[Mapping[str, Any]] = None,
    logger: Optional[Any] = None,
) -> BundleBinaryResponse:
    """Serve the reserved ``public/__content__/…`` route for one app.

    ``path_tail`` shapes:

    - ``""`` — machine-readable descriptor list of all enabled alias sitemaps
      (what a host reads to build its top-level sitemap index);
    - ``<alias>/sitemap.xml`` — the per-alias sitemap;
    - ``<alias>/<catalog-prefix>`` — a configured catalog: the server-rendered,
      paginated (``?offset=``), searchable (``?q=``) listing page;
    - ``<alias>/<slug…>`` — the crawlable item page (410 when retracted);
      wrapped in chrome + catalog rail when a configured catalog covers it.

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

    # A configured catalog prefix serves the listing page, not an item.
    catalog = config.catalog(rest)
    if catalog is not None:
        return await _serve_catalog_page(
            workflow=workflow,
            bundle_id=bundle_id,
            config=config,
            catalog=catalog,
            registry=registry,
            alias_base=_alias_base(alias),
            query_params=query_params or {},
        )

    try:
        item = await registry.get_item(rest)
    except ValueError:
        return _not_found(f"Invalid content path {rest}")
    if item is None:
        return _not_found(f"No content at {rest}")
    if item.state == "retracted":
        return _binary(render_gone_page(item.slug), _HTML, status_code=410)

    # Items under a configured catalog get the site chrome + the collapsible
    # catalog rail; the crawlable document (canonical, JSON-LD, article body)
    # is unchanged. Shell failures degrade to the plain page — an article must
    # never 500 because the index is momentarily unavailable.
    head_extra = body_class = body_prefix = body_suffix = ""
    item_catalog = config.catalog_for_slug(item.slug)
    if item_catalog is not None:
        try:
            index = await _current_index(registry)
        except Exception:
            index = None
        if index is not None:
            def _url_for_slug(slug: str) -> str:
                return config.canonical_url(slug) or f"{_alias_base(alias)}/{slug}"

            head_extra, body_prefix, body_suffix = build_item_shell(
                config=config,
                catalog=item_catalog,
                entries=_published_under(index, item_catalog),
                active_slug=item.slug,
                counts=_catalog_counts(index, config),
                catalog_url=_url_for_slug(item_catalog.prefix),
                catalog_url_for=lambda c: _url_for_slug(c.prefix),
                item_url_for=_url_for_slug,
            )
            body_class = "kdcpub-body"

    page = render_item_page(
        item,
        config=config,
        fallback_canonical_url=f"{_alias_base(alias)}/{item.slug}",
        head_extra=head_extra,
        body_class=body_class,
        body_prefix=body_prefix,
        body_suffix=body_suffix,
    )
    return _binary(page, _HTML)
