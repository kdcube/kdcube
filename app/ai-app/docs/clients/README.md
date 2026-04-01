---
id: ks:docs/clients/README.md
title: "Clients"
summary: "Start here for client integrations: transport/auth contract, SSE events, and frontend behavior under scaling, draining, and rate limits."
tags: ["clients", "frontend", "transport", "auth", "sse", "socketio", "rest"]
keywords: ["client integration", "headers", "cookies", "sse", "socketio", "rest", "stream id", "session id"]
see_also:
  - ks:docs/clients/client-communication-README.md
  - ks:docs/clients/sse-events-README.md
  - ks:docs/clients/frontend-awareness-on-service-state-README.md
  - ks:docs/service/auth/auth-README.md
  - ks:docs/service/comm/README-comm.md
---
# Clients

This section is the client-facing entry point for browser apps, widgets, adapters, and other external consumers.

## Read Order

1. [client-communication-README.md](client-communication-README.md)
   The transport/auth contract: supported headers, cookies, query params, response headers, SSE vs Socket.IO, and how REST requests can target one connected peer.
2. [sse-events-README.md](sse-events-README.md)
   The streaming event catalog and envelope shape.
3. [frontend-awareness-on-service-state-README.md](frontend-awareness-on-service-state-README.md)
   Retry, draining, rate-limit, reconnect, and multi-tab behavior.

## Scope

Use these docs when you need to know:

- how clients authenticate over REST, SSE, Socket.IO, and integrations
- which cookies and headers are supported
- which values are configurable by env
- how `stream_id` and `KDC-Stream-ID` affect peer-targeted delivery
- which response headers and retry signals the client should honor

## Server-Side References

- auth transport and precedence:
  [auth-README.md](../service/auth/auth-README.md)
- transport and relay internals:
  [README-comm.md](../service/comm/README-comm.md)
