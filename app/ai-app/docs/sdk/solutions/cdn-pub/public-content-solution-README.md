---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/cdn-pub/public-content-solution-README.md
title: "Public Content Solution (cdn-pub)"
summary: "The sdk/pub solution: how the platform turns app-declared content into discoverable web artifacts — the item model, the tiered registry (durable store + hot serving tier, generation marker, guarded build/mutation moments), the reserved serving route, sitemap/robots ownership, split-origin CDN deployment, and gateway/rate-limit behavior."
tags: ["sdk", "solutions", "cdn-pub", "public-content", "seo", "sitemap", "jsonld", "registry", "storage", "cdn"]
keywords: ["public content solution", "sdk pub", "content registry", "generation marker", "hot index", "crawlable html", "json-ld", "sitemap", "410 gone", "split origin", "cdn rewrite", "canonical base"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/public-content-provider-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/resource_sharing/publish-discoverable-content-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-storage-and-cache-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/synch-mechanisms/critical-section-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/maintenance/gateway-control-README.md
---
# Public Content Solution (cdn-pub)

`kdcube_ai_app/apps/chat/sdk/pub` is the platform solution that turns
app-declared content into **discoverable web artifacts**: crawlable HTML item
pages (real `<title>`/meta/body, no JavaScript), `rel=canonical` + Open
Graph + Twitter metadata, JSON-LD (declared `@type` + `BreadcrumbList`),
a per-alias `sitemap.xml` with accurate `lastmod`, `410 Gone` after
retraction, and a machine-readable sitemap descriptor list for host-site
federation.

Division of labor:

- **The app owns write time** — which items are public, their metadata, and
  the publish/update/retract decisions. How an app declares and drives the
  surface is the app-side contract:
  [Public Content Provider](../../bundle/public-content-provider-README.md).
- **This solution owns read time** — rendering and serving every
  discoverability artifact from the registry. No app-defined logic executes
  on the serving path (the route plumbing resolves the app and checks its
  declaration + config gate; it does not invoke app operations).
- **The site domain owns its root** — `robots.txt` and the top-level sitemap
  index stay host-owned; the solution provides what the host references.

## Module Map

```text
sdk/pub/
  model.py       PublicContentItem, alias config, hot index models
  registry.py    tiered registry: durable records + hot serving tier
  render.py      item -> crawlable page (canonical/OG/Twitter + JSON-LD)
  sitemap.py     alias index -> sitemap.xml + host-federation descriptor
  service.py     config resolution, registry construction, load-time ensure,
                 the serving dispatcher, Data Bus change notifier
```

## The Item Model

`PublicContentItem`: `alias` + `slug` (clean permalink path, unique within
the alias — lowercase slug segments, a trailing `.html` normalizes away),
`title` / `summary` / `body_html` (the crawlable body; `headline_in_body`
marks an authored body that renders its own headline card, so the page
renderer skips its generated `<h1>`/summary — metadata and JSON-LD are
unaffected), `schema_type`
(JSON-LD `@type`) with `jsonld_extra` overrides, images/author/section/tags/
language, `published_at` / `lastmod`, and the publication `state`:
`published` or `retracted`.

Visibility vocabulary is deliberately narrow: **explicit public exposure +
publication state + tenant/project/app scoping**. There are no per-user
audience selectors on this surface.

## The Tiered Registry

Two storage tiers — no Postgres, no Redis:

```text
DURABLE (source of truth) — BundleArtifactStorage (local FS in dev, S3 in cloud):
  public_content/<alias>/items/<slug>.json     full item records
  public_content/<alias>/generation.json       monotonic mutation counter

HOT (what serving reads) — app storage root (local disk in dev, EFS in cloud):
  _public_content/<alias>/index.json           the per-alias index (sitemap source)
  _public_content/<alias>/items/<slug>.json    mirrored records (item pages)
  _public_content/<alias>/.index.signature     generation stamp
```

- **Crawler traffic reads only the hot tier**; the durable backend is never
  on the request path.
- The hot tier is **derived and rebuildable**: wipe it and the next app load
  rebuilds it from durable records.
- Consistency never compares timestamps — it compares the **generation
  counter** (no clock-skew sensitivity). `published_at`/`lastmod` are
  crawler-facing metadata carried through to the sitemap and JSON-LD.
- Retracted items keep their record so the URL answers **410** (with a
  `noindex` body) rather than 404 — the strongest "permanently removed"
  signal a crawler can get.

### Two concurrency moments

Mirrors the platform's prepared-data pattern
([Synchronization Mechanisms](../../../service/synch-mechanisms/critical-section-README.md)):

- **Moment A — load-time bootstrap/rebuild** (`ensure_hot_index()`): many
  workers across many instances race on app load. Guarded by
  `run_once_for_shared_bundle_storage`: lock-free signature fast path when
  the hot generation matches the durable generation; otherwise ONE
  fleet-wide owner rebuilds while waiters observe and skip. This is the
  durable→hot materialization — automatic, signature-guarded, operator-free.
  `get_item` additionally refills single items lazily on a hot-tier miss.
- **Moment B — runtime mutation** (`publish`/`update`/`retract`): serialized
  by an observed file lock on the shared hot tier, holding durable write →
  generation bump → hot update → signature in one critical section. Readers
  never take the lock; hot files are replaced atomically, so torn reads are
  impossible. **Bulk seeds must use `publish_many(items)`** — one lock
  acquisition and one generation bump for the whole batch; publishing N items
  one-by-one thrashes the shared lock and the durable generation RMW, and on
  EFS/S3 can starve concurrent publishers past their wait budget.

A publish landing during a rebuild is safe by generation re-check — worst
case one redundant rebuild, never a lost publish. All registry I/O runs off
the event loop (a blocked loop starves the once-lock heartbeat and manifests
as a duplicate builder).

### Multi-environment sharing

Two environments may share one durable store (same tenant/project/app
scope): **reads** propagate pull-on-load (a running env serves its own hot
copy until its next load/reload — serving never checks the durable backend
per request); **writes must come from one env** (the mutation lock lives on
each env's own shared filesystem, so cross-env writers are not serialized —
a recorded follow-up, not a current capability).

## Serving: The Reserved Route

Everything serves under the app's existing public namespace on the reserved
`__content__` segment (platform-owned; never dispatched to app operations):

```text
GET …/bundles/{tenant}/{project}/{bundle_id}/public/__content__
      → JSON descriptor list of enabled alias sitemaps (host federation)
GET …/public/__content__/{alias}/sitemap.xml
      → the per-alias sitemap (published items only, accurate lastmod)
GET …/public/__content__/{alias}/{slug…}
      → the crawlable item page (200), 410 when retracted, 404 unknown
```

The handler reads the hot tier and renders with solution code. The route
plumbing resolves the app instance to consult its manifest (is the alias
declared?) and its props (is it enabled? what is the alias config?) — **a
content-provider app should therefore be a singleton** (`singleton: true`),
so crawler traffic reuses one instance instead of constructing a fresh
entrypoint per request.

The canonical URL is decoupled from the serving route: the alias config's
`canonical_base` (an operator-mapped clean prefix) drives `rel=canonical`,
JSON-LD `url`, and sitemap `<loc>`; when empty, serving-route URLs are used
so a local deployment still emits valid, testable artifacts.

### robots.txt and the top-level sitemap index

Host-level artifacts stay **host/deployment-owned**: `robots.txt` and the
site's sitemap **index** belong to whoever owns the domain root. The
solution provides what the host references — per-alias sitemaps plus the
descriptor list route, so a host generates its index entries without
scraping.

## Deployment Topology: Split Origin (host site + runtime)

The common production shape is a static host site on one domain and the
KDCube runtime on another. Public content is **anonymous** — no cookies, no
session — so unlike embedded widgets (which need same-origin auth cookies),
crawlable pages can be fronted across origins by pure CDN plumbing.

```text
crawler / shared link
   │
   ▼
site CDN (https://site.example)
   ├─ static site               → /, /docs/*, sitemap.xml, robots.txt
   └─ ordered behavior /news/*  → custom origin = the runtime host
        + viewer-request function rewriting the URI:
          /news/<rest> → /api/integrations/bundles/<t>/<p>/<app>/public/__content__/news/<rest>
              │
              ▼
        runtime CDN → proxy (public location) → proc → __content__ handler → hot tier
```

The three configuration pieces, each in its owner's home:

| Piece | Owner | Value |
| --- | --- | --- |
| `canonical_base` | runtime descriptor (`public_content.<alias>` block) | `https://site.example/news` — item pages, JSON-LD `url`, and sitemap `<loc>` all use it |
| path behavior + URI-rewrite function | the **site's** CDN distribution | maps the clean prefix to the `__content__` route (CDN routes by path but does not rewrite it — a small viewer-request function does) |
| federation | the website build | `Sitemap: https://site.example/news/sitemap.xml` in `robots.txt`; one `<sitemap>` entry in the site's `sitemap.xml` index |

Result for a crawler: `robots.txt` → site sitemap index → the app's
runtime-generated sitemap (accurate `lastmod`) → clean item URLs, each
canonical to itself — all on the site's domain; the runtime host never
appears in the index.

Ordering note: ship the CDN behavior **before** (or together with) the
`robots.txt`/index federation lines — otherwise the index points crawlers at
a 404. Caching: item pages are anonymous and cacheable behind the behavior;
keep the sitemap path at low/no TTL so `lastmod` freshness is not masked
(CDN invalidation on publish is a designed pipeline hook, not built yet).

The raw serving URLs on the runtime host work with **no infra changes** (the
public proxy locations and gateway patterns already pass them) — use them
for staging verification before wiring the site-domain mapping. Local
development composes the same shape on one origin with any reverse proxy
performing the same rewrite.

## Gateway, Rate Limits, And Proxies

No special-casing is required — the reserved route rides the existing public
sub-path admission — but the operational consequences are worth knowing:

- **Admission**: the gateway guarded pattern `…/public/[^/]+(?:/.*)?` admits
  `__content__/…` as an anonymous public route; the OpenResty templates
  match only up to `/public/`, so multi-segment slugs pass every proxy
  shape.
- **Rate limits**: anonymous requests are limited per session (IP + user
  agent fingerprint) with the configured `rate_limits.proc.anonymous`
  budget. A crawler is one anonymous session per IP+UA; on 429 it backs
  off. Large content sets under aggressive crawling may warrant raising the
  anonymous budget or adding the sitemap route to
  `bypass_throttling_patterns.proc` in `gateway.yaml`.
- **Backpressure**: anonymous traffic sheds first
  (`anonymous_pressure_threshold`) — under load, crawler requests are
  deprioritized before user traffic.
- The Data Bus change notifier publishes server-side from proc, not through
  the Socket.IO ingress path, so `data_bus.publish` streaming limits do not
  apply to it.

## Change Notification (and its limits)

Every successful mutation can emit a `public_content.changed` Data Bus
message. It is a **notification hook only**: the durable registry and its
generation marker stay authoritative, and consumers (future
submission/syndication workers — IndexNow, feeds, CDN invalidation) must
tolerate missed messages by resyncing from durable records.

## Deferred (designed, not built)

The submission/syndication pipeline attaches to the notification and the
durable registry: IndexNow POST, RSS/Atom + WebSub, Search Console sitemap
registration, CDN invalidation on publish. Operator-owned credentials
(IndexNow key, Search Console property, analytics ids) live in platform
descriptors/secrets; when they become reusable provider credentials they
follow Connection Hub integration ownership. Also recorded: a dedicated
indexed table for very large content sets, and cross-environment write
coordination.

## References (code)

- Solution: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/pub/`
- Declaration: `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_loader.py` (`@public_content`)
- Serving dispatch: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py` (`PUBLIC_CONTENT_ROUTE_SEGMENT`)
- Load-time ensure: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py` (`_ensure_public_content_indexes`)
- Tests: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/pub/tests/`
