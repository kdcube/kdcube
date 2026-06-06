---
id: ks:docs/sdk/bundle/versatile-reference-bundle-README.md
title: "Versatile Reference Bundle"
summary: "Reference implementation guide for the versatile bundle: file layout, exposed surfaces, configuration patterns, and where to mine working bundle patterns."
tags: ["sdk", "bundle", "reference", "example", "react", "configuration", "widget", "api", "mcp"]
keywords: ["reference implementation bundle", "working bundle patterns", "file layout example", "configuration surface example", "widget api mcp data bus example", "versatile bundle reference", "telegram webapp bundle"]
see_also:
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/bundle/auth-bundle-federated-README.md
  - ks:docs/service/comm/data-bus-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
---
# Versatile Reference Bundle

Reference bundle root:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

This is the bundle to study first.

## What It Demonstrates

| Capability | Where to look |
| --- | --- |
| Entry point and graph bootstrap | `entrypoint.py` |
| React workflow orchestration | `orchestrator/workflow.py` |
| Economics + cross-conversation memory entrypoint | `entrypoint.py` via `BaseEntrypointWithEconomicsAndMemory` |
| Bundle-local memory/preference tools | `tools/preference_tools.py` |
| Bundle-local skills | `skills_descriptor.py` and bundle `skills/` tree |
| Effective bundle props and defaults | `entrypoint.py`, `config/bundles.template.yaml` |
| Bundle secrets; prefer `await get_secret("b:...")` in async code | `config/bundles.secrets.template.yaml`, `tools/preference_tools.py` |
| Bundle storage layout | `preferences_store.py`, `docs/storage/README.md` |
| Source-folder widget with shared UI sources | `entrypoint.py`, `ui/widgets/versatile_webapp`, `config/bundles.template.yaml` |
| Iframe main view | `ui/main/src/App.tsx`, `entrypoint.py` |
| Public Telegram webhook and WebApp bridge | `entrypoint.py`, `docs/integrations/telegram-setup.md`, `docs/design/telegram-webapp.md` |
| Federated Data Bus claim for Telegram WebApp | `entrypoint.py:telegram_federated_data_bus_claim` |
| Data Bus handler and browser probe | `entrypoint.py:data_bus_echo`, `ui/widgets/versatile_webapp/src/store/dataBusClient.ts` |
| MCP connector declarations | `tools_descriptor.py` |

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
2. `orchestrator/workflow.py`
3. `docs/scenarios/README.md`
4. `docs/storage/README.md`
5. `ui/main/src/App.tsx`
6. `ui/widgets/versatile_webapp/src/App.tsx`
7. `ui/widgets/versatile_webapp/src/store/dataBusClient.ts`
8. `tools_descriptor.py`
9. `skills_descriptor.py`
10. `tools/preference_tools.py`
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
- `integrations.telegram.web_app_auth_max_age_seconds`
- `visibility.bundle.allowed_roles`
- `ui.widgets.versatile_webapp.src_folder`
- `ui.widgets.versatile_webapp.build_command`
- `ui.widgets.versatile_webapp.shared_sources`
- `mcp.services`

Secret props demonstrated here include:

- `telemetry_sink.auth.token`
- `integrations.telegram.bot_token`
- `integrations.telegram.webhook_secret`
- `mcp.preferences.auth.shared_token`
- `preferences.snapshot_hmac_key`

The `versatile_webapp` widget imports two shared source packages during the
widget build:

- `sdk://context/memory/ui/widget/memories` into `_shared/memory-widget`
- `sdk://integrations/telegram/ui/widget.telegram` into `_shared/telegram-widget`

Read the exact rules here:

- [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [bundle-reserved-platform-properties-README.md](bundle-reserved-platform-properties-README.md)

## API and UI Surface Actually Present

This bundle currently demonstrates:

- authenticated operations endpoints via `@api(..., route="operations")`
- widget discovery via `@ui_widget(...)`
- public Telegram endpoints via `@api(..., route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)`
- a custom main UI
- a source-folder widget alias: `versatile_webapp`
- shared source materialization for widget builds
- Telegram webhook ingestion and Telegram WebApp operations
- a federated Data Bus token claim endpoint for the Telegram WebApp
- a scoped Data Bus handler on subject `versatile.echo`
- the single-`@on_job` dispatch pattern for SDK mixins: call
  `await super().handle_job(**kwargs)` first, then process bundle-owned
  `work_kind` values only when the superclass returns `handled=false`

Use the exact decorator and route contract here:

- [bundle-platform-integration-README.md](bundle-platform-integration-README.md)

## Data Bus Echo Probe

The Memory tab in `versatile_webapp` includes a small Data Bus echo probe. It is
intended as a working integration example for widgets that need server-pushed
bundle events without running a chat turn. The same probe is available from the
normal platform widget and from the Telegram WebApp.

Platform widget path:

1. The widget reads `/profile` to get the current `session_id`.
2. The widget opens Socket.IO with `user_session_id` and any available platform
   auth tokens.
3. The widget emits `data_bus.publish` with subject `versatile.echo`.
4. `entrypoint.py:data_bus_echo` replies through the Data Bus result channel.

Telegram WebApp path:

1. The widget calls the public bundle operation
   `telegram_federated_data_bus_claim`.
2. The bundle validates Telegram identity and issues a temporary federated Data
   Bus token scoped to this bundle and to `versatile.echo`.
3. The widget opens Socket.IO with `federated_token`.
4. The widget emits `data_bus.publish` with subject `versatile.echo`.
5. `entrypoint.py:data_bus_echo` replies through the Data Bus result channel.

The handler is declared with the anonymous visibility threshold so the platform
widget can call it from anonymous, registered, paid, or privileged sessions. The
Telegram path remains narrower because the federated token is scoped to the
single echo subject.

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

Widget build check:

```bash
cd app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/widgets/versatile_webapp
npm install --no-package-lock
npm run build
```

Data Bus manifest sanity check:

- the bundle manifest should include handler subject `versatile.echo`
- the Telegram claim endpoint should return only a token scoped to that subject
