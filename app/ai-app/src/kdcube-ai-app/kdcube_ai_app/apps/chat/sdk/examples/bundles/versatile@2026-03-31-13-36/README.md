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
| Minimal bundle contract                | `entrypoint.py`, `orchestrator/workflow.py`, `tools_descriptor.py`, `skills_descriptor.py` |
| React workflow                         | `entrypoint.py`, `orchestrator/workflow.py`, `agents/gate.py`                              |
| Economics / quotas                     | `entrypoint.py` via `BaseEntrypointWithEconomics` and `app_quota_policies`                 |
| Bundle props / effective config        | `entrypoint.py`, `orchestrator/workflow.py`                                                |
| Bundle secrets via `get_secret(...)`   | `tools/preference_tools.py`                                                                |
| Bundle-local tools                     | `tools/preference_tools.py`                                                                |
| Bundle-local skills                    | `skills/product/preferences/SKILL.md`                                                      |
| Shared bundle storage backend          | `preferences_store.py`, `entrypoint.py`, `orchestrator/workflow.py`, `tools/preference_tools.py` |
| Agent tool consumers                   | `tools_descriptor.py`, `config/bundles.template.yaml`                                      |
| MCP tool consumers                     | `surfaces.as_consumer.agents.main.tools`                                                   |
| Source-folder webapp widget            | `ui/widgets/versatile_webapp`, `entrypoint.py:versatile_webapp_widget`                     |
| Active iframe main view                | `ui/scene`, `entrypoint.py` main-view config                                               |
| Legacy custom iframe main view         | `ui/main/src/App.tsx`, `ui/main/src/settings.ts` retained for comparison                    |
| SDK chat widget mount                  | `versatile_chat`, backed by `sdk://solutions/chat/ui/widget`                               |
| SDK durable memory widget              | shared memory widget source, inherited `memories_widget_*` operations                      |
| Memory maintenance                     | `memories_widget_snapshot_*`, `memories_widget_reconcile_*` inherited from SDK memory mixin |
| Bundle interface contract              | `interface/README.md`                                                                       |
| Bundle config templates                | `config/bundles.template.yaml`, `config/bundles.secrets.template.yaml`                     |
| Bundle release metadata                | `release.yaml`                                                                              |
| Operational storage map                | `docs/storage/README.md`                                                                    |
| Runtime scenarios                      | `docs/scenarios/README.md`                                                                  |
| Authenticated `GET` bundle API         | `entrypoint.py:preferences_summary`                                                         |
| Anonymous public bundle API            | `entrypoint.py:preferences_public_info`                                                     |
| Telegram bot transport                 | `entrypoint.py:telegram_webhook`, `entrypoint.py:telegram_user_admin_*`                      |
| Telegram WebApp / Mini App             | `ui/widgets/versatile_webapp`, `entrypoint.py:telegram_versatile_webapp_data`              |
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
  - chat + preference capture
  - KDCube widget
  - Telegram bot chat
  - Telegram Mini App
  - consumer-surface tool wiring
  - named-service canvas/ReAct integration
  - widget build/serve flow
- `docs/design/telegram-webapp.md`
  - frontend/backend contract for the dual KDCube iframe and Telegram Mini App widget

## Bundle behavior

- The workflow is a normal gate → solver React loop.
- The active main view is `ui/scene`. It is a small scene shell that embeds the
  reusable SDK chat widget as `versatile_chat`; the previous `ui/main` source is
  kept in the bundle for comparison while the main-view configuration points at
  `ui/scene`.
- The bundle heuristically captures preference statements from user messages while the chat is running.
- Preferences are stored per user in the shared bundle storage backend (`AIBundleStorage`).
- The solver can consult those preferences with the bundle-local tool:
  - `preferences.get_preferences(recency, kwords)`
- Natural-language capture and structured updates can be written through:
  - `preferences.capture_preferences(text, source)`
  - `preferences.set_preference(key, value, source)`
- A storage-backend snapshot can be exported through:
  - `preferences.export_preferences_snapshot(filename)`
  - when secret `bundles.<bundle_id>.secrets.preferences.snapshot_hmac_key` is configured, the export is also signed and a `.sig.json` sidecar is written
- The bundle demonstrates the reusable Telegram bot transport:
  - `telegram_webhook` receives Bot API updates through a public route guarded
    by Telegram's webhook secret header
  - `telegram_user_admin_*` operations manage the bundle-owned Telegram user
    registry from KDCube-authenticated operations routes
  - queued Telegram turns are wrapped with
    `telegram_user_admin.run_with_queued_telegram_delivery(...)`, so the normal
    workflow result is rendered back to Telegram as text and files
- The bundle demonstrates a source-folder Telegram WebApp:
  - `versatile_webapp` is served as a normal KDCube widget and can also be
    opened by Telegram as a Mini App
  - the widget shows the current preferences canvas, Telegram-linked chat
    channels, and Telegram user administration
  - public `telegram_*` WebApp APIs are normal public bundle APIs; they verify
    Telegram `initData` inside the bundle before reading or mutating data
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

## Preference storage layout

Inside the bundle storage backend root:

```text
preferences/
  users/
    <user_id>/
      current.json
      events.jsonl
```

These bundle-storage keys stay readable for humans:
- `current.json` is the latest materialized view
- `events.jsonl` is the append-only observation history

The widget also exposes `current.json` as a simplified collaborative notebook.
Each preference is rendered as one editable line with:

- read-only timestamp
- author badge (`user` or `assistant`)
- editable label
- editable text

When a user edits a line, the bundle rewrites it as a fresh user-authored entry
with a new timestamp and appends the corresponding history events in
`events.jsonl`.

The same notebook can also be exported to Excel and imported back from Excel.
The spreadsheet uses one row per preference line with columns like label, text,
timestamp, author, source, origin, and evidence.

The full storage map, including Telegram admin state and rebuildable UI output,
lives in `docs/storage/README.md`.

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
        preferences:
          auto_capture: true
          widget_max_events: 15
        execution:
          runtime:
            mode: docker
        integrations:
          telegram:
            enabled: false
            webhook_url: ""
            send_responses: true
        mcp:
          preferences:
            auth:
              header_name: "X-Versatile-Preferences-MCP-Token"
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

This bundle demonstrates real secret usage in
`tools/preference_tools.py:export_preferences_snapshot(...)`.

It reads the bundle secret:

```text
bundles.<bundle_id>.secrets.preferences.snapshot_hmac_key
```

If that key exists, the bundle:

- signs the exported snapshot with `get_secret(...)`
- writes a JSON signature sidecar next to the snapshot
- returns only metadata about the signature, never the secret value itself

Example `bundles.secrets.yaml` snippet for CLI/CI provisioning:

```yaml
bundles:
  version: "1"
  items:
    - id: "versatile@2026-03-31-13-36"
      secrets:
        mcp:
          preferences:
            auth:
              shared_token: null
        preferences:
          snapshot_hmac_key: null
        integrations:
          telegram:
            bot_token: null
            webhook_secret: null
```

The CLI injects that into the configured secrets provider under the bundle's
canonical secret namespace:

```text
bundles.versatile@2026-03-31-13-36.secrets.preferences.snapshot_hmac_key
```

Bundle code normally reads it with the current-bundle shorthand:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

snapshot_hmac_key = get_secret("b:preferences.snapshot_hmac_key")
```

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

## Widget + operations

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
    alias="preferences_public_info",
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

This reference bundle now includes all three common shapes:

- authenticated `GET`:
  - `preferences_summary`
- authenticated `POST`:
  - `preferences_widget_data`
- anonymous public `GET`:
  - `preferences_public_info`

Concrete routes:

- `GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_summary`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_widget_data`
- `GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/preferences_public_info`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/telegram_webhook`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/telegram_user_admin_data`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/telegram_user_admin_upsert`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/telegram_user_admin_delete`

This bundle also exposes widget and notebook operations:

- `preferences_summary`
  - authenticated `GET` example
  - returns current preference/event counts for the current user
- `preferences_public_info`
  - anonymous `GET` public endpoint example
  - returns a small non-sensitive bundle capabilities payload
- `preferences_widget_data`
  - returns the current widget payload as JSON for the authenticated iframe client
  - is called by the widget through the integrations operations API
- `preferences_canvas_data`
  - returns the collaborative preferences notebook entries for the current user
  - still exposes the bundle-storage key for the underlying `current.json`
- `preferences_canvas_save`
  - accepts edited notebook entries from the widget
  - rewrites them back into the structured `current.json` store
  - appends change/remove events so the history remains visible to the bundle
- `preferences_canvas_export_excel`
  - exports the current notebook as an `.xlsx` workbook
  - returns a browser-downloadable payload for the widget
- `preferences_canvas_import_excel`
  - accepts an uploaded `.xlsx` workbook from the widget
  - imports notebook rows and rewrites them as fresh user-authored entries

## Main View UI

This bundle also ships a standalone main UI configured through `ui.main_view`
in `entrypoint.py`.

The active source lives under `ui/scene/`. It is a scene shell that embeds the
reusable SDK chat widget as `versatile_chat`, embeds the SDK memory widget as
`memories`, and renders the SDK canvas component as the main work surface.

The previous custom chat implementation remains under `ui/main/` as a legacy
reference for comparison.

It intentionally covers the minimal but real platform contract:

- runtime config handshake via `CONFIG_REQUEST`
- authenticated bootstrap via `GET /profile`
- live streaming over `GET /sse/stream` and `POST /sse/chat`
- bundle-scoped conversation list + historical conversation load via `/api/cb/conversations/...`
- assistant file download resolution through `POST /api/cb/resources/by-rn`
- attachments, rate-limit/service banners, streamed markdown, followups, tool widgets, and separate timeline/steps/downloads tabs

The active scene inherits those chat behaviors from the SDK chat widget.

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

## Widget integration contract

The widget is not a static mockup. It follows the real platform integration pattern:

- it requests runtime config from the parent frame with `CONFIG_REQUEST`
- it accepts `baseUrl`, `accessToken`, `idToken`, `idTokenHeader`, `defaultTenant`, `defaultProject`, and `defaultAppBundleId`
- it calls the bundle backend through:
  - `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_widget_data`
- it sends `credentials: "include"` and forwards bearer / ID-token headers when present

This integration shape matters. If a bundle widget talks to bundle or platform REST APIs, keep the config/auth wiring aligned with the platform examples instead of inventing a different handshake.

Reference frontend patterns:
- `src/kdcube-ai-app/kdcube_ai_app/journal/26/03/widgets/App.tsx`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/AIBundleDashboard.tsx`

Reference backend endpoint:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`

The widget uses the platform iframe config handshake and then calls:

- `GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_summary`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_widget_data`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_canvas_data`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_canvas_save`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_canvas_export_excel`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_canvas_import_excel`
- `GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/preferences_public_info`

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
method. In this bundle:
- `preferences_widget_data(...)` uses the per-request `self.comm` context and
  ignores extra params
- `preferences_canvas_data(...)` loads the current collaborative notebook lines
- `preferences_canvas_save(entries=...)` saves the edited notebook lines back
  into the structured preference store
- `preferences_canvas_export_excel(...)` returns a base64 `.xlsx` payload for download
- `preferences_canvas_import_excel(content_b64=...)` imports uploaded `.xlsx` rows
  and rewrites them into the notebook

## Tool surface

The bundle includes:

- SDK tools:
  - `io_tools`
  - `ctx_tools`
  - `exec_tools`
  - `web_tools`
  - `rendering_tools`
- Bundle-local tools:
  - `preferences`
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
