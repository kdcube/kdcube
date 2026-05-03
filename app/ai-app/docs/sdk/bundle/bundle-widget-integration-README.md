---
id: ks:docs/sdk/bundle/bundle-widget-integration-README.md
title: "Bundle Widget Integration"
summary: "Iframe widget contract for bundles: source-folder widget apps, host handshake, operation URL construction, auth propagation, and the recommended pattern when a capability is both widget and operation."
tags: ["sdk", "bundle", "widget", "iframe", "frontend", "integrations"]
keywords: ["iframe widget contract", "widget source folder", "web app widget build", "host config handshake", "operation url construction", "auth propagation to widget", "widget and operation dual pattern", "bundle iframe integration"]
see_also:
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-client-ui-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
---
# Bundle Widget Integration

Use this doc when a bundle exposes a widget that runs inside the platform iframe shell and must call bundle operations correctly.

This is the rule:

- new widget apps should be source folders, not Python-rendered TSX snippets
- the widget app is rendered inside an iframe
- the iframe must request runtime config from the parent frame
- the iframe must build bundle operation URLs from that runtime config
- the iframe must not hardcode tenant, project, or bundle id from the source tree

## Source Folder Widget Apps

For new React/Vite widgets, keep widget app source under a widget-specific
folder such as:

```text
widgets/<widget_alias>/
  package.json
  index.html
  vite.config.js
  src/
```

Do not put widget source under `ui-src`. In KDCube docs and examples, `ui-src`
is the convention for a bundle main view declared by `ui.main_view`.

Declare the widget source in bundle configuration:

```yaml
ui:
  web_app_widgets:
    task_memo_webapp:
      enabled: true
      src_folder: widgets/task_memo_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

The bundle loader builds that source folder into shared bundle storage under:

```text
<bundle_storage_root>/ui/widgets/<widget_alias>
```

The widget route serves the built app and supports SPA subpath fallback:

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}/{widget_path}
```

For Telegram Mini Apps or other public launch surfaces where the static widget
app must load before platform auth exists, use the public static-widget route:

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}/{widget_path}
```

That route serves only the built widget app assets. It does not authenticate
product data. Any public data/action API used by that widget must have its own
bundle-level auth, for example Telegram WebApp `initData` verification.

## Dual Runtime Pattern

When the same widget app runs in KDCube control plane and Telegram, select the
transport at runtime:

- if `window.Telegram?.WebApp?.initData` exists, treat it as Telegram
- otherwise, use the normal KDCube iframe parent config handshake

KDCube control-plane runtime:

- wait for `CONFIG_RESPONSE` or `CONN_RESPONSE`
- use `baseUrl`, `defaultTenant`, `defaultProject`, `defaultAppBundleId`
- call `/operations/{alias}`
- pass KDCube auth headers from the parent config

Telegram Mini App runtime:

- do not wait for the parent config handshake
- call `window.Telegram.WebApp.ready()` and `expand()` when available
- serve the app from `/public/widgets/{widget_alias}`
- call bundle public aliases such as `/public/{telegram_alias}`
- send the exact `window.Telegram.WebApp.initData` string as
  `X-Telegram-Init-Data`

Keep the API aliases explicit. A common pattern is:

```ts
const telegramAliases: Record<string, string> = {
  task_memo_webapp_data: "telegram_task_memo_webapp_data",
  tasks_list: "telegram_tasks_list",
  tasks_create: "telegram_tasks_create",
  run_task_now: "telegram_run_task_now",
};
```

The public route loads the app; the public operation verifies the user. Do not
trust caller-supplied `user_id` or `fingerprint` in Telegram mode.

Use `npm ci` in `build_command` when the widget source commits a lockfile. For
early prototype widgets without a lockfile, `npm install --no-package-lock`
avoids mutating the source folder during loader builds.

The decorated `@ui_widget(...)` method remains the widget discovery/manifest
surface. Product behavior and data mutations should live behind separate
structured `@api(route="operations")` methods that the widget calls.

### Per-Alias Selection

Source-folder serving is selected per widget alias.

This config affects only `task_memo_webapp`:

```yaml
ui:
  web_app_widgets:
    task_memo_webapp:
      enabled: true
      src_folder: widgets/task_memo_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

It does not change inherited or legacy widgets such as `ai_bundles`, `opex`, or
other `@ui_widget` methods on a base class. Those aliases continue to invoke
their Python method and return method-rendered HTML unless their own alias also
has `src_folder` and `build_command`.

Do not add `ui.web_app_widgets.<alias>.src_folder/build_command` for an alias
unless that alias is intentionally migrating to the folder-built widget model.

## Main View Is Separate

Do not confuse widget app source with main-view source.

Use:

- `ui.main_view.src_folder: ui-src` for the bundle main view
- `ui.web_app_widgets.<alias>.src_folder: widgets/<alias>` for widget apps

Both use the same loader/build/storage paradigm. They are different surfaces.

## Required Runtime Config

The widget should request these fields from the parent frame:

- `baseUrl`
- `accessToken`
- `idToken`
- `idTokenHeader`
- `defaultTenant`
- `defaultProject`
- `defaultAppBundleId`

The widget must accept both response event types:

- `CONN_RESPONSE`
- `CONFIG_RESPONSE`

Both are used in the platform today. Do not listen only for `CONFIG_RESPONSE`.

## Required URL Shape

Bundle operations must be called as:

```text
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}
```

For bundle widgets, `{bundle_id}` should come from `defaultAppBundleId`.

Do not build operation URLs with:

- empty `tenant` / `project` / `bundleId`
- source-folder names
- bundle-local constants that may drift from `bundles.yaml`

## Compatibility Pattern

For source-folder widgets, prefer a minimal decorated widget method plus
separate structured APIs. If an existing client still loads the widget through
the operations route, keep the widget method decorated with both:

```python
@ui_widget(alias="task-board", user_types=("registered",))
@api(alias="task-board", route="operations", user_types=("registered",))
async def task_board(self, **kwargs):
    ...
```

That means:

- widget discovery/fetch is still driven by `@ui_widget(...)`
- legacy operation callers can still call the same method through `/operations/task-board`

For a source-folder widget, the method may return only a small compatibility
fallback because the platform serves the built widget app from bundle storage
when `ui.web_app_widgets.<alias>.src_folder/build_command` is configured.

If the iframe itself needs a structured backend API, expose a separate alias such as:

```python
@api(method="POST", alias="task-tracker-api", route="operations", user_types=("registered",))
async def task_tracker_api(self, operation: str, payload: dict | None = None, **kwargs):
    ...
```

Example operations for that API might be:

- `list_tasks`
- `create_task`
- `update_schedule`
- `run_task_now`

Then the widget calls `/operations/task-tracker-api`, not `/operations/task-board`.

## Minimal Widget Handshake Example

```ts
const identity = 'MY_BUNDLE_WIDGET'

const state = {
  baseUrl: window.location.origin,
  accessToken: '',
  idToken: '',
  idTokenHeader: 'X-ID-Token',
  tenant: '',
  project: '',
  bundleId: '',
}

function hasConfig(): boolean {
  return !!(state.baseUrl && state.tenant && state.project && state.bundleId)
}

function makeHeaders(): Headers {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  if (state.accessToken) headers.set('Authorization', `Bearer ${state.accessToken}`)
  if (state.idToken) headers.set(state.idTokenHeader || 'X-ID-Token', state.idToken)
  return headers
}

function operationUrl(alias: string): string {
  if (!hasConfig()) throw new Error('Widget configuration is incomplete.')
  const baseUrl = state.baseUrl.replace(/\/+$/, '')
  const tenant = encodeURIComponent(state.tenant)
  const project = encodeURIComponent(state.project)
  const bundleId = encodeURIComponent(state.bundleId)
  return `${baseUrl}/api/integrations/bundles/${tenant}/${project}/${bundleId}/operations/${alias}`
}

async function postOperation<T>(alias: string, payload: Record<string, unknown>): Promise<T> {
  const response = await fetch(operationUrl(alias), {
    method: 'POST',
    credentials: 'include',
    headers: makeHeaders(),
    body: JSON.stringify({ data: payload }),
  })

  const text = await response.text()
  let parsed: unknown = {}
  try {
    parsed = text ? JSON.parse(text) : {}
  } catch {
    parsed = { raw: text }
  }

  if (!response.ok) {
    const detail =
      parsed && typeof parsed === 'object' && 'detail' in (parsed as Record<string, unknown>)
        ? String((parsed as Record<string, unknown>).detail)
        : text || response.statusText
    throw new Error(detail)
  }

  if (parsed && typeof parsed === 'object' && alias in (parsed as Record<string, unknown>)) {
    return (parsed as Record<string, unknown>)[alias] as T
  }

  return parsed as T
}

function applyConfig(config: Record<string, unknown>): void {
  if (typeof config.baseUrl === 'string' && config.baseUrl) state.baseUrl = config.baseUrl
  if (typeof config.accessToken === 'string' || config.accessToken === null) state.accessToken = config.accessToken || ''
  if (typeof config.idToken === 'string' || config.idToken === null) state.idToken = config.idToken || ''
  if (typeof config.idTokenHeader === 'string' && config.idTokenHeader) state.idTokenHeader = config.idTokenHeader
  if (typeof config.defaultTenant === 'string' && config.defaultTenant) state.tenant = config.defaultTenant
  if (typeof config.defaultProject === 'string' && config.defaultProject) state.project = config.defaultProject
  if (typeof config.defaultAppBundleId === 'string' && config.defaultAppBundleId) state.bundleId = config.defaultAppBundleId
}

window.addEventListener('message', (event: MessageEvent) => {
  if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return
  if (event.data.identity !== identity || !event.data.config) return
  applyConfig(event.data.config)
})

window.parent.postMessage(
  {
    type: 'CONFIG_REQUEST',
    data: {
      identity,
      requestedFields: [
        'baseUrl',
        'accessToken',
        'idToken',
        'idTokenHeader',
        'defaultTenant',
        'defaultProject',
        'defaultAppBundleId',
      ],
    },
  },
  '*',
)
```

## Operational Rules

- Widget load should be read-only by default.
- Use an explicit in-widget action such as `Refresh` if the widget needs to trigger a syncing bootstrap or other mutating backend operation.
- If the widget can derive tenant/project/bundle defaults from its route, use those only as safe standalone fallbacks.
- Still keep the parent-frame config handshake, because auth tokens and final runtime scope belong to the host.
- For platform widgets and iframe clients, the preferred `POST /operations/{alias}` body shape is `{ "data": { ... } }`.
- The integrations layer also accepts a raw JSON object body and treats it as `data`, so webhook-style service integrations do not need a platform-specific wrapper.
- The integrations layer returns an envelope shaped like `{ status, tenant, project, bundle_id, [alias]: result }`; widgets should unwrap the `[alias]` field.

## Reference Examples

Use these as reference implementations for the iframe config handshake and
operation-call shape:

- [PreferencesBrowser.tsx](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/PreferencesBrowser.tsx)
- [KnowledgeBaseAdmin.tsx](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/ui/KnowledgeBaseAdmin.tsx)

Those examples are useful for runtime config and API calls. For new widgets,
prefer the source-folder layout described above instead of embedding TSX/HTML in
Python-rendered widget responses.

They show:

- `CONFIG_REQUEST` to the parent frame
- accept both `CONN_RESPONSE` and `CONFIG_RESPONSE`
- build operation URLs from runtime scope
- use the host-provided auth tokens and token-header name
