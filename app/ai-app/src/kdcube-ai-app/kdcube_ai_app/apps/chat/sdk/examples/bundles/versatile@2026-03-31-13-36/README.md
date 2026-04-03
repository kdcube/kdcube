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
| MCP tools                              | `tools_descriptor.py`                                                                      |
| Direct isolated exec from bundle code  | `entrypoint.py:preferences_exec_report`                                                    |
| Custom TSX widget                      | `ui/PreferencesBrowser.tsx`, `entrypoint.py:preferences_widget`                            |
| Custom iframe main view                | `ui-src/src/App.tsx`, `ui-src/src/settings.ts`, `entrypoint.py`                            |

## Bundle behavior

- The workflow is a normal gate → solver React loop.
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
        mcp:
          services:
            mcpServers:
              docs:
                transport: http
                url: https://mcp.internal.example.com
```

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
        preferences:
          snapshot_hmac_key: null
```

The CLI injects that into the configured secrets provider using the dot-path key:

```text
bundles.versatile@2026-03-31-13-36.secrets.preferences.snapshot_hmac_key
```

Bundle code reads it with:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

snapshot_hmac_key = get_secret(
    "bundles.versatile@2026-03-31-13-36.secrets.preferences.snapshot_hmac_key"
)
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

This bundle exposes four entrypoint operations:

- `preferences_widget`
  - reads current preference data
  - renders `ui/PreferencesBrowser.tsx`
  - returns iframe-ready HTML
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
- `preferences_exec_report`
  - runs a tiny report job through the isolated exec runtime
  - writes a markdown report artifact from shared bundle preference content
  - is wired to the widget's `Run Exec Report` button through the same integrations
operations API

## Custom main view UI

This bundle also ships a standalone iframe main view configured through `ui.main_view`
in `entrypoint.py`.

The source lives under `ui-src/`, and `ui-src/src/App.tsx` is the current lightweight
reference implementation for a custom bundle chat surface.

It intentionally covers the minimal but real platform contract:

- parent-frame config handshake via `CONFIG_REQUEST`
- authenticated bootstrap via `GET /profile`
- live streaming over `GET /sse/stream` and `POST /sse/chat`
- bundle-scoped conversation list + historical conversation load via `/api/cb/conversations/...`
- assistant file download resolution through `POST /api/cb/resources/by-rn`
- attachments, rate-limit/service banners, streamed markdown, followups, tool widgets, and separate timeline/steps/downloads tabs

The main view also includes a chats panel for the current bundle and can load an
existing conversation back into the same turn UI used for live streaming.

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

- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_widget_data`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_canvas_data`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_canvas_save`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_canvas_export_excel`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_canvas_import_excel`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/preferences_exec_report`

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
- `preferences_exec_report(recency=..., kwords=...)` consumes forwarded values
  and falls back to defaults when they are absent

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
  - `web_search`
  - `deepwiki`
  - `stack`
  - `docs`
  - `local`
  - `firecrawl`

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
| MCP | optional | yes |
| Shared bundle storage backend | optional | yes |
| Storage backend snapshot/export | optional | yes |
| Direct isolated exec from bundle code | optional | yes |
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

- `docs/sdk/bundle/bundle-index-README.md`
- `docs/sdk/bundle/bundle-dev-README.md`
- `docs/sdk/bundle/bundle-reference-versatile-README.md`
