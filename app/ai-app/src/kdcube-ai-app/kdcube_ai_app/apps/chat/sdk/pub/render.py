# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/pub/render.py
"""Crawlable page rendering for public content items.

The platform owns the discoverability artifacts: a published item renders to a
complete HTML document with a real ``<title>``, meta description, canonical
link, Open Graph + Twitter card metadata, and JSON-LD (declared ``@type`` plus
``BreadcrumbList``) — verifiable with ``curl``, no JavaScript required. A
retracted item renders to a 410 page.

The app supplies content and metadata (:class:`PublicContentItem`); nothing
here reads app state, so the renderer cannot leak identity-scoped data.
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.pub.model import (
    PublicContentAliasConfig,
    PublicContentItem,
)


def _esc(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def _meta(name_attr: str, name: str, content: str) -> str:
    if not content:
        return ""
    return f'<meta {name_attr}="{_esc(name)}" content="{_esc(content)}" />'


def build_jsonld(
    item: PublicContentItem,
    *,
    canonical_url: str,
    site_name: str = "",
) -> Dict[str, Any]:
    """Build the JSON-LD document for one item; ``jsonld_extra`` wins on conflict."""
    doc: Dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": item.schema_type or "Article",
        "headline": item.title,
        "inLanguage": item.language or "en",
        "datePublished": item.published_at,
        "dateModified": item.lastmod,
    }
    if item.summary:
        doc["description"] = item.summary
    if canonical_url:
        doc["mainEntityOfPage"] = {"@type": "WebPage", "@id": canonical_url}
        doc["url"] = canonical_url
    if item.author:
        doc["author"] = {"@type": "Person", "name": item.author}
    if site_name:
        doc["publisher"] = {"@type": "Organization", "name": site_name}
    images = [img.url for img in item.images if img.url]
    if images:
        doc["image"] = images
    if item.tags:
        doc["keywords"] = ", ".join(item.tags)
    if item.section:
        doc["articleSection"] = item.section
    doc.update(item.jsonld_extra or {})
    return doc


def build_breadcrumbs_jsonld(
    item: PublicContentItem,
    *,
    canonical_url: str,
    alias_base_url: str = "",
    site_name: str = "",
) -> Optional[Dict[str, Any]]:
    """BreadcrumbList: site/alias root -> item. None when no canonical URL."""
    if not canonical_url:
        return None
    elements: List[Dict[str, Any]] = []
    position = 1
    if alias_base_url:
        elements.append(
            {
                "@type": "ListItem",
                "position": position,
                "name": site_name or item.alias,
                "item": alias_base_url,
            }
        )
        position += 1
    elements.append(
        {
            "@type": "ListItem",
            "position": position,
            "name": item.title,
            "item": canonical_url,
        }
    )
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": elements}


def render_item_page(
    item: PublicContentItem,
    *,
    config: PublicContentAliasConfig,
    fallback_canonical_url: str = "",
) -> str:
    """Render a published item to a complete crawlable HTML document.

    ``fallback_canonical_url`` is the serving-route URL, used when the alias
    does not configure a ``canonical_base``; when both exist, the configured
    canonical wins so shared-link equity consolidates on the clean URL.
    """
    canonical_url = config.canonical_url(item.slug) or (fallback_canonical_url or "")
    alias_base_url = (config.canonical_base or "").rstrip("/")
    og = config.og_defaults
    og_image = ""
    for img in item.images:
        if img.url:
            og_image = img.url
            break
    og_image = og_image or og.image

    head_parts: List[str] = [
        '<meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        f"<title>{_esc(item.title)}</title>",
        _meta("name", "description", item.summary),
    ]
    if canonical_url:
        head_parts.append(f'<link rel="canonical" href="{_esc(canonical_url)}" />')
    head_parts.extend(
        [
            _meta("property", "og:type", "article"),
            _meta("property", "og:title", item.title),
            _meta("property", "og:description", item.summary),
            _meta("property", "og:url", canonical_url),
            _meta("property", "og:site_name", og.site_name),
            _meta("property", "og:image", og_image),
            _meta("property", "article:published_time", item.published_at),
            _meta("property", "article:modified_time", item.lastmod),
            _meta("name", "twitter:card", "summary_large_image" if og_image else "summary"),
            _meta("name", "twitter:title", item.title),
            _meta("name", "twitter:description", item.summary),
            _meta("name", "twitter:image", og_image),
            _meta("name", "twitter:site", og.twitter_site),
        ]
    )

    jsonld_docs: List[Dict[str, Any]] = [
        build_jsonld(item, canonical_url=canonical_url, site_name=og.site_name)
    ]
    breadcrumbs = build_breadcrumbs_jsonld(
        item,
        canonical_url=canonical_url,
        alias_base_url=alias_base_url,
        site_name=og.site_name,
    )
    if breadcrumbs:
        jsonld_docs.append(breadcrumbs)
    for doc in jsonld_docs:
        payload = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
        head_parts.append(f'<script type="application/ld+json">{payload}</script>')

    head = "\n".join(part for part in head_parts if part)
    lang = _esc(item.language or "en")
    summary_html = f"<p>{_esc(item.summary)}</p>" if item.summary else ""
    return (
        "<!doctype html>\n"
        f'<html lang="{lang}">\n'
        f"<head>\n{head}\n</head>\n"
        "<body>\n"
        "<article>\n"
        f"<h1>{_esc(item.title)}</h1>\n"
        f"{summary_html}\n"
        f"{item.body_html or ''}\n"
        "</article>\n"
        "</body>\n"
        "</html>\n"
    )


def render_gone_page(slug: str) -> str:
    """The 410 body for a retracted item."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8" />\n'
        '<meta name="robots" content="noindex" />\n'
        "<title>Content removed</title>\n"
        "</head>\n"
        "<body>\n"
        f"<h1>Content removed</h1>\n<p>The item {_esc(slug)} is no longer available.</p>\n"
        "</body>\n"
        "</html>\n"
    )
