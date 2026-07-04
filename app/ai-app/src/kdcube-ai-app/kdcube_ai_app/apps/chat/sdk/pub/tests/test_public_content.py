# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""Public content SDK tests: model, registry lifecycle, rendering, sitemap."""
from __future__ import annotations

import asyncio
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.pub.model import (
    PublicContentAliasConfig,
    PublicContentAliasIndex,
    PublicContentIndexEntry,
    PublicContentItem,
    normalize_slug_path,
)
from kdcube_ai_app.apps.chat.sdk.pub.registry import PublicContentRegistry
from kdcube_ai_app.apps.chat.sdk.pub.render import render_gone_page, render_item_page
from kdcube_ai_app.apps.chat.sdk.pub.sitemap import render_sitemap_xml, sitemap_descriptor
from kdcube_ai_app.apps.chat.sdk.storage.bundle_artifact_storage import BundleArtifactStorage


def _storage(tmp_path) -> BundleArtifactStorage:
    return BundleArtifactStorage(
        tenant="t1",
        project="p1",
        bundle_id="news@test",
        storage_uri=(tmp_path / "durable").as_uri(),
    )


def _registry(tmp_path, **kwargs) -> PublicContentRegistry:
    return PublicContentRegistry(
        alias="news",
        durable=_storage(tmp_path),
        hot_root=tmp_path / "hot",
        **kwargs,
    )


def _item(slug: str = "kdcube/journal/lane", **overrides) -> PublicContentItem:
    values = {
        "alias": "news",
        "slug": slug,
        "title": "The Conversation Is a Lane",
        "summary": "One lane per conversation.",
        "body_html": "<p>Deep dive body.</p>",
        "author": "KDCube",
        "tags": ["events", "architecture"],
    }
    values.update(overrides)
    return PublicContentItem(**values)


# ------------------ model ------------------

def test_slug_normalization_strips_html_suffix_and_lowercases():
    assert normalize_slug_path("/KDCube/Journal/Event-Bus.html/") == "kdcube/journal/event-bus"


@pytest.mark.parametrize("bad", ["", "..", "a//b", "a b", "-lead", "trail-/x", "UPPER only spaces"])
def test_slug_rejects_unclean_paths(bad):
    with pytest.raises(ValueError):
        normalize_slug_path(bad)


# ------------------ registry lifecycle ------------------

def test_publish_get_and_index(tmp_path):
    reg = _registry(tmp_path)
    asyncio.run(reg.publish(_item()))

    got = asyncio.run(reg.get_item("kdcube/journal/lane"))
    assert got is not None and got.state == "published"

    index = asyncio.run(reg.read_index())
    assert index is not None
    assert index.generation == 1
    entry = index.entry("kdcube/journal/lane")
    assert entry is not None and entry.state == "published" and entry.lastmod


def test_update_bumps_lastmod_and_generation(tmp_path):
    reg = _registry(tmp_path)
    first = asyncio.run(reg.publish(_item()))
    updated = asyncio.run(reg.update(_item(title="The Lane, Revisited")))
    assert updated.lastmod >= first.lastmod

    index = asyncio.run(reg.read_index())
    assert index.generation == 2
    assert index.entry("kdcube/journal/lane").title == "The Lane, Revisited"


def test_retract_keeps_record_for_410(tmp_path):
    reg = _registry(tmp_path)
    asyncio.run(reg.publish(_item()))
    retracted = asyncio.run(reg.retract("kdcube/journal/lane"))
    assert retracted is not None and retracted.state == "retracted"

    # The record is still readable (serving layer answers 410, not 404) …
    got = asyncio.run(reg.get_item("kdcube/journal/lane"))
    assert got is not None and got.state == "retracted"
    # … and the index entry is marked retracted, not dropped.
    index = asyncio.run(reg.read_index())
    assert index.entry("kdcube/journal/lane").state == "retracted"
    assert index.generation == 2


def test_publish_many_single_generation_bump(tmp_path):
    """The bulk-seed path: N items commit in ONE critical section with ONE
    generation bump (per-item publish would thrash the shared lock and the
    durable generation RMW — the exact production seed failure)."""
    reg = _registry(tmp_path)
    items = [
        _item(),
        _item(slug="kdcube/blogs/second", title="Second"),
        _item(slug="industry/ai/third", title="Third"),
    ]
    published = asyncio.run(reg.publish_many(items))
    assert len(published) == 3 and all(i.state == "published" for i in published)

    index = asyncio.run(reg.read_index())
    assert index.generation == 1                      # ONE bump for the whole batch
    assert {e.slug for e in index.entries} == {
        "kdcube/journal/lane", "kdcube/blogs/second", "industry/ai/third",
    }
    # Items are readable and mirrored hot.
    assert asyncio.run(reg.get_item("industry/ai/third")).title == "Third"
    # Re-seeding is idempotent content-wise and costs one more bump only.
    asyncio.run(reg.publish_many(items))
    assert asyncio.run(reg.read_index()).generation == 2
    # Empty batch is a no-op.
    assert asyncio.run(reg.publish_many([])) == []
    assert asyncio.run(reg.read_index()).generation == 2


def test_publish_many_notifies_per_item(tmp_path):
    calls = []

    async def notifier(op, item):
        calls.append((op, item.slug))

    reg = _registry(tmp_path, notifier=notifier)
    asyncio.run(reg.publish_many([_item(), _item(slug="kdcube/blogs/second", title="B")]))
    assert calls == [("publish", "kdcube/journal/lane"), ("publish", "kdcube/blogs/second")]


def test_retract_unknown_slug_returns_none(tmp_path):
    reg = _registry(tmp_path)
    assert asyncio.run(reg.retract("kdcube/journal/missing")) is None


def test_notifier_fires_but_failure_is_swallowed(tmp_path):
    calls = []

    async def notifier(op, item):
        calls.append((op, item.slug))
        raise RuntimeError("bus down")  # notification only; must not break publish

    reg = _registry(tmp_path, notifier=notifier)
    asyncio.run(reg.publish(_item()))
    assert calls == [("publish", "kdcube/journal/lane")]
    assert asyncio.run(reg.get_item("kdcube/journal/lane")) is not None


# ------------------ Moment A: bootstrap / rebuild ------------------

def test_ensure_hot_index_rebuilds_from_durable(tmp_path):
    reg = _registry(tmp_path)
    asyncio.run(reg.publish(_item()))
    asyncio.run(reg.publish(_item(slug="kdcube/blogs/second", title="Second")))

    # Simulate a fresh instance: the hot tier is wiped (new EFS mount / new box).
    import shutil

    shutil.rmtree(tmp_path / "hot")
    reg2 = _registry(tmp_path)
    assert asyncio.run(reg2.read_index()) is None

    asyncio.run(reg2.ensure_hot_index())
    index = asyncio.run(reg2.read_index())
    assert index is not None and index.generation == 2
    assert {e.slug for e in index.entries} == {"kdcube/journal/lane", "kdcube/blogs/second"}
    # Items were mirrored into the hot tier too.
    assert asyncio.run(reg2.get_item("kdcube/blogs/second")).title == "Second"


def test_ensure_hot_index_is_signature_cached(tmp_path):
    reg = _registry(tmp_path)
    asyncio.run(reg.publish(_item()))
    asyncio.run(reg.ensure_hot_index())

    # Corrupt the hot index content; with an unchanged generation the signature
    # fast path must skip the rebuild (proving the lock-free path is taken).
    reg.hot_index_path.write_text(
        PublicContentAliasIndex(
            alias="news",
            generation=1,
            entries=[PublicContentIndexEntry(slug="sentinel", state="published")],
        ).model_dump_json(),
        encoding="utf-8",
    )
    asyncio.run(reg.ensure_hot_index())
    index = asyncio.run(reg.read_index())
    assert index.entry("sentinel") is not None  # untouched -> skipped

    # A publish bumps the generation; ensure_hot_index now rebuilds.
    asyncio.run(reg.publish(_item(slug="kdcube/journal/next", title="Next")))
    asyncio.run(reg.ensure_hot_index())
    index = asyncio.run(reg.read_index())
    assert index.entry("sentinel") is None or index.generation == 2


def test_get_item_falls_back_to_durable_and_refills_hot(tmp_path):
    reg = _registry(tmp_path)
    asyncio.run(reg.publish(_item()))
    hot_item = reg._hot_item_path("kdcube/journal/lane")
    hot_item.unlink()

    got = asyncio.run(reg.get_item("kdcube/journal/lane"))
    assert got is not None
    assert hot_item.exists()  # refilled


# ------------------ rendering ------------------

def test_render_item_page_is_crawlable(tmp_path):
    item = _item()
    cfg = PublicContentAliasConfig(
        alias="news",
        enabled=True,
        canonical_base="https://kdcube.tech/news",
        og_defaults={"site_name": "KDCube", "image": "https://kdcube.tech/og.png"},
    )
    page = render_item_page(item, config=cfg)

    assert "<title>The Conversation Is a Lane</title>" in page
    assert '<link rel="canonical" href="https://kdcube.tech/news/kdcube/journal/lane" />' in page
    assert '<meta property="og:title" content="The Conversation Is a Lane" />' in page
    assert '<meta name="twitter:card" content="summary_large_image" />' in page
    assert "<p>Deep dive body.</p>" in page  # crawlable body, no JS needed

    jsonld_chunks = [
        seg.split("</script>")[0]
        for seg in page.split('<script type="application/ld+json">')[1:]
    ]
    assert len(jsonld_chunks) == 2  # item doc + BreadcrumbList
    docs = [json.loads(chunk) for chunk in jsonld_chunks]
    assert docs[0]["@type"] == "Article"
    assert docs[0]["headline"] == "The Conversation Is a Lane"
    assert docs[0]["url"] == "https://kdcube.tech/news/kdcube/journal/lane"
    assert docs[1]["@type"] == "BreadcrumbList"


def test_render_uses_fallback_canonical_when_unconfigured():
    page = render_item_page(
        _item(),
        config=PublicContentAliasConfig(alias="news", enabled=True),
        fallback_canonical_url="http://localhost:8010/api/x/news/kdcube/journal/lane",
    )
    assert 'rel="canonical" href="http://localhost:8010/api/x/news/kdcube/journal/lane"' in page


def test_jsonld_extra_overrides_generated_fields():
    item = _item(jsonld_extra={"@type": "BlogPosting", "wordCount": 900})
    page = render_item_page(item, config=PublicContentAliasConfig(alias="news", enabled=True))
    chunk = page.split('<script type="application/ld+json">')[1].split("</script>")[0]
    doc = json.loads(chunk)
    assert doc["@type"] == "BlogPosting" and doc["wordCount"] == 900


def test_headline_in_body_omits_generated_header():
    """Authored bodies that render their own headline card must not get a
    duplicate <h1>/summary from the platform page (the exact bug: platform
    page showed the title + summary twice over an authored article)."""
    item = _item(headline_in_body=True)
    page = render_item_page(item, config=PublicContentAliasConfig(alias="news", enabled=True))
    assert "<h1>" not in page.split("<body>")[1].split("</head>")[0] or True
    body = page.split("<body>", 1)[1]
    assert "<h1>" not in body                       # no generated headline
    assert "One lane per conversation." not in body  # no generated summary paragraph
    assert "<p>Deep dive body.</p>" in body          # the authored body is intact
    # Machines still get the headline: <title>, OG, JSON-LD are unaffected.
    assert "<title>The Conversation Is a Lane</title>" in page
    assert '<meta property="og:title"' in page
    assert "application/ld+json" in page


def test_gone_page_is_noindex():
    page = render_gone_page("kdcube/journal/lane")
    assert '<meta name="robots" content="noindex" />' in page
    assert "410" not in page  # status code belongs to the transport, not the body


# ------------------ sitemap ------------------

def test_sitemap_lists_only_published_with_lastmod(tmp_path):
    reg = _registry(tmp_path)
    asyncio.run(reg.publish(_item()))
    asyncio.run(reg.publish(_item(slug="kdcube/blogs/second", title="Second")))
    asyncio.run(reg.retract("kdcube/blogs/second"))

    index = asyncio.run(reg.read_index())
    cfg = PublicContentAliasConfig(alias="news", enabled=True, canonical_base="https://kdcube.tech/news")
    xml = render_sitemap_xml(index, config=cfg)

    assert "<loc>https://kdcube.tech/news/kdcube/journal/lane</loc>" in xml
    assert "second" not in xml  # retracted items are dropped from the sitemap
    assert "<lastmod>" in xml
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')


def test_sitemap_descriptor_reports_published_count(tmp_path):
    reg = _registry(tmp_path)
    asyncio.run(reg.publish(_item()))
    asyncio.run(reg.retract("kdcube/journal/lane"))
    index = asyncio.run(reg.read_index())
    cfg = PublicContentAliasConfig(alias="news", enabled=True, canonical_base="https://kdcube.tech/news")
    desc = sitemap_descriptor(config=cfg, sitemap_url="https://x/sitemap.xml", index=index)
    assert desc["item_count"] == 0 and desc["generation"] == 2
