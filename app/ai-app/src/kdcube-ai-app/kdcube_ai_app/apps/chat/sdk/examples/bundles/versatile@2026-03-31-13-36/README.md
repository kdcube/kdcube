# versatile bundle

`versatile@2026-03-31-13-36` is the full-feature reference bundle for bundle builders.

It intentionally demonstrates the main SDK bundle surfaces together in one place, so a human or bundle-builder copilot can learn the platform from one concrete implementation before branching into narrower examples.

## What it demonstrates

| Capability                             | Where to look                                                                              |
|----------------------------------------|--------------------------------------------------------------------------------------------|
| Minimal bundle contract                | `entrypoint.py`, `orchestrator/workflow.py`, `tools_descriptor.py`, `skills_descriptor.py` |
| React workflow                         | `entrypoint.py`, `orchestrator/workflow.py`, `agents/gate.py`                              |
| Economics / quotas                     | `entrypoint.py` via `BaseEntrypointWithEconomics` and `app_quota_policies`                 |
| Bundle-local tools                     | `tools/preference_tools.py`                                                                |
| Bundle-local skills                    | `skills/product/preferences/SKILL.md`                                                      |
| Shared bundle storage backend          | `preferences_store.py`, `entrypoint.py`, `orchestrator/workflow.py`, `tools/preference_tools.py` |
| MCP tools                              | `tools_descriptor.py`                                                                      |
| Direct isolated exec from bundle code  | `entrypoint.py:preferences_exec_report`                                                    |
| Custom TSX widget                      | `ui/PreferencesBrowser.tsx`, `entrypoint.py:preferences_widget`                            |

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

The widget also exposes `current.json` as a simplified collaborative JSON canvas.
Users edit plain key/value JSON in the browser, and the bundle normalizes it back
into the structured stored form used by the agent tools.

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
  - returns the collaborative preferences document as editable JSON text
  - exposes the bundle-storage key for the current user's `current.json`
- `preferences_canvas_save`
  - accepts edited JSON text from the widget
  - normalizes the document back into the structured `current.json` store
  - appends change/remove events so the history remains visible to the bundle
- `preferences_exec_report`
  - runs a tiny report job through the isolated exec runtime
  - writes a markdown report artifact from shared bundle preference content
  - is wired to the widget's `Run Exec Report` button through the same integrations
operations API

## Widget integration contract

The widget is not a static mockup. It follows the real platform integration pattern:

- it requests runtime config from the parent frame with `CONFIG_REQUEST`
- it accepts `baseUrl`, `accessToken`, `idToken`, `idTokenHeader`, `defaultTenant`, `defaultProject`, and `defaultAppBundleId`
- it calls the bundle backend through:
  - `POST /api/integrations/bundles/{tenant}/{project}/operations/preferences_widget_data`
- it sends `credentials: "include"` and forwards bearer / ID-token headers when present

This integration shape matters. If a bundle widget talks to bundle or platform REST APIs, keep the config/auth wiring aligned with the platform examples instead of inventing a different handshake.

Reference frontend patterns:
- `src/kdcube-ai-app/kdcube_ai_app/journal/26/03/widgets/App.tsx`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/AIBundleDashboard.tsx`

Reference backend endpoint:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`

The widget uses the platform iframe config handshake and then calls:

- `POST /api/integrations/bundles/{tenant}/{project}/operations/preferences_widget_data`
- `POST /api/integrations/bundles/{tenant}/{project}/operations/preferences_canvas_data`
- `POST /api/integrations/bundles/{tenant}/{project}/operations/preferences_canvas_save`
- `POST /api/integrations/bundles/{tenant}/{project}/operations/preferences_exec_report`

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
- `preferences_canvas_data(...)` loads the current collaborative JSON document
- `preferences_canvas_save(document_text=...)` saves the edited collaborative
  JSON document back into the structured preference store
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
| Custom tools | optional | yes |
| Custom skills | optional | yes |
| Economics | optional | yes |
| MCP | optional | yes |
| Shared bundle storage backend | optional | yes |
| Storage backend snapshot/export | optional | yes |
| Direct isolated exec from bundle code | optional | yes |
| Widget / operations | optional | yes |

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
