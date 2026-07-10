# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""Catalog serving tests: listing pages, search (hook + lexical degrade),
pagination, item-page chrome/rail shell, sitemap catalog URLs, and the
hot-index schema upgrade path."""
from __future__ import annotations

import asyncio
import pathlib

from kdcube_ai_app.apps.chat.sdk.pub.model import INDEX_SCHEMA, PublicContentItem
from kdcube_ai_app.apps.chat.sdk.pub.registry import PublicContentRegistry
from kdcube_ai_app.apps.chat.sdk.pub.service import (
    resolve_alias_configs,
    serve_public_content,
)
from kdcube_ai_app.apps.chat.sdk.storage.bundle_artifact_storage import BundleArtifactStorage
from kdcube_ai_app.infra.plugin.bundle_loader import public_content, public_content_search


class _App:
    BUNDLE_ID = "news@test"

    @public_content(alias="news")
    async def news_items(self):
        return []

    @public_content_search(alias="news")
    async def news_search(self, query: str, *, prefix: str = "", limit: int = 50):
        # Deterministic, order-bearing hook: worst-to-best reversed.
        if "boom" in query:
            raise RuntimeError("engine down")
        return [
            f"{prefix}/2026-07-01-alpha",
            f"{prefix}/2026-07-03-gamma",
        ]


class _AppNoHook:
    BUNDLE_ID = "news@test"

    @public_content(alias="news")
    async def news_items(self):
        return []


_PROPS = {
    "public_content": {
        "news": {
            "enabled": True,
            "canonical_base": "https://kdcube.tech/news",
            "sitemap": True,
            "og_defaults": {"site_name": "KDCube"},
            "catalogs": {
                "kdcube/blogs": {
                    "title": "Engineering blog",
                    "nav_label": "Blogs",
                    "eyebrow": "KDCube Press",
                    "accent": "#01BEB2",
                    "page_size": 2,
                },
                "kdcube/journal": {
                    "title": "Our Journal",
                    "nav_label": "Journal",
                    "accent": "#0969DA",
                    "background": "#F4F9FF",
                },
            },
            "chrome": {
                "brand_label": "KDCube",
                "brand_href": "https://kdcube.tech/",
                "links": [
                    {"label": "Home", "href": "https://kdcube.tech/"},
                    {"label": "Blog", "href": "https://kdcube.tech/news/kdcube/blogs"},
                ],
            },
        },
    }
}

_BASE = "http://localhost:8010/api/integrations/bundles/t1/p1/news@test/public/__content__"


def _registry(tmp_path) -> PublicContentRegistry:
    return PublicContentRegistry(
        alias="news",
        durable=BundleArtifactStorage(
            tenant="t1", project="p1", bundle_id="news@test",
            storage_uri=(tmp_path / "durable").as_uri(),
        ),
        hot_root=tmp_path / "hot",
    )


def _seed(tmp_path) -> None:
    items = [
        PublicContentItem(
            alias="news",
            slug=f"kdcube/blogs/2026-07-0{i}-{name}",
            title=f"{name.title()} article",
            summary=f"The {name} summary about delegated credentials." if name == "alpha" else f"The {name} summary.",
            tags=["mcp", "oauth"] if name == "alpha" else ["platform"],
            kicker="Deep",
            section="blogs",
            body_html=f"<p>{name} body</p>",
            published_at=f"2026-07-0{i}",
            lastmod=f"2026-07-0{i}",
        )
        for i, name in ((1, "alpha"), (2, "beta"), (3, "gamma"))
    ]
    items.append(PublicContentItem(
        alias="news", slug="kdcube/journal/2026-07-04-delta", title="Delta note",
        summary="Journal note.", section="journal", body_html="<p>delta</p>",
        published_at="2026-07-04", lastmod="2026-07-04",
    ))
    items.append(PublicContentItem(
        alias="news", slug="industry/ai/2026-07-05-eps", title="Industry digest",
        body_html="<p>digest</p>", published_at="2026-07-05", lastmod="2026-07-05",
    ))
    asyncio.run(_registry(tmp_path).publish_many(items))


def _serve(tmp_path, path_tail: str, *, workflow=None, query_params=None):
    import kdcube_ai_app.apps.chat.sdk.pub.service as service_mod

    original = service_mod.BundleArtifactStorage

    def _factory(**kwargs):
        kwargs["storage_uri"] = (tmp_path / "durable").as_uri()
        return original(**kwargs)

    service_mod.BundleArtifactStorage = _factory
    try:
        return asyncio.run(serve_public_content(
            workflow=workflow or _App(),
            tenant="t1", project="p1", bundle_id="news@test",
            props=_PROPS,
            hot_root=tmp_path / "hot",
            path_tail=path_tail,
            serving_base_url=_BASE,
            query_params=query_params or {},
        ))
    finally:
        service_mod.BundleArtifactStorage = original


def test_catalogs_mapping_form_parses():
    config = resolve_alias_configs(_PROPS)["news"]
    assert [c.prefix for c in config.catalogs] == ["kdcube/blogs", "kdcube/journal"]
    assert config.catalog("kdcube/blogs").label == "Blogs"
    assert config.chrome and config.chrome.links[1].label == "Blog"


def test_catalog_page_lists_newest_first_with_pagination(tmp_path):
    _seed(tmp_path)
    resp = _serve(tmp_path, "news/kdcube/blogs")
    assert resp.status_code == 200 and resp.media_type.startswith("text/html")
    page = resp.content.decode("utf-8")
    # newest two of three (page_size=2), fold pills with counts, chrome
    assert "Gamma article" in page and "Beta article" in page and "Alpha article" not in page
    assert "Blogs · 3" in page and "Journal · 1" in page
    assert "kdcpub-header" in page and "Engineering blog" in page
    assert "1–2 of 3" in page
    # second page
    page2 = _serve(tmp_path, "news/kdcube/blogs", query_params={"offset": "2"}).content.decode("utf-8")
    assert "Alpha article" in page2 and "Gamma article" not in page2


def test_catalog_search_uses_declared_hook_order(tmp_path):
    _seed(tmp_path)
    page = _serve(
        tmp_path, "news/kdcube/blogs", query_params={"q": "credentials"}
    ).content.decode("utf-8")
    # hook returns alpha then gamma; beta is not a result
    assert page.index("Alpha article") < page.index("Gamma article")
    assert "Beta article" not in page
    assert "results" in page and "noindex" in page


def test_catalog_search_degrades_to_lexical_without_hook(tmp_path):
    _seed(tmp_path)
    page = _serve(
        tmp_path, "news/kdcube/blogs", workflow=_AppNoHook(),
        query_params={"q": "delegated credentials"},
    ).content.decode("utf-8")
    assert "Alpha article" in page and "Beta article" not in page


def test_catalog_search_degrades_to_lexical_when_hook_raises(tmp_path):
    _seed(tmp_path)
    page = _serve(
        tmp_path, "news/kdcube/blogs", query_params={"q": "boom delegated"}
    ).content.decode("utf-8")
    # hook raised; lexical still matches alpha via summary
    assert "Alpha article" in page


def test_item_under_catalog_gets_chrome_and_rail(tmp_path):
    _seed(tmp_path)
    page = _serve(tmp_path, "news/kdcube/blogs/2026-07-02-beta").content.decode("utf-8")
    assert "kdcpub-rail-card" in page and "kdcpub-header" in page
    assert "kdcpub-now" in page  # active rail item
    assert 'rel="canonical" href="https://kdcube.tech/news/kdcube/blogs/2026-07-02-beta"' in page
    assert "<p>beta body</p>" in page
    # crawlable metadata intact alongside the shell
    assert "application/ld+json" in page


def test_item_outside_catalogs_stays_plain(tmp_path):
    _seed(tmp_path)
    page = _serve(tmp_path, "news/industry/ai/2026-07-05-eps").content.decode("utf-8")
    assert "kdcpub-rail-card" not in page and "kdcpub-header" not in page


def test_sitemap_includes_catalog_urls(tmp_path):
    _seed(tmp_path)
    xml = _serve(tmp_path, "news/sitemap.xml").content.decode("utf-8")
    assert "<loc>https://kdcube.tech/news/kdcube/blogs</loc>" in xml
    assert "<loc>https://kdcube.tech/news/kdcube/journal</loc>" in xml
    assert "<loc>https://kdcube.tech/news/kdcube/blogs/2026-07-01-alpha</loc>" in xml


def test_old_schema_hot_index_is_rebuilt_on_catalog_serve(tmp_path):
    _seed(tmp_path)
    hot_dir = tmp_path / "hot" / "_public_content" / "news"
    # Simulate a hot tier written by the previous release: schema-1 index
    # (entries without card fields) and the old gen-only signature format.
    index_path = hot_dir / "index.json"
    index_path.write_text(
        '{"alias":"news","generation":1,"updated_at":"2026-07-01T00:00:00+00:00",'
        '"entries":[{"slug":"kdcube/blogs/2026-07-03-gamma","title":"Gamma article",'
        '"lastmod":"2026-07-03","published_at":"2026-07-03","state":"published"}]}',
        encoding="utf-8",
    )
    (hot_dir / ".index.signature").write_text("gen:1\n", encoding="utf-8")
    page = _serve(tmp_path, "news/kdcube/blogs").content.decode("utf-8")
    # rebuilt from durable: all three blog items back, with summaries (schema 2)
    assert "Gamma article" in page and "Beta article" in page
    assert "The gamma summary." in page
    rebuilt = (hot_dir / "index.json").read_text(encoding="utf-8")
    assert f'"index_schema":{INDEX_SCHEMA}' in rebuilt
    assert pathlib.Path(hot_dir / ".index.signature").read_text().startswith("gen:")
