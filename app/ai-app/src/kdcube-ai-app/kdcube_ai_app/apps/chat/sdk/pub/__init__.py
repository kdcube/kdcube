# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/pub/__init__.py
"""Public content SDK: model, registry, rendering, and sitemap generation.

Apps declare and publish public, discoverable content; the platform generates
and serves the discoverability artifacts (crawlable HTML, JSON-LD,
canonical/OG/Twitter metadata, per-alias sitemaps).
"""

from kdcube_ai_app.apps.chat.sdk.pub.model import (
    OpenGraphDefaults,
    PublicContentAliasConfig,
    PublicContentAliasIndex,
    PublicContentImage,
    PublicContentIndexEntry,
    PublicContentItem,
    normalize_slug_path,
)
from kdcube_ai_app.apps.chat.sdk.pub.registry import PublicContentRegistry
from kdcube_ai_app.apps.chat.sdk.pub.render import (
    build_breadcrumbs_jsonld,
    build_jsonld,
    render_gone_page,
    render_item_page,
)
from kdcube_ai_app.apps.chat.sdk.pub.sitemap import render_sitemap_xml, sitemap_descriptor

__all__ = [
    "OpenGraphDefaults",
    "PublicContentAliasConfig",
    "PublicContentAliasIndex",
    "PublicContentImage",
    "PublicContentIndexEntry",
    "PublicContentItem",
    "PublicContentRegistry",
    "build_breadcrumbs_jsonld",
    "build_jsonld",
    "normalize_slug_path",
    "render_gone_page",
    "render_item_page",
    "render_sitemap_xml",
    "sitemap_descriptor",
]
