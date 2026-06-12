---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-ui-README.md
title: "Bundle Client UI"
summary: "Entry page for bundle-facing frontend integration: source layout for main UI vs widgets, browser transport, auth, chat stream lifecycle, multi-tab behavior, and widget or operation interoperability."
tags: ["sdk", "bundle", "frontend", "transport", "auth", "sse", "socketio", "rest", "ui"]
keywords: ["frontend integration entrypoint", "bundle ui contract", "main view ui/main", "widget source folder", "widget and operation interoperability", "browser auth and transport", "chat stream lifecycle guidance", "multi tab coordination", "client side bundle behavior"]
updated_at: 2026-06-06
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-chat-stream-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-frontend-awareness-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/README-comm.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/bus-routing-and-partitioning-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/embedding-control-plane-frontend-README.md
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

For mounted bundle source or widget config edits in a running local runtime,
the targeted bundle reload path is documented in
[KDCube CLI / Bundle Reload Flow](../../service/cicd/cli-README.md#bundle-reload-flow).

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

For inherited widgets, remember that `ui.widgets.<alias>` does not create or
hide the widget surface. The surface comes from `@ui_widget(alias="<alias>")`.
Use `enabled.widget.<alias>: false` to suppress an inherited widget. Use
`ui.widgets.<alias>.src_folder/build_command` to replace the inherited
method-rendered UI with a built app for the same alias. If code overrides the
decorator, override the same Python method name; do not add a second method with
the same alias.

Source-folder widgets also have a public static route for launch surfaces such
as Telegram Mini Apps:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}/
```

`@ui_widget(alias="<alias>")` has no separate public-serving flag. The
decorator creates the widget surface, `ui.widgets.<alias>` builds the static
app, and the URL family chooses the serving mode. Use `/widgets/...` for the
KDCube-authenticated control plane and `/public/widgets/...` for a Telegram
Mini App menu button or another public launcher.

Use the public widget route only to serve the app shell/assets. Public widget
APIs still need their own request authentication, such as Telegram `initData`
verification or a bundle-issued federated Data Bus token.

The bundle **main view** (`ui/main`) has the same authed/public split. The
authed route requires a logged-in user:

```text
/api/integrations/static/{tenant}/{project}/{bundle_id}
/api/integrations/static/{tenant}/{project}/{bundle_id}/{path}
```

For a public page that must show the main view to **anonymous** visitors (e.g. a
landing site embedding the chat read-only), use the public static route:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/static
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/static/{path}
```

It serves the same built SPA shell with no authentication. The shell is not
sensitive — data is still gated by the bundle's authed operations/SSE, and the
app resolves identity itself via `/profile`. So an anonymous visitor can view
the UI, while actions that need a user (e.g. sending a chat turn) still return
`401/403`; the UI can forward that to its host to trigger login (see the
`kdcube-auth-required` note under
[bundle-widget-integration-README.md](bundle-widget-integration-README.md#frame-view-contract-host-driven-expand)).
The injected `<base href>` is route-aware, so relative assets resolve under
`/public/static/`.

Buildable bundle browser apps must therefore emit relative asset URLs. For Vite
apps under `ui/main` or `ui/widgets/<alias>`, set `base: './'`:

```ts
export default defineConfig({
  base: './',
  build: {
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
})
```

After `npm run build`, inspect `dist/index.html`. It should contain
`./assets/index-....js` and `./assets/index-....css`. Root-relative
`/assets/...` URLs bypass the injected route base and make the browser request
assets from the KDCube domain root instead of the bundle static route.

For one widget codebase that runs in both KDCube and Telegram:

- detect Telegram with `window.Telegram?.WebApp?.initData`
- in KDCube, first fetch `/api/cp-frontend-config` from the KDCube frame origin
  and call `/operations/{alias}`
- if that endpoint is unavailable, fall back to iframe parent
  `CONFIG_REQUEST` / `CONFIG_RESPONSE`
- in Telegram, load the shell from `/public/widgets/{widget_alias}/`, skip
  parent config, call `/public/{telegram_alias}`, and send
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

## Sending External Events to the Active Turn

Bundle main UIs, platform chat clients, and external product clients use the same chat
send contract for followups and steers:

```text
client intent
  -> /sse/chat or Socket.IO chat_message
  -> ingress writes conversation external event
  -> live React owner consumes it, or proc promotes it later as one fallback turn
```

Client-side rules:

- send `message_kind` / `continuation_kind` as `followup` or `steer`
- include `target_turn_id` when the user is aiming at the currently visible turn
- treat the synchronous `followup_accepted` / `steer_accepted` response as admission, not as a new turn
- only render an immediate same-turn followup bubble when `live_owner_detected !== false`
- use `event_id || queued_turn_id || turn_id` as the optimistic bubble dedupe key

For the field-level contract, read
[bundle-client-communication-README.md](bundle-client-communication-README.md).
For stream behavior after admission, read
[bundle-chat-stream-events-README.md](bundle-chat-stream-events-README.md).

## Sending Durable Bundle State Messages

When a bundle UI mutates bundle-owned state, use the Data Bus instead of
conversation `external_events[]`.

```text
widget/main UI action
  -> optional bundle token claim for app-specific/public clients
  -> Socket.IO data_bus.publish on namespace "/"
  -> ingress normalizes and enqueues to the bundle Data Bus stream
  -> proc loads @data_bus_handler(...)
  -> durable storage revision/object update
  -> optional chat_service reply for this connected peer/session
```

Use this pattern for collaborative board patches, issue edits, annotations, or
other domain mutations that must be processed even when no chat turn is open.
The `data_bus.publish` ack is only stream admission; the UI should wait for the
handler reply or refetch durable state when it needs the final result.

Platform-authenticated UIs use the normal runtime session and token material.
App-specific clients, such as a public widget opened outside the platform
session, call a bundle endpoint first; the bundle validates that upstream
context and issues a short-lived federated Data Bus token.

If the same action should also wake or inform an agent, bridge it explicitly
into conversation `external_events[]` after the bundle decides that is needed.

Read how the buses fit together in
[Conversation Event Bus And Data Bus](../../service/comm/conversation-event-bus-and-data-bus-README.md)
and the routing/partitioning map in
[Bus Routing And Partitioning](../../service/comm/bus-routing-and-partitioning-README.md)
and the field contract in
[bundle-client-communication-README.md#data-bus-contract](bundle-client-communication-README.md#data-bus-contract).

## External Embedding

When a bundle UI is opened from another web application, for example:

```text
https://dev.kdcube.tech/api/integrations/static/demo/demo-march/versatile@2026-03-31-13-36
```

the browser decides whether that document may be framed from the response
headers on the KDCube route. Bundle React code cannot override
`X-Frame-Options` or CSP `frame-ancestors`.

That example uses the authed static route (logged-in users only). For an
anonymous-visible embed (a public landing page), point the iframe at the public
static route instead —
`/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/static` — so
visitors who are not signed in can still load the shell (see "Source Layout").

Use the deployment `proxy.frame_embedding` descriptor to choose the frame
policy. Cross-origin embedding requires `mode: allowlist`, clears
`X-Frame-Options`, and emits `Content-Security-Policy: frame-ancestors ...` on
the KDCube shell and frameable bundle/static document routes. See
[Embedding The Control Plane Frontend](../../service/cicd/embedding-control-plane-frontend-README.md)
for the deployment contract and examples.

For how login and cookies behave when embedded — a same-site subdomain embed can
reuse a top-level login via a shared parent-domain cookie, while a cross-site
embed needs `SameSite=None; Secure` cookies or the parent `CONFIG_RESPONSE`
token handoff — see that doc's **Auth And Cookies** section.

For cross-origin iframe sizing, the embedding page must listen for the KDCube
resize message. The host page owns the normal iframe width with CSS; do not set
`iframe.style.width` from every resize event. Doing that can feed a temporary
narrow measurement back into the child frame, make the bundle UI reflow as a
mobile-width page, and leave the final height much larger than expected.

Start with a stable iframe box:

```html
<iframe
  id="bundle-frame"
  src="https://dev.kdcube.tech/api/integrations/static/demo/demo-march/versatile@2026-03-31-13-36"
  style="display:block;width:100%;border:0"
></iframe>
```

Then update height from the cooperative message. `width` is only an optional
overflow/min-width signal; ignore it for ordinary responsive embedding.

```js
window.addEventListener('message', (event) => {
  if (event.data?.type !== 'kdcube-resize') return;
  if (event.origin !== 'https://dev.kdcube.tech') return;
  const height = Number(event.data.height);
  const width = Number(event.data.width);
  if (Number.isFinite(height) && height > 0) iframe.style.height = `${Math.ceil(height)}px`;
  if (Number.isFinite(width) && width > iframe.clientWidth) {
    iframe.style.minWidth = `${Math.ceil(width)}px`;
  }
});
```

KDCube injects this reporter into static bundle UI and widget HTML entrypoints.
Headers such as CSP `frame-ancestors` allow display; they do not let the parent
read cross-origin iframe DOM dimensions. The embedding page should validate
`event.origin` against the exact KDCube origin it framed.

The message can include diagnostics:

- `height`: content height the parent should apply to the iframe
- `width`: non-zero only when content needs more horizontal space than the
  current iframe viewport
- `contentWidth`: measured content width
- `viewportWidth`: current iframe viewport width seen by the embedded document
- `seq`: monotonic sequence number from the reporter instance
- `reason`: trigger that produced the measurement, such as `load`,
  `window.resize`, `resize-observer`, or `mutation-observer`

To diagnose sizing issues, append `?kdcube_resize_debug=1` to the framed KDCube
URL or run this in the embedded page DevTools console and reload:

```js
localStorage.setItem('kdcube.resize.debug', '1')
```

The injected reporter then writes `[kdcube-resize]` console entries for posted
measurements and skipped measurements, including the observed viewport width.
Turn it off with:

```js
localStorage.removeItem('kdcube.resize.debug')
```

To let a widget expand to a fullscreen/overlay view, the host (not the iframe)
owns the overlay; the widget signals intent via `kdcube-widget-view` and stays
in sync via `kdcube-set-view`. See
[Frame View Contract](bundle-widget-integration-README.md#frame-view-contract-host-driven-expand).

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
