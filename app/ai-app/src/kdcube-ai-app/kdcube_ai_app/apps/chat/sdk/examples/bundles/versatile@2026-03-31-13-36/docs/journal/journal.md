---
title: Versatile Bundle Journal
kind: bundle-journal
bundle_id: versatile@2026-03-31-13-36
updated_at: 2026-06-18
---

# Journal

## 2026-06-18

- Released `2026.6.18.2105` as the reference scene/component bundle line.
- Kept `ui/scene` as the main bundle view and kept `ui/main` only as a legacy
  comparison surface.
- Released the SDK chat, memory, and canvas component composition in the
  reference scene, including draggable/resizable panels, context drops, chat
  context pins, and right-side component controls.
- Released the bundle canvas service wiring with shared `canvas.*` operation
  names and the SDK event/source policy path for canvas and memory objects.
- Included the ReAct external-event ownership and close-gate fixes needed by
  Telegram and other reactive event sources: accepted event blocks now belong to
  the runtime turn, while ingress active-turn information remains provenance.
- Included the gateway auth-log noise fix so expected bundle-session rejection
  degrades to anonymous without warning traceback floods on public deployments.
- Recorded follow-up validation for deployed public landing-page iframe use and
  compact widget defaults after descriptor restaging.

## 2026-06-13

- Adopted the SDK economics enforcement fixes from `economics_fixes`: shared
  quota-lock handling, funding-flow-owned plan/paid reservation paths, paid
  lane fallback, zero-cost release behavior, and subscription/wallet settlement
  coverage now live in SDK code instead of bundle-specific handlers.
- Kept `versatile` as the reference example bundle and did not restore removed
  copilot/react example bundles. The useful copilot-side addition was the
  configurable telemetry ingest header; `versatile` now exposes
  `telemetry_sink.auth_header` in config and sends the sink token through that
  header when configured.
- Updated the interface notes so deployers know when to use a non-
  Authorization telemetry header behind gateways that parse `Authorization` as
  a platform JWT.

## 2026-06-09

- Retargeted the active main view to `ui/scene` while keeping `ui/main` as a
  legacy comparison surface.
- Mounted the reusable SDK chat widget as `versatile_chat`, the SDK memory
  widget as `memories`, and the SDK canvas component through the scene host.
- Added generic canvas backend operations and Data Bus subject `canvas.patch`.
  The reference bundle intentionally uses `canvas.*` protocol names, not
  bundle-prefixed names.
- Updated `config/bundles.template.yaml` to show the scene main view,
  `versatile_chat`, `memories`, `telegram_miniapp`, and the shared SDK canvas
  component source.
- Added `docs/design/scene-sdk-components.md` to document the component wiring.

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
- Added a source-folder `telegram_miniapp` widget that can run in the KDCube
  control plane or as a Telegram Mini App.
- Added public Telegram WebApp APIs for profile, conversations, memory canvas,
  and admin operations.
- Added `config/`, `interface/`, and `release.yaml` maintainer artifacts so the
  reference bundle follows the bundle-maintainer contract.
- Expanded bundle documentation with storage, Telegram operator setup, runtime
  scenarios, and Telegram WebApp design notes so the reference bundle documents
  the external work required beyond simply hosting the bundle in KDCube.
