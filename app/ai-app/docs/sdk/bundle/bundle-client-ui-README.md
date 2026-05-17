---
id: ks:docs/sdk/bundle/bundle-client-ui-README.md
title: "Bundle Client UI"
summary: "Entry page for bundle-facing frontend integration: source layout for main UI vs widgets, browser transport, auth, chat stream lifecycle, multi-tab behavior, and widget or operation interoperability."
tags: ["sdk", "bundle", "frontend", "transport", "auth", "sse", "socketio", "rest", "ui"]
keywords: ["frontend integration entrypoint", "bundle ui contract", "main view ui/main", "widget source folder", "widget and operation interoperability", "browser auth and transport", "chat stream lifecycle guidance", "multi tab coordination", "client side bundle behavior"]
see_also:
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
  - ks:docs/sdk/bundle/bundle-chat-stream-events-README.md
  - ks:docs/sdk/bundle/bundle-frontend-awareness-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/service/auth/auth-README.md
  - ks:docs/service/comm/README-comm.md
  - ks:docs/service/cicd/embedding-control-plane-frontend-README.md
---
# Bundle Client UI

This section is the browser/UI entry point for bundle developers.

Use it when your bundle ships:

- widgets
- a custom main UI
- a custom frontend calling `/api/integrations/*`
- frontend code that must consume chat stream events correctly

## Read Order

1. [bundle-client-communication-README.md](bundle-client-communication-README.md)
   The transport/auth contract: supported headers, cookies, query params, response headers, SSE vs Socket.IO, and how REST requests can target one connected peer.
2. [bundle-widget-integration-README.md](bundle-widget-integration-README.md)
   Widget source-folder layout, iframe config handshake, operation URL shape, and widget/API split.
3. [bundle-chat-stream-events-README.md](bundle-chat-stream-events-README.md)
   The shared SSE + Socket.IO chat stream event catalog and envelope shape.
4. [bundle-frontend-awareness-README.md](bundle-frontend-awareness-README.md)
   Retry, draining, rate-limit, reconnect, and multi-tab behavior.

## Source Layout

KDCube has two browser-facing bundle UI surfaces with the same build paradigm
and separate source subtrees.

Use `ui/main` for the bundle main view:

```yaml
ui:
  main_view:
    src_folder: ui/main
    build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

Use a widget-specific folder for widgets:

```yaml
ui:
  widgets:
    task_memo_webapp:
      enabled: true
      src_folder: ui/widgets/task_memo_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

Do not put widget app source under `ui/main`. That name is reserved by
convention for main-view source. A bundle may have both:

```text
my_bundle/
  ui/
    main/                    # main view
    widgets/
      task_memo_webapp/      # widget app
```

Both source folders are built by bundle-loader infrastructure into shared
bundle storage. Bundle code and operators should edit source, not the built
runtime storage directory.

Widget source-folder config is per alias. A bundle can define
`ui.widgets.task_memo_webapp.src_folder/build_command` and still inherit
method-rendered widgets such as `ai_bundles` from `BaseEntrypoint`; those
inherited widgets keep using their Python-rendered HTML unless their own alias
is also configured as a source-folder widget.

Source-folder widgets also have a public static route for launch surfaces such
as Telegram Mini Apps:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}
```

Use that only to serve the app shell/assets. Public widget APIs still need their
own request authentication, such as Telegram `initData` verification.

For one widget codebase that runs in both KDCube and Telegram:

- detect Telegram with `window.Telegram?.WebApp?.initData`
- in KDCube, first fetch `/api/cp-frontend-config` from the KDCube frame origin
  and call `/operations/{alias}`
- if that endpoint is unavailable, fall back to iframe parent
  `CONFIG_REQUEST` / `CONFIG_RESPONSE`
- in Telegram, skip parent config, call `/public/{telegram_alias}`, and send
  `X-Telegram-Init-Data`
- keep admin-only panels behind KDCube-authenticated operations, not Telegram
  Mini App public APIs

## Scope

Use these docs when you need to know:

- how bundle UIs authenticate over REST, SSE, Socket.IO, and integrations
- which cookies and headers are supported
- which values are configurable by env
- how `stream_id` and `KDC-Stream-ID` affect peer-targeted delivery
- which response headers and retry signals the client should honor

This is no longer a separate top-level client namespace. These docs live under bundle because bundle code now owns widgets, main UI apps, and custom frontend interactions.

## External Embedding

When a bundle UI is opened from another web application, for example:

```text
https://dev.kdcube.tech/api/integrations/static/demo/demo-march/versatile@2026-03-31-13-36
```

the browser decides whether that document may be framed from the response
headers on the KDCube route. Bundle React code cannot override
`X-Frame-Options` or CSP `frame-ancestors`.

Use the deployment `proxy.frame_embedding` descriptor to choose the frame
policy. Cross-origin embedding requires `mode: allowlist`, clears
`X-Frame-Options`, and emits `Content-Security-Policy: frame-ancestors ...` on
the KDCube shell and frameable bundle/static document routes. See
[Embedding The Control Plane Frontend](../../service/cicd/embedding-control-plane-frontend-README.md)
for the deployment contract and examples.

For cross-origin iframe sizing, the embedding page must listen for the KDCube
resize message:

```js
window.addEventListener('message', (event) => {
  if (event.data?.type !== 'kdcube-resize') return;
  if (event.origin !== 'https://dev.kdcube.tech') return;
  const height = Number(event.data.height);
  const width = Number(event.data.width);
  if (Number.isFinite(height) && height > 0) iframe.style.height = `${Math.ceil(height)}px`;
  if (Number.isFinite(width) && width > 0) iframe.style.width = `${Math.ceil(width)}px`;
});
```

KDCube injects this reporter into static bundle UI and widget HTML entrypoints.
Headers such as CSP `frame-ancestors` allow display; they do not let the parent
read cross-origin iframe DOM dimensions. The embedding page should validate
`event.origin` against the exact KDCube origin it framed.

Bundle UIs that need runtime scope must support both config paths:

1. `GET /api/cp-frontend-config`, used by direct external embedding and by
   normal KDCube-hosted frames; if it returns a valid response, do not wait for
   parent messaging
2. parent `CONFIG_RESPONSE` / `CONN_RESPONSE`, used as fallback when the config
   endpoint is unavailable or blocked

If both are unavailable, static routes can still recover tenant/project/bundle
from `/api/integrations/static/{tenant}/{project}/{bundle_id}` or
`/api/integrations/bundles/{tenant}/{project}/{bundle_id}/...`, but that route
fallback does not carry auth token metadata.

## Server-Side References

- auth transport and precedence:
  [auth-README.md](../../service/auth/auth-README.md)
- transport and relay internals:
  [README-comm.md](../../service/comm/README-comm.md)
