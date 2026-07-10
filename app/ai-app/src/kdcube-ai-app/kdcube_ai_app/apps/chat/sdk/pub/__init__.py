# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/pub/__init__.py
"""Public content SDK: model, registry, rendering, catalogs, and sitemaps.

Apps declare and publish public, discoverable content; the platform generates
and serves the discoverability artifacts (crawlable HTML, JSON-LD,
canonical/OG/Twitter metadata, per-alias sitemaps) and — when the alias
configures catalogs — the browsable listing pages with search, pagination,
site chrome, and the article-page side rail.
"""

from kdcube_ai_app.apps.chat.sdk.pub.model import (
    INDEX_SCHEMA,
    OpenGraphDefaults,
    PublicContentAliasConfig,
    PublicContentAliasIndex,
    PublicContentCatalogConfig,
    PublicContentChromeConfig,
    PublicContentChromeLink,
    PublicContentImage,
    PublicContentIndexEntry,
    PublicContentItem,
    normalize_slug_path,
)
from kdcube_ai_app.apps.chat.sdk.pub.pages import (
    build_item_shell,
    render_catalog_page,
    render_chrome_header,
)
from kdcube_ai_app.apps.chat.sdk.pub.registry import (
    PublicContentRegistry,
    index_entry_for_item,
)
from kdcube_ai_app.apps.chat.sdk.pub.render import (
    build_breadcrumbs_jsonld,
    build_jsonld,
    render_gone_page,
    render_item_page,
)
from kdcube_ai_app.apps.chat.sdk.pub.sitemap import render_sitemap_xml, sitemap_descriptor

__all__ = [
    "INDEX_SCHEMA",
    "OpenGraphDefaults",
    "PublicContentAliasConfig",
    "PublicContentAliasIndex",
    "PublicContentCatalogConfig",
    "PublicContentChromeConfig",
    "PublicContentChromeLink",
    "PublicContentImage",
    "PublicContentIndexEntry",
    "PublicContentItem",
    "PublicContentRegistry",
    "build_breadcrumbs_jsonld",
    "build_item_shell",
    "build_jsonld",
    "index_entry_for_item",
    "normalize_slug_path",
    "render_catalog_page",
    "render_chrome_header",
    "render_gone_page",
    "render_item_page",
    "render_sitemap_xml",
    "sitemap_descriptor",
]
