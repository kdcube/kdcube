# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/pub/model.py
"""Public content model.

An app declares public, discoverable content items (articles, docs, catalog
entries, reports). The platform owns the discoverability artifacts generated
from these items: crawlable HTML pages, JSON-LD, canonical/OG/Twitter metadata,
and per-alias sitemaps.

Visibility vocabulary: an item is exposed because its alias is explicitly
public and the item's publication state is ``published`` — scoped by
tenant/project/app. There is no per-user audience concept on this surface.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

PublicationState = Literal["published", "retracted"]

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:[a-z0-9._-]*[a-z0-9])?$")
# Slug path: one or more slug segments joined by "/" (clean, human-readable
# permalinks — no session/auth params, no uppercase, no spaces).
_SLUG_PATH_RE = re.compile(
    r"^[a-z0-9]+(?:[a-z0-9._-]*[a-z0-9])?(?:/[a-z0-9]+(?:[a-z0-9._-]*[a-z0-9])?)*$"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_slug_path(raw: str) -> str:
    """Normalize a caller-supplied slug path; raise ValueError when unusable."""
    slug = str(raw or "").strip().strip("/").lower()
    if slug.endswith(".html"):
        slug = slug[: -len(".html")]
    if not slug or not _SLUG_PATH_RE.match(slug):
        raise ValueError(f"invalid public content slug: {raw!r}")
    return slug


class PublicContentImage(BaseModel):
    """An image attached to an item (OG card / JSON-LD image)."""

    url: str
    alt: str = ""
    width: Optional[int] = None
    height: Optional[int] = None


class PublicContentItem(BaseModel):
    """One public content item owned by an app.

    The app supplies content and metadata; the platform renders the crawlable
    page, structured data, and sitemap entry from it. ``body_html`` is the
    item's crawlable body (real text, no client rendering required).
    """

    alias: str = Field(description="Public content alias this item belongs to.")
    slug: str = Field(description="Slug path unique within the alias (clean permalink).")
    title: str
    summary: str = ""
    body_html: str = ""
    language: str = "en"
    schema_type: str = Field(
        default="Article",
        description="JSON-LD @type: Article | BlogPosting | Product | FAQPage | ...",
    )
    jsonld_extra: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extra/override JSON-LD fields merged over the generated document.",
    )
    images: List[PublicContentImage] = Field(default_factory=list)
    author: str = ""
    section: str = ""
    tags: List[str] = Field(default_factory=list)
    published_at: str = Field(default_factory=utc_now_iso)
    lastmod: str = Field(default_factory=utc_now_iso)
    state: PublicationState = "published"

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        return normalize_slug_path(value)

    @field_validator("alias")
    @classmethod
    def _validate_alias(cls, value: str) -> str:
        alias = str(value or "").strip().lower()
        if not alias or not _SLUG_RE.match(alias):
            raise ValueError(f"invalid public content alias: {value!r}")
        return alias


class PublicContentIndexEntry(BaseModel):
    """The bounded per-item record kept in the hot alias index.

    This is what the sitemap and the serving route read on the hot path; the
    full item record stays in the durable store.
    """

    slug: str
    title: str = ""
    lastmod: str = ""
    published_at: str = ""
    state: PublicationState = "published"


class PublicContentAliasIndex(BaseModel):
    """The hot per-alias index: atomically replaced, read lock-free."""

    alias: str
    generation: int = 0
    updated_at: str = Field(default_factory=utc_now_iso)
    entries: List[PublicContentIndexEntry] = Field(default_factory=list)

    def entry(self, slug: str) -> Optional[PublicContentIndexEntry]:
        for item in self.entries:
            if item.slug == slug:
                return item
        return None

    def upsert(self, entry: PublicContentIndexEntry) -> None:
        for i, existing in enumerate(self.entries):
            if existing.slug == entry.slug:
                self.entries[i] = entry
                return
        self.entries.append(entry)


class OpenGraphDefaults(BaseModel):
    """Alias-level defaults for OG/Twitter cards."""

    site_name: str = ""
    image: str = ""
    twitter_site: str = ""


class PublicContentAliasConfig(BaseModel):
    """Operator/app configuration for one public content alias.

    Declared in the app config (``bundles.yaml``) under the public content
    block. Exposure is explicit: nothing is public unless ``enabled`` is true.
    """

    alias: str
    enabled: bool = False
    base_path: str = Field(
        default="",
        description="Public path prefix for item pages under the app public route.",
    )
    canonical_base: str = Field(
        default="",
        description=(
            "Absolute base URL used for rel=canonical and sitemap entries "
            "(e.g. https://example.com/news). Operator-mapped via CDN/vanity "
            "path. Empty = derive from the serving route."
        ),
    )
    sitemap: bool = True
    og_defaults: OpenGraphDefaults = Field(default_factory=OpenGraphDefaults)

    @field_validator("alias")
    @classmethod
    def _validate_alias(cls, value: str) -> str:
        alias = str(value or "").strip().lower()
        if not alias or not _SLUG_RE.match(alias):
            raise ValueError(f"invalid public content alias: {value!r}")
        return alias

    def canonical_url(self, slug: str) -> str:
        base = (self.canonical_base or "").rstrip("/")
        if not base:
            return ""
        return f"{base}/{slug}"
