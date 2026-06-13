---
title: Versatile Runtime Scenarios
kind: scenarios
bundle_id: versatile@2026-03-31-13-36
updated_at: 2026-06-13
---

# Versatile Runtime Scenarios

This page describes the main scenarios demonstrated by the reference bundle.
It is not a product manual; it is a maintainer map for understanding which
bundle surfaces cooperate in each runtime flow.

## Scenario 1: KDCube Chat With SDK Durable Memory

```text
user message
  |
  v
VersatileWorkflow.process_main_turn(...)
  |
  v
gate -> React solver
  |
  +-- memory tools may read/write SDK durable memory
  |
  v
assistant answer
```

This scenario demonstrates how a bundle can combine a normal React workflow
with SDK durable memory. The bundle-local preference store has been removed;
the `product.preferences` skill now steers the solver toward the SDK memory
tools when the user reveals durable preferences or asks about remembered facts.

## Scenario 1A: KDCube Scene With SDK Components

```text
KDCube control plane
  |
  v
iframe loads ui/scene
  |
  +-- embeds widgets/versatile_chat
  +-- embeds widgets/memories
  +-- renders SDK CanvasBoard
  |
  +-- canvas reads/uploads/actions -> bundle operations
  +-- canvas patches -> Data Bus subject canvas.patch
  +-- context attach/focus -> versatile_chat iframe
```

The active scene is the reference for assembling reusable SDK components in one
bundle. See `docs/design/scene-sdk-components.md` for the exact widget and
operation wiring.

## Scenario 2: Telegram Bot Chat

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

## Scenario 3: Telegram Mini App

```text
Telegram opens telegram_miniapp
  |
  v
ui/widgets/telegram_miniapp detects Telegram WebApp runtime
  |
  v
public/telegram_miniapp_data
  header/body contains Telegram initData
  |
  v
bundle validates initData HMAC with bot token
  |
  v
resolve Telegram registry row
  |
  +-- role anonymous -> reject
  +-- role registered/admin -> serve SDK memory + conversations
  +-- role admin -> also serve Telegram admin data
```

The source folder is `ui/widgets/telegram_miniapp`. It demonstrates a Telegram
Mini App shell that embeds the SDK memory widget and the SDK Telegram widget
panels. The same source can also be opened through the KDCube widget route for
operator testing.

## Scenario 4: Agent Consumer Surfaces

```text
bundles.yaml
  |
  v
surfaces.as_consumer.agents.main.tools
  |
  +-- python tools -> SDK/local tool modules
  +-- mcp tools -> configured MCP servers
  +-- named_service tools -> namespace-owned provider operations
  |
  +-- event_sources/pull -> react.pull can materialize external refs
  +-- ui.canvas.resolvers -> canvas object cards delegate actions
  v
VersatileWorkflow builds ReAct catalog and runtime event-source bridges
```

This demonstrates the current consumer-side configuration surface. Tool
connections are agent-scoped. Named-service refs are not native ReAct files;
configured pull policies materialize them into `fi:` artifacts and configured
canvas resolvers delegate UI object actions to the owning provider.

## Scenario 5: Widget Build And Serving

```text
first request for configured SDK widget
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

The configured widget sources are SDK sources:

```text
sdk://solutions/chat/ui/widget
sdk://context/memory/ui/widget/memories
sdk://infra/economics/ui/widget/usage-card
sdk://solutions/canvas/ui/widget/pinboard
ui/widgets/telegram_miniapp
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
