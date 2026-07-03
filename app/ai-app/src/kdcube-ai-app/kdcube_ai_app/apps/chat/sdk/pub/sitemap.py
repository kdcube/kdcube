# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/pub/sitemap.py
"""Per-alias sitemap generation.

The platform serves one ``sitemap.xml`` per public content alias, listing the
canonical URL and accurate ``lastmod`` of every *published* item from the hot
alias index (retracted items are dropped — their URLs answer 410).

Host-level ``robots.txt`` and the top-level sitemap **index** stay
host/deployment-owned: a site references the per-alias sitemap URLs from its
own index. ``sitemap_descriptor`` is the machine-readable handle a host can
use to build that reference without scraping.
"""
from __future__ import annotations

import html
from typing import Any, Dict, List

from kdcube_ai_app.apps.chat.sdk.pub.model import (
    PublicContentAliasConfig,
    PublicContentAliasIndex,
)


def _esc(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def render_sitemap_xml(
    index: PublicContentAliasIndex,
    *,
    config: PublicContentAliasConfig,
    fallback_base_url: str = "",
) -> str:
    """Render the alias urlset. ``fallback_base_url`` mirrors the render-layer
    rule: the configured ``canonical_base`` wins; otherwise entries resolve
    against the serving route so a local deployment still emits valid URLs."""
    base = (config.canonical_base or "").rstrip("/") or (fallback_base_url or "").rstrip("/")
    lines: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for entry in index.entries:
        if entry.state != "published":
            continue
        if not base:
            continue
        lines.append("  <url>")
        lines.append(f"    <loc>{_esc(f'{base}/{entry.slug}')}</loc>")
        if entry.lastmod:
            lines.append(f"    <lastmod>{_esc(entry.lastmod)}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def sitemap_descriptor(
    *,
    config: PublicContentAliasConfig,
    sitemap_url: str,
    index: PublicContentAliasIndex,
) -> Dict[str, Any]:
    """Machine-readable descriptor of one alias sitemap, for host-level
    federation (the host's sitemap index references ``sitemap_url``)."""
    published = [e for e in index.entries if e.state == "published"]
    lastmod = max((e.lastmod for e in published if e.lastmod), default="")
    return {
        "alias": config.alias,
        "sitemap_url": sitemap_url,
        "canonical_base": config.canonical_base,
        "item_count": len(published),
        "lastmod": lastmod,
        "generation": index.generation,
    }
