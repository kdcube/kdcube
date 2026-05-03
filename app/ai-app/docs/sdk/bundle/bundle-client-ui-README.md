---
id: ks:docs/sdk/bundle/bundle-client-ui-README.md
title: "Bundle Client UI"
summary: "Entry page for bundle-facing frontend integration: source layout for main UI vs widgets, browser transport, auth, chat stream lifecycle, multi-tab behavior, and widget or operation interoperability."
tags: ["sdk", "bundle", "frontend", "transport", "auth", "sse", "socketio", "rest", "ui"]
keywords: ["frontend integration entrypoint", "bundle ui contract", "main view ui-src", "widget source folder", "widget and operation interoperability", "browser auth and transport", "chat stream lifecycle guidance", "multi tab coordination", "client side bundle behavior"]
see_also:
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
  - ks:docs/sdk/bundle/bundle-chat-stream-events-README.md
  - ks:docs/sdk/bundle/bundle-frontend-awareness-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/service/auth/auth-README.md
  - ks:docs/service/comm/README-comm.md
---
# Bundle Client UI

This section is the browser/UI entry point for bundle developers.

Use it when your bundle ships:

- widgets
- a custom iframe main view
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
but different source conventions.

Use `ui-src` for the bundle main view:

```yaml
ui:
  main_view:
    src_folder: ui-src
    build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

Use a widget-specific folder for widgets:

```yaml
ui:
  web_app_widgets:
    task_memo_webapp:
      enabled: true
      src_folder: widgets/task_memo_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

Do not put widget app source under `ui-src`. That name is reserved by
convention for main-view source. A bundle may have both:

```text
my_bundle/
  ui-src/                    # main view
  widgets/
    task_memo_webapp/        # widget app
```

Both source folders are built by bundle-loader infrastructure into shared
bundle storage. Bundle code and operators should edit source, not the built
runtime storage directory.

Widget source-folder config is per alias. A bundle can define
`ui.web_app_widgets.task_memo_webapp.src_folder/build_command` and still inherit
legacy widgets such as `ai_bundles` from `BaseEntrypoint`; those inherited
widgets keep using their method-rendered HTML unless their own alias is also
configured as a source-folder widget.

Source-folder widgets also have a public static route for launch surfaces such
as Telegram Mini Apps:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}
```

Use that only to serve the app shell/assets. Public widget APIs still need their
own request authentication, such as Telegram `initData` verification.

For one widget codebase that runs in both KDCube and Telegram:

- detect Telegram with `window.Telegram?.WebApp?.initData`
- in KDCube, wait for iframe parent config and call `/operations/{alias}`
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

This is no longer a separate top-level client namespace. These docs live under bundle because bundle code now owns widgets, iframe UIs, and custom frontend interactions.

## Server-Side References

- auth transport and precedence:
  [auth-README.md](../../service/auth/auth-README.md)
- transport and relay internals:
  [README-comm.md](../../service/comm/README-comm.md)
