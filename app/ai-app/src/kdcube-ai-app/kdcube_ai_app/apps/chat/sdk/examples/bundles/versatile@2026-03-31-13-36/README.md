---
title: Versatile Reference Bundle
kind: reference-bundle
bundle_id: versatile@2026-03-31-13-36
updated_at: 2026-06-13
---

# versatile bundle

`versatile@2026-03-31-13-36` is the full-feature reference bundle for bundle builders.

It intentionally demonstrates the main SDK bundle surfaces together in one place, so a human or bundle-builder copilot can learn the platform from one concrete implementation before branching into narrower examples.

## What it demonstrates

| Capability                             | Where to look                                                                              |
|----------------------------------------|--------------------------------------------------------------------------------------------|
| Minimal bundle contract                | `entrypoint.py`, `orchestrator/workflow.py`, `skills_descriptor.py`                        |
| React workflow                         | `entrypoint.py`, `orchestrator/workflow.py`, `agents/gate.py`                              |
| Economics / quotas                     | `entrypoint.py` via `BaseEntrypointWithEconomics` and `app_quota_policies`                 |
| Bundle props / effective config        | `entrypoint.py`, `orchestrator/workflow.py`                                                |
| Bundle secrets via `get_secret(...)`   | `config/bundles.secrets.template.yaml`, `entrypoint.py`                                    |
| Agent and UI consumer surfaces         | `config/bundles.template.yaml` under `surfaces.as_consumer`                                |
| Bundle-local skills                    | `skills/product/preferences/SKILL.md`                                                      |
| Canvas and telemetry services          | `services/canvas.py`, `services/telemetry.py`                                               |
| Agent tool consumers                   | `surfaces.as_consumer.agents.main.tools`                                                   |
| MCP tool consumers                     | `surfaces.as_consumer.agents.main.tools`                                                   |
| Active iframe main view                | `ui/scene`, `entrypoint.py` main-view config                                               |
| SDK chat widget mount                  | `versatile_chat`, backed by `sdk://solutions/chat/ui/widget`                               |
| SDK durable memory widget              | shared memory widget source, inherited `memories_widget_*` operations                      |
| Memory maintenance                     | `memories_widget_snapshot_*`, `memories_widget_reconcile_*` inherited from SDK memory mixin |
| Bundle interface contract              | `interface/README.md`                                                                       |
| Bundle config templates                | `config/bundles.template.yaml`, `config/bundles.secrets.template.yaml`                     |
| Bundle release metadata                | `release.yaml`                                                                              |
| Operational storage map                | `docs/storage/README.md`                                                                    |
| Runtime scenarios                      | `docs/scenarios/README.md`                                                                  |
| Telegram bot transport                 | `entrypoint.py:telegram_webhook`, `entrypoint.py:telegram_user_admin_*`                      |
| Telegram operator setup                | `docs/integrations/telegram-setup.md`                                                       |

## Operational docs

The reference bundle now keeps the same kind of application-level operational
documentation expected from real bundles:

- `docs/storage/README.md`
  - canonical bundle data
  - rebuildable widget/main-view output
  - Telegram registry and webhook idempotency state
  - debugging paths and commands
- `docs/integrations/telegram-setup.md`
  - BotFather work
  - public HTTPS/ngrok requirement
  - `bundles.yaml` and `bundles.secrets.yaml` shape
  - Telegram webhook registration
  - Telegram user promotion from `anonymous` to `registered`/`admin`
- `docs/scenarios/README.md`
  - KDCube widget
  - Telegram bot chat
  - consumer-surface tool wiring
  - named-service canvas/ReAct integration
  - widget build/serve flow

## Bundle behavior

- The workflow is a normal gate → solver React loop.
- The active main view is `ui/scene`. It is a small scene shell that embeds the
  reusable SDK chat widget as `versatile_chat` and the shared SDK canvas
  component as a scene surface.
- Memory is handled by the SDK durable-memory subsystem and widget. The older
  bundle-local preference storage and webapp widget sources have been removed.
- The solver uses the SDK durable-memory tool surface configured under
  `surfaces.as_consumer.agents.main.tools`:
  - `memory.search_memory(...)`
  - `memory.recent_memories(...)`
  - `memory.record_memory(...)`
  - `memory.confirm_memory(...)`
  - `memory.retire_memory(...)`
- The bundle demonstrates the reusable Telegram bot transport:
  - `telegram_webhook` receives Bot API updates through a public route guarded
    by Telegram's webhook secret header
  - `telegram_user_admin_*` operations manage the bundle-owned Telegram user
    registry from KDCube-authenticated operations routes
  - queued Telegram turns are wrapped with
    `telegram_user_admin.run_with_queued_telegram_delivery(...)`, so the normal
    workflow result is rendered back to Telegram as text and files
- The bundle inherits the SDK memory widget operations through
  `BaseEntrypointWithEconomicsAndMemory`:
  - `memories_widget_snapshot_create`, `memories_widget_snapshots`, and
    `memories_widget_snapshot_export` manage memory snapshots
  - `memories_widget_reconcile_analyze` inspects candidate memory records
  - `memories_widget_reconcile_run` queues a dry-run proposal job and does not
    mutate memory records; it accepts `agent_type: lite | regular | strong`
    and stores the selected reconciler strength with the background job.
    It also accepts optional JSON-safe `reconciliation_context`, which is
    persisted, enqueued, and rebound under
    `bundle_call_context.memory.reconciliation.context` when the job runs.
    Override `on_memory_reconciliation_request(request=...)` for bundle-local
    validation or request augmentation.
  - `memories_widget_reconcile_export` exposes proposal artifacts for review
  - `memories_widget_reconcile_apply` applies a succeeded proposal only with
    explicit confirmation and first creates a safety snapshot
  - Telegram Mini App wrappers expose the same maintenance flow as
    `telegram_memories_widget_reconcile_*` public APIs

Telegram requires external operator setup. Hosting this bundle in KDCube is not
enough by itself: an operator must create or choose a bot in BotFather, expose
the KDCube deployment through public HTTPS, provision bot/webhook secrets,
register the webhook with Telegram, and promote Telegram users in the widget
admin screen. See `docs/integrations/telegram-setup.md`.

## Durable memory storage

Versatile now uses the SDK durable-memory subsystem for remembered user facts,
preferences, and corrections. The older bundle-local preference notebook
surface was removed; the remaining `product.preferences` skill teaches the
solver when to consult or update the SDK memory tools.

The full storage map, including Telegram admin state, canvas state, and
rebuildable UI output, lives in `docs/storage/README.md`.

## Bundle props and secrets

This reference bundle intentionally demonstrates the real split between:

- bundle props for non-secret behavior/configuration
- bundle secrets for credentials or signing material

### Non-secret props

This bundle reads effective props with `self.bundle_prop(...)`.

Concrete examples already used by `versatile`:

- `self.bundle_prop("preferences.auto_capture", True)` in `orchestrator/workflow.py`
- `self.bundle_prop("execution.runtime")` in `entrypoint.py`
- `self.bundle_prop("mcp.services")` in `entrypoint.py`

Those effective props come from the normal platform merge:

1. code defaults in `entrypoint.configuration`
2. `bundles.yaml`
3. runtime/admin props overrides

Example `bundles.yaml` snippet:

```yaml
bundles:
  version: "1"
  items:
    - id: "versatile@2026-03-31-13-36"
      config:
        execution:
          runtime:
            mode: docker
        integrations:
          telegram:
            enabled: false
            webhook_url: ""
            send_responses: true
        mcp:
          services:
            mcpServers:
              docs:
                transport: http
                url: https://mcp.internal.example.com
```

This snippet is intentionally sparse. Bundle config is an override over code
defaults; do not enumerate every enabled resource in normal deployments. Add
explicit values when the deployment changes behavior, for example enabling
Telegram public APIs after BotFather/webhook setup is complete.

### Secrets

This bundle demonstrates real secret usage for telemetry and Telegram
integration. `telemetry_sink.auth.token` is read with `get_secret(...)` when a
telemetry endpoint is configured, and Telegram secrets are read by the webhook
and Mini App integration paths.

Example `bundles.secrets.yaml` snippet for CLI/CI provisioning:

```yaml
bundles:
  version: "1"
  items:
    - id: "versatile@2026-03-31-13-36"
      secrets:
        telemetry_sink:
          auth:
            token: "<RANDOM_TELEMETRY_SINK_BEARER_TOKEN>"
        integrations:
          telegram:
            bot_token: null
            webhook_secret: null
```

The CLI injects those values into the configured secrets provider under the
bundle's canonical secret namespace. Bundle code normally reads them with the
current-bundle shorthand, for example `await get_secret("b:telemetry_sink.auth.token")`.

Secrets-manager configuration:

- local/dev sidecar:
  - `SECRETS_PROVIDER=secrets-service`
  - `SECRETS_URL=http://kdcube-secrets:7777`
  - if you provision bundle secrets via `bundles.secrets.yaml`, keep read tokens non-expiring:
    - `SECRETS_TOKEN_TTL_SECONDS=0`
    - `SECRETS_TOKEN_MAX_USES=0`
- AWS Secrets Manager:
  - `SECRETS_PROVIDER=aws-sm`
  - `SECRETS_AWS_SM_PREFIX` or `SECRETS_SM_PREFIX` sets the provider prefix
- process-local testing:
  - `SECRETS_PROVIDER=in-memory`

Important:

- bundle props are normal config and belong in `bundles.yaml`
- bundle secrets belong in `bundles.secrets.yaml` and in the configured secrets provider
- `bundles.yaml` env reset is authoritative; `bundles.secrets.yaml` is currently upsert-only
- admin UI for bundle secrets is write-only for values; it shows known keys but not secret contents

## Operations and UI

This bundle exposes both authenticated `operations` APIs and anonymous `public`
APIs.

The full decorator surface is:

```python
@api(
    method="POST",          # default: "POST"
    alias="my_operation",   # default: function name
    route="operations",     # "operations" | "public", default: "operations"
    user_types=("registered",),  # default: ()
    public_auth=None,       # only valid for route="public"
)
```

For public endpoints:

```python
@api(
    method="GET",
    alias="telegram_profile",
    route="public",
    public_auth="none",
)
```

If `route="public"` then `public_auth` is mandatory. Today the accepted forms are:

- `"none"`
- `{ "mode": "header_secret", "header": "X-KDCUBE-Public-Secret", "secret_key": "bundles.<bundle>.secrets...." }`
- `"bundle"` for bundle-owned hook auth; the method should accept `request: Request`,
  read the inbound headers/body itself, and raise `HTTPException(401/403/...)`
  on failure

This reference bundle includes common shapes such as authenticated widget
operations, canvas operations, Telegram admin operations, and public Telegram
webhook / Telegram-authenticated bridge operations.

Concrete routes:

- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/telegram_webhook`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/telegram_user_admin_data`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/telegram_user_admin_upsert`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/telegram_user_admin_delete`

## Main View UI

This bundle also ships a standalone main UI configured through `ui.main_view`
in `config/bundles.template.yaml`.

The active source lives under `ui/scene/`. It is a scene shell that embeds the
reusable SDK chat widget as `versatile_chat`, embeds the SDK memory widget as
`memories`, and renders the SDK canvas component as the main work surface.

The old bundle-local `ui/main/` implementation has been removed. The active
scene inherits chat behavior from the SDK chat widget.

The scene writes canvas mutations through Data Bus subject `canvas.patch` and
keeps request/response operations such as `canvas_read`, `canvas_list`,
`canvas_attachment_upload`, and `canvas_object_action` as bundle operations.
The canvas event source ids are generic protocol names: `canvas.state` and
`canvas.focus`.

The `canvas_object_action` operation also hosts configured named-service
namespace resolvers for both canvas and chat object actions. For example,
`crm:` refs can be delegated to a CRM owner bundle:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        event_sources:
        - kind: named_service
          namespace: crm
          policies:
            block_production:
              mode: provider
              operation: block.produce
            pull:
              mode: provider
              operation: object.get
    ui:
      canvas:
        resolvers:
        - kind: named_service
          namespace: crm
          allowed: [object.action]
```

The configured resolver resolves the owner through Named Service Discovery and
then calls the owning bundle through the request-bound local bridge, so it
preserves the current tenant/project/user session without making an HTTP
callback. A concrete `providers` list may be added only when this bundle must
pin one or more provider endpoints instead of using discovery.

The `surfaces.as_consumer.agents.<agent>.tools` list controls model-callable
named-service tools. The `surfaces.as_consumer.agents.<agent>.event_sources`
pull policy controls whether ReAct can materialize external refs through
`react.pull`. The `surfaces.as_consumer.ui.canvas.resolvers` list controls
canvas/chat object-card delegation; concrete resolver actions are accepted or
rejected by the owning provider at call time.

Detailed scene wiring is documented in `docs/design/scene-sdk-components.md`.

## UI integration contract

The active scene and SDK widgets are not static mockups. They follow the real
platform integration pattern:

- it requests runtime config from the parent frame with `CONFIG_REQUEST`
- it accepts `baseUrl`, `accessToken`, `idToken`, `idTokenHeader`, `defaultTenant`, `defaultProject`, and `defaultAppBundleId`
- SDK widgets call their configured bundle/backend operations through the
  integrations API.
- it sends `credentials: "include"` and forwards bearer / ID-token headers when present

This integration shape matters. If a bundle widget talks to bundle or platform REST APIs, keep the config/auth wiring aligned with the platform examples instead of inventing a different handshake.

Reference frontend patterns:
- `src/kdcube-ai-app/kdcube_ai_app/journal/26/03/widgets/App.tsx`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/AIBundleDashboard.tsx`

Reference backend endpoint:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`

The scene uses the platform iframe config handshake, embeds configured SDK
widgets, and calls canvas operations such as `canvas_read`, `canvas_write`,
`canvas_patch`, `canvas_attachment_upload`, and `canvas_object_action`.

The custom main view follows the same handshake and auth model, but it talks to the
chat runtime directly once mounted:

- `GET /profile`
- `GET /sse/stream`
- `POST /sse/chat`
- `GET /api/cb/conversations/{tenant}/{project}?bundle_id={bundle_id}`
- `POST /api/cb/conversations/{tenant}/{project}/{conversation_id}/fetch`
- `POST /api/cb/resources/by-rn`

Important request shape:

```json
{
  "bundle_id": "versatile@2026-03-31-13-36",
  "data": {
    "recency": 10,
    "kwords": "language timezone"
  }
}
```

The integrations endpoint forwards `data` as keyword arguments to the bundle
method. The canvas operation wrappers normalize the call payload and delegate
the actual storage/action behavior to `services/canvas.py`.

## Tool surface

The bundle includes:

- SDK tools:
  - `io_tools`
  - `ctx_tools`
  - `exec_tools`
  - `web_tools`
  - `rendering_tools`
- MCP connectors:
  - `knowledge`

## Skills

The bundle ships one bundle-local skill:

- `product.preferences`
  - teaches the solver to consult and update durable user memory before personalizing an answer

## Minimal vs versatile

| Shape | Required to pass the basic suite | Demonstrated here |
| --- | --- | --- |
| Minimal bundle | entrypoint, compiled graph, role models, tools descriptor, skills descriptor | yes |
| Bundle props / effective config | required for real deployments | yes |
| Custom tools | optional | yes |
| Custom skills | optional | yes |
| Bundle secrets via `get_secret(...)` | optional | yes |
| Economics | optional | yes |
| MCP tool consumers | optional | yes |
| Shared bundle storage backend | optional | yes |
| Storage backend snapshot/export | optional | yes |
| Widget / operations | optional | yes |
| Custom main view UI | optional | yes |

## Running the shared bundle test suite

```bash
BUNDLE_UNDER_TEST=/abs/path/to/versatile@2026-03-31-13-36 \
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
pytest -q app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle
```

## Running shared + bundle-local tests

Bundle-local tests for this reference bundle live under:
- `tests/`

Preferred combined validation command:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/versatile@2026-03-31-13-36 -v --tb=short
```

This runs:
- the shared SDK bundle suite under `sdk/tests/bundle`
- this bundle's own `tests/` directory when present

## Related docs

- `docs/storage/README.md`
- `docs/integrations/telegram-setup.md`
- `docs/scenarios/README.md`
- `docs/design/telegram-webapp.md`
- `docs/sdk/bundle/bundle-index-README.md`
- `docs/sdk/bundle/bundle-dev-README.md`
- `docs/sdk/bundle/bundle-reference-versatile-README.md`
