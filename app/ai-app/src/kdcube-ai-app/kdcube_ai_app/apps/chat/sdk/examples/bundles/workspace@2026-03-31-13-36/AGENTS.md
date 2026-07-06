# Versatile Reference Bundle Agent Notes

This bundle is the current reference implementation for a full KDCube chat
bundle. Treat it as a config-first bundle: consumer tool/event/canvas surfaces
are declared in `config/bundles.template.yaml` and deployment config, not in a
Python defaults module.

## Current Shape

Important files:

- `entrypoint.py` - bundle entrypoint, API/widget decorators, graph bootstrap.
- `agents/main.py` - gate to ReAct workflow and tool catalog wiring.
- `services/canvas.py` - canvas storage, resolver registry, object actions, Data Bus patch handling.
- `services/telemetry.py` - comm event recording and telemetry sink delivery.
- `config/bundles.template.yaml` - reference consumer surfaces, UI widgets, Telegram, telemetry, memory, canvas.
- `config/bundles.secrets.template.yaml` - reference secret keys.
- `ui/scene` - active main view.
- `ui/widgets/telegram_miniapp` - Telegram Mini App widget source.
- `interface/README.md` and `interface/versatile.openapi.yaml` - frontend/API contract.

## Tool Wiring

The solver tool policy is read from:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools: [...]
        event_sources: [...]
    ui:
      canvas:
        resolvers: [...]
```

Lists are explicit policy lists. If a deployment wants to remove a tool source,
it removes that item from config. Runtime code should call
`agent_tool_config_from_bundle_props(...)` directly on effective bundle props.

Named-service tools are agent-facing only when declared under
`surfaces.as_consumer.agents.<agent>.tools`. Canvas object actions are UI-facing
and belong under `surfaces.as_consumer.ui.canvas.resolvers`.

## UI Surfaces

The active main view is `ui/scene`. It embeds SDK widgets and the SDK canvas
component.

The widgets currently configured by the reference bundle are:

- `versatile_chat` from `sdk://solutions/chat/ui/widget`
- `memories` from `sdk://context/memory/ui/widget/memories`
- `usage_card` from `sdk://infra/economics/ui/widget/usage-card`
- `pinboard` from `sdk://solutions/canvas/ui/widget/pinboard`
- `telegram_miniapp` from `ui/widgets/telegram_miniapp`

The Telegram Mini App uses `telegram_miniapp_data` for bootstrap in both
authenticated KDCube widget mode and Telegram public mode. Telegram mode sends
raw `window.Telegram.WebApp.initData` on every public API call.

## Scene And Canvas Contract

Scene composition is server-configured. Put widget/source/surface/resolver
configuration in `config/bundles.template.yaml` and deployment `bundles.yaml`:

```yaml
ui.main_view.shared_sources
ui.widgets
surfaces.as_consumer.ui.scene.external_panels
surfaces.as_consumer.ui.canvas.resolvers
surfaces.as_consumer.agents.main.event_sources
```

Do not add hidden widget subscriptions to `ui/scene/src/main.tsx`. Widgets must
post `kdcube-scene-subscribe`; the scene logs registration and dispatch.

Do not parse `mem:`, `task:`, `conv:`, or future provider refs in scene/canvas UI
code to decide behavior. Pass the full `object_ref` to `canvas_object_action` or
the scene runtime and let the provider resolver return `ui_event.target_surface`.

Canvas stores proxy cards and canvas-owned annotations/layout. Provider objects
stay in their owning app.

## Validation

Run focused Python checks with:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m pytest -q \
  app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tests
```

Run the Telegram Mini App build check from:

```bash
cd app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/widgets/telegram_miniapp
npm install --no-package-lock
OUTDIR=/tmp/telegram-miniapp-build npm run build
```
