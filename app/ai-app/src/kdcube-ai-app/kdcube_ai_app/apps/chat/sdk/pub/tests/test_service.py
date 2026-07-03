# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""Public content service tests: config resolution + the serving dispatcher."""
from __future__ import annotations

import asyncio
import json

from kdcube_ai_app.apps.chat.sdk.pub.model import PublicContentItem
from kdcube_ai_app.apps.chat.sdk.pub.registry import PublicContentRegistry
from kdcube_ai_app.apps.chat.sdk.pub.service import (
    ensure_public_content_ready,
    resolve_alias_configs,
    serve_public_content,
)
from kdcube_ai_app.apps.chat.sdk.storage.bundle_artifact_storage import BundleArtifactStorage
from kdcube_ai_app.infra.plugin.bundle_loader import public_content


class _FakeApp:
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
        },
        "disabled_alias": {"enabled": False},
    }
}

_BASE = "http://localhost:8010/api/integrations/bundles/t1/p1/news@test/public/__content__"


def _seed(tmp_path) -> None:
    registry = PublicContentRegistry(
        alias="news",
        durable=BundleArtifactStorage(
            tenant="t1", project="p1", bundle_id="news@test",
            storage_uri=(tmp_path / "durable").as_uri(),
        ),
        hot_root=tmp_path / "hot",
    )
    asyncio.run(registry.publish(PublicContentItem(
        alias="news", slug="kdcube/journal/lane",
        title="The Conversation Is a Lane", summary="Deep dive.",
        body_html="<p>Body.</p>",
    )))
    asyncio.run(registry.publish(PublicContentItem(
        alias="news", slug="kdcube/journal/gone", title="Gone", state="published",
    )))
    asyncio.run(registry.retract("kdcube/journal/gone"))


def _serve(tmp_path, path_tail: str, monkeypatch=None, props=_PROPS):
    # Route the module-level BundleArtifactStorage construction at tmp durable.
    import kdcube_ai_app.apps.chat.sdk.pub.service as service_mod

    original = service_mod.BundleArtifactStorage

    def _factory(**kwargs):
        kwargs.setdefault("storage_uri", (tmp_path / "durable").as_uri())
        kwargs["storage_uri"] = (tmp_path / "durable").as_uri()
        return original(**kwargs)

    service_mod.BundleArtifactStorage = _factory
    try:
        return asyncio.run(serve_public_content(
            workflow=_FakeApp(),
            tenant="t1", project="p1", bundle_id="news@test",
            props=props,
            hot_root=tmp_path / "hot",
            path_tail=path_tail,
            serving_base_url=_BASE,
        ))
    finally:
        service_mod.BundleArtifactStorage = original


def test_resolve_alias_configs_reads_block():
    configs = resolve_alias_configs(_PROPS)
    assert configs["news"].enabled and configs["news"].canonical_base == "https://kdcube.tech/news"
    assert not configs["disabled_alias"].enabled


def test_item_page_served_with_canonical_and_jsonld(tmp_path):
    _seed(tmp_path)
    resp = _serve(tmp_path, "news/kdcube/journal/lane")
    assert resp.status_code == 200 and resp.media_type.startswith("text/html")
    page = resp.content.decode("utf-8")
    assert "<title>The Conversation Is a Lane</title>" in page
    assert 'rel="canonical" href="https://kdcube.tech/news/kdcube/journal/lane"' in page
    assert "application/ld+json" in page


def test_retracted_item_serves_410(tmp_path):
    _seed(tmp_path)
    resp = _serve(tmp_path, "news/kdcube/journal/gone")
    assert resp.status_code == 410
    assert "noindex" in resp.content.decode("utf-8")


def test_unknown_item_serves_404(tmp_path):
    _seed(tmp_path)
    assert _serve(tmp_path, "news/kdcube/journal/never").status_code == 404


def test_sitemap_lists_published_only(tmp_path):
    _seed(tmp_path)
    resp = _serve(tmp_path, "news/sitemap.xml")
    assert resp.status_code == 200 and resp.media_type.startswith("application/xml")
    xml = resp.content.decode("utf-8")
    assert "<loc>https://kdcube.tech/news/kdcube/journal/lane</loc>" in xml
    assert "gone" not in xml


def test_descriptor_list_for_host_federation(tmp_path):
    _seed(tmp_path)
    resp = _serve(tmp_path, "")
    body = json.loads(resp.content)
    assert len(body["sitemaps"]) == 1
    desc = body["sitemaps"][0]
    assert desc["alias"] == "news"
    assert desc["sitemap_url"] == f"{_BASE}/news/sitemap.xml"
    assert desc["item_count"] == 1  # retracted item excluded


def test_disabled_or_undeclared_alias_is_404(tmp_path):
    _seed(tmp_path)
    assert _serve(tmp_path, "disabled_alias/sitemap.xml").status_code == 404
    assert _serve(tmp_path, "ghost/sitemap.xml").status_code == 404
    # Explicit exposure: no props block at all -> nothing is public.
    assert _serve(tmp_path, "news/kdcube/journal/lane", props={}).status_code == 404


def test_ensure_public_content_ready_builds_cold_hot_tier(tmp_path):
    _seed(tmp_path)
    import shutil

    shutil.rmtree(tmp_path / "hot")

    import kdcube_ai_app.apps.chat.sdk.pub.service as service_mod

    original = service_mod.BundleArtifactStorage

    def _factory(**kwargs):
        kwargs["storage_uri"] = (tmp_path / "durable").as_uri()
        return original(**kwargs)

    service_mod.BundleArtifactStorage = _factory
    try:
        asyncio.run(ensure_public_content_ready(
            workflow=_FakeApp(),
            tenant="t1", project="p1", bundle_id="news@test",
            props=_PROPS,
            hot_root=tmp_path / "hot",
        ))
    finally:
        service_mod.BundleArtifactStorage = original

    assert (tmp_path / "hot" / "_public_content" / "news" / "index.json").exists()
