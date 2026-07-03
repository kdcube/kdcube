---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/resource_sharing/share-static-resources-README.md
title: "Share Static Resources From An App"
summary: "Recipe for exposing an app's hosted content — a rendered page, a stored file, or a whole built UI — at a public, anonymously openable URL, using a public op that returns bytes/file/stream, clean sub-path URLs via the path-tail route, or platform-served widget static. Worked example: serving a news article as a shareable static webpage."
status: active
tags: ["recipes", "resource-sharing", "static", "public", "bundle", "widget", "storage", "s3", "html", "path-tail"]
updated_at: 2026-06-29
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/ui-components-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/gateway-descriptor-README.md
---
# Share Static Resources From An App

An app holds content in its own hosting — pages it renders, files it writes to
storage (local FS or S3), and the UI it builds. **Sharing** that content means
giving it a public URL that anyone can open in a browser, with no sign-in.

Every public URL an app owns lives under its **public prefix**:

```
{base}/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/…
```

Pick the shape by what you are sharing:

| You want to share | Use | URL under the public prefix |
| --- | --- | --- |
| A page/file the app **renders on demand** (HTML, PDF, CSV, image) | a public op that returns `BundleBinaryResponse` / `BundleFileResponse` / `BundleStreamResponse` | `…/public/{alias}` |
| The same, but with a **clean, meaningful path** | the same op, declaring a `path_tail` kwarg | `…/public/{alias}/{rest}` |
| A **stored file** (an artifact in FS/S3) | a public op that reads storage and returns its bytes | `…/public/{alias}` or `…/public/{alias}/{rest}` |
| A **whole built UI** (a widget or main-view SPA `dist`) | declare it with `@ui_widget` / `@ui_main`; the platform serves the assets | `…/public/widgets/{alias}/…`, `…/public/static/…` |

The first three are this recipe. The last one is the platform's UI pipeline —
covered by [ui-components-lifecycle](../../sdk/bundle/ui-components-lifecycle-README.md)
and [bundle-widget-integration](../../sdk/bundle/bundle-widget-integration-README.md);
this recipe only points at it.

Sharing gives content a URL. If you also want it **discoverable** — crawlable
pages with JSON-LD and metadata, a `sitemap.xml`, 410 after removal — that is
the public-content surface: see
[Publish Discoverable Content](publish-discoverable-content-README.md).

## Plain shape

```
browser  ──GET /…/{bundle_id}/public/articles/kdcube/journal/my-id.html──▶  gateway
gateway  ──(classified public: throttle/backpressure, NO sign-in)──────────▶  app op
op       ──renders or reads the resource──────────────────────────────────▶  BundleBinaryResponse(text/html)
op       ◀──────────────── HTTP 200, Content-Type: text/html ──────────────  browser
```

A public op runs as an **anonymous** session — there is no user to authenticate.
The op is the access-control point: it decides what is shareable and returns
only that.

## Option A — a public op that returns the resource

Declare a public op (`route="public"`) and return one of the response carriers
from `kdcube_ai_app.apps.chat.sdk.runtime.http_ops`. The integrations router
coerces them into the real HTTP response:

- `BundleBinaryResponse(content: bytes, media_type, filename?, headers?, status_code?)` → an in-memory body (rendered HTML, a generated image).
- `BundleFileResponse(path: str, media_type?, filename?, …)` → a file already on disk.
- `BundleStreamResponse(chunks: AsyncIterable[bytes], media_type?, …)` → a streamed body for large/long content.

```python
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import BundleBinaryResponse
from kdcube_ai_app.infra.plugin.bundle_loader import api

@api(method="GET", alias="welcome", route="public")
async def public_welcome(self, **kwargs):
    html = "<!doctype html><meta charset=utf-8><title>Hi</title><body>…</body>"
    return BundleBinaryResponse(
        content=html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )
```

Served at `…/public/welcome`. This route takes **query params only** — good for a
single page or a `?id=…`-style lookup.

## Option B — clean sub-path URLs (the path-tail route)

When you want a URL that reads like a path — `…/articles/kdcube/journal/my-id.html`
— give the op a `path_tail` parameter. The platform routes any
`…/public/{alias}/{rest}` to the op and hands it `rest` as `path_tail`:

```python
@api(method="GET", alias="articles", route="public")
async def public_articles(self, path_tail: str = "", **kwargs):
    # path_tail == "kdcube/journal/my-id.html"  (no leading slash)
    parts = [p for p in str(path_tail).strip("/").split("/") if p]
    …
    return BundleBinaryResponse(content=page.encode("utf-8"),
                                media_type="text/html; charset=utf-8")
```

The catch-all path-tail route is registered **after** the concrete public
sub-surfaces (`static`, `widgets`, `mcp`), so those keep first-match precedence
and your op only sees what they did not claim. The gateway already classifies
`…/public/…` (with or without a sub-path) as a public, sign-in-free route — see
[gateway-descriptor](../../configuration/gateway-descriptor-README.md) if you run
a custom descriptor.

## Worked example — a news article as a shareable webpage

The **AI Industry News** app turns each published article into a standalone page
anyone can open. It uses Option B end-to-end.

The op maps a URL prefix to a content channel, then renders the page
(`applications/.../news@.../entrypoint.py`):

```python
ARTICLE_PREFIX_TO_CHANNEL = {
    "kdcube/journal": NewsChannel.JOURNAL,
    "kdcube/blogs":   NewsChannel.BLOGS,
    "industry/ai":    NewsChannel.NEWS,
}

@api(method="GET", alias="articles", route="public")
async def public_articles(self, request=None, path_tail: str = "", **kwargs):
    parts = [p for p in str(path_tail or "").strip("/").split("/") if p]
    if len(parts) < 2:
        return _article_not_found_response()       # a 404 HTML page
    name = parts[-1][:-5] if parts[-1].lower().endswith(".html") else parts[-1]
    channel = ARTICLE_PREFIX_TO_CHANNEL.get("/".join(parts[:-1]).lower())
    if not channel or not name:
        return _article_not_found_response()
    page = await asyncio.to_thread(self._service_render, channel, name)
    if not page:
        return _article_not_found_response()
    return BundleBinaryResponse(
        content=page.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )
```

The service builds the page (`services/news/service.py`,
`render_article_page`): an authored article that carries its own `<style>` is
returned **verbatim** so it looks exactly as published; a generated fragment is
wrapped with the shared article CSS.

That gives stable, shareable URLs:

```
…/news@2026-05-20-12-05/public/articles/kdcube/journal/<id>.html
…/news@2026-05-20-12-05/public/articles/kdcube/blogs/<id>.html
…/news@2026-05-20-12-05/public/articles/industry/ai/<id>.html
```

The reading widgets build that URL from the article's channel + id and offer
**Copy link** and **Open** next to Copy / Download, so a reader can hand the page
to anyone.

## Sharing a stored file (FS / S3)

When the resource already lives in the app's storage, read it inside the op and
return its bytes. The op is still the gate — access is granted by the op
returning the file, not by exposing the storage:

```python
@api(method="GET", alias="report", route="public")
async def public_report(self, path_tail: str = "", **kwargs):
    key = f"reports/{str(path_tail).strip('/')}"
    storage = self._artifact_storage()            # BundleArtifactStorage (FS or S3)
    if not storage.exists(key):
        return BundleBinaryResponse(content=b"Not found",
                                    media_type="text/plain", status_code=404)
    return BundleBinaryResponse(
        content=storage.read(key),
        media_type="text/csv",
        filename=key.rsplit("/", 1)[-1],
    )
```

For a file already on local disk, return `BundleFileResponse(path=…)`; for a
large download, stream with `BundleStreamResponse(chunks=storage.iter_bytes(key))`.
Access goes through the op every time — the platform does not hand out direct
storage URLs, so the op stays the single place that decides what is public.

## When the whole thing is a built UI

If you are sharing an entire front-end (a React/Vue `dist`), you do **not** write
a serving op — declare it as a widget (`@ui_widget`) or main view (`@ui_main`)
and the platform builds and serves the assets at `…/public/widgets/{alias}/…`
and `…/public/static/…`. The build/serve/reload lifecycle is owned by
[ui-components-lifecycle](../../sdk/bundle/ui-components-lifecycle-README.md);
the widget contract (config handshake, auth propagation) is in
[bundle-widget-integration](../../sdk/bundle/bundle-widget-integration-README.md).

## Auth and caching

- `route="public"` runs the op with an **anonymous** session — return only what
  is meant to be public.
- The op owns any validation it needs (for example a signature on an inbound
  hook); a plain shareable page needs none.
- Set `Cache-Control` on the response so browsers and any CDN can cache the page.
- `…/public/…` routes are still gated for throttling/backpressure at the gateway
  — public means "no sign-in", not "unmetered".

## What not to do

- Do not expect a top-level `/articles/…` URL outside the app's public prefix —
  every public URL lives under `…/{bundle_id}/public/…`. Encode the structure in
  the `path_tail` instead (as the news app does).
- Do not return content an anonymous visitor should not see; the session is
  anonymous by design.
- Do not put secrets, tokens, or private storage keys in a public op's output or
  query contract.
- Do not try to bypass the op with a direct storage/S3 URL — the op is the
  access-control point.

## Minimal test

```bash
curl -i "$BASE/api/integrations/bundles/$TENANT/$PROJECT/$BUNDLE/public/articles/kdcube/journal/<id>.html"
# → HTTP/1.1 200 OK
#   content-type: text/html; charset=utf-8
#   cache-control: public, max-age=300
```

A `404` with an HTML body means the op ran but the resource was not found; a
`405`/route error means the sub-path did not reach your op — check that the op
declares a `path_tail` kwarg and that the alias is not a reserved sub-surface
(`static` / `widgets` / `mcp`).
