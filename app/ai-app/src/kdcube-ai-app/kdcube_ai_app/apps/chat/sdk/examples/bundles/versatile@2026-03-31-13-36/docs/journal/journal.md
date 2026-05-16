---
title: Versatile Bundle Journal
kind: bundle-journal
bundle_id: versatile@2026-03-31-13-36
updated_at: 2026-05-16
---

# Journal

## 2026-05-16

- Released `2026.5.16.407` together with the platform release line.
- Reworked the reference WebApp to reuse shared memory and Telegram widget UI
  sources instead of copying bundle-local implementations.
- Kept the WebApp usable both as a KDCube widget and as a Telegram Mini App by
  detecting signed Telegram `initData` before switching to Telegram mode.
- Updated Telegram setup docs with compact BotFather, webhook, and Mini App
  commands.
- Kept durable user memory as the replacement for the older preference-demo
  surface and documented the shared-widget build pattern for bundle builders.

## 2026-05-12

- Added Telegram bot transport to the reference bundle.
- Added a source-folder `versatile_webapp` widget that can run in the KDCube
  control plane or as a Telegram Mini App.
- Added public Telegram WebApp APIs for profile, conversations, memory canvas,
  and admin operations.
- Added `config/`, `interface/`, and `release.yaml` maintainer artifacts so the
  reference bundle follows the bundle-maintainer contract.
- Expanded bundle documentation with storage, Telegram operator setup, runtime
  scenarios, and Telegram WebApp design notes so the reference bundle documents
  the external work required beyond simply hosting the bundle in KDCube.
