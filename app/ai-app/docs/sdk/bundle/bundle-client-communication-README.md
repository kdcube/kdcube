---
id: ks:docs/sdk/bundle/bundle-client-communication-README.md
title: "Bundle Client Communication"
summary: "Browser/UI transport contract for bundles across REST, SSE, Socket.IO, and integrations, including supported headers, cookies, query params, response headers, and peer targeting."
tags: ["sdk", "bundle", "transport", "auth", "headers", "cookies", "sse", "socketio", "rest", "integrations"]
keywords: ["Authorization", "X-ID-Token", "KDC-Stream-ID", "User-Session-ID", "__Secure-LATC", "__Secure-LITC", "stream_id", "peer targeting", "bundle widget"]
see_also:
  - ks:docs/sdk/bundle/bundle-transports-README.md
  - ks:docs/sdk/bundle/bundle-client-ui-README.md
  - ks:docs/sdk/bundle/bundle-sse-events-README.md
  - ks:docs/sdk/bundle/bundle-frontend-awareness-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/service/auth/auth-README.md
  - ks:docs/service/comm/README-comm.md
---
# Bundle Client Communication

This document is the browser/UI contract for talking to the platform over:

- REST
- SSE
- Socket.IO
- proc integrations (`/api/integrations/*`)

It focuses on what a bundle-facing client can send, what the server accepts, and what the client should expect back.

This page is intentionally browser/UI-oriented.

It does not define bundle-served MCP routes or the full bundle transport map.
Use:

- [bundle-transports-README.md](bundle-transports-README.md) for the overall inbound/outbound surface map
- [bundle-platform-integration-README.md](bundle-platform-integration-README.md) for the exact `@mcp(...)` contract

## 1. Transport Overview

| Transport | Typical use | Notes |
| --- | --- | --- |
| REST | non-streaming APIs | Standard headers/cookies auth. |
| SSE | one-way server-to-client event stream plus `POST /sse/chat` send path | `stream_id` is required on the stream. |
| Socket.IO | bidirectional chat and event delivery | Socket `sid` acts as the peer stream id. |
| Proc integrations | bundle widgets, bundle REST operations, custom frontend ↔ bundle APIs | Supports the same auth context plus peer targeting through a header. |

## 2. Supported Request Headers

These headers are accepted as part of the public client contract.

| Header | Purpose | Configurable |
| --- | --- | --- |
| `Authorization: Bearer <access_token>` | Access token for REST/SSE/integrations auth | No |
| `X-ID-Token` | ID token header | Yes, via `ID_TOKEN_HEADER_NAME` |
| `KDC-Stream-ID` | Connected peer identifier carried on REST requests when the client wants server-side events targeted back to that exact peer | Yes, via `STREAM_ID_HEADER_NAME` |
| `X-User-Timezone` | User timezone for server-formatted messages and context | Yes, via `USER_TIMEZONE_HEADER_NAME` |
| `X-User-UTC-Offset` | User UTC offset in minutes | Yes, via `USER_UTC_OFFSET_MIN_HEADER_NAME` |
| `User-Session-ID` | Reuse/verify an existing authenticated user session | No |

Current default values for configurable headers:

| Env var | Default header name |
| --- | --- |
| `ID_TOKEN_HEADER_NAME` | `X-ID-Token` |
| `STREAM_ID_HEADER_NAME` | `KDC-Stream-ID` |
| `USER_TIMEZONE_HEADER_NAME` | `X-User-Timezone` |
| `USER_UTC_OFFSET_MIN_HEADER_NAME` | `X-User-UTC-Offset` |

## 3. Supported Cookies

| Cookie | Purpose | Configurable |
| --- | --- | --- |
| `__Secure-LATC` | Access token cookie fallback | Yes, via `AUTH_TOKEN_COOKIE_NAME` |
| `__Secure-LITC` | ID token cookie fallback | Yes, via `ID_TOKEN_COOKIE_NAME` |

Current default values:

| Env var | Default cookie name |
| --- | --- |
| `AUTH_TOKEN_COOKIE_NAME` | `__Secure-LATC` |
| `ID_TOKEN_COOKIE_NAME` | `__Secure-LITC` |

## 4. Auth Resolution Order

The server resolves auth in this order:

1. explicit transport payload
   REST/integrations/SSE-send headers, or Socket.IO auth payload
2. SSE query params
3. cookies

Practical meaning:

- if you send `Authorization` and `X-ID-Token`, those win
- if SSE cannot set headers, `bearer_token` and `id_token` query params are accepted
- cookies are fallback transport, not the preferred explicit contract

## 5. SSE Contract

### Open stream

`GET /sse/stream`

Supported query params:

| Query param | Required | Purpose |
| --- | --- | --- |
| `stream_id` | Yes | Client-provided identifier for the connected peer |
| `user_session_id` | No | Reuse an existing session owned by the authenticated user |
| `bearer_token` | No | Access token fallback when headers are unavailable |
| `id_token` | No | ID token fallback when headers are unavailable |
| `tenant` | No | Override tenant for the stream |
| `project` | No | Override project for the stream |

`stream_id` is the peer identifier later used for direct-delivery semantics.

### Send chat request

`POST /sse/chat`

The stream-side client should keep using the same session and peer identity it established on `/sse/stream`.

### Conversation status

`POST /sse/conv_status.get`

Clients should use the same stream/session pairing they established for chat.

## 6. Socket.IO Contract

### Connect auth payload

The Socket.IO `connect` auth payload may include:

| Auth field | Purpose |
| --- | --- |
| `user_session_id` | Existing session to attach to |
| `bearer_token` | Access token |
| `id_token` | ID token |
| `tenant` | Tenant override |
| `project` | Project override |

The Socket.IO connection `sid` is the peer stream identifier for direct delivery.

## 7. Integrations and Bundle REST Calls

This is the relevant contract for:

- bundle widgets
- custom bundle frontends
- any client calling `/api/integrations/*`

### Auth on integrations REST

The integrations layer accepts the same auth/timezone context as normal HTTP requests:

- `Authorization`
- configured ID token header
- configured timezone header
- configured UTC offset header
- cookies as fallback

For browser/widget cases where setting headers is inconvenient, the middleware also accepts these query params on `/api/integrations/*` and injects them into headers before gateway processing:

| Query param | Injected header |
| --- | --- |
| `bearer_token` | `Authorization: Bearer ...` |
| `id_token` | configured ID token header |
| `user_timezone` | configured timezone header |
| `user_utc_offset_min` | configured UTC offset header |

### Peer-targeted communicator delivery from REST

If the client wants a REST-triggered bundle operation to emit events back to one exact already-connected peer, it must send the configured stream-id header:

```http
KDC-Stream-ID: <connected-peer-stream-id>
```

Here, `KDC-Stream-ID` means the request header whose default name is `KDC-Stream-ID` and whose value is the identifier of the already-connected SSE or Socket.IO peer.

Behavior:

- header present:
  server maps it into communicator target peer id, so bundle-side emits can target that one connected client
- header absent:
  communicator emits remain session-scoped broadcast

Session-scoped broadcast means:

- all connected peers on that session receive the event
- if no peer is listening for that session, nobody receives it

## 8. Response Headers Clients Should Use

| Header | Meaning |
| --- | --- |
| `X-User-Type` | Resolved user type for the request |
| `X-Session-ID` | Server session id for the request |
| `Retry-After` | Retry hint on `429` and some `503` responses |

Clients should:

- keep `X-Session-ID` stable when they intend to reuse the same session
- honor `Retry-After` when rate-limited or backpressured

## 9. Supported Streaming Payload Patterns

The transport is generic, but there are a few payload styles the platform
already understands and renders consistently.

### A) Main answer / thinking text

Use `chat.delta` with marker `answer` or `thinking`.

Example:

```json
{
  "type": "chat.delta",
  "delta": {
    "text": "Here is the answer.",
    "index": 0,
    "marker": "answer"
  }
}
```

### B) Structured subsystem payloads

Use `chat.delta` with marker `subsystem` when the client should route the
payload to a specialized widget/tool panel.

Example:

```json
{
  "type": "chat.delta",
  "delta": {
    "text": "{\"status\":\"running\",\"progress\":42}",
    "index": 0,
    "marker": "subsystem"
  },
  "extra": {
    "sub_type": "code_exec.status",
    "format": "json",
    "artifact_name": "code_exec.status"
  }
}
```

### C) Canvas-style artifact stream

Use `chat.delta` with marker `canvas` for inline artifact/canvas content such as
HTML, JSON, or managed structured payloads.

Example:

```json
{
  "type": "chat.delta",
  "delta": {
    "text": "{\"type\":\"chart\",\"data\":{\"points\":[1,2,3]}}",
    "index": 0,
    "marker": "canvas"
  },
  "extra": {
    "format": "json",
    "artifact_name": "canvas.chart.v1",
    "title": "Chart"
  }
}
```

### D) Compact timeline text

Use `chat.delta` with marker `timeline_text` for short human-readable entries.

Example:

```json
{
  "type": "chat.delta",
  "delta": {
    "text": "Loaded 3 prior turns",
    "index": 0,
    "marker": "timeline_text"
  }
}
```

### E) Custom typed events

If the bundle wants a custom non-delta semantic event, it can emit a typed
event that still travels over the standard streaming transport.

Example:

```json
{
  "type": "bundle.preferences.updated",
  "timestamp": "2026-04-01T10:00:00Z",
  "event": {
    "agent": "preferences",
    "step": "preferences.updated",
    "status": "completed",
    "title": "Preferences updated"
  },
  "data": {
    "keys": ["city", "diet"]
  }
}
```

Client rule:

- built-in markers and event families are rendered by platform clients
- custom markers or custom event types are allowed, but a client must
  explicitly support them to do anything more than generic display/logging

See:

- [bundle-sse-events-README.md](bundle-sse-events-README.md)
- [README-comm.md](../../service/comm/README-comm.md)

## 10. Typical Browser Patterns

### Standard app

1. open SSE with `stream_id`
2. send chat via `POST /sse/chat`
3. receive streamed events on that stream

### Widget or custom bundle frontend

1. get or reuse connected peer id from the host app
2. call `/api/integrations/*`
3. include the configured stream-id header if bundle-side communicator emits should go only to that peer

### Cookie-based proxylogin deployment

1. browser keeps token cookies
2. requests omit explicit auth headers
3. server falls back to configured auth cookies

## 11. What To Read Next

- streaming payload catalog:
  [bundle-sse-events-README.md](bundle-sse-events-README.md)
- reconnect, draining, retry, and multi-tab behavior:
  [bundle-frontend-awareness-README.md](bundle-frontend-awareness-README.md)
- server-side auth transport details:
  [auth-README.md](../../service/auth/auth-README.md)
