---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/public-content-provider-README.md
title: "Public Content Provider"
summary: "How an app declares, publishes, and serves public discoverable content: crawlable item pages, JSON-LD, canonical/OG/Twitter metadata, per-alias sitemaps, the publish/update/retract lifecycle, and the concurrency model behind the registry."
tags: ["sdk", "bundle", "public-content", "seo", "sitemap", "jsonld", "discoverability"]
keywords: ["public content", "crawlable html", "json-ld", "sitemap", "robots", "canonical url", "open graph", "publish retract lifecycle", "content registry", "410 gone", "discoverable app content"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-storage-and-cache-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/synch-mechanisms/critical-section-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/maintenance/gateway-control-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/resource_sharing/share-static-resources-README.md
---
# Public Content Provider

An app can declare **public, discoverable content** — articles, docs, catalog
entries, public reports. The app owns the content, metadata, and publish
decisions; the platform owns the discoverability artifacts generated from
them:

- a **crawlable HTML page** per item: real `<title>`, meta description, body —
  verifiable with `curl`, no JavaScript required;
- **`rel=canonical`**, **Open Graph** and **Twitter card** metadata;
- **JSON-LD** for the declared `@type` plus a `BreadcrumbList`;
- a **per-alias `sitemap.xml`** with accurate `lastmod`;
- a **410 Gone** response after retraction;
- a machine-readable **sitemap descriptor list** a host site uses to federate
  its own top-level sitemap index.

The widget URL stays a widget shell. The crawlable item page is a separate,
platform-rendered artifact — an iframe widget is never the SEO surface.

## Visibility Vocabulary

Exposure is explicit and item-state driven:

- the alias must be **declared** in code (`@public_content`) **and enabled**
  in the app config — nothing is public by default;
- each item carries a publication state: `published` or `retracted`;
- scoping is tenant/project/app.

There are no per-user audience selectors on this surface, and no `user_type`
concept. If management APIs (publish dashboards, editorial flows) are added
later, they are protected through roles/grants/authority like any other
protected surface.

## Declaring The Surface

In the app entrypoint:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import public_content

class MyApp(BaseEntrypoint):
    @public_content(alias="news", schema_type="Article")
    async def news_items(self) -> list[PublicContentItem]:
        """Full-sync source: the app's current public items for this alias."""
        ...
```

The decorated method is the **full-sync source** — used to seed or resync the
durable registry. Day-to-day lifecycle goes through the registry API (below).

In the app config (`bundles.yaml` props):

```yaml
public_content:
  news:
    enabled: true                                  # explicit exposure
    canonical_base: "https://example.com/news"     # clean canonical prefix (CDN-mapped)
    sitemap: true
    og_defaults:
      site_name: "Example"
      image: "https://example.com/og-default.png"
      twitter_site: "@example"
```

`canonical_base` decouples the canonical URL from the serving route: the
operator maps a clean prefix (CDN behavior / vanity path) and `rel=canonical`,
JSON-LD `url`, and sitemap `<loc>` all use it, so shared-link equity
consolidates on one URL. When it is empty, the serving-route URL is used —
a local deployment still emits valid, testable artifacts.

## The Content Model

`PublicContentItem` (`kdcube_ai_app/apps/chat/sdk/pub/model.py`):

| Field | Meaning |
| --- | --- |
| `alias`, `slug` | Identity. Slugs are clean permalinks (`kdcube/journal/my-post`) — lowercase slug segments, no session/auth params. A trailing `.html` is normalized away. |
| `title`, `summary`, `body_html` | The crawlable page content. `body_html` is real HTML text. |
| `schema_type` | JSON-LD `@type`: `Article`, `BlogPosting`, `Product`, `FAQPage`, … |
| `jsonld_extra` | Extra/override JSON-LD fields merged over the generated document. |
| `images`, `author`, `section`, `tags`, `language` | Card + structured-data metadata. |
| `published_at`, `lastmod` | Timestamps; `lastmod` drives the sitemap. |
| `state` | `published` or `retracted`. |

## Lifecycle: publish / update / retract

```python
from kdcube_ai_app.apps.chat.sdk.pub import PublicContentItem, PublicContentRegistry
from kdcube_ai_app.apps.chat.sdk.pub.service import build_registry, make_databus_notifier

registry = build_registry(
    alias="news", tenant=tenant, project=project, bundle_id=bundle_id,
    hot_root=self.bundle_storage_root(),
    notifier=make_databus_notifier(tenant=tenant, project=project, bundle_id=bundle_id),
)

await registry.publish(item)     # new or replaced item; sitemap entry upserted
await registry.update(item)      # same, with an explicit lastmod bump
await registry.retract(slug)     # record kept; URL now answers 410 Gone
```

Retraction **keeps the record** (`state=retracted`): the URL answers `410
Gone` with a `noindex` body — a deliberate signal to crawlers that the page is
permanently removed, which de-indexes faster than a 404.

Every successful mutation can emit a `public_content.changed` Data Bus message
(`make_databus_notifier`). This is a **notification hook only**: the durable
registry and its generation marker remain authoritative, and consumers
(future submission/syndication workers — IndexNow, feeds, CDN invalidation)
must tolerate missed messages by resyncing from durable records.

## Serving Routes

Everything is served under the app's existing public route namespace, on the
reserved `__content__` segment (platform-owned; never dispatched to app ops):

```text
GET …/bundles/{tenant}/{project}/{bundle_id}/public/__content__
      → JSON descriptor list of enabled alias sitemaps (for host federation)

GET …/bundles/{tenant}/{project}/{bundle_id}/public/__content__/{alias}/sitemap.xml
      → the per-alias sitemap (published items only, with lastmod)

GET …/bundles/{tenant}/{project}/{bundle_id}/public/__content__/{alias}/{slug…}
      → the crawlable item page (200), or 410 when retracted, 404 when unknown
```

Acceptance check (local, no CDN required):

```bash
curl -i "$BASE/public/__content__/news/kdcube/journal/my-post"
# expect: 200, <title>, <meta name="description">, rel=canonical,
#         og:*/twitter:* metas, two application/ld+json blocks, body text

curl -i "$BASE/public/__content__/news/sitemap.xml"
# expect: 200 urlset with <loc> + <lastmod>

# after registry.retract(...):
curl -i "$BASE/public/__content__/news/kdcube/journal/my-post"   # expect: 410
```

### robots.txt and the top-level sitemap index

Host-level artifacts stay **host/deployment-owned**: `robots.txt` and the
site's sitemap **index** belong to whoever owns the domain root (the website
build, CDN config, or deployment descriptors). The platform provides what the
host references: per-alias sitemaps plus the descriptor list route, so a host
can generate its index entries without scraping.

## Storage And Concurrency Model

Two tiers (see the storage guide and Synchronization Mechanisms):

- **Durable records** — `BundleArtifactStorage` (local-fs locally, S3 on
  cloud): `public_content/<alias>/items/<slug>.json` plus a per-alias
  **generation marker** bumped on every mutation. Source of truth; the hot
  tier is always rebuildable from it.
- **Hot serving tier** — under the shared app storage root (local disk / EFS):
  `_public_content/<alias>/index.json` + mirrored item records. Item-page and
  sitemap reads never touch the durable backend on the request path. Files are
  replaced atomically, so readers are lock-free and torn reads are impossible.

Two concurrency moments:

- **App load (bootstrap/rebuild).** Many workers across many instances run
  `on_bundle_load` concurrently. `ensure_hot_index()` uses the shared
  once-per-signature guard (`run_once_for_shared_bundle_storage`): a lock-free
  fast path when the hot tier matches the durable generation; otherwise one
  fleet-wide owner rebuilds while waiters poll. Serving-side cold starts use
  `allow_existing_while_locked=True` — a seconds-stale sitemap during a
  rebuild beats a failed request.
- **Runtime mutation.** `publish`/`update`/`retract` hold an observed file
  lock on the shared hot tier for the whole critical section (durable write →
  generation bump → hot update → signature). Every writer shares the mount,
  so the lock serializes publishers across workers and instances. Readers
  never take it.

A publish landing during a load-time rebuild is safe: the rebuild's signature
was computed from generation `G`, the publish bumps to `G+1`, and the next
check rebuilds. Worst case is one redundant rebuild — never a lost publish.

All registry I/O runs off the event loop (`asyncio.to_thread`); a blocked
loop starves the once-lock heartbeat and manifests as a duplicate builder.

## Gateway, Rate Limits, And Proxies

No special-casing is required — the reserved route rides the existing public
sub-path admission, but the operational consequences are worth knowing:

- **Admission**: the gateway guarded pattern
  `…/public/[^/]+(?:/.*)?` admits `__content__/…` as an anonymous public
  route; the OpenResty/nginx templates (local compose and ECS) match only up
  to `/public/`, so multi-segment slugs pass every proxy shape.
- **Rate limits**: anonymous requests are limited **per session** (IP + user
  agent fingerprint) with the configured `rate_limits.proc.anonymous` budget.
  A crawler is one anonymous session per IP+UA. On 429 it backs off; a large
  content set under aggressive crawling may warrant raising the anonymous
  budget or adding the sitemap route to `bypass_throttling_patterns.proc` in
  `gateway.yaml`.
- **Backpressure**: anonymous traffic sheds first (`anonymous_pressure_threshold`),
  so under load crawler requests are deprioritized before user traffic —
  usually the right trade.
- The server-side Data Bus notifier publishes from proc, not through the
  Socket.IO ingress path, so `data_bus.publish` streaming limits do not apply
  to it.

## Deferred (designed, not built)

The submission/syndication pipeline attaches to the `public_content.changed`
notification and the durable registry: IndexNow POST, RSS/Atom + WebSub,
Search Console sitemap registration, CDN invalidation. Operator-owned
credentials (IndexNow key, Search Console property, analytics ids) live in
platform descriptors/secrets; when they become reusable provider credentials
they follow Connection Hub integration ownership.

## References (code)

- Model/registry/render/sitemap/service: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/pub/`
- Declaration: `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_loader.py` (`@public_content`)
- Serving dispatch: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py` (`PUBLIC_CONTENT_ROUTE_SEGMENT`)
- Load-time ensure: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py` (`_ensure_public_content_indexes`)
- Tests: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/pub/tests/`
