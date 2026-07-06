---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md
title: "Versatile Reference Bundle"
summary: "Reference implementation guide for the versatile bundle: file layout, exposed surfaces, configuration patterns, and where to mine working bundle patterns."
tags: ["sdk", "bundle", "reference", "example", "react", "configuration", "widget", "api", "mcp"]
keywords: ["reference implementation bundle", "working bundle patterns", "file layout example", "configuration surface example", "widget api mcp data bus example", "versatile bundle reference", "telegram webapp bundle"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-developer-guide-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-sdk-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
---
# Versatile Reference Bundle

Reference bundle root:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

This is the bundle to study first.

## What It Demonstrates

| Capability | Where to look |
| --- | --- |
| Entry point and graph bootstrap | `entrypoint.py` |
| React workflow orchestration | `agents/main.py` |
| Economics + cross-conversation memory entrypoint | `entrypoint.py` via `BaseEntrypointWithEconomicsAndMemory` |
| Agent and UI consumer surfaces | `config/bundles.template.yaml` under `surfaces.as_consumer` |
| SDK durable memory | `memory.*` tools, shared memory widget, and `BaseEntrypointWithEconomicsAndMemory` |
| Effective bundle props and defaults | `entrypoint.py`, `config/bundles.template.yaml` |
| Bundle secrets; prefer `await get_secret("b:...")` in async code | `config/bundles.secrets.template.yaml`, `entrypoint.py` |
| Canvas and telemetry service helpers | `services/canvas.py`, `services/telemetry.py` |
| Active iframe main scene | `ui/scene`, `entrypoint.py`, `docs/design/scene-sdk-components.md` |
| SDK chat widget mount | `ui.widgets.versatile_chat` in `entrypoint.py` and `config/bundles.template.yaml` |
| SDK memory widget mount | `ui.widgets.memories` in `entrypoint.py` and `config/bundles.template.yaml` |
| SDK canvas component mount | `ui/scene`, `sdk://solutions/canvas/ui/component`, `entrypoint.py` canvas operations |
| SDK canvas board as a standalone widget | `ui.widgets.pinboard`, `sdk://solutions/canvas/ui/widget/pinboard`; see [Scene Composition](../solutions/scene/scene-composition-README.md#the-canvas-board-as-a-standalone-widget) |
| Public Telegram webhook and WebApp bridge | `entrypoint.py`, `docs/integrations/telegram-setup.md`, `docs/design/telegram-webapp.md` |
| Telegram Mini App live Data Bus claim | Connection Hub `federated_data_bus_claim`; Versatile only forwards `authContext` |
| Data Bus handlers | `entrypoint.py:data_bus_echo`, `entrypoint.py` handler for subject `canvas.patch` |
| Canvas Data Bus mutation path | `entrypoint.py` handler for subject `canvas.patch` |
| MCP connector declarations | `surfaces.as_consumer.agents.main.tools` in config |

When studying the entrypoint, pay attention to lifecycle inheritance. A bundle
that subclasses the `BaseEntrypoint` family and overrides `on_bundle_load(...)`
must keep `await super().on_bundle_load(**kwargs)` in the hook unless it
intentionally replaces platform prop refresh and UI build behavior. That base
hook is what lets startup preload build configured `ui.main_view` and
`ui.widgets.*` assets before a user opens the UI.

## Adjacent Patterns

Use the focused SDK docs for cron jobs, isolated venv calls, and Claude Code
subagent integration:

- [bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md)
- [bundle-venv-README.md](bundle-venv-README.md)
- [bundle-agent-integration-README.md](bundle-agent-integration-README.md)

## Study Order

1. `entrypoint.py`
2. `config/bundles.template.yaml`
3. `docs/design/scene-sdk-components.md`
4. `ui/scene/src/main.tsx`
5. `ui/scene/vite.config.js`
6. `agents/main.py`
7. `docs/scenarios/README.md`
8. `docs/storage/README.md`
9. `services/canvas.py`
10. `services/telemetry.py`
11. bundle-local tests under `tests/`

## Config Surfaces Used by This Bundle

The exact seed shape is in:

- `config/bundles.template.yaml`
- `config/bundles.secrets.template.yaml`

Non-secret props demonstrated here include:

- `memory.enabled`, `memory.announce.*`, `memory.tools.*`, and `memory.widget.*`
- `execution.runtime.mode`
- `telemetry_sink.endpoint_url`
- `integrations.telegram.enabled`
- `integrations.telegram.webhook_url`
- `integrations.telegram.send_responses`
- `integrations.telegram.stream_activity`
- `integrations.telegram.stream_activity_display`
- `integrations.telegram.web_app_auth_max_age_seconds`
- `visibility.bundle.allowed_roles`
- `canvas.artifact_prefix`
- `canvas.origin_prefix`
- `canvas.state_event_source_id`
- `canvas.ui_event_type`
- `canvas.artifact_resolver_name`
- `canvas.data_bus_subject`
- `canvas.revision_retention`
- `ui.main_view.src_folder`
- `ui.main_view.build_command`
- `ui.main_view.shared_sources.canvas_component`
- `ui.widgets.versatile_chat.src_folder`
- `ui.widgets.versatile_chat.build_command`
- `ui.widgets.memories.src_folder`
- `ui.widgets.memories.build_command`
- `surfaces.as_consumer.mcp.services`

Secret props demonstrated here include:

- `telemetry_sink.auth.token`
- `integrations.telegram.bot_token`
- `integrations.telegram.webhook_secret`

The active main scene imports the SDK canvas component as a shared source:

- `sdk://solutions/canvas/ui/component` into `_shared/canvas-component`

The active scene embeds two separately built SDK widgets:

- `sdk://solutions/chat/ui/widget` as widget alias `versatile_chat`
- `sdk://context/memory/ui/widget/memories` as widget alias `memories`

Read the exact rules here:

- [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [bundle-reserved-platform-properties-README.md](bundle-reserved-platform-properties-README.md)

## API and UI Surface Actually Present

This bundle currently demonstrates:

- authenticated operations endpoints via `@api(..., route="operations")`
- widget discovery via `@ui_widget(...)`
- public Telegram endpoints via `@api(..., route="public")`
- an active scene main UI built from `ui/scene`
- the reusable SDK chat widget mounted as `versatile_chat`
- the reusable SDK memory widget mounted as `memories`
- the reusable SDK canvas component mounted into the scene as shared source
- canvas operations: `canvas_list`, `canvas_read`, `canvas_search`,
  `canvas_pin_read`, `canvas_write`, `canvas_patch`,
  `canvas_attachment_upload`, and `canvas_object_action`
- shared source materialization for widget builds
- Telegram webhook ingestion and Telegram WebApp operations
- a federated Data Bus token claim endpoint for the Telegram WebApp
- a scoped Data Bus handler on subject `versatile.echo`
- a scoped Data Bus handler on subject `canvas.patch`
- the single-`@on_job` dispatch pattern for SDK mixins: call
  `await super().handle_job(**kwargs)` first, then process bundle-owned
  `work_kind` values only when the superclass returns `handled=false`

Use the exact decorator and route contract here:

- [bundle-platform-integration-README.md](bundle-platform-integration-README.md)

## Active Scene Composition

The current main view is `ui/scene`. It is a host scene that composes SDK
components instead of reimplementing chat, memory, and canvas behavior in one
bundle-owned React app.

```text
platform opens bundle main UI
  -> ui/scene
       |
       +-- iframe widget: versatile_chat
       |     src_folder: sdk://solutions/chat/ui/widget
       |
       +-- iframe widget: memories
       |     src_folder: sdk://context/memory/ui/widget/memories
       |
       +-- React component: CanvasBoard
             shared source: sdk://solutions/canvas/ui/component
```

The scene brokers browser messages between mounted components. For example,
memory and canvas selections are forwarded to the chat widget as context
attachments. Canvas mutations are published through Data Bus subject
`canvas.patch`, partitioned by canvas object reference on the backend handler.

Use generic canvas protocol names:

| Protocol item | Current value |
| --- | --- |
| Canvas mutation subject | `canvas.patch` |
| Canvas state event source | `canvas.state` |
| Canvas focus event source | `canvas.focus` |
| Canvas UI event type | `canvas.patch.applied` |
| Canvas artifact resolver | `canvas.bundle_artifact_storage` |

Do not introduce bundle-prefixed names such as `versatile.canvas.*` for these
reusable canvas concepts.

The scene also demonstrates current resolver ownership boundaries:

| Namespace | Owner |
| --- | --- |
| `conv:fi:` | ReAct event/artifact resolver |
| `mem:` | memory subsystem resolver |
| `cnv:` | canvas-owned object resolver |

The canvas card stores one canonical object reference. The resolver owns
preview, open, download, and rehost behavior for that namespace. The scene and
canvas component should not store transport handles as alternate identities.

Current implementation note: the scene uses explicit entrypoint glue and a
Vite shared-source adapter while this SDK component composition path is being
tested. Treat this bundle as the reference for the current integration shape.
The intended SDK direction is to move the repeatable mount/build glue behind
helpers so future bundles do not copy the scene adapter by hand.

Detailed bundle-local notes:

- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/docs/design/scene-sdk-components.md`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/config/bundles.template.yaml`

## Data Bus Echo Probe

The Memory tab in `telegram_miniapp` includes a small Data Bus echo probe. It is
intended as a working integration example for widgets that need server-pushed
bundle events without running a chat turn. The same probe is available from the
normal platform widget and from the Telegram WebApp.

This echo probe is separate from the active scene canvas mutation path. Canvas
patches use subject `canvas.patch`; the echo probe uses subject
`versatile.echo`.

Platform widget path:

1. The widget reads `/profile` to get the current `session_id`.
2. The widget opens Socket.IO with `user_session_id` and any available platform
   auth tokens.
3. The widget emits `data_bus.publish` with subject `versatile.echo`.
4. `entrypoint.py:data_bus_echo` replies through the Data Bus result channel.

Telegram Mini App Connection Hub path:

1. The Telegram host receives Telegram `initData` and returns it as
   `authContext.headers` in the standard widget `CONFIG_RESPONSE`.
2. The Connection Hub iframe calls Connection Hub
   `federated_data_bus_claim`.
3. Connection Hub validates the promoted Telegram proof, resolves any
   connection edge, and issues a temporary Data Bus token scoped to
   `connection-hub@1-0`.
4. The iframe opens Socket.IO with `federated_token`.
5. Connection Hub uses that live channel to deliver
   `connection_hub.edge.changed`.

The Versatile bundle does not mint this token and does not validate Telegram
for Connection Hub. It only supplies the host-side auth context.

The detailed platform contract is in:

- [auth-bundle-federated-README.md](auth-bundle-federated-README.md)
- [../../service/comm/data-bus-README.md](../../service/comm/data-bus-README.md)

## Validation

Shared SDK bundle suite:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/versatile@2026-03-31-13-36
```

Bundle-local tests:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
pytest -q app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tests
```

Active scene build check:

```bash
cd app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/scene
npm install --no-package-lock
OUTDIR=/private/tmp/versatile-scene-build npm run build
```

Legacy WebApp widget build check:

```bash
cd app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/widgets/telegram_miniapp
npm install --no-package-lock
npm run build
```

Data Bus manifest sanity check:

- the bundle manifest should include handler subject `versatile.echo`
- the bundle manifest should include handler subject `canvas.patch`
- the Telegram claim endpoint should return only a token scoped to that subject
