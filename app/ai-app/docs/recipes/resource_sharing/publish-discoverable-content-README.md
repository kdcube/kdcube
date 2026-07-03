---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/resource_sharing/publish-discoverable-content-README.md
title: "Publish Discoverable Content From An App"
summary: "Step-by-step recipe for making an app a public content provider: declare @public_content, adapt your domain objects to PublicContentItem, wire publish/retract hooks and a Publish-to-Web seed, configure the public_content block (singleton, canonical_base), verify with curl, map clean canonical URLs, and federate into the site's robots.txt and sitemap index."
status: active
tags: ["recipes", "resource-sharing", "public-content", "seo", "sitemap", "jsonld", "crawlable", "cdn", "bundle"]
updated_at: 2026-07-03
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/public-content-provider-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/cdn-pub/public-content-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/resource_sharing/share-static-resources-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
---
# Publish Discoverable Content From An App

[Share Static Resources](share-static-resources-README.md) gives one page a
public URL. This recipe goes the rest of the way: it makes your app's content
set **discoverable** — every item gets a crawlable HTML page with real
title/meta/body (no JavaScript needed), JSON-LD structured data,
canonical/OG/Twitter metadata, an entry in a `sitemap.xml` with accurate
`lastmod`, and a proper `410 Gone` after removal. Search engines and AI
crawlers can find, index, and cite your content.

You write the **content mapping and the publish decisions**. The platform
renders and serves every discoverability artifact — you never write a
sitemap, a JSON-LD block, or an OG tag by hand.

Worked example throughout: an app with an `articles` content set. Slugs are
path-shaped, so one alias can carry sections:

```text
articles/guides/getting-started
articles/blog/2026-07-01-launch
```

## Step 1 — Declare the surface

One decorated method per alias on your entrypoint. It returns the app's
current public items — the **full-sync source** (used for seeding/reseeding,
not on every request):

```python
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, public_content
from kdcube_ai_app.apps.chat.sdk.pub import PublicContentItem

@bundle_entrypoint(...)
class MyApp(BaseEntrypoint):

    @public_content(alias="articles", schema_type="Article")
    async def public_articles(self) -> list[PublicContentItem]:
        store = await self._store()
        return [
            _to_public_item(record)
            for record in store.list_published()
            if _to_public_item(record) is not None
        ]
```

## Step 2 — Map your domain objects to items

Keep the mapping in one small module (single source for what is public):

```python
from kdcube_ai_app.apps.chat.sdk.pub import PublicContentItem

# Your sections -> slug prefixes. THE exposure map: anything absent here
# is not public. Adding a section later = adding one entry.
SECTION_TO_PREFIX = {
    "guides": "guides",
    "blog": "blog",
}

def _to_public_item(record) -> PublicContentItem | None:
    prefix = SECTION_TO_PREFIX.get(record.section)
    if not prefix or not record.title or not record.body_html:
        return None
    return PublicContentItem(
        alias="articles",
        slug=f"{prefix}/{record.slug}",
        title=record.title,
        summary=record.summary,
        body_html=record.body_html,          # real HTML text — this is what crawlers read
        tags=record.tags,
        author=record.author,
        published_at=record.published_date,  # e.g. "2026-07-01"
        # lastmod must be STABLE for unchanged content — derive it from the
        # record's own update time, never from "now", or an idempotent reseed
        # will churn every sitemap <lastmod>.
        lastmod=record.updated_at or record.published_date,
    )
```

Rules that matter:

- **Slugs are clean permalinks**: lowercase slug segments joined by `/`
  (`a-z0-9`, `-`, `_`, `.`); a trailing `.html` normalizes away. No session
  or auth params.
- **`body_html` is the crawlable body.** If your stored HTML is a full
  document, extract the `<body>` content — the platform wraps the fragment
  in its own document with all the metadata.
- Never map identity-scoped or private data into an item. Everything in an
  item becomes world-readable.

## Step 3 — Wire the lifecycle

Build the registry once (an accessor gated on the config block, so all of
this is a no-op until the operator enables the surface):

```python
def _content_registry(self):
    from kdcube_ai_app.apps.chat.sdk.pub.service import build_registry, resolve_alias_configs

    props = self.bundle_props if isinstance(self.bundle_props, dict) else {}
    config = resolve_alias_configs(props).get("articles")
    if config is None or not config.enabled:
        return None                      # surface off -> every hook no-ops
    tenant, project, bundle_id = self._runtime_scope()
    return build_registry(
        alias="articles", tenant=tenant, project=project, bundle_id=bundle_id,
        hot_root=self.bundle_storage_root(),
    )
```

Mirror every content mutation into it — after your save/delete operations
succeed:

```python
# after a successful save/publish in your admin op:
registry = self._content_registry()
if registry is not None:
    item = _to_public_item(saved_record)
    if item is not None:
        await registry.publish(item)     # page + sitemap entry live immediately

# after a successful delete:
registry = self._content_registry()
if registry is not None:
    await registry.retract(f"{prefix}/{record.slug}")   # URL answers 410 from now on
```

Wrap the hook in try/except with a logged warning — the editorial action
must never fail because the web mirror hiccupped.

And add one **idempotent seed operation** (admin-gated) for first enablement
and bulk changes — this is also what a "Publish to Web" button in your admin
widget calls:

```python
@api(method="POST", alias="articles_publish_to_web", route="operations",
     roles=["kdcube:role:super-admin"])
async def articles_publish_to_web(self, **kwargs):
    registry = self._content_registry()
    if registry is None:
        return {"ok": False, "error": "public content surface is not enabled"}
    items = await self.public_articles()          # the full-sync source from Step 1
    for item in items:
        await registry.publish(item)              # idempotent — safe to re-run
    return {"ok": True, "published": len(items), "slugs": [i.slug for i in items]}
```

## Step 4 — Configure the app

In the app's `bundles.yaml` descriptor:

```yaml
bundles:
  items:
    - id: "my-app@1-0"
      # Content providers should be singletons: every crawler request resolves
      # the app instance for the declaration/config check. A non-singleton app
      # constructs a fresh entrypoint per crawled page — pointless work.
      singleton: true
      config:
        public_content:
          articles:
            enabled: true                              # explicit exposure — off by default
            canonical_base: ""                         # set in Step 6
            sitemap: true
            og_defaults:
              site_name: "My Product"
              image: "https://example.com/og-default.png"
```

Nothing is public until `enabled: true` — you can ship all the code of Steps
1–3 ahead of enablement.

## Step 5 — Reload, seed, verify

```bash
kdcube reload my-app@1-0 -t <tenant> -p <project>
```

Seed once (your admin widget button, or the operation directly as an admin),
then verify with plain `curl` — no browser, no JS:

```bash
B="http://localhost:5173/api/integrations/bundles/<t>/<p>/my-app@1-0/public/__content__"

curl -s "$B" | python3 -m json.tool
# → {"sitemaps": [{"alias": "articles", "sitemap_url": "...", "item_count": N, ...}]}

curl -s "$B/articles/sitemap.xml" | grep -o '<loc>[^<]*</loc>'
# → one <loc> per published item, with <lastmod>

curl -is "$B/articles/guides/getting-started" | head -40
# → HTTP 200, <title>, <meta name="description">, rel=canonical,
#   og:* / twitter:* metas, two <script type="application/ld+json"> blocks, body text

# delete/retract that article, then:
curl -is "$B/articles/guides/getting-started" | head -3
# → HTTP 410
```

Also check your widget URL still serves the widget shell — the crawlable
page is a separate artifact, an iframe widget is never the SEO surface.

## Step 6 — Clean canonical URLs

Raw serving URLs work, but you want canonicals on YOUR domain
(`https://example.com/articles/...`), so link equity consolidates there.
Two pieces:

1. Set `canonical_base: "https://example.com/articles"` in the config block.
   Every page's `rel=canonical`, JSON-LD `url`, and sitemap `<loc>` now use
   it.
2. Map that prefix to the serving route at your edge — a **rewrite +
   forward, never a redirect** (a URL that answers 3xx cannot be a
   canonical). Any reverse proxy can do it; local/dev example (Caddy):

```caddyfile
@content path /articles /articles/*
handle @content {
    rewrite * /api/integrations/bundles/<t>/<p>/my-app@1-0/public/__content__{uri}
    reverse_proxy 127.0.0.1:5173
}
```

For the production split-origin shape (static site on one domain, runtime on
another, CloudFront path behavior + URI-rewrite function) see
[Public Content Solution → Deployment Topology](../../sdk/solutions/cdn-pub/public-content-solution-README.md#deployment-topology-split-origin-host-site--runtime).

## Step 7 — Federate into the site

The site owns its root; you add two references (ship them together with the
Step-6 mapping, or the index points crawlers at a 404):

```text
# robots.txt
Sitemap: https://example.com/articles/sitemap.xml

# sitemap.xml (the site's sitemapindex)
<sitemap><loc>https://example.com/articles/sitemap.xml</loc></sitemap>
```

The descriptor list route from Step 5 (`GET …/public/__content__`) is the
machine-readable source a site build can read to generate these entries.

Done. A crawler now walks: `robots.txt` → site sitemap index → your
runtime-generated sitemap (fresh `lastmod` on every publish) → clean item
URLs, each canonical to itself.

## What not to do

- **Don't** point `canonical_base` at a URL you haven't mapped — every page
  would canonicalize to a 404.
- **Don't** use a redirect for the clean prefix — rewrite + forward only.
- **Don't** derive `lastmod` from "now" in the mapping — reseeds would mark
  everything as changed.
- **Don't** put identity-scoped data in items, and don't try to scope items
  per user — this surface is public-or-nothing by design.
- **Don't** treat the `public_content.changed` Data Bus message as the
  source of truth — it is a notification; the registry is authoritative.
- **Don't** leave the app non-singleton — see Step 4.

## Minimal test

```bash
curl -is "$B/articles/sitemap.xml" | head -3
# → HTTP/1.1 200 OK
#   content-type: application/xml; charset=utf-8
curl -s  "$B/articles/<some-slug>" | grep -c 'application/ld+json'
# → 2      (item document + BreadcrumbList)
```
