---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/public-content-provider-README.md
title: "Public Content Provider"
summary: "The app-surface contract for public discoverable content: the @public_content declaration, the provider method, the public_content.<alias> config block (explicit exposure, canonical_base, singleton requirement), and how app code drives publish/update/retract through the registry."
tags: ["sdk", "bundle", "public-content", "seo", "sitemap", "declaration", "config"]
keywords: ["public content provider", "@public_content", "public_content config block", "canonical_base", "publish retract lifecycle", "content registry api", "singleton content provider", "publish to web"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/cdn-pub/public-content-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/resource_sharing/publish-discoverable-content-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
---
# Public Content Provider

An app can declare **public, discoverable content** — articles, docs, catalog
entries, public reports. The app owns the content, metadata, and publish
decisions; the platform's [cdn-pub solution](../solutions/cdn-pub/public-content-solution-README.md)
owns everything generated from them: crawlable item pages, JSON-LD,
canonical/OG/Twitter metadata, the per-alias `sitemap.xml`, and `410 Gone`
after retraction.

This page is the **app-surface contract**: what an app declares, configures,
and calls. How the machinery works (registry tiers, serving route, CDN
deployment, gateway behavior) is the solution doc; a hands-on walkthrough is
the [publishing recipe](../../recipes/resource_sharing/publish-discoverable-content-README.md).

The widget URL stays a widget shell. The crawlable item page is a separate,
platform-rendered artifact — an iframe widget is never the SEO surface.

## Declaration

```python
from kdcube_ai_app.infra.plugin.bundle_loader import public_content
from kdcube_ai_app.apps.chat.sdk.pub import PublicContentItem

class MyApp(BaseEntrypoint):
    @public_content(alias="articles", schema_type="Article")
    async def public_articles(self) -> list[PublicContentItem]:
        """Full-sync source: the app's current public items for this alias."""
        ...
```

- One method per alias; duplicate aliases are rejected at discovery.
- The decorated method is the **full-sync source** — used to seed or resync
  the registry (first enablement, bulk changes). Day-to-day lifecycle goes
  through the registry API (below), not through re-running this method.
- `schema_type` is the default JSON-LD `@type`; items can override it.
- Exposure is explicit and item-state driven: the alias must ALSO be enabled
  in config, and each item carries `published` / `retracted` state. There are
  no per-user audience selectors on this surface (no `user_types`).

## Configuration

In the app config (`bundles.yaml` props):

```yaml
public_content:
  articles:
    enabled: true                                  # explicit exposure — off by default
    canonical_base: "https://example.com/articles" # clean canonical prefix (CDN-mapped)
    sitemap: true
    og_defaults:
      site_name: "Example"
      image: "https://example.com/og-default.png"
      twitter_site: "@example"
```

- `enabled: false` (or an absent block) means nothing is public and app-side
  lifecycle calls are no-ops — safe to ship wiring ahead of enablement.
- `canonical_base` decouples the canonical URL from the serving route: the
  operator maps a clean prefix (CDN behavior / reverse-proxy rewrite) and
  `rel=canonical`, JSON-LD `url`, and sitemap `<loc>` all use it. Empty =
  serving-route URLs (fine for local verification).

### Singleton requirement

A content-provider app should declare **`singleton: true`** in its
descriptor. Every crawler request resolves the app instance to check the
declaration and the config gate (no app operation code runs, but the
instance is constructed); a non-singleton app would build a fresh entrypoint
per crawled page, which is pointless work under crawl traffic.

## Driving The Lifecycle From App Code

```python
from kdcube_ai_app.apps.chat.sdk.pub import PublicContentItem
from kdcube_ai_app.apps.chat.sdk.pub.service import build_registry, make_databus_notifier

registry = build_registry(
    alias="articles", tenant=tenant, project=project, bundle_id=bundle_id,
    hot_root=self.bundle_storage_root(),
    notifier=make_databus_notifier(tenant=tenant, project=project,
                                   bundle_id=bundle_id, redis=self.redis),
)

await registry.publish(item)     # new or replaced item; sitemap entry upserted
await registry.update(item)      # same, with an explicit lastmod bump
await registry.retract(slug)     # record kept; the URL answers 410 Gone
```

The proven wiring pattern (reference implementation):

- a small **adapter module** owning the domain→item mapping and a
  channel/section→slug-prefix map as the single exposure source;
- an **enabled-gated accessor** on the entrypoint returning the adapter only
  when the config block is enabled (so hooks are no-ops while the surface is
  off);
- **hooks after every content-mutating operation** — publish/delete admin
  ops mirror into the registry after success; hook failures are logged
  warnings and never fail the editorial action;
- an **idempotent seed operation** (admin-gated) calling the full sync — for
  first enablement, after bulk archive changes, and as a "Publish to Web"
  button in the app's admin widget.

Keep `lastmod` **stable for unchanged content** (derive it from the item's
own update time, never from "now") — otherwise an idempotent re-seed churns
every sitemap `<lastmod>` and falsely signals crawlers that everything
changed.

## What The Platform Serves For You

Once declared + enabled, with no further app code:

```text
GET …/public/__content__                         descriptor list (host federation)
GET …/public/__content__/{alias}/sitemap.xml     per-alias sitemap
GET …/public/__content__/{alias}/{slug…}         crawlable item page / 410 / 404
```

## Acceptance Check

```bash
curl -i "$BASE/public/__content__/articles/my/first-post"
# expect: 200, <title>, <meta name="description">, rel=canonical,
#         og:*/twitter:* metas, two application/ld+json blocks, body text — no JS needed

curl -i "$BASE/public/__content__/articles/sitemap.xml"
# expect: 200 urlset with <loc> + <lastmod>

# after registry.retract(...):
curl -i "$BASE/public/__content__/articles/my/first-post"   # expect: 410
```

Also assert the widget URL (`…/public/widgets/<alias>`) still serves the
widget shell — it must not become the SEO page.
