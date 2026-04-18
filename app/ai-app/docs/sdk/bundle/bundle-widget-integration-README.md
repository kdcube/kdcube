---
id: ks:docs/sdk/bundle/bundle-widget-integration-README.md
title: "Bundle Widget Integration"
summary: "Contract for bundle iframe widgets: host config handshake, operation URL construction, auth headers, and the compatibility pattern for widgets that are also callable through operations."
tags: ["sdk", "bundle", "widget", "iframe", "frontend", "integrations"]
keywords: ["bundle widget", "iframe widget", "CONFIG_REQUEST", "CONFIG_RESPONSE", "CONN_RESPONSE", "defaultAppBundleId", "operations route"]
see_also:
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-client-ui-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
---
# Bundle Widget Integration

Use this doc when a bundle widget returns HTML or a small SPA that runs inside the platform iframe shell and must call bundle operations correctly.

This is the rule:

- the widget HTML is rendered inside an iframe
- the iframe must request runtime config from the parent frame
- the iframe must build bundle operation URLs from that runtime config
- the iframe must not hardcode tenant, project, or bundle id from the source tree

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

If an existing client still loads the widget through the operations route, keep the widget method decorated with both:

```python
@ui_widget(alias="task-board", user_types=("registered",))
@api(alias="task-board", route="operations", user_types=("registered",))
async def task_board(self, **kwargs):
    ...
```

That means:

- widget discovery/fetch is still driven by `@ui_widget(...)`
- legacy operation callers can still call the same method through `/operations/task-board`

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
- If the widget can work from server-seeded tenant/project/bundle defaults, seed them into the HTML as a safe fallback.
- Still keep the parent-frame config handshake, because auth tokens and final runtime scope belong to the host.
- For platform widgets and iframe clients, the preferred `POST /operations/{alias}` body shape is `{ "data": { ... } }`.
- The integrations layer also accepts a raw JSON object body and treats it as `data`, so webhook-style service integrations do not need a platform-specific wrapper.
- The integrations layer returns an envelope shaped like `{ status, tenant, project, bundle_id, [alias]: result }`; widgets should unwrap the `[alias]` field.

## Reference Examples

Use these as the current reference implementations:

- [PreferencesBrowser.tsx](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/PreferencesBrowser.tsx)
- [KnowledgeBaseAdmin.tsx](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/ui/KnowledgeBaseAdmin.tsx)

These examples show the real pattern:

- `CONFIG_REQUEST` to the parent frame
- accept both `CONN_RESPONSE` and `CONFIG_RESPONSE`
- build operation URLs from runtime scope
- use the host-provided auth tokens and token-header name
