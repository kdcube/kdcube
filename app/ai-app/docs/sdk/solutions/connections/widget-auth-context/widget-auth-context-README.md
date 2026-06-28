---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/widget-auth-context/widget-auth-context-README.md
title: "Widget Auth Context Transport"
summary: "Connection Hub role: standard host-to-iframe auth-context transport through CONFIG_REQUEST/CONFIG_RESPONSE and opaque promoted headers."
status: active
tags: ["sdk", "connections", "connection-hub", "widgets", "iframe", "auth-context", "scene"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/cross-surface-context-drag-README.md
---
# Widget Auth Context Transport

Connection Hub widgets can be hosted by a browser scene, a Telegram Mini App, or
another app-owned iframe host. The child widget should not learn provider
internals from its parent. It should receive an opaque auth context and promote
that context on its own API calls.

## Standard Handshake

```text
child iframe
  -> CONFIG_REQUEST(identity=CONNECTIONS_WIDGET)

host
  -> CONFIG_RESPONSE({
       baseUrl,
       tenant,
       project,
       authContext: {
         headers: {
           ...
         }
       }
     })

child iframe
  -> calls its own backend APIs with authContext.headers
```

There should not be a second `kdcube.auth.*` message family for Telegram or any
other provider. Telegram proof travels through the same widget config handshake
as bearer/cookie scene auth material.

## Telegram Example

```text
Telegram Mini App host
  has Telegram.WebApp.initData
  has server config authority_id=telegram.kdcube_ref
  has server config authenticator_id=telegram.kdcube_ref.init_data
       |
       v
CONFIG_RESPONSE.authContext.headers
  X-Telegram-Init-Data: <initData>
  X-KDCube-Auth-Provider: telegram
  X-KDCube-Auth-Authority-ID: telegram.kdcube_ref
  X-KDCube-Auth-Authenticator-ID: telegram.kdcube_ref.init_data
       |
       v
Connection Hub iframe requests
  include those headers unchanged
```

The child iframe:

- does not read `window.parent.Telegram`;
- does not know bot tokens;
- does not validate Telegram proof;
- does not call the host app to complete Connection Hub work.

The host:

- reads its server-side integration config;
- collects runtime provider proof available to that host;
- passes only the auth context required for the child to call its own backend.

## Auth Refresh Signal

Hosts should emit:

```text
kdcube-auth-changed
```

when auth material becomes available or changes. The child can re-request config
through the same handshake and retry its normal API calls.

## Why This Matters

```text
host knows context
  Telegram initData / bearer token / cookies / integration id
       |
       v
child receives opaque headers
       |
       v
backend authenticates through gateway or Connection Hub
```

This lets the same widget work in:

- website scene;
- KDCube scene;
- Telegram Mini App;
- future app-owned iframe surfaces.
