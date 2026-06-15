---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/engine-config-README.md
title: "Engine Config & Auth"
summary: "EngineConfig — the explicit, injected, host-agnostic configuration for createChatEngine: connection (baseUrl/tenant/project/bundleId) and auth (cookie mode by default, or host-supplied token callbacks). Replaces the widget's settings singleton; login stays external."
status: design
tags: ["sdk", "npm", "components-core", "config", "auth", "cookie", "token", "EngineRuntime"]
updated_at: 2026-06-16
keywords:
  [
    "EngineConfig",
    "EngineRuntime",
    "cookie auth credentials include",
    "token getAccessToken getIdToken",
    "external login",
    "settings singleton replacement",
  ]
---

# Engine Config & Auth

The engine takes only what it needs to talk to the server. Where those values come
from (route, query, parent frame, a website's own config) is the host's job, done
**before** `createChatEngine(config)`. This is deliberately *not* the widget's
`settings.ts` — that module blended connection config with iframe handshake and
query-param resolution, both host concerns.

## `EngineConfig`

```ts
interface EngineConfig {
  connection: {
    baseUrl: string      // server origin, no trailing slash
    tenant: string
    project: string
    bundleId: string     // the bundle whose operations/streams this engine talks to
  }
  auth?: EngineAuth
  transport?: 'auto' | 'socket' | 'sse'   // default 'auto'
}
```

## Auth — login is external

The components never show a login UI. The host authenticates elsewhere; the engine
only carries credentials and bubbles `unauthorized` when the server rejects a call
(see [host event bus](./host-event-bus-README.md)).

```ts
interface EngineAuth {
  mode?: 'cookie' | 'token'              // default 'cookie'
  getAccessToken?: () => string | null | Promise<string | null>  // token mode
  getIdToken?: () => string | null | Promise<string | null>      // token mode
  idTokenHeader?: string                 // default 'X-ID-Token'
}
```

- **`cookie` (default)** — requests use `credentials: 'include'`; the host's
  externally-set session cookie rides along. Nothing to supply.
- **`token`** — the host provides `getAccessToken` / `getIdToken` callbacks. They are
  re-read per request, so refresh is transparent, and they feed both request headers
  and the SSE query-params / Socket.IO auth payload.

## `EngineRuntime` (internal)

`buildRuntime(config)` derives an `EngineRuntime` from the config — the object the
transport reads instead of a singleton: `baseUrl`, `tenant`, `project`, `bundleId`,
`getTokens()`, `authHeaders()`, `createLocalId()`, `clientTimezone()`. Exposed for
advanced/non-React hosts; most callers only touch `EngineConfig`.
