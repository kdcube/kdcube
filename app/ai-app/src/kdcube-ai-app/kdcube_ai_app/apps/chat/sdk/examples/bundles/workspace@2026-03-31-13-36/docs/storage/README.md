---
title: Versatile Bundle Storage
kind: storage-map
bundle_id: versatile@2026-03-31-13-36
updated_at: 2026-06-23
---

# Versatile Bundle Storage

This page explains which state the reference bundle owns, where it is stored,
and which files are canonical versus rebuildable. It is written for bundle
maintainers and bundle-builder copilots that need to debug a local deployment
or port the pattern into a real application bundle.

## Roots

The bundle uses the SDK `AIBundleStorage` backend. In local development the
root usually looks like:

```sh
B=/Users/elenaviter/.kdcube/dev-workspace/data/bundle-storage/demo-tenant/demo-project/versatile-2026-03-31-13-36
```

Use the actual tenant, project, bundle id, and storage backend from the
deployment. On cloud deployments this storage may be backed by shared filesystem
storage or another configured bundle-storage backend.

## Canonical Bundle Data

```text
$B/
  admin/
    telegram-users.json
    telegram-updates/
      state.json
      <update_id>.json
  canvas/
    users/
      <user_id>/
        canvases/
          index.json
          <canvas_id>/
            latest.json
            revisions/
              <revision>.json
            objects/
              ...
```

### Durable memory

Remembered user facts and preferences are stored by the SDK durable-memory
subsystem, not by a bundle-local `preferences/` tree. Versatile configures the
SDK memory tools and widget as consumer surfaces; memory storage itself follows
the SDK memory storage contract.

### Telegram Registry

`admin/telegram-users.json` is the bundle-owned Telegram user registry. It maps
Telegram user ids to:

- Telegram chat id
- Telegram username
- optional KDCube user id
- bundle-local role: `anonymous`, `registered`, or `admin`
- default conversation id
- operator notes

Telegram users are recorded as `anonymous` when the webhook first sees them.
An operator must promote them to `registered` or `admin` before they can use
Telegram bot turns or Telegram Mini App APIs.

### Telegram Webhook Idempotency

`admin/telegram-updates/` stores update claim/completion records so Telegram
webhook retries do not execute the same update repeatedly. These files are
bundle operational state and should be preserved with the bundle storage root.

### Canvas

`canvas/users/<user_id>/canvases/index.json` is the per-user canvas manifest
for the active scene story.

`canvas/users/<user_id>/canvases/<canvas_id>/latest.json` is the latest
materialized board state. Revisions are retained under `revisions/` according
to `canvas.revision_retention`.

Canvas-owned text and attachments are stored under `objects/`. Proxy cards keep
their canonical object refs (`fi:`, `mem:`, `cnv:`, etc.) and are not rehosted
just because they were pinned.

### Scene and resolver config

Scene composition is not stored in the canvas data tree. It is runtime app
configuration:

```text
bundles.yaml
  config.namespace_styles
  config.ui.main_view.shared_sources
  config.ui.widgets
  config.surfaces.as_consumer.ui.scene.external_panels
  config.surfaces.as_consumer.ui.canvas.resolvers
  config.surfaces.as_consumer.agents.main.event_sources
```

Those settings decide which widgets are mounted, which npm shared sources are
materialized, which external panels are registered as target surfaces, and which
provider resolvers are available for object actions. The actual widget build
output remains rebuildable under `ui/`.

## Rebuildable Data

```text
$B/
  ui/
    main_view/
      index.html
      assets/...
    widgets/
      versatile_chat/
        index.html
        assets/...
      memories/
        index.html
        assets/...
      usage_card/
        index.html
        assets/...
      telegram_miniapp/
        index.html
        assets/...
      pinboard/
        index.html
        assets/...
```

Widget output under `ui/widgets/` is generated from:

```text
sdk://solutions/chat/ui/widget
sdk://context/memory/ui/widget/memories
sdk://infra/economics/ui/widget/usage-card
sdk://solutions/canvas/ui/widget/pinboard
ui/widgets/telegram_miniapp
```

It is safe to rebuild from source. The shared build lock under `.kdcube.once/`
prevents multiple workers from building the same static widget at the same
time.

The active main view is generated from:

```text
ui/scene/
```

The main view output is also rebuildable from source.

## Platform-Owned Conversation Data

Versatile bot turns still use the normal KDCube conversation store. Those
conversation records, uploaded files, generated artifacts, and turn logs are
platform-owned, not bundle-storage-owned.

The bundle connects Telegram messages to the normal chat workflow. Generated
files that must be delivered back to Telegram are hosted by the platform before
temporary execution workspaces are cleaned.

## Storage Map

| Purpose | Path or owner | Canonical? | Notes |
| --- | --- | --- | --- |
| Durable memory | SDK memory storage | Yes | User facts/preferences handled by the SDK memory subsystem. |
| Telegram user registry | `$B/admin/telegram-users.json` | Yes | Operator-managed mapping from Telegram users to KDCube users/roles/conversations. |
| Telegram webhook update state | `$B/admin/telegram-updates/...` | Yes | Idempotency state for Bot API retries. |
| Canvas state | `$B/canvas/users/<user_id>/canvases/...` | Yes | Versioned board state and canvas-owned objects for the active scene. |
| Scene/resolver config | `bundles.yaml` effective app config | Yes, config-owned | Controls widgets, shared npm sources, external panels, presentation, and provider resolvers. |
| Chat widget static output | `$B/ui/widgets/versatile_chat/...` | No | Built from `sdk://solutions/chat/ui/widget`. |
| Memory widget static output | `$B/ui/widgets/memories/...` | No | Built from `sdk://context/memory/ui/widget/memories`. |
| Usage-card widget static output | `$B/ui/widgets/usage_card/...` | No | Built from `sdk://infra/economics/ui/widget/usage-card`. |
| Telegram Mini App static output | `$B/ui/widgets/telegram_miniapp/...` | No | Built from `ui/widgets/telegram_miniapp`. |
| Pinboard widget static output | `$B/ui/widgets/pinboard/...` | No | Built from `sdk://solutions/canvas/ui/widget/pinboard`. |
| Main view static output | bundle UI build output | No | Built from `ui/scene`. |
| Chat timeline/files | platform conversation store | Yes, platform-owned | Normal KDCube conversation persistence. |

## Debugging

Set local roots:

```sh
B=/Users/elenaviter/.kdcube/dev-workspace/data/bundle-storage/demo-tenant/demo-project/versatile-2026-03-31-13-36
U=<user_id>
```

Inspect Telegram registry:

```sh
jq . "$B/admin/telegram-users.json"
```

Find webhook update records:

```sh
find "$B/admin/telegram-updates" -type f -print
```

If the widget does not refresh after source changes, inspect or remove stale
build output only when no build is running. Canonical state is not under
`ui/widgets/`.
