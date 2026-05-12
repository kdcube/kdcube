---
title: Versatile Telegram WebApp Design
kind: design-note
bundle_id: versatile@2026-03-31-13-36
updated_at: 2026-05-12
---

# Versatile Telegram WebApp Design

The source-folder widget `ui/widgets/versatile_webapp` is both a KDCube embedded
widget and a Telegram Mini App shell.

```text
KDCube control plane iframe
  -> operations/versatile_webapp_data
  -> operations/preferences_canvas_*
  -> operations/conversations_*
  -> operations/telegram_user_admin_* (admin role)

Telegram Mini App
  -> public/telegram_versatile_webapp_data
  -> public/telegram_memory_canvas_*
  -> public/telegram_conversations_*
  -> public/telegram_webapp_user_admin_* (Telegram admin role)
```

The frontend always sends platform tokens when embedded in KDCube and sends raw
Telegram `initData` as `X-Telegram-Init-Data` when running inside Telegram.
The public Telegram APIs verify that signed initData on every request before
reading or mutating data.

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
public/telegram_* APIs
  bundle validates initData HMAC using bot token
  bundle resolves Telegram registry row and role
```

Do not use KDCube operation tokens as a substitute for Telegram `initData` in
the Mini App lane. Do not accept Telegram Mini App requests without validating
the signed `initData`.

Tabs:

- Memory: current preferences canvas. This will be replaced by the new memory
  subsystem later, but the webapp route contract stays stable.
- Chats: Telegram-linked conversation/channel selection.
- Admin: Telegram user registry, visible to KDCube admins in the control plane
  and to Telegram users with role `admin` in the registry.

## Storage Used By The Widget

The widget reads and mutates bundle storage through bundle APIs. It never writes
directly to the storage backend from the browser.

```text
preferences/users/<user_id>/current.json
preferences/users/<user_id>/events.jsonl
admin/telegram-users.json
admin/telegram-updates/...
```

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
