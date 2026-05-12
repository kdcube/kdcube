---
title: Versatile Runtime Scenarios
kind: scenarios
bundle_id: versatile@2026-03-31-13-36
updated_at: 2026-05-12
---

# Versatile Runtime Scenarios

This page describes the main scenarios demonstrated by the reference bundle.
It is not a product manual; it is a maintainer map for understanding which
bundle surfaces cooperate in each runtime flow.

## Scenario 1: KDCube Chat With Preference Capture

```text
user message
  |
  v
VersatileWorkflow.process_main_turn(...)
  |
  +-- if preferences.auto_capture=true:
  |     auto_capture_preferences(...)
  |       -> preferences/users/<user_id>/events.jsonl
  |       -> preferences/users/<user_id>/current.json
  |
  v
gate -> React solver
  |
  +-- preferences tools may read/write bundle storage
  |
  v
assistant answer
```

This scenario demonstrates how a bundle can combine a normal React workflow
with bundle-local durable state. The preference store is deliberately simple:
it is a reference pattern for storage/tool/widget integration.

## Scenario 2: KDCube Widget Preferences Canvas

```text
KDCube control plane
  |
  v
iframe loads widgets/versatile_webapp
  |
  v
parent CONFIG_REQUEST handshake
  |
  v
operations/versatile_webapp_data
  |
  +-- preferences canvas payload
  +-- conversation list payload
  +-- admin visibility based on KDCube role
  |
  v
Memory / Chats / Admin tabs
```

The widget uses authenticated KDCube operations APIs. It does not use Telegram
`initData` in this mode.

The Memory tab reads and edits the preferences canvas. Saving appends events
and rewrites the current preference view.

The Chats tab uses the platform conversation integration to list, create,
switch, and delete the active conversation for this bundle/user context.

The Admin tab uses operations APIs to manage the Telegram user registry.

## Scenario 3: Telegram Bot Chat

```text
Telegram user sends message
  |
  v
Telegram Bot API calls public/telegram_webhook
  |
  v
bundle validates X-Telegram-Bot-Api-Secret-Token
  |
  v
telegram update idempotency claim
  |
  +-- first message from user:
  |     create or refresh anonymous registry row
  |
  +-- registered/admin user:
        resolve KDCube user scope + conversation id
        run normal VersatileWorkflow turn
        deliver text/files back to Telegram
```

The webhook route is public but bundle-authenticated by Telegram's webhook
secret header. The bot token is used only by the server side to call Telegram
for outgoing messages/files.

## Scenario 4: Telegram Mini App

```text
Telegram opens versatile_webapp
  |
  v
widget detects Telegram WebApp runtime
  |
  v
public/telegram_versatile_webapp_data
  header/body contains Telegram initData
  |
  v
bundle validates initData HMAC with bot token
  |
  v
resolve Telegram registry row
  |
  +-- role anonymous -> reject
  +-- role registered/admin -> serve Memory and Chats data
  +-- role admin -> also serve Admin tab data
```

The same source-folder widget is used for KDCube and Telegram. The difference
is the auth lane:

```text
KDCube iframe    -> operations/* APIs with KDCube auth
Telegram Mini App -> public/telegram_* APIs with signed initData
```

## Scenario 5: Bundle-Authenticated MCP Endpoint

```text
MCP client
  |
  v
operations/mcp/preferences_tools
  header: X-Versatile-Preferences-MCP-Token
  |
  v
bundle checks shared token from bundle secrets
  |
  v
preference CRUD tools
```

This demonstrates bundle-owned MCP auth. The shared token is a bundle secret;
the header name is bundle config.

## Scenario 6: Isolated Exec Report From Bundle API

```text
widget button / operations call
  |
  v
preferences_exec_report
  |
  v
read preference snapshot from bundle storage
  |
  v
run isolated exec
  |
  v
produce markdown report artifact
```

This demonstrates invoking isolated exec from bundle backend code. The report
is generated from bundle-owned storage and returned as operation metadata.

## Scenario 7: Widget Build And Serving

```text
first request for versatile_webapp
  |
  v
bundle on_load / UI builder
  |
  +-- build lock acquired by one worker
  |     npm install --no-package-lock
  |     OUTDIR=<dest> npm run build
  |
  +-- other workers wait or serve existing current build
  |
  v
static widget served from bundle storage
```

The widget source is:

```text
widgets/versatile_webapp
```

The generated output is rebuildable and should not be treated as source.

## What A Bundle Builder Should Copy

Use these patterns when building another bundle:

- keep external-integration prerequisites in `docs/integrations/`
- document canonical bundle storage separately from rebuildable UI output
- document both KDCube and external auth lanes when the same widget runs in
  multiple hosts
- keep public routes disabled unless the corresponding external setup exists
- store deployment secrets in bundle secrets, not in `bundles.yaml`
- record operator/user setup steps that cannot be automated by KDCube
