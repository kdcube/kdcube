---
title: KDCube Companion Telegram Mini App Design
kind: design-note
bundle_id: versatile@2026-03-31-13-36
updated_at: 2026-06-27
---

# KDCube Companion Telegram Mini App Design

The source-folder widget `ui/widgets/telegram_miniapp` demonstrates a Telegram
Mini App shell that can also be opened as a KDCube widget for operator testing.

```text
KDCube control plane iframe
  -> operations/telegram_miniapp_data
  -> operations/conversations_*
  -> operations/telegram_user_admin_* (admin role)
  -> embedded user-memories iframe (Memory tab; user-memories app owns its APIs)
  -> embedded connection-hub iframe (Connect tab; Connection Hub owns linking)

Telegram Mini App
  -> public/telegram_miniapp_data
  -> embedded user-memories iframe (Memory tab)
       -> user-memories app's own APIs with authContext.headers
  -> embedded connection-hub iframe (Connect tab)
       -> connection-hub public APIs with authContext.headers
       -> connection-hub Socket.IO live channel for link completion events
  -> public/telegram_conversations_*
  -> public/telegram_webapp_user_admin_* (Telegram admin role)
```

The frontend always sends platform tokens when embedded in KDCube and sends raw
Telegram `initData` as `X-Telegram-Init-Data` when running inside Telegram.
For reusable SDK widgets, the Telegram shell first gets a server-authored
`authContext.headers` template from `telegram_miniapp_data`, adds browser-owned
`initData` only because that template declares Telegram as the provider, and
passes the resulting opaque header map through the standard `CONFIG_REQUEST` /
`CONFIG_RESPONSE` iframe handshake. The widget then calls its normal
`operations/*` routes and blindly promotes those headers. Gateway request-auth
delegates the Telegram proof to Connection Hub, whose Telegram authenticator
module validates the signed initData, resolves any identity link, and stamps
platform authority.

The Memory tab carries no bundle-owned memory operations. It iframes the
dedicated user-memories app, which serves its own widget and owns the durable
memory contract; the Telegram shell only forwards the `authContext` proof to
that iframe through the same `CONFIG_REQUEST` / `CONFIG_RESPONSE` handshake.

The Connect tab follows the same host/iframe rule. It iframes the Connection
Hub `connections_settings` widget. The Telegram shell forwards only the opaque
`authContext.headers` map through `CONFIG_RESPONSE`; it does not validate
Telegram, does not know Connection Hub secrets, and does not call Connection
Hub on the child's behalf. The Connection Hub iframe uses those headers on its
own public APIs.

For the link journey, the Connection Hub iframe creates its own short-lived
Socket.IO live channel by calling Connection Hub's `federated_data_bus_claim`
public operation. When the user finishes the browser-side claim, Connection
Hub emits `connection_hub.identity.link_changed` to that session. This avoids
polling and keeps Versatile out of Connection Hub's server-to-widget lifecycle.
After a link or unlink event, the iframe claims a fresh token and reconnects so
the backing `UserSession` carries the current projected authority.

## Auth Lanes

The same widget source runs in two hosts, but the auth lane is different.

```text
KDCube control plane iframe
  parent frame supplies runtime config and KDCube tokens
  |
  v
operations/* APIs
  normal KDCube operations authorization

Telegram Mini App
  Telegram.WebApp.initData
  |
  v
public/telegram_miniapp_data returns authContext.headers
  |
  v
standard widget CONFIG_RESPONSE carries authContext.headers + initData
  |
  v
operations/* APIs + promoted authContext.headers
  gateway request-auth calls Connection Hub
  Connection Hub validates initData and resolves linked platform authority

Connect tab link completion
  Connection Hub iframe claims a connection-hub live Socket.IO session
  |
  v
  connection-hub@1-0/public/telegram_identity_link_start stores that session
  on the link challenge
  |
  v
  browser-side KDCube claim completes the challenge
  |
  v
  Connection Hub emits connection_hub.identity.link_changed to the iframe
  |
  v
  iframe refreshes linked/unlinked state

public/telegram_* APIs, for app-owned Telegram routes
  app validates initData/webhook proof using bot token
  app resolves Telegram registry row and app-local role
```

Do not use KDCube operation tokens as a substitute for Telegram `initData` in
the Mini App lane. Do not accept Telegram Mini App requests without validating
the signed `initData`.

Tabs:

- Memory: SDK durable-memory widget embedded in the Mini App shell.
- Chats: Telegram-linked conversation/channel selection.
- Connect: Connection Hub widget embedded in the Mini App shell for linking the
  Telegram subject to the signed-in platform account.
- Admin: Telegram user registry, visible to KDCube admins in the control plane
  and to Telegram users with role `admin` in the registry.

## Storage Used By The Widget

The widget reads and mutates bundle storage through bundle APIs. It never writes
directly to the storage backend from the browser.

Memory records are owned by the SDK durable-memory subsystem. Telegram registry
and webhook idempotency state remain under the bundle storage root.

The storage contract is documented in:

```text
docs/storage/README.md
```

## Required External Setup

Telegram setup requires work outside the bundle:

- create or choose a bot in BotFather
- get the bot token
- expose KDCube through a public HTTPS URL, for local dev usually ngrok
- generate and store a webhook secret
- enable the Telegram public APIs in bundle config
- store Telegram secrets in the configured secrets provider
- register the webhook with Telegram
- configure BotFather menu button / Mini App URL if Mini App is used
- promote Telegram users from `anonymous` to `registered` or `admin`

The operator checklist is documented in:

```text
docs/integrations/telegram-setup.md
```

## Failure Modes

Common failures:

- Widget works in KDCube but not in Telegram:
  - Telegram public APIs are disabled, `initData` is missing/invalid, or the
    Telegram user registry row is still `anonymous`.
- Bot webhook receives no updates:
  - webhook not registered, public URL stale, ngrok stopped, or
    `telegram_webhook.POST` disabled.
- Bot receives updates but does not answer:
  - user not promoted, conversation mapping missing/invalid, bot token missing,
    or response delivery disabled.
- Admin tab appears in KDCube but not Telegram:
  - Telegram user role is not `admin`; KDCube roles do not automatically grant
    Telegram Mini App admin role.
